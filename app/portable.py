import html as html_lib
import json
import os
import flask
import re
import requests
import secrets
import uuid
import threading
import time
from functools import lru_cache
from urllib.parse import urljoin, urlparse, quote, unquote
from flask import request, Response
from bs4 import BeautifulSoup


def load_local_env(path=os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env.local")):
    """Read KEY=VALUE lines from .env.local (git-ignored) into the environment.
    Real environment variables win. Used for optional settings like AI tidy."""
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip("'\""))
    except OSError:
        pass


load_local_env()

import reader  # noqa: E402
import ai_settings  # noqa: E402
import settings_page  # noqa: E402
import tidy  # noqa: E402

app = flask.Flask(__name__)
# The Send to Reader bookmarklet posts a whole page; Werkzeug's default limit
# for a form field is 500 KB, which many article pages exceed.
app.config["MAX_FORM_MEMORY_SIZE"] = 25 * 1024 * 1024
app.config["MAX_CONTENT_LENGTH"] = 30 * 1024 * 1024

_DEFAULT_STRINGS = {
    "heading": "Enter Website Link",
    "label": "Link to the page you want to read:",
    "submit": "Submit",
    "toggle_dark_mode": "Toggle Dark Mode",
    "status_heading": "Fetching Article",
    "error_title": "Something went wrong",
    "retry_button": "Try Another URL",
    "elapsed_template": "{seconds}s elapsed",
    "connection_lost_error": "Connection to server lost. The page may be taking too long to load.",
    "step_connect": "Connecting to website...",
    "step_fetch": "Downloading page content...",
    "step_detect": "Checking for anti-bot challenges...",
    "step_fallback_freedium": "Trying Freedium (Medium mirror)...",
    "step_fallback_org": "Trying archive.org snapshot...",
    "step_fallback_ph": "Trying archive.today / archive.ph snapshot...",
    "step_process": "Processing article...",
    "step_cleanup": "Cleaning up & preparing view...",
    "step_done": "Done!",
    "invalid_url": "Invalid URL",
    "medium_challenge_error": (
        "Medium served an anti-bot challenge and Freedium / archive.org / "
        "archive.today all came back empty. The article may be too new to "
        "have been archived yet."
    ),
    "challenge_error": (
        "The site served an anti-bot challenge (Cloudflare or similar) "
        "and no usable snapshot was found on archive.org or archive.today. "
        "This site is actively blocking automated requests."
    ),
    "timeout_error": "The website took too long to respond (30s timeout). Try again later.",
    "connection_error": "Could not connect to the website. Check the URL and try again.",
    "request_error": "Failed to fetch the page.",
    "unexpected_error": "Unexpected error while fetching the page.",
    "reader_checkbox": "Open in Reader view",
    "reader_button": "Reader view",
    "reader_original": "Original page",
    "reader_source": "Source",
    "reader_minutes": "{minutes} min read",
    "reader_smaller": "Smaller text",
    "reader_larger": "Larger text",
    "reader_theme": "Change theme",
    "bookmarklet_label": "Send to 9001ft Reader",
    "bookmarklet_hint": (
        "Drag this to your bookmarks bar. On any article you're reading in your own "
        "browser, including ones you subscribe to, click it to open that page in Reader view."
    ),
    "tidy_button": "Tidy up",
    "tidy_working": "Tidying…",
    "tidy_working_note": (
        "Asking the AI how this page should be laid out. It only arranges what's "
        "already here and never rewrites it. This can take a minute."
    ),
    "tidy_done": (
        "Tidied by {model} in {seconds}s: {headings} headings added, {joined} pieces joined, "
        "{lists} lists, {dropped} words of clutter removed. The wording is unchanged."
    ),
    "tidy_show_original": "Show original",
    "tidy_show_tidy": "Show tidied",
    "tidy_failed": "Couldn't tidy this page: {reason}.",
    "settings_link": "AI settings (optional)",
    "reader_empty": (
        "Couldn't find the article text on this page. "
        "Try the original page instead."
    ),
}


def get_locale():
    locale = os.environ.get("LOCALE", "en")
    if not re.match(r"^[a-zA-Z0-9_-]+$", locale):
        locale = "en"
    return locale


@lru_cache(maxsize=None)
def load_strings_for_locale(locale):
    strings = dict(_DEFAULT_STRINGS)
    locale_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "locales")
    locale_file = os.path.join(locale_dir, f"{locale}.json")

    if locale != "en":
        try:
            with open(locale_file, "r", encoding="utf-8") as file:
                strings.update(json.load(file))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            strings = dict(_DEFAULT_STRINGS)
            locale = "en"

    strings["lang"] = locale
    return strings


def load_strings():
    return dict(load_strings_for_locale(get_locale()))


googlebot_headers = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 6.0.1; Nexus 5X Build/MMB29P) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.6533.119 Mobile Safari/537.36 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
}

jobs = {}
jobs_lock = threading.Lock()

page_cache = {}
page_cache_lock = threading.Lock()
PAGE_CACHE_TTL = 300


class UserFacingError(Exception):
    def __init__(self, user_message):
        super().__init__(user_message)
        self.user_message = user_message

html = """
<!DOCTYPE html>
<html lang="{{ lang }}">

<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="X-UA-Compatible" content="IE=edge">
    <title>9001ft Ladder</title>
    <link rel="icon" href="/favicon.ico" type="image/png">
    <link href="https://fonts.googleapis.com/css2?family=Open+Sans:wght@400;600&display=swap" rel="stylesheet" async>
    <style>
        * { box-sizing: border-box; }
        body {
            font-family: 'Open Sans', sans-serif;
            background-color: #FFF;
            margin: 0;
            padding: 0;
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
            transition: background-color 0.3s, color 0.3s;
        }

        #form-view { width: 100%; max-width: 760px; padding: 56px 16px 32px; }

        .banner {
            display: block;
            width: 100%;
            height: auto;
            aspect-ratio: 1983 / 793;
            border-radius: 14px;
            margin-bottom: 28px;
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.18);
        }

        #url-form { max-width: 520px; margin: 0 auto; }

        .bookmarklet { max-width: 520px; margin: 28px auto 0; text-align: center; font-size: 14px; color: #555; }
        .bookmarklet p { margin: 10px 0 0; line-height: 1.5; }
        .bookmarklet-link {
            display: inline-block;
            padding: 9px 16px;
            border: 2px dashed #9b4dca;
            border-radius: 999px;
            color: #9b4dca;
            font-weight: 600;
            text-decoration: none;
            cursor: grab;
        }
        body.dark-mode .bookmarklet { color: #bbb; }
        .settings-link { color: inherit; }
        body.dark-mode .bookmarklet-link { color: #b9a8ff; border-color: #7359f8; }

        h1 {
            font-size: 1.5rem;
            margin-bottom: 20px;
            text-align: center;
            color: #333;
        }

        label {
            display: block;
            margin-bottom: 10px;
            font-weight: bold;
        }

        input[type=text] {
            padding: 10px;
            margin-bottom: 10px;
            border: 1px solid #ccc;
            border-radius: 5px;
            width: 100%;
            font-size: 1rem;
        }

        input[type="submit"] {
            padding: 16px;
            background-color: #a327f0;
            color: #fff;
            border: none;
            border-radius: 5px;
            width: 100%;
            text-transform: uppercase;
            font-size: 1rem;
            font-weight: 600;
            cursor: pointer;
            transition: background-color 0.3s ease;
        }

        input[type="submit"]:hover {
            background-color: #821fc0;
        }

        .reader-option {
            display: flex;
            align-items: center;
            gap: 8px;
            margin: -4px 0 16px;
            font-size: 14px;
            cursor: pointer;
        }

        .reader-option input { width: auto; margin: 0; }

        .dark-mode-toggle {
            position: absolute;
            top: 10px;
            right: 10px;
        }

        .dark-mode-toggle input {
            position: absolute;
            width: 1px;
            height: 1px;
            padding: 0;
            margin: -1px;
            overflow: hidden;
            clip: rect(0, 0, 0, 0);
            white-space: nowrap;
            border: 0;
        }

        .dark-mode-toggle label {
            cursor: pointer;
            width: 54px;
            height: 28px;
            background: #cbd5e1;
            display: block;
            border-radius: 100px;
            position: relative;
            transition: background-color 0.3s, border-color 0.3s;
            border: 1px solid #b8c2cc;
        }

        .dark-mode-toggle label:hover {
            background: #b8c2cc;
            border-color: #a1b0be;
        }

        .dark-mode-toggle input:focus-visible + label {
            outline: 2px solid #a327f0;
            outline-offset: 3px;
        }

        body.dark-mode .dark-mode-toggle input:focus-visible + label {
            outline-color: #7359f8;
        }

        .dark-mode-toggle .thumb {
            position: absolute;
            top: 2px;
            left: 2px;
            width: 22px;
            height: 22px;
            background: #fff;
            border-radius: 50%;
            transition: transform 0.3s cubic-bezier(0.4, 0, 0.2, 1), background-color 0.3s, box-shadow 0.3s;
            display: flex;
            align-items: center;
            justify-content: center;
            box-shadow: 0 1px 3px rgba(0, 0, 0, 0.15);
        }

        .dark-mode-toggle input:checked+label {
            background: #312e81;
            border-color: #4338ca;
        }

        .dark-mode-toggle input:checked+label:hover {
            background: #2e2a78;
            border-color: #3b379d;
        }

        .dark-mode-toggle input:checked+label .thumb {
            transform: translateX(26px);
            background: #1e1b4b;
            box-shadow: 0 1px 3px rgba(0, 0, 0, 0.4), 0 0 8px rgba(99, 102, 241, 0.4);
        }

        .dark-mode-toggle .icon {
            position: absolute;
            width: 14px;
            height: 14px;
            transition: transform 0.3s cubic-bezier(0.4, 0, 0.2, 1), opacity 0.3s ease;
        }

        .dark-mode-toggle .icon.sun {
            color: #ea580c;
            opacity: 1;
            transform: scale(1) rotate(0deg);
        }

        .dark-mode-toggle .icon.moon {
            color: #4f46e5;
            opacity: 0;
            transform: scale(0) rotate(-90deg);
        }

        .dark-mode-toggle input:checked+label .icon.sun {
            opacity: 0;
            transform: scale(0) rotate(90deg);
        }

        .dark-mode-toggle input:checked+label .icon.moon {
            color: #c7d2fe;
            opacity: 1;
            transform: scale(1) rotate(0deg);
        }

        @media only screen and (max-width: 600px) {
            form, .status-container { padding: 10px; }
            h1 { font-size: 1.2rem; }
        }

        body.dark-mode { background-color: #191a21; color: #FFF; }
        body.dark-mode h1 { color: #FFF; }
        body.dark-mode input[type=text] { background-color: #555; border: 1px solid #777; color: #FFF; }
        body.dark-mode input[type="submit"] { background-color: #7359f8; }
        body.dark-mode input[type="submit"]:hover { background-color: #5c47c6; }
        body.dark-mode .status-container { background: #23243a; }
        body.dark-mode .step { color: #aaa; }
        body.dark-mode .step.active { color: #fff; }
        body.dark-mode .step.done { color: #7359f8; }
        body.dark-mode .error-box { background: #3a1c1c; border-color: #ff6b6b; color: #ffaaaa; }

        /* Status page styles */
        .status-container {
            max-width: 480px;
            width: 100%;
            padding: 30px;
            text-align: center;
        }

        .status-container h1 { margin-bottom: 30px; }

        .url-display {
            font-size: 0.85rem;
            color: #888;
            margin-bottom: 24px;
            word-break: break-all;
        }

        .progress-bar-track {
            width: 100%;
            height: 6px;
            background: #e0e0e0;
            border-radius: 3px;
            overflow: hidden;
            margin-bottom: 28px;
        }

        body.dark-mode .progress-bar-track { background: #444; }

        .progress-bar-fill {
            height: 100%;
            width: 0%;
            background: linear-gradient(90deg, #a327f0, #7359f8);
            border-radius: 3px;
            transition: width 0.5s ease;
        }

        .steps { text-align: left; margin-bottom: 24px; }

        .step {
            display: flex;
            align-items: center;
            gap: 12px;
            padding: 8px 0;
            color: #bbb;
            font-size: 0.95rem;
            transition: color 0.3s;
        }

        .step.active { color: #333; font-weight: 600; }
        .step.done { color: #a327f0; }

        .step-icon {
            width: 24px;
            height: 24px;
            flex-shrink: 0;
            display: flex;
            align-items: center;
            justify-content: center;
        }

        .spinner {
            width: 18px;
            height: 18px;
            border: 2px solid #e0e0e0;
            border-top-color: #a327f0;
            border-radius: 50%;
            animation: spin 0.8s linear infinite;
        }

        body.dark-mode .spinner { border-color: #444; border-top-color: #7359f8; }

        @keyframes spin { to { transform: rotate(360deg); } }

        .checkmark { color: #a327f0; font-weight: bold; font-size: 1.1rem; }

        .elapsed {
            font-size: 0.85rem;
            color: #999;
            margin-top: 4px;
        }

        .error-box {
            background: #fff0f0;
            border: 1px solid #ffcccc;
            border-radius: 8px;
            padding: 16px;
            margin-top: 16px;
            color: #cc3333;
            font-size: 0.9rem;
            text-align: left;
        }

        .error-box strong { display: block; margin-bottom: 6px; }

        .retry-btn {
            display: inline-block;
            margin-top: 16px;
            padding: 10px 24px;
            background: #a327f0;
            color: #fff;
            border: none;
            border-radius: 5px;
            font-size: 0.95rem;
            font-weight: 600;
            cursor: pointer;
            text-decoration: none;
        }

        .retry-btn:hover { background: #821fc0; }
    </style>
</head>

<body>
    <div class="dark-mode-toggle">
        <input type="checkbox" id="dark-mode-toggle">
        <label for="dark-mode-toggle" title="{{ toggle_dark_mode }}">
            <span class="thumb">
                <svg class="icon sun" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                    <circle cx="12" cy="12" r="5"></circle>
                    <line x1="12" y1="1" x2="12" y2="3"></line>
                    <line x1="12" y1="21" x2="12" y2="23"></line>
                    <line x1="4.22" y1="4.22" x2="5.64" y2="5.64"></line>
                    <line x1="18.36" y1="18.36" x2="19.78" y2="19.78"></line>
                    <line x1="1" y1="12" x2="3" y2="12"></line>
                    <line x1="21" y1="12" x2="23" y2="12"></line>
                    <line x1="4.22" y1="19.78" x2="5.64" y2="18.36"></line>
                    <line x1="18.36" y1="5.64" x2="19.78" y2="4.22"></line>
                </svg>
                <svg class="icon moon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"></path>
                </svg>
            </span>
        </label>
    </div>

    <div id="form-view">
        <img class="banner" src="/banner.webp" width="1983" height="793"
             alt="9001ft Ladder: a grandmother carrying a lasagna climbs a huge ladder over a Subscribe wall. All this for one lasagna recipe.">
        <form id="url-form">
            <h1>{{ heading }}</h1>
            <label for="link">{{ label }}</label>
            <input type="text" id="link" name="link" required autofocus>
            <label class="reader-option" for="reader-mode">
                <input type="checkbox" id="reader-mode"> {{ reader_checkbox }}
            </label>
            <input type="submit" value="{{ submit }}">
        </form>
        <div class="bookmarklet">
            <a class="bookmarklet-link" href="{{ reader_bookmarklet }}" onclick="return false;"><span aria-hidden="true">&#128214;</span>&nbsp; {{ bookmarklet_label }}</a>
            <p>{{ bookmarklet_hint }}</p>
            <p><a class="settings-link" href="/settings">&#9881;&#65039; {{ settings_link }}</a></p>
        </div>
    </div>

    <div id="status-view" style="display:none;">
        <div class="status-container">
            <h1>{{ status_heading }}</h1>
            <div class="url-display" id="status-url"></div>
            <div class="progress-bar-track">
                <div class="progress-bar-fill" id="progress-fill"></div>
            </div>
            <div class="steps" id="steps-list"></div>
            <div class="elapsed" id="elapsed-time"></div>
            <div id="error-area"></div>
        </div>
    </div>

    <script>
        const toggleSwitch = document.getElementById('dark-mode-toggle');
        const currentTheme = localStorage.getItem('theme') || (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");

        if (currentTheme === "dark") {
            document.body.classList.add("dark-mode");
            toggleSwitch.checked = true;
        }

        toggleSwitch.addEventListener('change', function () {
            if (this.checked) {
                document.body.classList.add("dark-mode");
                localStorage.setItem('theme', 'dark');
            } else {
                document.body.classList.remove("dark-mode");
                localStorage.setItem('theme', 'light');
            }
        });

        const readerToggle = document.getElementById('reader-mode');
        try { readerToggle.checked = localStorage.getItem('readerMode') === '1'; } catch (e) {}
        readerToggle.addEventListener('change', function () {
            try { localStorage.setItem('readerMode', this.checked ? '1' : '0'); } catch (e) {}
        });

        const UI_STRINGS = {{ ui_strings | tojson }};
        const STEPS = {{ steps | tojson }};

        function renderSteps(activeIdx) {
            const container = document.getElementById('steps-list');
            container.innerHTML = '';
            STEPS.forEach((step, i) => {
                const div = document.createElement('div');
                div.className = 'step' + (i < activeIdx ? ' done' : i === activeIdx ? ' active' : '');
                const icon = document.createElement('div');
                icon.className = 'step-icon';
                if (i < activeIdx) {
                    icon.innerHTML = '<span class="checkmark">&#10003;</span>';
                } else if (i === activeIdx) {
                    icon.innerHTML = '<div class="spinner"></div>';
                } else {
                    icon.innerHTML = '<span style="color:#ddd;">&#9675;</span>';
                }
                const text = document.createElement('span');
                text.textContent = step.label;
                div.appendChild(icon);
                div.appendChild(text);
                container.appendChild(div);
            });
        }

        function showStatus(url) {
            document.getElementById('form-view').style.display = 'none';
            document.getElementById('status-view').style.display = 'flex';
            document.getElementById('status-url').textContent = url;
            document.getElementById('error-area').innerHTML = '';
            renderSteps(0);
            document.getElementById('progress-fill').style.width = '0%';
        }

        function showError(message) {
            const area = document.getElementById('error-area');
            area.innerHTML = '';
            const box = document.createElement('div');
            box.className = 'error-box';
            const strong = document.createElement('strong');
            strong.textContent = UI_STRINGS.error_title;
            box.appendChild(strong);
            box.appendChild(document.createTextNode(message));
            area.appendChild(box);
            const retry = document.createElement('a');
            retry.href = '/';
            retry.className = 'retry-btn';
            retry.textContent = UI_STRINGS.retry_button;
            area.appendChild(retry);
        }

        document.getElementById('url-form').addEventListener('submit', function(e) {
            e.preventDefault();
            const link = document.getElementById('link').value.trim();
            if (!link) return;
            showStatus(link);

            const startTime = Date.now();
            const elapsedEl = document.getElementById('elapsed-time');
            const progressEl = document.getElementById('progress-fill');

            const timer = setInterval(() => {
                const sec = ((Date.now() - startTime) / 1000).toFixed(0);
                elapsedEl.textContent = UI_STRINGS.elapsed_template.replace('{seconds}', sec);
            }, 500);

            const evtSource = new EventSource('/status?url=' + encodeURIComponent(link));

            evtSource.addEventListener('step', function(e) {
                const data = JSON.parse(e.data);
                const idx = STEPS.findIndex(s => s.id === data.step);
                if (idx >= 0) {
                    renderSteps(idx);
                    const pct = Math.min(((idx + 1) / STEPS.length) * 100, 95);
                    progressEl.style.width = pct + '%';
                }
                if (data.redirect) {
                    evtSource.close();
                    clearInterval(timer);
                    renderSteps(STEPS.length);
                    progressEl.style.width = '100%';
                    const target = readerToggle.checked ? '/reader' + data.redirect : data.redirect;
                    setTimeout(() => {
                        window.location.href = target;
                    }, 400);
                }
            });

            evtSource.addEventListener('error_msg', function(e) {
                evtSource.close();
                clearInterval(timer);
                const data = JSON.parse(e.data);
                showError(data.message);
            });

            evtSource.onerror = function() {
                evtSource.close();
                clearInterval(timer);
                showError(UI_STRINGS.connection_lost_error);
            };
        });

        window.addEventListener('pageshow', function(event) {
            if (event.persisted) {
                document.getElementById('form-view').style.display = '';
                document.getElementById('status-view').style.display = 'none';
                document.getElementById('link').value = '';
            }
        });
    </script>
</body>

</html>
"""


def build_steps(strings):
    return [
        {"id": "connect", "label": strings["step_connect"]},
        {"id": "fetch", "label": strings["step_fetch"]},
        {"id": "detect", "label": strings["step_detect"]},
        {"id": "fallback_freedium", "label": strings["step_fallback_freedium"]},
        {"id": "fallback_org", "label": strings["step_fallback_org"]},
        {"id": "fallback_ph", "label": strings["step_fallback_ph"]},
        {"id": "process", "label": strings["step_process"]},
        {"id": "cleanup", "label": strings["step_cleanup"]},
        {"id": "done", "label": strings["step_done"]},
    ]


# Sends the page the person is looking at, as their browser rendered it, to the
# Reader view. Scripts, styles and SVG are dropped first to keep the post small.
READER_BOOKMARKLET = (
    "javascript:(function(){"
    "var d=document.documentElement.cloneNode(true);"
    "d.querySelectorAll('script,style,svg,link,template').forEach(function(e){e.remove();});"
    "var f=document.createElement('form');"
    "f.method='post';f.action='{action}';f.target='_blank';"
    "f.enctype='multipart/form-data';f.acceptCharset='UTF-8';f.style.display='none';"
    "[['html','<!DOCTYPE html>'+d.outerHTML],['url',location.href]].forEach(function(p){"
    "var t=document.createElement('textarea');t.name=p[0];t.value=p[1];f.appendChild(t);});"
    "document.body.appendChild(f);f.submit();f.remove();"
    "})();"
)


def reader_bookmarklet(host_url):
    return READER_BOOKMARKLET.replace("{action}", host_url.rstrip("/") + "/reader")


def render_main_page():
    strings = load_strings()
    ui_strings = {
        "error_title": strings["error_title"],
        "retry_button": strings["retry_button"],
        "elapsed_template": strings["elapsed_template"],
        "connection_lost_error": strings["connection_lost_error"],
    }
    return flask.render_template_string(
        html,
        steps=build_steps(strings),
        ui_strings=ui_strings,
        reader_bookmarklet=reader_bookmarklet(request.host_url),
        **strings,
    )


# --- archive.org (Wayback Machine) snapshots ---
#
# We fetch snapshots in raw `id_` mode, which returns the page exactly as it
# was captured. Its asset URLs still point at the original site, and the <base>
# tag we inject makes root-relative ones ("/w/load.php") resolve to
# web.archive.org's root, where nothing exists. So we point every asset at its
# archived copy instead. CSS goes through `cs_` so Wayback also rewrites the
# url() references inside it; scripts and images stay raw (`id_`), because
# Wayback's rewritten JavaScript only runs with its own toolbar loaded.
#
# Fonts and CSS mask images (most icon sets) are only usable cross-origin when
# the server sends Access-Control-Allow-Origin, and archive.org doesn't. So
# stylesheets, <style> blocks and font preloads go through this server's
# /_relay/ route, which fetches them from archive.org and adds that header.
# process_html_document doesn't know this server's address (and the page's
# <base> points at archive.org), so it writes RELAY_TOKEN and finalize_page
# swaps in the real address when the page is served.

RELAY_TOKEN = "__9001FT_RELAY__/"
RELAY_TARGET_RE = re.compile(r"^https://web\.archive\.org/web/\d{1,14}[a-z]{0,2}_?/https?://", re.I)
RELAY_CONTENT_TYPES = (
    "text/css", "font/", "image/", "application/font", "application/x-font",
    "application/vnd.ms-fontobject", "application/octet-stream",
)
RELAY_MAX_BYTES = 15 * 1024 * 1024

WAYBACK_SNAPSHOT_RE = re.compile(r"^https?://web\.archive\.org/web/(\d{1,14})(?:[a-z]{2}_)?/(.+)$", re.I)

_NON_FETCHABLE = ("data:", "blob:", "javascript:", "mailto:", "tel:", "about:", "#")

WAYBACK_ASSET_ATTRS = [
    ("img", "src", "id_"),
    ("img", "data-src", "id_"),
    ("source", "src", "id_"),
    ("video", "src", "id_"),
    ("video", "poster", "id_"),
    ("audio", "src", "id_"),
    ("track", "src", "id_"),
    ("script", "src", "id_"),
    ("iframe", "src", "if_"),
    ("embed", "src", "id_"),
    ("object", "data", "id_"),
    ("input", "src", "id_"),
]

CSS_URL_RE = re.compile(r"""url\(\s*(['"]?)(.*?)\1\s*\)""", re.I)
CSS_IMPORT_RE = re.compile(r"""@import\s+(['"])(.*?)\1""", re.I)

# Shown if an archived image was never captured: fall back to the live copy.
IMG_FALLBACK_JS = "this.onerror=null;this.removeAttribute('srcset');this.src=this.dataset.liveSrc;"


def parse_wayback_url(url):
    """Return (timestamp, original_url) for a Wayback snapshot URL, else None."""
    match = WAYBACK_SNAPSHOT_RE.match(url or "")
    if not match:
        return None
    original = match.group(2)
    if not re.match(r"^https?://", original, re.I):
        original = "http://" + original.lstrip("/")
    return match.group(1), original


def _absolute_url(value, page_url):
    value = (value or "").strip()
    if not value or value.lower().startswith(_NON_FETCHABLE):
        return None
    absolute = urljoin(page_url, value)
    if urlparse(absolute).scheme not in ("http", "https"):
        return None
    return absolute


def _archived_url(value, timestamp, flag, page_url):
    absolute = _absolute_url(value, page_url)
    if absolute is None:
        return value
    if (urlparse(absolute).hostname or "").endswith("archive.org"):
        return absolute
    return f"https://web.archive.org/web/{timestamp}{flag}/{absolute}"


def _rewrite_srcset(value, timestamp, page_url):
    # Candidates are separated by ", ". Splitting on a bare comma would break
    # CDN URLs that use commas inside the path (e.g. "w_400,h_300").
    candidates = re.split(r",\s+", value.strip()) if ", " in value else value.split(",")
    rewritten = []
    for candidate in candidates:
        bits = candidate.strip().split(None, 1)
        if not bits:
            continue
        bits[0] = _archived_url(bits[0], timestamp, "id_", page_url)
        rewritten.append(" ".join(bits))
    return ", ".join(rewritten)


def _relayed(url):
    """Send an archive.org URL through this server's relay (see RELAY_TOKEN)."""
    return RELAY_TOKEN + url if RELAY_TARGET_RE.match(url) else url


def _rewrite_css(css, timestamp, page_url, relay=False):
    wrap = _relayed if relay else (lambda url: url)
    css = CSS_IMPORT_RE.sub(
        lambda m: f"@import {m.group(1)}{wrap(_archived_url(m.group(2), timestamp, 'cs_', page_url))}{m.group(1)}",
        css,
    )
    return CSS_URL_RE.sub(
        lambda m: f"url({m.group(1)}{wrap(_archived_url(m.group(2), timestamp, 'id_', page_url))}{m.group(1)})",
        css,
    )


def relay_css(css, css_url):
    """Rewrite a relayed stylesheet so everything it loads also comes through
    the relay. It's served from this server, so root-relative /_relay/ works."""
    def relay_ref(value):
        absolute = _absolute_url(value, css_url)
        if absolute and RELAY_TARGET_RE.match(absolute):
            return "/_relay/" + absolute
        return absolute or value

    css = CSS_IMPORT_RE.sub(lambda m: f"@import {m.group(1)}{relay_ref(m.group(2))}{m.group(1)}", css)
    return CSS_URL_RE.sub(lambda m: f"url({m.group(1)}{relay_ref(m.group(2))}{m.group(1)})", css)


def rewrite_wayback_snapshot(soup, timestamp, page_url):
    """Point a raw Wayback snapshot's assets at their archived copies."""
    for tag, attr, flag in WAYBACK_ASSET_ATTRS:
        for element in soup.find_all(tag, attrs={attr: True}):
            element[attr] = _archived_url(element[attr], timestamp, flag, page_url)

    for element in soup.find_all(["img", "source"]):
        for attr in ("srcset", "data-srcset"):
            if element.get(attr):
                element[attr] = _rewrite_srcset(element[attr], timestamp, page_url)

    for img in soup.find_all("img", src=True):
        archived = img["src"]
        prefix_end = archived.find("/", len("https://web.archive.org/web/"))
        if archived.startswith("https://web.archive.org/web/") and prefix_end != -1:
            img["data-live-src"] = archived[prefix_end + 1:]
            img["onerror"] = IMG_FALLBACK_JS

    for link in soup.find_all("link", href=True):
        rel = [r.lower() for r in (link.get("rel") or [])]
        kind = (link.get("as") or "").lower()
        is_css = "stylesheet" in rel or kind == "style"
        archived = _archived_url(link["href"], timestamp, "cs_" if is_css else "id_", page_url)
        link["href"] = _relayed(archived) if is_css or kind == "font" else archived

    for style in soup.find_all("style"):
        if style.string:
            style.string = _rewrite_css(style.string, timestamp, page_url, relay=True)
    for element in soup.find_all(style=True):
        element["style"] = _rewrite_css(element["style"], timestamp, page_url)

    # Links to other pages go to the live site, as they do for direct fetches.
    for element in soup.find_all(["a", "area"], href=True):
        absolute = _absolute_url(element["href"], page_url)
        if absolute:
            element["href"] = absolute


def process_html_document(html_content, original_url):
    """
    Parses HTML, injects the <base> tag to fix relative links, and applies
    site-specific modifications (like stripping paywall JS) where needed.
    """
    soup = BeautifulSoup(html_content, "html.parser")
    parsed_url = urlparse(original_url)
    base_url = f"{parsed_url.scheme}://{parsed_url.netloc}/"

    # Handle paths that are not root, e.g., "https://x.com/some/path/w.html"
    if parsed_url.path and not parsed_url.path.endswith("/"):
        base_url = urljoin(base_url, parsed_url.path.rsplit("/", 1)[0] + "/")

    base_tag = soup.find("base")
    if base_tag:
        # Always overwrite — sites may have <base href="/"> which would resolve
        # root-relative asset paths to this server instead of the original domain.
        base_tag["href"] = base_url
    else:
        new_base_tag = soup.new_tag("base", href=base_url)
        if soup.head:
            soup.head.insert(0, new_base_tag)
        else:
            head_tag = soup.new_tag("head")
            head_tag.insert(0, new_base_tag)
            soup.insert(0, head_tag)

    wayback = parse_wayback_url(original_url)
    if wayback:
        rewrite_wayback_snapshot(soup, *wayback)

    # Site-specific fixes below key off the article's own domain, not archive.org's.
    domain = (urlparse(wayback[1]).netloc if wayback else parsed_url.netloc).lower()

    # --- GENERAL FIXES ---

    # Strip anti-hotlinking, anti-embedding, and frame-busting scripts.
    for script in soup.find_all("script", src=False):
        content = script.get_text()
        
        # Target the specific hostname check
        if "location.hostname" in content and ("location.href" in content or "location.replace" in content):
            script.decompose()
            
        # Optional: Catch common frame-busting scripts if you are viewing this via an iframe
        elif "top.location" in content or "window.top" in content:
            script.decompose()

    # --- SITE SPECIFIC FIXES ---

    # The Seattle Times
    if "seattletimes.com" in domain:
        # Seattle Times uses specific JS bundles to trigger the paywall modal and ads.
        # Removing these scripts prevents the paywall from locking the screen.
        for script in soup.find_all("script", src=True):
            if "st-user-messaging" in script["src"] or "st-advertising" in script["src"]:
                script.decompose()

        # Ensure the body isn't locked from scrolling by JS-injected inline styles
        if soup.body:
            current_style = soup.body.get("style", "")
            soup.body["style"] = f"{current_style}; overflow: auto !important; position: static !important;"

    # --- FAVICON OVERRIDE ---
    # Remove any existing favicon links from the source page and inject the
    # ladder favicon so the browser tab always shows the ladder logo.
    for link_tag in soup.find_all("link", rel=lambda r: r and any(
            v.lower() in ("icon", "shortcut icon", "apple-touch-icon",
                          "apple-touch-icon-precomposed", "mask-icon")
            for v in (r if isinstance(r, list) else [r])
    )):
        link_tag.decompose()

    favicon_tag = soup.new_tag("link", rel="icon", href="/favicon.ico", type="image/png")
    if soup.head:
        soup.head.append(favicon_tag)

    return str(soup)



CHALLENGE_SIGNATURES = [
    "just a moment",
    "checking your browser",
    "cloudflare",
    "cf-challenge",
    "ddos protection",
    "attention required",
    "enable javascript and cookies to continue",
    "please verify you are a human",
    # Imperva / Incapsula block pages (e.g. bizjournals.com). These are also
    # captured by archive.org, so an archived snapshot can silently be a block
    # page rather than the article — detect it and fall through to an error.
    "_incapsula_resource",
    "incapsula incident",
    "request unsuccessful",
    # DataDome block pages (e.g. nytimes.com, ft.com, wsj.com).
    "datadome",
    "enable js and disable any ad blocker",
]


def is_challenge_page(html_text):
    if not html_text:
        return False
    lower = html_text[:8000].lower()
    hits = sum(1 for sig in CHALLENGE_SIGNATURES if sig in lower)
    if hits >= 1 and len(html_text) < 20000:
        return True
    return hits >= 2


def set_step(job_id, step):
    if job_id and job_id in jobs:
        jobs[job_id]['step'] = step


REAL_BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

ARCHIVE_PH_MIRRORS = ["archive.ph", "archive.today", "archive.is", "archive.li"]

FREEDIUM_MIRRORS = ["freedium-mirror.cfd", "freedium.cfd"]

# The original Freedium layout wrapped the post in <div id="main-content">, but
# the current mirror renders it inside a <main> element instead. Its error pages
# (e.g. "Article not found") still ship an empty <article> skeleton, so <article>
# on its own is not proof that the post was rendered.
FREEDIUM_CONTENT_MARKERS = ("main-content", "<main")

# SvelteKit streams the failure into the page body instead of using a non-200
# status, so the machine-readable error code is what marks a miss.
FREEDIUM_ERROR_MARKERS = ("ARTICLE_NOT_FOUND",)


def freedium_has_article(html_text):
    """True when a Freedium response actually contains the rendered post."""
    if not html_text or len(html_text) < 2000:
        return False
    if any(marker in html_text for marker in FREEDIUM_ERROR_MARKERS):
        return False
    return any(marker in html_text for marker in FREEDIUM_CONTENT_MARKERS)


def is_medium_url(url):
    try:
        host = (urlparse(url).hostname or "").lower()
        return host == "medium.com" or host.endswith(".medium.com")
    except Exception:
        return False


def fetch_via_freedium(url, job_id=None):
    """Freedium is a Medium-specific paywall bypass service."""
    set_step(job_id, 'fallback_freedium')
    clean_url = url.split('?')[0]
    for mirror in FREEDIUM_MIRRORS:
        try:
            freedium_url = f"https://{mirror}/{clean_url}"
            resp = requests.get(
                freedium_url,
                headers=REAL_BROWSER_HEADERS,
                timeout=30,
                allow_redirects=True,
            )
            if resp.status_code != 200:
                continue
            resp.encoding = resp.apparent_encoding
            text = resp.text
            if is_challenge_page(text):
                continue
            if not freedium_has_article(text):
                continue
            return text, resp.url
        except Exception:
            continue
    return None, None


def fetch_via_archive_org(url, job_id=None):
    set_step(job_id, 'fallback_org')
    wayback_api = "https://archive.org/wayback/available"
    try:
        meta = requests.get(wayback_api, params={"url": url}, timeout=15).json()
        snapshot = meta.get("archived_snapshots", {}).get("closest", {})
        if not snapshot.get("available"):
            return None, None
        archived_url = snapshot["url"]
        if archived_url.startswith("http://"):
            archived_url = "https://" + archived_url[len("http://"):]
        if "/web/" in archived_url and "id_/" not in archived_url:
            archived_url = archived_url.replace(
                archived_url.split("/web/")[1].split("/")[0],
                archived_url.split("/web/")[1].split("/")[0] + "id_",
                1,
            )
        resp = requests.get(archived_url, headers=googlebot_headers, timeout=30)
        resp.encoding = resp.apparent_encoding
        return resp.text, resp.url
    except Exception:
        return None, None


def fetch_via_archive_ph(url, job_id=None):
    """Try archive.today / archive.ph / archive.is — all mirrors of the same service."""
    set_step(job_id, 'fallback_ph')
    for mirror in ARCHIVE_PH_MIRRORS:
        try:
            newest_url = f"https://{mirror}/newest/{quote(url, safe=':/')}"
            resp = requests.get(
                newest_url,
                headers=REAL_BROWSER_HEADERS,
                timeout=20,
                allow_redirects=True,
            )
            if resp.status_code != 200:
                continue

            final = resp.url
            if not re.search(r"https?://archive\.(ph|today|is|li)/[A-Za-z0-9]{4,}", final):
                continue
            if re.search(r"archive\.(ph|today|is|li)/newest/", final):
                continue

            text = resp.text
            if is_challenge_page(text):
                continue

            lower_head = text[:5000].lower()
            if "no results" in lower_head or "0 captures" in lower_head:
                continue

            return text, final
        except Exception:
            continue
    return None, None


def bypass_paywall(url, strings, job_id=None):
    set_step(job_id, 'connect')

    if not url.startswith("http"):
        url = "https://" + url

    medium = is_medium_url(url)
    html_text = ""
    final_url = url

    if medium:
        freedium_html, freedium_final = fetch_via_freedium(url, job_id)
        if freedium_html:
            set_step(job_id, 'process')
            result = process_html_document(freedium_html, freedium_final)
            set_step(job_id, 'cleanup')
            return result
    else:
        set_step(job_id, 'fetch')
        response = requests.get(url, headers=googlebot_headers, timeout=30)
        response.encoding = response.apparent_encoding
        html_text = response.text
        final_url = response.url

        # Treat error status codes as a challenge so the archive fallbacks trigger.
        # Sites like News24 return 403 with their homepage HTML (which contains a
        # JS redirect back to the homepage), causing the browser to redirect away.
        if response.status_code in (401, 403, 407, 429) or response.status_code >= 500:
            html_text = ""

    set_step(job_id, 'detect')
    if not html_text or is_challenge_page(html_text):
        recovered = False

        archived_html, archived_url = fetch_via_archive_org(url, job_id)
        if archived_html and not is_challenge_page(archived_html):
            html_text = archived_html
            final_url = archived_url
            recovered = True

        if not recovered:
            archived_html, archived_url = fetch_via_archive_ph(url, job_id)
            if archived_html and not is_challenge_page(archived_html):
                html_text = archived_html
                final_url = archived_url
                recovered = True

        if not recovered:
            if medium:
                msg = strings["medium_challenge_error"]
            else:
                msg = strings["challenge_error"]
            raise UserFacingError(msg)

    set_step(job_id, 'process')
    result = process_html_document(html_text, final_url)

    set_step(job_id, 'cleanup')
    return result


def normalize_cache_url(url):
    if "://" in url:
        return "https://" + url.split("://", 1)[1]
    return "https://" + url


def clean_page_cache():
    now = time.time()
    expired = [k for k, v in page_cache.items() if now - v['timestamp'] > PAGE_CACHE_TTL]
    for k in expired:
        del page_cache[k]


def fetch_worker(job_id, url, strings):
    try:
        result = bypass_paywall(url, strings, job_id)
        # Never cache challenge/error pages — only store clean article HTML.
        if not is_challenge_page(result):
            cache_key = normalize_cache_url(url)
            with page_cache_lock:
                clean_page_cache()
                page_cache[cache_key] = {'html': result, 'timestamp': time.time()}
        else:
            cache_key = normalize_cache_url(url)
        with jobs_lock:
            jobs[job_id]['result'] = cache_key
            jobs[job_id]['step'] = 'done'
    except requests.exceptions.Timeout:
        with jobs_lock:
            jobs[job_id]['error'] = strings["timeout_error"]
            jobs[job_id]['step'] = 'error'
    except requests.exceptions.ConnectionError:
        with jobs_lock:
            jobs[job_id]['error'] = strings["connection_error"]
            jobs[job_id]['step'] = 'error'
    except requests.exceptions.RequestException as e:
        with jobs_lock:
            jobs[job_id]['error'] = strings["request_error"]
            jobs[job_id]['step'] = 'error'
    except UserFacingError as error:
        with jobs_lock:
            jobs[job_id]['error'] = error.user_message
            jobs[job_id]['step'] = 'error'
    except Exception as e:
        with jobs_lock:
            jobs[job_id]['error'] = strings["unexpected_error"]
            jobs[job_id]['step'] = 'error'


@app.route("/favicon.ico")
def favicon():
    logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logo.png")
    if os.path.exists(logo_path):
        with open(logo_path, "rb") as f:
            data = f.read()
        return Response(data, mimetype="image/png",
                        headers={"Cache-Control": "public, max-age=86400"})
    return "", 404


@app.route("/banner.webp")
def banner():
    banner_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets", "banner.webp")
    if os.path.exists(banner_path):
        with open(banner_path, "rb") as f:
            data = f.read()
        return Response(data, mimetype="image/webp",
                        headers={"Cache-Control": "public, max-age=86400"})
    return "", 404


@app.route("/")
def main_page():
    return render_main_page()


@app.route("/status")
def status_stream():
    raw_url = request.args.get("url", "")
    url = unquote(raw_url)

    if not url:
        return "Missing URL", 400

    strings = load_strings()
    job_id = str(uuid.uuid4())
    with jobs_lock:
        jobs[job_id] = {'step': 'queued', 'result': None, 'error': None}

    thread = threading.Thread(target=fetch_worker, args=(job_id, url, strings))
    thread.daemon = True
    thread.start()

    def generate():
        last_step = None
        try:
            while True:
                with jobs_lock:
                    job = dict(jobs[job_id]) if job_id in jobs else None
                if not job:
                    break

                current = job['step']
                if current != last_step:
                    last_step = current
                    if current == 'done':
                        yield f"event: step\ndata: {json.dumps({'step': 'done', 'redirect': '/' + job['result']})}\n\n"
                        break
                    elif current == 'error':
                        yield f"event: error_msg\ndata: {json.dumps({'message': job['error']})}\n\n"
                        break
                    else:
                        yield f"event: step\ndata: {json.dumps({'step': current})}\n\n"

                time.sleep(0.2)
        finally:
            with jobs_lock:
                jobs.pop(job_id, None)

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


# Floating button on article pages. The page carries a <base> tag pointing at
# the original site, so the link must be absolute to reach this server.
READER_BUTTON = (
    '<a id="ladder-reader-btn" href="{href}" style="all:initial;position:fixed;right:16px;bottom:16px;'
    "z-index:2147483647;background:#1f2937;color:#fff;cursor:pointer;text-decoration:none;"
    "font:600 14px/1 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;padding:11px 15px;"
    'border-radius:999px;box-shadow:0 4px 14px rgba(0,0,0,.28)">&#128214; {label}</a>'
)


def finalize_page(page_html, url, strings):
    """Last step before serving a fetched page: point relay links at this
    server and add the Reader view button. Both need this server's address,
    which only the request knows, so the cached copy stays without them."""
    page_html = page_html.replace(RELAY_TOKEN, request.host_url + "_relay/")
    href = request.host_url + "reader/" + url
    button = READER_BUTTON.format(href=html_lib.escape(href, quote=True), label=html_lib.escape(strings["reader_button"]))
    end = page_html.lower().rfind("</body>")
    if end == -1:
        return page_html + button
    return page_html[:end] + button + page_html[end:]


def relay_target_url(full_url):
    """'http://host/_relay/https://web.archive.org/web/...' -> the archive.org
    URL, or None if it isn't an archive.org snapshot asset."""
    parts = full_url.split("/", 3)
    if len(parts) < 4 or not parts[3].startswith("_relay/"):
        return None
    target = parts[3][len("_relay/"):]
    # Some clients collapse "//" in paths; put the schemes back together.
    target = re.sub(r"^https?:/+", "https://", target, flags=re.I)
    target = re.sub(r"(/web/\d{1,14}[a-z]{0,2}_?/)(https?:)/+", r"\1\2//", target, flags=re.I)
    return target if RELAY_TARGET_RE.match(target) else None


@app.route("/_relay/<path:path>", methods=["GET"])
def relay(path):
    """Serve a stylesheet, font or image from archive.org with the CORS header
    archive.org leaves off. Only archive.org snapshot URLs, only those types."""
    target = relay_target_url(request.url)
    if not target:
        return "", 404
    try:
        upstream = requests.get(target, headers=REAL_BROWSER_HEADERS, timeout=20, stream=True)
    except requests.exceptions.RequestException:
        return "", 502
    with upstream:
        content_type = (upstream.headers.get("Content-Type") or "application/octet-stream").split(";")[0].strip().lower()
        if (upstream.status_code != 200
                or (urlparse(upstream.url).hostname or "").lower() != "web.archive.org"
                or not content_type.startswith(RELAY_CONTENT_TYPES)):
            return "", 404
        chunks, size = [], 0
        for chunk in upstream.iter_content(64 * 1024):
            chunks.append(chunk)
            size += len(chunk)
            if size > RELAY_MAX_BYTES:
                return "", 413
        body = b"".join(chunks)
        final_url = upstream.url
        encoding = upstream.encoding

    if content_type == "text/css":
        body = relay_css(body.decode(encoding or "utf-8", errors="replace"), final_url).encode("utf-8")
        content_type = "text/css; charset=utf-8"

    response = Response(body, content_type=content_type)
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Cache-Control"] = "public, max-age=86400"
    response.headers["X-Content-Type-Options"] = "nosniff"
    # Nothing relayed is meant to be opened as a page; if it is, it can't run.
    response.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; sandbox"
    return response


def get_page(url, strings):
    """Fetch a page, reusing the short-lived page cache."""
    key = normalize_cache_url(url)
    with page_cache_lock:
        cached = page_cache.get(key)
    if cached and time.time() - cached["timestamp"] <= PAGE_CACHE_TTL:
        return cached["html"]
    result = bypass_paywall(url, strings)
    if not is_challenge_page(result):
        with page_cache_lock:
            clean_page_cache()
            page_cache[key] = {"html": result, "timestamp": time.time()}
    return result


def reader_target_url(full_url):
    """'http://host/reader/https://site/a?b' -> 'https://site/a?b'."""
    parts = full_url.split("/", 3)
    if len(parts) < 4 or not parts[3].startswith("reader/"):
        return None
    target = re.sub(r"^https?:/*", "", parts[3][len("reader/"):], flags=re.I).lstrip("/")
    return "https://" + target if target else None


@app.route("/reader/<path:path>", methods=["GET"])
def reader_view(path):
    strings = load_strings()
    url = reader_target_url(request.url)
    if not url:
        return strings["invalid_url"], 400
    try:
        page_html = get_page(url, strings)
    except requests.exceptions.Timeout:
        return strings["timeout_error"], 400
    except requests.exceptions.ConnectionError:
        return strings["connection_error"], 400
    except requests.exceptions.RequestException:
        return strings["request_error"], 400
    except UserFacingError as error:
        return error.user_message, 400
    except Exception:
        return strings["unexpected_error"], 500

    return render_reader(page_html, url, "/" + url, strings)


@app.route("/reader", methods=["POST"])
def reader_from_browser():
    """Reader view for a page sent by the Send to Reader bookmarklet: the page
    exactly as the person's own browser shows it, with their own access."""
    strings = load_strings()
    page_html = request.form.get("html", "")
    url = request.form.get("url", "").strip()
    if not page_html or not re.match(r"^https?://", url, re.I):
        return strings["invalid_url"], 400
    return render_reader(page_html, url, None, strings)


LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def local_request_problem(json_only=False):
    """Why this request may not use the settings or the AI, or None if it may.

    These endpoints use your API keys, so they only answer:
    - requests addressed to localhost (or a host in LADDER_ALLOWED_HOSTS), so a
      website can't reach them by pointing its own domain at your computer;
    - from this server's own pages (the Origin check);
    - with a JSON body when they change something, which a page on another
      site can't send without a CORS preflight this server never approves."""
    host = (urlparse("//" + (request.host or "")).hostname or "").lower()
    allowed = LOCAL_HOSTS | {h.strip().lower() for h in os.environ.get("LADDER_ALLOWED_HOSTS", "").split(",") if h.strip()}
    if host not in allowed and not host.endswith(".localhost"):
        return "AI settings only work at localhost (see LADDER_ALLOWED_HOSTS in the README)"
    origin = request.headers.get("Origin")
    if origin and origin.rstrip("/") != request.host_url.rstrip("/"):
        return "not allowed from another site"
    if json_only and not request.is_json:
        return "not allowed"
    return None


@app.route("/settings", methods=["GET"])
def settings_view():
    problem = local_request_problem()
    if problem:
        return problem, 403
    nonce = secrets.token_urlsafe(16)
    providers = {key: {k: v for k, v in spec.items() if k in ("label", "key_hint", "models")}
                 for key, spec in ai_settings.PROVIDERS.items()}
    body = flask.render_template_string(settings_page.SETTINGS_TEMPLATE, nonce=nonce, providers=providers)
    response = Response(body, mimetype="text/html")
    response.headers["Content-Security-Policy"] = settings_page.content_security_policy(nonce)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/settings/ai", methods=["GET", "POST"])
def settings_ai():
    problem = local_request_problem(json_only=request.method == "POST")
    if problem:
        return flask.jsonify(error=problem), 403
    if request.method == "POST":
        try:
            ai_settings.save(ai_settings.merge_draft(request.get_json(silent=True) or {}))
        except ValueError as error:
            return flask.jsonify(error=str(error)), 400
    response = flask.jsonify(ai_settings.public_view())
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/settings/ai/models", methods=["POST"])
def settings_ai_models():
    problem = local_request_problem(json_only=True)
    if problem:
        return flask.jsonify(error=problem), 403
    data = request.get_json(silent=True) or {}
    try:
        draft = ai_settings.merge_draft(data)
        draft["list"] = data.get("list")
        return flask.jsonify(models=ai_settings.list_models(draft))
    except ValueError as error:
        return flask.jsonify(error=str(error)), 400


@app.route("/settings/ai/test", methods=["POST"])
def settings_ai_test():
    problem = local_request_problem(json_only=True)
    if problem:
        return flask.jsonify(error=problem), 403
    try:
        return flask.jsonify(tidy.test_connection(ai_settings.merge_draft(request.get_json(silent=True) or {})))
    except ValueError as error:
        return flask.jsonify(error=str(error)), 400
    except tidy.TidyError as error:
        return flask.jsonify(error=str(error)), 502


@app.route("/reader/tidy", methods=["POST"])
def reader_tidy():
    """AI tidy for the article shown in Reader view (see tidy.py)."""
    problem = local_request_problem(json_only=True)
    if problem:
        return flask.jsonify(error=problem), 403
    if not tidy.enabled():
        return flask.jsonify(error="AI tidy isn't set up on this server"), 404
    data = request.get_json(silent=True) or {}
    url = data.get("url") if isinstance(data.get("url"), str) else ""
    html = data.get("html") if isinstance(data.get("html"), str) else ""
    if not html:
        return flask.jsonify(error="nothing to tidy"), 400
    base = url if re.match(r"^https?://", url, re.I) else request.host_url
    try:
        result = tidy.tidy(str(reader.clean_fragment(html, base)))
    except tidy.TidyError as error:
        return flask.jsonify(error=str(error)), 502
    return flask.jsonify(result)


def render_reader(page_html, url, original_href, strings):
    try:
        article = reader.extract_article(page_html, url)
    except Exception:
        article = {"title": url, "site": "", "author": "", "published": "",
                   "minutes": 0, "lead_image": "", "content": ""}

    nonce = secrets.token_urlsafe(16)
    body = flask.render_template_string(
        reader.READER_TEMPLATE,
        article=article,
        source_url=url,
        original_href=original_href,
        nonce=nonce,
        tidy_enabled=tidy.enabled(),
        tidy_strings={key[len("tidy_"):]: value for key, value in strings.items() if key.startswith("tidy_")},
        **strings,
    )
    response = Response(body, mimetype="text/html")
    response.headers["Content-Security-Policy"] = reader.content_security_policy(nonce)
    return response


@app.route("/article", methods=["POST"])
def show_article():
    link = flask.request.form["link"]
    strings = load_strings()
    try:
        return finalize_page(bypass_paywall(link, strings), link, strings)
    except requests.exceptions.Timeout:
        return strings["timeout_error"], 400
    except requests.exceptions.ConnectionError:
        return strings["connection_error"], 400
    except requests.exceptions.RequestException:
        return strings["request_error"], 400
    except UserFacingError as error:
        return error.user_message, 400
    except Exception:
        return strings["unexpected_error"], 500


@app.route("/", defaults={"path": ""})
@app.route("/<path:path>", methods=["GET"])
def get_article(path):
    strings = load_strings()
    full_url = request.url
    parts = full_url.split("/", 4)
    if len(parts) >= 5:
        actual_url = "https://" + parts[4].lstrip("/")

        with page_cache_lock:
            cached = page_cache.get(actual_url)
        if cached:
            return finalize_page(cached['html'], actual_url, strings)

        try:
            return finalize_page(bypass_paywall(actual_url, strings), actual_url, strings)
        except requests.exceptions.Timeout:
            return strings["timeout_error"], 400
        except requests.exceptions.ConnectionError:
            return strings["connection_error"], 400
        except requests.exceptions.RequestException:
            return strings["request_error"], 400
        except UserFacingError as error:
            return error.user_message, 400
        except Exception:
            return strings["unexpected_error"], 500
    else:
        return strings["invalid_url"], 400


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=os.getenv("PORT") or 5000, debug=False)
