"""Library: keep copies of pages on this computer, to read later or offline.

Each copy lives in <data folder>/library/<id>/:
    index.html    the page, with every file it uses pointing at a local copy
    assets/       its stylesheets, images, fonts and scripts
    meta.json     title, site, address, when it was saved, size, what was skipped
    reader.json   the Reader view version (title, author, date, text)

The files a page uses are downloaded by this server. So that nobody can use
that to look around your own network, it only fetches http(s) addresses on the
public internet. It checks the addresses a name resolves to before asking, and
the address it actually connected to (so a name can't be switched to a local
address in between), again after every redirect. Sizes and time are capped,
and whatever can't be saved is skipped and listed.
"""

import hashlib
import html as html_lib
import ipaddress
import json
import os
import re
import secrets
import shutil
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urldefrag, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool

import ai_settings
import reader

ID_RE = re.compile(r"^[0-9a-f]{24}$")
ASSET_NAME_RE = re.compile(r"^[0-9a-f]{20}\.[a-z0-9]{2,5}$")
DRAFT_RE = re.compile(r"^[0-9a-f]{32}$")

MAX_TOTAL_BYTES = 50 * 1024 * 1024      # a whole copy, page included
MAX_ASSET_BYTES = 15 * 1024 * 1024      # any one file
MAX_ASSETS = 400
MAX_REDIRECTS = 5
FETCH_WORKERS = 8
DEADLINE_SECONDS = 120                  # for all of a page's files together
CSS_DEPTH = 4                           # stylesheets importing stylesheets
DRAFT_TTL = 3600
MAX_DRAFTS = 10

USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# Content types we keep, and the extension each is saved (and later served) as.
TYPE_EXT = {
    "text/css": "css",
    "text/javascript": "js", "application/javascript": "js", "application/x-javascript": "js",
    "application/ecmascript": "js", "text/ecmascript": "js",
    "image/png": "png", "image/jpeg": "jpg", "image/jpg": "jpg", "image/pjpeg": "jpg", "image/gif": "gif",
    "image/webp": "webp", "image/avif": "avif", "image/svg+xml": "svg", "image/x-icon": "ico",
    "image/vnd.microsoft.icon": "ico", "image/bmp": "bmp",
    "font/woff2": "woff2", "font/woff": "woff", "font/ttf": "ttf", "font/otf": "otf", "font/sfnt": "ttf",
    "application/font-woff2": "woff2", "application/font-woff": "woff", "application/x-font-woff": "woff",
    "application/x-font-ttf": "ttf", "application/x-font-truetype": "ttf", "application/font-sfnt": "ttf",
    "application/x-font-opentype": "otf", "application/vnd.ms-fontobject": "eot",
}
EXT_TYPE = {
    "html": "text/html", "css": "text/css", "js": "text/javascript",  # Flask adds charset=utf-8
    "png": "image/png", "jpg": "image/jpeg", "gif": "image/gif", "webp": "image/webp", "avif": "image/avif",
    "svg": "image/svg+xml", "ico": "image/x-icon", "bmp": "image/bmp",
    "woff2": "font/woff2", "woff": "font/woff", "ttf": "font/ttf", "otf": "font/otf",
    "eot": "application/vnd.ms-fontobject",
}
IMAGE_EXTS = {"png", "jpg", "gif", "webp", "avif", "svg", "ico", "bmp"}
FONT_EXTS = {"woff2", "woff", "ttf", "otf", "eot"}
KIND_EXTS = {
    "css": {"css"},
    "script": {"js"},
    "image": IMAGE_EXTS,
    "font": FONT_EXTS,
    "asset": IMAGE_EXTS | FONT_EXTS,           # url() in a stylesheet
}
KIND_NAMES = {"css": "a stylesheet", "script": "a script", "image": "an image", "font": "a font", "asset": "an image or font"}
GENERIC_TYPES = {"", "application/octet-stream", "binary/octet-stream", "text/plain"}
PATH_EXT_ALIASES = {"jpeg": "jpg", "mjs": "js"}

NOT_FETCHED = ("data:", "blob:", "javascript:", "vbscript:", "mailto:", "tel:", "sms:", "about:")


class FetchError(Exception):
    """A file that couldn't be saved, with a short reason for the list."""


class BlockedAddress(FetchError):
    pass


class _Full(FetchError):
    """The copy reached MAX_TOTAL_BYTES."""


WAYBACK_ASSET_RE = re.compile(r"^https?://web\.archive\.org/web/\d{1,14}[a-z]{0,2}_?/(https?://.+)$", re.I)


# --- where the library lives -------------------------------------------------

def library_dir():
    return os.path.join(ai_settings.data_dir(), "library")


def _ensure_dir(path):
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def _write(path, data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


# --- only the public internet ------------------------------------------------

def is_public_address(ip):
    """True for an address on the public internet; False for private, loopback,
    link-local, shared, reserved and multicast ones, including IPv4 addresses
    wrapped in IPv6."""
    try:
        address = ipaddress.ip_address(str(ip).split("%", 1)[0])
    except ValueError:
        return False
    if address.version == 6:
        embedded = address.ipv4_mapped or address.sixtofour or (address.teredo or (None, None))[1]
        if embedded is None and address in ipaddress.ip_network("64:ff9b::/96"):
            embedded = ipaddress.ip_address(int(address) & 0xFFFFFFFF)
        if embedded is not None:
            address = embedded
    return address.is_global and not address.is_multicast


def _resolve(host, port):
    return {info[4][0] for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)}


def check_address(url):
    """Raise BlockedAddress unless url is http(s) on a public host."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise BlockedAddress("not a web address")
    if parsed.username or parsed.password:
        raise BlockedAddress("addresses with a password aren't fetched")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        raise BlockedAddress("not a web address")
    try:
        addresses = _resolve(parsed.hostname, port)
    except (OSError, UnicodeError):
        raise FetchError("couldn't find that site")
    if not addresses or not all(is_public_address(a) for a in addresses):
        raise BlockedAddress("on a private or local network")


class _PublicOnly:
    """Refuse a connection unless the address it reached is public. Checking
    the connected socket means a name can't pass check_address and then
    resolve to a local address for the real request."""

    def _new_conn(self):
        sock = super()._new_conn()
        try:
            peer = sock.getpeername()[0]
        except (OSError, IndexError):
            peer = ""
        if not is_public_address(peer):
            sock.close()
            raise BlockedAddress("on a private or local network")
        return sock


class _PublicHTTPConnection(_PublicOnly, HTTPConnection):
    pass


class _PublicHTTPSConnection(_PublicOnly, HTTPSConnection):
    pass


class _PublicHTTPPool(HTTPConnectionPool):
    ConnectionCls = _PublicHTTPConnection


class _PublicHTTPSPool(HTTPSConnectionPool):
    ConnectionCls = _PublicHTTPSConnection


class PublicOnlyAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        super().init_poolmanager(*args, **kwargs)
        self.poolmanager.pool_classes_by_scheme = {"http": _PublicHTTPPool, "https": _PublicHTTPSPool}


def make_session():
    session = requests.Session()
    session.trust_env = False   # no proxies: the connected address must be the site's own
    adapter = PublicOnlyAdapter(pool_connections=8, pool_maxsize=FETCH_WORKERS)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9"})
    return session


def fetch_bytes(session, url, referer, max_bytes, deadline):
    """GET a public http(s) file, following redirects ourselves so each hop is
    checked. Returns (body, content_type, final_url)."""
    headers = {"Referer": referer} if referer else {}
    for _ in range(MAX_REDIRECTS + 1):
        check_address(url)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise FetchError("took too long")
        response = session.get(url, headers=headers, stream=True, allow_redirects=False,
                               timeout=(min(5.0, remaining), min(20.0, remaining)))
        try:
            location = response.headers.get("Location")
            if response.status_code in (301, 302, 303, 307, 308) and location:
                url = urljoin(url, location)
                continue
            if response.status_code != 200:
                raise FetchError(f"the site answered {response.status_code}")
            length = str(response.headers.get("Content-Length") or "")
            if length.isdigit() and int(length) > max_bytes:
                raise FetchError("too large")
            chunks, size = [], 0
            for chunk in response.iter_content(64 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    raise FetchError("too large")
                if time.monotonic() > deadline:
                    raise FetchError("took too long")
                chunks.append(chunk)
            return b"".join(chunks), response.headers.get("Content-Type") or "", url
        finally:
            response.close()
    raise FetchError("too many redirects")


def asset_extension(content_type, url, kind):
    """The extension to save a downloaded file as, or FetchError if it isn't
    the kind of file the page asked for (an error page instead of an image)."""
    base_type = content_type.split(";", 1)[0].strip().lower()
    ext = TYPE_EXT.get(base_type)
    if ext is None and base_type in GENERIC_TYPES:
        path_ext = os.path.splitext(urlparse(url).path)[1][1:].lower()
        path_ext = PATH_EXT_ALIASES.get(path_ext, path_ext)
        if path_ext in EXT_TYPE and path_ext != "html":
            ext = path_ext
    if ext is None or ext not in KIND_EXTS[kind]:
        got = "a web page" if "html" in base_type else (base_type or "an unknown type")
        raise FetchError(f"expected {KIND_NAMES[kind]}, got {got}")
    return ext


def _charset(content_type):
    match = re.search(r"charset=[\"']?([\w.:-]+)", content_type or "", re.I)
    return match.group(1) if match else None


def decode_css(body, content_type):
    charset = _charset(content_type)
    if not charset:
        match = re.match(rb'@charset\s+"([\w.:-]+)"', body)
        charset = match.group(1).decode("ascii") if match else "utf-8"
    try:
        text = body.decode(charset, errors="replace")
    except LookupError:
        text = body.decode("utf-8", errors="replace")
    # Saved and served as UTF-8 from now on.
    return re.sub(r'^﻿?@charset\s+"[^"]*"\s*;?', "", text.lstrip("﻿"))


# --- finding every file a page uses -------------------------------------------

# kind None: not downloaded (links, frames, video), only made absolute so it
# still points at the live site from inside the copy.
ATTR_KINDS = {
    "img": {"src": "image", "data-src": "image", "data-lazy-src": "image", "data-original": "image",
            "srcset": "srcset", "data-srcset": "srcset", "data-lazy-srcset": "srcset"},
    "source": {"src": "image", "srcset": "srcset", "data-srcset": "srcset"},
    "video": {"poster": "image", "src": None},
    "audio": {"src": None},
    "track": {"src": None},
    "script": {"src": "script"},
    "input": {"src": "image"},
    "image": {"href": "image", "xlink:href": "image"},
    "use": {"href": "image", "xlink:href": "image"},
    "iframe": {"src": None},
    "frame": {"src": None},
    "embed": {"src": None},
    "object": {"data": None},
    "a": {"href": None},
    "area": {"href": None},
    "form": {"action": None},
    "button": {"formaction": None},
    "blockquote": {"cite": None},
    "q": {"cite": None},
    "body": {"background": "image"},
    "table": {"background": "image"},
    "td": {"background": "image"},
}
ICON_RELS = {"icon", "shortcut", "apple-touch-icon", "apple-touch-icon-precomposed", "mask-icon"}

CSS_REF_RE = re.compile(
    r"""@import\s+(?:url\(\s*(['"]?)(.*?)\1\s*\)|(['"])(.*?)\3)|url\(\s*(['"]?)(.*?)\5\s*\)""",
    re.I | re.S,
)


def _link_kind(link):
    rel = {r.lower() for r in (link.get("rel") or [])}
    kind = (link.get("as") or "").lower()
    if "stylesheet" in rel or ("preload" in rel and kind == "style"):
        return "css"
    if "modulepreload" in rel or ("preload" in rel and kind == "script"):
        return "script"
    if "preload" in rel and kind in ("font", "image"):
        return kind
    if rel & ICON_RELS:
        return "image"
    return None


def _split_srcset(value):
    # Candidates are separated by ", "; a bare comma can be part of a CDN URL.
    return re.split(r",\s+", value.strip()) if ", " in value else value.split(",")


def _srcset(value, ref, base):
    rewritten = []
    for candidate in _split_srcset(value):
        bits = candidate.strip().split(None, 1)
        if bits:
            bits[0] = ref(bits[0], "image", base)
            rewritten.append(" ".join(bits))
    return ", ".join(rewritten)


def rewrite_css(text, base, ref):
    """Call ref(address, kind, base) for each @import and url() in a stylesheet
    and put back what it returns."""
    def replace(match):
        if match.group(2) is not None:
            quote = match.group(1)
            return f"@import url({quote}{ref(match.group(2), 'css', base)}{quote})"
        if match.group(4) is not None:
            quote = match.group(3)
            return f"@import {quote}{ref(match.group(4), 'css', base)}{quote}"
        quote = match.group(5)
        return f"url({quote}{ref(match.group(6), 'asset', base)}{quote})"
    return CSS_REF_RE.sub(replace, text)


def rewrite_html(soup, base, ref):
    """Call ref(address, kind, base) for every address in the page and put back
    what it returns."""
    for element in soup.find_all(True):
        if element.name == "link" and element.get("href"):
            kind = _link_kind(element)
            element["href"] = ref(element["href"], kind, base)
            if element.get("imagesrcset"):
                element["imagesrcset"] = _srcset(element["imagesrcset"], ref, base)
        kinds = ATTR_KINDS.get(element.name, {})
        for attr, kind in kinds.items():
            value = element.get(attr)
            if not value or not isinstance(value, str):
                continue
            if element.name == "source" and element.parent is not None and element.parent.name in ("video", "audio"):
                kind = None if kind == "image" else kind
            if kind == "srcset":
                element[attr] = _srcset(value, ref, base)
            else:
                element[attr] = ref(value, kind, base)
        if element.get("style"):
            element["style"] = rewrite_css(element["style"], base, ref)
        if element.name == "style" and element.string:
            element.string = rewrite_css(element.string, base, ref)


def _absolute(value, base):
    value = (value or "").strip()
    if not value or value.startswith("#") or value.lower().startswith(NOT_FETCHED):
        return None
    absolute = urljoin(base, value)
    return absolute if urlparse(absolute).scheme in ("http", "https") else None


class Snapshot:
    """Downloads everything one page uses into folder/assets."""

    def __init__(self, page_url, folder, session=None):
        self.page_url = page_url
        self.assets = _ensure_dir(os.path.join(folder, "assets"))
        self.session = session or make_session()
        self.deadline = time.monotonic() + DEADLINE_SECONDS
        self.files = {}         # absolute URL (without #fragment) -> saved file name
        self.css = {}           # URL -> (text, final URL), rewritten once its own files are in
        self.kinds = {}         # URL -> kind, for everything asked for
        self.skipped = []       # [{"url", "reason"}]
        self.total = 0
        self.lock = threading.Lock()

    # Recording what a page asks for, then rewriting addresses to local copies.

    def _collector(self, queue):
        def ref(value, kind, base):
            absolute = _absolute(value, base)
            if absolute and kind:
                queue.append((urldefrag(absolute)[0], kind))
            return value
        return ref

    def _localizer(self, prefix):
        def ref(value, kind, base):
            absolute = _absolute(value, base)
            if absolute is None:
                return value
            clean, fragment = urldefrag(absolute)
            name = self.files.get(clean) if kind else None
            if name:
                return prefix + name + ("#" + fragment if fragment else "")
            return absolute
        return ref

    def _skip(self, url, reason):
        with self.lock:
            self.skipped.append({"url": url, "reason": reason})

    def _save_asset(self, url, source, kind):
        """Download source and save it as the copy of url (they differ when
        an archived file falls back to the live one)."""
        body, content_type, final_url = fetch_bytes(self.session, source, self.page_url, MAX_ASSET_BYTES, self.deadline)
        ext = asset_extension(content_type, final_url, kind)
        with self.lock:
            if self.total + len(body) > MAX_TOTAL_BYTES:
                raise _Full("the copy reached its size limit")
            self.total += len(body)
        name = hashlib.sha256(url.encode("utf-8")).hexdigest()[:20] + "." + ext
        if ext == "css":
            with self.lock:
                self.css[url] = (decode_css(body, content_type), final_url)
        else:
            _write(os.path.join(self.assets, name), body)
        with self.lock:
            self.files[url] = name

    def _download(self, item):
        url, kind = item
        # archive.org didn't save every file a page used; as on the pages
        # 9001ft shows, the live copy stands in for a missing archived one.
        archived = WAYBACK_ASSET_RE.match(url)
        sources = [url, archived.group(1)] if archived else [url]
        reason = None
        for source in sources:
            try:
                self._save_asset(url, source, kind)
                return
            except (_Full, BlockedAddress) as error:
                reason = reason or str(error)
                break
            except FetchError as error:
                reason = reason or str(error)
            except requests.exceptions.Timeout:
                reason = reason or "took too long"
            except requests.exceptions.RequestException:
                reason = reason or "couldn't download it"
            except Exception:
                reason = reason or "couldn't save it"
                break
        self._skip(url, reason)

    def _fetch(self, items):
        batch = []
        for url, kind in items:
            if url in self.kinds:
                continue
            self.kinds[url] = kind
            if len(self.kinds) > MAX_ASSETS:
                self._skip(url, "the page uses too many files")
            else:
                batch.append((url, kind))
        if batch:
            with ThreadPoolExecutor(max_workers=min(FETCH_WORKERS, len(batch))) as pool:
                list(pool.map(self._download, batch))
        return [url for url, _ in batch if url in self.css]

    def run(self, soup, base):
        queue = []
        rewrite_html(soup, base, self._collector(queue))
        # Stylesheets first, a level at a time, since they name more files.
        for _ in range(CSS_DEPTH + 1):
            sheets = [item for item in queue if item[1] == "css" and item[0] not in self.kinds]
            if not sheets:
                break
            for url in self._fetch(sheets):
                text, final_url = self.css[url]
                rewrite_css(text, final_url, self._collector(queue))
        for url, kind in queue:
            if kind == "css" and url not in self.kinds:
                self.kinds[url] = kind
                self._skip(url, "stylesheets nested too deeply")
        self._fetch([item for item in queue if item[1] != "css"])

        for url, (text, final_url) in self.css.items():
            _write(os.path.join(self.assets, self.files[url]), rewrite_css(text, final_url, self._localizer("")))
        rewrite_html(soup, base, self._localizer("assets/"))


def _prepare(soup, page_url, from_ladder):
    """Find the page's base address, then take out what would stop a local
    copy from working: <base>, the page's own security policy and refresh
    redirects, integrity hashes (stylesheets are rewritten) and 9001ft's own
    floating buttons and tab icon."""
    base = page_url
    base_tag = soup.find("base", href=True)
    if base_tag:
        base = urljoin(page_url, base_tag["href"])
    for tag in soup.find_all("base"):
        tag.decompose()
    if from_ladder:
        for link in soup.find_all("link", href="/favicon.ico"):
            link.decompose()
    for tag_id in ("ladder-buttons", "ladder-reader-btn", "ladder-keep-btn"):
        for element in soup.find_all(id=tag_id):
            element.decompose()
    for meta in soup.find_all("meta"):
        equiv = (meta.get("http-equiv") or "").lower()
        if meta.get("charset") or equiv in ("content-security-policy", "refresh", "content-type"):
            meta.decompose()
    for element in soup.find_all(["script", "link"]):
        for attr in ("integrity", "crossorigin", "nonce"):
            if attr in element.attrs:
                del element[attr]
    if soup.head is None:
        head = soup.new_tag("head")
        (soup.html or soup).insert(0, head)
    soup.head.insert(0, soup.new_tag("meta", charset="utf-8"))
    return base


def _localize_reader_images(article, files, prefix, base):
    """Point the Reader version's images at the copies saved with the page, and
    make every other address absolute: the Reader version is served from this
    server, where a relative address would lead nowhere."""
    def absolute(value):
        return _absolute(value, base) or value

    def local(url):
        found = _absolute(url, base)
        name = files.get(urldefrag(found)[0]) if found else None
        return prefix + name if name else None

    fragment = BeautifulSoup(article.get("content") or "", "html.parser")
    for tag in fragment.find_all(True):
        for attr in ("href", "src", "poster", "cite", "data-fallback-src"):
            if isinstance(tag.get(attr), str):
                tag[attr] = absolute(tag[attr])
        if isinstance(tag.get("srcset"), str):
            tag["srcset"] = _srcset(tag["srcset"], lambda value, kind, _: absolute(value), base)
    for img in fragment.find_all("img"):
        found = local(img.get("src")) or local(img.get("data-fallback-src"))
        if not found:
            continue
        img["src"] = found
        candidates = [c.strip().split(None, 1) for c in _split_srcset(img.get("srcset") or "") if c.strip()]
        if candidates and all(local(bits[0]) for bits in candidates):
            img["srcset"] = ", ".join(" ".join([local(bits[0])] + bits[1:]) for bits in candidates)
        elif "srcset" in img.attrs:
            del img["srcset"]
        if "data-fallback-src" in img.attrs:
            del img["data-fallback-src"]
    article["content"] = str(fragment) if article.get("content") else ""
    if article.get("lead_image"):
        article["lead_image"] = local(article["lead_image"]) or absolute(article["lead_image"])
    return article


def page_title(page_html):
    match = re.search(r"<title[^>]*>(.*?)</title>", page_html or "", re.I | re.S)
    return re.sub(r"\s+", " ", html_lib.unescape(match.group(1))).strip()[:300] if match else ""


def _cleanup_stale(folder, prefix, max_age):
    try:
        names = os.listdir(folder)
    except OSError:
        return
    now = time.time()
    for name in names:
        path = os.path.join(folder, name)
        try:
            if name.startswith(prefix) and now - os.path.getmtime(path) > max_age:
                shutil.rmtree(path) if os.path.isdir(path) else os.remove(path)
        except OSError:
            pass


def save(page_html, url, source, session=None):
    """Keep a copy of a page. Returns its metadata (see meta.json)."""
    page_bytes = len(page_html.encode("utf-8"))
    if page_bytes > MAX_TOTAL_BYTES:
        raise FetchError("the page is too large to keep")
    root = _ensure_dir(library_dir())
    _cleanup_stale(root, ".tmp-", 3600)
    item_id = secrets.token_hex(12)
    folder = os.path.join(root, ".tmp-" + item_id)
    os.makedirs(folder, mode=0o700)
    try:
        try:
            article = reader.extract_article(page_html, url)
        except Exception:
            article = {"title": "", "site": "", "author": "", "published": "",
                       "minutes": 0, "lead_image": "", "content": ""}

        soup = BeautifulSoup(page_html, "html.parser")
        from_ladder = source == "9001ft"
        base = _prepare(soup, url, from_ladder)
        snapshot = Snapshot(url, folder, session)
        snapshot.total = page_bytes
        snapshot.run(soup, base)
        if from_ladder:
            # As on pages 9001ft shows; the copy is served from this server.
            soup.head.append(soup.new_tag("link", rel="icon", href="/favicon.ico", type="image/png"))
        _write(os.path.join(folder, "index.html"), str(soup))

        article = _localize_reader_images(dict(article), snapshot.files, "assets/", base)
        article["title"] = article.get("title") or page_title(page_html) or url
        _write(os.path.join(folder, "reader.json"), json.dumps(article))

        size = sum(os.path.getsize(os.path.join(dirpath, name))
                   for dirpath, _, names in os.walk(folder) for name in names)
        meta = {
            "id": item_id,
            "url": url,
            "title": article["title"],
            "site": article.get("site") or (urlparse(url).hostname or ""),
            "author": article.get("author") or "",
            "published": article.get("published") or "",
            "minutes": article.get("minutes") or 0,
            "has_text": bool(article.get("content")),
            "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "source": source,
            "size": size,
            "files": len(snapshot.files),
            "skipped": snapshot.skipped,
        }
        _write(os.path.join(folder, "meta.json"), json.dumps(meta, indent=1))
        os.rename(folder, os.path.join(root, item_id))
        return meta
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise


# --- reading and removing copies ---------------------------------------------

def item_folder(item_id):
    if not isinstance(item_id, str) or not ID_RE.match(item_id):
        return None
    folder = os.path.join(library_dir(), item_id)
    return folder if os.path.isdir(folder) else None


def get(item_id):
    folder = item_folder(item_id)
    return _read_json(os.path.join(folder, "meta.json")) if folder else None


def reader_article(item_id):
    folder = item_folder(item_id)
    return _read_json(os.path.join(folder, "reader.json")) if folder else None


def items():
    """Every kept copy, newest first."""
    try:
        names = os.listdir(library_dir())
    except OSError:
        return []
    found = []
    for name in names:
        if ID_RE.match(name):
            meta = get(name)
            if meta and meta.get("id") == name:
                found.append(meta)
    return sorted(found, key=lambda meta: meta.get("saved_at", ""), reverse=True)


def item_file(item_id, name):
    """(path, content type) of a file inside one copy, or None. Only the page
    and its assets/ files, by names this module writes; nothing else."""
    folder = item_folder(item_id)
    if not folder:
        return None
    if name == "index.html":
        path, ext = os.path.join(folder, "index.html"), "html"
    elif name.startswith("assets/") and ASSET_NAME_RE.match(name[len("assets/"):]):
        ext = name.rsplit(".", 1)[1]
        if ext not in EXT_TYPE or ext == "html":
            return None
        path = os.path.join(folder, "assets", name[len("assets/"):])
    else:
        return None
    return (path, EXT_TYPE[ext]) if os.path.isfile(path) else None


def delete(item_id):
    folder = item_folder(item_id)
    if not folder:
        return False
    shutil.rmtree(folder)
    return True


# --- pages waiting for "Keep a copy" to be confirmed ---------------------------
#
# A page sent by the bookmarklet (or shown in Reader view from your browser)
# waits here until you confirm, so another website posting a page can't add it
# to your library. Drafts expire after an hour.

def _drafts_dir():
    return os.path.join(library_dir(), ".drafts")


def save_draft(page_html, url):
    folder = _ensure_dir(_drafts_dir())
    _cleanup_stale(folder, "", DRAFT_TTL)
    try:
        existing = sorted((os.path.getmtime(os.path.join(folder, n)), n) for n in os.listdir(folder))
    except OSError:
        existing = []
    for _, name in existing[:max(0, len(existing) - MAX_DRAFTS + 1)]:
        try:
            os.remove(os.path.join(folder, name))
        except OSError:
            pass
    token = secrets.token_hex(16)
    _write(os.path.join(folder, token + ".json"), json.dumps({"url": url, "html": page_html}))
    return token


def load_draft(token):
    """(page_html, url) for a draft that is still waiting, or None."""
    if not isinstance(token, str) or not DRAFT_RE.match(token):
        return None
    path = os.path.join(_drafts_dir(), token + ".json")
    try:
        if time.time() - os.path.getmtime(path) > DRAFT_TTL:
            return None
    except OSError:
        return None
    data = _read_json(path)
    if not data or not isinstance(data.get("html"), str) or not isinstance(data.get("url"), str):
        return None
    return data["html"], data["url"]


def drop_draft(token):
    if isinstance(token, str) and DRAFT_RE.match(token):
        try:
            os.remove(os.path.join(_drafts_dir(), token + ".json"))
        except OSError:
            pass


# --- for the library page ------------------------------------------------------

def human_size(size):
    size = float(size or 0)
    for unit in ("bytes", "KB", "MB"):
        if size < 1024 or unit == "MB":
            return f"{size:.0f} {unit}" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1024
    return ""


def human_date(stamp):
    try:
        moment = time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return ""
    return f"{moment.tm_mday} {time.strftime('%b %Y', moment)}"
