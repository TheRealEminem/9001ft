"""Offline regression tests. Run: python -m unittest discover -s tests -v."""
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

# Tests never touch real saved settings: each run gets its own data folder.
os.environ["LADDER_DATA_DIR"] = tempfile.mkdtemp(prefix="9001ft-tests-")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import portable as web

ARTICLE = "<html><head><title>Article</title></head><body><p>Useful article content.</p></body></html>"
URL = "https://example.com/news/article"


def error_page_message(response):
    """The explanation shown on the friendly error page."""
    match = re.search(r'<p class="message" id="error-message">(.*?)</p>', response.get_data(as_text=True), re.S)
    return web.html_lib.unescape(match.group(1)) if match else None


class AppTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"LOCALE": "en"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.network = patch.object(web.requests.sessions.Session, "request", side_effect=AssertionError("Unexpected network access"))
        self.network.start()
        self.addCleanup(self.network.stop)
        web.jobs.clear()
        web.page_cache.clear()
        web.load_strings_for_locale.cache_clear()
        self.addCleanup(web.jobs.clear)
        self.addCleanup(web.page_cache.clear)
        self.addCleanup(web.load_strings_for_locale.cache_clear)
        self.client = web.app.test_client()
        self.strings = web.load_strings()

    def test_home_renders_form(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"What would you like to read?", response.data)
        self.assertIn(b'<form', response.data)
        self.assertIn(b'id="reader-mode"', response.data)

    def test_portuguese_home(self):
        with patch.dict(os.environ, {"LOCALE": "pt"}):
            response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'lang="pt"', response.data)

    def test_unknown_locale_falls_back_to_english(self):
        with patch.dict(os.environ, {"LOCALE": "nonexistent"}):
            self.assertEqual(web.load_strings()["lang"], "en")

    def test_locale_path_traversal_falls_back(self):
        with patch.dict(os.environ, {"LOCALE": "../../etc/passwd"}):
            self.assertEqual(web.get_locale(), "en")

    def test_article_post_returns_processed_result(self):
        with patch.object(web, "bypass_paywall", return_value=ARTICLE) as fetch:
            response = self.client.post("/article", data={"link": URL})
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Useful article content", response.data)
        fetch.assert_called_once_with(URL, self.strings)

    def test_article_requires_link(self):
        self.assertEqual(self.client.post("/article", data={}).status_code, 400)

    def test_article_errors_are_user_facing(self):
        cases = [
            (web.requests.exceptions.Timeout(), "timeout_error", 400),
            (web.requests.exceptions.ConnectionError(), "connection_error", 400),
            (web.requests.exceptions.RequestException(), "request_error", 400),
            (RuntimeError("private details"), "unexpected_error", 500),
        ]
        for error, message, status in cases:
            with self.subTest(error=type(error).__name__), patch.object(web, "bypass_paywall", side_effect=error):
                response = self.client.post("/article", data={"link": URL})
                self.assertEqual(response.status_code, status)
                self.assertEqual(error_page_message(response), self.strings[message])
                self.assertNotIn("private details", response.get_data(as_text=True))

    def test_article_custom_error(self):
        with patch.object(web, "bypass_paywall", side_effect=web.UserFacingError("Unavailable")):
            response = self.client.post("/article", data={"link": URL})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(error_page_message(response), "Unavailable")

    def test_cached_article_avoids_fetch(self):
        web.page_cache[URL] = {"html": ARTICLE, "timestamp": web.time.time()}
        with patch.object(web, "bypass_paywall") as fetch:
            response = self.client.get("/" + URL)
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Useful article content.", body)
        self.assertIn('id="ladder-reader-btn" href="http://localhost/reader/' + URL + '"', body)
        self.assertTrue(body.endswith("</body></html>"))
        fetch.assert_not_called()
        # The site's own scripts run sandboxed, away from this server's origin.
        csp = response.headers["Content-Security-Policy"]
        self.assertTrue(csp.startswith("sandbox "))
        self.assertIn("allow-scripts", csp)
        self.assertNotIn("allow-same-origin", csp)

    def test_scripts_on_fetched_pages_cannot_change_settings(self):
        # A sandboxed page's requests carry "Origin: null", which is refused.
        response = self.client.post("/settings/ai", json={"mode": "off"}, headers={"Origin": "null"})
        self.assertEqual(response.status_code, 403)

    def test_status_requires_url(self):
        self.assertEqual(self.client.get("/status").status_code, 400)

    def test_status_stream_success_and_cleanup(self):
        def start(thread):
            thread._target(*thread._args, **thread._kwargs)
        with patch.object(web.threading.Thread, "start", start), patch.object(web, "bypass_paywall", return_value=ARTICLE):
            response = self.client.get("/status", query_string={"url": URL})
            body = response.get_data(as_text=True)
        self.assertEqual(response.mimetype, "text/event-stream")
        event = json.loads(body.split("data: ", 1)[1].strip())
        self.assertEqual(event, {"step": "done", "redirect": "/" + URL})
        self.assertEqual(web.jobs, {})
        self.assertEqual(web.page_cache[URL]["html"], ARTICLE)

    def test_status_stream_failure_and_cleanup(self):
        def start(thread):
            thread._target(*thread._args, **thread._kwargs)
        with patch.object(web.threading.Thread, "start", start), patch.object(web, "bypass_paywall", side_effect=web.requests.exceptions.Timeout()):
            response = self.client.get("/status", query_string={"url": URL})
            body = response.get_data(as_text=True)
        self.assertIn("event: error_msg", body)
        self.assertIn(self.strings["timeout_error"], body)
        self.assertEqual(web.jobs, {})
        self.assertEqual(web.page_cache, {})

    def test_html_fixes_relative_links(self):
        soup = web.BeautifulSoup(web.process_html_document(ARTICLE, URL), "html.parser")
        self.assertEqual(soup.base["href"], "https://example.com/news/")
        self.assertIn("Useful article content", soup.get_text())

    def test_html_replaces_existing_base(self):
        source = '<html><head><base href="/"></head><body>Article</body></html>'
        soup = web.BeautifulSoup(web.process_html_document(source, URL), "html.parser")
        self.assertEqual(len(soup.find_all("base")), 1)
        self.assertEqual(soup.base["href"], "https://example.com/news/")

    def test_direct_fetch_adds_scheme_and_googlebot(self):
        response = Mock(status_code=200, text=ARTICLE, url=URL, apparent_encoding="utf-8")
        with patch.object(web.requests, "get", return_value=response) as get:
            result = web.bypass_paywall("example.com/news/article", self.strings)
        get.assert_called_once_with(URL, headers=web.googlebot_headers, timeout=30)
        self.assertIn("Useful article content", result)

    def test_blocked_response_uses_archive(self):
        response = Mock(status_code=403, text="Blocked", url=URL)
        with patch.object(web.requests, "get", return_value=response), patch.object(web, "fetch_via_archive_org", return_value=(ARTICLE, URL)) as archive, patch.object(web, "fetch_via_archive_ph") as fallback:
            result = web.bypass_paywall(URL, self.strings)
        self.assertIn("Useful article content", result)
        archive.assert_called_once_with(URL, None)
        fallback.assert_not_called()

    def test_second_archive_is_used_when_first_is_empty(self):
        response = Mock(status_code=403, text="Blocked", url=URL)
        with patch.object(web.requests, "get", return_value=response), patch.object(web, "fetch_via_archive_org", return_value=(None, None)), patch.object(web, "fetch_via_archive_ph", return_value=(ARTICLE, URL)) as fallback:
            self.assertIn("Useful article content", web.bypass_paywall(URL, self.strings))
        fallback.assert_called_once_with(URL, None)

    def test_all_archives_empty_raise_user_error(self):
        response = Mock(status_code=403, text="Blocked", url=URL)
        with patch.object(web.requests, "get", return_value=response), patch.object(web, "fetch_via_archive_org", return_value=(None, None)), patch.object(web, "fetch_via_archive_ph", return_value=(None, None)):
            with self.assertRaises(web.UserFacingError) as error:
                web.bypass_paywall(URL, self.strings)
        self.assertEqual(str(error.exception), self.strings["challenge_error"])

    def test_medium_uses_freedium_before_direct_fetch(self):
        with patch.object(web, "fetch_via_freedium", return_value=(ARTICLE, URL)), patch.object(web.requests, "get") as get:
            self.assertIn("Useful article content", web.bypass_paywall("https://medium.com/story", self.strings))
        get.assert_not_called()

    def test_cache_removes_only_expired_entries(self):
        web.page_cache.update({"old": {"timestamp": 0}, "fresh": {"timestamp": 999}})
        with patch.object(web.time, "time", return_value=1000):
            web.clean_page_cache()
        self.assertNotIn("old", web.page_cache)
        self.assertIn("fresh", web.page_cache)


SNAPSHOT = "https://web.archive.org/web/20261118081412id_/https://en.wikipedia.org/wiki/Ladder"
ARCHIVED = """<html><head>
<link rel="stylesheet" href="/w/load.php?modules=site.styles">
<style>body { background: url('/img/bg.png'); }</style>
<script src="/static/app.js"></script>
</head><body>
<img src="/static/logo.svg" srcset="/a.png 1x, /b.png 2x">
<img src="https://upload.wikimedia.org/x.jpg">
<img src="data:image/gif;base64,R0lGOD">
<a href="/wiki/Step_ladder">Step ladder</a>
<a href="#history">History</a>
<div style="background-image:url(&quot;bg2.png&quot;)">Text</div>
</body></html>"""

LONG_TEXT = " ".join(["The ladder is a vertical or inclined set of rungs or steps."] * 12)
READER_PAGE = f"""<html><head>
<base href="https://example.com/news/">
<meta property="og:title" content="How tall can a ladder get?">
<meta property="og:site_name" content="Example Times">
<meta name="author" content="Pat Writer">
<meta property="article:published_time" content="2026-11-20T10:00:00Z">
</head><body>
<nav><a href="/">Home</a> <a href="/sports">Sports</a></nav>
<article>
  <h1>How tall can a ladder get?</h1>
  <p onclick="steal()">{LONG_TEXT}</p>
  <img src="data:image/gif;base64,R0lGOD" data-src="photos/ladder.jpg" alt="A ladder">
  <p>{LONG_TEXT} <a href="javascript:alert(1)">bad link</a> <a href="/more">more</a></p>
  <script>alert('x')</script>
</article>
<footer>Copyright</footer>
</body></html>"""


class WaybackRewriteTests(unittest.TestCase):
    def setUp(self):
        self.soup = web.BeautifulSoup(web.process_html_document(ARCHIVED, SNAPSHOT), "html.parser")

    def test_parse_wayback_url(self):
        self.assertEqual(web.parse_wayback_url(SNAPSHOT), ("20261118081412", "https://en.wikipedia.org/wiki/Ladder"))
        self.assertIsNone(web.parse_wayback_url("https://archive.ph/abc12"))

    def test_stylesheets_go_through_relay_in_css_mode(self):
        self.assertEqual(self.soup.find("link", rel="stylesheet")["href"],
                         web.RELAY_TOKEN + "https://web.archive.org/web/20261118081412cs_/https://en.wikipedia.org/w/load.php?modules=site.styles")

    def test_root_relative_assets_point_at_archived_copies(self):
        prefix = "https://web.archive.org/web/20261118081412id_/"
        self.assertEqual(self.soup.find("script")["src"], prefix + "https://en.wikipedia.org/static/app.js")
        logo = self.soup.find_all("img")[0]
        self.assertEqual(logo["src"], prefix + "https://en.wikipedia.org/static/logo.svg")
        self.assertEqual(logo["srcset"], f"{prefix}https://en.wikipedia.org/a.png 1x, {prefix}https://en.wikipedia.org/b.png 2x")
        self.assertEqual(logo["data-live-src"], "https://en.wikipedia.org/static/logo.svg")

    def test_absolute_assets_and_inline_css_are_archived(self):
        imgs = self.soup.find_all("img")
        self.assertIn("20261118081412id_/https://upload.wikimedia.org/x.jpg", imgs[1]["src"])
        self.assertEqual(imgs[2]["src"], "data:image/gif;base64,R0lGOD")
        self.assertIn(f"url('{web.RELAY_TOKEN}https://web.archive.org/web/20261118081412id_/https://en.wikipedia.org/img/bg.png')", self.soup.style.string)
        # Inline style attributes stay direct: plain backgrounds don't need CORS.
        self.assertNotIn(web.RELAY_TOKEN, self.soup.find("div")["style"])
        self.assertIn("20261118081412id_/https://en.wikipedia.org/wiki/bg2.png", self.soup.find("div")["style"])

    def test_page_links_go_to_the_live_site(self):
        links = self.soup.find_all("a")
        self.assertEqual(links[0]["href"], "https://en.wikipedia.org/wiki/Step_ladder")
        self.assertEqual(links[1]["href"], "#history")

    def test_non_wayback_pages_are_untouched(self):
        soup = web.BeautifulSoup(web.process_html_document(ARCHIVED, URL), "html.parser")
        self.assertEqual(soup.find("script")["src"], "/static/app.js")


MESSY = """<div><p>Intro text here.</p><div>Section Heading</div><p>This paragraph was split</p><p>right in the middle.</p>
<div>Advertisement</div><ul><li>One</li><li>Two</li></ul>
<figure><img src="https://x.example/a.jpg" alt="A ladder"><figcaption>Photo: Someone</figcaption></figure>
Loose <a href="https://x.example/">inline</a> text.</div>"""
TIDY_ENV = {"TIDY_API_BASE": "http://router.test/v1", "TIDY_MODEL": "main-model"}


def no_tidy_env():
    return {key: value for key, value in os.environ.items() if not key.startswith("TIDY_")}


def chat_reply(content, status=200):
    return Mock(status_code=status, json=Mock(return_value={"choices": [{"message": {"content": content}}]}))


class TidyTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {**no_tidy_env(), **TIDY_ENV}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.network = patch.object(web.requests.sessions.Session, "request", side_effect=AssertionError("Unexpected network access"))
        self.network.start()
        self.addCleanup(self.network.stop)
        self.blocks = web.tidy.split_blocks(MESSY)

    def test_splits_article_into_ordered_blocks(self):
        self.assertEqual([(b.tag, b.text) for b in self.blocks], [
            ("p", "Intro text here."), ("text", "Section Heading"), ("p", "This paragraph was split"),
            ("p", "right in the middle."), ("text", "Advertisement"), ("li", "One"), ("li", "Two"),
            ("img", "A ladder"), ("figcaption", "Photo: Someone"), ("text", "Loose inline text."),
        ])
        self.assertTrue(self.blocks[7].atomic)
        self.assertEqual(self.blocks[5].list_type, "ul")

    def test_splits_headings_wrapped_in_paragraphs(self):
        blocks = web.tidy.split_blocks('<p><h2>Safety</h2><span>[ <a href="https://x.example/edit">edit</a> ]</span></p><p>Body.</p>')
        self.assertEqual([(b.tag, b.text) for b in blocks], [("h2", "Safety"), ("text", "[ edit ]"), ("p", "Body.")])

    def test_applies_a_valid_plan(self):
        plan = [{"ids": [2], "as": "h2"}, {"ids": [3, 4], "as": "p"}, {"ids": [5], "as": "drop"}]
        groups, stats = web.tidy.apply_plan(self.blocks, plan)
        html, lists = web.tidy.render(groups)
        self.assertIn("<h2>Section Heading</h2>", html)
        self.assertIn("<p>This paragraph was split right in the middle.</p>", html)
        self.assertNotIn("Advertisement", html)
        self.assertIn("<ul>\n<li>One</li>\n<li>Two</li>\n</ul>", html)
        self.assertIn('<p class="caption">Photo: Someone</p>', html)
        self.assertIn('<a href="https://x.example/">inline</a>', html)
        self.assertEqual((stats["headings"], stats["joined"], stats["dropped_words"], lists), (1, 1, 1, 1))

    def test_ignores_unsafe_or_invalid_plan_entries(self):
        long_text = " ".join(["word"] * 70)
        blocks = web.tidy.split_blocks(MESSY.replace("Intro text here.", long_text))
        plan = [
            {"ids": [1, 9], "as": "p"},        # not next to each other
            {"ids": [99], "as": "h2"},         # no such block
            {"ids": [1], "as": "drop"},        # 70 words: too long to drop
            {"ids": [7, 8], "as": "p"},        # would merge an image
            {"ids": [3], "as": "invent"},      # unknown role
            {"ids": [3, 4], "as": "h2"},       # headings are one block
            {"ids": [2], "as": "h2"},
            {"ids": [2], "as": "drop"},        # already used
        ]
        groups, stats = web.tidy.apply_plan(blocks, plan)
        html, _ = web.tidy.render(groups)
        self.assertIn(long_text, html)
        self.assertIn("<h2>Section Heading</h2>", html)
        self.assertEqual(stats["dropped_words"], 0)
        self.assertEqual(stats["joined"], 0)

    def test_caps_total_clutter_removed(self):
        page = "<div>" + "".join(f"<p>clutter block {i} here</p>" for i in range(10)) + "</div>"
        blocks = web.tidy.split_blocks(page)
        groups, stats = web.tidy.apply_plan(blocks, [{"ids": [i], "as": "drop"} for i in range(1, 11)])
        self.assertLessEqual(stats["dropped_words"] / 40, web.tidy.MAX_DROP_SHARE)
        self.assertEqual(stats["dropped_blocks"], 2)

    def test_parses_plans_from_chatty_answers(self):
        self.assertEqual(web.tidy.parse_plan('Sure!\n```json\n{"blocks": [{"ids": [1], "as": "h2"}]}\n```'), [{"ids": [1], "as": "h2"}])
        self.assertEqual(web.tidy.parse_plan('<think>hmm {"x":1}</think>[{"ids": [2], "as": "p"}]'), [{"ids": [2], "as": "p"}])
        with self.assertRaises(web.tidy.TidyError):
            web.tidy.parse_plan("I can't help with that.")

    def test_tidy_end_to_end_sends_previews_not_full_text(self):
        long_text = " ".join(["lorem"] * 200)
        page = MESSY.replace("Intro text here.", long_text)
        reply = chat_reply('{"blocks": [{"ids": [2], "as": "h2"}, {"ids": [3, 4], "as": "p"}, {"ids": [5], "as": "drop"}]}')
        with patch.object(web.tidy.requests, "post", return_value=reply) as post:
            result = web.tidy.tidy(page)
        sent = post.call_args.kwargs["json"]
        self.assertEqual(post.call_args.args[0], "http://router.test/v1/chat/completions")
        self.assertEqual(sent["model"], "main-model")
        self.assertEqual(sent["temperature"], 0)
        self.assertIn("[2] <text> Section Heading", sent["messages"][1]["content"])
        self.assertNotIn(long_text, sent["messages"][1]["content"])
        self.assertIn("<h2>Section Heading</h2>", result["html"])
        self.assertIn(long_text, result["html"])
        self.assertEqual(result["model"], "main-model")

    def test_reads_key_from_file(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".key", delete=False) as handle:
            handle.write("secret-key\n")
        self.addCleanup(os.remove, handle.name)
        with patch.dict(os.environ, {"TIDY_API_KEY_FILE": handle.name}):
            with patch.object(web.tidy.requests, "post", return_value=chat_reply('{"blocks": []}')) as post:
                web.tidy.tidy(MESSY)
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer secret-key")

    def test_falls_back_when_first_service_is_unreachable(self):
        fallback = {"TIDY_FALLBACK_API_BASE": "http://localhost:11434/v1", "TIDY_FALLBACK_MODEL": "local-model"}
        replies = [web.requests.exceptions.ConnectionError(), chat_reply('{"blocks": []}')]
        with patch.dict(os.environ, fallback), patch.object(web.tidy.requests, "post", side_effect=replies) as post:
            result = web.tidy.tidy(MESSY)
        self.assertEqual(result["model"], "local-model")
        self.assertEqual(post.call_args.args[0], "http://localhost:11434/v1/chat/completions")

    def test_reports_unreachable_and_errors_plainly(self):
        with patch.object(web.tidy.requests, "post", side_effect=web.requests.exceptions.ConnectionError()):
            with self.assertRaisesRegex(web.tidy.TidyError, "couldn't reach"):
                web.tidy.tidy(MESSY)
        with patch.object(web.tidy.requests, "post", return_value=chat_reply("", status=500)):
            with self.assertRaisesRegex(web.tidy.TidyError, "error 500"):
                web.tidy.tidy(MESSY)


class TidyRouteTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {**no_tidy_env(), **TIDY_ENV}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.network = patch.object(web.requests.sessions.Session, "request", side_effect=AssertionError("Unexpected network access"))
        self.network.start()
        self.addCleanup(self.network.stop)
        web.page_cache.clear()
        self.addCleanup(web.page_cache.clear)
        self.client = web.app.test_client()

    def post(self, payload, **kwargs):
        return self.client.post("/reader/tidy", json=payload, **kwargs)

    def test_tidies_and_sanitizes_what_the_page_sends(self):
        dirty = MESSY.replace("Intro text here.", 'Intro <script>alert(1)</script><b onclick="x()">text</b> here.')
        with patch.object(web.tidy.requests, "post", return_value=chat_reply('{"blocks": [{"ids": [2], "as": "h2"}]}')) as post:
            response = self.post({"html": dirty, "url": URL})
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertIn("<h2>Section Heading</h2>", data["html"])
        self.assertNotIn("script", data["html"])
        self.assertNotIn("onclick", data["html"])
        self.assertNotIn("alert", post.call_args.kwargs["json"]["messages"][1]["content"])

    def test_refuses_other_sites_and_non_json(self):
        self.assertEqual(self.post({"html": MESSY}, headers={"Origin": "https://evil.example"}).status_code, 403)
        self.assertEqual(self.client.post("/reader/tidy", data={"html": MESSY}).status_code, 403)
        self.assertEqual(self.post({"html": ""}).status_code, 400)

    def test_reports_tidy_errors_as_json(self):
        with patch.object(web.tidy.requests, "post", side_effect=web.requests.exceptions.ConnectionError()):
            response = self.post({"html": MESSY, "url": URL})
        self.assertEqual(response.status_code, 502)
        self.assertIn("couldn't reach", response.get_json()["error"])

    def test_off_when_not_configured(self):
        with patch.dict(os.environ, no_tidy_env(), clear=True):
            self.assertEqual(self.post({"html": MESSY}).status_code, 404)
            web.page_cache[URL] = {"html": READER_PAGE, "timestamp": web.time.time()}
            self.assertNotIn('id="tidy"', self.client.get("/reader/" + URL).get_data(as_text=True))

    def test_reader_shows_tidy_button_when_configured(self):
        web.page_cache[URL] = {"html": READER_PAGE, "timestamp": web.time.time()}
        response = self.client.get("/reader/" + URL)
        self.assertIn('id="tidy"', response.get_data(as_text=True))
        self.assertIn("connect-src 'self'", response.headers["Content-Security-Policy"])

    def test_local_env_file_never_overrides_real_settings(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as handle:
            handle.write("# comment\nTIDY_MODEL=from-file\nNEW_9001FT_SETTING='quoted value'\n")
        self.addCleanup(os.remove, handle.name)
        web.load_local_env(handle.name)
        self.assertEqual(os.environ["TIDY_MODEL"], "main-model")
        self.assertEqual(os.environ["NEW_9001FT_SETTING"], "quoted value")


def clear_saved_settings():
    path = web.ai_settings.settings_path()
    if os.path.exists(path):
        os.remove(path)


class AISettingsTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, no_tidy_env(), clear=True)
        env.start()
        self.addCleanup(env.stop)
        clear_saved_settings()
        self.addCleanup(clear_saved_settings)
        self.network = patch.object(web.requests.sessions.Session, "request", side_effect=AssertionError("Unexpected network access"))
        self.network.start()
        self.addCleanup(self.network.stop)

    def test_backends_for_each_mode(self):
        s = web.ai_settings
        local = {"mode": "local", "ollama_url": "http://localhost:11434", "ollama_model": "qwen3.5:4b", "local_reasoning": "none"}
        [b] = s.backends_for(local)
        self.assertEqual((b.base, b.model, b.reasoning_effort, b.kind), ("http://localhost:11434/v1", "qwen3.5:4b", "none", "openai"))
        claude = {"mode": "byok", "provider": "anthropic", "keys": {"anthropic": {"key": "sk-ant-test"}}, "models": {}}
        [b] = s.backends_for(claude)
        self.assertEqual((b.kind, b.model, b.key), ("anthropic", "claude-opus-5-5", "sk-ant-test"))
        self.assertEqual(s.backends_for({**claude, "keys": {}}), [], "hosted providers need a key")
        router = {"mode": "byok", "provider": "custom", "base_url": "http://router.test/v1/", "models": {"custom": "general"},
                  "fallback_local": True, **{k: v for k, v in local.items() if k != "mode"}}
        first, second = s.backends_for(router)
        self.assertEqual((first.base, first.model, first.key), ("http://router.test/v1", "general", ""))
        self.assertEqual(second.model, "qwen3.5:4b")
        self.assertEqual(s.backends_for({"mode": "off"}), [])

    def test_saved_settings_override_environment(self):
        with patch.dict(os.environ, TIDY_ENV):
            self.assertEqual(web.ai_settings.source(), "env")
            self.assertTrue(web.tidy.enabled())
            web.ai_settings.save({"mode": "off"})
            self.assertEqual(web.ai_settings.source(), "settings")
            self.assertFalse(web.tidy.enabled())

    def test_save_is_private_and_keys_stay_hidden(self):
        s = web.ai_settings
        s.save(s.merge_draft({"mode": "byok", "provider": "openai", "key": "sk-secret-1234", "model": "some-model"}))
        mode = stat.S_IMODE(os.stat(s.settings_path()).st_mode)
        self.assertEqual(mode, 0o600)
        view = s.public_view()
        self.assertEqual(view["keys"], {"openai": "saved key ending 1234"})
        self.assertNotIn("sk-secret", json.dumps(view))
        # A blank key keeps the saved one; clear_key removes it.
        s.save(s.merge_draft({"mode": "byok", "provider": "openai", "key": "", "model": "other"}))
        self.assertEqual(s.load()["keys"]["openai"], {"key": "sk-secret-1234"})
        s.save(s.merge_draft({"mode": "byok", "provider": "openai", "clear_key": True}))
        self.assertNotIn("openai", s.load()["keys"])

    def test_rejects_non_http_addresses(self):
        with self.assertRaises(ValueError):
            web.ai_settings.merge_draft({"mode": "local", "ollama_url": "file:///etc"})

    def test_environment_settings_show_without_revealing_keys(self):
        env = {"TIDY_API_BASE": "http://router.test/v1", "TIDY_MODEL": "general", "TIDY_API_KEY_FILE": "/no/such/file",
               "TIDY_FALLBACK_API_BASE": "http://localhost:11434/v1", "TIDY_FALLBACK_MODEL": "qwen3.5:4b",
               "TIDY_FALLBACK_REASONING_EFFORT": "none"}
        with patch.dict(os.environ, env):
            view = web.ai_settings.public_view()
        self.assertEqual(view["source"], "env")
        self.assertEqual((view["mode"], view["provider"], view["base_url"]), ("byok", "custom", "http://router.test/v1"))
        self.assertEqual(view["keys"], {"custom": "saved key file"})
        self.assertTrue(view["fallback_local"])
        self.assertEqual(view["ollama_model"], "qwen3.5:4b")

    def test_lists_local_models_without_embedding_models(self):
        tags = Mock(status_code=200, json=Mock(return_value={"models": [
            {"name": "qwen3.5:4b", "size": 4_000_000_000}, {"name": "nomic-embed-text:latest", "size": 300_000_000}]}))
        tags.raise_for_status = Mock()
        with patch.object(web.requests, "get", return_value=tags) as get:
            models = web.ai_settings.list_models({"mode": "local", "ollama_url": "http://localhost:11434/"})
        self.assertEqual(get.call_args.args[0], "http://localhost:11434/api/tags")
        self.assertEqual(models, [{"id": "qwen3.5:4b", "size": 4.0}])

    def test_lists_hosted_models(self):
        listing = Mock(status_code=200, json=Mock(return_value={"data": [{"id": "b-model"}, {"id": "a-model"}]}))
        listing.raise_for_status = Mock()
        ai = {"mode": "byok", "provider": "groq", "keys": {"groq": {"key": "gsk_x"}}}
        with patch.object(web.requests, "get", return_value=listing) as get:
            models = web.ai_settings.list_models(ai)
        self.assertEqual(get.call_args.args[0], "https://api.groq.com/openai/v1/models")
        self.assertEqual(get.call_args.kwargs["headers"], {"Authorization": "Bearer gsk_x"})
        self.assertEqual([m["id"] for m in models], ["a-model", "b-model"])
        client = MagicMock()
        client.models.list.return_value = [Mock(id="claude-opus-5-5")]
        with patch("anthropic.Anthropic", return_value=client):
            models = web.ai_settings.list_models({"mode": "byok", "provider": "anthropic", "keys": {"anthropic": {"key": "k"}}})
        self.assertEqual(models, [{"id": "claude-opus-5-5", "size": None}])


def claude_reply(text, stop_reason="end_turn"):
    return Mock(stop_reason=stop_reason, content=[Mock(type="thinking"), Mock(type="text", text=text)])


class ClaudeTidyTests(unittest.TestCase):
    def setUp(self):
        self.network = patch.object(web.requests.sessions.Session, "request", side_effect=AssertionError("Unexpected network access"))
        self.network.start()
        self.addCleanup(self.network.stop)
        self.client = MagicMock()
        patcher = patch.object(web.tidy.anthropic, "Anthropic", return_value=self.client)
        self.constructor = patcher.start()
        self.addCleanup(patcher.stop)

    def backend(self, model="claude-opus-5-5", effort=""):
        return web.ai_settings.Backend(base="", model=model, key="sk-ant-test", kind="anthropic", reasoning_effort=effort)

    def test_opus_uses_server_side_fallbacks_and_low_effort(self):
        self.client.beta.messages.create.return_value = claude_reply('{"blocks": []}')
        answer = web.tidy.chat(self.backend(), "system", "user")
        self.assertEqual(answer, '{"blocks": []}')
        kwargs = self.client.beta.messages.create.call_args.kwargs
        self.assertEqual(kwargs["betas"], ["server-side-fallback-2026-07-01"])
        self.assertEqual(kwargs["fallbacks"], "default")
        self.assertEqual(kwargs["output_config"], {"effort": "low"})
        self.assertNotIn("temperature", kwargs)
        self.assertEqual(self.constructor.call_args.kwargs["api_key"], "sk-ant-test")

    def test_haiku_uses_plain_messages_and_keeps_chosen_effort(self):
        self.client.messages.create.return_value = claude_reply("OK")
        web.tidy.chat(self.backend("claude-haiku-5-5", effort="high"), "system", "user")
        kwargs = self.client.messages.create.call_args.kwargs
        self.assertEqual(kwargs["output_config"], {"effort": "high"})
        self.assertNotIn("fallbacks", kwargs)

    def test_refusals_and_unreachable_service(self):
        self.client.beta.messages.create.return_value = claude_reply("", stop_reason="refusal")
        with self.assertRaisesRegex(web.tidy.TidyError, "declined"):
            web.tidy.chat(self.backend(), "s", "u")
        self.client.beta.messages.create.side_effect = web.tidy.anthropic.APIConnectionError(request=Mock())
        local = web.ai_settings.Backend(base="http://localhost:11434/v1", model="qwen3.5:4b")
        with patch.object(web.tidy.requests, "post", return_value=chat_reply("OK")):
            answer, used = web.tidy.first_answer([self.backend(), local], "s", "u")
        self.assertEqual((answer, used.model), ("OK", "qwen3.5:4b"))


class SettingsRouteTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, no_tidy_env(), clear=True)
        env.start()
        self.addCleanup(env.stop)
        clear_saved_settings()
        self.addCleanup(clear_saved_settings)
        self.network = patch.object(web.requests.sessions.Session, "request", side_effect=AssertionError("Unexpected network access"))
        self.network.start()
        self.addCleanup(self.network.stop)
        self.client = web.app.test_client()

    def test_settings_page_is_local_only_and_locked_down(self):
        response = self.client.get("/settings")
        self.assertEqual(response.status_code, 200)
        self.assertIn("AI settings", response.get_data(as_text=True))
        self.assertIn("script-src 'nonce-", response.headers["Content-Security-Policy"])
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(self.client.get("/settings", headers={"Host": "evil.example:5013"}).status_code, 403)
        with patch.dict(os.environ, {"LADDER_ALLOWED_HOSTS": "ladder.lan"}):
            self.assertEqual(self.client.get("/settings", headers={"Host": "ladder.lan:5013"}).status_code, 200)

    def test_save_and_read_back_without_the_key(self):
        response = self.client.post("/settings/ai", json={"mode": "byok", "provider": "anthropic", "key": "sk-ant-abcd9999"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["keys"], {"anthropic": "saved key ending 9999"})
        body = self.client.get("/settings/ai").get_data(as_text=True)
        self.assertNotIn("sk-ant-abcd", body)

    def test_changes_need_json_from_this_site(self):
        self.assertEqual(self.client.post("/settings/ai", data={"mode": "off"}).status_code, 403)
        self.assertEqual(self.client.post("/settings/ai", json={"mode": "off"}, headers={"Origin": "https://evil.example"}).status_code, 403)
        self.assertEqual(self.client.post("/settings/ai", json={"mode": "off"}, headers={"Host": "evil.example"}).status_code, 403)
        self.assertEqual(self.client.post("/reader/tidy", json={"html": MESSY}, headers={"Host": "evil.example"}).status_code, 403)
        self.assertEqual(self.client.post("/settings/ai", json={"mode": "local", "ollama_url": "ftp://x"}).status_code, 400)

    def test_models_and_test_routes(self):
        with patch.object(web.ai_settings, "list_models", return_value=[{"id": "m", "size": 1.0}]) as listing:
            response = self.client.post("/settings/ai/models", json={"mode": "local", "list": "local"})
        self.assertEqual(response.get_json(), {"models": [{"id": "m", "size": 1.0}]})
        self.assertEqual(listing.call_args.args[0]["list"], "local")
        draft = {"mode": "local", "ollama_url": "http://localhost:11434", "ollama_model": "qwen3.5:4b"}
        with patch.object(web.tidy.requests, "post", return_value=chat_reply("OK")) as post:
            response = self.client.post("/settings/ai/test", json=draft)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["reply"], "OK")
        self.assertEqual(post.call_args.args[0], "http://localhost:11434/v1/chat/completions")
        self.assertEqual(self.client.post("/settings/ai/test", json={"mode": "off"}).status_code, 502)

    def test_home_page_links_to_settings(self):
        self.assertIn('href="/settings"', self.client.get("/").get_data(as_text=True))


class FunPassTests(unittest.TestCase):
    """The climbing loading screen, the home page's small surprises and the friendly error page."""

    def setUp(self):
        self.env = patch.dict(os.environ, {"LOCALE": "en"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.network = patch.object(web.requests.sessions.Session, "request", side_effect=AssertionError("Unexpected network access"))
        self.network.start()
        self.addCleanup(self.network.stop)
        web.page_cache.clear()
        web.load_strings_for_locale.cache_clear()
        self.addCleanup(web.load_strings_for_locale.cache_clear)
        self.client = web.app.test_client()
        self.strings = web.load_strings()

    def test_english_locale_file_matches_the_defaults(self):
        path = Path(__file__).resolve().parents[1] / "app" / "locales" / "en.json"
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), web._DEFAULT_STRINGS)

    def test_step_ids_are_unchanged_and_lines_stay_clear(self):
        steps = web.build_steps(self.strings)
        self.assertEqual(
            [step["id"] for step in steps],
            ["connect", "fetch", "detect", "fallback_freedium", "fallback_org", "fallback_ph", "process", "cleanup", "done"],
        )
        self.assertIn("archive.org", self.strings["step_fallback_org"])
        self.assertIn("archive.today", self.strings["step_fallback_ph"])
        self.assertIn("9,001", self.strings["step_done"])

    def test_home_has_the_climbing_scene(self):
        body = self.client.get("/").get_data(as_text=True)
        self.assertIn('id="scene"', body)
        self.assertIn('class="climber"', body)
        self.assertIn('id="altitude"', body)
        # Screen readers hear the current step, not the drawing or the spinning counter.
        self.assertIn('id="status-now" role="status" aria-live="polite"', body)
        self.assertIn('<svg class="scene-art" viewBox="0 0 360 420" aria-hidden="true"', body)
        self.assertIn('id="error-area" role="alert"', body)
        self.assertIn("prefers-reduced-motion", body)
        self.assertIn(self.strings["step_fallback_org"], web.html_lib.unescape(json.loads(
            re.search(r"const STEPS = (\[.*?\]);", body).group(1))[4]["label"]))

    def test_home_has_tagline_welcome_sample_and_feet(self):
        body = self.client.get("/").get_data(as_text=True)
        taglines = self.strings["taglines"]
        self.assertTrue(8 <= len(taglines) <= 15)
        self.assertEqual(len(set(taglines)), len(taglines))
        self.assertIn('<span id="tagline">' + taglines[0] + "</span>", body)
        self.assertIn('id="welcome" hidden', body)
        self.assertIn('id="sample-link" href="/reader/https://en.wikipedia.org/wiki/Ladder"', body)
        self.assertIn(">Try a sample<", body)
        self.assertIn('id="feet" hidden', body)
        milestones = self.strings["feet_milestones"]
        self.assertEqual(milestones, sorted(milestones))
        # The surprises are extras: every use of saved state goes through the guarded helper.
        self.assertEqual(body.count("localStorage."), 2)
        self.assertIn("try { return localStorage.getItem(key); } catch (e) { return null; }", body)
        self.assertIn("try { localStorage.setItem(key, value); } catch (e) {}", body)

    def test_home_keeps_form_bookmarklet_and_settings_link(self):
        body = self.client.get("/").get_data(as_text=True)
        for needle in ('id="url-form"', 'id="link"', 'id="reader-mode"', 'class="bookmarklet-link"', 'href="/settings"'):
            self.assertIn(needle, body)

    def test_scene_works_under_a_strict_policy(self):
        svg = web.climb.scene_svg()
        self.assertNotIn("style=", svg)
        self.assertNotIn("<script", svg)
        self.assertNotRegex(svg, r"\son\w+=")
        self.assertEqual(svg.count("<svg"), 1)
        self.assertTrue(svg.endswith("</svg>"))

    def test_climber_stays_on_the_ladder(self):
        bottom, top = web.climb.climber_position(0), web.climb.climber_position(1)
        self.assertEqual(web.climb.climber_position(-3), bottom)
        self.assertEqual(web.climb.climber_position(7), top)
        self.assertLess(top[1], bottom[1])
        self.assertGreater(top[0], bottom[0])
        self.assertIn('transform="translate(%s %s)"' % web.climb.climber_position(0.45), web.climb.scene_svg(0.45))

    def test_error_page_is_friendly_and_locked_down(self):
        with patch.object(web, "bypass_paywall", side_effect=web.requests.exceptions.ConnectionError()):
            response = self.client.get("/" + URL)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.mimetype, "text/html")
        body = response.get_data(as_text=True)
        self.assertEqual(error_page_message(response), self.strings["connection_error"])
        self.assertIn('class="scene is-error"', body)
        self.assertIn('<a class="btn" href="/">Try another link</a>', body)
        self.assertIn("<b>400 ft</b>", body)
        policy = response.headers["Content-Security-Policy"]
        nonce = re.search(r"script-src 'nonce-([\w-]+)'", policy).group(1)
        self.assertIn("default-src 'none'", policy)
        self.assertIn(f"style-src 'nonce-{nonce}'", policy)
        self.assertNotIn("unsafe", policy)
        self.assertIn("frame-ancestors 'none'", policy)
        self.assertEqual(body.count(f'nonce="{nonce}"'), 2)
        self.assertEqual(len(re.findall(r"<(?:script|style)\b", body)), 2)
        self.assertNotIn("style=", body)
        self.assertNotIn("http://", body.replace("http://www.w3.org/2000/svg", ""))
        self.assertNotIn("https://", body)
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")

    def test_error_page_escapes_the_message(self):
        with patch.object(web, "bypass_paywall", side_effect=web.UserFacingError('<script>alert(1)</script> "quoted"')):
            response = self.client.get("/" + URL)
        self.assertNotIn("<script>alert(1)", response.get_data(as_text=True))
        self.assertEqual(error_page_message(response), '<script>alert(1)</script> "quoted"')

    def test_every_article_route_uses_the_error_page(self):
        invalid = self.strings["invalid_url"]
        self.assertEqual(error_page_message(self.client.get("/not-a-link")), invalid)
        self.assertEqual(error_page_message(self.client.post("/reader", data={"url": URL})), invalid)
        with patch.object(web, "bypass_paywall", side_effect=RuntimeError("private details")):
            for response in (self.client.get("/" + URL), self.client.get("/reader/" + URL), self.client.post("/article", data={"link": URL})):
                self.assertEqual(response.status_code, 500)
                self.assertEqual(error_page_message(response), self.strings["unexpected_error"])
                self.assertNotIn("private details", response.get_data(as_text=True))

    def test_other_languages_get_the_new_lines_in_english(self):
        with patch.dict(os.environ, {"LOCALE": "de"}):
            web.load_strings_for_locale.cache_clear()
            body = self.client.get("/").get_data(as_text=True)
        self.assertIn('lang="de"', body)
        self.assertIn("Website-Link eingeben", body)
        self.assertIn(">Try a sample<", body)


class PublicWordingTests(unittest.TestCase):
    """The project never advertises getting around paywalls or similar."""
    BANNED = re.compile(
        r"bypass|paywall|circumvent|without (paying|subscribing)|remove\W+.{0,20}wall"
        r"|umgehung|contournement|回避|우회|绕过|обход|saihesbidea",
        re.I,
    )
    ROOT = Path(__file__).resolve().parents[1]

    def assertClean(self, label, text):
        match = self.BANNED.search(text)
        self.assertIsNone(match, f"{label} contains {match.group(0)!r}" if match else "")

    def test_interface_text_in_every_language(self):
        self.assertClean("default UI strings", json.dumps(web._DEFAULT_STRINGS))
        for path in sorted((self.ROOT / "app" / "locales").glob("*.json")):
            self.assertClean(path.name, path.read_text(encoding="utf-8"))

    def test_pages_readme_and_image_labels(self):
        self.assertClean("home page template", web.html)
        self.assertClean("error page template", web.climb.ERROR_TEMPLATE)
        self.assertClean("reader template", web.reader.READER_TEMPLATE)
        self.assertClean("settings page", web.settings_page.SETTINGS_TEMPLATE)
        for name in ("README.md", "Dockerfile", "docker-compose.yaml"):
            self.assertClean(name, (self.ROOT / name).read_text(encoding="utf-8"))


CSS_URL = "https://web.archive.org/web/20261117063225cs_/https://en.wikipedia.org/w/load.php?modules=site.styles"
RELAYED_CSS = """@font-face { font-family: Serif; src: url(/web/20261117063225im_/https://fonts.example.com/serif.woff2) format('woff2'); }
.icon { mask-image: url("https://web.archive.org/web/20261117063225im_/https://en.wikipedia.org/icons/eye.svg"); }
@import "/web/20261117063225cs_/https://en.wikipedia.org/more.css";
.logo { background: url(data:image/png;base64,AAAA); }"""


def upstream_response(body, content_type="text/css", url=CSS_URL, status=200):
    response = Mock(status_code=status, url=url, encoding="utf-8", headers={"Content-Type": content_type})
    response.iter_content.return_value = [body]
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    return response


class RelayTests(unittest.TestCase):
    def setUp(self):
        self.network = patch.object(web.requests.sessions.Session, "request", side_effect=AssertionError("Unexpected network access"))
        self.network.start()
        self.addCleanup(self.network.stop)
        web.page_cache.clear()
        self.addCleanup(web.page_cache.clear)
        self.client = web.app.test_client()

    def test_relay_rewrites_css_and_adds_cors(self):
        with patch.object(web.requests, "get", return_value=upstream_response(RELAYED_CSS.encode())) as get:
            response = self.client.get("/_relay/" + CSS_URL)
        get.assert_called_once()
        self.assertEqual(get.call_args.args[0], CSS_URL)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Access-Control-Allow-Origin"], "*")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertTrue(response.content_type.startswith("text/css"))
        css = response.get_data(as_text=True)
        self.assertIn("url(/_relay/https://web.archive.org/web/20261117063225im_/https://fonts.example.com/serif.woff2)", css)
        self.assertIn('url("/_relay/https://web.archive.org/web/20261117063225im_/https://en.wikipedia.org/icons/eye.svg")', css)
        self.assertIn('@import "/_relay/https://web.archive.org/web/20261117063225cs_/https://en.wikipedia.org/more.css"', css)
        self.assertIn("url(data:image/png;base64,AAAA)", css)

    def test_relay_passes_fonts_through(self):
        font_url = "https://web.archive.org/web/20261117063225im_/https://fonts.example.com/serif.woff2"
        with patch.object(web.requests, "get", return_value=upstream_response(b"wOF2", "font/woff2", font_url)):
            response = self.client.get("/_relay/" + font_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b"wOF2")
        self.assertEqual(response.headers["Access-Control-Allow-Origin"], "*")

    def test_relay_only_serves_archive_org_snapshots(self):
        with patch.object(web.requests, "get") as get:
            self.assertEqual(self.client.get("/_relay/https://example.com/style.css").status_code, 404)
            self.assertEqual(self.client.get("/_relay/https://web.archive.org/save/https://x.com").status_code, 404)
        get.assert_not_called()

    def test_relay_refuses_pages_and_off_site_redirects(self):
        with patch.object(web.requests, "get", return_value=upstream_response(b"<html>", "text/html")):
            self.assertEqual(self.client.get("/_relay/" + CSS_URL).status_code, 404)
        with patch.object(web.requests, "get", return_value=upstream_response(b"x", "text/css", "https://evil.example/x.css")):
            self.assertEqual(self.client.get("/_relay/" + CSS_URL).status_code, 404)

    def test_relay_limits_size(self):
        big = upstream_response(b"", "image/png")
        big.iter_content.return_value = [b"x" * (web.RELAY_MAX_BYTES // 2 + 1)] * 2
        with patch.object(web.requests, "get", return_value=big):
            self.assertEqual(self.client.get("/_relay/" + CSS_URL).status_code, 413)

    def test_served_pages_point_relay_at_this_server(self):
        page = f'<html><head><link rel="stylesheet" href="{web.RELAY_TOKEN}{CSS_URL}"></head><body>Hi</body></html>'
        web.page_cache[URL] = {"html": page, "timestamp": web.time.time()}
        body = self.client.get("/" + URL).get_data(as_text=True)
        self.assertNotIn(web.RELAY_TOKEN, body)
        self.assertIn(f'href="http://localhost/_relay/{CSS_URL}"', body)

    def test_relay_target_url(self):
        self.assertEqual(web.relay_target_url("http://h/_relay/" + CSS_URL), CSS_URL)
        collapsed = CSS_URL.replace("https://", "https:/")
        self.assertEqual(web.relay_target_url("http://h/_relay/" + collapsed), CSS_URL)
        self.assertIsNone(web.relay_target_url("http://h/_relay/https://example.com/a.css"))


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.network = patch.object(web.requests.sessions.Session, "request", side_effect=AssertionError("Unexpected network access"))
        self.network.start()
        self.addCleanup(self.network.stop)
        web.page_cache.clear()
        self.addCleanup(web.page_cache.clear)
        self.client = web.app.test_client()

    def test_extracts_metadata_and_body(self):
        article = web.reader.extract_article(READER_PAGE, URL)
        self.assertEqual(article["title"], "How tall can a ladder get?")
        self.assertEqual(article["site"], "Example Times")
        self.assertEqual(article["author"], "Pat Writer")
        self.assertEqual(article["published"], "November 20, 2026")
        self.assertIn("vertical or inclined set of rungs", article["content"])
        self.assertNotIn("Sports", article["content"])
        self.assertNotIn("Copyright", article["content"])

    def test_sanitizes_and_resolves_content(self):
        content = web.reader.extract_article(READER_PAGE, URL)["content"]
        self.assertNotIn("<script", content)
        self.assertNotIn("onclick", content)
        self.assertNotIn("javascript:", content)
        self.assertIn('src="https://example.com/news/photos/ladder.jpg"', content)
        self.assertIn('href="https://example.com/more"', content)
        self.assertNotIn("<h1", content)

    def test_archived_images_load_live_first(self):
        archived = "https://web.archive.org/web/20261118081412id_/https://img.example.com/a.jpg"
        page = READER_PAGE.replace('data-src="photos/ladder.jpg"', f'data-src="{archived}" srcset="{archived} 1x, {archived}?w=2 2x"')
        content = web.reader.extract_article(page, URL)["content"]
        self.assertIn('src="https://img.example.com/a.jpg"', content)
        self.assertIn(f'data-fallback-src="{archived}"', content)
        self.assertIn('srcset="https://img.example.com/a.jpg 1x, https://img.example.com/a.jpg?w=2 2x"', content)

    def test_wiki_edit_links_are_removed(self):
        fragment = web.reader.clean_fragment(
            '<p><h2>Safety</h2><span><span>[</span><a href="https://en.wikipedia.org/w/index.php?title=Ladder&action=edit&section=6">edit</a><span>]</span></span></p>'
            '<p>Read the <a href="https://example.com/edit-guide">edit guide</a>.</p>', "https://en.wikipedia.org/wiki/")
        text = " ".join(fragment.get_text(" ").split())
        self.assertEqual(text, "Safety Read the edit guide .")

    def test_jump_links_within_the_article_become_plain_text(self):
        base = "https://example.com/news/"
        fragment = web.reader.clean_fragment(
            f'<p>Fact<a href="{base}#cite-1">[1]</a> and <a href="#top">top</a> and <a href="/other#part">other</a>.</p>', base)
        links = fragment.find_all("a")
        self.assertNotIn("href", links[0].attrs)
        self.assertNotIn("href", links[1].attrs)
        self.assertEqual(links[2]["href"], "https://example.com/other#part")

    def test_short_pages_report_no_article(self):
        article = web.reader.extract_article("<html><body><p>Hi.</p></body></html>", URL)
        self.assertEqual(article["content"], "")

    def test_reader_route_uses_cache_and_sets_csp(self):
        web.page_cache[URL] = {"html": READER_PAGE, "timestamp": web.time.time()}
        with patch.object(web, "bypass_paywall") as fetch:
            response = self.client.get("/reader/" + URL)
        fetch.assert_not_called()
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("How tall can a ladder get?", body)
        self.assertIn("Pat Writer", body)
        self.assertIn(f'href="/{URL}"', body)
        self.assertIn("script-src 'nonce-", response.headers["Content-Security-Policy"])

    def test_reader_route_fetches_and_caches(self):
        with patch.object(web, "bypass_paywall", return_value=READER_PAGE) as fetch:
            self.client.get("/reader/example.com/news/article")
            self.client.get("/reader/https://example.com/news/article")
        fetch.assert_called_once()
        self.assertIn(URL, web.page_cache)

    def test_reader_route_reports_fetch_errors(self):
        with patch.object(web, "bypass_paywall", side_effect=web.requests.exceptions.Timeout()):
            response = self.client.get("/reader/" + URL)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(error_page_message(response), web.load_strings()["timeout_error"])

    def test_reader_accepts_page_from_bookmarklet(self):
        response = self.client.post("/reader", data={"html": READER_PAGE, "url": URL})
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("How tall can a ladder get?", body)
        # The page came from the person's own browser: no ladder "Original page" link.
        self.assertNotIn(">Original page<", body)
        self.assertIn("script-src 'nonce-", response.headers["Content-Security-Policy"])

    def test_reader_post_handles_large_pages(self):
        big = READER_PAGE.replace("</article>", "<p>" + "padding " * 150000 + "</p></article>")
        self.assertGreater(len(big), 1_000_000)
        response = self.client.post("/reader", data={"html": big, "url": URL}, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 200)

    def test_reader_post_rejects_bad_input(self):
        self.assertEqual(self.client.post("/reader", data={"html": READER_PAGE, "url": "file:///etc/passwd"}).status_code, 400)
        self.assertEqual(self.client.post("/reader", data={"url": URL}).status_code, 400)

    def test_home_page_has_banner_and_bookmarklet(self):
        body = self.client.get("/").get_data(as_text=True)
        self.assertIn('src="/banner.webp"', body)
        self.assertIn("javascript:(function(){", body)
        self.assertIn("http://localhost/reader", body)
        banner = self.client.get("/banner.webp")
        self.assertEqual(banner.status_code, 200)
        self.assertEqual(banner.mimetype, "image/webp")

    def test_readme_bookmarklet_matches_app(self):
        readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
        self.assertIn(web.reader_bookmarklet("http://localhost:5013/"), readme)

    def test_reader_target_url(self):
        self.assertEqual(web.reader_target_url("http://h:1/reader/https://a.com/b?c=1"), "https://a.com/b?c=1")
        self.assertEqual(web.reader_target_url("http://h:1/reader/https:/a.com/b"), "https://a.com/b")
        self.assertEqual(web.reader_target_url("http://h:1/reader/a.com"), "https://a.com")
        self.assertIsNone(web.reader_target_url("http://h:1/https://a.com"))


LIBRARY_PAGE = """<!DOCTYPE html>
<html><head><title>Ladders &amp; you</title>
<base href="/news/">
<meta http-equiv="Content-Security-Policy" content="img-src https://cdn.example.com">
<meta charset="iso-8859-1">
<link rel="stylesheet" href="/static/site.css" integrity="sha384-abc" crossorigin="anonymous">
<link rel="preload" as="font" href="https://cdn.example.com/fonts/head.woff2">
<link rel="canonical" href="/news/article">
<style>@import "more.css"; .hero { background: url(img/bg.png); } .x { background: url("data:image/png;base64,AAAA"); }</style>
<script src="app.js"></script>
</head><body>
<article><h1>Ladders &amp; you</h1>
<p>""" + "A long paragraph about very tall ladders. " * 20 + """</p>
<img src="pic.jpg" srcset="pic.jpg 1x, pic@2x.jpg 2x" alt="A ladder">
<img src="data:image/gif;base64,R0lGOD" data-src="lazy.jpg" alt="">
<video poster="poster.jpg" src="movie.mp4"></video>
<svg><use href="sprite.svg#icon"></use></svg>
<p style="background-image: url('/img/dots.png')">More about ladders.</p>
<a href="/other">Another article</a> <a href="#top">Top</a>
<img src="https://intranet.example/secret.png" alt="">
<img src="missing.png" alt="">
<img src="notanimage.png" alt="">
</article></body></html>"""


def library_asset(body, content_type, status=200, headers=None):
    return {"body": body, "type": content_type, "status": status, "headers": headers or {}}


LIBRARY_FILES = {
    "https://example.com/static/site.css": library_asset(
        b'@charset "utf-8"; @font-face { src: url(../fonts/body.woff2) format("woff2"); } h1 { background: url(data:image/png;base64,AAAA); }',
        "text/css"),
    "https://example.com/fonts/body.woff2": library_asset(b"wOF2body", "font/woff2"),
    "https://cdn.example.com/fonts/head.woff2": library_asset(b"wOF2head", "application/octet-stream"),
    "https://example.com/news/more.css": library_asset(b".more { mask-image: url(icons/eye.svg); }", "text/css; charset=utf-8"),
    "https://example.com/news/icons/eye.svg": library_asset(b"<svg xmlns='http://www.w3.org/2000/svg'/>", "image/svg+xml"),
    "https://example.com/news/img/bg.png": library_asset(b"\x89PNGbg", "image/png"),
    "https://example.com/news/app.js": library_asset(b"console.log('hi');", "application/javascript"),
    "https://example.com/news/pic.jpg": library_asset(b"\xff\xd8pic", "image/jpeg"),
    "https://example.com/news/pic@2x.jpg": library_asset(b"\xff\xd8pic2", "image/jpeg"),
    "https://example.com/news/lazy.jpg": library_asset(b"\xff\xd8lazy", "image/jpeg"),
    "https://example.com/news/poster.jpg": library_asset(b"\xff\xd8poster", "image/jpeg"),
    "https://example.com/news/sprite.svg": library_asset(b"<svg xmlns='http://www.w3.org/2000/svg'/>", "image/svg+xml"),
    "https://example.com/img/dots.png": library_asset(b"\x89PNGdots", "image/png"),
    "https://example.com/news/notanimage.png": library_asset(b"<html>Not found</html>", "text/html"),
}


class FakeSession:
    """Stands in for library.make_session(): serves LIBRARY_FILES, 404 otherwise."""

    def __init__(self, files=None):
        self.files = dict(LIBRARY_FILES if files is None else files)
        self.calls = []

    def get(self, url, headers=None, **kwargs):
        self.calls.append(url)
        entry = self.files.get(url) or library_asset(b"gone", "text/html", status=404)
        response = Mock(status_code=entry["status"], headers=dict({"Content-Type": entry["type"]}, **entry["headers"]))
        response.iter_content.return_value = [entry["body"]]
        return response


REAL_MAKE_SESSION = web.library.make_session


def fake_resolve(host, port):
    if re.match(r"^[\d.]+$", host):
        return {host}
    return {"10.0.0.5"} if host.startswith("intranet") else {"93.184.216.34"}


class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"LOCALE": "en"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.network = patch.object(web.requests.sessions.Session, "request", side_effect=AssertionError("Unexpected network access"))
        self.network.start()
        self.addCleanup(self.network.stop)
        resolve = patch.object(web.library, "_resolve", side_effect=fake_resolve)
        resolve.start()
        self.addCleanup(resolve.stop)
        self.session = FakeSession()
        sessions = patch.object(web.library, "make_session", side_effect=lambda: self.session)
        sessions.start()
        self.addCleanup(sessions.stop)
        self.clear()
        self.addCleanup(self.clear)
        web.page_cache.clear()
        self.addCleanup(web.page_cache.clear)
        web.load_strings_for_locale.cache_clear()
        self.client = web.app.test_client()

    def clear(self):
        import shutil
        shutil.rmtree(web.library.library_dir(), ignore_errors=True)

    def csrf(self, response):
        return re.search(r'name="csrf" value="([^"]+)"', response.get_data(as_text=True)).group(1)

    def keep(self, page=LIBRARY_PAGE):
        web.page_cache[web.normalize_cache_url(URL)] = {"html": page, "timestamp": web.time.time()}
        confirm = self.client.get("/library/save", query_string={"url": URL})
        response = self.client.post("/library/save", data={"url": URL, "csrf": self.csrf(confirm)})
        self.assertEqual(response.status_code, 303, response.get_data(as_text=True))
        return response.headers["Location"].split("saved=", 1)[1]

    def folder(self, item_id):
        return Path(web.library.library_dir()) / item_id

    # --- the snapshot itself ---

    def test_copy_has_every_asset_with_local_addresses(self):
        meta = web.library.save(LIBRARY_PAGE, URL, "9001ft", session=self.session)
        folder = self.folder(meta["id"])
        page = (folder / "index.html").read_text(encoding="utf-8")

        def local(url):
            return "assets/" + hashlib.sha256(url.encode()).hexdigest()[:20]

        self.assertNotIn("<base", page)
        self.assertNotIn("Content-Security-Policy", page)
        self.assertNotIn("integrity", page)
        self.assertNotIn("iso-8859-1", page)
        self.assertIn('<meta charset="utf-8"/>', page)
        self.assertIn('href="' + local("https://example.com/static/site.css") + '.css"', page)
        self.assertIn('href="' + local("https://cdn.example.com/fonts/head.woff2") + '.woff2"', page)
        self.assertIn('@import "' + local("https://example.com/news/more.css") + '.css"', page)
        self.assertIn("url(" + local("https://example.com/news/img/bg.png") + ".png)", page)
        self.assertIn('src="' + local("https://example.com/news/app.js") + '.js"', page)
        self.assertIn(local("https://example.com/news/pic.jpg") + ".jpg 1x, " + local("https://example.com/news/pic@2x.jpg") + ".jpg 2x", page)
        self.assertIn('data-src="' + local("https://example.com/news/lazy.jpg") + '.jpg"', page)
        self.assertIn('poster="' + local("https://example.com/news/poster.jpg") + '.jpg"', page)
        self.assertIn('href="' + local("https://example.com/news/sprite.svg") + '.svg#icon"', page)
        self.assertIn("url('" + local("https://example.com/img/dots.png") + ".png')", page)
        # Not downloaded, but still pointing at the live site from inside the copy.
        self.assertIn('src="https://example.com/news/movie.mp4"', page)
        self.assertIn('href="https://example.com/other"', page)
        self.assertIn('href="https://example.com/news/article"', page)
        self.assertIn('href="#top"', page)
        self.assertIn("data:image/png;base64,AAAA", page)
        # Stylesheets are rewritten too, pointing at files beside them.
        css = (folder / (local("https://example.com/static/site.css") + ".css")).read_text(encoding="utf-8")
        self.assertIn("url(" + local("https://example.com/fonts/body.woff2")[len("assets/"):] + ".woff2)", css)
        self.assertNotIn("@charset", css)
        more = (folder / (local("https://example.com/news/more.css") + ".css")).read_text(encoding="utf-8")
        self.assertIn(local("https://example.com/news/icons/eye.svg")[len("assets/"):] + ".svg", more)
        self.assertEqual((folder / (local("https://example.com/fonts/body.woff2") + ".woff2")).read_bytes(), b"wOF2body")
        # Everything is private to this user.
        for path in folder.rglob("*"):
            if path.is_file():
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600, path)
        self.assertEqual(stat.S_IMODE(folder.stat().st_mode), 0o700)

    def test_copy_records_what_was_skipped_and_why(self):
        meta = web.library.save(LIBRARY_PAGE, URL, "9001ft", session=self.session)
        reasons = {skip["url"]: skip["reason"] for skip in meta["skipped"]}
        self.assertEqual(reasons["https://intranet.example/secret.png"], "on a private or local network")
        self.assertEqual(reasons["https://example.com/news/missing.png"], "the site answered 404")
        self.assertEqual(reasons["https://example.com/news/notanimage.png"], "expected an image, got a web page")
        self.assertNotIn("https://intranet.example/secret.png", self.session.calls)
        self.assertEqual(meta["files"], 13)
        self.assertEqual(meta["title"], "Ladders & you")
        self.assertEqual(meta["site"], "example.com")
        self.assertTrue(meta["has_text"])
        self.assertGreater(meta["size"], len(LIBRARY_PAGE))
        saved = json.loads((self.folder(meta["id"]) / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["skipped"], meta["skipped"])

    def test_reader_version_uses_saved_images(self):
        meta = web.library.save(LIBRARY_PAGE, URL, "9001ft", session=self.session)
        article = json.loads((self.folder(meta["id"]) / "reader.json").read_text(encoding="utf-8"))
        self.assertEqual(article["title"], "Ladders & you")
        self.assertIn("very tall ladders", article["content"])
        self.assertRegex(article["content"], r'src="assets/[0-9a-f]{20}\.jpg"')
        self.assertNotIn("https://example.com/news/pic.jpg", article["content"])

    def test_redirects_to_local_addresses_are_refused(self):
        files = {
            "https://example.com/a.png": library_asset(b"", "text/html", status=302, headers={"Location": "http://127.0.0.1:5013/settings"}),
            "https://example.com/b.png": library_asset(b"", "text/html", status=301, headers={"Location": "/c.png"}),
            "https://example.com/c.png": library_asset(b"\x89PNG", "image/png"),
        }
        session = FakeSession(files)
        meta = web.library.save('<img src="a.png"><img src="b.png">', "https://example.com/", "browser", session=session)
        self.assertEqual(meta["skipped"], [{"url": "https://example.com/a.png", "reason": "on a private or local network"}])
        self.assertNotIn("http://127.0.0.1:5013/settings", session.calls)
        self.assertIn("https://example.com/c.png", session.calls)
        self.assertEqual(meta["files"], 1)

    def test_missing_archived_files_fall_back_to_the_live_copy(self):
        archived = "https://web.archive.org/web/2026id_/https://example.com/photo.jpg"
        session = FakeSession({"https://example.com/photo.jpg": library_asset(b"\xff\xd8live", "image/jpeg")})
        page = ('<html><head><link rel="icon" href="/favicon.ico" type="image/png"></head>'
                '<body><img src="' + archived + '"></body></html>')
        meta = web.library.save(page, "https://web.archive.org/web/2026id_/https://example.com/a", "9001ft", session=session)
        self.assertEqual(session.calls, [archived, "https://example.com/photo.jpg"])
        self.assertEqual(meta["skipped"], [])
        index = (self.folder(meta["id"]) / "index.html").read_text(encoding="utf-8")
        self.assertRegex(index, r'<img src="assets/[0-9a-f]{20}\.jpg"/>')
        # 9001ft's own tab icon isn't looked for on archive.org, and still shows.
        self.assertEqual(index.count("/favicon.ico"), 1)

    def test_size_limits(self):
        big = {"https://example.com/big.png": library_asset(b"x" * 2048, "image/png"),
               "https://example.com/huge.png": library_asset(b"", "image/png", headers={"Content-Length": "999999999"})}
        with patch.object(web.library, "MAX_ASSET_BYTES", 1024):
            meta = web.library.save('<img src="big.png"><img src="huge.png">', "https://example.com/", "browser", session=FakeSession(big))
        self.assertEqual({s["reason"] for s in meta["skipped"]}, {"too large"})
        with patch.object(web.library, "MAX_TOTAL_BYTES", 2000):
            meta = web.library.save('<img src="big.png">', "https://example.com/", "browser", session=FakeSession(big))
        self.assertEqual(meta["skipped"][0]["reason"], "the copy reached its size limit")
        with patch.object(web.library, "MAX_TOTAL_BYTES", 10):
            with self.assertRaises(web.library.FetchError):
                web.library.save("<p>" + "x" * 100 + "</p>", "https://example.com/", "browser", session=FakeSession({}))
        self.assertEqual(len(web.library.items()), 2)  # the page that was too large left nothing behind
        self.assertEqual(sorted(os.listdir(web.library.library_dir())), sorted(m["id"] for m in web.library.items()))

    def test_public_address_checks(self):
        for ip in ("93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"):
            self.assertTrue(web.library.is_public_address(ip), ip)
        for ip in ("127.0.0.1", "10.1.2.3", "192.168.1.10", "172.16.0.1", "169.254.169.254", "100.64.0.1",
                   "0.0.0.0", "::1", "fe80::1%en0", "fd00::1", "::ffff:127.0.0.1", "64:ff9b::a00:1",
                   "2002:7f00:1::", "224.0.0.1", "not an ip", ""):
            self.assertFalse(web.library.is_public_address(ip), ip)
        for url in ("ftp://example.com/a", "file:///etc/passwd", "http://user:pw@example.com/", "https://intranet.example/",
                    "http://127.0.0.1/", "http://example.com:99999/"):
            with self.assertRaises(web.library.BlockedAddress, msg=url):
                web.library.check_address(url)
        web.library.check_address("https://example.com/a.png")

    def test_connections_to_local_addresses_are_refused_after_connecting(self):
        # Even if a name passed check_address and then resolved somewhere local.
        class Base:
            def __init__(self, peer):
                self.sock = Mock()
                self.sock.getpeername.return_value = (peer, 443)

            def _new_conn(self):
                return self.sock

        class Connection(web.library._PublicOnly, Base):
            pass

        local = Connection("127.0.0.1")
        with self.assertRaises(web.library.BlockedAddress):
            local._new_conn()
        local.sock.close.assert_called_once()
        public = Connection("93.184.216.34")
        self.assertIs(public._new_conn(), public.sock)

    def test_session_uses_the_public_only_connections(self):
        session = REAL_MAKE_SESSION()
        self.assertFalse(session.trust_env)  # no proxy in between
        for scheme, pool in (("http", web.library._PublicHTTPPool), ("https", web.library._PublicHTTPSPool)):
            adapter = session.get_adapter(scheme + "://example.com/")
            self.assertIsInstance(adapter, web.library.PublicOnlyAdapter)
            self.assertIs(adapter.poolmanager.pool_classes_by_scheme[scheme], pool)
        self.assertTrue(issubclass(web.library._PublicHTTPSPool.ConnectionCls, web.library._PublicOnly))

    # --- routes ---

    def test_keep_a_fetched_page_after_confirming(self):
        page = LIBRARY_PAGE.replace('href="/static/site.css"', 'href="' + web.RELAY_TOKEN + 'https://web.archive.org/web/2026cs_/https://example.com/static/site.css"')
        self.session.files["https://web.archive.org/web/2026cs_/https://example.com/static/site.css"] = library_asset(b"h1{}", "text/css")
        web.page_cache[web.normalize_cache_url(URL)] = {"html": page, "timestamp": web.time.time()}
        confirm = self.client.get("/library/save", query_string={"url": URL})
        self.assertEqual(confirm.status_code, 200)
        body = confirm.get_data(as_text=True)
        self.assertIn("Keep a copy?", body)
        self.assertIn("Ladders &amp; you", body)
        self.assertIn('action="/library/save"', body)
        self.assertIn("script-src 'nonce-", confirm.headers["Content-Security-Policy"])
        # With "no-referrer", browsers would post the form with "Origin: null".
        self.assertEqual(confirm.headers["Referrer-Policy"], "same-origin")
        self.assertNotIn("no-referrer", body)
        self.assertIn("SameSite=Strict", confirm.headers["Set-Cookie"])
        self.assertIn("HttpOnly", confirm.headers["Set-Cookie"])
        self.assertEqual(web.library.items(), [])  # asking saves nothing

        response = self.client.post("/library/save", data={"url": URL, "csrf": self.csrf(confirm)})
        self.assertEqual(response.status_code, 303)
        item_id = response.headers["Location"].split("saved=", 1)[1]
        self.assertRegex(item_id, r"^[0-9a-f]{24}$")
        # The relay placeholder was turned back into the real archive.org address.
        self.assertIn("https://web.archive.org/web/2026cs_/https://example.com/static/site.css", self.session.calls)
        self.assertFalse(any(web.RELAY_TOKEN in call for call in self.session.calls))
        meta = web.library.get(item_id)
        self.assertEqual(meta["url"], URL)
        self.assertEqual(meta["source"], "9001ft")

        listing = self.client.get(response.headers["Location"]).get_data(as_text=True)
        self.assertIn("Saved to your library.", listing)
        self.assertIn("Ladders &amp; you", listing)
        self.assertIn('href="/library/' + item_id + '/"', listing)
        self.assertIn('href="/library/' + item_id + '/reader"', listing)
        self.assertIn('href="' + URL + '"', listing)
        self.assertIn('action="/library/' + item_id + '/delete"', listing)
        self.assertIn("Files that couldn&#39;t be saved: 3", listing)

    def test_saving_needs_this_sites_form(self):
        web.page_cache[web.normalize_cache_url(URL)] = {"html": LIBRARY_PAGE, "timestamp": web.time.time()}
        token = self.csrf(self.client.get("/library/save", query_string={"url": URL}))
        attempts = [
            ({"url": URL}, {}),
            ({"url": URL, "csrf": "x" * 43}, {}),
            ({"url": URL, "csrf": token}, {"Origin": "https://evil.example"}),
            ({"url": URL, "csrf": token}, {"Origin": "null"}),  # a sandboxed page, such as a kept copy
        ]
        for data, headers in attempts:
            with self.subTest(data=data, headers=headers):
                self.assertEqual(self.client.post("/library/save", data=data, headers=headers).status_code, 403)
        fresh = web.app.test_client()  # another site's form: no cookie
        self.assertEqual(fresh.post("/library/save", data={"url": URL, "csrf": token}).status_code, 403)
        self.assertEqual(web.library.items(), [])
        self.assertEqual(self.session.calls, [])

    def test_private_addresses_cannot_be_kept(self):
        confirm = self.client.get("/library/save", query_string={"url": "http://intranet.example/wiki"})
        with patch.object(web, "bypass_paywall") as fetch:
            response = self.client.post("/library/save", data={"url": "http://intranet.example/wiki", "csrf": self.csrf(confirm)})
        self.assertEqual(response.status_code, 400)
        self.assertIn("own network", response.get_data(as_text=True))
        fetch.assert_not_called()

    def test_fetch_errors_show_on_the_confirmation_page(self):
        confirm = self.client.get("/library/save", query_string={"url": URL})
        with patch.object(web, "bypass_paywall", side_effect=web.UserFacingError("No copy anywhere")):
            response = self.client.post("/library/save", data={"url": URL, "csrf": self.csrf(confirm)})
        self.assertEqual(response.status_code, 502)
        body = response.get_data(as_text=True)
        self.assertIn("No copy anywhere", body)
        self.assertIn('name="url" value="' + URL + '"', body)  # one click to try again
        self.assertEqual(self.client.get("/library/save", query_string={"url": ""}).status_code, 400)

    def test_bookmarklet_page_waits_for_confirmation(self):
        response = self.client.post("/library/keep", data={"html": LIBRARY_PAGE, "url": URL}, headers={"Origin": "https://example.com"})
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Keep a copy?", body)
        self.assertIn("Ladders &amp; you", body)
        draft = re.search(r'name="draft" value="([0-9a-f]{32})"', body).group(1)
        self.assertEqual(web.library.items(), [])

        saved = self.client.post("/library/save", data={"draft": draft, "csrf": self.csrf(response)})
        self.assertEqual(saved.status_code, 303)
        item = web.library.items()[0]
        self.assertEqual(item["source"], "browser")
        self.assertEqual(item["url"], URL)
        self.assertIsNone(web.library.load_draft(draft))
        again = self.client.post("/library/save", data={"draft": draft, "csrf": self.csrf(response)})
        self.assertEqual(again.status_code, 404)

    def test_bookmarklet_post_rejects_bad_input(self):
        self.assertEqual(self.client.post("/library/keep", data={"html": LIBRARY_PAGE, "url": "file:///etc/passwd"}).status_code, 400)
        self.assertEqual(self.client.post("/library/keep", data={"url": URL}).status_code, 400)
        self.assertEqual(self.client.get("/library/save", query_string={"draft": "0" * 32}).status_code, 404)
        self.assertIsNone(web.library.load_draft("../../settings"))

    def test_drafts_are_capped(self):
        tokens = [web.library.save_draft("<p>%d</p>" % n, URL) for n in range(web.library.MAX_DRAFTS + 3)]
        waiting = [t for t in tokens if web.library.load_draft(t)]
        self.assertLessEqual(len(waiting), web.library.MAX_DRAFTS)
        self.assertIn(tokens[-1], waiting)

    def test_keep_buttons_on_fetched_pages_and_reader_view(self):
        web.page_cache[URL] = {"html": ARTICLE, "timestamp": web.time.time()}
        quoted = "https%3A%2F%2Fexample.com%2Fnews%2Farticle"
        page = self.client.get("/" + URL).get_data(as_text=True)
        self.assertIn('id="ladder-keep-btn" href="http://localhost/library/save?url=' + quoted + '"', page)
        reader_page = self.client.get("/reader/" + URL).get_data(as_text=True)
        self.assertIn('href="/library/save?url=' + quoted + '"', reader_page)
        # A page your own browser sent waits as a draft for the Keep a copy button.
        sent = self.client.post("/reader", data={"html": READER_PAGE, "url": URL}).get_data(as_text=True)
        draft = re.search(r'href="/library/save\?draft=([0-9a-f]{32})"', sent).group(1)
        self.assertEqual(web.library.load_draft(draft), (READER_PAGE, URL))

    def test_copies_are_served_sandboxed_and_only_their_own_files(self):
        item_id = self.keep()
        page = self.client.get("/library/" + item_id + "/")
        self.assertEqual(page.status_code, 200)
        self.assertTrue(page.content_type.startswith("text/html"))
        self.assertEqual(page.headers["Content-Security-Policy"], web.FETCHED_PAGE_CSP)
        self.assertNotIn("allow-same-origin", page.headers["Content-Security-Policy"])
        self.assertEqual(page.headers["X-Content-Type-Options"], "nosniff")
        self.assertNotIn("Access-Control-Allow-Origin", page.headers)
        self.assertIn("very tall ladders", page.get_data(as_text=True))
        asset = re.search(r'src="(assets/[0-9a-f]{20}\.js)"', page.get_data(as_text=True)).group(1)
        script = self.client.get("/library/" + item_id + "/" + asset)
        self.assertEqual(script.status_code, 200)
        self.assertTrue(script.content_type.startswith("text/javascript"))
        self.assertEqual(script.headers["Access-Control-Allow-Origin"], "*")
        self.assertEqual(script.headers["Content-Security-Policy"], web.FETCHED_PAGE_CSP)
        self.assertEqual(script.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(self.client.get("/library/" + item_id).status_code, 308)
        for path in ("meta.json", "reader.json", "assets/", "assets/../meta.json", "../" + item_id + "/meta.json",
                     "assets/%2e%2e/meta.json", "index.html/../meta.json"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get("/library/" + item_id + "/" + path).status_code, 404)
        for bad_id in (item_id.upper(), item_id[:-1], "0" * 24, ".drafts"):
            with self.subTest(bad_id=bad_id):
                self.assertEqual(self.client.get("/library/" + bad_id + "/index.html", follow_redirects=True).status_code, 404)

    def test_reader_version_of_a_copy(self):
        item_id = self.keep()
        response = self.client.get("/library/" + item_id + "/reader")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Ladders &amp; you", body)
        self.assertIn("very tall ladders", body)
        self.assertRegex(body, r'src="assets/[0-9a-f]{20}\.jpg"')
        self.assertIn('href="/library/' + item_id + '/"', body)
        self.assertNotIn('id="keep-copy"', body)
        self.assertIn("script-src 'nonce-", response.headers["Content-Security-Policy"])
        self.assertEqual(self.client.get("/library/" + "0" * 24 + "/reader").status_code, 404)

    def test_delete_needs_this_sites_form(self):
        item_id = self.keep()
        listing = self.client.get("/library")
        self.assertEqual(self.client.post("/library/" + item_id + "/delete", data={}).status_code, 403)
        self.assertEqual(self.client.post("/library/" + item_id + "/delete", data={"csrf": self.csrf(listing)},
                                          headers={"Origin": "null"}).status_code, 403)
        self.assertTrue(self.folder(item_id).is_dir())
        response = self.client.post("/library/" + item_id + "/delete", data={"csrf": self.csrf(listing)})
        self.assertEqual(response.status_code, 303)
        self.assertFalse(self.folder(item_id).exists())
        self.assertIn("Deleted.", self.client.get(response.headers["Location"]).get_data(as_text=True))
        self.assertEqual(self.client.post("/library/" + item_id + "/delete", data={"csrf": self.csrf(listing)}).status_code, 404)

    def test_library_page(self):
        response = self.client.get("/library")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("Nothing here yet", body)
        self.assertIn("Keep a copy in 9001ft", body)
        self.assertIn(html_escape(web.keep_bookmarklet("http://localhost/")), body)
        self.assertIn("http://localhost/library/keep", body)
        csp = response.headers["Content-Security-Policy"]
        self.assertIn("script-src 'nonce-", csp)
        self.assertNotIn("'unsafe-inline'", csp.split("script-src", 1)[1].split(";")[0])
        self.assertIn('href="/library"', self.client.get("/").get_data(as_text=True))

    def test_readme_and_wording(self):
        readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
        self.assertIn(web.keep_bookmarklet("http://localhost:5013/"), readme)
        for label, text in (("library page", web.library_page.LIBRARY_TEMPLATE),
                            ("confirmation page", web.library_page.CONFIRM_TEMPLATE)):
            match = PublicWordingTests.BANNED.search(text)
            self.assertIsNone(match, label)

    def test_english_locale_has_every_library_string(self):
        english = json.loads((Path(__file__).resolve().parents[1] / "app" / "locales" / "en.json").read_text(encoding="utf-8"))
        for key, value in web._DEFAULT_STRINGS.items():
            if key == "keep_button" or key.startswith("library_"):
                self.assertEqual(english.get(key), value, key)


def html_escape(text):
    from markupsafe import escape  # what Jinja uses
    return str(escape(text))

EXPORT_HTML = """<h2>Rungs &amp; rails</h2>
<p>A <b>ladder</b> is <i>tall</i>. See <a href="https://example.com/a b">the guide</a> and <code>step_count</code>.<br>Next line.</p>
<p># not a heading and 1. not a list</p>
<ul><li>Wood<ul><li>Oak</li></ul></li><li>Metal</li></ul>
<ol start="3"><li>Third</li><li>Fourth</li></ol>
<blockquote><p>Climb carefully.</p></blockquote>
<pre><code>height = rungs * 0.3
print(height)</code></pre>
<figure><img src="https://example.com/ladder.jpg" alt="A tall ladder"><figcaption>Very tall</figcaption></figure>
<table><tr><th>Type</th><th>Feet</th></tr><tr><td>Step</td><td>6</td></tr></table>
<p><div>Block inside a paragraph</div></p>
<p onclick="steal()">Bad <script>alert(1)</script><a href="javascript:alert(2)">link</a> \x07end</p>"""

EXPORT_META = {"title": "Ünïcode: How tall / can it get?", "url": URL, "site": "Example Times",
               "byline": "Pat Writer", "date": "November 20, 2026", "lang": "en"}


class ExportTests(unittest.TestCase):
    def fragment(self, html=EXPORT_HTML):
        return web.reader.clean_fragment(html, URL)

    def test_markdown_conversion(self):
        md = web.export.to_markdown(EXPORT_META, self.fragment())
        self.assertTrue(md.startswith("# Ünïcode: How tall / can it get?\n\n"))
        self.assertIn(f"Source: [Example Times]({URL})", md)
        self.assertIn("By: Pat Writer", md)
        self.assertIn("Published: November 20, 2026", md)
        self.assertIn("## Rungs & rails", md)
        self.assertIn("A **ladder** is *tall*. See [the guide](https://example.com/a%20b) and `step_count`.  \nNext line.", md)
        self.assertIn("\\# not a heading", md)
        self.assertIn("- Wood\n  - Oak\n- Metal", md)
        self.assertIn("3. Third\n4. Fourth", md)
        self.assertIn("> Climb carefully.", md)
        self.assertIn("```\nheight = rungs * 0.3\nprint(height)\n```", md)
        self.assertIn("![A tall ladder](https://example.com/ladder.jpg)", md)
        self.assertIn("| Type | Feet |\n| --- | --- |\n| Step | 6 |", md)
        self.assertIn("Block inside a paragraph", md)
        self.assertNotIn("alert", md)
        self.assertNotIn("javascript", md)

    def test_markdown_escapes_text_that_looks_like_markup(self):
        md = web.export.to_markdown({**EXPORT_META, "url": ""}, self.fragment(
            "<p>1. First</p><p>Use *stars* and [brackets] and <span>&lt;tags&gt;</span>_</p>"))
        self.assertIn("1\\. First", md)
        self.assertIn("Use \\*stars\\* and \\[brackets\\] and \\<tags\\>\\_", md)
        self.assertNotIn("Source:", md)

    def test_epub_structure_and_validity(self):
        import io
        import zipfile
        from xml.etree import ElementTree
        data = web.export.to_epub(EXPORT_META, self.fragment(), now=web.export.datetime(2026, 11, 25, 12, 0, 0))
        book = zipfile.ZipFile(io.BytesIO(data))
        first = book.infolist()[0]
        self.assertEqual(first.filename, "mimetype")
        self.assertEqual(first.compress_type, zipfile.ZIP_STORED)
        self.assertEqual(first.extra, b"")
        self.assertEqual(book.read("mimetype"), b"application/epub+zip")
        self.assertEqual(data[30:38], b"mimetype")  # right after the first local header
        for name in book.namelist():
            if name.endswith((".xml", ".opf", ".xhtml")):
                ElementTree.fromstring(book.read(name))  # well-formed XML
        container = ElementTree.fromstring(book.read("META-INF/container.xml"))
        rootfile = container.find(".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile")
        self.assertEqual(rootfile.get("full-path"), "EPUB/package.opf")

        ns = {"opf": "http://www.idpf.org/2007/opf", "dc": "http://purl.org/dc/elements/1.1/"}
        opf = ElementTree.fromstring(book.read("EPUB/package.opf"))
        self.assertEqual(opf.get("version"), "3.0")
        uid = opf.find(f".//dc:identifier[@id='{opf.get('unique-identifier')}']", ns)
        self.assertTrue(uid.text.startswith("urn:uuid:"))
        self.assertEqual(opf.find(".//opf:meta[@property='dcterms:modified']", ns).text, "2026-11-25T12:00:00Z")
        self.assertEqual(opf.find(".//dc:title", ns).text, EXPORT_META["title"])
        self.assertEqual(opf.find(".//dc:date", ns).text, "2026-11-20")
        self.assertEqual(opf.find(".//opf:item[@properties='nav']", ns).get("href"), "nav.xhtml")
        manifest = {item.get("href") for item in opf.findall(".//opf:item", ns)}
        self.assertEqual(manifest, {"nav.xhtml", "chapter.xhtml", "style.css"})
        self.assertEqual([i.get("idref") for i in opf.findall(".//opf:itemref", ns)], ["chapter"])

        nav = book.read("EPUB/nav.xhtml").decode()
        self.assertIn('epub:type="toc"', nav)
        self.assertIn('href="chapter.xhtml#h1">Rungs &amp; rails</a>', nav)

        chapter = book.read("EPUB/chapter.xhtml").decode()
        self.assertIn("<h1>Ünïcode: How tall / can it get?</h1>", chapter)
        self.assertIn('<a href="https://example.com/a%20b">the guide</a>', chapter)
        self.assertIn("<br/>", chapter)
        self.assertIn('<a href="https://example.com/ladder.jpg">[Image: A tall ladder]</a>', chapter)
        self.assertIn("Images aren't included", chapter)
        self.assertIn("<div><div>Block inside a paragraph</div></div>", chapter)
        self.assertNotIn("<img", chapter)
        self.assertNotIn("script", chapter)
        self.assertNotIn("javascript", chapter)
        self.assertNotIn("\x07", chapter)

    def test_epub_without_images_has_no_note(self):
        import io
        import zipfile
        data = web.export.to_epub({**EXPORT_META, "lang": "not a language!"}, self.fragment("<p>Just words.</p>"))
        book = zipfile.ZipFile(io.BytesIO(data))
        chapter = book.read("EPUB/chapter.xhtml").decode()
        self.assertNotIn("Images aren't included", chapter)
        self.assertIn('xml:lang="en"', chapter)

    def test_safe_filenames(self):
        self.assertEqual(web.export.safe_filename(EXPORT_META["title"], "md"), "unicode-how-tall-can-it-get.md")
        self.assertEqual(web.export.safe_filename("日本語のタイトル", "epub"), "article.epub")
        self.assertEqual(web.export.safe_filename('a"b\r\nc;d', "md"), "a-b-c-d.md")
        self.assertLessEqual(len(web.export.safe_filename("word " * 100, "epub")), 85)


class ExportRouteTests(unittest.TestCase):
    def setUp(self):
        self.network = patch.object(web.requests.sessions.Session, "request", side_effect=AssertionError("Unexpected network access"))
        self.network.start()
        self.addCleanup(self.network.stop)
        web.page_cache.clear()
        self.addCleanup(web.page_cache.clear)
        self.client = web.app.test_client()

    def post(self, payload, **kwargs):
        return self.client.post("/reader/export", json=payload, **kwargs)

    def test_markdown_download(self):
        response = self.post({"format": "markdown", "html": EXPORT_HTML, **EXPORT_META})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Content-Type"], "text/markdown; charset=utf-8")
        self.assertEqual(response.headers["Content-Disposition"], 'attachment; filename="unicode-how-tall-can-it-get.md"')
        body = response.get_data(as_text=True)
        self.assertIn("## Rungs & rails", body)
        self.assertNotIn("alert", body)

    def test_epub_download(self):
        import io
        import zipfile
        response = self.post({"format": "epub", "html": EXPORT_HTML, **EXPORT_META})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/epub+zip")
        self.assertTrue(response.headers["Content-Disposition"].endswith('.epub"'))
        self.assertEqual(zipfile.ZipFile(io.BytesIO(response.data)).namelist()[0], "mimetype")

    def test_exports_what_the_page_sends_after_resanitizing(self):
        tidied = '<h2>Added heading</h2><p>Tidied <img src="x" onerror="alert(1)"> text.</p>'
        body = self.post({"format": "markdown", "html": tidied, "title": "T", "url": URL}).get_data(as_text=True)
        self.assertIn("## Added heading", body)
        self.assertIn("![](https://example.com/news/x)", body)
        self.assertNotIn("onerror", body)

    def test_refuses_bad_requests(self):
        self.assertEqual(self.post({"format": "markdown", "html": "<p>x</p>"}, headers={"Origin": "https://evil.example"}).status_code, 403)
        self.assertEqual(self.client.post("/reader/export", data={"format": "markdown", "html": "<p>x</p>"}).status_code, 403)
        # Export uses no keys, so unlike the AI settings it works under any hostname.
        self.assertEqual(self.post({"format": "markdown", "html": "<p>x</p>"}, base_url="http://ladder.example.com").status_code, 200)
        self.assertEqual(self.post({"format": "pdf", "html": "<p>x</p>"}).status_code, 400)
        self.assertEqual(self.post({"format": "epub", "html": "  "}).status_code, 400)
        self.assertEqual(self.post(["not", "an", "object"]).status_code, 400)
        self.assertEqual(self.post({"format": ["epub"], "html": "<p>x</p>"}).status_code, 400)
        with patch.object(web, "EXPORT_MAX_HTML", 10):
            self.assertEqual(self.post({"format": "epub", "html": "<p>far too long</p>"}).status_code, 413)

    def test_bad_metadata_is_ignored(self):
        response = self.post({"format": "markdown", "html": "<p>x</p>", "title": ["no"], "url": "javascript:alert(1)",
                              "byline": 5, "lang": "<script>"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Content-Disposition"], 'attachment; filename="article.md"')
        self.assertNotIn("javascript", response.get_data(as_text=True))


class ReaderExtrasPageTests(unittest.TestCase):
    def setUp(self):
        self.network = patch.object(web.requests.sessions.Session, "request", side_effect=AssertionError("Unexpected network access"))
        self.network.start()
        self.addCleanup(self.network.stop)
        web.page_cache.clear()
        self.addCleanup(web.page_cache.clear)
        self.client = web.app.test_client()

    def check_page(self, response):
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        csp = response.headers["Content-Security-Policy"]
        nonce = re.search(r"'nonce-([^']+)'", csp).group(1)
        self.assertNotIn("unsafe-inline", csp.split("script-src")[1].split(";")[0])
        self.assertIn("connect-src 'self'", csp)
        # Every script carries the nonce; no inline handlers.
        # Every script carries the page's nonce (CSP allows nothing else).
        self.assertEqual(body.count("<script"), len(re.findall(r'<script\b[^>]*\bnonce="' + re.escape(nonce) + '"', body)))
        self.assertNotRegex(body, r"<[^>]+\son[a-z]+=")
        self.assertIn('id="listen"', body)
        self.assertIn('aria-controls="listen-panel"', body)
        self.assertIn('id="listen-status" role="status"', body)
        self.assertIn('data-export="markdown"', body)
        self.assertIn('data-export="epub"', body)
        self.assertIn('data-export="print"', body)
        self.assertIn("window.print()", body)
        self.assertIn('fetch("/reader/export"', body)
        self.assertIn("@media print", body)
        self.assertIn("prefers-reduced-motion", body)
        return body

    def test_reader_page_has_read_aloud_and_export(self):
        web.page_cache[URL] = {"html": READER_PAGE.replace("<html>", '<html lang="en-GB">'), "timestamp": web.time.time()}
        body = self.check_page(self.client.get("/reader/" + URL))
        self.assertIn('<article lang="en-GB">', body)
        self.assertIn('"url": "https://example.com/news/article"', body)

    def test_bookmarklet_page_has_them_too(self):
        self.check_page(self.client.post("/reader", data={"html": READER_PAGE, "url": URL}))

    def test_no_extras_without_an_article(self):
        response = self.client.post("/reader", data={"html": "<html><body><p>Hi.</p></body></html>", "url": URL})
        body = response.get_data(as_text=True)
        self.assertNotIn('id="listen"', body)
        self.assertNotIn('id="export"', body)

    def test_article_language_is_validated(self):
        page = READER_PAGE.replace("<html>", '<html lang="x onmouseover=alert(1)">')
        self.assertEqual(web.reader.extract_article(page, URL)["lang"], "")
        self.assertEqual(web.reader.extract_article(READER_PAGE.replace("<html>", '<html lang="pt-BR">'), URL)["lang"], "pt-BR")

    def test_new_strings_are_in_english_locale(self):
        en = json.loads((Path(__file__).resolve().parents[1] / "app" / "locales" / "en.json").read_text(encoding="utf-8"))
        for key, value in web._DEFAULT_STRINGS.items():
            if key.startswith(("listen_", "export_")):
                self.assertEqual(en.get(key), value, key)
        labels = {key[len("export_label_"):] for key in web._DEFAULT_STRINGS if key.startswith("export_label_")}
        self.assertEqual(labels, set(web.export.DEFAULT_LABELS))


def png_size(data):
    """Width and height from a PNG header, without an imaging library."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


class SelfHostingTests(unittest.TestCase):
    """Health check, installable app, share target and the optional password."""
    PASSWORD = "correct horse battery staple"

    def setUp(self):
        env = {key: value for key, value in os.environ.items()
               if key not in ("LADDER_PASSWORD", "LADDER_SECRET_KEY", "LADDER_TRUST_PROXY", "LADDER_ALLOWED_HOSTS")}
        env["LOCALE"] = "en"
        patcher = patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.network = patch.object(web.requests.sessions.Session, "request", side_effect=AssertionError("Unexpected network access"))
        self.network.start()
        self.addCleanup(self.network.stop)
        web.auth.failed_logins.clear()
        self.addCleanup(web.auth.failed_logins.clear)
        web.page_cache.clear()
        self.addCleanup(web.page_cache.clear)
        web.load_strings_for_locale.cache_clear()
        self.client = web.app.test_client()

    def with_password(self, password=None):
        patcher = patch.dict(os.environ, {"LADDER_PASSWORD": password or self.PASSWORD})
        patcher.start()
        self.addCleanup(patcher.stop)

    def log_in(self, client=None, password=None):
        return (client or self.client).post("/login", data={"password": password or self.PASSWORD})

    # --- health check -------------------------------------------------------

    def test_healthz(self):
        for password in (False, True):
            if password:
                self.with_password()
            response = self.client.get("/healthz")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json(), {"ok": True, "version": web.VERSION})
            self.assertNotIn("Set-Cookie", response.headers)

    # --- without a password nothing changes -------------------------------------

    def test_no_password_no_login_no_cookie(self):
        home = self.client.get("/")
        self.assertEqual(home.status_code, 200)
        self.assertNotIn("Set-Cookie", home.headers)
        body = home.get_data(as_text=True)
        self.assertNotIn('href="/logout"', body)
        self.assertNotIn("'token'", body)
        self.assertEqual(self.client.get("/settings").status_code, 200)
        self.assertNotIn("Log out", self.client.get("/settings").get_data(as_text=True))
        response = self.client.post("/reader", data={"html": READER_PAGE, "url": URL})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Set-Cookie", response.headers)
        login = self.client.get("/login")
        self.assertEqual((login.status_code, login.headers["Location"]), (303, "/"))
        self.assertEqual(self.client.post("/login", data={"password": "x"}).status_code, 303)
        self.assertIsNone(self.client.get_cookie("ladder_session"))

    # --- with a password -----------------------------------------------------------

    def test_password_protects_pages_and_apis(self):
        self.with_password()
        home = self.client.get("/", headers={"Accept": "text/html"})
        self.assertEqual((home.status_code, home.headers["Location"]), (303, "/login"))
        article = self.client.get("/https://example.com/a?b=1", headers={"Accept": "text/html"})
        self.assertEqual(article.status_code, 303)
        self.assertEqual(article.headers["Location"], "/login?next=/https://example.com/a%3Fb%3D1")
        for path in ("/settings/ai", "/status?url=example.com", "/reader/https://example.com/a"):
            with self.subTest(path=path):
                response = self.client.get(path, headers={"Accept": "application/json"})
                self.assertEqual(response.status_code, 401)
        self.assertEqual(self.client.post("/settings/ai", json={"mode": "off"}).status_code, 401)
        self.assertEqual(self.client.post("/reader/tidy", json={"html": "<p>x</p>"}).status_code, 401)
        self.assertEqual(self.client.post("/article", data={"link": URL}).status_code, 401)
        # The relay stays public: sandboxed pages fetch fonts and styles through it
        # without the login cookie, and it only serves archive.org files.
        with patch.object(web.requests, "get", side_effect=web.requests.exceptions.ConnectionError()):
            self.assertEqual(self.client.get("/_relay/https://web.archive.org/web/1/https://a.com/x.css").status_code, 502)

    def test_catch_all_cannot_hide_under_public_paths(self):
        self.with_password()
        with patch.object(web, "bypass_paywall", return_value=ARTICLE) as fetch:
            for path in ("/icons/example.com/article", "/static/../example.com/a", "/healthz/example.com/a",
                         "/login/example.com/a", "/sw.js/example.com/a"):
                with self.subTest(path=path):
                    self.assertIn(self.client.get(path).status_code, (303, 401, 404))
        fetch.assert_not_called()

    def test_public_files_need_no_login(self):
        self.with_password()
        for path in ("/healthz", "/login", "/manifest.webmanifest", "/icons/icon-192.png", "/sw.js",
                     "/static/app.js", "/banner.webp", "/favicon.ico", "/offline"):
            with self.subTest(path=path):
                response = self.client.get(path, headers={"Accept": "text/html"})
                self.assertEqual(response.status_code, 200)
                self.assertNotIn("Set-Cookie", response.headers)

    def test_login_flow_and_cookie_flags(self):
        self.with_password()
        page = self.client.get("/login?next=/settings")
        self.assertEqual(page.status_code, 200)
        text = page.get_data(as_text=True)
        self.assertIn('type="password"', text)
        self.assertIn('action="/login?next=/settings"', text)
        self.assertIn("#9b4dca", text)
        self.assertIn("prefers-color-scheme: dark", text)
        # no-referrer would make browsers send "Origin: null", which the login refuses.
        self.assertNotIn('content="no-referrer"', text)
        self.assertNotIn("unsafe-inline", page.headers["Content-Security-Policy"].split("script-src")[1].split(";")[0])

        wrong = self.client.post("/login", data={"password": "nope"})
        self.assertEqual(wrong.status_code, 401)
        self.assertIn("That password isn&#39;t right.", wrong.get_data(as_text=True))
        self.assertNotIn("Set-Cookie", wrong.headers)

        right = self.client.post("/login?next=/settings", data={"password": self.PASSWORD})
        self.assertEqual((right.status_code, right.headers["Location"]), (303, "/settings"))
        cookie = right.headers["Set-Cookie"]
        self.assertTrue(cookie.startswith("ladder_session="))
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertNotIn("Secure", cookie)

        home = self.client.get("/")
        self.assertEqual(home.status_code, 200)
        self.assertIn('href="/logout"', home.get_data(as_text=True))
        self.assertIn("Log out", self.client.get("/settings").get_data(as_text=True))
        self.assertEqual(self.client.get("/login").status_code, 303)

        out = self.client.get("/logout")
        self.assertEqual((out.status_code, out.headers["Location"]), (303, "/login"))
        self.assertEqual(self.client.get("/", headers={"Accept": "text/html"}).status_code, 303)

    def test_secure_cookie_on_https_and_trusted_proxy_only(self):
        self.with_password()
        https = web.app.test_client().post("/login", base_url="https://ladder.lan", data={"password": self.PASSWORD})
        self.assertIn("Secure", https.headers["Set-Cookie"])
        forwarded = {"X-Forwarded-Proto": "https"}
        untrusted = self.client.post("/login", data={"password": self.PASSWORD}, headers=forwarded)
        self.assertNotIn("Secure", untrusted.headers["Set-Cookie"])
        with patch.dict(os.environ, {"LADDER_TRUST_PROXY": "1"}):
            trusted = web.app.test_client().post("/login", data={"password": self.PASSWORD}, headers=forwarded)
        self.assertIn("Secure", trusted.headers["Set-Cookie"])

    def test_failed_logins_are_limited(self):
        self.with_password()
        here = {"REMOTE_ADDR": "192.0.2.10"}
        for _ in range(web.auth.FAILS_PER_ADDRESS):
            self.assertEqual(self.client.post("/login", data={"password": "guess"}, environ_base=here).status_code, 401)
        blocked = self.client.post("/login", data={"password": self.PASSWORD}, environ_base=here)
        self.assertEqual(blocked.status_code, 429)
        self.assertNotIn("Set-Cookie", blocked.headers)
        elsewhere = self.client.post("/login", data={"password": self.PASSWORD}, environ_base={"REMOTE_ADDR": "192.0.2.11"})
        self.assertEqual(elsewhere.status_code, 303)

    def test_login_refuses_other_sites(self):
        self.with_password()
        response = self.client.post("/login", data={"password": self.PASSWORD}, headers={"Origin": "https://evil.example"})
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("Set-Cookie", response.headers)

    def test_next_must_stay_on_this_server(self):
        for value in ("//evil.example", "/\\evil.example", "https://evil.example", "", None, "/a\nb"):
            self.assertEqual(web.auth.safe_next(value), "/")
        self.assertEqual(web.auth.safe_next("/https://example.com/a"), "/https://example.com/a")
        self.with_password()
        response = self.client.post("/login?next=//evil.example", data={"password": self.PASSWORD})
        self.assertEqual(response.headers["Location"], "/")

    def test_changing_the_password_logs_out(self):
        self.with_password()
        self.log_in()
        self.assertEqual(self.client.get("/").status_code, 200)
        self.with_password("a different password")
        self.assertEqual(self.client.get("/", headers={"Accept": "text/html"}).status_code, 303)

    def test_settings_stay_local_only_after_login(self):
        self.with_password()
        self.log_in()
        self.assertEqual(self.client.get("/settings").status_code, 200)
        lan = web.app.test_client()
        self.assertEqual(lan.post("/login", base_url="http://ladder.lan", data={"password": self.PASSWORD}).status_code, 303)
        self.assertEqual(lan.get("/", base_url="http://ladder.lan").status_code, 200)
        self.assertEqual(lan.get("/settings", base_url="http://ladder.lan").status_code, 403)
        with patch.dict(os.environ, {"LADDER_ALLOWED_HOSTS": "ladder.lan"}):
            self.assertEqual(lan.get("/settings", base_url="http://ladder.lan").status_code, 200)

    # --- the bookmarklet with a password -----------------------------------------

    def test_bookmarklet_token(self):
        self.with_password()
        token = web.auth.bookmarklet_token()
        self.assertRegex(token, r"^[0-9a-f]{40}$")
        self.log_in()
        home = self.client.get("/").get_data(as_text=True)
        self.assertIn("[&#39;url&#39;,location.href],[&#39;token&#39;,&#39;%s&#39;]]" % token, home)

        stranger = web.app.test_client()  # another site's page: no login cookie
        form = {"html": READER_PAGE, "url": URL}
        missing = stranger.post("/reader", data=form)
        self.assertEqual((missing.status_code, missing.headers["Location"]), (303, "/login"))
        self.assertEqual(stranger.post("/reader", data={**form, "token": "0" * 40}).status_code, 303)
        accepted = stranger.post("/reader", data={**form, "token": token}, content_type="multipart/form-data")
        self.assertEqual(accepted.status_code, 200)
        self.assertIn("How tall can a ladder get?", accepted.get_data(as_text=True))
        self.assertNotIn("Set-Cookie", accepted.headers)

        # Only POST /reader takes it.
        self.assertEqual(stranger.get("/?token=" + token, headers={"Accept": "text/html"}).status_code, 303)
        self.assertEqual(stranger.post("/article", data={"link": URL, "token": token}).status_code, 401)
        self.assertEqual(stranger.post("/reader/tidy", data={"token": token}).status_code, 401)

        # A new password retires old bookmarklets.
        self.with_password("a different password")
        self.assertNotEqual(web.auth.bookmarklet_token(), token)
        self.assertEqual(stranger.post("/reader", data={**form, "token": token}).status_code, 303)

    def test_no_token_without_password(self):
        self.assertIsNone(web.auth.bookmarklet_token())
        self.assertEqual(web.reader_bookmarklet("http://localhost/", None), web.reader_bookmarklet("http://localhost/"))

    # --- the signing key ---------------------------------------------------------

    def test_secret_key_saved_privately_once(self):
        folder = tempfile.mkdtemp(prefix="9001ft-key-")
        with patch.dict(os.environ, {"LADDER_DATA_DIR": folder}):
            first = web.auth.secret_key()
            path = os.path.join(folder, web.auth.SECRET_FILE)
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            web.auth._key_cache.clear()
            self.assertEqual(web.auth.secret_key(), first)
            with patch.dict(os.environ, {"LADDER_SECRET_KEY": "from-the-environment"}):
                self.assertEqual(web.auth.secret_key(), "from-the-environment")
        self.assertGreaterEqual(len(first), 32)

    # --- installable app ---------------------------------------------------------

    def test_manifest(self):
        response = self.client.get("/manifest.webmanifest")
        self.assertEqual(response.mimetype, "application/manifest+json")
        manifest = json.loads(response.get_data(as_text=True))
        self.assertEqual(manifest["name"], "9001ft Ladder")
        self.assertEqual(manifest["short_name"], "9001ft")
        self.assertEqual((manifest["start_url"], manifest["display"]), ("/", "standalone"))
        self.assertTrue(manifest["theme_color"] and manifest["background_color"])
        self.assertEqual(manifest["share_target"], {
            "action": "/share", "method": "GET", "params": {"title": "title", "text": "text", "url": "url"}})
        purposes = set()
        for icon in manifest["icons"]:
            purposes.add(icon["purpose"])
            image = self.client.get(icon["src"])
            self.assertEqual(image.status_code, 200)
            self.assertEqual(image.mimetype, "image/png")
            self.assertEqual("%dx%d" % png_size(image.data), icon["sizes"])
        self.assertEqual(purposes, {"any", "maskable"})
        self.assertIn("512x512", {icon["sizes"] for icon in manifest["icons"]})
        self.assertEqual(png_size(self.client.get("/icons/apple-touch-icon.png").data), (180, 180))
        self.assertEqual(self.client.get("/icons/nope.png").status_code, 404)

    def test_service_worker(self):
        response = self.client.get("/sw.js")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "text/javascript")
        self.assertEqual(response.headers["Service-Worker-Allowed"], "/")
        self.assertEqual(response.headers["Cache-Control"], "no-cache")
        script = response.get_data(as_text=True)
        shell = re.findall(r'"(/[^"]*)"', re.search(r"const SHELL = \[(.*?)\];", script, re.S).group(1))
        self.assertEqual(shell, web.pwa.SHELL_URLS)
        for url in shell:
            self.assertFalse(url == "/" or url.startswith(("/reader", "/settings", "/status", "/share", "/http")), url)
            self.assertEqual(self.client.get(url).status_code, 200, url)
        self.assertIn('request.method !== "GET"', script)
        self.assertIn('register("/sw.js"', self.client.get("/static/app.js").get_data(as_text=True))

    def test_pages_link_the_app_and_keep_strict_csp(self):
        tags = ('rel="manifest" href="/manifest.webmanifest"', 'name="theme-color"',
                'rel="apple-touch-icon"', 'src="/static/app.js"')
        home = self.client.get("/").get_data(as_text=True)
        reader_page = self.client.post("/reader", data={"html": READER_PAGE, "url": URL})
        settings = self.client.get("/settings")
        for name, body in (("home", home), ("reader", reader_page.get_data(as_text=True)),
                           ("settings", settings.get_data(as_text=True))):
            for tag in tags:
                self.assertIn(tag, body, name)
        for name, response in (("reader", reader_page), ("settings", settings)):
            csp = response.headers["Content-Security-Policy"]
            nonce = re.search(r"script-src 'nonce-([^']+)'", csp).group(1)
            self.assertIn('<script src="/static/app.js" defer nonce="%s">' % nonce, response.get_data(as_text=True))
            self.assertIn("worker-src 'self'", csp)
            self.assertIn("manifest-src 'self'", csp)
            self.assertEqual(csp.split("script-src")[1].split(";")[0].strip(), "'nonce-%s'" % nonce, name)
            self.assertIn("default-src 'none'", csp)

    def test_offline_page(self):
        response = self.client.get("/offline")
        self.assertEqual(response.status_code, 200)
        self.assertIn("You&#39;re offline", response.get_data(as_text=True))
        self.assertIn("script-src 'nonce-", response.headers["Content-Security-Policy"])

    # --- share target ------------------------------------------------------------

    def test_share_finds_the_link(self):
        cases = [
            ({"url": "https://example.com/a?b=1"}, "/reader/https://example.com/a?b=1"),
            ({"title": "A story", "text": "Read this: https://example.com/story. So good!"}, "/reader/https://example.com/story"),
            ({"text": "(see http://example.com/x)"}, "/reader/http://example.com/x"),
            ({"text": "https://en.wikipedia.org/wiki/Ladder_(disambiguation)"},
             "/reader/https://en.wikipedia.org/wiki/Ladder_(disambiguation)"),
            ({"url": "", "text": "look https://example.com/é"}, "/reader/https://example.com/%C3%A9"),
        ]
        for args, location in cases:
            with self.subTest(args=args):
                response = self.client.get("/share", query_string=args)
                self.assertEqual(response.status_code, 303)
                self.assertEqual(response.headers["Location"], location)

    def test_share_without_a_link(self):
        for args in ({}, {"title": "Hello", "text": "no link here"}, {"text": "javascript:alert(1)"},
                     {"url": "ftp://example.com/file"}):
            with self.subTest(args=args):
                response = self.client.get("/share", query_string=args)
                self.assertEqual(response.status_code, 200)
                self.assertIn("No link to read", response.get_data(as_text=True))

    def test_share_needs_login_with_a_password(self):
        self.with_password()
        response = self.client.get("/share?url=https://example.com/a", headers={"Accept": "text/html"})
        self.assertEqual(response.status_code, 303)
        self.assertTrue(response.headers["Location"].startswith("/login?next=/share"))
        self.log_in()
        self.assertEqual(self.client.get("/share?url=https://example.com/a").headers["Location"],
                         "/reader/https://example.com/a")

    def test_new_pages_use_public_wording(self):
        sw = (PublicWordingTests.ROOT / "app" / "static" / "sw.js").read_text(encoding="utf-8")
        for name, text in (("login page", web.auth.LOGIN_TEMPLATE), ("message page", web.pwa.MESSAGE_TEMPLATE),
                           ("service worker", sw)):
            self.assertIsNone(PublicWordingTests.BANNED.search(text), name)


class IntegrationFixTests(unittest.TestCase):
    """Fixes for how the features fit together."""

    def setUp(self):
        env = patch.dict(os.environ, no_tidy_env(), clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.network = patch.object(web.requests.sessions.Session, "request", side_effect=AssertionError("Unexpected network access"))
        self.network.start()
        self.addCleanup(self.network.stop)
        self.client = web.app.test_client()

    def test_library_is_localhost_only_like_the_settings(self):
        self.assertEqual(self.client.get("/library").status_code, 200)
        self.assertEqual(self.client.get("/library", headers={"Host": "evil.example:5013"}).status_code, 403)
        with patch.dict(os.environ, {"LADDER_ALLOWED_HOSTS": "ladder.lan"}):
            self.assertEqual(self.client.get("/library", headers={"Host": "ladder.lan"}).status_code, 200)

    def test_keep_bookmarklet_carries_the_token_with_a_password(self):
        with patch.dict(os.environ, {"LADDER_PASSWORD": "pw", "LADDER_SECRET_KEY": "k" * 32}):
            token = web.auth.bookmarklet_token()
            self.assertIn("['token','" + token + "']", web.keep_bookmarklet("http://localhost/", token))
            page = "<html><head><title>T</title></head><body><p>" + "words " * 80 + "</p></body></html>"
            # Without the token (and without a cookie), the cross-site post is sent to login.
            self.assertEqual(self.client.post("/library/keep", data={"html": page, "url": URL}).status_code, 303)
            allowed = self.client.post("/library/keep", data={"html": page, "url": URL, "token": token})
            self.assertNotEqual(allowed.headers.get("Location", ""), "/login")
            self.assertNotEqual(allowed.status_code, 401)
        self.assertNotIn("token", web.keep_bookmarklet("http://localhost/"))

    def test_kept_copy_files_load_without_the_cookie_but_the_page_needs_login(self):
        with patch.dict(os.environ, {"LADDER_PASSWORD": "pw", "LADDER_SECRET_KEY": "k" * 32}):
            with patch.object(web.library, "item_file", return_value=None):
                asset = self.client.get("/library/" + "a" * 24 + "/assets/" + "b" * 16 + ".css")
                page = self.client.get("/library/" + "a" * 24 + "/", headers={"Accept": "text/html"})
        self.assertEqual(asset.status_code, 404)  # reached the route, no login asked
        self.assertEqual(page.status_code, 303)
        self.assertTrue(page.headers["Location"].startswith("/login"))

    def test_relative_base_resolves_against_the_page(self):
        page = '<html><head><base href="/news/"></head><body><article><p>' + "Ladder words here. " * 30 + \
               '</p><img src="pic.jpg" alt="x"></article></body></html>'
        content = web.reader.extract_article(page, "https://example.com/a/b")["content"]
        self.assertIn('src="https://example.com/news/pic.jpg"', content)


if __name__ == "__main__":
    unittest.main()
