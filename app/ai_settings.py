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
"""

import json
import os
import tempfile
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


def save(ai):
    """Write settings atomically, readable only by this user."""
    folder = data_dir()
    os.makedirs(folder, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".settings-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"ai": ai}, handle, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, settings_path())
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


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
    view["source"] = source()
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
    """Validate a settings draft from the page. A blank key keeps the saved one."""
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
    elif draft.get("clear_key"):
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
