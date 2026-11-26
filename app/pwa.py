"""Installable app: manifest, icons, offline page and the share target.

The service worker (static/sw.js) keeps only the app shell listed in
SHELL_URLS for an offline page. It never stores articles, Reader pages, API
answers or settings.
"""
import re

THEME_COLOR = "#9b4dca"
BACKGROUND_COLOR = "#ffffff"

ICON_FILES = {
    "icon-192.png", "icon-512.png", "maskable-192.png", "maskable-512.png", "apple-touch-icon.png",
}

# Kept in step with SHELL in static/sw.js (a test checks).
SHELL_URLS = [
    "/offline",
    "/banner.webp",
    "/favicon.ico",
    "/manifest.webmanifest",
    "/static/app.js",
    "/icons/icon-192.png",
    "/icons/icon-512.png",
    "/icons/maskable-192.png",
    "/icons/maskable-512.png",
    "/icons/apple-touch-icon.png",
]


def manifest(description):
    return {
        "id": "/",
        "name": "9001ft Ladder",
        "short_name": "9001ft",
        "description": description,
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "theme_color": THEME_COLOR,
        "background_color": BACKGROUND_COLOR,
        "icons": [
            {"src": "/icons/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "/icons/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": "/icons/maskable-192.png", "sizes": "192x192", "type": "image/png", "purpose": "maskable"},
            {"src": "/icons/maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
        # Android lets you share a link straight to 9001ft once it's installed.
        "share_target": {
            "action": "/share",
            "method": "GET",
            "params": {"title": "title", "text": "text", "url": "url"},
        },
    }


URL_IN_TEXT_RE = re.compile(r"https?://[^\s<>\"'`]+", re.I)
TRAILING_PUNCTUATION = ".,;:!?)]}>»”’'\""


def first_web_link(*values):
    """The first http(s) address in any of the values, without the full stop
    or bracket that often follows a link in shared text."""
    for value in values:
        match = URL_IN_TEXT_RE.search(value or "")
        if match:
            link = match.group(0)
            while link and link[-1] in TRAILING_PUNCTUATION:
                if link[-1] == ")" and link.count("(") >= link.count(")"):
                    break  # e.g. a Wikipedia title in brackets
                link = link[:-1]
            if re.match(r"^https?://[^/?#\s]+", link, re.I):
                return link
    return None


def content_security_policy(nonce):
    return (
        "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; "
        f"script-src 'nonce-{nonce}'; worker-src 'self'; manifest-src 'self'; "
        "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    )


# A small page in the app's look, for the offline and share messages.
MESSAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="{{ lang }}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="referrer" content="no-referrer">
<title>{{ title }} · 9001ft Ladder</title>
<link rel="icon" href="/favicon.ico" type="image/png">
<link rel="manifest" href="/manifest.webmanifest">
<meta name="theme-color" content="#9b4dca">
<link rel="apple-touch-icon" href="/icons/apple-touch-icon.png">
<script src="/static/app.js" defer nonce="{{ nonce }}"></script>
<style>
  :root { --bg: #ffffff; --text: #23232b; --muted: #6b6b76; --accent: #9b4dca; --accent-hover: #8233b3; }
  @media (prefers-color-scheme: dark) {
    :root { --bg: #191a21; --text: #ececf1; --muted: #a3a3b0; --accent: #7359f8; --accent-hover: #5c47c6; }
  }
  * { box-sizing: border-box; }
  body { margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center; background: var(--bg); color: var(--text); font: 16px/1.5 'Open Sans', -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
  main { width: 100%; max-width: 440px; padding: 32px 16px; text-align: center; }
  img { width: 112px; height: 112px; border-radius: 50%; box-shadow: 0 8px 24px rgba(0, 0, 0, 0.18); }
  h1 { font-size: 1.5rem; margin: 18px 0 8px; }
  p { margin: 0 0 24px; color: var(--muted); }
  a.button { display: inline-block; padding: 10px 24px; color: #fff; background: var(--accent); border-radius: 6px; font-weight: 600; text-decoration: none; }
  a.button:hover { background: var(--accent-hover); }
  a.button:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }
</style>
</head>
<body>
<main>
  <img src="/icons/icon-192.png" width="112" height="112" alt="">
  <h1>{{ title }}</h1>
  <p>{{ text }}</p>
  <a class="button" href="/">{{ button }}</a>
</main>
</body>
</html>
"""
