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
from urllib.parse import urldefrag, urljoin, urlparse

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

LANG_RE = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{1,8}){0,4}$")

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


EDIT_LINK_TEXT = re.compile(r"^\s*\[?\s*edit(\s+source)?\s*\]?\s*$", re.I)


def _drop_edit_links(fragment):
    """Remove wiki-style "[edit]" links next to headings, with their brackets."""
    for link in fragment.find_all("a", href=True):
        if link.decomposed or "action=edit" not in link["href"] or not EDIT_LINK_TEXT.match(link.get_text()):
            continue
        target = link
        while target.parent is not None and target.parent.name in ("span", "sup") \
                and EDIT_LINK_TEXT.match(target.parent.get_text()):
            target = target.parent
        target.decompose()


def _clean_fragment(fragment_html, base_url):
    fragment = BeautifulSoup(fragment_html, "html.parser")
    for comment in fragment.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()
    for tag in fragment.find_all(DROP_TAGS):
        tag.decompose()
    _drop_edit_links(fragment)
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
        if tag.name == "a" and tag.get("href"):
            # Jump links within the article ("#cite_ref-6") can't work once ids
            # are stripped, and readability turns them into odd absolute URLs.
            target, anchor = urldefrag(tag["href"])
            if anchor and (not target or target == urldefrag(base_url)[0]):
                del tag["href"]
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
    # A relative <base href="/news/"> is relative to the page itself.
    base_url = urljoin(page_url, base_tag["href"]) if base_tag else page_url

    button = soup.find(id="ladder-reader-btn")
    if button:
        button.decompose()

    title = _meta(soup, "og:title", "twitter:title")
    site = _meta(soup, "og:site_name", "application-name") or (urlparse(page_url).hostname or "").removeprefix("www.")
    author = _author(soup)
    published = _published(soup)
    lead_image = _meta(soup, "og:image", "twitter:image")
    html_tag = soup.find("html")
    lang = (html_tag.get("lang") or html_tag.get("xml:lang") or "").strip() if html_tag else ""
    lang = lang if LANG_RE.match(lang) else ""

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
        "lang": lang,
        "content": str(fragment) if len(text) >= MIN_ARTICLE_CHARS else "",
    }


def content_security_policy(nonce):
    # The article HTML is sanitized, but the CSP makes sure nothing in it can
    # run script on this server's origin even if something slips through.
    return (
        "default-src 'none'; img-src * data: blob:; media-src *; frame-src *; "
        f"style-src 'unsafe-inline'; script-src 'nonce-{nonce}'; connect-src 'self'; "
        "worker-src 'self'; manifest-src 'self'; "
        "base-uri 'none'; form-action 'none'"
    )


# AI tidy re-sanitizes whatever the page sends back before using it.
clean_fragment = _clean_fragment


READER_TEMPLATE = """<!DOCTYPE html>
<html lang="{{ lang }}" data-theme="auto">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="referrer" content="no-referrer">
<title>{{ article.title or source_url }}</title>
<link rel="icon" href="/favicon.ico" type="image/png">
<link rel="manifest" href="/manifest.webmanifest">
<meta name="theme-color" content="#9b4dca">
<link rel="apple-touch-icon" href="/icons/apple-touch-icon.png">
<script src="/static/app.js" defer nonce="{{ nonce }}"></script>
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
  .bar button:disabled { opacity: 0.6; cursor: progress; }
  .tidy-note {
    font: 400 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: var(--muted);
    background: var(--quote); border-radius: 6px; padding: 10px 14px; margin: -12px 0 28px;
  }
  /* Read aloud and Export */
  :root { --highlight: #fff0b3; }
  :root[data-theme="dark"] { --highlight: #4d4114; }
  :root[data-theme="sepia"] { --highlight: #f0d88a; }
  @media (prefers-color-scheme: dark) { :root[data-theme="auto"] { --highlight: #4d4114; } }
  .bar-inner { flex-wrap: wrap; }
  .bar button:focus-visible, .bar a:focus-visible, .bar select:focus-visible { outline: 2px solid var(--link); outline-offset: 2px; }
  .tools { position: relative; }
  .menu {
    position: absolute; right: 0; top: calc(100% + 6px); z-index: 20; min-width: 200px;
    display: flex; flex-direction: column; gap: 2px; padding: 6px;
    background: var(--bg); border: 1px solid var(--rule); border-radius: 8px; box-shadow: 0 6px 24px rgba(0, 0, 0, 0.18);
  }
  .menu.menu-left { right: auto; left: 0; }
  .menu[hidden], .listen-panel[hidden] { display: none; }
  .bar .menu button { border: 0; text-align: left; padding: 9px 10px; color: var(--text); white-space: nowrap; }
  .bar .menu button:hover, .bar .menu button:focus-visible { background: var(--quote); }
  .listen-panel {
    max-width: var(--measure); margin: 0 auto; padding: 0 16px 10px;
    display: flex; flex-wrap: wrap; align-items: center; gap: 8px 14px;
  }
  .listen-panel label { display: inline-flex; align-items: center; gap: 6px; color: var(--muted); }
  .listen-panel select {
    font: inherit; color: var(--text); background: var(--bg); border: 1px solid var(--rule);
    border-radius: 6px; padding: 4px 6px; max-width: 220px;
  }
  .listen-status { color: var(--muted); font-weight: 400; }
  article .reading { background: var(--highlight); box-shadow: 0 0 0 6px var(--highlight); border-radius: 2px; }
  @media (prefers-reduced-motion: no-preference) { article .reading { transition: background-color 0.2s; } }
  .print-source { display: none; }
  @media print {
    :root, :root[data-theme] { --bg: #fff; --text: #000; --muted: #444; --rule: #bbb; --link: #000; --quote: #f2f2f2; }
    .bar, .tidy-note, .listen-panel, .menu { display: none !important; }
    body { background: #fff; color: #000; font-size: 12pt !important; }
    main { max-width: none; padding: 0; }
    .print-source { display: block; font: 400 9pt/1.4 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #444; word-break: break-all; margin: -20px 0 24px; }
    article .reading { background: none; box-shadow: none; }
    article a { color: inherit; }
    h1.title, article h2, article h3, article h4 { break-after: avoid; page-break-after: avoid; }
    article img, article figure, article blockquote, article pre, article table { break-inside: avoid; page-break-inside: avoid; }
    article img { max-height: 60vh; width: auto; }
    article pre { white-space: pre-wrap; overflow: visible; }
    article table { display: table; overflow: visible; }
    article iframe { display: none; }
    @page { margin: 2cm 1.8cm; }
  }
  @media (max-width: 600px) { :root { --size: 18px; } h1.title { font-size: 1.6em; } main { padding-top: 28px; } }
</style>
</head>
<body>
<header class="bar">
  <div class="bar-inner">
    <a class="brand" href="/">9001ft</a>
    {% if original_href %}<a href="{{ original_href }}">{{ reader_original }}</a>{% endif %}
    <a href="{{ source_url }}" rel="noopener noreferrer">{{ reader_source }} &#8599;</a>
    {% if keep_href %}<a href="{{ keep_href }}" id="keep-copy">&#128204; {{ keep_button }}</a>{% endif %}
    <button type="button" id="smaller" title="{{ reader_smaller }}" aria-label="{{ reader_smaller }}">A&minus;</button>
    <button type="button" id="larger" title="{{ reader_larger }}" aria-label="{{ reader_larger }}">A+</button>
    {% if article.content %}
    <button type="button" id="listen" title="{{ listen_button }}" aria-label="{{ listen_button }}" aria-expanded="false" aria-controls="listen-panel" hidden>&#128266;</button>
    <div class="tools">
      <button type="button" id="export" aria-expanded="false" aria-controls="export-menu">{{ export_button }} <span aria-hidden="true">&#9662;</span></button>
      <div class="menu" id="export-menu" role="group" aria-label="{{ export_menu }}" hidden>
        <button type="button" data-export="markdown">{{ export_markdown }}</button>
        <button type="button" data-export="epub">{{ export_epub }}</button>
        <button type="button" data-export="print">{{ export_print }}</button>
      </div>
    </div>
    {% endif %}
    {% if tidy_enabled and article.content %}<button type="button" id="tidy">&#10024; {{ tidy_button }}</button>{% endif %}
    <button type="button" id="theme" title="{{ reader_theme }}" aria-label="{{ reader_theme }}">&#9681;</button>
  </div>
  {% if article.content %}
  <div class="listen-panel" id="listen-panel" role="group" aria-label="{{ listen_controls }}" hidden>
    <button type="button" id="listen-play">{{ listen_pause }}</button>
    <button type="button" id="listen-stop">{{ listen_stop }}</button>
    <label>{{ listen_speed }}
      <select id="listen-rate">
        <option value="0.8">0.8&times;</option><option value="1" selected>1&times;</option>
        <option value="1.25">1.25&times;</option><option value="1.5">1.5&times;</option>
        <option value="1.75">1.75&times;</option><option value="2">2&times;</option>
      </select>
    </label>
    <label>{{ listen_voice }} <select id="listen-voice"></select></label>
    <span class="listen-status" id="listen-status" role="status" aria-live="polite"></span>
  </div>
  {% endif %}
</header>
<main>
  {% if article.site %}<div class="site">{{ article.site }}</div>{% endif %}
  <h1 class="title">{{ article.title }}</h1>
  <div class="meta">
    {% if article.author %}<span>{{ article.author }}</span>{% endif %}
    {% if article.published %}<span>{{ article.published }}</span>{% endif %}
    {% if article.content %}<span>{{ reader_minutes.replace('{minutes}', article.minutes|string) }}</span>{% endif %}
  </div>
  <p class="print-source">{{ source_url }}</p>
  <p class="tidy-note" id="tidy-note" role="status" hidden></p>
  <p class="tidy-note" id="export-note" role="status" hidden></p>
  {% if article.lead_image %}<img class="lead" src="{{ article.lead_image }}" alt="">{% endif %}
  {% if article.content %}
  <article{% if article.lang %} lang="{{ article.lang }}"{% endif %}>{{ article.content | safe }}</article>
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

  var tidyButton = document.getElementById("tidy");
  if (tidyButton) {
    var TIDY = {{ tidy_strings | tojson }};
    var article = document.querySelector("article");
    var note = document.getElementById("tidy-note");
    var original = null, tidied = null, showingTidy = false;
    function label(text) { tidyButton.textContent = text; }
    tidyButton.onclick = function () {
      if (tidied !== null) {
        showingTidy = !showingTidy;
        article.innerHTML = showingTidy ? tidied : original;
        label(showingTidy ? TIDY.show_original : TIDY.show_tidy);
        return;
      }
      original = article.innerHTML;
      tidyButton.disabled = true;
      label(TIDY.working);
      note.hidden = false;
      note.textContent = TIDY.working_note;
      fetch("/reader/tidy", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ html: original, url: {{ source_url | tojson }} })
      }).then(function (response) {
        return response.json().then(function (data) {
          if (!response.ok) { throw new Error(data.error || String(response.status)); }
          return data;
        });
      }).then(function (data) {
        tidied = data.html;
        showingTidy = true;
        article.innerHTML = tidied;
        label(TIDY.show_original);
        var s = data.stats;
        note.textContent = TIDY.done.replace("{model}", data.model).replace("{seconds}", data.seconds)
          .replace("{headings}", s.headings).replace("{joined}", s.joined)
          .replace("{lists}", s.lists).replace("{dropped}", s.dropped_words);
      }).catch(function (error) {
        note.textContent = TIDY.failed.replace("{reason}", error.message);
        label("\\u2728 " + TIDY.button);
      }).then(function () { tidyButton.disabled = false; });
    };
  }

  var articleEl = document.querySelector("article");
  var DOC = {{ {"title": article.title, "url": source_url, "site": article.site, "byline": article.author,
                "date": article.published, "lang": article.lang or ""} | tojson }};

  // Read aloud: the browser's own speech, so nothing leaves this computer.
  var listenButton = document.getElementById("listen");
  var synth = window.speechSynthesis;
  if (listenButton && articleEl && synth && window.SpeechSynthesisUtterance) {
    var LISTEN = {{ {"play": listen_play, "pause": listen_pause, "reading": listen_reading, "paused": listen_paused,
                     "stopped": listen_stopped, "done": listen_done, "no_voices": listen_no_voices} | tojson }};
    var panel = document.getElementById("listen-panel");
    var playButton = document.getElementById("listen-play");
    var stopButton = document.getElementById("listen-stop");
    var rateSelect = document.getElementById("listen-rate");
    var voiceSelect = document.getElementById("listen-voice");
    var listenStatus = document.getElementById("listen-status");
    var reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    var docLang = (articleEl.getAttribute("lang") || root.getAttribute("lang") || navigator.language || "en").toLowerCase();
    var langPrefix = docLang.split("-")[0];
    var BLOCKS = "h1,h2,h3,h4,h5,h6,p,li,blockquote,pre,figcaption,dt,dd,td,th,caption";
    var CONTAINERS = BLOCKS + ",ul,ol,dl,table,thead,tbody,tfoot,tr,figure,div,section,article,aside,header,footer";
    var voices = [], chunks = [], index = 0, total = 0, lastBlock = -1;
    var playing = false, token = 0, current = null, utterance = null, failures = 0;
    listenButton.hidden = false;
    synth.cancel();

    var voiceKey = "readerVoice-" + langPrefix;
    function matchScore(voice) {
      var lang = (voice.lang || "").toLowerCase().replace("_", "-");
      return lang === docLang ? 0 : lang.split("-")[0] === langPrefix ? 1 : 2;
    }
    function loadVoices() {
      var list = synth.getVoices().slice();
      if (!list.length) { return; }
      list.sort(function (a, b) { return matchScore(a) - matchScore(b) || a.name.localeCompare(b.name); });
      voices = list;
      var saved = load(voiceKey, "");
      function first(test) { return voices.filter(test)[0]; }
      var chosen = first(function (v) { return v.voiceURI === saved; })
        || first(function (v) { return matchScore(v) < 2 && v["default"]; })
        || first(function (v) { return matchScore(v) < 2 && v.localService; })
        || first(function (v) { return matchScore(v) < 2; })
        || first(function (v) { return v["default"]; }) || voices[0];
      voiceSelect.textContent = "";
      voices.forEach(function (voice, i) {
        var option = document.createElement("option");
        option.value = String(i);
        option.textContent = voice.name + " (" + voice.lang + ")";
        option.selected = voice === chosen;
        voiceSelect.appendChild(option);
      });
    }
    loadVoices();
    if (synth.addEventListener) { synth.addEventListener("voiceschanged", loadVoices); }
    else { synth.onvoiceschanged = loadVoices; }
    rateSelect.value = load("readerRate", "1");
    if (!rateSelect.value) { rateSelect.value = "1"; }

    // The element's own words, leaving nested blocks to be read on their own.
    function ownText(el) {
      var text = "";
      for (var node = el.firstChild; node; node = node.nextSibling) {
        if (node.nodeType === 3) { text += node.nodeValue; }
        else if (node.nodeType === 1 && !node.matches(CONTAINERS)) { text += node.tagName === "BR" ? " " : ownText(node); }
      }
      return text;
    }
    // Browsers cut off long utterances, so speak a few sentences at a time.
    function split(text) {
      text = text.replace(/\\s+/g, " ").trim();
      if (!text) { return []; }
      var sentences;
      if (window.Intl && Intl.Segmenter) {
        sentences = Array.from(new Intl.Segmenter(docLang, { granularity: "sentence" }).segment(text), function (s) { return s.segment; });
      } else {
        sentences = text.match(/[^.!?。！？]+[.!?。！？]*\\s*/g) || [text];
      }
      var out = [], buffer = "";
      function push(piece) {
        piece = piece.trim();
        if (!piece) { return; }
        if (buffer && (buffer + " " + piece).length > 220) { out.push(buffer); buffer = piece; }
        else { buffer = buffer ? buffer + " " + piece : piece; }
      }
      sentences.forEach(function (sentence) {
        sentence = sentence.trim();
        while (sentence.length > 260) {
          var cut = Math.max(sentence.lastIndexOf(", ", 220) + 1, sentence.lastIndexOf(" ", 220));
          if (cut < 60) { cut = 220; }
          push(sentence.slice(0, cut));
          sentence = sentence.slice(cut).trim();
        }
        push(sentence);
      });
      if (buffer) { out.push(buffer); }
      return out;
    }
    function build() {
      var blocks = [];
      var title = document.querySelector("h1.title");
      if (title && title.textContent.trim()) { blocks.push({ el: title, text: title.textContent }); }
      [articleEl].concat(Array.prototype.slice.call(articleEl.querySelectorAll(BLOCKS + ",div,section,figure"))).forEach(function (el) {
        var text = ownText(el);
        if (text.trim()) { blocks.push({ el: el, text: text }); }
      });
      chunks = [];
      blocks.forEach(function (block, i) {
        split(block.text).forEach(function (text) { chunks.push({ el: block.el, text: text, block: i }); });
      });
      total = blocks.length;
      index = 0;
      lastBlock = -1;
    }
    function setStatus(text) { listenStatus.textContent = text; }
    function highlight(el) {
      if (current === el) { return; }
      if (current) { current.classList.remove("reading"); }
      current = el;
      if (!el) { return; }
      el.classList.add("reading");
      var box = el.getBoundingClientRect();
      var barBottom = document.querySelector(".bar").getBoundingClientRect().bottom;
      if (box.top < barBottom || box.bottom > window.innerHeight) {
        el.scrollIntoView({ block: "center", behavior: reduceMotion ? "auto" : "smooth" });
      }
    }
    function speak() {
      if (!playing) { return; }
      var chunk = chunks[index];
      if (!chunk || !chunk.el.isConnected) { finish(chunk ? LISTEN.stopped : LISTEN.done); return; }
      highlight(chunk.el);
      if (chunk.block !== lastBlock) {
        lastBlock = chunk.block;
        setStatus(LISTEN.reading.replace("{current}", chunk.block + 1).replace("{total}", total));
      }
      var mine = ++token;
      utterance = new SpeechSynthesisUtterance(chunk.text);  // kept so it isn't garbage-collected mid-sentence
      var voice = voices[parseInt(voiceSelect.value, 10)];
      if (voice) { utterance.voice = voice; utterance.lang = voice.lang; } else { utterance.lang = docLang; }
      utterance.rate = parseFloat(rateSelect.value) || 1;
      utterance.onstart = function () { failures = 0; };
      utterance.onend = function () {
        if (mine !== token || !playing) { return; }
        index += 1;
        speak();
      };
      utterance.onerror = function (event) {
        if (mine !== token || !playing || event.error === "interrupted" || event.error === "canceled") { return; }
        failures += 1;
        if (failures >= 3) { finish(voices.length ? LISTEN.stopped : LISTEN.no_voices); return; }
        index += 1;
        speak();
      };
      synth.speak(utterance);
    }
    function silence() {
      token += 1;  // ignore the end events of whatever was speaking
      synth.cancel();
    }
    function play() {
      if (!chunks.length || index >= chunks.length || !chunks[index].el.isConnected) { build(); }
      if (!chunks.length) { setStatus(LISTEN.done); return; }
      if (!voices.length) { loadVoices(); }
      playing = true;
      failures = 0;
      lastBlock = -1;
      playButton.textContent = LISTEN.pause;
      if (synth.speaking || synth.pending) { silence(); setTimeout(speak, 60); }
      else { speak(); }
    }
    function pause() {
      playing = false;
      silence();
      playButton.textContent = LISTEN.play;
      setStatus(LISTEN.paused);
    }
    function finish(message) {
      playing = false;
      silence();
      highlight(null);
      chunks = [];
      index = 0;
      playButton.textContent = LISTEN.play;
      setStatus(message || "");
    }
    function restart() {
      if (!playing) { return; }
      silence();
      setTimeout(speak, 60);
    }
    function closePanel() {
      panel.hidden = true;
      listenButton.setAttribute("aria-expanded", "false");
    }
    listenButton.onclick = function () {
      if (panel.hidden) {
        panel.hidden = false;
        listenButton.setAttribute("aria-expanded", "true");
        play();
      } else {
        finish(LISTEN.stopped);
        closePanel();
      }
    };
    playButton.onclick = function () { if (playing) { pause(); } else { play(); } };
    stopButton.onclick = function () {
      finish(LISTEN.stopped);
      closePanel();
      listenButton.focus();
    };
    panel.addEventListener("keydown", function (event) { if (event.key === "Escape") { stopButton.onclick(); } });
    rateSelect.onchange = function () { save("readerRate", rateSelect.value); restart(); };
    voiceSelect.onchange = function () {
      var voice = voices[parseInt(voiceSelect.value, 10)];
      if (voice) { save(voiceKey, voice.voiceURI); }
      restart();
    };
    // Tidy swaps the article out: stop first so the highlight isn't saved with it.
    document.addEventListener("click", function (event) {
      if ((playing || current) && event.target.closest && event.target.closest("#tidy")) { finish(LISTEN.stopped); }
    }, true);
    window.addEventListener("pagehide", silence);
    window.addEventListener("beforeunload", silence);
  }

  // Export: the server converts the article exactly as it's shown now.
  var exportButton = document.getElementById("export");
  if (exportButton && articleEl) {
    var EXPORT = {{ {"working": export_working, "done": export_done, "failed": export_failed} | tojson }};
    var menu = document.getElementById("export-menu");
    var exportNote = document.getElementById("export-note");
    var busy = false;
    function setMenu(open) {
      menu.hidden = !open;
      exportButton.setAttribute("aria-expanded", open ? "true" : "false");
      if (open) {  // on narrow screens the button can sit near the left edge
        menu.classList.remove("menu-left");
        if (menu.getBoundingClientRect().left < 8) { menu.classList.add("menu-left"); }
      }
    }
    function showExportNote(text) { exportNote.hidden = false; exportNote.textContent = text; }
    exportButton.onclick = function () {
      setMenu(menu.hidden);
      if (!menu.hidden) { menu.querySelector("button").focus(); }
    };
    menu.addEventListener("keydown", function (event) {
      var items = Array.prototype.slice.call(menu.querySelectorAll("button"));
      var at = items.indexOf(document.activeElement);
      if (event.key === "Escape") { setMenu(false); exportButton.focus(); }
      else if (event.key === "ArrowDown") { event.preventDefault(); items[(at + 1) % items.length].focus(); }
      else if (event.key === "ArrowUp") { event.preventDefault(); items[(at - 1 + items.length) % items.length].focus(); }
    });
    document.addEventListener("click", function (event) {
      if (!menu.hidden && !(event.target.closest && event.target.closest(".tools"))) { setMenu(false); }
    });
    menu.addEventListener("click", function (event) {
      var button = event.target.closest("button[data-export]");
      if (!button) { return; }
      setMenu(false);
      exportButton.focus();
      var format = button.getAttribute("data-export");
      if (format === "print") { window.print(); } else { download(format); }
    });
    function download(format) {
      if (busy) { return; }
      busy = true;
      showExportNote(EXPORT.working);
      var lead = document.querySelector("img.lead");
      var payload = { format: format, html: (lead ? lead.outerHTML : "") + articleEl.innerHTML };
      Object.keys(DOC).forEach(function (key) { payload[key] = DOC[key]; });
      fetch("/reader/export", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      }).then(function (response) {
        if (!response.ok) {
          return response.json().catch(function () { return {}; }).then(function (data) {
            throw new Error(data.error || String(response.status));
          });
        }
        var match = /filename="([^"]+)"/.exec(response.headers.get("Content-Disposition") || "");
        var name = match ? match[1] : "article";
        return response.blob().then(function (blob) {
          var link = document.createElement("a");
          link.href = URL.createObjectURL(blob);
          link.download = name;
          link.hidden = true;
          document.body.appendChild(link);
          link.click();
          link.remove();
          setTimeout(function () { URL.revokeObjectURL(link.href); }, 10000);
          showExportNote(EXPORT.done.replace("{filename}", name));
        });
      })["catch"](function (error) {
        showExportNote(EXPORT.failed.replace("{reason}", error.message));
      }).then(function () { busy = false; });
    }
  }
})();
</script>
</body>
</html>
"""
