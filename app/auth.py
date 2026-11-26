"""Optional password for running 9001ft on a home server.

Without LADDER_PASSWORD nothing here does anything: no login, no cookie.
With it, every page needs a login except the few listed in PUBLIC_ENDPOINTS.

- The login lives in Flask's signed session cookie (HttpOnly, SameSite=Lax,
  Secure on https). The signing key comes from LADDER_SECRET_KEY, or is made
  once and kept in the data folder, readable only by this user.
- Changing the password logs everyone out and retires old bookmarklets,
  because both are derived from the key and the password together.
- The Send to Reader bookmarklet posts from other sites, which a Lax cookie
  doesn't follow, so it carries its own token (see bookmarklet_token).
"""
import hashlib
import hmac
import logging
import os
import secrets
import threading
import time
from collections import deque
from datetime import timedelta
from urllib.parse import quote

import flask
from flask import request
from flask.sessions import SecureCookieSessionInterface
from werkzeug.middleware.proxy_fix import ProxyFix

import ai_settings

log = logging.getLogger(__name__)

# Routes anyone may load: health check, the login itself, and what a phone
# needs to install the app before logging in. Matched by endpoint, never by
# path prefix, so the catch-all page route can't sneak in under them.
PUBLIC_ENDPOINTS = {
    "healthz", "login", "logout", "manifest", "app_icon", "service_worker",
    "offline_page", "banner", "favicon", "static", "font_file",
    # Sandboxed pages (fetched articles, kept copies) have an opaque origin, so
    # their font and stylesheet requests carry no login cookie. The relay only
    # serves archive.org files, so it's safe without a login.
    "relay",
}

# Endpoints the bookmarklets post to from other sites (no Lax cookie), which
# accept the bookmarklet token instead.
BOOKMARKLET_ENDPOINTS = {"reader_from_browser", "library_keep_from_browser"}

SECRET_FILE = "secret_key"
SESSION_LIFETIME = timedelta(days=30)

# Failed logins: a few per address, and a cap for everyone together, so
# guessing is slow even from many addresses.
FAILS_PER_ADDRESS = 5
FAILS_TOTAL = 50
FAIL_WINDOW = 15 * 60


def password():
    return os.environ.get("LADDER_PASSWORD", "")


def enabled():
    return bool(password())


def trust_proxy():
    return os.environ.get("LADDER_TRUST_PROXY", "").strip().lower() in ("1", "true", "yes", "on")


# --- the signing key ---------------------------------------------------------

_key_lock = threading.Lock()
_key_cache = {}


def secret_key():
    """LADDER_SECRET_KEY, else the key saved in the data folder (made on first
    use, mode 0600). If the folder can't be written, a key that lasts until
    the server restarts."""
    from_env = os.environ.get("LADDER_SECRET_KEY", "")
    if from_env:
        return from_env
    path = os.path.join(ai_settings.data_dir(), SECRET_FILE)
    with _key_lock:
        if path in _key_cache:
            return _key_cache[path]
        key = _read_or_create_key(path)
        _key_cache[path] = key
        return key


def _read_or_create_key(path):
    try:
        with open(path, encoding="utf-8") as handle:
            key = handle.read().strip()
        if len(key) >= 32:
            return key
    except OSError:
        pass
    key = secrets.token_urlsafe(48)
    try:
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        # O_EXCL: if another worker made one first, use theirs.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(key)
        return key
    except FileExistsError:
        with open(path, encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError as error:
        log.warning("Can't save the login key in %s (%s); logins will end when the server restarts. "
                    "Set LADDER_SECRET_KEY or LADDER_DATA_DIR to fix this.", path, error)
        return key


def _derived(purpose):
    message = (purpose + ":" + password()).encode("utf-8")
    return hmac.new(secret_key().encode("utf-8"), message, hashlib.sha256).hexdigest()[:40]


def bookmarklet_token():
    """Per-install token for the Send to Reader bookmarklet, or None when no
    password is set. Only accepted on POST /reader."""
    return _derived("bookmarklet") if enabled() else None


def _same(a, b):
    return hmac.compare_digest(hashlib.sha256(a.encode("utf-8")).digest(),
                               hashlib.sha256(b.encode("utf-8")).digest())


def password_matches(given):
    return enabled() and _same(given or "", password())


def logged_in():
    return enabled() and _same(str(flask.session.get("auth", "")), _derived("login"))


# --- session cookie ------------------------------------------------------------

class LadderSessionInterface(SecureCookieSessionInterface):
    """Flask's signed cookie session, switched on only when a password is set."""

    def get_signing_serializer(self, app):
        if not enabled():
            return None  # a null session: nothing can be stored, no cookie is sent
        app.secret_key = secret_key()
        return super().get_signing_serializer(app)

    def get_cookie_secure(self, app):
        return bool(flask.has_request_context() and request.is_secure)


class _ProxyHeaders:
    """Honour X-Forwarded-For and X-Forwarded-Proto only when LADDER_TRUST_PROXY
    is set, i.e. when 9001ft sits behind your own reverse proxy."""

    def __init__(self, wsgi_app):
        self.plain = wsgi_app
        self.proxied = ProxyFix(wsgi_app, x_for=1, x_proto=1)

    def __call__(self, environ, start_response):
        return (self.proxied if trust_proxy() else self.plain)(environ, start_response)


# --- failed-login limit ------------------------------------------------------------

class FailedLogins:
    def __init__(self):
        self.lock = threading.Lock()
        self.by_address = {}
        self.everyone = deque()

    def _prune(self, now):
        cutoff = now - FAIL_WINDOW
        while self.everyone and self.everyone[0] < cutoff:
            self.everyone.popleft()
        for address in list(self.by_address):
            times = self.by_address[address]
            while times and times[0] < cutoff:
                times.popleft()
            if not times:
                del self.by_address[address]

    def blocked(self, address):
        with self.lock:
            self._prune(time.monotonic())
            return (len(self.everyone) >= FAILS_TOTAL
                    or len(self.by_address.get(address, ())) >= FAILS_PER_ADDRESS)

    def record(self, address):
        with self.lock:
            now = time.monotonic()
            self.everyone.append(now)
            self.by_address.setdefault(address, deque()).append(now)

    def forget(self, address):
        with self.lock:
            self.by_address.pop(address, None)

    def clear(self):
        with self.lock:
            self.by_address.clear()
            self.everyone.clear()


failed_logins = FailedLogins()


# --- the gate ----------------------------------------------------------------------

def safe_next(value):
    """Only paths on this server: '/x', never '//host' or '/\\host'."""
    value = value or ""
    if (not value.startswith("/") or value.startswith("//") or value.startswith("/\\")
            or any(ord(ch) < 32 for ch in value)):
        return "/"
    return value


def _bookmarklet_post_allowed():
    if request.method != "POST" or request.endpoint not in BOOKMARKLET_ENDPOINTS:
        return False
    token = request.form.get("token", "")
    expected = bookmarklet_token()
    return bool(token and expected) and _same(token, expected)


def require_login():
    """before_request hook: None to carry on, or the response to send instead."""
    if not enabled() or request.endpoint in PUBLIC_ENDPOINTS:
        return None
    if request.endpoint == "library_file" and (request.view_args or {}).get("name") != "index.html":
        # A kept copy's own files, requested by the sandboxed copy without the
        # login cookie. They sit behind two long random names (the copy's id and
        # the file's), and the copy's page itself still needs a login.
        return None
    if logged_in() or _bookmarklet_post_allowed():
        return None
    if request.method in ("GET", "HEAD") and request.accept_mimetypes.accept_html:
        target = request.full_path[:-1] if request.full_path.endswith("?") else request.full_path
        next_path = safe_next(target)
        location = "/login" if next_path == "/" else "/login?next=" + quote(next_path, safe="/:")
        return flask.redirect(location, 303)
    if request.endpoint in BOOKMARKLET_ENDPOINTS:
        return flask.redirect("/login", 303)
    response = flask.jsonify(error="Log in first")
    response.status_code = 401
    return response


def init_app(app):
    app.session_interface = LadderSessionInterface()
    app.config.update(
        SESSION_COOKIE_NAME="ladder_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=SESSION_LIFETIME,
    )
    app.wsgi_app = _ProxyHeaders(app.wsgi_app)
    app.before_request(require_login)


def log_in():
    flask.session.clear()
    flask.session["auth"] = _derived("login")
    flask.session.permanent = True


def log_out():
    if enabled():
        flask.session.clear()


def client_address():
    return request.remote_addr or "unknown"


# --- login page --------------------------------------------------------------------

LOGIN_TEMPLATE = """<!DOCTYPE html>
<html lang="{{ lang }}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<!-- same-origin, not no-referrer: no-referrer makes browsers send "Origin: null" with the form. -->
<meta name="referrer" content="same-origin">
<meta name="robots" content="noindex">
<title>{{ login_title }} · 9001ft Ladder</title>
<link rel="icon" href="/favicon.ico" type="image/png">
<link rel="manifest" href="/manifest.webmanifest">
<meta name="theme-color" content="#fdf3dc">
<link rel="apple-touch-icon" href="/icons/apple-touch-icon.png">
<script src="/static/app.js" defer nonce="{{ nonce }}"></script>
<style>
  :root { --bg: #ffffff; --text: #23232b; --muted: #6b6b76; --rule: #d9d6e2; --card: #faf9fd; --accent: #9b4dca; --accent-hover: #8233b3; --link: #8233b3; --bad: #b42318; --bad-bg: #fff0f0; }
  @media (prefers-color-scheme: dark) {
    :root { --bg: #191a21; --text: #ececf1; --muted: #a3a3b0; --rule: #3a3b47; --card: #22232c; --accent: #7359f8; --accent-hover: #5c47c6; --link: #b9a8ff; --bad: #ffaaaa; --bad-bg: #3a1c1c; }
  }
  * { box-sizing: border-box; }
  body { margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center; background: var(--bg); color: var(--text); font: 16px/1.5 'Open Sans', -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
  main { width: 100%; max-width: 380px; padding: 32px 16px; text-align: center; }
  img { width: 112px; height: 112px; border-radius: 50%; box-shadow: 0 8px 24px rgba(0, 0, 0, 0.18); }
  h1 { font-size: 1.5rem; margin: 18px 0 4px; }
  p { margin: 0 0 20px; color: var(--muted); }
  form { background: var(--card); border: 1px solid var(--rule); border-radius: 12px; padding: 20px; text-align: left; }
  label { display: block; font-weight: 600; margin-bottom: 6px; }
  input[type=password] { display: block; width: 100%; padding: 10px 12px; font: inherit; color: var(--text); background: var(--bg); border: 1px solid var(--rule); border-radius: 6px; }
  button { margin-top: 16px; width: 100%; padding: 12px; font: inherit; font-weight: 600; text-transform: uppercase; color: #fff; background: var(--accent); border: 0; border-radius: 6px; cursor: pointer; }
  button:hover { background: var(--accent-hover); }
  input:focus-visible, button:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  .error { background: var(--bad-bg); color: var(--bad); border: 1px solid currentColor; border-radius: 8px; padding: 10px 12px; margin: 0 0 16px; }
</style>
</head>
<body>
<main>
  <img src="/icons/icon-192.png" width="112" height="112" alt="">
  <h1>9001ft Ladder</h1>
  <p>{{ login_title }}</p>
  <form method="post" action="/login{% if next_path != '/' %}?next={{ next_path | urlencode }}{% endif %}">
    {% if error %}<div class="error" role="alert">{{ error }}</div>{% endif %}
    <label for="password">{{ login_label }}</label>
    <input type="password" id="password" name="password" autocomplete="current-password" required autofocus>
    <button type="submit">{{ login_button }}</button>
  </form>
</main>
</body>
</html>
"""


import theme  # noqa: E402

LOGIN_TEMPLATE = theme.with_theme(LOGIN_TEMPLATE, auto_dark=True)
