import html
import json
import os
import random
import re
import time
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

import nh3
from google import genai
from google.genai import types

# Primary model with automatic fallback to gemini-1.5-flash if overloaded
PRIMARY_MODEL = os.environ.get("GEMINI_MODEL") or "gemini-3.8-flash"
MODELS_TO_TRY = [PRIMARY_MODEL, "gemini-1.5-flash"]
# Deduplicate in case PRIMARY_MODEL is already gemini-1.5-flash
MODELS_TO_TRY = list(dict.fromkeys(MODELS_TO_TRY))

print(f"Configured models fallback order: {MODELS_TO_TRY}")

SITE_URL = "https://justcalculators.app"
BLOG_URL = "https://blog.justcalculators.app"
BLOG_TITLE = "JustCalculators Blog"
BLOG_DESCRIPTION = "Practical guides and tips for using everyday calculators."
RSS_LINK_TAG = (f'<link rel="alternate" type="application/rss+xml" '
                f'title="{BLOG_TITLE}" href="/rss.xml">')
FEED_LIMIT = 20
MAX_ATTEMPTS_PER_MODEL = 3
TRANSIENT_MARKERS = ("503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED",
                     "500 INTERNAL", "504", "DEADLINE_EXCEEDED",
                     "timed out", "Timeout")
POSTS_DIR = Path("posts")
ALLOWED_TAGS = {"h2", "h3", "p", "ul", "ol", "li", "strong", "em", "a"}

# Logo markup, same as the main site's nav logo. "/" is the blog home page
# because the blog lives on its own domain (blog.justcalculators.app).
LOGO_HTML = """<a href="/" class="nav-logo" aria-label="JustCalculators blog home"><svg class="nav-logo-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="30" height="30" role="img" aria-hidden="true"><rect x="4" y="4" width="56" height="56" rx="14" fill="#ffffff"/><rect x="12" y="12" width="40" height="22" rx="6" fill="#1a56db"/><rect x="20" y="19" width="24" height="4" rx="2" fill="#ffffff"/><rect x="20" y="27" width="24" height="4" rx="2" fill="#ffffff"/><circle cx="20" cy="46" r="6" fill="#e8f0fe"/><circle cx="32" cy="46" r="6" fill="#e8f0fe"/><circle cx="44" cy="46" r="6" fill="#fbbf24"/><path d="M 41.6 46.2 L 43.2 47.8 L 46.4 44.6" fill="none" stroke="#1a56db" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg><span class="nav-logo-text">JustCalculators<span class="dot">.</span>app <span class="nav-blog">Blog</span></span></a>"""


def slugify(text):
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60].strip("-")
    return slug or "post"


def recent_calculators(n=10):
    names = set()
    for f in sorted(POSTS_DIR.glob("*.html"), reverse=True)[:n]:
        m = re.search(r'<meta name="calculator" content="([^"]*)"',
                    f.read_text(encoding="utf-8"))
        if m:
            names.add(html.unescape(m.group(1)))
    return names


def refresh_headers():
    """Make every existing post use the logo header and a canonical URL.

    Replaces whatever <h1> is inside <header> (old plain title, or a literal
    {LOGO_HTML} placeholder that was pasted by mistake) with the logo, and adds
    a canonical link if the post doesn't have one yet.
    """
    header_pattern = re.compile(r"(<header>\s*)<h1>.*?</h1>", re.S)
    for f in POSTS_DIR.glob("*.html"):
        text = f.read_text(encoding="utf-8")
        new = header_pattern.sub(
            lambda m: f"{m.group(1)}<h1>{LOGO_HTML}</h1>", text, count=1)
        if 'rel="canonical"' not in new:
            new = new.replace(
                "</head>",
                f'    <link rel="canonical" href="{BLOG_URL}/posts/{f.name}">\n</head>',
                1)
        if "application/rss+xml" not in new:
            new = new.replace("</head>", f"    {RSS_LINK_TAG}\n</head>", 1)
        if new != text:
            f.write_text(new, encoding="utf-8")
            print(f"Updated header/canonical in {f.name}")


def generate_post(client, calc):
    prompt = f"""
You are a content writer for JustCalculators.app. Write a helpful, SEO-friendly
blog post about the practical, real-world value of this calculator:

Tool: {calc['name']}
Description: {calc['description']}
Main App URL: {SITE_URL}

Rules:
- Return a JSON object with exactly two keys: "title" and "content".
- "title": a clear, specific headline (no clickbait).
- "content": HTML using only <h2>, <p>, <ul>, <li>, <strong>, <em>, <a>. Do not include an <h1> or a call-to-action box.
- 3-4 substantial sections with practical examples.
- Do NOT state specific statistics, interest rates, tax figures, or legal claims. Use illustrative round numbers and label them as examples.

Internal Linking Guidelines:
- Naturally embed 2 to 3 internal links inside the body paragraphs using the HTML <a> tag. Link ONLY to {SITE_URL} or {calc['url']}; never invent any other URL.
- Use varied, contextual anchor text (never use the exact identical anchor text twice). Mix singular and plural variations such as: "calculator", "calculators", "online calculator", "calculation tools", "calculation utility", or "financial tool".
- Example: "...you can explore more options on our <a href="{SITE_URL}">calculators hub</a> or test them directly using this <a href="{calc['url']}">online calculator</a>."
"""
    last_error = None
    
    for model_name in MODELS_TO_TRY:
        print(f"Attempting generation with model: {model_name}")
        for attempt in range(1, MAX_ATTEMPTS_PER_MODEL + 1):
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json"),
                )
                if not response.text:
                    raise ValueError("Empty response from model")
                data = json.loads(response.text)
                if data.get("title") and len(data.get("content", "")) > 800:
                    print(f"Successfully generated post using {model_name}")
                    return data
                raise ValueError("Response missing title or content too short")
            except Exception as e:
                last_error = e
                print(f"[{model_name}] Attempt {attempt} failed: {e}")
                
                retryable = isinstance(e, ValueError) or any(
                    marker in str(e) for marker in TRANSIENT_MARKERS)
                
                if not retryable:
                    # Non-retryable error (e.g. 404 model not found, auth error), skip to next model immediately
                    break
                
                if attempt == MAX_ATTEMPTS_PER_MODEL:
                    print(f"Exhausted attempts for {model_name}, moving to next model if available...")
                    break
                
                wait = min(10 * (2 ** (attempt - 1)), 60)
                print(f"Retrying in {wait}s...")
                time.sleep(wait)

    raise RuntimeError(f"Generation failed across all models. Last error: {last_error}")


def restrict_links(body, allowed):
    """Unwrap any <a> whose href is not on the allowlist (keeps the text)."""
    def repl(m):
        href = html.unescape(m.group(1)).rstrip("/")
        return m.group(0) if href in allowed else m.group(2)
    return re.sub(r'<a\s[^>]*?href="([^"]*)"[^>]*>(.*?)</a>', repl, body,
                  flags=re.S)


def make_http_options():
    """Give each Gemini request a 2-minute timeout instead of hanging."""
    try:
        return types.HttpOptions(timeout=120_000)  # milliseconds
    except Exception:
        return None


def main():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY secret is missing!")

    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    POSTS_DIR.mkdir(exist_ok=True)
    refresh_headers()

    if list(POSTS_DIR.glob(f"{today}-*.html")) and not os.environ.get("FORCE"):
        print(f"A post for {today} already exists; skipping.")
        rebuild_index(now)
        return

    with open("calculators.json", "r", encoding="utf-8") as f:
        calculators = json.load(f)

    recent = recent_calculators()
    pool = [c for c in calculators if c["name"] not in recent] or calculators
    calc = random.choice(pool)
    print(f"Selected calculator: {calc['name']}")

    client = genai.Client(api_key=api_key, http_options=make_http_options())
    post = generate_post(client, calc)

    title = html.escape(post["title"].strip())
    body = nh3.clean(
        post["content"],
        tags=ALLOWED_TAGS,
        attributes={"a": {"href", "target"}},
    )
    body = restrict_links(
        body, {SITE_URL.rstrip("/"), calc["url"].rstrip("/")})

    callout = (
        '<div class="calculator-callout"><p>Try it yourself: '
        f'<a href="{html.escape(calc["url"])}" target="_blank" rel="noopener">'
        f'{html.escape(calc["name"])}</a> - {html.escape(calc["description"])}'
        "</p></div>"
    )
    body = body.replace("</p>", "</p>" + callout, 1) if "</p>" in body else callout + body

    text = " ".join(re.sub(r"<[^>]+>", " ", body).split())
    description = html.escape(text[:155])

    filename = POSTS_DIR / f"{today}-{slugify(post['title'])}.html"
    timestamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    post_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title} - JustCalculators Blog</title>
    <meta name="description" content="{description}">
    <meta name="calculator" content="{html.escape(calc['name'])}">
    <link rel="canonical" href="{BLOG_URL}/posts/{filename.name}">
    {RSS_LINK_TAG}
    <link rel="stylesheet" href="../style.css">
</head>
<body>
    <header>
        <h1>{LOGO_HTML}</h1>
        <nav><a href="{SITE_URL}">Back to JustCalculators</a></nav>
    </header>
    <main>
        <article>
            <h2>{title}</h2>
            <div class="post-meta">Published <time datetime="{timestamp}">{today}</time></div>
            {body}
            <p class="disclaimer"><em>This article is for general information only and is not financial, legal, or medical advice.</em></p>
        </article>
    </main>
    <footer>
        <p>&copy; {now.year} JustCalculators.app. All rights reserved.</p>
    </footer>
</body>
</html>
"""
    filename.write_text(post_html, encoding="utf-8")
    print(f"Created post file: {filename}")
    rebuild_index(now)


def read_post(f):
    """Pull title, description and publish time out of a generated post."""
    text = f.read_text(encoding="utf-8")
    m = re.search(r"<article>\s*<h2>(.*?)</h2>", text, re.S)
    title = html.unescape(m.group(1)).strip() if m else f.stem
    m = re.search(r'<meta name="description" content="([^"]*)"', text)
    description = html.unescape(m.group(1)) if m else ""
    published = None
    m = re.search(r'<time datetime="([^"]+)"', text)
    if m:
        try:
            published = datetime.strptime(
                m.group(1), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    if published is None:
        published = datetime.strptime(
            f.name[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return {
        "title": title,
        "description": description,
        "published": published,
        "url": f"{BLOG_URL}/posts/{f.name}",
    }


def build_feeds():
    """Write sitemap.xml, rss.xml and robots.txt (clean URLs only)."""
    posts = sorted((read_post(f) for f in POSTS_DIR.glob("*.html")),
                    key=lambda p: p["published"], reverse=True)
    newest = posts[0]["published"] if posts else datetime.now(timezone.utc)

    # sitemap.xml
    urls = [f"  <url><loc>{BLOG_URL}/</loc>"
            f"<lastmod>{newest.strftime('%Y-%m-%d')}</lastmod></url>"]
    for p in posts:
        urls.append(f"  <url><loc>{xml_escape(p['url'])}</loc>"
                    f"<lastmod>{p['published'].strftime('%Y-%m-%d')}</lastmod></url>")
    Path("sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(urls) + "\n</urlset>\n", encoding="utf-8")

    # rss.xml
    items = []
    for p in posts[:FEED_LIMIT]:
        items.append(
            "    <item>\n"
            f"      <title>{xml_escape(p['title'])}</title>\n"
            f"      <link>{xml_escape(p['url'])}</link>\n"
            f"      <guid isPermaLink=\"true\">{xml_escape(p['url'])}</guid>\n"
            f"      <pubDate>{format_datetime(p['published'])}</pubDate>\n"
            f"      <description>{xml_escape(p['description'])}</description>\n"
            "    </item>")
    Path("rss.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">\n'
        "  <channel>\n"
        f"    <title>{xml_escape(BLOG_TITLE)}</title>\n"
        f"    <link>{BLOG_URL}/</link>\n"
        f"    <description>{xml_escape(BLOG_DESCRIPTION)}</description>\n"
        "    <language>en-us</language>\n"
        f"    <lastBuildDate>{format_datetime(newest)}</lastBuildDate>\n"
        f'    <atom:link href="{BLOG_URL}/rss.xml" rel="self" type="application/rss+xml"/>\n'
        + "\n".join(items) + "\n  </channel>\n</rss>\n", encoding="utf-8")

    # robots.txt
    Path("robots.txt").write_text(
        f"User-agent: *\nAllow: /\n\nSitemap: {BLOG_URL}/sitemap.xml\n",
        encoding="utf-8")
    print(f"Wrote sitemap.xml, rss.xml and robots.txt ({len(posts)} posts)")


def rebuild_index(now):
    posts = []
    for f in sorted(POSTS_DIR.glob("*.html"), reverse=True):
        content = f.read_text(encoding="utf-8")
        m = re.search(r"<article>\s*<h2>(.*?)</h2>", content, re.S)
        posts.append({
            "title": m.group(1) if m else "Blog Post",  # already escaped
            "url": f"posts/{f.name}",
            "date": f.name[:10],
        })

    posts_html = "".join(
        f"""
        <article class="post-card">
            <div class="post-meta">{p['date']}</div>
            <h3><a href="{p['url']}">{p['title']}</a></h3>
        </article>"""
        for p in posts
    ) or "<p>No posts yet. Check back soon!</p>"

    index_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>JustCalculators Blog - Practical Guides</title>
    <meta name="description" content="Practical guides and tips for using everyday calculators.">
    <link rel="canonical" href="{BLOG_URL}/">
    {RSS_LINK_TAG}
    <link rel="stylesheet" href="/style.css">
</head>
<body>
    <header>
        <h1>{LOGO_HTML}</h1>
        <nav><a href="{SITE_URL}">Back to JustCalculators</a></nav>
    </header>
    <main>
        <h2>Latest Posts</h2>
        <p>Guides and practical tips powered by our calculator tools.</p>
        {posts_html}
    </main>
    <footer>
        <p>&copy; {now.year} JustCalculators.app. All rights reserved.</p>
    </footer>
</body>
</html>
"""
    Path("index.html").write_text(index_html, encoding="utf-8")
    print("Successfully updated index.html")
    build_feeds()


if __name__ == "__main__":
    main()
