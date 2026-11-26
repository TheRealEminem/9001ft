"""The climbing scene: a hand-drawn ladder, a grandmother with a lasagna, and
the friendly error page that reuses them.

Everything here is plain SVG and CSS. The SVG uses presentation attributes
only (no style="" attributes), so it also works under a strict
Content-Security-Policy.
"""

INK = "#1d2b30"
CREAM = "#fff8e7"
RED = "#d1382c"
YELLOW = "#ffcf33"
ORANGE = "#ee7b22"
SKIN = "#f6c39f"
GREY = "#dfe3e6"

# The ladder's left rail runs from LADDER_FOOT to LADDER_FOOT + LADDER_RISE (at
# t = 1); the right rail is LADDER_WIDTH to its right. The climber's hand grips
# the left rail, and CLIMB_FROM..CLIMB_TO is the stretch of rail she covers.
LADDER_FOOT = (96, 408)
LADDER_RISE = (140, -324)
LADDER_WIDTH = 44
CLIMBER_SCALE = 1.2
CLIMBER_HAND = (88, 40)
CLIMB_FROM = 0.35
CLIMB_TO = 0.9
SUMMIT_FEET = 9001

DISPLAY_FONT = "Bangers, Impact, Haettenschweiler, 'Arial Narrow Bold', sans-serif"


def climber_position(fraction):
    """Where the climber group sits when `fraction` (0..1) of the way up."""
    fraction = max(0.0, min(1.0, fraction))
    t = CLIMB_FROM + (CLIMB_TO - CLIMB_FROM) * fraction
    x = LADDER_FOOT[0] + LADDER_RISE[0] * t - CLIMBER_HAND[0] * CLIMBER_SCALE
    y = LADDER_FOOT[1] + LADDER_RISE[1] * t - CLIMBER_HAND[1] * CLIMBER_SCALE
    return round(x, 1), round(y, 1)


def _limb(path):
    """A rounded arm or leg: an ink outline with skin drawn over it."""
    return (
        f'<path d="{path}" fill="none" stroke="{INK}" stroke-width="11" stroke-linecap="round"/>'
        f'<path d="{path}" fill="none" stroke="{SKIN}" stroke-width="6.5" stroke-linecap="round"/>'
    )


def _mitt(cx, cy, angle=0):
    return (
        f'<g transform="rotate({angle} {cx} {cy})">'
        f'<ellipse cx="{cx}" cy="{cy}" rx="8" ry="9.5" fill="{CREAM}" stroke="{INK}" stroke-width="2.5"/>'
        f'<path d="M{cx - 5},{cy - 3} l10,6 M{cx - 5},{cy + 3} l10,-6" stroke="{RED}" stroke-width="1.2" fill="none"/>'
        "</g>"
    )


def _cloud(x, y, scale, extra_class=""):
    return (
        f'<g class="cloud {extra_class}" transform="translate({x} {y}) scale({scale})">'
        f'<path d="M10,40 a12,12 0 0 1 2,-24 a20,20 0 0 1 36,-8 a16,16 0 0 1 28,8 a12,12 0 0 1 8,24 z" '
        f'fill="{CREAM}" stroke="{INK}" stroke-width="{round(2.5 / scale, 2)}" stroke-linejoin="round"/>'
        "</g>"
    )


def _ladder():
    (x0, y0), (dx, dy) = LADDER_FOOT, LADDER_RISE
    parts = []
    for layer_color, rung_width, rail_width in ((INK, 9, 13), (ORANGE, 4.5, 8)):
        steps = 13
        for i in range(steps):
            t = 0.04 + i * (0.92 / (steps - 1))
            x, y = x0 + dx * t, y0 + dy * t
            parts.append(
                f'<line x1="{x:.1f}" y1="{y:.1f}" x2="{x + LADDER_WIDTH:.1f}" y2="{y:.1f}" '
                f'stroke="{layer_color}" stroke-width="{rung_width}" stroke-linecap="round"/>'
            )
        for offset in (0, LADDER_WIDTH):
            parts.append(
                f'<line x1="{x0 + offset}" y1="{y0}" x2="{x0 + dx + offset}" y2="{y0 + dy}" '
                f'stroke="{layer_color}" stroke-width="{rail_width}" stroke-linecap="round"/>'
            )
    return '<g class="ladder">' + "".join(parts) + "</g>"


def _rays():
    wedges = "".join(
        f'<polygon points="0,0 -10,-150 10,-150" transform="rotate({angle})"/>' for angle in range(0, 360, 30)
    )
    return (
        '<g transform="translate(290 52)"><g class="rays" fill="#ffe27a" fill-opacity="0.38">'
        + wedges + "</g></g>"
    )


def _recipe_card():
    return (
        '<g transform="translate(258 20) rotate(7)"><g class="recipe">'
        f'<rect width="62" height="60" rx="4" fill="{CREAM}" stroke="{INK}" stroke-width="2.5"/>'
        f'<rect x="4" y="4" width="54" height="52" rx="2" fill="none" stroke="{RED}" stroke-width="2" stroke-dasharray="4 3"/>'
        f'<ellipse cx="31" cy="36" rx="19" ry="5" fill="#fff" stroke="{INK}" stroke-width="1.5"/>'
        f'<rect x="17" y="19" width="28" height="15" rx="2" fill="#f2a93b" stroke="{INK}" stroke-width="2"/>'
        f'<path d="M18,24 h26 M18,29 h26" stroke="{RED}" stroke-width="2"/>'
        f'<circle cx="31" cy="18" r="3.2" fill="#4f9a4c" stroke="{INK}" stroke-width="1.2"/>'
        f'<path d="M12,47 h38 M16,52 h30" stroke="{INK}" stroke-width="1.6" stroke-opacity="0.45" stroke-linecap="round"/>'
        "</g></g>"
    )


def _dish():
    steam = "".join(
        f'<path class="steam steam-{i}" d="M{x},64 q-4,-5 0,-10 q4,-5 0,-10" fill="none" '
        f'stroke="{CREAM}" stroke-width="2.5" stroke-linecap="round" stroke-opacity="0.85"/>'
        for i, x in enumerate((11, 21, 31))
    )
    return (
        '<g class="dish">' + steam
        + f'<path d="M4,77 Q8,67 14,72 Q20,65 26,71 Q32,66 38,75 L38,79 L4,79 Z" fill="#f2a93b" stroke="{INK}" stroke-width="2" stroke-linejoin="round"/>'
        + f'<circle cx="13" cy="75" r="1.6" fill="{RED}"/><circle cx="29" cy="74" r="1.6" fill="{RED}"/>'
        + '<circle cx="21" cy="72" r="1.8" fill="#4f9a4c"/>'
        + f'<path d="M2,77 H40 L37,90 Q21,94 5,90 Z" fill="{CREAM}" stroke="{INK}" stroke-width="2.5" stroke-linejoin="round"/>'
        + f'<path d="M5,83 H37" stroke="{RED}" stroke-width="2"/>'
        + "</g>"
    )


def _head():
    dots = "".join(
        f'<circle cx="{x}" cy="{y}" r="1.7" fill="#fff"/>'
        for x, y in ((50, 20), (56, 15), (63, 16), (53, 26), (60, 21), (68, 19), (34, 27), (36, 36))
    )
    curls = "".join(
        f'<circle cx="{x}" cy="{y}" r="{r}" fill="{GREY}" stroke="{INK}" stroke-width="2"/>'
        for x, y, r in ((46, 41, 6.5), (44, 34, 5.5), (51, 46, 5), (57, 25, 4))
    )
    return (
        '<g class="head">'
        f'<path d="M54,44 v8 M60,44 v8" stroke="{SKIN}" stroke-width="7"/>'
        f'<circle cx="58" cy="32" r="14" fill="{SKIN}" stroke="{INK}" stroke-width="2.5"/>'
        f'<path d="M71,30 q5,2 1,5" fill="{SKIN}" stroke="{INK}" stroke-width="2" stroke-linecap="round"/>'
        + curls
        + '<g class="scarf-tails">'
        f'<path d="M45,27 L27,21 L33,31 Z" fill="{RED}" stroke="{INK}" stroke-width="2.2" stroke-linejoin="round"/>'
        f'<path d="M45,30 L29,40 L39,40 Z" fill="{RED}" stroke="{INK}" stroke-width="2.2" stroke-linejoin="round"/>'
        "</g>"
        f'<path d="M44,30 Q43,11 60,11 Q72,12 73,22 Q58,17 47,31 Z" fill="{RED}" stroke="{INK}" stroke-width="2.5" stroke-linejoin="round"/>'
        f'<circle cx="45" cy="29" r="3.4" fill="{RED}" stroke="{INK}" stroke-width="2"/>'
        + dots
        + f'<circle cx="54" cy="39" r="2" fill="#fff" stroke="{INK}" stroke-width="1.2"/>'
        f'<circle cx="62" cy="36" r="3" fill="#ef8f7a" fill-opacity="0.7"/>'
        f'<circle cx="66" cy="29" r="5" fill="#fff" fill-opacity="0.55" stroke="{INK}" stroke-width="1.8"/>'
        f'<path d="M61,28 L52,30" stroke="{INK}" stroke-width="1.6"/>'
        f'<circle cx="67" cy="29" r="1.5" fill="{INK}"/>'
        f'<path class="pose-climb" d="M62,22 L71,24" stroke="{INK}" stroke-width="2.2" stroke-linecap="round"/>'
        f'<path class="pose-shrug" d="M62,21 Q66,18 71,21" fill="none" stroke="{INK}" stroke-width="2.2" stroke-linecap="round"/>'
        f'<path class="pose-climb" d="M66,39 Q69,40 71,38" fill="none" stroke="{INK}" stroke-width="2" stroke-linecap="round"/>'
        f'<ellipse class="pose-shrug" cx="69" cy="39.5" rx="2" ry="2.4" fill="{INK}"/>'
        "</g>"
    )


def _climber(x, y):
    dress = (
        f'<path d="M45,50 Q55,46 66,50 L70,74 Q84,96 82,106 Q56,114 30,106 Q28,92 41,74 Z" '
        f'fill="{RED}" stroke="{INK}" stroke-width="2.5" stroke-linejoin="round"/>'
        f'<path d="M60,60 Q71,62 70,74 Q84,96 82,106 Q70,110 57,110 Q62,86 58,70 Z" '
        f'fill="{CREAM}" stroke="{INK}" stroke-width="2.2" stroke-linejoin="round"/>'
        f'<path d="M40,76 q-9,-6 -9,2 q0,6 9,0 M40,78 q-8,2 -6,9 q5,3 7,-7" fill="{CREAM}" stroke="{INK}" stroke-width="1.8" stroke-linejoin="round"/>'
        f'<circle cx="47" cy="57" r="7" fill="{RED}" stroke="{INK}" stroke-width="2.2"/>'
        f'<circle cx="64" cy="57" r="7" fill="{RED}" stroke="{INK}" stroke-width="2.2"/>'
    )
    legs = (
        '<g class="leg leg-a">' + _limb("M50,100 L49,124")
        + f'<ellipse cx="52" cy="128" rx="9" ry="5" fill="{INK}"/></g>'
        '<g class="leg leg-b">' + _limb("M63,100 Q74,106 70,119")
        + f'<ellipse cx="73" cy="123" rx="9" ry="5" fill="{INK}"/></g>'
    )
    question_marks = "".join(
        f'<text class="pose-shrug qmark qmark-{i}" x="{qx}" y="{qy}" font-size="{size}" '
        f'font-family="{DISPLAY_FONT}" fill="{YELLOW}" stroke="{INK}" stroke-width="1.4" paint-order="stroke">?</text>'
        for i, (qx, qy, size) in enumerate(((78, 10, 24), (93, 22, 17)))
    )
    return (
        f'<g class="climber" transform="translate({x} {y})">'
        f'<g transform="scale({CLIMBER_SCALE})"><g class="climber-bob">'
        '<g class="pose-climb">' + _limb("M63,57 Q79,60 87,43") + _mitt(88, 40, 20) + "</g>"
        + legs + dress
        + '<g class="pose-climb">' + _limb("M46,59 Q35,66 31,79") + "</g>"
        + '<g class="pose-shrug">' + _limb("M46,59 Q27,66 16,53") + _mitt(13, 49, -80)
        + _limb("M65,59 Q82,65 89,52") + _mitt(91, 48, 80) + "</g>"
        + _dish()
        + '<g class="pose-climb">' + _mitt(31, 85, -30) + "</g>"
        + _head() + question_marks
        + "</g></g></g>"
    )


def duster_svg():
    """Grandma with a feather duster, for Reader view's AI tidy card."""
    feathers = "".join(
        f'<ellipse cx="{x}" cy="{y}" rx="4.6" ry="10" transform="rotate({angle} {x} {y})" '
        f'fill="{color}" stroke="{INK}" stroke-width="2"/>'
        for x, y, angle, color in ((100, 17, 15, ORANGE), (112, 23, 75, ORANGE), (104, 13, 35, YELLOW), (111, 17, 55, YELLOW))
    )
    star = "M0,-6 L1.6,-1.6 L6,0 L1.6,1.6 L0,6 L-1.6,1.6 L-6,0 L-1.6,-1.6 Z"
    sparkles = "".join(
        f'<g transform="translate({x} {y}) scale({scale})"><path class="sparkle sparkle-{i}" d="{star}" '
        f'fill="{YELLOW}" stroke="{INK}" stroke-width="{round(1.4 / scale, 2)}" stroke-linejoin="round"/></g>'
        for i, (x, y, scale) in enumerate(((124, 8, 1.0), (88, 9, 0.7), (126, 34, 0.8)))
    )
    return (
        '<svg class="tidy-art" viewBox="22 0 112 78" aria-hidden="true" focusable="false" '
        'xmlns="http://www.w3.org/2000/svg">'
        f'<path d="M34,80 Q35,55 57,52 Q78,55 80,80 Z" fill="{RED}" stroke="{INK}" stroke-width="2.5" stroke-linejoin="round"/>'
        '<g class="duster">'
        f'<path d="M86,46 L103,24" stroke="{INK}" stroke-width="6" stroke-linecap="round"/>'
        f'<path d="M86,46 L103,24" stroke="{ORANGE}" stroke-width="2.5" stroke-linecap="round"/>'
        + feathers + "</g>"
        + _limb("M70,64 Q84,66 86,49") + _mitt(86, 46, 35)
        + _head() + sparkles
        + "</svg>"
    )


def scene_svg(fraction=0.0):
    """The whole scene. `fraction` is how far up the climber starts (0..1)."""
    x, y = climber_position(fraction)
    return (
        '<svg class="scene-art" viewBox="0 0 360 420" aria-hidden="true" focusable="false" '
        'xmlns="http://www.w3.org/2000/svg">'
        + _rays()
        + _cloud(14, 150, 0.85, "drift-a")
        + _cloud(238, 236, 0.7, "drift-b")
        + _cloud(120, 44, 0.5, "drift-b")
        + f'<path d="M0,420 V388 Q70,362 150,388 T360,372 V420 Z" fill="#4f9a4c" stroke="{INK}" stroke-width="2.5"/>'
        + f'<path d="M0,420 V404 Q110,388 220,404 T360,398 V420 Z" fill="#7dbb5c" stroke="{INK}" stroke-width="2.5"/>'
        + _recipe_card()
        + _ladder()
        + _cloud(190, 58, 1.7, "summit-cloud")
        + _climber(x, y)
        + "</svg>"
    )


# Shared by the home page's loading view and the error page. Colours come from
# custom properties the page defines on <body> (see PALETTE_CSS).
PALETTE_CSS = """
body {
    --paper: #fdf3dc; --card: #fffaf0; --ink: #1d2b30; --muted: #586469;
    --line: #1d2b30; --shadow: #1d2b30; --accent: #ad2a20; --field: #fff;
    --red: #d1382c; --yellow: #ffcf33; --orange: #ee7b22; --dots: rgba(29, 43, 48, 0.07);
    --display: Bangers, Impact, Haettenschweiler, 'Arial Narrow Bold', sans-serif;
}
body.dark-mode {
    --paper: #0f2428; --card: #16343a; --ink: #fbf1d6; --muted: #b4c4c2;
    --line: #fbf1d6; --shadow: #050d0f; --accent: #ffcf33; --field: #0b1c1f;
    --dots: rgba(251, 241, 214, 0.05);
}
"""

SCENE_CSS = """
.scene {
    position: relative;
    border: 3px solid var(--line);
    border-radius: 18px;
    overflow: hidden;
    box-shadow: 6px 6px 0 var(--shadow);
    background:
        radial-gradient(circle at 80% 12%, rgba(255, 226, 122, 0.95) 0, rgba(255, 207, 51, 0.5) 14%, rgba(255, 207, 51, 0) 40%),
        linear-gradient(180deg, #134a50 0%, #1f6f73 55%, #4a9b8b 100%);
}
body.dark-mode .scene {
    background:
        radial-gradient(circle at 80% 12%, rgba(255, 226, 122, 0.8) 0, rgba(255, 207, 51, 0.3) 14%, rgba(255, 207, 51, 0) 38%),
        linear-gradient(180deg, #071d22 0%, #0f3b41 60%, #1d5a58 100%);
}
.scene::before {
    content: "";
    position: absolute;
    inset: 0;
    background-image: radial-gradient(rgba(255, 255, 255, 0.17) 1px, transparent 1.5px);
    background-size: 7px 7px;
    pointer-events: none;
}
.scene-art { display: block; width: 100%; height: auto; position: relative; }
.scene .pose-shrug { display: none; }
.scene.is-error .pose-shrug { display: inline; }
.scene.is-error .pose-climb { display: none; }
.scene.is-error .dish { transform: translate(-10px, -46px); }
.scene.is-error .steam { display: none; }
.scene.is-error .rays { fill-opacity: 0.12; }

.altimeter {
    position: absolute;
    left: 12px;
    top: 12px;
    z-index: 2;
    padding: 6px 12px 5px;
    background: #1d2b30;
    color: #ffcf33;
    border: 2px solid #fbf1d6;
    border-radius: 10px;
    box-shadow: 3px 3px 0 rgba(0, 0, 0, 0.35);
    transform: rotate(-3deg);
    line-height: 1;
    text-align: left;
}
.altimeter small {
    display: block;
    margin-bottom: 3px;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: #fbf1d6;
}
.altimeter b {
    font-family: var(--display);
    font-size: 30px;
    font-weight: 400;
    letter-spacing: 0.04em;
    font-variant-numeric: tabular-nums;
}

@media (prefers-reduced-motion: no-preference) {
    .scene .rays { transform-box: fill-box; transform-origin: center; animation: ladder-rays 60s linear infinite; }
    .scene .drift-a { animation: ladder-drift 9s ease-in-out infinite alternate; }
    .scene .drift-b { animation: ladder-drift 13s ease-in-out infinite alternate-reverse; }
    .scene .steam { animation: ladder-steam 1.8s ease-in-out infinite; }
    .scene .steam-1 { animation-delay: 0.6s; }
    .scene .steam-2 { animation-delay: 1.2s; }
    .scene .scarf-tails { transform-box: fill-box; transform-origin: right center; animation: ladder-flap 0.7s ease-in-out infinite alternate; }
    .scene.is-climbing .climber-bob { animation: ladder-bob 0.44s ease-in-out infinite alternate; }
    .scene.is-climbing .leg-a { animation: ladder-step 0.88s ease-in-out infinite; }
    .scene.is-climbing .leg-b { animation: ladder-step 0.88s ease-in-out -0.44s infinite; }
    .scene.is-error .climber-bob { animation: ladder-shrug 1.6s ease-in-out infinite; }
    .scene.is-error .qmark { animation: ladder-wonder 1.6s ease-in-out infinite; }
    .scene.is-error .qmark-1 { animation-delay: 0.3s; }
    .scene.is-done .recipe { transform-box: fill-box; transform-origin: center; animation: ladder-cheer 0.5s ease-in-out infinite alternate; }
    .scene.is-done .climber-bob { animation: ladder-hop 0.36s ease-in-out infinite alternate; }
}
@keyframes ladder-rays { to { transform: rotate(360deg); } }
@keyframes ladder-drift { from { translate: -8px 0; } to { translate: 10px 0; } }
@keyframes ladder-steam {
    0% { opacity: 0; translate: 0 5px; }
    40% { opacity: 1; }
    100% { opacity: 0; translate: 0 -7px; }
}
@keyframes ladder-flap { from { transform: rotate(-9deg); } to { transform: rotate(10deg); } }
@keyframes ladder-bob { from { translate: 0 1.5px; } to { translate: 0 -2.5px; } }
@keyframes ladder-step { 0%, 100% { translate: 0 0; } 50% { translate: 0 -7px; } }
@keyframes ladder-shrug { 0%, 100% { translate: 0 0; } 50% { translate: 0 -3px; } }
@keyframes ladder-wonder { 0%, 100% { opacity: 0.55; translate: 0 2px; } 50% { opacity: 1; translate: 0 -2px; } }
@keyframes ladder-cheer { from { transform: rotate(-5deg) scale(1); } to { transform: rotate(5deg) scale(1.12); } }
@keyframes ladder-hop { from { translate: 0 0; } to { translate: 0 -6px; } }
"""

ERROR_TEMPLATE = """<!DOCTYPE html>
<html lang="{{ lang }}">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="referrer" content="no-referrer">
<meta name="robots" content="noindex">
<title>{{ error_title }} · 9001ft Ladder</title>
<link rel="icon" href="/favicon.ico" type="image/png">
<style nonce="{{ nonce }}">
{{ font_face_css | safe }}
* { box-sizing: border-box; }
{{ palette_css | safe }}
body {
    margin: 0;
    min-height: 100vh;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 24px 16px;
    background-color: var(--paper);
    background-image: radial-gradient(var(--dots) 1px, transparent 1.5px);
    background-size: 9px 9px;
    color: var(--ink);
    font: 16px/1.55 -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Open Sans', sans-serif;
}
main { width: 100%; max-width: 460px; text-align: center; }
.scene { max-width: 300px; margin: 0 auto 26px; }
{{ scene_css | safe }}
h1 { font-family: var(--display); font-weight: 400; font-size: 1.7rem; letter-spacing: 0.03em; line-height: 1.15; margin: 0 0 12px; }
.message {
    margin: 0 0 22px;
    padding: 14px 16px;
    background: var(--card);
    border: 3px solid var(--line);
    border-radius: 14px;
    box-shadow: 4px 4px 0 var(--shadow);
    text-align: left;
    overflow-wrap: anywhere;
}
.btn {
    display: inline-block;
    padding: 10px 22px;
    background: var(--yellow);
    color: #1d2b30;
    border: 3px solid var(--line);
    border-radius: 12px;
    box-shadow: 4px 4px 0 var(--shadow);
    font-family: var(--display);
    font-size: 1.3rem;
    letter-spacing: 0.05em;
    text-decoration: none;
}
.btn:hover { transform: translate(-1px, -1px); box-shadow: 5px 5px 0 var(--shadow); }
.btn:active { transform: translate(3px, 3px); box-shadow: 1px 1px 0 var(--shadow); }
.btn:focus-visible { outline: 3px solid var(--accent); outline-offset: 3px; }
@media (prefers-reduced-motion: no-preference) { .btn { transition: transform 0.12s, box-shadow 0.12s; } }
</style>
</head>
<body>
<main>
  <div class="scene is-error">
    <div class="altimeter" aria-hidden="true"><small>{{ error_stuck_label }}</small><b>{{ stuck_at }}</b></div>
    {{ scene_svg | safe }}
  </div>
  <h1>{{ error_title }}</h1>
  <p class="message" id="error-message">{{ message }}</p>
  <a class="btn" href="/">{{ retry_button }}</a>
</main>
<script nonce="{{ nonce }}">
  // Follow the home page's light or dark choice.
  (function () {
    var theme = null;
    try { theme = localStorage.getItem('theme'); } catch (e) {}
    if (!theme) theme = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    if (theme === 'dark') document.body.classList.add('dark-mode');
  })();
</script>
</body>
</html>
"""


def content_security_policy(nonce):
    return (
        f"default-src 'none'; img-src 'self'; font-src 'self'; style-src 'nonce-{nonce}'; script-src 'nonce-{nonce}'; "
        "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    )
