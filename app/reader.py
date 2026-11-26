"""Reader view: pull the article out of a fetched page and lay it out cleanly.

The page has already been fetched (and, for archive.org snapshots, had its
asset URLs rewritten) by portable.py. Here we find the article body with
readability, gather the title / author / date from the page's metadata, strip
everything that isn't content, and hand back pieces for READER_TEMPLATE.
Nothing in the article's own words is changed.
"""

import json
import re
from datetime import datetime
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Comment
from readability import Document

# Lazy-loading attributes sites use instead of a real src.
LAZY_SRC_ATTRS = ("data-src", "data-lazy-src", "data-original", "data-url", "data-hi-res-src", "data-full-src")
LAZY_SRCSET_ATTRS = ("data-srcset", "data-lazy-srcset")

# Everything else is dropped, including every on* handler and style attribute.
ALLOWED_ATTRS = {
    "a": {"href", "title"},
    "img": {"src", "srcset", "sizes", "alt", "title", "width", "height"},
    "source": {"src", "srcset", "sizes", "type", "media"},
    "video": {"src", "poster", "controls", "width", "height"},
    "audio": {"src", "controls"},
    "iframe": {"src", "width", "height", "title", "allowfullscreen"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan", "scope"},
    "time": {"datetime"},
    "ol": {"start", "reversed"},
    "blockquote": {"cite"},
    "q": {"cite"},
}
URL_ATTRS = {"href", "src", "poster", "cite"}
DROP_TAGS = ["script", "style", "noscript", "form", "input", "button", "select", "textarea",
             "object", "embed", "link", "meta", "svg", "canvas", "template"]

WAYBACK_ASSET_RE = re.compile(r"^https?://web\.archive\.org/web/\d{1,14}[a-z]{0,2}_?/(https?://.+)$", re.I)

MIN_ARTICLE_CHARS = 250
WORDS_PER_MINUTE = 230


def _meta(soup, *keys):
    for key in keys:
        tag = soup.find("meta", attrs={"property": key}) or soup.find("meta", attrs={"name": key})
        if tag and tag.get("content", "").strip():
            return tag["content"].strip()
    return ""


def _json_ld_items(soup):
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except ValueError:
            continue
        stack = data if isinstance(data, list) else [data]
        for item in stack:
            if isinstance(item, dict):
                yield item
                for child in item.get("@graph", []) if isinstance(item.get("@graph"), list) else []:
                    if isinstance(child, dict):
                        yield child


def _names(value):
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [value["name"]] if isinstance(value.get("name"), str) else []
    if isinstance(value, list):
        return [name for entry in value for name in _names(entry)]
    return []


def _author(soup):
    author = _meta(soup, "author", "article:author", "parsely-author", "sailthru.author")
    if author and not author.startswith("http"):
        return author
    for item in _json_ld_items(soup):
        names = _names(item.get("author"))
        if names:
            return ", ".join(dict.fromkeys(n.strip() for n in names if n.strip()))
    byline = soup.find(attrs={"rel": "author"}) or soup.find(class_=re.compile(r"\bbyline\b|\bauthor-name\b", re.I))
    if byline:
        text = byline.get_text(" ", strip=True)
        if 0 < len(text) < 80:
            return re.sub(r"^by\s+", "", text, flags=re.I)
    return ""


def _published(soup):
    raw = _meta(soup, "article:published_time", "og:published_time", "date", "pubdate", "parsely-pub-date")
    if not raw:
        for item in _json_ld_items(soup):
            if isinstance(item.get("datePublished"), str):
                raw = item["datePublished"]
                break
    if not raw:
        time_tag = soup.find("time", datetime=True)
        raw = time_tag["datetime"] if time_tag else ""
    match = re.match(r"(\d{4})-(\d{2})-(\d{2})", raw or "")
    if not match:
        return ""
    try:
        date = datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return ""
    return f"{date:%B} {date.day}, {date.year}"


def _is_placeholder(src):
    src = (src or "").strip()
    if not src:
        return True
    if src.startswith("data:"):
        return len(src) < 400  # 1x1 GIFs and blank SVGs, not real inline images
    return bool(re.search(r"(placeholder|blank|spacer|transparent|pixel)\.(gif|png|svg)", src, re.I))


def _promote_lazy_images(soup):
    for noscript in soup.find_all("noscript"):
        if noscript.find("img"):
            noscript.unwrap()
    for img in soup.find_all("img"):
        if _is_placeholder(img.get("src")):
            for attr in LAZY_SRC_ATTRS:
                if img.get(attr):
                    img["src"] = img[attr]
                    break
        if not img.get("srcset"):
            for attr in LAZY_SRCSET_ATTRS:
                if img.get(attr):
                    img["srcset"] = img[attr]
                    break
        if _is_placeholder(img.get("src")) and not img.get("srcset"):
            img.decompose()


def _clean_fragment(fragment_html, base_url):
    fragment = BeautifulSoup(fragment_html, "html.parser")
    for comment in fragment.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()
    for tag in fragment.find_all(DROP_TAGS):
        tag.decompose()
    for tag in fragment.find_all(True):
        allowed = ALLOWED_ATTRS.get(tag.name, set())
        for attr in list(tag.attrs):
            if attr not in allowed:
                del tag[attr]
        for attr in URL_ATTRS & set(tag.attrs):
            value = tag[attr].strip()
            if value.lower().startswith(("javascript:", "vbscript:", "data:text")):
                del tag[attr]
            elif not value.startswith(("#", "data:")):
                tag[attr] = urljoin(base_url, value)
        if tag.name == "img":
            tag["loading"] = "lazy"
            tag["decoding"] = "async"
        if tag.name == "a" and tag.get("href", "").startswith("http"):
            tag["rel"] = "noopener noreferrer"
    for img in fragment.find_all("img"):
        if _is_placeholder(img.get("src")) and not img.get("srcset"):
            img.decompose()
        else:
            _prefer_live_image(img)
    return fragment


def _prefer_live_image(img):
    """Archive.org often never saved an article's images. Load the live copy
    first and keep the archived one as the fallback (see READER_TEMPLATE)."""
    match = WAYBACK_ASSET_RE.match(img.get("src", ""))
    if not match:
        return
    img["data-fallback-src"] = img["src"]
    img["src"] = match.group(1)
    if img.get("srcset"):
        candidates = []
        for candidate in re.split(r",\s+", img["srcset"].strip()):
            url, _, descriptor = candidate.strip().partition(" ")
            candidates.append(" ".join(filter(None, [WAYBACK_ASSET_RE.sub(r"\1", url), descriptor.strip()])))
        img["srcset"] = ", ".join(candidates)


def extract_article(page_html, page_url):
    """Return a dict for READER_TEMPLATE: title, site, author, date, minutes, content."""
    soup = BeautifulSoup(page_html, "html.parser")
    base_tag = soup.find("base", href=True)
    base_url = base_tag["href"] if base_tag else page_url

    button = soup.find(id="ladder-reader-btn")
    if button:
        button.decompose()

    title = _meta(soup, "og:title", "twitter:title")
    site = _meta(soup, "og:site_name", "application-name") or (urlparse(page_url).hostname or "").removeprefix("www.")
    author = _author(soup)
    published = _published(soup)
    lead_image = _meta(soup, "og:image", "twitter:image")

    _promote_lazy_images(soup)
    document = Document(str(soup), url=base_url)
    fragment = _clean_fragment(document.summary(html_partial=True), base_url)
    title = title or document.short_title()

    # Readability often keeps the headline; we show our own.
    first_heading = fragment.find(["h1", "h2"])
    if first_heading and first_heading.get_text(strip=True) == title.strip():
        first_heading.decompose()

    text = fragment.get_text(" ", strip=True)
    words = len(text.split())

    if lead_image and not fragment.find("img"):
        lead_image = urljoin(base_url, lead_image)
    else:
        lead_image = ""

    return {
        "title": title,
        "site": site,
        "author": author,
        "published": published,
        "minutes": max(1, round(words / WORDS_PER_MINUTE)),
        "lead_image": lead_image,
        "content": str(fragment) if len(text) >= MIN_ARTICLE_CHARS else "",
    }


def content_security_policy(nonce):
    # The article HTML is sanitized, but the CSP makes sure nothing in it can
    # run script on this server's origin even if something slips through.
    return (
        "default-src 'none'; img-src * data: blob:; media-src *; frame-src *; "
        f"style-src 'unsafe-inline'; script-src 'nonce-{nonce}'; "
        "base-uri 'none'; form-action 'none'"
    )


READER_TEMPLATE = """<!DOCTYPE html>
<html lang="{{ lang }}" data-theme="auto">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="referrer" content="no-referrer">
<title>{{ article.title or source_url }}</title>
<link rel="icon" href="/favicon.ico" type="image/png">
<style>
  :root {
    --bg: #fbfaf7; --text: #1d1c1a; --muted: #6b6760; --rule: #e6e2da;
    --link: #2357b5; --quote: #f2efe8; --bar: rgba(251, 250, 247, 0.92);
    --size: 20px; --measure: 680px;
  }
  :root[data-theme="dark"] {
    --bg: #17181b; --text: #e6e4df; --muted: #9a978f; --rule: #2c2e33;
    --link: #8fb4ff; --quote: #202227; --bar: rgba(23, 24, 27, 0.92);
  }
  :root[data-theme="sepia"] {
    --bg: #f4ecd8; --text: #3b2f22; --muted: #7c6a55; --rule: #e1d4b5;
    --link: #8a4b14; --quote: #ebe0c6; --bar: rgba(244, 236, 216, 0.92);
  }
  @media (prefers-color-scheme: dark) {
    :root[data-theme="auto"] {
      --bg: #17181b; --text: #e6e4df; --muted: #9a978f; --rule: #2c2e33;
      --link: #8fb4ff; --quote: #202227; --bar: rgba(23, 24, 27, 0.92);
    }
  }
  * { box-sizing: border-box; }
  html { -webkit-text-size-adjust: 100%; }
  body {
    margin: 0; background: var(--bg); color: var(--text);
    font: 400 var(--size)/1.65 "Iowan Old Style", "Charter", "Palatino Linotype", Georgia, serif;
    text-rendering: optimizeLegibility;
  }
  .bar {
    position: sticky; top: 0; z-index: 10; backdrop-filter: blur(8px);
    background: var(--bar); border-bottom: 1px solid var(--rule);
    font: 500 14px/1 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  }
  .bar-inner {
    max-width: var(--measure); margin: 0 auto; padding: 10px 16px;
    display: flex; align-items: center; gap: 14px;
  }
  .bar a { color: var(--muted); text-decoration: none; }
  .bar a:hover { color: var(--text); }
  .brand { font-weight: 700; color: var(--text) !important; margin-right: auto; }
  .bar button {
    font: inherit; color: var(--muted); background: none; border: 1px solid var(--rule);
    border-radius: 6px; padding: 5px 9px; cursor: pointer;
  }
  .bar button:hover { color: var(--text); }
  main { max-width: var(--measure); margin: 0 auto; padding: 40px 16px 96px; }
  .site {
    font: 600 13px/1.3 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    letter-spacing: 0.06em; text-transform: uppercase; color: var(--muted);
  }
  h1.title { font-size: 1.9em; line-height: 1.2; margin: 10px 0 14px; letter-spacing: -0.01em; }
  .meta {
    font: 400 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    color: var(--muted); padding-bottom: 24px; margin-bottom: 32px; border-bottom: 1px solid var(--rule);
  }
  .meta span + span::before { content: "·"; margin: 0 8px; }
  article a { color: var(--link); text-underline-offset: 2px; }
  article p { margin: 0 0 1.1em; }
  article h2, article h3, article h4 { line-height: 1.3; margin: 1.8em 0 0.6em; }
  article img, article video, .lead { display: block; max-width: 100%; height: auto; margin: 1.6em auto; border-radius: 4px; }
  article iframe { display: block; width: 100%; aspect-ratio: 16 / 9; border: 0; margin: 1.6em 0; }
  article figure { margin: 1.6em 0; }
  article figcaption, article .caption {
    font: 400 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: var(--muted); margin-top: -0.8em;
  }
  article blockquote { margin: 1.4em 0; padding: 0.6em 1.2em; background: var(--quote); border-left: 3px solid var(--rule); }
  article pre, article code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.82em; }
  article pre { overflow-x: auto; padding: 1em; background: var(--quote); border-radius: 4px; }
  article table { display: block; overflow-x: auto; border-collapse: collapse; font-size: 0.85em; }
  article td, article th { border: 1px solid var(--rule); padding: 6px 10px; }
  article hr { border: 0; border-top: 1px solid var(--rule); margin: 2em 0; }
  .empty { color: var(--muted); }
  @media (max-width: 600px) { :root { --size: 18px; } h1.title { font-size: 1.6em; } main { padding-top: 28px; } }
</style>
</head>
<body>
<header class="bar">
  <div class="bar-inner">
    <a class="brand" href="/">9001ft</a>
    {% if original_href %}<a href="{{ original_href }}">{{ reader_original }}</a>{% endif %}
    <a href="{{ source_url }}" rel="noopener noreferrer">{{ reader_source }} &#8599;</a>
    <button type="button" id="smaller" title="{{ reader_smaller }}" aria-label="{{ reader_smaller }}">A&minus;</button>
    <button type="button" id="larger" title="{{ reader_larger }}" aria-label="{{ reader_larger }}">A+</button>
    <button type="button" id="theme" title="{{ reader_theme }}" aria-label="{{ reader_theme }}">&#9681;</button>
  </div>
</header>
<main>
  {% if article.site %}<div class="site">{{ article.site }}</div>{% endif %}
  <h1 class="title">{{ article.title }}</h1>
  <div class="meta">
    {% if article.author %}<span>{{ article.author }}</span>{% endif %}
    {% if article.published %}<span>{{ article.published }}</span>{% endif %}
    {% if article.content %}<span>{{ reader_minutes.replace('{minutes}', article.minutes|string) }}</span>{% endif %}
  </div>
  {% if article.lead_image %}<img class="lead" src="{{ article.lead_image }}" alt="">{% endif %}
  {% if article.content %}
  <article>{{ article.content | safe }}</article>
  {% else %}
  <p class="empty">{{ reader_empty }} <a href="{{ original_href or source_url }}">{{ reader_original }}</a></p>
  {% endif %}
</main>
<script nonce="{{ nonce }}">
(function () {
  var root = document.documentElement;
  var themes = ["auto", "light", "dark", "sepia"];
  function load(key, fallback) { try { return localStorage.getItem(key) || fallback; } catch (e) { return fallback; } }
  function save(key, value) { try { localStorage.setItem(key, value); } catch (e) {} }
  var size = parseInt(load("readerSize", "0"), 10) || 0;
  function applySize() { if (size) { root.style.setProperty("--size", size + "px"); } }
  root.setAttribute("data-theme", load("readerTheme", "auto"));
  applySize();
  function step(delta) {
    var current = size || parseFloat(getComputedStyle(root).getPropertyValue("--size")) || 20;
    size = Math.min(28, Math.max(14, current + delta));
    applySize();
    save("readerSize", String(size));
  }
  document.getElementById("smaller").onclick = function () { step(-1); };
  document.getElementById("larger").onclick = function () { step(1); };
  document.addEventListener("error", function (event) {
    var img = event.target;
    if (img.tagName !== "IMG" || !img.dataset.fallbackSrc) { return; }
    var fallback = img.dataset.fallbackSrc;
    delete img.dataset.fallbackSrc;
    img.removeAttribute("srcset");
    img.src = fallback;
  }, true);
  document.getElementById("theme").onclick = function () {
    var next = themes[(themes.indexOf(root.getAttribute("data-theme")) + 1) % themes.length];
    root.setAttribute("data-theme", next);
    save("readerTheme", next);
  };
})();
</script>
</body>
</html>
"""
