"""The /settings page: choose the AI used for optional features (AI tidy)."""

SETTINGS_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="referrer" content="no-referrer">
<title>AI settings · 9001ft Ladder</title>
<link rel="icon" href="/favicon.ico" type="image/png">
<link rel="manifest" href="/manifest.webmanifest">
<meta name="theme-color" content="#fdf3dc">
<link rel="apple-touch-icon" href="/icons/apple-touch-icon.png">
<script src="/static/app.js" defer nonce="{{ nonce }}"></script>
<style>
  :root { --bg: #ffffff; --text: #23232b; --muted: #6b6b76; --rule: #e3e1ea; --card: #faf9fd; --accent: #9b4dca; --accent-text: #fff; --bad: #b42318; --good: #1b7f3b; }
  :root.dark { --bg: #191a21; --text: #ececf1; --muted: #a3a3b0; --rule: #33343f; --card: #22232c; --accent: #7359f8; --bad: #ff8a80; --good: #7ad79a; }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--text); font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
  main { max-width: 640px; margin: 0 auto; padding: 32px 16px 80px; }
  a { color: var(--accent); }
  h1 { font-size: 1.5rem; margin: 8px 0 4px; }
  .lede, .faint { color: var(--muted); }
  .lede { margin: 0 0 20px; }
  .note { background: var(--card); border: 1px solid var(--rule); border-radius: 8px; padding: 10px 14px; margin: 0 0 20px; }
  .mode { display: flex; gap: 10px; align-items: flex-start; padding: 12px 14px; border: 1px solid var(--rule); border-radius: 10px; margin: 10px 0 0; cursor: pointer; }
  .mode input { margin-top: 4px; }
  .mode b { display: block; }
  .body { border: 1px solid var(--rule); border-top: 0; border-radius: 0 0 10px 10px; margin: -6px 0 0; padding: 16px 14px 8px; background: var(--card); }
  label.field { display: block; margin: 0 0 12px; font-weight: 600; font-size: 14px; }
  label.field small { font-weight: 400; }
  input[type=text], input[type=password], input[type=url], select {
    display: block; width: 100%; margin-top: 4px; padding: 8px 10px; font: inherit; color: var(--text);
    background: var(--bg); border: 1px solid var(--rule); border-radius: 6px;
  }
  .inline { display: flex; gap: 8px; margin-top: 4px; }
  .inline select { margin-top: 0; }
  button { font: inherit; font-weight: 600; padding: 8px 14px; border-radius: 6px; border: 1px solid var(--rule); background: var(--bg); color: var(--text); cursor: pointer; white-space: nowrap; }
  button.primary { background: var(--accent); border-color: var(--accent); color: var(--accent-text); }
  button:disabled { opacity: 0.6; cursor: progress; }
  .check { display: flex; gap: 8px; align-items: flex-start; font-weight: 400; margin: 4px 0 12px; }
  .check input { margin-top: 4px; }
  .actions { display: flex; gap: 10px; align-items: center; margin-top: 20px; flex-wrap: wrap; }
  #status { min-height: 1.5em; }
  #status.bad { color: var(--bad); }
  #status.good { color: var(--good); }
  [hidden] { display: none !important; }
  .logout { float: right; }
</style>
</head>
<body>
<main>
  <a href="/">&larr; 9001ft Ladder</a>
  {% if show_logout %}<a class="logout" href="/logout">Log out</a>{% endif %}
  <h1>AI settings</h1>
  <p class="lede">AI is optional. It powers <b>&#10024; Tidy up</b> in Reader view, which fixes the layout of a messy article without changing its words. Nothing is sent anywhere until you press it.</p>
  <p class="note" id="env-note" hidden>These settings currently come from <code>.env.local</code>. Saving here replaces them; the file itself isn't changed.</p>

  <form id="form" autocomplete="off">
    <label class="mode"><input type="radio" name="mode" value="off"><span><b>Off</b><span class="faint">No AI features.</span></span></label>

    <label class="mode"><input type="radio" name="mode" value="local"><span><b>Local model with Ollama</b><span class="faint">Runs on your own computer. Nothing leaves it.</span></span></label>

    <label class="mode"><input type="radio" name="mode" value="byok"><span><b>Your own API key</b><span class="faint">Anthropic, OpenAI, Gemini, OpenRouter, or any OpenAI-compatible service such as a LiteLLM router.</span></span></label>
    <div class="body" data-for="byok">
      <label class="field">Provider <select name="provider"></select></label>
      <label class="field" data-only="custom">API address <small class="faint">(ending in /v1, usually)</small>
        <input type="url" name="base_url" placeholder="http://192.0.2.10:4000/v1"></label>
      <label class="field">API key
        <input type="password" name="key" autocomplete="new-password"></label>
      <label class="check" id="clear-key-row" hidden><input type="checkbox" name="clear_key"> Remove the saved key</label>
      <label class="field">Model
        <span class="inline"><select name="model"></select><button type="button" id="load-models" title="Ask the provider which models your key can use">Load list</button></span></label>
      <label class="field" id="model-other" hidden>Model id <input type="text" name="model_other" placeholder="exactly as the provider names it"></label>
      <label class="field">Thinking <small class="faint">(for models that think before answering)</small>
        <select name="reasoning">
          <option value="">Provider default</option>
          <option value="none">Off (fastest)</option>
          <option value="low">Low</option>
          <option value="medium">Medium</option>
          <option value="high">High</option>
        </select></label>
      <label class="check"><input type="checkbox" name="fallback_local"> If this can't be reached, use my local model instead</label>
      <p class="faint" id="privacy"></p>
    </div>

    <div class="body" data-for="local">
      <label class="field">Ollama address <input type="url" name="ollama_url" placeholder="http://localhost:11434"></label>
      <label class="field">Local model
        <span class="inline"><select name="ollama_model"></select><button type="button" id="load-local">Refresh</button></span></label>
      <label class="field">Thinking
        <select name="local_reasoning">
          <option value="none">Off (much faster)</option>
          <option value="">Model default</option>
          <option value="low">Low</option>
          <option value="medium">Medium</option>
          <option value="high">High</option>
        </select></label>
      <p class="faint">A local model uses about its download size in memory while it works, and Ollama unloads it a few minutes later. Small models (2–4B) suit laptops.</p>
    </div>

    <div class="actions">
      <button type="submit" class="primary" id="save">Save</button>
      <button type="button" id="test">Test AI</button>
      <span id="status" role="status"></span>
    </div>
  </form>
</main>
<script nonce="{{ nonce }}">
(function () {
  try { if (localStorage.getItem("theme") === "dark" || (!localStorage.getItem("theme") && matchMedia("(prefers-color-scheme: dark)").matches)) { document.documentElement.classList.add("dark"); } } catch (e) {}
  var PROVIDERS = {{ providers | tojson }};
  var form = document.getElementById("form");
  var statusEl = document.getElementById("status");
  var OTHER = "__other__";
  var saved = {};

  function status(text, kind) { statusEl.textContent = text; statusEl.className = kind || ""; }
  function option(value, text) { var o = document.createElement("option"); o.value = value; o.textContent = text; return o; }

  Object.keys(PROVIDERS).forEach(function (id) { form.provider.appendChild(option(id, PROVIDERS[id].label)); });

  function call(path, body) {
    return fetch(path, {
      method: body ? "POST" : "GET",
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined
    }).then(function (response) {
      return response.json().then(function (data) {
        if (!response.ok) { throw new Error(data.error || String(response.status)); }
        return data;
      });
    });
  }

  function fillModels(select, ids, current, withOther) {
    select.innerHTML = "";
    var list = ids.slice();
    if (current && list.map(function (m) { return m.id; }).indexOf(current) === -1) { list.unshift({ id: current, size: null }); }
    list.forEach(function (m) { select.appendChild(option(m.id, m.size ? m.id + " · " + m.size + " GB" : m.id)); });
    if (withOther) { select.appendChild(option(OTHER, "Other…")); }
    if (!list.length && !withOther) { select.appendChild(option("", "Press Refresh")); }
    select.value = current || (list[0] ? list[0].id : (withOther ? OTHER : ""));
  }

  function providerSuggestions(provider) {
    return (PROVIDERS[provider].models || []).map(function (id) { return { id: id, size: null }; });
  }

  function refreshVisibility() {
    var mode = form.mode.value;
    var provider = form.provider.value;
    document.querySelector('[data-for="byok"]').hidden = mode !== "byok";
    document.querySelector('[data-for="local"]').hidden = !(mode === "local" || (mode === "byok" && form.fallback_local.checked));
    document.querySelectorAll("[data-only]").forEach(function (el) { el.hidden = el.dataset.only !== provider; });
    document.getElementById("model-other").hidden = form.model.value !== OTHER;
    var hint = (saved.keys || {})[provider];
    form.key.placeholder = hint ? hint + " (leave blank to keep it)" : (PROVIDERS[provider].key_hint || "");
    document.getElementById("clear-key-row").hidden = !hint;
    document.getElementById("privacy").textContent = mode === "byok"
      ? "When you press Tidy up, the article's text is sent to " + (provider === "custom" ? "the API address above" : PROVIDERS[provider].label.replace(/ \\(.*\\)$/, "")) + " to work out the layout."
      : "";
  }

  function draft() {
    var model = form.model.value === OTHER ? form.model_other.value.trim() : form.model.value;
    return {
      mode: form.mode.value, provider: form.provider.value, base_url: form.base_url.value.trim(),
      key: form.key.value.trim(), clear_key: form.clear_key.checked, model: model,
      reasoning: form.reasoning.value, fallback_local: form.fallback_local.checked,
      ollama_url: form.ollama_url.value.trim(), ollama_model: form.ollama_model.value,
      local_reasoning: form.local_reasoning.value
    };
  }

  function fill(view) {
    saved = view;
    form.mode.value = view.mode || "off";
    form.provider.value = view.provider || "anthropic";
    form.base_url.value = view.base_url || "";
    form.key.value = "";
    form.clear_key.checked = false;
    form.reasoning.value = view.reasoning || "";
    form.fallback_local.checked = !!view.fallback_local;
    form.ollama_url.value = view.ollama_url || "";
    form.local_reasoning.value = view.local_reasoning === undefined ? "none" : view.local_reasoning;
    fillModels(form.model, providerSuggestions(form.provider.value), (view.models || {})[form.provider.value] || "", true);
    fillModels(form.ollama_model, [], view.ollama_model || "", false);
    document.getElementById("env-note").hidden = view.source !== "env";
    refreshVisibility();
  }

  form.addEventListener("change", function (event) {
    if (event.target.name === "provider") {
      fillModels(form.model, providerSuggestions(form.provider.value), (saved.models || {})[form.provider.value] || "", true);
      form.key.value = "";
    }
    refreshVisibility();
  });

  document.getElementById("load-models").onclick = function () {
    status("Asking for the model list…");
    var d = draft(); d.list = "hosted";
    call("/settings/ai/models", d).then(function (data) {
      fillModels(form.model, data.models, form.model.value === OTHER ? "" : form.model.value, true);
      refreshVisibility();
      status(data.models.length + " models found.", "good");
    }).catch(function (error) { status("Couldn't load models: " + error.message, "bad"); });
  };

  document.getElementById("load-local").onclick = function () {
    status("Asking Ollama…");
    var d = draft(); d.list = "local";
    call("/settings/ai/models", d).then(function (data) {
      fillModels(form.ollama_model, data.models, form.ollama_model.value, false);
      status(data.models.length ? data.models.length + " local models found." : "Ollama has no chat models yet. Download one with ollama pull.", data.models.length ? "good" : "bad");
    }).catch(function (error) { status(error.message, "bad"); });
  };

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    status("Saving…");
    call("/settings/ai", draft()).then(function (view) { fill(view); status("Saved.", "good"); })
      .catch(function (error) { status("Couldn't save: " + error.message, "bad"); });
  });

  document.getElementById("test").onclick = function () {
    var button = this;
    button.disabled = true;
    status("Testing… a local model may take a moment to load.");
    call("/settings/ai/test", draft()).then(function (data) {
      status("Works: " + data.model + " answered in " + data.seconds + "s.", "good");
    }).catch(function (error) { status("Test failed: " + error.message, "bad"); })
      .then(function () { button.disabled = false; });
  };

  call("/settings/ai").then(fill).catch(function (error) { status("Couldn't load settings: " + error.message, "bad"); });
})();
</script>
</body>
</html>
"""


import theme  # noqa: E402

SETTINGS_TEMPLATE = theme.with_theme(SETTINGS_TEMPLATE)


def content_security_policy(nonce):
    return (
        "default-src 'none'; img-src 'self'; font-src 'self'; style-src 'unsafe-inline'; "
        f"script-src 'nonce-{nonce}'; connect-src 'self'; worker-src 'self'; manifest-src 'self'; "
        "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    )
