"""Which AI service 9001ft uses for optional features such as AI tidy.

Three modes, chosen on the settings page (/settings):
    off    no AI
    local  a model on your own computer through Ollama
    byok   your own API key for a hosted provider, or any OpenAI-compatible API
           (a LiteLLM router, LM Studio, ...), optionally falling back to the
           local model when it can't be reached

The choice is saved to settings.json in the data folder (LADDER_DATA_DIR,
default: data/ next to app/), readable only by you. When there is no saved
choice, the TIDY_* environment variables (or .env.local) are used instead.
API keys are never sent back to the browser.

Each save from the page keeps the settings it replaced in
settings-history.json (also readable only by you, last HISTORY_SIZE saves), so
"Undo last save" can put them back. Undoing the first save after using
environment settings deletes settings.json, which returns to .env.local.
"""

import json
import os
import tempfile
import threading
import time
from dataclasses import dataclass

DEFAULT_OLLAMA_URL = "http://localhost:11434"

# Hosted providers. `kind` says how to talk to them: "anthropic" through the
# official SDK, "openai" through the OpenAI-compatible chat API. `models` are
# suggestions until "Load list" asks the provider; any model id is accepted.
PROVIDERS = {
    "anthropic": {"label": "Anthropic Claude", "kind": "anthropic", "key_hint": "sk-ant-…",
                  "models": ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"]},
    "openai": {"label": "OpenAI", "kind": "openai", "base": "https://api.openai.com/v1", "key_hint": "sk-…"},
    "gemini": {"label": "Google Gemini", "kind": "openai", "key_hint": "AIza…",
               "base": "https://generativelanguage.googleapis.com/v1beta/openai",
               "models": ["gemini-flash-lite-latest", "gemini-flash-latest"]},
    "openrouter": {"label": "OpenRouter (hundreds of models)", "kind": "openai",
                   "base": "https://openrouter.ai/api/v1", "key_hint": "sk-or-…"},
    "groq": {"label": "Groq", "kind": "openai", "base": "https://api.groq.com/openai/v1", "key_hint": "gsk_…"},
    "mistral": {"label": "Mistral", "kind": "openai", "base": "https://api.mistral.ai/v1"},
    "deepseek": {"label": "DeepSeek", "kind": "openai", "base": "https://api.deepseek.com/v1"},
    "xai": {"label": "xAI Grok", "kind": "openai", "base": "https://api.x.ai/v1", "key_hint": "xai-…"},
    "together": {"label": "Together AI", "kind": "openai", "base": "https://api.together.xyz/v1"},
    "custom": {"label": "Other OpenAI-compatible API (LiteLLM, LM Studio, …)", "kind": "openai"},
}

MODES = ("off", "local", "byok")
# What Reader view does with AI tidy when an AI is set up:
#   suggest  show a prominent "Tidy this up?" offer (default)
#   auto     tidy archived or messy pages automatically, with Show original
#   off      only the toolbar button
TIDY_MODES = ("suggest", "auto", "off")
REASONING = ("", "none", "low", "medium", "high")


@dataclass
class Backend:
    base: str
    model: str
    key: str = ""
    reasoning_effort: str = ""
    kind: str = "openai"   # "openai" or "anthropic"
    label: str = ""

    def __post_init__(self):
        self.label = self.label or self.model


# --- where settings live ---------------------------------------------------

def data_dir():
    default = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
    return os.path.abspath(os.path.expanduser(os.environ.get("LADDER_DATA_DIR") or default))


def settings_path():
    return os.path.join(data_dir(), "settings.json")


def load():
    """The saved settings, or None when nothing has been saved."""
    try:
        with open(settings_path(), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    ai = data.get("ai") if isinstance(data, dict) else None
    return ai if isinstance(ai, dict) and ai.get("mode") in MODES else None


def _write_private(path, data):
    """Write JSON atomically, readable only by this user."""
    folder = os.path.dirname(path)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".settings-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def save(ai, saved_at=None):
    """Write settings atomically, readable only by this user."""
    _write_private(settings_path(), {"ai": ai, "saved_at": saved_at or time.time()})


# --- undo: the settings each save replaced -----------------------------------

HISTORY_SIZE = 5
_history_lock = threading.Lock()


def history_path():
    return os.path.join(data_dir(), "settings-history.json")


def _saved_at():
    """When the current settings.json was saved (older files: its mtime)."""
    try:
        with open(settings_path(), encoding="utf-8") as handle:
            stamp = json.load(handle).get("saved_at")
        if isinstance(stamp, (int, float)):
            return float(stamp)
    except (OSError, ValueError, AttributeError):
        pass
    try:
        return os.path.getmtime(settings_path())
    except OSError:
        return None


def _read_history():
    try:
        with open(history_path(), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    versions = [v for v in data.get("versions") or [] if isinstance(v, dict) and v.get("kind") in ("settings", "env", "none")]
    redo = data.get("redo") if isinstance(data.get("redo"), dict) else None
    return {"versions": versions[-HISTORY_SIZE:], "redo": redo}


def _current_state():
    """The settings in use now, as a history entry."""
    ai = load()
    if ai is not None:
        return {"kind": "settings", "ai": ai, "saved_at": _saved_at()}
    return {"kind": "env" if _env_backends() else "none", "ai": None, "saved_at": None}


def _restore(entry):
    if entry["kind"] == "settings" and isinstance(entry.get("ai"), dict):
        save(entry["ai"], saved_at=entry.get("saved_at"))
    elif os.path.exists(settings_path()):
        os.remove(settings_path())


def commit(ai):
    """Save settings from the page, remembering the ones they replace.
    Returns False (and keeps no history entry) when nothing changed."""
    with _history_lock:
        current = _current_state()
        if current["ai"] == ai:
            return False
        history = _read_history()
        history["versions"] = (history["versions"] + [current])[-HISTORY_SIZE:]
        history["redo"] = None
        _write_private(history_path(), history)
        save(ai)
        return True


def undo(redo=False):
    """Put back the settings the last save replaced (or, with redo, the ones
    the last undo took away). Raises ValueError when there's nothing to do."""
    with _history_lock:
        history = _read_history()
        if redo:
            entry = history["redo"]
            if not entry:
                raise ValueError("nothing to redo")
            history["versions"] = (history["versions"] + [_current_state()])[-HISTORY_SIZE:]
            history["redo"] = None
        else:
            if not history["versions"]:
                raise ValueError("nothing to undo")
            entry = history["versions"].pop()
            history["redo"] = _current_state()
        _write_private(history_path(), history)
        _restore(entry)


def history_view():
    """What the page may know about the history: kinds and ages, never keys."""
    history = _read_history()
    now = time.time()

    def describe(entry):
        if not entry:
            return None
        stamp = entry.get("saved_at")
        age = max(0, int(now - stamp)) if isinstance(stamp, (int, float)) else None
        return {"kind": entry["kind"], "age": age}

    return {"undo": describe(history["versions"][-1] if history["versions"] else None),
            "undo_count": len(history["versions"]), "redo": describe(history["redo"])}


# --- turning settings into services to call ---------------------------------

def _read_key_file(path):
    try:
        with open(os.path.expanduser(path), encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def _env_backends():
    found = []
    for prefix in ("TIDY_", "TIDY_FALLBACK_"):
        base = os.environ.get(prefix + "API_BASE", "").strip()
        model = os.environ.get(prefix + "MODEL", "").strip()
        if not (base and model):
            continue
        key = os.environ.get(prefix + "API_KEY", "").strip()
        if not key and os.environ.get(prefix + "API_KEY_FILE", "").strip():
            key = _read_key_file(os.environ[prefix + "API_KEY_FILE"].strip())
        found.append(Backend(base=base.rstrip("/"), model=model, key=key,
                             reasoning_effort=os.environ.get(prefix + "REASONING_EFFORT", "").strip()))
    return found


def _saved_key(ai, provider):
    entry = (ai.get("keys") or {}).get(provider) or {}
    if entry.get("key"):
        return entry["key"]
    if entry.get("key_file"):
        return _read_key_file(entry["key_file"])
    return ""


def local_backend(ai):
    model = (ai.get("ollama_model") or "").strip()
    if not model:
        return None
    url = (ai.get("ollama_url") or DEFAULT_OLLAMA_URL).rstrip("/")
    return Backend(base=url + "/v1", model=model, reasoning_effort=ai.get("local_reasoning", "none"),
                   label=f"{model} (local)")


def hosted_backend(ai):
    provider = ai.get("provider")
    spec = PROVIDERS.get(provider)
    if not spec:
        return None
    model = ((ai.get("models") or {}).get(provider) or "").strip() or (spec.get("models") or [""])[0]
    base = (ai.get("base_url") or "").strip().rstrip("/") if provider == "custom" else spec.get("base", "")
    key = _saved_key(ai, provider)
    if not model or (spec["kind"] == "openai" and not base) or (provider != "custom" and not key):
        return None
    return Backend(base=base, model=model, key=key, reasoning_effort=ai.get("reasoning", ""),
                   kind=spec["kind"], label=model)


def backends_for(ai):
    """Services to try, in order, for a settings dict (saved or a draft)."""
    mode = ai.get("mode")
    if mode == "local":
        found = [local_backend(ai)]
    elif mode == "byok":
        found = [hosted_backend(ai)]
        if ai.get("fallback_local"):
            found.append(local_backend(ai))
    else:
        found = []
    return [backend for backend in found if backend]


def tidy_mode():
    """Reader view's AI tidy behaviour (see TIDY_MODES). Saved settings win,
    then the TIDY_MODE environment variable, then "suggest"."""
    saved = (load() or {}).get("tidy_mode")
    mode = saved or os.environ.get("TIDY_MODE", "").strip().lower() or "suggest"
    return mode if mode in TIDY_MODES else "suggest"


def backends():
    ai = load()
    return backends_for(ai) if ai is not None else _env_backends()


def source():
    if load() is not None:
        return "settings"
    return "env" if _env_backends() else "none"


# --- what the settings page sees --------------------------------------------

def _key_hint(entry):
    if entry.get("key"):
        return "saved key ending " + entry["key"][-4:]
    if entry.get("key_file"):
        return "saved key file"
    return ""


def public_view():
    """Settings for the page, with keys replaced by hints."""
    ai = load()
    if ai is None:
        ai = _settings_from_env()
    view = {k: v for k, v in ai.items() if k != "keys"}
    view["keys"] = {provider: _key_hint(entry) for provider, entry in (ai.get("keys") or {}).items() if _key_hint(entry)}
    view.setdefault("ollama_url", DEFAULT_OLLAMA_URL)
    view.setdefault("tidy_mode", tidy_mode())
    view["source"] = source()
    view.update(history_view())
    return view


def _settings_from_env():
    """Show .env.local settings in the page's terms, so saving keeps them."""
    found = _env_backends()
    if not found:
        return {"mode": "off", "provider": "anthropic", "ollama_url": DEFAULT_OLLAMA_URL, "local_reasoning": "none"}
    first, rest = found[0], found[1:]
    ai = {"mode": "byok", "provider": "custom", "base_url": first.base, "models": {"custom": first.model},
          "reasoning": first.reasoning_effort, "keys": {}, "ollama_url": DEFAULT_OLLAMA_URL, "local_reasoning": "none"}
    key_file = os.environ.get("TIDY_API_KEY_FILE", "").strip()
    if key_file:
        ai["keys"]["custom"] = {"key_file": key_file}
    elif first.key:
        ai["keys"]["custom"] = {"key": first.key}
    local = next((b for b in rest if b.base.rstrip("/").endswith(":11434/v1")), None)
    if local:
        ai.update(fallback_local=True, ollama_url=local.base[: -len("/v1")], ollama_model=local.model,
                  local_reasoning=local.reasoning_effort or "none")
    return ai


def merge_draft(draft, keep_saved_keys=True):
    """Validate a settings draft from the page. A blank key keeps the saved one;
    only "clear_key": true (exactly) removes it."""
    current = load() or _settings_from_env()
    mode = draft.get("mode") if draft.get("mode") in MODES else "off"
    provider = draft.get("provider") if draft.get("provider") in PROVIDERS else current.get("provider", "anthropic")
    reasoning = draft.get("reasoning") if draft.get("reasoning") in REASONING else ""
    local_reasoning = draft.get("local_reasoning") if draft.get("local_reasoning") in REASONING else "none"
    ollama_url = str(draft.get("ollama_url") or DEFAULT_OLLAMA_URL).strip()
    base_url = str(draft.get("base_url") or "").strip()
    for url in (ollama_url, base_url):
        if url and not url.lower().startswith(("http://", "https://")):
            raise ValueError("addresses must start with http:// or https://")

    keys = {p: dict(e) for p, e in (current.get("keys") or {}).items()} if keep_saved_keys else {}
    new_key = str(draft.get("key") or "").strip()
    if new_key:
        keys[provider] = {"key": new_key}
    elif draft.get("clear_key") is True:
        keys.pop(provider, None)

    models = dict(current.get("models") or {})
    model = str(draft.get("model") or "").strip()
    if model:
        models[provider] = model

    return {
        "mode": mode, "provider": provider, "base_url": base_url if provider == "custom" else current.get("base_url", ""),
        "keys": keys, "models": models, "reasoning": reasoning,
        "fallback_local": bool(draft.get("fallback_local")),
        "ollama_url": ollama_url, "ollama_model": str(draft.get("ollama_model") or "").strip(),
        "local_reasoning": local_reasoning,
        "tidy_mode": draft.get("tidy_mode") if draft.get("tidy_mode") in TIDY_MODES else current.get("tidy_mode", "suggest"),
    }


# --- asking a service which models it has ------------------------------------

def list_models(ai):
    """[{"id", "size"}] for the mode/provider in a settings dict. Raises ValueError
    with a plain message on failure."""
    import anthropic
    import requests

    if ai.get("mode") == "local" or ai.get("list") == "local":
        url = (ai.get("ollama_url") or DEFAULT_OLLAMA_URL).rstrip("/")
        try:
            response = requests.get(url + "/api/tags", timeout=5)
            response.raise_for_status()
            models = response.json().get("models", [])
        except (requests.exceptions.RequestException, ValueError):
            raise ValueError(f"couldn't reach Ollama at {url}. Is it running?")
        return [{"id": m.get("name", ""), "size": round(m.get("size", 0) / 1e9, 1)}
                for m in models if m.get("name") and "embed" not in m.get("name", "")]

    provider = ai.get("provider")
    spec = PROVIDERS.get(provider)
    if not spec:
        raise ValueError("pick a provider first")
    key = _saved_key(ai, provider)
    if spec["kind"] == "anthropic":
        if not key:
            raise ValueError("enter an API key first")
        try:
            return [{"id": m.id, "size": None} for m in anthropic.Anthropic(api_key=key, max_retries=1).models.list()]
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError):
            raise ValueError("Anthropic refused the API key")
        except anthropic.APIError:
            raise ValueError("couldn't get the model list from Anthropic")
    base = (ai.get("base_url") or "").rstrip("/") if provider == "custom" else spec.get("base", "")
    if not base:
        raise ValueError("enter the API address first")
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    try:
        response = requests.get(base + "/models", headers=headers, timeout=10)
    except requests.exceptions.RequestException:
        raise ValueError(f"couldn't reach {base}")
    if response.status_code in (401, 403):
        raise ValueError("the service refused the API key")
    try:
        response.raise_for_status()
        return sorted(({"id": m["id"], "size": None} for m in response.json().get("data", []) if m.get("id")),
                      key=lambda m: m["id"])
    except (requests.exceptions.RequestException, ValueError, KeyError, TypeError):
        raise ValueError("the service didn't send a model list")
