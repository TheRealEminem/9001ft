"""The app's look, shared by every page: bundled fonts and the comic theme.

Fonts: Bangers (headings) and Open Sans (text), both under the SIL Open Font
License (assets/fonts/OFL-*.txt), served from /fonts/ so no page calls out to
a font service. Pages with a CSP need font-src 'self'.

Theme: the cream-and-ink comic look of the home page (see climb.PALETTE_CSS)
for the secondary pages (settings, library, login, offline, share). Those
pages already style themselves with a small set of colour names (--bg, --text,
--card, --accent, ...); style_tag() redefines those names in the comic palette
and adds the comic components on top. It goes last in each page's <head>, so
it takes precedence over the page's own rules.
"""

import os

FONTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets", "fonts")

_LATIN = ("U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+0304, U+0308, U+0329, "
          "U+2000-206F, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215, U+FEFF, U+FFFD")
_LATIN_EXT = ("U+0100-02BA, U+02BD-02C5, U+02C7-02CC, U+02CE-02D7, U+02DD-02FF, U+0304, U+0308, U+0329, "
              "U+1D00-1DBF, U+1E00-1E9F, U+1EF2-1EFF, U+2020, U+20A0-20AB, U+20AD-20C4, U+2113, U+2C60-2C7F, U+A720-A7FF")

# file name -> (family, weight range, unicode range)
FONT_FILES = {
    "bangers-latin.woff2": ("Bangers", "400", _LATIN),
    "bangers-latin-ext.woff2": ("Bangers", "400", _LATIN_EXT),
    "open-sans-latin.woff2": ("Open Sans", "300 800", _LATIN),
    "open-sans-latin-ext.woff2": ("Open Sans", "300 800", _LATIN_EXT),
}


def font_path(name):
    """The file for /fonts/<name>, or None. Only the bundled files are served."""
    if name not in FONT_FILES:
        return None
    path = os.path.join(FONTS_DIR, name)
    return path if os.path.isfile(path) else None


FONT_FACE_CSS = "\n".join(
    f"@font-face {{ font-family: '{family}'; font-style: normal; font-weight: {weight}; font-display: swap; "
    f"src: url(/fonts/{name}) format('woff2'); unicode-range: {urange}; }}"
    for name, (family, weight, urange) in FONT_FILES.items()
)

_LIGHT = """
  --bg: #fdf3dc; --text: #1d2b30; --muted: #586469; --rule: #1d2b30; --card: #fffaf0; --field: #ffffff;
  --accent: #ad2a20; --accent-text: #ffffff; --accent-hover: #8f2219; --link: #ad2a20; --focus: #ad2a20;
  --bad: #b42318; --bad-bg: #fde3df; --good: #1b7f3b; --shadow: #1d2b30; --dots: rgba(29, 43, 48, 0.07);
  --yellow: #ffcf33; --yellow-hover: #ffd94f; --red: #c22f24;
"""
_DARK = """
  --bg: #0f2428; --text: #fbf1d6; --muted: #b4c4c2; --rule: #fbf1d6; --card: #16343a; --field: #0b1c1f;
  --accent: #ffcf33; --accent-text: #1d2b30; --accent-hover: #ffd94f; --link: #ffcf33; --focus: #ffcf33;
  --bad: #ff8a80; --bad-bg: #3a1c1c; --good: #7ad79a; --shadow: #050d0f; --dots: rgba(251, 241, 214, 0.05);
"""

_COMPONENTS = """
  :root { --display: Bangers, Impact, Haettenschweiler, 'Arial Narrow Bold', sans-serif; }
  body {
    background-color: var(--bg);
    background-image: radial-gradient(var(--dots) 1px, transparent 1.5px);
    background-size: 9px 9px;
    color: var(--text);
    font-family: 'Open Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  }
  h1 {
    font-family: var(--display); font-weight: 400; letter-spacing: 0.04em; line-height: 1.1;
    font-size: 2.3rem; margin: 10px 0 8px;
  }
  a { color: var(--link); text-underline-offset: 2px; }
  a:focus-visible, button:focus-visible, summary:focus-visible, input:focus-visible, select:focus-visible {
    outline: 3px solid var(--focus); outline-offset: 3px;
  }

  /* Panels: thick ink border and a hard shadow, like the home page's form. */
  .card, .notice, .page, .note, .mode, .body, .confirm, form.panel, main > form:not(#form) {
    background: var(--card);
    border: 3px solid var(--rule);
    border-radius: 14px;
  }
  .card, .page, form.panel, main > form:not(#form) { box-shadow: 6px 6px 0 var(--shadow); }
  form.panel, main > form:not(#form) { padding: 16px 18px; }
  .notice { box-shadow: 3px 3px 0 var(--shadow); font-weight: 600; }
  .notice.good { border-color: var(--good); }
  .notice.bad, .error { border-color: var(--bad); color: var(--bad); background: var(--bad-bg); }
  .empty { border: 3px dashed var(--rule); border-radius: 14px; background: var(--card); color: var(--muted); }

  /* Fields */
  input[type=text], input[type=password], input[type=url], input[type=search], select {
    background: var(--field); color: var(--text); border: 2px solid var(--rule); border-radius: 10px;
  }

  /* Buttons: yellow comic buttons for the main action, card-coloured for the rest. */
  button, .button, a.button {
    font-family: var(--display); font-weight: 400; letter-spacing: 0.06em; font-size: 1.05rem; line-height: 1.2;
    padding: 7px 15px; border: 2px solid var(--rule); border-radius: 11px;
    background: var(--card); color: var(--text) !important; box-shadow: 3px 3px 0 var(--shadow);
    text-decoration: none; text-transform: none; cursor: pointer;
  }
  button:hover, .button:hover, a.button:hover { transform: translate(-1px, -1px); box-shadow: 4px 4px 0 var(--shadow); }
  button:active, .button:active, a.button:active { transform: translate(2px, 2px); box-shadow: 1px 1px 0 var(--shadow); }
  button.primary, .button.primary, main > form:not(#form) button[type=submit], a.button {
    background: var(--yellow); color: #1d2b30 !important; border-width: 3px; box-shadow: 4px 4px 0 var(--shadow);
    font-size: 1.15rem;
  }
  button.primary:hover, .button.primary:hover, a.button:hover { background: var(--yellow-hover); }
  .danger, button.danger { background: var(--red) !important; color: #ffffff !important; }
  button:disabled { opacity: 0.6; cursor: progress; transform: none; }

  .bookmarklet-link {
    font-family: var(--display); font-weight: 400; letter-spacing: 0.05em; font-size: 1.1rem;
    border: 2px dashed var(--accent) !important; color: var(--accent) !important; background: var(--card);
  }

  /* Round avatar art (login, offline, share pages): an ink ring like the stickers. */
  main > img { border: 3px solid var(--rule); box-shadow: 5px 5px 0 var(--shadow) !important; }

  @media (prefers-reduced-motion: reduce) {
    button:hover, .button:hover, a.button:hover, button:active, .button:active, a.button:active { transform: none; }
  }
"""


def style_tag(auto_dark=False):
    """The <style> block for a secondary page. With auto_dark the page follows
    the system's dark mode on its own (for pages without the theme script);
    otherwise dark mode comes from a "dark" class on <html> or "dark-mode" on
    <body>, set by the page's script from the home page's toggle."""
    dark = f":root.dark, body.dark-mode {{{_DARK}}}"
    if auto_dark:
        dark += f"\n  @media (prefers-color-scheme: dark) {{ :root {{{_DARK}}} }}"
    return (f"<style>\n{FONT_FACE_CSS}\n  :root {{{_LIGHT}}}\n  {dark}\n{_COMPONENTS}</style>\n"
            "<meta name=\"theme-color\" content=\"#fdf3dc\" media=\"(prefers-color-scheme: light)\">\n"
            "<meta name=\"theme-color\" content=\"#0f2428\" media=\"(prefers-color-scheme: dark)\">\n")


def with_theme(template, auto_dark=False):
    """Add the theme just before </head> in a page template."""
    assert "</head>" in template
    return template.replace("</head>", style_tag(auto_dark) + "</head>", 1)
