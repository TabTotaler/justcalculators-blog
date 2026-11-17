import html
import json
import os
import random
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse
from xml.sax.saxutils import escape as xml_escape

import nh3
from google import genai
from google.genai import types

MODEL = os.environ.get("GEMINI_MODEL") or "gemini-3.8-flash"
print(f"Using model: {MODEL}")

SITE_URL = "https://justcalculators.app"
BLOG_URL = "https://blog.justcalculators.app"
BLOG_TITLE = "JustCalculators Blog"
BLOG_DESCRIPTION = "Practical guides and tips for using everyday calculators."
RSS_LINK_TAG = (f'<link rel="alternate" type="application/rss+xml" '
                f'title="{BLOG_TITLE}" href="/rss.xml">')
FEED_LIMIT = 20
MAX_ATTEMPTS = 3
TRANSIENT_MARKERS = ("503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED",
                     "500 INTERNAL", "504", "DEADLINE_EXCEEDED",
                     "timed out", "Timeout")
POSTS_DIR = Path("posts")
ALLOWED_TAGS = {"h2", "h3", "p", "ul", "ol", "li", "strong", "em", "a"}

SITEMAP_XML_URL = f"{SITE_URL}/sitemap.xml"
SITEMAP_HTML_URL = f"{SITE_URL}/sitemap.html"
PAGE_RE = re.compile(r"^https://justcalculators\.app/([a-z0-9-]+)/([a-z0-9-]+)\.html$")
ACRONYMS = {
    "roi", "irr", "npv", "wacc", "bmi", "bmr", "tdee", "apr", "apy", "cagr",
    "capm", "dti", "fica", "fifo", "hsa", "ira", "pmi", "rmd", "vat", "eps",
    "ctr", "cpa", "cac", "gpa", "fba", "pod", "spod", "ups", "usps", "dhl",
    "fte", "magi", "agi", "dscr", "ear", "roas", "roce", "roic", "rgb", "hex",
    "bsa", "bac", "pslf", "heloc", "cogs", "ebitda", "ev", "3pl",
}
STOPWORDS = {"calculator", "calc", "the", "of", "to", "and", "vs", "for", "a",
             "an", "in", "with", "rate", "embed", "2026"}

LOGO_HTML = """<a href="/" class="nav-logo" aria-label="JustCalculators blog home"><svg class="nav-logo-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="30" height="30" role="img" aria-hidden="true"><rect x="4" y="4" width="56" height="56" rx="14" fill="#ffffff"/><rect x="12" y="12" width="40" height="22" rx="6" fill="#1a56db"/><rect x="20" y="19" width="24" height="4" rx="2" fill="#ffffff"/><rect x="20" y="27" width="24" height="4" rx="2" fill="#ffffff"/><circle cx="20" cy="46" r="6" fill="#e8f0fe"/><circle cx="32" cy="46" r="6" fill="#e8f0fe"/><circle cx="44" cy="46" r="6" fill="#fbbf24"/><path d="M 41.6 46.2 L 43.2 47.8 L 46.4 44.6" fill="none" stroke="#1a56db" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg><span class="nav-logo-text">JustCalculators<span class="dot">.</span>app <span class="nav-blog">Blog</span></span></a>"""


def slugify(text):
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60].strip("-")
    return slug or "post"


def normalize_url(url):
    return html.unescape(url).strip().lower().rstrip("/")


def used_calculators():
    """Return (used_urls, used_names) across ALL existing posts so no calculator repeats."""
    used_urls = set()
    used_names = set()
    for f in POSTS_DIR.glob("*.html"):
        text = f.read_text(encoding="utf-8")

        m_url = re.search(r'<meta name="calculator-url" content="([^"]*)"', text)
        if m_url:
            used_urls.add(normalize_url(m_url.group(1)))

        m_callout = re.search(
            r'<div class="calculator-callout">.*?<a\s[^>]*?href="([^"]*)"',
            text,
            re.S,
        )
        if m_callout:
            used_urls.add(normalize_url(m_callout.group(1)))

        m_name = re.search(r'<meta name="calculator" content="([^"]*)"', text)
        if m_name:
            used_names.add(html.unescape(m_name.group(1)).strip().lower())

    return used_urls, used_names


def refresh_headers():
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


def http_get(url, timeout=20, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "JustCalculatorsBlogBot/1.0 (+https://blog.justcalculators.app)"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception as e:
            last = e
            time.sleep(2 * (i + 1))
    raise last


def name_from_slug(slug):
    return " ".join(w.upper() if w in ACRONYMS else w.capitalize()
                    for w in slug.split("-"))


def clean_label(text):
    label = html.unescape(re.sub(r"<[^>]+>", "", text)).strip()
    label = re.split(r"\s+[—–]\s+", label)[0]
    label = re.sub(r"\s+20\d\d$", "", label)
    return label.strip()


def tokens(text):
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOPWORDS}


def norm(text):
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def load_site_index():
    """Return a list of real calculator pages from JustCalculators.app."""
    try:
        root = ET.fromstring(http_get(SITEMAP_XML_URL))
    except Exception as e:
        print(f"WARNING: could not load {SITEMAP_XML_URL}: {e}")
        return None

    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    entries = {}
    for u in root.findall("s:url", ns):
        loc = (u.findtext("s:loc", default="", namespaces=ns) or "").strip()
        m = PAGE_RE.match(loc)
        if not m or m.group(2).endswith("-embed"):
            continue
        try:
            priority = float(u.findtext("s:priority", default="0.5", namespaces=ns))
        except ValueError:
            priority = 0.5
        entries[loc] = {"url": loc, "category": m.group(1), "slug": m.group(2),
                        "name": name_from_slug(m.group(2)), "priority": priority}

    try:
        seen = set()
        page = http_get(SITEMAP_HTML_URL)
        for href, text in re.findall(r'<a\s[^>]*?href="([^"]+)"[^>]*>(.*?)</a>', page, re.S):
            url = urljoin(SITEMAP_HTML_URL, html.unescape(href)).split("#")[0]
            m = PAGE_RE.match(url)
            if not m or m.group(2).endswith("-embed") or url in seen:
                continue
            label = clean_label(text)
            if not label:
                continue
            seen.add(url)
            if url not in entries:
                entries[url] = {"url": url, "category": m.group(1), "slug": m.group(2),
                                "name": label, "priority": 0.6}
            else:
                entries[url]["name"] = label
    except Exception as e:
        print(f"WARNING: could not load {SITEMAP_HTML_URL} (using names from URLs): {e}")

    if len(entries) < 50:
        print(f"WARNING: sitemap looked too small ({len(entries)} pages); ignoring it.")
        return None

    index = sorted(entries.values(), key=lambda e: e["priority"], reverse=True)
    for e in index:
        e["tokens"] = tokens(e["name"]) | tokens(e["slug"].replace("-", " "))
    print(f"Loaded {len(index)} real calculator pages from {SITE_URL}.")
    return index


def find_entry(calc, by_url, by_slug, by_key):
    """Match one calculators.json entry to a real page (URL, then slug, then name)."""
    url = (calc.get("url") or "").strip()
    if url:
        hit = by_url.get(urljoin(SITE_URL + "/", url).lower().rstrip("/"))
        if hit:
            return hit
    segs = [s for s in urlparse(url).path.split("/") if s]
    category = segs[-2] if len(segs) >= 2 else None
    slug = segs[-1].removesuffix(".html") if segs else ""

    cands = []
    if slug:
        cands = by_slug.get(slug) or by_slug.get(slug + "-calculator") or []
    if not cands:
        key = norm(calc.get("name", ""))
        for k in (key, key + "calculator", key.removesuffix("calculator")):
            if k and k in by_key:
                cands = by_key[k]
                break
    if not cands:
        return None
    if category:
        same = [e for e in cands if e["category"] == category]
        if same:
            return same[0]
    return cands[0]


def build_calculator_catalog(calculators_json, index):
    """Build the full catalog of real calculators on JustCalculators.app, enriched with descriptions."""
    if not index:
        return calculators_json

    by_url = {e["url"].lower().rstrip("/"): e for e in index}
    by_slug, by_key = {}, {}
    for e in index:
        by_slug.setdefault(e["slug"], []).append(e)
        for key in {norm(e["name"]), norm(e["slug"])}:
            by_key.setdefault(key, []).append(e)

    descriptions_by_url = {}
    for c in calculators_json:
        e = find_entry(c, by_url, by_slug, by_key)
        if e and c.get("description"):
            descriptions_by_url[e["url"]] = c["description"]

    catalog = []
    for e in index:
        catalog.append({
            "name": e["name"],
            "url": e["url"],
            "description": descriptions_by_url.get(
                e["url"],
                f"Free online {e['name']} from JustCalculators.app.",
            ),
            "category": e["category"],
        })
    return catalog


def pick_related(calc, index, n=3):
    """Choose calculators on the same topic first, then popular ones in the category."""
    mine = next((e for e in index if e["url"] == calc["url"]), None)
    base = mine["tokens"] if mine else tokens(calc["name"])
    category = calc.get("category")
    scored = []
    for e in index:
        if e["url"] == calc["url"]:
            continue
        score = 3 * len(base & e["tokens"]) + (2 if e["category"] == category else 0) + e["priority"]
        scored.append((score, e))
    scored.sort(key=lambda t: t[0], reverse=True)

    same_topic = [e for _, e in scored if base & e["tokens"]][:n * 2]
    chosen = random.sample(same_topic, min(n, len(same_topic)))
    if len(chosen) < n:
        urls = {e["url"] for e in chosen}
        rest = [e for _, e in scored if e["url"] not in urls][:n]
        chosen += random.sample(rest, min(n - len(chosen), len(rest)))
    return [{"name": e["name"], "url": e["url"],
             "description": f"Free online {e['name']}", "category": e["category"]}
            for e in chosen]


def generate_fallback_post(calc):
    """Generate a detailed structural post if API quota is exhausted."""
    print("API Quota exhausted or rate limited. Generating comprehensive fallback post.")
    title = f"Comprehensive Guide to Using the {calc['name']}"
    content = f"""
    <h2>Understanding Your Numbers with the {calc['name']}</h2>
    <p>When managing everyday planning, projections, and personal estimates, having a reliable and accessible <a href="{calc['url']}">online calculator</a> makes all the difference. The <strong>{calc['name']}</strong> is specifically engineered to remove the friction from complex calculations, delivering clear, actionable insights in seconds.</p>
    
    <h2>Practical Everyday Applications and Use Cases</h2>
    <p>Using structured <a href="{SITE_URL}">calculation tools</a> helps eliminate guesswork from your planning process. For example, by breaking down your core figures into manageable estimates, you can evaluate multiple scenarios and spot trends instantly without manual math errors.</p>
    <ul>
        <li>Double-check all initial inputs and baseline metrics before running your calculation to ensure maximum accuracy.</li>
        <li>Test various round numbers as illustrative examples to gauge potential best-case and worst-case outcomes.</li>
        <li>Save valuable time by utilizing dedicated utilities on your desktop, tablet, or mobile device whenever you need quick evaluations.</li>
    </ul>

    <h2>Key Factors to Keep in Mind</h2>
    <p>Every calculation relies heavily on the quality of the inputs provided. When evaluating your results, always consider external variables that might shift your baseline assumptions over time. Taking a methodical approach ensures your long-term planning remains solid and dependable.</p>
    <ul>
        <li>Keep your core data updated regularly as circumstances evolve.</li>
        <li>Compare your calculator outputs against historical trends for better context.</li>
        <li>Consult professional guidance when dealing with high-stakes financial or legal planning.</li>
    </ul>

    <h2>Get Started and Explore More Tools</h2>
    <p>Ready to put these insights into practice? You can test your figures directly using this <a href="{calc['url']}">online calculator</a> or browse through our complete collection of utilities on the main <a href="{SITE_URL}">JustCalculators</a> hub to support all your daily estimation needs.</p>
    """
    return {"title": title, "content": content.strip()}


def generate_post(client, calc, related_calcs):
    related_links_text = "\n".join(
        f"- {rc['name']}: {rc['url']}" for rc in related_calcs
    )

    prompt = f"""
You are an expert content writer for JustCalculators.app. Write a thorough, in-depth, and comprehensive SEO-friendly blog post about the practical, real-world value of this primary calculator:

Primary Tool: {calc['name']}
Description: {calc['description']}
Primary URL: {calc['url']}
Main App URL: {SITE_URL}

Other real calculator pages on the site that you can naturally link to (these are the ONLY valid calculator URLs):
{related_links_text}

Rules:
- Return a JSON object with exactly two keys: "title" and "content".
- "title": a clear, specific, engaging headline (no clickbait).
- "content": HTML using only <h2>, <p>, <ul>, <li>, <strong>, <em>, <a>. Do not include an <h1> or a call-to-action box.
- Provide 4 to 5 substantial, detailed sections with rich practical examples, context on why these calculations matter, and best practices.
- Aim for a comprehensive article length (rich, descriptive paragraphs that thoroughly explain the topic).
- Do NOT state specific statistics, interest rates, tax figures, or legal claims. Use illustrative round numbers and label them as examples.

Internal Linking Guidelines:
- Naturally embed 3 to 4 internal links inside the body paragraphs using the HTML <a> tag.
- Always link to the primary tool ({calc['url']}) once. Link to at least two of the related calculators listed above, only where they are genuinely relevant to the sentence. You may also link once to the homepage ({SITE_URL}).
- Use ONLY the exact URLs given above. Never invent, shorten, or guess any other URL.
- Make the anchor text describe the destination: use the calculator's name (for example "{calc['name']}") or a natural variation of it. Never use "click here". Do not use the exact same anchor text twice.
- Example: "...you can evaluate your figures with our <a href="{calc['url']}">{calc['name']}</a> and compare scenarios using the related tools above."
"""
    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
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

            if data.get("title") and len(data.get("content", "")) > 1500:
                return data
            raise ValueError("Response missing title or content too short (< 1500 chars)")
        except Exception as e:
            last_error = e
            print(f"Attempt {attempt} failed: {e}")

            if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                return generate_fallback_post(calc)

            retryable = isinstance(e, ValueError) or any(
                marker in str(e) for marker in TRANSIENT_MARKERS)
            if not retryable or attempt == MAX_ATTEMPTS:
                break
            wait = min(10 * (2 ** (attempt - 1)), 60)
            print(f"Retrying in {wait}s...")
            time.sleep(wait)

    print(f"Generation failed after attempts. Last error: {last_error}")
    return generate_fallback_post(calc)


def restrict_links(body, allowed):
    """Unwrap any <a> whose href is not a known real page (keeps the text)."""
    def repl(m):
        href = html.unescape(m.group(1)).rstrip("/")
        return m.group(0) if href in allowed else m.group(2)
    return re.sub(r'<a\s[^>]*?href="([^"]*)"[^>]*>(.*?)</a>', repl, body,
                  flags=re.S)


def make_http_options():
    try:
        return types.HttpOptions(timeout=120_000)
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

    calculators_json = []
    if Path("calculators.json").exists():
        with open("calculators.json", "r", encoding="utf-8") as f:
            calculators_json = json.load(f)

    index = load_site_index()
    catalog = build_calculator_catalog(calculators_json, index)

    used_urls, used_names = used_calculators()
    unused_pool = [
        c for c in catalog
        if normalize_url(c["url"]) not in used_urls
        and c["name"].strip().lower() not in used_names
    ]

    print(f"Total calculators available: {len(catalog)} | "
          f"Already covered: {len(catalog) - len(unused_pool)} | "
          f"Remaining unused: {len(unused_pool)}")

    pool = unused_pool if unused_pool else catalog
    calc = random.choice(pool)
    print(f"Selected primary calculator: {calc['name']} -> {calc['url']}")

    if index:
        related_calcs = pick_related(calc, index)
    else:
        others = [c for c in catalog if c["url"] != calc["url"]]
        related_calcs = random.sample(others, min(3, len(others)))
    print("Related calculators: " + ", ".join(rc["name"] for rc in related_calcs))

    client = genai.Client(api_key=api_key, http_options=make_http_options())
    post = generate_post(client, calc, related_calcs)

    title = html.escape(post["title"].strip())
    body = nh3.clean(
        post["content"],
        tags=ALLOWED_TAGS,
        attributes={"a": {"href", "target"}},
    )

    allowed_urls = {SITE_URL.rstrip("/"), calc["url"].rstrip("/")}
    allowed_urls.update(rc["url"].rstrip("/") for rc in related_calcs)
    if index:
        allowed_urls.update(e["url"].rstrip("/") for e in index)

    body = restrict_links(body, allowed_urls)

    callout = (
        '<div class="calculator-callout"><p>Try it yourself: '
        f'<a href="{html.escape(calc["url"])}" target="_blank" rel="noopener">'
        f'{html.escape(calc["name"])}</a> - {html.escape(calc["description"])}'
        "</p></div>"
    )
    body = body.replace("</p>", "</p>" + callout, 1) if "</p>" in body else callout + body

    body = re.sub(r'(</(?:h2|h3|p|ul|ol|li|div)>)', r'\1\n            ', body).strip()

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
    <meta name="calculator-url" content="{html.escape(calc['url'])}">
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
    posts = sorted((read_post(f) for f in POSTS_DIR.glob("*.html")),
                    key=lambda p: p["published"], reverse=True)
    newest = posts[0]["published"] if posts else datetime.now(timezone.utc)

    urls = [f"  <url><loc>{BLOG_URL}/</loc>"
            f"<lastmod>{newest.strftime('%Y-%m-%d')}</lastmod></url>"]
    for p in posts:
        urls.append(f"  <url><loc>{xml_escape(p['url'])}</loc>"
                    f"<lastmod>{p['published'].strftime('%Y-%m-%d')}</lastmod></url>")
    Path("sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(urls) + "\n</urlset>\n", encoding="utf-8")

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
            "title": m.group(1) if m else "Blog Post",
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
