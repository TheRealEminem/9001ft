"""Offline regression tests. Run: python -m unittest discover -s tests -v."""
import json
import os
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import portable as web

ARTICLE = "<html><head><title>Article</title></head><body><p>Useful article content.</p></body></html>"
URL = "https://example.com/news/article"


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
        self.assertIn(b"Enter Website Link", response.data)
        self.assertIn(b'<form', response.data)

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
                self.assertEqual(response.get_data(as_text=True), self.strings[message])

    def test_article_custom_error(self):
        with patch.object(web, "bypass_paywall", side_effect=web.UserFacingError("Unavailable")):
            response = self.client.post("/article", data={"link": URL})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_data(as_text=True), "Unavailable")

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
        self.assertClean("reader template", web.reader.READER_TEMPLATE)
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


if __name__ == "__main__":
    unittest.main()
