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
import climb  # noqa: E402
import ai_settings  # noqa: E402
import settings_page  # noqa: E402
import tidy  # noqa: E402
import library  # noqa: E402
import library_page  # noqa: E402
import export  # noqa: E402

import auth  # noqa: E402
import pwa  # noqa: E402
import theme  # noqa: E402

VERSION = "0.4.0"

app = flask.Flask(__name__)
# Optional password (LADDER_PASSWORD): a login in front of every page. Off by default.
auth.init_app(app)
# The Send to Reader bookmarklet posts a whole page; Werkzeug's default limit
# for a form field is 500 KB, which many article pages exceed.
app.config["MAX_FORM_MEMORY_SIZE"] = 25 * 1024 * 1024
app.config["MAX_CONTENT_LENGTH"] = 30 * 1024 * 1024

_DEFAULT_STRINGS = {
    "heading": "What would you like to read?",
    "label": "Paste a link to the page:",
    "submit": "Start climbing",
    "toggle_dark_mode": "Toggle Dark Mode",
    "status_heading": "Climbing up to your page",
    "error_title": "Oh dear. Couldn't reach that one.",
    "retry_button": "Try another link",
    "elapsed_template": "{seconds}s elapsed",
    "connection_lost_error": "Connection to server lost. The page may be taking too long to load.",
    "step_connect": "Leaning the ladder against the website…",
    "step_fetch": "Climbing up to fetch the page…",
    "step_detect": "Checking the page really arrived…",
    "step_fallback_freedium": "Asking Freedium, the Medium mirror…",
    "step_fallback_org": "Dusting off the Wayback Machine at archive.org…",
    "step_fallback_ph": "Checking the archives at archive.today…",
    "step_process": "Brushing the crumbs off the article…",
    "step_cleanup": "Setting the table for reading…",
    "step_done": "9,001 ft! Enjoy the view.",
    "altitude_label": "Altitude",
    "feet_template": "{feet} ft",
    "feet_label": "Feet climbed so far:",
    "feet_milestones": [
        [9001, "That's one very tall ladder."],
        [18002, "Higher than most clouds bother to go."],
        [29032, "Taller than Mount Everest."],
        [45005, "Above the airliners. Wave."],
        [120000, "Higher than a weather balloon."],
        [328084, "That's space. Bring a napkin."],
    ],
    "first_summit": "First summit! 9,001 ft!",
    "welcome_title": "First time up here?",
    "welcome_line": (
        "Paste a link to any article and Grandma climbs up to fetch a clean, "
        "easy-to-read copy. Or watch her go with a sample."
    ),
    "welcome_dismiss": "Hide this welcome",
    "sample_button": "Try a sample",
    "taglines": [
        "Taller than 13 feet. Considerably.",
        "Bring your own parmesan.",
        "One rung at a time, dear.",
        "Recipes taste better at altitude.",
        "Skip the life story. Get to the recipe.",
        "Hold my lasagna.",
        "Now with 8,988 extra feet.",
        "No elevator. Excellent cardio.",
        "Grandma doesn't scroll. Grandma climbs.",
        "Still warm when it reaches you.",
        "The view from up here is very readable.",
        "Mind the top rung.",
    ],
    "error_stuck_label": "Stuck at",
    "invalid_url": "That doesn't look like a web link. Check it and try again.",
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
    "listen_button": "Read aloud",
    "listen_controls": "Read aloud controls",
    "listen_play": "Play",
    "listen_pause": "Pause",
    "listen_stop": "Stop",
    "listen_speed": "Speed",
    "listen_voice": "Voice",
    "listen_reading": "Reading {current} of {total}",
    "listen_paused": "Paused",
    "listen_stopped": "Stopped",
    "listen_done": "Finished reading",
    "listen_no_voices": "This browser has no voices to read with.",
    "export_button": "Export",
    "export_menu": "Export options",
    "export_markdown": "Markdown (.md)",
    "export_epub": "E-book (.epub)",
    "export_print": "Print or save as PDF",
    "export_working": "Preparing the download…",
    "export_done": "Downloaded {filename}.",
    "export_failed": "Couldn't export this page: {reason}.",
    "export_label_source": "Source",
    "export_label_by": "By",
    "export_label_published": "Published",
    "export_label_images_note": "Images aren't included in this e-book; each one is a link to the original.",
    "export_label_contents": "Contents",
    "export_label_image": "Image",
    "export_label_video": "Video",
    "export_label_audio": "Audio",
    "export_label_embed": "Embedded content",
    "settings_link": "AI settings (optional)",
    "reader_empty": (
        "Couldn't find the article text on this page. "
        "Try the original page instead."
    ),
    "keep_button": "Keep a copy",
    "library_link": "Your library",
    "library_title": "Your library",
    "library_lede": (
        "Copies of pages you kept, saved on this computer with their styles, images and fonts, "
        "so they open later, even without an internet connection."
    ),
    "library_empty": (
        "Nothing here yet. Press \U0001F4CC Keep a copy on a page 9001ft opened, or use the "
        "bookmarklet below on any page open in your own browser."
    ),
    "library_add_label": "Keep a copy of a link",
    "library_saved": "Saved to your library.",
    "library_saved_skipped": "Some of its files ({count}) couldn't be saved, so the copy may look a little different.",
    "library_deleted": "Deleted.",
    "library_saved_on": "Saved {date}",
    "library_open": "Open copy",
    "library_reader": "Reader version",
    "library_live": "Live page",
    "library_delete": "Delete",
    "library_delete_confirm": "Delete your copy of \u201c{title}\u201d? This can't be undone.",
    "library_delete_yes": "Yes, delete it",
    "library_skipped": "Files that couldn't be saved: {count}",
    "library_bookmarklet_heading": "Keep pages from your own browser",
    "library_bookmarklet_label": "Keep a copy in 9001ft",
    "library_bookmarklet_hint": (
        "Drag this button to your bookmarks bar (Ctrl+Shift+B shows the bar, or \u2318+Shift+B on a Mac). "
        "Then, on any page open in your browser, including ones you're signed in to, click it to keep "
        "a copy here exactly as you see it."
    ),
    "library_bookmarklet_click": "Drag the button to your bookmarks bar rather than clicking it here.",
    "library_confirm_title": "Keep a copy?",
    "library_confirm_text": (
        "9001ft will save this page on this computer with its styles, images, fonts and scripts, "
        "so you can read it later, even offline. Big pages can take a minute."
    ),
    "library_confirm_browser": "This is the page as your browser sent it.",
    "library_saving": "Saving\u2026 this can take a minute.",
    "library_cancel": "Cancel",
    "library_error_expired": "This page has expired. Go back, reload it and try again.",
    "library_error_draft": "That page is no longer waiting to be kept. Send it again.",
    "library_error_private": "Pages on your own network can't be kept.",
    "library_error_failed": "Couldn't keep this page: {reason}.",

    "app_description": "Paste a link and read the page cleanly, or in Reader view.",
    "logout_link": "Log out",
    "login_title": "Enter the password to continue.",
    "login_label": "Password",
    "login_button": "Log in",
    "login_wrong": "That password isn't right.",
    "login_too_many": "Too many wrong passwords. Wait a few minutes and try again.",
    "share_no_link_title": "No link to read",
    "share_no_link_text": "What you shared didn't include a web address. Share a page from your browser instead.",
    "offline_title": "You're offline",
    "offline_text": "9001ft needs a connection to fetch pages. Try again when you're back online.",
    "back_home": "Back to 9001ft",
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
    <link rel="manifest" href="/manifest.webmanifest">
    <meta name="theme-color" content="#fdf3dc">
    <link rel="apple-touch-icon" href="/icons/apple-touch-icon.png">
    <script src="/static/app.js" defer></script>
    <style>{{ font_face_css | safe }}</style>
    <style>
        * { box-sizing: border-box; }
        [hidden] { display: none !important; }
        {{ palette_css | safe }}
        body {
            font-family: 'Open Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background-color: var(--paper);
            background-image: radial-gradient(var(--dots) 1px, transparent 1.5px);
            background-size: 9px 9px;
            color: var(--ink);
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
            border: 3px solid var(--line);
            border-radius: 16px;
            box-shadow: 7px 7px 0 var(--shadow);
        }

        .tagline { margin: -17px 0 26px; text-align: center; position: relative; }
        .tagline span {
            display: inline-block;
            max-width: 92%;
            padding: 5px 16px 4px;
            background: var(--yellow);
            color: #1d2b30;
            border: 3px solid var(--line);
            border-radius: 10px;
            box-shadow: 3px 3px 0 var(--shadow);
            transform: rotate(-1.5deg);
            font-family: var(--display);
            font-size: 1.3rem;
            letter-spacing: 0.04em;
            line-height: 1.2;
        }

        .welcome {
            position: relative;
            display: flex;
            flex-wrap: wrap;
            align-items: center;
            gap: 10px 14px;
            max-width: 520px;
            margin: 0 auto 22px;
            padding: 12px 40px 12px 16px;
            background: var(--card);
            border: 3px dashed var(--line);
            border-radius: 14px;
            font-size: 15px;
            line-height: 1.5;
        }
        .welcome p { margin: 0; flex: 1 1 250px; }
        .welcome strong { font-family: var(--display); font-weight: 400; font-size: 1.2rem; letter-spacing: 0.04em; margin-right: 4px; }
        .welcome-close {
            position: absolute;
            top: 4px;
            right: 6px;
            width: 30px;
            height: 30px;
            padding: 0;
            background: none;
            border: 0;
            border-radius: 50%;
            color: var(--muted);
            font-size: 22px;
            line-height: 1;
            cursor: pointer;
        }
        .welcome-close:hover { color: var(--ink); }

        .btn, input[type="submit"] {
            display: inline-block;
            padding: 8px 18px;
            background: var(--yellow);
            color: #1d2b30;
            border: 3px solid var(--line);
            border-radius: 12px;
            box-shadow: 4px 4px 0 var(--shadow);
            font-family: var(--display);
            font-size: 1.15rem;
            font-weight: 400;
            letter-spacing: 0.06em;
            text-decoration: none;
            cursor: pointer;
            -webkit-appearance: none;
            appearance: none;
        }
        .btn:hover, input[type="submit"]:hover { transform: translate(-1px, -1px); box-shadow: 5px 5px 0 var(--shadow); background: #ffd94f; }
        .btn:active, input[type="submit"]:active { transform: translate(3px, 3px); box-shadow: 1px 1px 0 var(--shadow); }
        .btn-red { background: #c22f24; color: #fff; }
        .btn-red:hover { background: #cf3a2e; }

        a:focus-visible, button:focus-visible, input:focus-visible {
            outline: 3px solid var(--accent);
            outline-offset: 3px;
        }

        #url-form {
            max-width: 520px;
            margin: 0 auto;
            padding: 22px 24px 26px;
            background: var(--card);
            border: 3px solid var(--line);
            border-radius: 18px;
            box-shadow: 7px 7px 0 var(--shadow);
        }

        .feet { max-width: 520px; margin: 26px auto 0; text-align: center; font-size: 15px; color: var(--muted); }
        .feet b {
            font-family: var(--display);
            font-weight: 400;
            font-size: 1.35rem;
            letter-spacing: 0.04em;
            color: var(--ink);
            font-variant-numeric: tabular-nums;
        }
        .feet #feet-note { display: block; font-size: 14px; }

        .bookmarklet { max-width: 520px; margin: 26px auto 0; text-align: center; font-size: 14px; color: var(--muted); }
        .bookmarklet p { margin: 10px 0 0; line-height: 1.5; }
        .bookmarklet-link {
            display: inline-block;
            padding: 9px 16px;
            border: 2px dashed var(--accent);
            border-radius: 999px;
            color: var(--accent);
            font-weight: 600;
            text-decoration: none;
            cursor: grab;
        }
        .bookmarklet-link:hover { background: var(--card); }
        .settings-link { color: inherit; }

        h1 {
            font-family: var(--display);
            font-weight: 400;
            font-size: 2rem;
            letter-spacing: 0.03em;
            line-height: 1.15;
            margin: 0 0 14px;
            text-align: center;
            color: var(--ink);
        }

        label {
            display: block;
            margin-bottom: 8px;
            font-weight: 600;
        }

        input[type=text] {
            padding: 12px;
            margin-bottom: 12px;
            background: var(--field);
            color: var(--ink);
            border: 3px solid var(--line);
            border-radius: 10px;
            width: 100%;
            font-size: 1rem;
            font-family: inherit;
        }

        input[type="submit"] {
            padding: 12px 16px 10px;
            width: 100%;
            font-size: 1.5rem;
        }

        .reader-option {
            display: flex;
            align-items: center;
            gap: 8px;
            margin: 0 0 18px;
            font-size: 14px;
            font-weight: 400;
            cursor: pointer;
        }

        .reader-option input { width: 17px; height: 17px; margin: 0; accent-color: var(--red); }

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
            outline: 3px solid var(--accent);
            outline-offset: 3px;
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

        /* The climb: shown while a page is being fetched */
        #status-view { width: 100%; }

        .status-container {
            max-width: 760px;
            width: 100%;
            margin: 0 auto;
            padding: 48px 16px 32px;
            text-align: center;
        }

        .url-display {
            font-size: 0.85rem;
            color: var(--muted);
            margin: 0 auto 24px;
            max-width: 560px;
            word-break: break-all;
        }

        .climb {
            display: grid;
            grid-template-columns: minmax(0, 330px) minmax(0, 1fr);
            gap: 30px;
            align-items: center;
            text-align: left;
        }

        {{ scene_css | safe }}

        .summit-badge {
            position: absolute;
            left: 50%;
            top: 70%;
            z-index: 3;
            width: max-content;
            max-width: 88%;
            padding: 10px 18px 8px;
            background: var(--yellow);
            color: #ad2a20;
            border: 3px solid #1d2b30;
            border-radius: 14px;
            box-shadow: 5px 5px 0 #1d2b30;
            transform: translate(-50%, -50%) rotate(-5deg);
            font-family: var(--display);
            font-size: 1.7rem;
            letter-spacing: 0.04em;
            line-height: 1.1;
            text-align: center;
        }

        .now {
            min-height: 2.4em;
            margin: 0 0 14px;
            font-family: var(--display);
            font-size: 1.6rem;
            letter-spacing: 0.03em;
            line-height: 1.2;
        }

        .steps { list-style: none; margin: 0 0 14px; padding: 0; }

        .step {
            display: flex;
            align-items: baseline;
            gap: 10px;
            padding: 4px 0;
            color: var(--muted);
            font-size: 0.92rem;
            opacity: 0.75;
        }
        .step::before {
            content: "\\25CB";
            flex: 0 0 18px;
            text-align: center;
            font-weight: 700;
        }
        .step.active { color: var(--ink); font-weight: 600; opacity: 1; }
        .step.active::before { content: "\\25B6"; color: var(--accent); }
        .step.done { opacity: 1; }
        .step.done::before { content: "\\2713"; color: var(--accent); }
        .step.skipped { opacity: 0.5; }
        .step.skipped::before { content: "\\2013"; }
        .step.failed { color: var(--ink); opacity: 1; }
        .step.failed::before { content: "\\2717"; color: var(--accent); }

        .elapsed { font-size: 0.85rem; color: var(--muted); }

        .error-box {
            margin: 0 0 16px;
            padding: 14px 16px;
            background: var(--card);
            border: 3px solid var(--line);
            border-radius: 14px;
            box-shadow: 4px 4px 0 var(--shadow);
            font-size: 0.95rem;
            line-height: 1.5;
            overflow-wrap: anywhere;
        }
        .error-box h2 {
            margin: 0 0 6px;
            font-family: var(--display);
            font-weight: 400;
            font-size: 1.5rem;
            letter-spacing: 0.03em;
            line-height: 1.15;
            color: var(--accent);
        }
        .error-box p { margin: 0 0 14px; }

        .confetti { position: fixed; inset: 0; overflow: hidden; pointer-events: none; z-index: 50; }
        .confetti i {
            position: absolute;
            top: -24px;
            width: 10px;
            height: 16px;
            border: 1.5px solid #1d2b30;
            opacity: 0;
        }

        @media (prefers-reduced-motion: no-preference) {
            .btn, input[type="submit"] { transition: transform 0.12s, box-shadow 0.12s, background-color 0.2s; }
            .step.active::before { animation: step-pulse 0.9s ease-in-out infinite alternate; }
            .summit-badge { animation: badge-pop 0.5s cubic-bezier(0.2, 1.6, 0.4, 1) both; }
            .confetti i { animation: confetti-fall var(--dur, 1.9s) cubic-bezier(0.3, 0.5, 0.7, 1) var(--delay, 0s) forwards; }
            #form-view > *, .status-container > * { animation: rise-in 0.45s ease-out both; }
            #form-view > :nth-child(2) { animation-delay: 0.05s; }
            #form-view > :nth-child(n+3) { animation-delay: 0.1s; }
        }
        @keyframes step-pulse { from { translate: -2px 0; } to { translate: 2px 0; } }
        @keyframes badge-pop {
            from { opacity: 0; transform: translate(-50%, -50%) rotate(-5deg) scale(0.3); }
            to { opacity: 1; transform: translate(-50%, -50%) rotate(-5deg) scale(1); }
        }
        @keyframes confetti-fall {
            0% { opacity: 1; transform: translate3d(0, 0, 0) rotate(0deg); }
            100% { opacity: 1; transform: translate3d(var(--dx, 0px), 110vh, 0) rotate(var(--rot, 540deg)); }
        }
        @keyframes rise-in { from { translate: 0 12px; } to { translate: 0 0; } }

        @media only screen and (max-width: 640px) {
            #form-view { padding-top: 52px; }
            #url-form { padding: 18px 16px 22px; }
            h1 { font-size: 1.6rem; }
            .tagline span { font-size: 1.05rem; }
            .status-container { padding-top: 52px; }
            .climb { grid-template-columns: minmax(0, 1fr); gap: 22px; }
            .scene { max-width: 300px; margin: 0 auto; }
            .now { font-size: 1.35rem; min-height: 0; text-align: center; }
            .elapsed { text-align: center; }
        }
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
        <p class="tagline"><span id="tagline">{{ taglines[0] }}</span></p>
        <div class="welcome" id="welcome" hidden>
            <p><strong>{{ welcome_title }}</strong> {{ welcome_line }}</p>
            <a class="btn btn-red" id="sample-link" href="/reader/{{ sample_url }}">{{ sample_button }}</a>
            <button type="button" class="welcome-close" id="welcome-close" aria-label="{{ welcome_dismiss }}" title="{{ welcome_dismiss }}">&times;</button>
        </div>
        <form id="url-form">
            <h1>{{ heading }}</h1>
            <label for="link">{{ label }}</label>
            <input type="text" id="link" name="link" required autofocus>
            <label class="reader-option" for="reader-mode">
                <input type="checkbox" id="reader-mode"> {{ reader_checkbox }}
            </label>
            <input type="submit" value="{{ submit }}">
        </form>
        <p class="feet" id="feet" hidden>
            <span aria-hidden="true">&#129692;</span> {{ feet_label }} <b id="feet-total"></b>
            <span id="feet-note"></span>
        </p>
        <div class="bookmarklet">
            <a class="bookmarklet-link" href="{{ reader_bookmarklet }}" onclick="return false;"><span aria-hidden="true">&#128214;</span>&nbsp; {{ bookmarklet_label }}</a>
            <p>{{ bookmarklet_hint }}</p>
            <p><a class="settings-link" href="/library">&#128218; {{ library_link }}</a></p>
            <p><a class="settings-link" href="/settings">&#9881;&#65039; {{ settings_link }}</a></p>
            {% if show_logout %}<p><a class="settings-link" href="/logout">{{ logout_link }}</a></p>{% endif %}
        </div>
    </div>

    <div id="status-view" hidden>
        <div class="status-container">
            <h1>{{ status_heading }}</h1>
            <div class="url-display" id="status-url"></div>
            <div class="climb">
                <div class="scene is-climbing" id="scene">
                    <div class="altimeter" aria-hidden="true"><small>{{ altitude_label }}</small><b id="altitude"></b></div>
                    {{ scene_svg | safe }}
                    <div class="summit-badge" id="summit-badge" hidden>{{ first_summit }}</div>
                </div>
                <div class="climb-side">
                    <p class="now" id="status-now" role="status" aria-live="polite"></p>
                    <div id="error-area" role="alert"></div>
                    <ol class="steps" id="steps-list"></ol>
                    <div class="elapsed" id="elapsed-time"></div>
                </div>
            </div>
        </div>
    </div>

    <script>
    (function () {
        'use strict';

        // Saved choices and the little surprises are kept in the browser. Nothing
        // here is needed for the page to work, so every access may fail quietly.
        const store = {
            get(key) { try { return localStorage.getItem(key); } catch (e) { return null; } },
            set(key, value) { try { localStorage.setItem(key, value); } catch (e) {} }
        };
        const reducedMotion = function () {
            return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        };
        const byId = function (id) { return document.getElementById(id); };

        const toggleSwitch = byId('dark-mode-toggle');
        const currentTheme = store.get('theme') || (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");

        if (currentTheme === "dark") {
            document.body.classList.add("dark-mode");
            toggleSwitch.checked = true;
        }

        toggleSwitch.addEventListener('change', function () {
            document.body.classList.toggle("dark-mode", this.checked);
            store.set('theme', this.checked ? 'dark' : 'light');
        });

        const readerToggle = byId('reader-mode');
        readerToggle.checked = store.get('readerMode') === '1';
        readerToggle.addEventListener('change', function () {
            store.set('readerMode', this.checked ? '1' : '0');
        });

        const UI_STRINGS = {{ ui_strings | tojson }};
        const STEPS = {{ steps | tojson }};
        const CLIMB = {{ climb | tojson }};

        function formatFeet(feet) {
            let number;
            try { number = Math.round(feet).toLocaleString(document.documentElement.lang || undefined); }
            catch (e) { number = String(Math.round(feet)); }
            return UI_STRINGS.feet_template.replace('{feet}', number);
        }

        // --- Home page: tagline, welcome, feet climbed ---

        (function pickTagline() {
            const lines = UI_STRINGS.taglines || [];
            if (lines.length < 2) return;
            const last = parseInt(store.get('lastTagline'), 10);
            let pick = Math.floor(Math.random() * lines.length);
            if (pick === last) pick = (pick + 1) % lines.length;
            store.set('lastTagline', String(pick));
            byId('tagline').textContent = lines[pick];
        })();

        function feetClimbed() {
            const feet = parseInt(store.get('feetClimbed'), 10);
            return feet > 0 ? feet : 0;
        }

        function showFeetClimbed() {
            const feet = feetClimbed();
            byId('feet').hidden = feet <= 0;
            if (feet <= 0) return;
            byId('feet-total').textContent = formatFeet(feet);
            let note = '';
            (UI_STRINGS.feet_milestones || []).forEach(function (milestone) {
                if (feet >= milestone[0]) note = milestone[1];
            });
            byId('feet-note').textContent = note;
        }
        showFeetClimbed();

        const welcome = byId('welcome');
        function dismissWelcome() {
            welcome.hidden = true;
            store.set('welcomed', '1');
        }
        if (store.get('welcomed') !== '1' && feetClimbed() === 0) welcome.hidden = false;
        byId('welcome-close').addEventListener('click', function () {
            dismissWelcome();
            byId('link').focus();
        });
        byId('sample-link').addEventListener('click', function (event) {
            event.preventDefault();
            dismissWelcome();
            startClimb(CLIMB.sample_url, true);
        });

        // Dragging the bookmarklet to the bookmarks bar installs it; clicking it here does nothing.
        document.querySelector('.bookmarklet-link').addEventListener('click', function (event) {
            event.preventDefault();
        });

        // --- The climb ---

        const scene = byId('scene');
        const climber = scene.querySelector('.climber');
        const altitudeEl = byId('altitude');
        const nowEl = byId('status-now');
        const stepsEl = byId('steps-list');
        const errorArea = byId('error-area');
        const elapsedEl = byId('elapsed-time');
        const badge = byId('summit-badge');
        const stepItems = STEPS.map(function (step) {
            const item = document.createElement('li');
            item.className = 'step';
            item.textContent = step.label;
            stepsEl.appendChild(item);
            return item;
        });

        let altitude = 0;
        let frame = null;
        let source = null;
        let timer = null;
        let leaving = null;
        let visited = [];

        function place(feet) {
            altitude = feet;
            const t = CLIMB.from + (CLIMB.to - CLIMB.from) * Math.max(0, Math.min(1, feet / CLIMB.summit));
            const x = CLIMB.foot[0] + CLIMB.rise[0] * t - CLIMB.hand[0];
            const y = CLIMB.foot[1] + CLIMB.rise[1] * t - CLIMB.hand[1];
            climber.setAttribute('transform', 'translate(' + x.toFixed(1) + ' ' + y.toFixed(1) + ')');
            altitudeEl.textContent = formatFeet(feet);
        }

        // Ease the climber and the altitude counter up to `target` feet.
        function climbTo(target, duration) {
            if (frame) cancelAnimationFrame(frame);
            frame = null;
            if (reducedMotion() || !duration) { place(target); return; }
            const from = altitude;
            const start = performance.now();
            const tick = function (now) {
                const progress = Math.min((now - start) / duration, 1);
                place(from + (target - from) * (1 - Math.pow(1 - progress, 3)));
                frame = progress < 1 ? requestAnimationFrame(tick) : null;
            };
            frame = requestAnimationFrame(tick);
        }

        function markSteps(activeIdx, failed) {
            stepItems.forEach(function (item, i) {
                let state = '';
                if (i === activeIdx) state = failed ? ' failed' : ' active';
                else if (i < activeIdx) state = visited.indexOf(i) >= 0 ? ' done' : ' skipped';
                item.className = 'step' + state;
                if (i === activeIdx && !failed) item.setAttribute('aria-current', 'step');
                else item.removeAttribute('aria-current');
            });
        }

        function stopListening() {
            if (source) source.close();
            source = null;
            clearInterval(timer);
            timer = null;
        }

        function showForm() {
            stopListening();
            clearTimeout(leaving);
            if (frame) cancelAnimationFrame(frame);
            frame = null;
            document.querySelectorAll('.confetti').forEach(function (layer) { layer.remove(); });
            byId('status-view').hidden = true;
            byId('form-view').hidden = false;
            showFeetClimbed();
        }

        function showStatus(url) {
            byId('form-view').hidden = true;
            byId('status-view').hidden = false;
            byId('status-url').textContent = url;
            errorArea.textContent = '';
            badge.hidden = true;
            nowEl.hidden = false;
            nowEl.textContent = STEPS[0].label;
            elapsedEl.textContent = '';
            scene.className = 'scene is-climbing';
            visited = [];
            markSteps(0, false);
            place(0);
            window.scrollTo(0, 0);
        }

        function showError(message) {
            stopListening();
            if (frame) cancelAnimationFrame(frame);
            frame = null;
            scene.className = 'scene is-error';
            markSteps(visited.length ? visited[visited.length - 1] : 0, true);
            nowEl.hidden = true;
            errorArea.textContent = '';
            const box = document.createElement('div');
            box.className = 'error-box';
            const title = document.createElement('h2');
            title.textContent = UI_STRINGS.error_title;
            const text = document.createElement('p');
            text.textContent = message;
            const retry = document.createElement('button');
            retry.type = 'button';
            retry.className = 'btn';
            retry.textContent = UI_STRINGS.retry_button;
            retry.addEventListener('click', function () {
                showForm();
                const link = byId('link');
                link.focus();
                link.select();
            });
            box.appendChild(title);
            box.appendChild(text);
            box.appendChild(retry);
            errorArea.appendChild(box);
            retry.focus();
        }

        function throwConfetti() {
            const colors = ['#ffcf33', '#d1382c', '#ee7b22', '#fff8e7', '#4f9a4c', '#1f6f73'];
            const layer = document.createElement('div');
            layer.className = 'confetti';
            layer.setAttribute('aria-hidden', 'true');
            for (let i = 0; i < 44; i++) {
                const piece = document.createElement('i');
                piece.style.left = (Math.random() * 100).toFixed(1) + '%';
                piece.style.background = colors[i % colors.length];
                if (i % 3 === 0) piece.style.borderRadius = '50%';
                piece.style.setProperty('--dx', (Math.random() * 160 - 80).toFixed(0) + 'px');
                piece.style.setProperty('--rot', (Math.random() * 900 - 450).toFixed(0) + 'deg');
                piece.style.setProperty('--delay', (Math.random() * 0.5).toFixed(2) + 's');
                piece.style.setProperty('--dur', (1.5 + Math.random() * 0.9).toFixed(2) + 's');
                layer.appendChild(piece);
            }
            document.body.appendChild(layer);
        }

        // The page is ready: finish the climb, celebrate a first summit, then open it.
        function reachSummit(target) {
            stopListening();
            visited.push(STEPS.length - 1);
            markSteps(STEPS.length, false);
            nowEl.textContent = STEPS[STEPS.length - 1].label;
            scene.className = 'scene is-done';
            climbTo(CLIMB.summit, 800);

            store.set('feetClimbed', String(feetClimbed() + CLIMB.summit));
            store.set('welcomed', '1');
            let pause = reducedMotion() ? 500 : 1000;
            if (store.get('firstSummit') !== '1') {
                store.set('firstSummit', '1');
                badge.hidden = false;
                if (!reducedMotion()) throwConfetti();
                pause = 2400;
            }
            leaving = setTimeout(function () { window.location.href = target; }, pause);
        }

        function startClimb(link, forceReader) {
            showStatus(link);
            const startTime = Date.now();
            timer = setInterval(function () {
                const seconds = ((Date.now() - startTime) / 1000).toFixed(0);
                elapsedEl.textContent = UI_STRINGS.elapsed_template.replace('{seconds}', seconds);
            }, 500);

            source = new EventSource('/status?url=' + encodeURIComponent(link));

            source.addEventListener('step', function (e) {
                const data = JSON.parse(e.data);
                if (data.redirect) {
                    const openReader = forceReader || readerToggle.checked;
                    reachSummit(openReader ? '/reader' + data.redirect : data.redirect);
                    return;
                }
                const idx = STEPS.findIndex(function (s) { return s.id === data.step; });
                if (idx < 0) return;
                visited.push(idx);
                markSteps(idx, false);
                nowEl.textContent = STEPS[idx].label;
                // The last stretch is saved for the moment the page is ready.
                climbTo(CLIMB.summit * 0.94 * (idx + 1) / STEPS.length, 900);
            });

            source.addEventListener('error_msg', function (e) {
                showError(JSON.parse(e.data).message);
            });

            source.onerror = function () {
                showError(UI_STRINGS.connection_lost_error);
            };
        }

        byId('url-form').addEventListener('submit', function (e) {
            e.preventDefault();
            const link = byId('link').value.trim();
            if (link) startClimb(link, false);
        });

        window.addEventListener('pageshow', function (event) {
            if (event.persisted) {
                showForm();
                byId('link').value = '';
            }
        });
    })();
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


def reader_bookmarklet(host_url, token=None):
    script = READER_BOOKMARKLET.replace("{action}", host_url.rstrip("/") + "/reader")
    if token:
        # With a password set, the bookmarklet proves it came from this home
        # page (its post comes from another site, without the login cookie).
        script = script.replace("['url',location.href]]", "['url',location.href],['token','" + token + "']]")
    return script


# "Try a sample" on the home page opens this article in Reader view.
SAMPLE_URL = "https://en.wikipedia.org/wiki/Ladder"


def render_main_page():
    strings = load_strings()
    ui_strings = {key: strings[key] for key in (
        "error_title", "retry_button", "elapsed_template", "connection_lost_error",
        "feet_template", "feet_milestones", "taglines",
    )}
    climb_settings = {
        "foot": climb.LADDER_FOOT,
        "rise": climb.LADDER_RISE,
        "hand": [value * climb.CLIMBER_SCALE for value in climb.CLIMBER_HAND],
        "from": climb.CLIMB_FROM,
        "to": climb.CLIMB_TO,
        "summit": climb.SUMMIT_FEET,
        "sample_url": SAMPLE_URL,
    }
    return flask.render_template_string(
        html,
        font_face_css=theme.FONT_FACE_CSS,
        steps=build_steps(strings),
        ui_strings=ui_strings,
        climb=climb_settings,
        sample_url=SAMPLE_URL,
        palette_css=climb.PALETTE_CSS,
        scene_css=climb.SCENE_CSS,
        scene_svg=climb.scene_svg(),
        reader_bookmarklet=reader_bookmarklet(request.host_url, auth.bookmarklet_token()),
        show_logout=auth.enabled(),
        **strings,
    )


def error_page(message, status, strings):
    """A small friendly page for when an article can't be shown. It takes the
    place of a bare line of text, and is locked down like the other pages."""
    nonce = secrets.token_urlsafe(16)
    body = flask.render_template_string(
        climb.ERROR_TEMPLATE,
        font_face_css=theme.FONT_FACE_CSS,
        message=message,
        stuck_at=strings["feet_template"].replace("{feet}", str(status)),
        nonce=nonce,
        palette_css=climb.PALETTE_CSS,
        scene_css=climb.SCENE_CSS,
        scene_svg=climb.scene_svg(0.45),
        **strings,
    )
    response = Response(body, status=status, mimetype="text/html")
    response.headers["Content-Security-Policy"] = climb.content_security_policy(nonce)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response


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


@app.route("/fonts/<name>")
def font_file(name):
    """The bundled fonts (see theme.py). Only those files, cached for a year."""
    path = theme.font_path(name)
    if not path:
        return "", 404
    response = flask.send_file(path, mimetype="font/woff2", max_age=365 * 24 * 3600)
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


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


# --- self-hosting: health check, login, installable app, share target ---

def _no_store(response):
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/healthz")
def healthz():
    return _no_store(flask.jsonify(ok=True, version=VERSION))


def render_login(error=None, status=200):
    strings = load_strings()
    nonce = secrets.token_urlsafe(16)
    body = flask.render_template_string(
        auth.LOGIN_TEMPLATE, nonce=nonce, error=error,
        next_path=auth.safe_next(request.args.get("next")), **strings)
    response = Response(body, status=status, mimetype="text/html")
    response.headers["Content-Security-Policy"] = pwa.content_security_policy(nonce)
    return _no_store(response)


@app.route("/login", methods=["GET", "POST"])
def login():
    next_path = auth.safe_next(request.args.get("next"))
    if not auth.enabled() or (request.method == "GET" and auth.logged_in()):
        return flask.redirect(next_path, 303)
    if request.method == "GET":
        return render_login()
    origin = request.headers.get("Origin")
    if origin and origin.rstrip("/") != request.host_url.rstrip("/"):
        return "not allowed from another site", 403
    strings = load_strings()
    address = auth.client_address()
    if auth.failed_logins.blocked(address):
        return render_login(strings["login_too_many"], 429)
    if not auth.password_matches(request.form.get("password", "")):
        auth.failed_logins.record(address)
        return render_login(strings["login_wrong"], 401)
    auth.failed_logins.forget(address)
    auth.log_in()
    return flask.redirect(next_path, 303)


@app.route("/logout")
def logout():
    auth.log_out()
    return _no_store(flask.redirect("/login" if auth.enabled() else "/", 303))


def asset_path(*parts):
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", *parts)


@app.route("/manifest.webmanifest")
def manifest():
    response = Response(json.dumps(pwa.manifest(load_strings()["app_description"])),
                        mimetype="application/manifest+json")
    response.headers["Cache-Control"] = "public, max-age=3600"
    return response


@app.route("/icons/<name>")
def app_icon(name):
    if name not in pwa.ICON_FILES:
        return "", 404
    return flask.send_from_directory(asset_path("assets", "icons"), name, mimetype="image/png", max_age=86400)


@app.route("/sw.js")
def service_worker():
    response = flask.send_from_directory(app.static_folder, "sw.js", mimetype="text/javascript", max_age=0)
    response.headers["Cache-Control"] = "no-cache"
    response.headers["Service-Worker-Allowed"] = "/"
    return response


def render_message(title, text, status=200):
    strings = load_strings()
    nonce = secrets.token_urlsafe(16)
    body = flask.render_template_string(pwa.MESSAGE_TEMPLATE, nonce=nonce, title=title, text=text,
                                        button=strings["back_home"], lang=strings["lang"])
    response = Response(body, status=status, mimetype="text/html")
    response.headers["Content-Security-Policy"] = pwa.content_security_policy(nonce)
    return response


@app.route("/offline")
def offline_page():
    strings = load_strings()
    return render_message(strings["offline_title"], strings["offline_text"])


@app.route("/share")
def share():
    """Android's share sheet sends links here once the app is installed."""
    link = pwa.first_web_link(request.args.get("url"), request.args.get("text"))
    if link:
        return flask.redirect("/reader/" + quote(link, safe=":/?#[]@!$&'()*+,;=%~"), 303)
    strings = load_strings()
    return _no_store(render_message(strings["share_no_link_title"], strings["share_no_link_text"]))


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


# Floating buttons on article pages. The page carries a <base> tag pointing at
# the original site, so the links must be absolute to reach this server. The
# page is sandboxed (see FETCHED_PAGE_CSP), so both are plain links to this
# server's own pages: Reader view, and the "Keep a copy?" confirmation.
_FLOATING_LINK_STYLE = (
    "all:initial;color:#fff;cursor:pointer;text-decoration:none;"
    "font:600 14px/1 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;padding:11px 15px;"
    "border-radius:999px;box-shadow:0 4px 14px rgba(0,0,0,.28);"
)
READER_BUTTON = (
    '<div id="ladder-buttons" style="all:initial;position:fixed;right:16px;bottom:16px;z-index:2147483647;'
    'display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end">'
    '<a id="ladder-keep-btn" href="{keep_href}" style="' + _FLOATING_LINK_STYLE + 'background:#9b4dca">'
    '&#128204; {keep_label}</a>'
    '<a id="ladder-reader-btn" href="{href}" style="' + _FLOATING_LINK_STYLE + 'background:#1f2937">'
    '&#128214; {label}</a></div>'
)


def finalize_page(page_html, url, strings):
    """Last step before serving a fetched page: point relay links at this
    server, add the Reader view button (both need this server's address, which
    only the request knows, so the cached copy stays without them) and sandbox
    the page (see FETCHED_PAGE_CSP)."""
    page_html = page_html.replace(RELAY_TOKEN, request.host_url + "_relay/")
    href = request.host_url + "reader/" + url
    keep_href = request.host_url + "library/save?url=" + quote(url, safe="")
    button = READER_BUTTON.format(
        href=html_lib.escape(href, quote=True), label=html_lib.escape(strings["reader_button"]),
        keep_href=html_lib.escape(keep_href, quote=True), keep_label=html_lib.escape(strings["keep_button"]))
    end = page_html.lower().rfind("</body>")
    page_html = page_html + button if end == -1 else page_html[:end] + button + page_html[end:]
    response = Response(page_html, mimetype="text/html")
    response.headers["Content-Security-Policy"] = FETCHED_PAGE_CSP
    return response


# Fetched pages keep the original site's scripts (for animations and
# graphics). Sandboxing without allow-same-origin runs them in an opaque
# origin, so they can't read or call anything on this server, such as the AI
# settings and their API keys. Links, pop-ups and forms still work.
FETCHED_PAGE_CSP = "sandbox allow-scripts allow-popups allow-popups-to-escape-sandbox allow-forms"


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


# --- Library: keep copies of pages to read later, offline (see library.py) ---
#
# Saving and deleting change things, so they need a POST from one of this
# server's own pages: the form carries a token that the page also set in a
# SameSite=Strict cookie, which other sites can't send, and the Origin must be
# this server. Pages from other sites (the bookmarklet, the sandboxed article
# pages) only ever reach the "Keep a copy?" confirmation. Kept copies are
# served with FETCHED_PAGE_CSP, so their scripts run sandboxed too.

LIBRARY_CSRF_COOKIE = "ladder_library_csrf"
LIBRARY_CSRF_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")

# Like the Send to Reader bookmarklet, but keeps the whole page: styles,
# scripts and links. Styles that scripts added straight into the browser
# (empty <style> tags) are written out so they come along too.
KEEP_BOOKMARKLET = (
    "javascript:(function(){"
    "var d=document.documentElement.cloneNode(true),s=document.querySelectorAll('style'),c=d.querySelectorAll('style');"
    "for(var i=0;i<s.length&&i<c.length;i++){if(!c[i].textContent.trim()){try{"
    "c[i].textContent=Array.prototype.map.call(s[i].sheet.cssRules,function(r){return r.cssText;}).join(' ');"
    "}catch(e){}}}"
    "var t=document.doctype?'<!DOCTYPE '+document.doctype.name+'>':'';"
    "var f=document.createElement('form');"
    "f.method='post';f.action='{action}';f.target='_blank';"
    "f.enctype='multipart/form-data';f.acceptCharset='UTF-8';f.style.display='none';"
    "[['html',t+d.outerHTML],['url',location.href]].forEach(function(p){"
    "var e=document.createElement('textarea');e.name=p[0];e.value=p[1];f.appendChild(e);});"
    "document.body.appendChild(f);f.submit();f.remove();"
    "})();"
)


def keep_bookmarklet(host_url, token=None):
    script = KEEP_BOOKMARKLET.replace("{action}", host_url.rstrip("/") + "/library/keep")
    if token:
        # Same as the Send to Reader bookmarklet: proves it came from this app.
        script = script.replace("['url',location.href]]", "['url',location.href],['token','" + token + "']]")
    return script


def keep_copy_href(page_html, url, original_href):
    """Where Reader view's Keep a copy button goes: the confirmation for a page
    this server fetched, or for the page the person's own browser sent, which
    waits as a draft until they confirm."""
    if original_href:
        return "/library/save?url=" + quote(url, safe="")
    try:
        return "/library/save?draft=" + library.save_draft(page_html, url)
    except OSError:
        return None


def normalize_keep_url(raw):
    raw = (raw or "").strip()
    if raw and not re.match(r"^https?://", raw, re.I):
        raw = "https://" + raw.lstrip("/")
    return raw if raw and urlparse(raw).hostname else ""


def library_post_problem(strings):
    """Why a request that changes the library is refused, or None."""
    origin = request.headers.get("Origin")
    if origin is not None and urlparse(origin).netloc != request.host:
        return "not allowed from another site"
    cookie = request.cookies.get(LIBRARY_CSRF_COOKIE, "")
    if not LIBRARY_CSRF_RE.match(cookie) or not secrets.compare_digest(cookie, request.form.get("csrf", "")):
        return strings["library_error_expired"]
    return None


def render_library_template(template, strings, status=200, **values):
    nonce = secrets.token_urlsafe(16)
    csrf = request.cookies.get(LIBRARY_CSRF_COOKIE, "")
    if not LIBRARY_CSRF_RE.match(csrf):
        csrf = secrets.token_urlsafe(32)
    context = dict(strings)
    context.update(values, nonce=nonce, csrf=csrf)
    response = Response(flask.render_template_string(template, **context), status=status, mimetype="text/html")
    response.headers["Content-Security-Policy"] = library_page.content_security_policy(nonce)
    response.headers["Cache-Control"] = "no-store"
    # Not "no-referrer": with it, browsers send "Origin: null" on form posts,
    # which library_post_problem() rightly refuses.
    response.headers["Referrer-Policy"] = "same-origin"
    response.set_cookie(LIBRARY_CSRF_COOKIE, csrf, httponly=True, samesite="Strict", path="/library")
    return response


def render_library(strings, status=200, error=None):
    items = []
    for meta in library.items():
        item = dict(meta)
        item["date"] = library.human_date(meta.get("saved_at"))
        item["size_text"] = library.human_size(meta.get("size"))
        if not re.match(r"^https?://", str(item.get("url", "")), re.I):
            item["url"] = ""
        items.append(item)
    saved = library.get(request.args.get("saved", "")) if not error else None
    return render_library_template(
        library_page.LIBRARY_TEMPLATE, strings, status=status, items=items, saved=saved, error=error,
        deleted=request.args.get("deleted") == "1" and not error, keep_bookmarklet=keep_bookmarklet(request.host_url, auth.bookmarklet_token()))


def render_keep_confirmation(strings, status=200, error=None, url="", title="", draft=""):
    return render_library_template(
        library_page.CONFIRM_TEMPLATE, strings, status=status, error=error,
        url=url, title=title, draft=draft, cancel_href="/library")


@app.route("/library", methods=["GET"])
def library_view():
    return render_library(load_strings())


@app.route("/library/save", methods=["GET"])
def library_confirm():
    """The "Keep a copy?" page, for a link (?url=) or a page your browser sent (?draft=)."""
    strings = load_strings()
    draft = request.args.get("draft", "")
    if draft:
        loaded = library.load_draft(draft)
        if not loaded:
            return render_keep_confirmation(strings, 404, strings["library_error_draft"])
        return render_keep_confirmation(strings, url=loaded[1], title=library.page_title(loaded[0]), draft=draft)
    url = normalize_keep_url(request.args.get("url"))
    if not url:
        return render_keep_confirmation(strings, 400, strings["invalid_url"])
    with page_cache_lock:
        cached = page_cache.get(normalize_cache_url(url))
    return render_keep_confirmation(strings, url=url, title=library.page_title(cached["html"]) if cached else "")


@app.route("/library/save", methods=["POST"])
def library_save():
    strings = load_strings()
    draft = request.form.get("draft", "")
    url = normalize_keep_url(request.form.get("url"))
    loaded = library.load_draft(draft) if draft else None

    def failed(status, message):
        if loaded:
            return render_keep_confirmation(strings, status, message, loaded[1], library.page_title(loaded[0]), draft)
        return render_keep_confirmation(strings, status, message, "" if draft else url)

    problem = library_post_problem(strings)
    if problem:
        return failed(403, problem)
    if draft and not loaded:
        return failed(404, strings["library_error_draft"])
    if not loaded and not url:
        return failed(400, strings["invalid_url"])
    try:
        if loaded:
            meta = library.save(loaded[0], loaded[1], "browser")
            library.drop_draft(draft)
        else:
            library.check_address(url)
            page_html = get_page(url, strings).replace(RELAY_TOKEN, "")
            meta = library.save(page_html, url, "9001ft")
    except library.BlockedAddress:
        return failed(400, strings["library_error_private"])
    except library.FetchError as error:
        return failed(502, strings["library_error_failed"].replace("{reason}", str(error)))
    except requests.exceptions.Timeout:
        return failed(502, strings["timeout_error"])
    except requests.exceptions.ConnectionError:
        return failed(502, strings["connection_error"])
    except requests.exceptions.RequestException:
        return failed(502, strings["request_error"])
    except UserFacingError as error:
        return failed(502, error.user_message)
    except Exception:
        return failed(500, strings["unexpected_error"])
    return flask.redirect("/library?saved=" + meta["id"], code=303)


@app.route("/library/keep", methods=["POST"])
def library_keep_from_browser():
    """Receives the Keep a copy bookmarklet's post from another site. It only
    holds the page as a draft and asks; nothing is kept until you confirm."""
    strings = load_strings()
    page_html = request.form.get("html", "")
    url = request.form.get("url", "").strip()
    if not page_html or not re.match(r"^https?://", url, re.I):
        return strings["invalid_url"], 400
    draft = library.save_draft(page_html, url)
    return render_keep_confirmation(strings, url=url, title=library.page_title(page_html), draft=draft)


@app.route("/library/<item_id>/delete", methods=["POST"])
def library_delete(item_id):
    strings = load_strings()
    problem = library_post_problem(strings)
    if problem:
        return render_library(strings, 403, problem)
    if not library.delete(item_id):
        return "", 404
    return flask.redirect("/library?deleted=1", code=303)


@app.route("/library/<item_id>/reader", methods=["GET"])
def library_reader(item_id):
    """The Reader version saved with a copy; works offline."""
    meta = library.get(item_id)
    saved = library.reader_article(item_id)
    if not meta or saved is None:
        return "", 404
    article = {key: saved.get(key) or "" for key in ("title", "site", "author", "published", "lead_image", "content")}
    article["minutes"] = saved.get("minutes") or 0
    return render_reader("", meta.get("url", ""), "/library/" + item_id + "/", load_strings(), article=article)


@app.route("/library/<item_id>/", defaults={"name": "index.html"}, methods=["GET"])
@app.route("/library/<item_id>/<path:name>", methods=["GET"])
def library_file(item_id, name):
    """A kept copy and its files, sandboxed like fetched pages: its scripts
    can't reach this server's settings or other copies."""
    found = library.item_file(item_id, name)
    if not found:
        return "", 404
    path, content_type = found
    response = flask.send_file(path, mimetype=content_type, max_age=0)
    response.headers["Content-Security-Policy"] = FETCHED_PAGE_CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    if name != "index.html":
        # The sandboxed page has an opaque origin, so its fonts (and module
        # scripts) are cross-origin requests that need this header.
        response.headers["Access-Control-Allow-Origin"] = "*"
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
        return error_page(strings["invalid_url"], 400, strings)
    try:
        page_html = get_page(url, strings)
    except requests.exceptions.Timeout:
        return error_page(strings["timeout_error"], 400, strings)
    except requests.exceptions.ConnectionError:
        return error_page(strings["connection_error"], 400, strings)
    except requests.exceptions.RequestException:
        return error_page(strings["request_error"], 400, strings)
    except UserFacingError as error:
        return error_page(error.user_message, 400, strings)
    except Exception:
        return error_page(strings["unexpected_error"], 500, strings)

    return render_reader(page_html, url, "/" + url, strings)


@app.route("/reader", methods=["POST"])
def reader_from_browser():
    """Reader view for a page sent by the Send to Reader bookmarklet: the page
    exactly as the person's own browser shows it, with their own access."""
    strings = load_strings()
    page_html = request.form.get("html", "")
    url = request.form.get("url", "").strip()
    if not page_html or not re.match(r"^https?://", url, re.I):
        return error_page(strings["invalid_url"], 400, strings)
    return render_reader(page_html, url, None, strings)


LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def host_problem():
    """Refuse requests not addressed to localhost (or a host listed in
    LADDER_ALLOWED_HOSTS). Stops a website from reaching this server by
    pointing its own domain at your computer (DNS rebinding)."""
    host = (urlparse("//" + (request.host or "")).hostname or "").lower()
    allowed = LOCAL_HOSTS | {h.strip().lower() for h in os.environ.get("LADDER_ALLOWED_HOSTS", "").split(",") if h.strip()}
    if host not in allowed and not host.endswith(".localhost"):
        return "this only works at localhost (see LADDER_ALLOWED_HOSTS in the README)"
    return None


def same_site_problem(json_only=False):
    """Refuse requests from other sites: the Origin must be this server (a
    sandboxed page's "null" counts as another site), and with json_only the
    body must be JSON, which another site can't send without a CORS preflight
    this server never approves."""
    origin = request.headers.get("Origin")
    if origin and origin.rstrip("/") != request.host_url.rstrip("/"):
        return "not allowed from another site"
    if json_only and not request.is_json:
        return "not allowed"
    return None


def local_request_problem(json_only=False):
    """Why this request may not use the settings or the AI, or None if it may.
    These endpoints use your API keys, so they need both checks above."""
    return host_problem() or same_site_problem(json_only)


@app.before_request
def library_host_check():
    """Your library is private: same localhost rule as the settings."""
    if (request.endpoint or "").startswith("library_"):
        problem = host_problem()
        if problem:
            return problem, 403
    return None


@app.route("/settings", methods=["GET"])
def settings_view():
    problem = local_request_problem()
    if problem:
        return problem, 403
    nonce = secrets.token_urlsafe(16)
    providers = {key: {k: v for k, v in spec.items() if k in ("label", "key_hint", "models")}
                 for key, spec in ai_settings.PROVIDERS.items()}
    body = flask.render_template_string(settings_page.SETTINGS_TEMPLATE, nonce=nonce, providers=providers,
                                        show_logout=auth.enabled())
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


EXPORT_MAX_HTML = 10 * 1024 * 1024


@app.route("/reader/export", methods=["POST"])
def reader_export():
    """Markdown or EPUB download of the article the Reader page is showing
    (tidied, if AI tidy was applied). See export.py. It uses no keys, so it
    only needs to come from this server's own pages."""
    problem = same_site_problem(json_only=True)
    if problem:
        return flask.jsonify(error=problem), 403
    try:
        fmt, meta, html = export.read_request(request.get_json(silent=True))
    except ValueError as error:
        return flask.jsonify(error=str(error)), 400
    if len(html) > EXPORT_MAX_HTML:
        return flask.jsonify(error="article too large to export"), 413
    strings = load_strings()
    labels = {key[len("export_label_"):]: value for key, value in strings.items() if key.startswith("export_label_")}
    fragment = reader.clean_fragment(html, meta["url"] or request.host_url)
    payload, mimetype, filename = export.build(fmt, meta, fragment, labels)
    response = Response(payload, mimetype=mimetype)
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def render_reader(page_html, url, original_href, strings, article=None):
    kept = article is not None  # the Reader version of a library copy
    try:
        article = article if kept else reader.extract_article(page_html, url)
    except Exception:
        article = {"title": url, "site": "", "author": "", "published": "",
                   "minutes": 0, "lead_image": "", "content": ""}

    nonce = secrets.token_urlsafe(16)
    body = flask.render_template_string(
        reader.READER_TEMPLATE,
        font_face_css=theme.FONT_FACE_CSS,
        article=article,
        source_url=url,
        original_href=original_href,
        nonce=nonce,
        tidy_enabled=tidy.enabled(),
        keep_href=None if kept else keep_copy_href(page_html, url, original_href),
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
        return error_page(strings["timeout_error"], 400, strings)
    except requests.exceptions.ConnectionError:
        return error_page(strings["connection_error"], 400, strings)
    except requests.exceptions.RequestException:
        return error_page(strings["request_error"], 400, strings)
    except UserFacingError as error:
        return error_page(error.user_message, 400, strings)
    except Exception:
        return error_page(strings["unexpected_error"], 500, strings)


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
            return error_page(strings["timeout_error"], 400, strings)
        except requests.exceptions.ConnectionError:
            return error_page(strings["connection_error"], 400, strings)
        except requests.exceptions.RequestException:
            return error_page(strings["request_error"], 400, strings)
        except UserFacingError as error:
            return error_page(error.user_message, 400, strings)
        except Exception:
            return error_page(strings["unexpected_error"], 500, strings)
    else:
        return error_page(strings["invalid_url"], 400, strings)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=os.getenv("PORT") or 5000, debug=False)
