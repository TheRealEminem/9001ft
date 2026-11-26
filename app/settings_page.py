"""The /settings page: choose the AI used for optional features (AI tidy).

Safety rules the page follows, so settings can't be wiped by accident:
- a saved key is shown as a status ("Key saved, ending 1234"), never as an
  empty box; replacing it takes a click on "Replace key", and a blank box
  never changes it;
- removing a key takes "Remove key…" plus a confirmation, and only happens
  when Save is pressed;
- nothing is saved until Save is pressed (it is enabled only when something
  changed), leaving with unsaved changes asks first, and every save can be
  undone with "Undo last save" (see ai_settings.commit/undo).
"""

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
  main { max-width: 660px; margin: 0 auto; padding: 32px 16px 80px; }
  a { color: var(--accent); }
  h1 { font-size: 1.5rem; margin: 8px 0 4px; }
  h2 { font-family: var(--display, inherit); font-weight: 400; letter-spacing: 0.04em; font-size: 1.55rem; line-height: 1.15; margin: 26px 0 2px; }
  .lede, .faint, .help { color: var(--muted); }
  .lede { margin: 0 0 20px; }
  .help { display: block; font-weight: 400; font-size: 13.5px; line-height: 1.45; margin: 2px 0 6px; }
  .note { background: var(--card); border: 1px solid var(--rule); border-radius: 8px; padding: 10px 14px; margin: 0 0 20px; }
  fieldset.group { border: 0; padding: 0; margin: 0; min-width: 0; }
  fieldset.group > legend { padding: 0; }
  .mode { display: flex; gap: 10px; align-items: flex-start; padding: 12px 14px; border: 1px solid var(--rule); border-radius: 10px; margin: 10px 0 0; cursor: pointer; }
  .mode input { margin-top: 5px; flex: none; }
  .mode b { display: block; }
  .mode .help b { display: inline; }
  .mode .help { margin: 2px 0 0; }
  .body { border: 1px solid var(--rule); border-top: 0; border-radius: 0 0 10px 10px; margin: -6px 0 0; padding: 16px 14px 8px; background: var(--card); }
  .field { margin: 0 0 16px; }
  .field > label, .field > .label { display: block; font-weight: 700; font-size: 14px; }
  input[type=text], input[type=password], input[type=url], select {
    display: block; width: 100%; margin-top: 4px; padding: 8px 10px; font: inherit; color: var(--text);
    background: var(--bg); border: 1px solid var(--rule); border-radius: 6px;
  }
  .inline { display: flex; gap: 8px; margin-top: 4px; align-items: center; }
  .inline select, .inline input { margin-top: 0; }
  button { font: inherit; font-weight: 600; padding: 8px 14px; border-radius: 6px; border: 1px solid var(--rule); background: var(--bg); color: var(--text); cursor: pointer; white-space: nowrap; }
  button.primary { background: var(--accent); border-color: var(--accent); color: var(--accent-text); }
  button:disabled { opacity: 0.6; cursor: progress; }
  #save:disabled { cursor: not-allowed; opacity: 0.5; }
  .check { display: flex; gap: 8px; align-items: flex-start; font-weight: 700; font-size: 14px; margin: 4px 0 0; }
  .check input { margin-top: 4px; flex: none; }
  .check + .help { margin: 0 0 14px 24px; }
  .key-box { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; justify-content: space-between;
             margin-top: 6px; padding: 8px 10px; border: 2px dashed var(--rule); border-radius: 10px; background: var(--bg); }
  .key-status { font-weight: 700; color: var(--good); }
  .key-status.bad { color: var(--bad); }
  .key-buttons { display: flex; gap: 8px; flex-wrap: wrap; }
  .confirm { margin-top: 6px; padding: 10px 12px; border-color: var(--bad) !important; }
  .confirm p { margin: 0 0 10px; font-weight: 600; }
  .confirm .key-buttons { justify-content: flex-start; }
  .actions { display: flex; gap: 10px; align-items: center; margin-top: 22px; flex-wrap: wrap; }
  #save-state { font-weight: 700; }
  #save-state.dirty { color: var(--bad); }
  #save-state.good { color: var(--good); }
  .undo-row { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-top: 14px; }
  .undo-row .help { margin: 0; }
  #status { min-height: 1.5em; margin: 12px 0 0; }
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
  <p class="lede">AI is optional. It powers <b>&#10024; Tidy up</b> in Reader view, which fixes the layout of a messy article without changing its words. Nothing is sent anywhere unless you press it, or turn on <b>Tidy automatically</b> below.</p>
  <p class="note" id="env-note" hidden>These settings come from <code>.env.local</code> right now. Saving here keeps your own copy in the data folder and uses that instead; the file itself isn't changed, and <b>Undo last save</b> goes back to it.</p>

  <form id="form" autocomplete="off">
    <fieldset class="group">
      <legend><h2>Which AI to use</h2></legend>

      <label class="mode"><input type="radio" name="mode" value="off"><span><b>Off</b><span class="help">No AI features. Everything else in 9001ft works as usual.</span></span></label>

      <label class="mode"><input type="radio" name="mode" value="local"><span><b>Local model</b><span class="help">Runs on your own computer with Ollama, a free app for running AI models. Nothing leaves your computer, but it uses memory and can be slow on a laptop.</span></span></label>

      <label class="mode"><input type="radio" name="mode" value="byok"><span><b>Your own API key</b><span class="help">Use an AI service with a key from your own account: Anthropic, OpenAI, Gemini, OpenRouter and more, or your own server such as a LiteLLM router. Usually fast; the article's text goes to that service when it tidies.</span></span></label>
      <div class="body" data-for="byok">
        <div class="field">
          <label for="f-provider">Provider</label>
          <span class="help" id="h-provider">Who runs the model. You get a key from their website. Pick the last option for your own server.</span>
          <select name="provider" id="f-provider" aria-describedby="h-provider"></select>
        </div>
        <div class="field" data-only="custom">
          <label for="f-base">API address</label>
          <span class="help" id="h-base">The address of a server that speaks the OpenAI API (most AI servers do), usually ending in <code>/v1</code>. For example a LiteLLM router, which passes requests on to the models you've set up (<code>http://192.0.2.10:4000/v1</code>), or LM Studio (<code>http://localhost:1234/v1</code>).</span>
          <input type="url" name="base_url" id="f-base" placeholder="http://192.0.2.10:4000/v1" aria-describedby="h-base">
        </div>
        <div class="field" id="key-field">
          <span class="label" id="key-label">API key</span>
          <span class="help" id="h-key">Kept only on this computer, in a file only you can read. It's never shown again, not even here, and is only sent to the provider you picked.</span>
          <div class="key-box" id="key-saved" hidden>
            <span class="key-status" id="key-status"></span>
            <span class="key-buttons" id="key-buttons">
              <button type="button" id="replace-key">Replace key</button>
              <button type="button" id="remove-key">Remove key&hellip;</button>
            </span>
          </div>
          <div class="confirm" id="remove-confirm" role="group" aria-labelledby="remove-question" hidden>
            <p id="remove-question"></p>
            <span class="key-buttons">
              <button type="button" class="danger" id="remove-yes">Remove</button>
              <button type="button" id="remove-no">Keep it</button>
            </span>
          </div>
          <div class="key-box" id="key-removing" hidden>
            <span class="key-status bad">Key will be removed when you save.</span>
            <button type="button" id="keep-key">Keep the key</button>
          </div>
          <div class="inline" id="key-entry" hidden>
            <input type="password" name="key" id="f-key" autocomplete="new-password" aria-labelledby="key-label" aria-describedby="h-key h-key-new">
            <button type="button" id="cancel-key" hidden>Cancel</button>
          </div>
          <span class="help" id="h-key-new" hidden>The new key replaces the saved one when you press Save. Leave the box empty to keep the saved key.</span>
        </div>
        <div class="field">
          <label for="f-model">Model</label>
          <span class="help" id="h-model">Which model does the work. <b>Load list</b> asks the provider which models your key can use; pick <b>Other&hellip;</b> to type any model id.</span>
          <span class="inline"><select name="model" id="f-model" aria-describedby="h-model"></select><button type="button" id="load-models" title="Ask the provider which models your key can use">Load list</button></span>
        </div>
        <div class="field" id="model-other" hidden>
          <label for="f-model-other">Model id</label>
          <span class="help" id="h-model-other">Exactly as the provider writes it in its model list.</span>
          <input type="text" name="model_other" id="f-model-other" placeholder="model id" aria-describedby="h-model-other">
        </div>
        <div class="field">
          <label for="f-reasoning">Thinking</label>
          <span class="help" id="h-reasoning">How much the model thinks before it answers, for models that can. <b>Off</b> is fastest and cheapest, <b>Low</b> is plenty for tidying, <b>Medium</b> and <b>High</b> are more careful but slower and cost more with paid services. <b>Provider default</b> lets the service decide.</span>
          <select name="reasoning" id="f-reasoning" aria-describedby="h-reasoning">
            <option value="">Provider default</option>
            <option value="none">Off (fastest)</option>
            <option value="low">Low</option>
            <option value="medium">Medium</option>
            <option value="high">High</option>
          </select>
        </div>
        <label class="check"><input type="checkbox" name="fallback_local" aria-describedby="h-fallback"> If this can't be reached, use my local model instead</label>
        <span class="help" id="h-fallback">For when you're offline or the service is down: 9001ft then tries your Ollama model, set up below. It's only used when the first one fails.</span>
        <p class="faint" id="privacy"></p>
      </div>

      <div class="body" data-for="local">
        <div class="field">
          <label for="f-ollama-url">Ollama address</label>
          <span class="help" id="h-ollama-url">Where Ollama listens. Keep <code>http://localhost:11434</code> unless Ollama runs on another computer.</span>
          <input type="url" name="ollama_url" id="f-ollama-url" placeholder="http://localhost:11434" aria-describedby="h-ollama-url">
        </div>
        <div class="field">
          <label for="f-ollama-model">Local model</label>
          <span class="help" id="h-ollama-model">While it works, a model uses about its download size in memory; Ollama frees it a few minutes later. Small models (2&ndash;4B) suit laptops. <b>Refresh</b> asks Ollama which models you've downloaded.</span>
          <span class="inline"><select name="ollama_model" id="f-ollama-model" aria-describedby="h-ollama-model"></select><button type="button" id="load-local">Refresh</button></span>
        </div>
        <div class="field">
          <label for="f-local-reasoning">Thinking</label>
          <span class="help" id="h-local-reasoning"><b>Off</b> is much faster on a laptop and fine for tidying. Higher levels make the model think longer first, which is slower and rarely needed here.</span>
          <select name="local_reasoning" id="f-local-reasoning" aria-describedby="h-local-reasoning">
            <option value="none">Off (much faster)</option>
            <option value="">Model default</option>
            <option value="low">Low</option>
            <option value="medium">Medium</option>
            <option value="high">High</option>
          </select>
        </div>
      </div>
    </fieldset>

    <fieldset class="group" id="tidy-group">
      <legend><h2>When a page looks messy</h2></legend>
      <span class="help">What Reader view does with &#10024; Tidy up. This applies whenever an AI is set up above.</span>
      <span class="help" id="tidy-off-note" hidden>The AI is off, so these wait until you choose one above.</span>
      <label class="mode"><input type="radio" name="tidy_mode" value="suggest"><span><b>Offer to tidy it (recommended)</b><span class="help">Reader view shows a clear &ldquo;Tidy this up?&rdquo; offer on messy pages. Nothing is sent until you press it.</span></span></label>
      <label class="mode"><input type="radio" name="tidy_mode" value="auto"><span><b>Tidy automatically</b><span class="help">Pages from an archive copy, or that look jumbled, are tidied as they open, so their text goes to your AI straight away. <b>Show original</b> is always there.</span></span></label>
      <label class="mode"><input type="radio" name="tidy_mode" value="off"><span><b>Only the toolbar button</b><span class="help">No offers. Press &#10024; Tidy up in Reader view's toolbar whenever you want it.</span></span></label>
    </fieldset>

    <div class="actions">
      <button type="submit" class="primary" id="save" disabled>Save</button>
      <button type="button" id="discard" hidden>Discard changes</button>
      <button type="button" id="test" aria-describedby="h-test">Test AI</button>
      <span id="save-state" aria-live="polite"></span>
    </div>
    <span class="help">Nothing changes until you press <b>Save</b>, and every save can be undone.</span>
    <span class="help" id="h-test"><b>Test AI</b> sends one tiny request with the choices above, saved or not, to check they work. A local model loads into memory for a few minutes.</span>
    <div class="undo-row" id="undo-row" hidden>
      <button type="button" id="undo" aria-describedby="undo-what">Undo last save</button>
      <span class="help" id="undo-what"></span>
    </div>
    <div class="undo-row" id="redo-row" hidden>
      <button type="button" id="redo" aria-describedby="redo-what">Redo</button>
      <span class="help" id="redo-what">Redo: bring back the settings you just undid.</span>
    </div>
    <p id="status" role="status" aria-live="polite"></p>
  </form>
</main>
<script nonce="{{ nonce }}">
(function () {
  try { if (localStorage.getItem("theme") === "dark" || (!localStorage.getItem("theme") && matchMedia("(prefers-color-scheme: dark)").matches)) { document.documentElement.classList.add("dark"); } } catch (e) {}
  var PROVIDERS = {{ providers | tojson }};
  var form = document.getElementById("form");
  var statusEl = document.getElementById("status");
  var saveButton = document.getElementById("save");
  var saveState = document.getElementById("save-state");
  var OTHER = "__other__";
  var saved = {};
  var snapshot = "";
  var dirty = false;
  var busy = false;
  var savedTime = "";
  var keyAction = "keep";   // keep | replace | remove
  var confirming = false;

  function $(id) { return document.getElementById(id); }
  function status(text, kind) { statusEl.textContent = text; statusEl.className = kind || ""; }
  function option(value, text) { var o = document.createElement("option"); o.value = value; o.textContent = text; return o; }
  function shortLabel(provider) { return provider === "custom" ? "your API address" : PROVIDERS[provider].label.replace(/ \\(.*\\)$/, ""); }

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

  // --- the API key: a status with buttons when one is saved, never an empty box

  function savedHint() { return (saved.keys || {})[form.provider.value] || ""; }

  function keyStatusText(hint) {
    if (hint.indexOf("saved key ending ") === 0) { return "\\u2713 Key saved, ending " + hint.slice(17); }
    if (hint === "saved key file") { return "\\u2713 Key saved, from a key file"; }
    return "\\u2713 Key saved";
  }

  function renderKey() {
    var hint = savedHint();
    var provider = form.provider.value;
    var entry = !hint || keyAction === "replace";
    $("key-saved").hidden = !hint || keyAction === "remove" || confirming;
    $("key-buttons").hidden = keyAction === "replace";
    $("key-status").textContent = hint ? keyStatusText(hint) : "";
    $("remove-confirm").hidden = !confirming;
    $("remove-question").textContent = "Remove the saved key for " + shortLabel(provider) + "? 9001ft won't be able to use it until you add a key again.";
    $("key-removing").hidden = keyAction !== "remove";
    $("key-entry").hidden = !entry || confirming;
    $("cancel-key").hidden = !hint;
    $("h-key-new").hidden = !hint || keyAction !== "replace";
    form.key.placeholder = hint ? "new key" : (PROVIDERS[provider].key_hint || (provider === "custom" ? "leave empty if your server doesn't need one" : ""));
  }

  function resetKey() { keyAction = "keep"; confirming = false; form.key.value = ""; }

  $("replace-key").onclick = function () { keyAction = "replace"; confirming = false; update(); form.key.focus(); };
  $("cancel-key").onclick = function () { resetKey(); update(); $("replace-key").focus(); status("Kept the saved key."); };
  $("remove-key").onclick = function () { confirming = true; update(); $("remove-no").focus(); };
  $("remove-no").onclick = function () { confirming = false; update(); $("remove-key").focus(); };
  $("remove-yes").onclick = function () {
    confirming = false; keyAction = "remove"; form.key.value = ""; update(); $("keep-key").focus();
    status("The key will be removed when you press Save.");
  };
  $("keep-key").onclick = function () { resetKey(); update(); $("remove-key").focus(); status("Kept the saved key."); };
  $("remove-confirm").addEventListener("keydown", function (event) { if (event.key === "Escape") { $("remove-no").click(); } });

  // --- the rest of the form

  function refreshVisibility() {
    var mode = form.mode.value;
    var provider = form.provider.value;
    document.querySelector('[data-for="byok"]').hidden = mode !== "byok";
    document.querySelector('[data-for="local"]').hidden = !(mode === "local" || (mode === "byok" && form.fallback_local.checked));
    document.querySelectorAll("[data-only]").forEach(function (el) { el.hidden = el.dataset.only !== provider; });
    $("model-other").hidden = form.model.value !== OTHER;
    $("tidy-off-note").hidden = mode !== "off";
    $("privacy").textContent = mode === "byok"
      ? "When it tidies, the article's text is sent to " + (provider === "custom" ? "the API address above" : shortLabel(provider)) + " to work out the layout."
      : "";
    renderKey();
  }

  function draft() {
    var model = form.model.value === OTHER ? form.model_other.value.trim() : form.model.value;
    var typing = !$("key-entry").hidden;
    return {
      mode: form.mode.value, provider: form.provider.value, base_url: form.base_url.value.trim(),
      key: typing ? form.key.value.trim() : "", clear_key: keyAction === "remove", model: model,
      reasoning: form.reasoning.value, fallback_local: form.fallback_local.checked,
      ollama_url: form.ollama_url.value.trim(), ollama_model: form.ollama_model.value,
      local_reasoning: form.local_reasoning.value, tidy_mode: form.tidy_mode.value || "suggest"
    };
  }

  function ago(seconds) {
    if (seconds === null || seconds === undefined) { return "before"; }
    if (seconds < 60) { return "a moment ago"; }
    var units = [[86400, "day"], [3600, "hour"], [60, "minute"]];
    for (var i = 0; i < units.length; i++) {
      var n = Math.floor(seconds / units[i][0]);
      if (n >= 1) { return n + " " + units[i][1] + (n === 1 ? "" : "s") + " ago"; }
    }
    return "before";
  }

  function undoText(entry) {
    var target = entry.kind === "env" ? "the settings from .env.local (environment settings)"
      : entry.kind === "none" ? "no saved settings (AI off)"
      : "the settings from " + ago(entry.age);
    return "Undo: go back to " + target + "." + (dirty ? " Unsaved changes here are discarded too." : "");
  }

  function update() {
    refreshVisibility();
    dirty = JSON.stringify(draft()) !== snapshot;
    saveButton.disabled = !dirty || busy;
    $("discard").hidden = !dirty;
    saveState.textContent = dirty ? "Unsaved changes" : (savedTime ? "Saved at " + savedTime : "");
    saveState.className = dirty ? "dirty" : (savedTime ? "good" : "");
    $("undo-row").hidden = !saved.undo;
    if (saved.undo) { $("undo-what").textContent = undoText(saved.undo); }
    $("redo-row").hidden = !saved.redo;
  }

  function fill(view) {
    saved = view;
    resetKey();
    form.mode.value = view.mode || "off";
    form.provider.value = view.provider || "anthropic";
    form.base_url.value = view.base_url || "";
    form.reasoning.value = view.reasoning || "";
    form.fallback_local.checked = !!view.fallback_local;
    form.ollama_url.value = view.ollama_url || "";
    form.local_reasoning.value = view.local_reasoning === undefined ? "none" : view.local_reasoning;
    form.tidy_mode.value = view.tidy_mode || "suggest";
    fillModels(form.model, providerSuggestions(form.provider.value), (view.models || {})[form.provider.value] || "", true);
    form.model_other.value = "";
    fillModels(form.ollama_model, [], view.ollama_model || "", false);
    $("env-note").hidden = view.source !== "env";
    refreshVisibility();
    snapshot = JSON.stringify(draft());
    update();
  }

  form.addEventListener("change", function (event) {
    if (event.target.name === "provider") {
      fillModels(form.model, providerSuggestions(form.provider.value), (saved.models || {})[form.provider.value] || "", true);
      resetKey();
    }
    update();
  });
  form.addEventListener("input", update);

  window.addEventListener("beforeunload", function (event) {
    if (dirty) { event.preventDefault(); event.returnValue = ""; }
  });

  document.getElementById("load-models").onclick = function () {
    status("Asking for the model list…");
    var d = draft(); d.list = "hosted";
    call("/settings/ai/models", d).then(function (data) {
      fillModels(form.model, data.models, form.model.value === OTHER ? "" : form.model.value, true);
      update();
      status(data.models.length + " models found.", "good");
    }).catch(function (error) { status("Couldn't load models: " + error.message, "bad"); });
  };

  document.getElementById("load-local").onclick = function () {
    status("Asking Ollama…");
    var d = draft(); d.list = "local";
    call("/settings/ai/models", d).then(function (data) {
      fillModels(form.ollama_model, data.models, form.ollama_model.value, false);
      update();
      status(data.models.length ? data.models.length + " local models found." : "Ollama has no chat models yet. Download one with ollama pull.", data.models.length ? "good" : "bad");
    }).catch(function (error) { status(error.message, "bad"); });
  };

  function clock() { return new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    if (!dirty || busy) { return; }
    busy = true; update();
    status("Saving…");
    call("/settings/ai", draft()).then(function (view) {
      busy = false; savedTime = clock(); fill(view);
      status("Saved at " + savedTime + ".", "good");
    }).catch(function (error) { busy = false; update(); status("Couldn't save: " + error.message, "bad"); });
  });

  $("discard").onclick = function () {
    call("/settings/ai").then(function (view) { fill(view); status("Changes discarded. These are your saved settings."); $("save").focus(); })
      .catch(function (error) { status("Couldn't load settings: " + error.message, "bad"); });
  };

  function undo(redo) {
    status(redo ? "Redoing…" : "Undoing…");
    call("/settings/ai/undo", redo ? { redo: true } : {}).then(function (view) {
      savedTime = ""; fill(view);
      status(redo ? "Redone: the settings you undid are back." : "Undone: the previous settings are back." + (view.redo ? " Press Redo to change your mind." : ""), "good");
      (view.redo && !redo ? $("redo") : view.undo ? $("undo") : $("save")).focus();
    }).catch(function (error) { status("Couldn't undo: " + error.message, "bad"); });
  }
  $("undo").onclick = function () { undo(false); };
  $("redo").onclick = function () { undo(true); };

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
