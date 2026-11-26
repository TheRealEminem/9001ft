"""AI tidy: fix the structure of a Reader view article that came out messy.

The model never writes article text. It sees the article as numbered blocks
and answers with a plan: which blocks are headings, list items, quotes or
captions, which neighbouring blocks are one paragraph split apart, and which
are clutter (ads, share prompts, "related" links). The page is then rebuilt
from the original blocks, so the wording can't change. Dropping is capped so
a bad plan can't remove real content, and a final check compares the words
before and after.

Which AI it uses comes from ai_settings (the /settings page, or TIDY_*
environment variables): a local Ollama model, Anthropic Claude through the
official SDK, or any OpenAI-compatible chat API.
"""

import json
import re
import time
from dataclasses import dataclass, field
from html import escape

import anthropic
import requests
from bs4 import BeautifulSoup, NavigableString, Tag

import ai_settings
from ai_settings import Backend  # noqa: F401  (re-exported for callers and tests)

LEAF_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "pre", "figcaption", "dt", "dd"}
ATOMIC_TAGS = {"table", "img", "video", "audio", "iframe", "picture", "hr"}
CONTAINER_TAGS = {"div", "section", "article", "main", "header", "footer", "aside", "ul", "ol", "dl",
                  "figure", "blockquote", "nav", "body", "html", "[document]"}
BLOCK_TAGS = LEAF_TAGS | ATOMIC_TAGS | CONTAINER_TAGS

ROLES = {"p", "h2", "h3", "ul", "ol", "quote", "caption", "pre", "keep", "drop"}
MAX_BLOCKS = 600
PREVIEW_CHARS = 280
MAX_HEADING_WORDS = 25
MAX_DROP_WORDS_PER_BLOCK = 60
MAX_DROP_SHARE = 0.25
REQUEST_TIMEOUT = 240

SYSTEM_PROMPT = """You fix the layout of a news or blog article that was pulled out of a web page and came out messy.
You never write, rewrite, summarize or translate any text. You only decide what each numbered block is.

You get the blocks in order, one per line: [id] <current tag> text. Long text is cut off with "…".
Answer with JSON only, no other words:
{"blocks": [{"ids": [3], "as": "h2"}, {"ids": [4, 5], "as": "p"}, {"ids": [9], "as": "drop"}]}

"as" is one of:
- "h2" or "h3": a section heading (short, no full stop at the end)
- "p": a paragraph. Put several ids together only when they are ONE sentence or paragraph split apart.
- "ul" or "ol": an item of a bulleted or numbered list (one entry per item)
- "quote": a quoted passage or pull quote
- "caption": a photo or chart caption or credit
- "pre": code or preformatted text
- "drop": clutter that is not part of the article: ads, "Advertisement", "[edit]" links, share or follow buttons, newsletter or
  sign-up prompts, cookie notices, "related"/"read more"/"most popular" link lists, navigation, comment counts.
  Never drop article content, the byline or dates in the article body.

Rules: ids in each entry must be next to each other, in order. Use each id at most once.
Leave out ids that are already fine. When unsure, leave the block out of your answer."""


RETRY_REMINDER = "\n\nReply with the JSON object only, exactly in the format described, and nothing else."


class TidyError(Exception):
    """Something the person should see, in plain words."""


@dataclass
class Block:
    id: int
    tag: str
    html: str
    text: str
    atomic: bool = False
    list_type: str = ""
    words: int = field(init=False)

    def __post_init__(self):
        self.words = len(self.text.split())


# --- configuration --------------------------------------------------------

def backends():
    return ai_settings.backends()


def enabled():
    return bool(backends())


# --- splitting the article into blocks -------------------------------------

def _is_blockish(node):
    return isinstance(node, Tag) and (node.name in BLOCK_TAGS or bool(node.find(list(BLOCK_TAGS))))


def split_blocks(fragment_html):
    """Flatten the article into its smallest block-level pieces, in order."""
    soup = BeautifulSoup(fragment_html, "html.parser")
    blocks = []

    def add(tag, html, text, atomic=False, list_type=""):
        text = " ".join(text.split())
        if text or atomic:
            blocks.append(Block(len(blocks) + 1, tag, html.strip(), text, atomic, list_type))

    def walk(node, list_type=""):
        inline = []

        def flush():
            if inline:
                html = "".join(str(part) for part in inline)
                add("text", html, BeautifulSoup(html, "html.parser").get_text(" "))
                inline.clear()

        for child in list(node.children):
            if isinstance(child, NavigableString):
                if type(child) is NavigableString:
                    inline.append(escape(str(child)) if str(child).strip() else " ")
                continue
            if not isinstance(child, Tag):
                continue
            if not _is_blockish(child):
                inline.append(child)
                continue
            flush()
            if child.name in ATOMIC_TAGS:
                label = child.get("alt", "") if child.name == "img" else child.get_text(" ")
                add(child.name, str(child), label, atomic=True)
            # A leaf holding other blocks (readability sometimes wraps a heading
            # and its "[edit]" link in a <p>) is split up instead.
            elif child.name in LEAF_TAGS and not child.find(list(BLOCK_TAGS - {"img"})):
                add(child.name, child.decode_contents(), child.get_text(" "), list_type=list_type if child.name == "li" else "")
            elif child.name in ("ul", "ol"):
                walk(child, child.name)
            elif child.name == "blockquote" and not child.find(list(BLOCK_TAGS)):
                add("blockquote", child.decode_contents(), child.get_text(" "))
            else:
                walk(child, list_type)
        flush()

    walk(soup)
    return blocks


def _preview(block):
    if block.atomic:
        if block.tag == "img":
            return f'[{block.id}] <img> {block.text}'.rstrip()
        return f"[{block.id}] <{block.tag}>"
    text = block.text if len(block.text) <= PREVIEW_CHARS else block.text[:PREVIEW_CHARS].rstrip() + "…"
    return f"[{block.id}] <{block.tag}> {text}"


# --- talking to the model --------------------------------------------------

class Unreachable(Exception):
    """The service couldn't be reached at all, so a fallback may be tried."""


# Server-side refusal fallbacks (recommended for these Claude models): if a
# safety check declines, the API retries on a suitable model in the same call.
FALLBACK_MODELS = ("claude-opus-5", "claude-sonnet-5-5", "claude-fable-5")


def _chat_openai(backend, system, user, max_tokens):
    headers = {"Content-Type": "application/json"}
    if backend.key:
        headers["Authorization"] = f"Bearer {backend.key}"
    payload = {
        "model": backend.model,
        "temperature": 0,
        "max_tokens": max_tokens,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    if backend.reasoning_effort:
        payload["reasoning_effort"] = backend.reasoning_effort
    try:
        response = requests.post(backend.base + "/chat/completions", json=payload, headers=headers,
                                 timeout=(4, REQUEST_TIMEOUT))
    except requests.exceptions.Timeout as error:
        if isinstance(error, requests.exceptions.ConnectTimeout):
            raise Unreachable(backend.label)
        raise TidyError("the AI took too long to answer")
    except requests.exceptions.ConnectionError:
        raise Unreachable(backend.label)
    if response.status_code in (401, 403):
        raise TidyError("the AI service refused the API key")
    if response.status_code >= 400:
        raise TidyError(f"the AI service answered with error {response.status_code}")
    try:
        return response.json()["choices"][0]["message"].get("content") or ""
    except (ValueError, KeyError, IndexError, TypeError):
        raise TidyError("the AI service sent an answer this app couldn't read")


def _chat_anthropic(backend, system, user, max_tokens):
    client = anthropic.Anthropic(api_key=backend.key, timeout=REQUEST_TIMEOUT, max_retries=1)
    # Laying out blocks is a light task: low effort keeps it quick. "none" isn't
    # an effort level on Claude, so it maps to low.
    effort = backend.reasoning_effort if backend.reasoning_effort in ("medium", "high") else "low"
    params = dict(model=backend.model, max_tokens=max_tokens, system=system,
                  messages=[{"role": "user", "content": user}], output_config={"effort": effort})
    try:
        if backend.model.startswith(FALLBACK_MODELS):
            response = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"],
                                                   fallbacks="default", **params)
        else:
            response = client.messages.create(**params)
    except anthropic.APITimeoutError:
        raise TidyError("the AI took too long to answer")
    except anthropic.APIConnectionError:
        raise Unreachable(backend.label)
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError):
        raise TidyError("Anthropic refused the API key")
    except anthropic.NotFoundError:
        raise TidyError(f"Anthropic doesn't know the model {backend.model}")
    except anthropic.RateLimitError:
        raise TidyError("Anthropic is rate limiting this key; try again shortly")
    except anthropic.APIStatusError as error:
        raise TidyError(f"Anthropic answered with error {error.status_code}")
    if response.stop_reason == "refusal":
        raise TidyError("the AI declined to work on this page")
    return "".join(block.text for block in response.content if block.type == "text")


def chat(backend, system, user, max_tokens=16000):
    if backend.kind == "anthropic":
        return _chat_anthropic(backend, system, user, max_tokens)
    return _chat_openai(backend, system, user, max_tokens)


def first_answer(available, system, user, max_tokens=16000):
    """Ask each service in turn until one can be reached. Returns (answer, backend)."""
    unreachable = []
    for backend in available:
        try:
            return chat(backend, system, user, max_tokens), backend
        except Unreachable:
            unreachable.append(backend.label)
    raise TidyError("couldn't reach the AI service (" + ", ".join(unreachable) + ")")


def parse_plan(text):
    """Pull the plan out of the model's answer, tolerating code fences and chatter."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    start, end = text.find("{"), text.rfind("}")
    candidates = [text[start:end + 1]] if start != -1 and end > start else []
    list_start, list_end = text.find("["), text.rfind("]")
    if list_start != -1 and list_end > list_start:
        candidates.append(text[list_start:list_end + 1])
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except ValueError:
            continue
        entries = data.get("blocks") if isinstance(data, dict) else data
        if isinstance(entries, list):
            return entries
    raise TidyError("the AI's answer wasn't in the expected format")


# --- applying the plan -----------------------------------------------------

def apply_plan(blocks, entries):
    """Validate the plan against the blocks and return (groups, stats).

    A group is (role, [blocks]). Anything invalid in the plan is ignored, so
    the worst a bad plan can do is leave the article as it was."""
    by_id = {block.id: block for block in blocks}
    total_words = sum(block.words for block in blocks) or 1
    assigned = {}
    used = set()
    dropped_words = 0

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        role = str(entry.get("as", "")).lower().strip()
        ids = entry.get("ids")
        if isinstance(ids, int):
            ids = [ids]
        if role not in ROLES or not isinstance(ids, list) or not ids:
            continue
        try:
            ids = sorted(int(i) for i in ids)
        except (TypeError, ValueError):
            continue
        if any(i not in by_id or i in used for i in ids) or ids != list(range(ids[0], ids[0] + len(ids))):
            continue
        members = [by_id[i] for i in ids]
        if any(member.atomic for member in members):
            continue  # images, tables and media always stay as they are
        words = sum(member.words for member in members)
        if role in ("h2", "h3") and (len(ids) > 1 or words > MAX_HEADING_WORDS):
            continue
        if role == "drop":
            if words > MAX_DROP_WORDS_PER_BLOCK or (dropped_words + words) / total_words > MAX_DROP_SHARE:
                continue
            dropped_words += words
        if role in ("ul", "ol") and len(ids) > 1:
            continue
        used.update(ids)
        assigned[ids[0]] = (role, members)

    groups = []
    stats = {"headings": 0, "joined": 0, "lists": 0, "dropped_words": dropped_words, "dropped_blocks": 0}
    skip = set()
    for block in blocks:
        if block.id in skip:
            continue
        if block.id in assigned:
            role, members = assigned[block.id]
            skip.update(member.id for member in members)
            if role == "keep":
                role = _default_role(block)
            groups.append((role, members))
            if role in ("h2", "h3"):
                stats["headings"] += 1
            if role == "drop":
                stats["dropped_blocks"] += 1
            stats["joined"] += len(members) - 1
        else:
            groups.append((_default_role(block), [block]))
    return groups, stats


def _default_role(block):
    if block.atomic:
        return "atomic"
    if block.tag == "li":
        return block.list_type or "ul"
    if block.tag in ("h1", "h2"):
        return "h2"
    if block.tag in ("h3", "h4", "h5", "h6"):
        return "h3"
    if block.tag == "blockquote":
        return "quote"
    if block.tag == "figcaption":
        return "caption"
    if block.tag == "pre":
        return "pre"
    return "p"


def render(groups):
    html, open_list, lists = [], None, 0

    def close_list():
        nonlocal open_list
        if open_list:
            html.append(f"</{open_list}>")
            open_list = None

    for role, members in groups:
        if role == "drop":
            continue
        inner = " ".join(member.html for member in members)
        if role in ("ul", "ol"):
            if open_list != role:
                close_list()
                html.append(f"<{role}>")
                open_list = role
                lists += 1
            html.append(f"<li>{inner}</li>")
            continue
        close_list()
        if role == "atomic":
            html.append(inner)
        elif role == "quote":
            html.append(f"<blockquote><p>{inner}</p></blockquote>")
        elif role == "caption":
            html.append(f'<p class="caption">{inner}</p>')
        elif role == "pre":
            html.append(f"<pre>{inner}</pre>")
        else:
            html.append(f"<{role}>{inner}</{role}>")
    close_list()
    return "\n".join(html), lists


def _words(html):
    return BeautifulSoup(html, "html.parser").get_text(" ").split()


def tidy(fragment_html):
    """Return {"html", "stats", "model", "seconds"} or raise TidyError."""
    available = backends()
    if not available:
        raise TidyError("AI tidy isn't set up on this server")
    blocks = split_blocks(fragment_html)
    if len(blocks) < 2:
        raise TidyError("there's not enough here to tidy")
    if len(blocks) > MAX_BLOCKS:
        raise TidyError("this page is too long to tidy")

    started = time.time()
    user = "\n".join(_preview(block) for block in blocks)
    answer, used = first_answer(available, SYSTEM_PROMPT, user)
    try:
        entries = parse_plan(answer)
    except TidyError:
        # Models occasionally wrap or garble the JSON. Ask once more, firmly,
        # from the service that answered, before giving up.
        answer, used = first_answer([used], SYSTEM_PROMPT, user + RETRY_REMINDER)
        entries = parse_plan(answer)

    groups, stats = apply_plan(blocks, entries)
    html, stats["lists"] = render(groups)

    # By construction only dropped blocks lose words. Check it anyway.
    kept = [word for role, members in groups if role != "drop" for member in members for word in _words(member.html)]
    if _words(html) != kept:
        raise TidyError("the tidied page didn't match the original, so it was thrown away")

    return {"html": html, "stats": stats, "model": used.label, "seconds": round(time.time() - started, 1)}


def test_connection(ai):
    """Send a tiny request with these settings. Returns {"model", "seconds", "reply"}."""
    available = ai_settings.backends_for(ai)
    if not available:
        raise TidyError("these settings aren't complete yet")
    started = time.time()
    answer, used = first_answer(available, "Reply with the single word OK.", "Test", max_tokens=2048)
    return {"model": used.label, "seconds": round(time.time() - started, 1), "reply": (answer or "").strip()[:60]}
