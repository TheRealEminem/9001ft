"""Pages for the library: the list of kept copies (/library) and the "Keep a
copy?" confirmation (/library/save). Both are served with a nonce CSP."""

_HEAD = """<!DOCTYPE html>
<html lang="{{ lang }}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="referrer" content="same-origin">
<link rel="icon" href="/favicon.ico" type="image/png">
<style>
  :root { --bg: #ffffff; --text: #23232b; --muted: #6b6b76; --rule: #e3e1ea; --card: #faf9fd; --accent: #9b4dca; --accent-text: #fff; --bad: #b42318; --good: #1b7f3b; --focus: #9b4dca; }
  :root.dark { --bg: #191a21; --text: #ececf1; --muted: #a3a3b0; --rule: #33343f; --card: #22232c; --accent: #7359f8; --bad: #ff8a80; --good: #7ad79a; --focus: #b9a8ff; }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--text); font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
  main { max-width: 760px; margin: 0 auto; padding: 32px 16px 80px; }
  a { color: var(--accent); }
  :root.dark a { color: #b9a8ff; }
  a:focus-visible, button:focus-visible, summary:focus-visible, input:focus-visible { outline: 2px solid var(--focus); outline-offset: 2px; }
  h1 { font-size: 1.6rem; margin: 8px 0 4px; }
  .lede, .faint { color: var(--muted); }
  .lede { margin: 0 0 22px; }
  .button, button {
    display: inline-block; font: inherit; font-size: 14px; font-weight: 600; line-height: 1.2; padding: 8px 13px;
    border-radius: 7px; border: 1px solid var(--rule); background: var(--bg); color: var(--text) !important;
    cursor: pointer; text-decoration: none; white-space: nowrap;
  }
  .button:hover, button:hover { border-color: var(--accent); }
  .primary { background: var(--accent); border-color: var(--accent); color: var(--accent-text) !important; }
  .danger { background: var(--bad); border-color: var(--bad); color: #fff !important; }
  :root.dark .danger { color: #2a0f0c !important; }
  button:disabled { opacity: 0.65; cursor: progress; }
  .notice { border: 1px solid var(--rule); background: var(--card); border-radius: 10px; padding: 12px 16px; margin: 0 0 20px; }
  .notice.good { border-color: var(--good); }
  .notice.bad { border-color: var(--bad); color: var(--bad); }
  [hidden] { display: none !important; }
</style>
"""

LIBRARY_TEMPLATE = _HEAD + """<title>{{ library_title }} · 9001ft Ladder</title>
<style>
  .add { display: flex; gap: 8px; margin: 0 0 26px; flex-wrap: wrap; }
  .add label { flex-basis: 100%; font-weight: 600; font-size: 14px; }
  .add input { flex: 1 1 260px; min-width: 0; padding: 8px 10px; font: inherit; color: var(--text); background: var(--bg); border: 1px solid var(--rule); border-radius: 7px; }
  ul.items { list-style: none; margin: 0; padding: 0; display: grid; gap: 14px; }
  .card { border: 1px solid var(--rule); background: var(--card); border-radius: 12px; padding: 16px 18px; }
  .card .site { font-size: 12px; font-weight: 700; letter-spacing: 0.06em; text-transform: uppercase; color: var(--muted); }
  .card h2 { font-size: 1.15rem; line-height: 1.3; margin: 4px 0 4px; overflow-wrap: anywhere; }
  .card h2 a { color: var(--text); text-decoration: none; }
  .card h2 a:hover { text-decoration: underline; }
  .card .meta { margin: 0 0 12px; color: var(--muted); font-size: 14px; }
  .actions { display: flex; flex-wrap: wrap; gap: 8px; align-items: flex-start; }
  details.delete > summary { list-style: none; }
  details.delete > summary::-webkit-details-marker { display: none; }
  details.delete[open] > summary { border-color: var(--bad); }
  .confirm { flex-basis: 100%; margin-top: 10px; padding: 10px 12px; border: 1px solid var(--bad); border-radius: 8px; background: var(--bg); }
  .confirm p { margin: 0 0 8px; }
  details.skipped { margin-top: 10px; font-size: 13px; color: var(--muted); }
  details.skipped summary { cursor: pointer; }
  details.skipped ul { margin: 6px 0 0; padding-left: 18px; }
  details.skipped code { overflow-wrap: anywhere; }
  .empty { border: 2px dashed var(--rule); border-radius: 12px; padding: 22px; text-align: center; color: var(--muted); }
  .bookmarklet { margin-top: 36px; padding-top: 22px; border-top: 1px solid var(--rule); text-align: center; color: var(--muted); font-size: 14px; }
  .bookmarklet h2 { font-size: 1.05rem; color: var(--text); margin: 0 0 12px; }
  .bookmarklet p { max-width: 560px; margin: 12px auto 0; }
  .bookmarklet-link {
    display: inline-block; padding: 9px 16px; border: 2px dashed var(--accent); border-radius: 999px;
    color: var(--accent) !important; font-weight: 600; text-decoration: none; cursor: grab;
  }
  :root.dark .bookmarklet-link { color: #b9a8ff !important; border-color: #7359f8; }
</style>
</head>
<body>
<main>
  <a href="/">&larr; 9001ft Ladder</a>
  <h1>&#128218; {{ library_title }}</h1>
  <p class="lede">{{ library_lede }}</p>

  {% if saved %}
  <div class="notice good" role="status" tabindex="-1" id="notice">
    {{ library_saved }}
    {% if saved.skipped %} {{ library_saved_skipped.replace('{count}', saved.skipped|length|string) }}{% endif %}
  </div>
  {% elif deleted %}
  <div class="notice" role="status" tabindex="-1" id="notice">{{ library_deleted }}</div>
  {% elif error %}
  <div class="notice bad" role="alert" tabindex="-1" id="notice">{{ error }}</div>
  {% endif %}

  <form class="add" method="get" action="/library/save">
    <label for="add-url">{{ library_add_label }}</label>
    <input type="text" id="add-url" name="url" inputmode="url" placeholder="https://" required>
    <button type="submit" class="primary">&#128204; {{ keep_button }}</button>
  </form>

  {% if items %}
  <ul class="items">
    {% for item in items %}
    <li class="card">
      {% if item.site %}<div class="site">{{ item.site }}</div>{% endif %}
      <h2><a href="/library/{{ item.id }}/">{{ item.title }}</a></h2>
      <p class="meta">
        {{ library_saved_on.replace('{date}', item.date) }} &middot; {{ item.size_text }}{% if item.author %} &middot; {{ item.author }}{% endif %}
      </p>
      <div class="actions">
        <a class="button primary" href="/library/{{ item.id }}/">{{ library_open }}</a>
        <a class="button" href="/library/{{ item.id }}/reader">&#128214; {{ library_reader }}</a>
        {% if item.url %}<a class="button" href="{{ item.url }}" target="_blank" rel="noopener noreferrer">{{ library_live }} &#8599;</a>{% endif %}
        <details class="delete">
          <summary class="button" role="button">{{ library_delete }}</summary>
          <form class="confirm" method="post" action="/library/{{ item.id }}/delete">
            <input type="hidden" name="csrf" value="{{ csrf }}">
            <p>{{ library_delete_confirm.replace('{title}', item.title) }}</p>
            <button type="submit" class="danger">{{ library_delete_yes }}</button>
          </form>
        </details>
      </div>
      {% if item.skipped %}
      <details class="skipped">
        <summary>{{ library_skipped.replace('{count}', item.skipped|length|string) }}</summary>
        <ul>
          {% for skip in item.skipped[:100] %}<li><code>{{ skip.url|truncate(120, True) }}</code>: {{ skip.reason }}</li>{% endfor %}
        </ul>
      </details>
      {% endif %}
    </li>
    {% endfor %}
  </ul>
  {% else %}
  <p class="empty">{{ library_empty }}</p>
  {% endif %}

  <section class="bookmarklet" aria-labelledby="bookmarklet-heading">
    <h2 id="bookmarklet-heading">{{ library_bookmarklet_heading }}</h2>
    <a class="bookmarklet-link" id="keep-bookmarklet" href="{{ keep_bookmarklet }}"><span aria-hidden="true">&#128204;</span>&nbsp; {{ library_bookmarklet_label }}</a>
    <p>{{ library_bookmarklet_hint }}</p>
    <p id="bookmarklet-note" role="status"></p>
  </section>
</main>
<script nonce="{{ nonce }}">
(function () {
  try { if (localStorage.getItem("theme") === "dark" || (!localStorage.getItem("theme") && matchMedia("(prefers-color-scheme: dark)").matches)) { document.documentElement.classList.add("dark"); } } catch (e) {}
  var notice = document.getElementById("notice");
  if (notice) { notice.focus(); }
  document.getElementById("keep-bookmarklet").addEventListener("click", function (event) {
    event.preventDefault();
    document.getElementById("bookmarklet-note").textContent = {{ library_bookmarklet_click | tojson }};
  });
  document.querySelectorAll("details.delete").forEach(function (details) {
    details.addEventListener("toggle", function () {
      if (details.open) { details.querySelector("button").focus(); }
    });
    details.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && details.open) { details.open = false; details.querySelector("summary").focus(); }
    });
  });
})();
</script>
</body>
</html>
"""

CONFIRM_TEMPLATE = _HEAD + """<title>{{ library_confirm_title }} · 9001ft Ladder</title>
<style>
  main { max-width: 620px; }
  .page { border: 1px solid var(--rule); background: var(--card); border-radius: 12px; padding: 14px 18px; margin: 0 0 18px; overflow-wrap: anywhere; }
  .page b { display: block; font-size: 1.05rem; }
  .page span { color: var(--muted); font-size: 14px; }
  .actions { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-top: 18px; }
  #saving { color: var(--muted); }
</style>
</head>
<body>
<main>
  <a href="/library">&larr; {{ library_title }}</a>
  <h1>&#128204; {{ library_confirm_title }}</h1>
  {% if error %}<div class="notice bad" role="alert">{{ error }}</div>{% endif %}
  {% if url %}
  <div class="page">
    {% if title %}<b>{{ title }}</b>{% endif %}
    <span>{{ url }}</span>
  </div>
  <p>{{ library_confirm_text }}</p>
  {% if draft %}<p class="faint">{{ library_confirm_browser }}</p>{% endif %}
  <form method="post" action="/library/save" id="keep-form">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    {% if draft %}<input type="hidden" name="draft" value="{{ draft }}">{% else %}<input type="hidden" name="url" value="{{ url }}">{% endif %}
    <div class="actions">
      <button type="submit" class="primary" id="keep">&#128204; {{ keep_button }}</button>
      <a class="button" href="{{ cancel_href }}">{{ library_cancel }}</a>
      <span id="saving" role="status"></span>
    </div>
  </form>
  {% endif %}
</main>
<script nonce="{{ nonce }}">
(function () {
  try { if (localStorage.getItem("theme") === "dark" || (!localStorage.getItem("theme") && matchMedia("(prefers-color-scheme: dark)").matches)) { document.documentElement.classList.add("dark"); } } catch (e) {}
  var form = document.getElementById("keep-form");
  if (!form) { return; }
  document.getElementById("keep").focus();
  form.addEventListener("submit", function () {
    var button = document.getElementById("keep");
    setTimeout(function () { button.disabled = true; }, 0);
    document.getElementById("saving").textContent = {{ library_saving | tojson }};
  });
})();
</script>
</body>
</html>
"""


import theme  # noqa: E402

LIBRARY_TEMPLATE = theme.with_theme(LIBRARY_TEMPLATE)
CONFIRM_TEMPLATE = theme.with_theme(CONFIRM_TEMPLATE)


def content_security_policy(nonce):
    return (
        "default-src 'none'; img-src 'self'; font-src 'self'; style-src 'unsafe-inline'; "
        f"script-src 'nonce-{nonce}'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    )
