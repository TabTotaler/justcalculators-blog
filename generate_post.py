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

# Where the real calculator pages are listed (source of truth for links).
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

        # 1. Check explicit calculator-url meta tag
        m_url = re.search(r'<meta name="calculator-url" content="([^"]*)"', text)
        if m_url:
            used_urls.add(normalize_url(m_url.group(1)))

        # 2. Check the primary callout link (works on all historical posts)
        m_callout = re.search(
            r'<div class="calculator-callout">.*?<a\s[^>]*?href="([^"]*)"',
            text,
            re.S,
        )
        if m_callout:
            used_urls.add(normalize_url(m_callout.group(1)))

        # 3. Also record the calculator name as a secondary fallback
        m_name = re.search(r'<meta name="calculator" content="([^"]*)"', text)
        if m_name:
            used_names.add(html.unescape(m_name.group(1)).strip().lower())

    return used_urls, used_names


def refresh
