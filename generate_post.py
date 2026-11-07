import html
import json
import os
import random
import re
from datetime import datetime, timezone
from pathlib import Path

import nh3
from google import genai
from google.genai import types

MODEL = os.environ.get("GEMINI_MODEL") or "gemini-3.8-flash"
print(f"Using model: {MODEL}")
SITE_URL = "https://justcalculators.app"
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
    """Swap the old plain-text header in existing posts for the logo header."""
    pattern = re.compile(r'<h1><a href="\.\./index\.html">.*?</a></h1>', re.S)
    for f in POSTS_DIR.glob("*.html"):
        text = f.read_text(encoding="utf-8")
        new = pattern.sub(lambda m: f"<h1>{LOGO_HTML}</h1>", text, count=1)
        if new != text:
            f.write_text(new, encoding="utf-8")
            print(f"Updated header in {f.name}")


def generate_post(client, calc):
    prompt = f"""
You are a content writer for JustCalculators.app. Write a helpful, SEO-friendly
blog post about the practical, real-world value of this calculator:

Tool: {calc['name']}
Description: {calc['description']}

Rules:
- Return a JSON object with exactly two keys: "title" and "content".
- "title": a clear, specific headline (no clickbait).
- "content": HTML using only <h2>, <p>, <ul>, <li>, <strong>. Do not include
  an <h1>, links, or a call-to-action box.
- 3-4 substantial sections with practical examples.
- Do NOT state specific statistics, interest rates, tax figures, or legal
  claims. Use illustrative round numbers and label them as examples.
"""
    last_error = None
    for attempt in range(3):
        try:
            response = client.models.generate_content(
                model=MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json"),
            )
            if not response.text:
                raise ValueError("Empty response from model")
            data = json.loads(response.text)
            if data.get("title") and len(data.get("content", "")) > 800:
                return data
            raise ValueError("Response missing title or content too short")
        except Exception as e:
            last_error = e
            print(f"Attempt {attempt + 1} failed: {e}")
            if "NOT_FOUND" in str(e):
                break  # a missing model won't fix itself; don't retry
    raise RuntimeError(f"Generation failed: {last_error}")


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

    client = genai.Client(api_key=api_key)
    post = generate_post(client, calc)

    title = html.escape(post["title"].strip())
    body = nh3.clean(post["content"], tags=ALLOWED_TAGS, attributes={})

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
    <link rel="stylesheet" href="style.css">
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


if __name__ == "__main__":
    main()
