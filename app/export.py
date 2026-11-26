"""Export the article shown in Reader view as Markdown or as an EPUB e-book.

The Reader page sends the article HTML it is showing (so a tidied article is
exported as tidied). portable.py re-sanitizes it with reader.clean_fragment
before it gets here; this module only converts. Standard library plus
BeautifulSoup, which the Reader view already uses.
"""

import re
import unicodedata
import uuid
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from urllib.parse import quote
from xml.sax.saxutils import escape as xml_escape, quoteattr

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

FORMATS = {
    "markdown": ("text/markdown", "md"),  # Flask adds "; charset=utf-8"
    "epub": ("application/epub+zip", "epub"),
}

DEFAULT_LABELS = {
    "source": "Source",
    "by": "By",
    "published": "Published",
    "images_note": "Images aren't included in this e-book; each one is a link to the original.",
    "contents": "Contents",
    "image": "Image",
    "video": "Video",
    "audio": "Audio",
    "embed": "Embedded content",
}

LANG_RE = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{1,8}){0,4}$")


def safe_filename(title, extension):
    """An ASCII-only file name such as 'how-tall-can-a-ladder-get.md'."""
    ascii_title = unicodedata.normalize("NFKD", title or "").encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^A-Za-z0-9]+", "-", ascii_title).strip("-").lower()[:80].rstrip("-")
    return f"{slug or 'article'}.{extension}"


def _text(value, limit=500):
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def _http_url(value):
    value = (value or "").strip()
    return value if re.match(r"^https?://", value, re.I) else ""


def _first_src(tag):
    src = (tag.get("src") or "").strip()
    if not src and tag.get("srcset"):
        src = tag["srcset"].strip().split(",")[0].strip().split(" ")[0]
    if not src:
        source = tag.find("source", src=True)
        src = source["src"].strip() if source else ""
    return src


# --------------------------------------------------------------------------
# Markdown
# --------------------------------------------------------------------------

BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "details", "dd", "div", "dl", "dt", "fieldset",
    "figcaption", "figure", "footer", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li",
    "main", "nav", "ol", "p", "pre", "section", "summary", "table", "ul", "video", "audio", "iframe",
}
BREAK = "\x00br\x00"
MD_SPECIAL = re.compile(r"([\\`*_\[\]<>])")
# Text that would turn into a heading, list or rule at the start of a line.
LINE_START = re.compile(r"^(#{1,6}|[-+=])(?=\s|$)|^(\d+)([.)])(?=\s|$)")


class _ListBlock(str):
    """A rendered list, so a list item can keep its nested lists tight."""


def _md_escape(text):
    return MD_SPECIAL.sub(r"\\\1", text)


def _md_url(url):
    return url.replace(" ", "%20").replace("(", "%28").replace(")", "%29")


def _md_line_start(match):
    if match.group(1):
        return "\\" + match.group(1)
    return match.group(2) + "\\" + match.group(3)


def _md_paragraph(text):
    text = re.sub(r" {2,}", " ", text)
    lines = [line.strip() for line in text.split(BREAK)]
    lines = [LINE_START.sub(_md_line_start, line) for line in lines]
    return "  \n".join(line for line in lines if line).strip()


def _md_wrap(marker, inner):
    if not inner.strip():
        return inner
    lead = inner[: len(inner) - len(inner.lstrip())]
    trail = inner[len(inner.rstrip()):]
    return f"{lead}{marker}{inner.strip()}{marker}{trail}"


def _md_code(text):
    ticks = "`"
    while ticks in text:
        ticks += "`"
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{ticks}{pad}{text}{pad}{ticks}"


def _md_inline(node, labels):
    if isinstance(node, Comment):
        return ""
    if isinstance(node, NavigableString):
        return _md_escape(re.sub(r"\s+", " ", str(node)))
    name = node.name
    if name == "br":
        return BREAK
    if name in ("source", "track", "wbr"):
        return ""
    if name == "img":
        src = _first_src(node)
        alt = _md_escape(_text(node.get("alt", "")))
        if not src or src.startswith("data:"):
            return alt
        title = _text(node.get("title", "")).replace('"', "'")
        return f"![{alt}]({_md_url(src)}" + (f' "{title}"' if title else "") + ")"
    if name in ("code", "kbd", "samp", "tt"):
        text = re.sub(r"\s+", " ", node.get_text())
        return _md_code(text) if text.strip() else text
    inner = "".join(_md_inline(child, labels) for child in node.children)
    if name in ("strong", "b"):
        return _md_wrap("**", inner)
    if name in ("em", "i", "cite", "var"):
        return _md_wrap("*", inner)
    if name in ("del", "s", "strike"):
        return _md_wrap("~~", inner)
    if name == "q":
        return f"\u201c{inner}\u201d"
    if name == "a":
        href = (node.get("href") or "").strip()
        if not href:
            return inner
        if not inner.strip():
            return f"<{_md_url(href)}>"
        return f"[{inner.strip()}]({_md_url(href)})"
    return inner


def _md_blocks(node, labels):
    out = []
    run = []

    def flush():
        text = _md_paragraph("".join(run))
        if text:
            out.append(text)
        run.clear()

    for child in node.children:
        if isinstance(child, Tag) and child.name in BLOCK_TAGS:
            flush()
            out.extend(block for block in _md_block(child, labels) if block)
        else:
            run.append(_md_inline(child, labels))
    flush()
    return out


def _md_list(tag, labels):
    ordered = tag.name == "ol"
    try:
        number = int(tag.get("start", 1))
    except ValueError:
        number = 1
    items = []
    lis = tag.find_all("li", recursive=False)
    if not lis:
        return _ListBlock("\n\n".join(_md_blocks(tag, labels)))
    for li in lis:
        marker = f"{number}. " if ordered else "- "
        number += 1
        parts = _md_blocks(li, labels)
        body = ""
        for part in parts:
            if not body:
                body = part
            else:
                body += ("\n" if isinstance(part, _ListBlock) else "\n\n") + part
        indent = " " * len(marker)
        lines = body.split("\n") if body else [""]
        items.append(marker + lines[0] + "".join("\n" + (indent + line if line else "") for line in lines[1:]))
    return _ListBlock("\n".join(items))


def _md_table(tag, labels):
    rows = []
    for tr in tag.find_all("tr"):
        if tr.find_parent("table") is not tag:
            continue
        cells = [_md_paragraph("".join(_md_inline(c, labels) for c in cell.children)).replace("  \n", " ")
                 for cell in tr.find_all(["td", "th"], recursive=False)]
        if cells:
            rows.append([cell.replace("\n", " ").replace("|", "\\|") for cell in cells])
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    lines = ["| " + " | ".join(rows[0]) + " |", "|" + "|".join([" --- "] * width) + "|"]
    lines += ["| " + " | ".join(row) + " |" for row in rows[1:]]
    return "\n".join(lines)


def _md_block(tag, labels):
    name = tag.name
    if name in ("h1", "h2", "h3", "h4", "h5", "h6"):
        text = _md_paragraph("".join(_md_inline(c, labels) for c in tag.children)).replace("  \n", " ")
        return ["#" * int(name[1]) + " " + text] if text else []
    if name == "hr":
        return ["---"]
    if name == "pre":
        code = tag.get_text().strip("\n")
        if not code.strip():
            return []
        fence = "```"
        while fence in code:
            fence += "`"
        return [f"{fence}\n{code}\n{fence}"]
    if name in ("ul", "ol"):
        return [_md_list(tag, labels)]
    if name == "li":  # a stray item outside a list
        return ["- " + "\n\n".join(_md_blocks(tag, labels))]
    if name == "blockquote":
        inner = "\n\n".join(_md_blocks(tag, labels))
        return ["\n".join("> " + line if line else ">" for line in inner.split("\n"))] if inner else []
    if name == "table":
        return [_md_table(tag, labels)]
    if name in ("video", "audio", "iframe"):
        src = _first_src(tag)
        label = labels["embed" if name == "iframe" else name]
        return [f"[{label}]({_md_url(src)})"] if _http_url(src) else []
    if name == "dt":
        return [_md_wrap("**", " ".join(_md_blocks(tag, labels)))]
    return _md_blocks(tag, labels)


def to_markdown(meta, fragment, labels=None):
    """meta: title, url, site, byline, date. fragment: sanitized article soup."""
    labels = {**DEFAULT_LABELS, **(labels or {})}
    lines = ["# " + _md_paragraph(_md_escape(meta["title"]) or "Untitled"), ""]
    url = _http_url(meta.get("url"))
    if url:
        lines.append(f"{labels['source']}: [{_md_escape(meta.get('site') or url)}]({_md_url(url)})  ")
    if meta.get("byline"):
        lines.append(f"{labels['by']}: {_md_escape(meta['byline'])}  ")
    if meta.get("date"):
        lines.append(f"{labels['published']}: {_md_escape(meta['date'])}  ")
    if lines[-1]:
        lines[-1] = lines[-1].rstrip()
        lines.append("")
    lines += ["---", ""]
    body = "\n\n".join(_md_blocks(fragment, labels))
    return "\n".join(lines) + "\n" + body + "\n"


# --------------------------------------------------------------------------
# EPUB 3
# --------------------------------------------------------------------------

XHTML_INLINE = {"a", "abbr", "b", "cite", "code", "del", "em", "i", "ins", "kbd", "mark", "q", "s",
                "samp", "small", "span", "strong", "sub", "sup", "u", "var"}
XHTML_BLOCK = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li", "blockquote", "pre",
               "figure", "figcaption", "dl", "dt", "dd", "div", "section", "table", "caption",
               "thead", "tbody", "tfoot", "tr", "td", "th", "hr", "br"}
XHTML_VOID = {"br", "hr"}
# Elements whose content model is phrasing only: block children make them invalid.
PHRASING_ONLY = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "pre"} | XHTML_INLINE
FLOW_CHILDREN = XHTML_BLOCK - {"br", "li", "dt", "dd", "caption", "thead", "tbody", "tfoot", "tr", "td", "th"}
INVALID_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")


def _href(url):
    """Percent-encode spaces and other characters a strict EPUB checker rejects."""
    return quote(url, safe=":/?#[]@!$&'()*+,;=%~")


def _has_block(node):
    return any(isinstance(d, Tag) and d.name in FLOW_CHILDREN for d in node.descendants)


def _x(text):
    return xml_escape(INVALID_XML.sub("", text or ""))


def _xa(text):
    return quoteattr(INVALID_XML.sub("", text or ""))


class _Chapter:
    def __init__(self, labels):
        self.labels = labels
        self.headings = []
        self.had_images = False

    def attrs(self, tag, name):
        """Attributes worth keeping, for the element as written out (name)."""
        if name != tag.name:
            return ""
        out = ""
        if name == "a":
            href = (tag.get("href") or "").strip()
            if re.match(r"^(https?|mailto):", href, re.I):
                out += " href=" + _xa(_href(href))
        if name in ("td", "th"):
            for attr in ("colspan", "rowspan"):
                if str(tag.get(attr, "")).isdigit() and int(tag[attr]) > 0:
                    out += f' {attr}="{int(tag[attr])}"'
        if name == "ol" and re.match(r"^-?\d+$", str(tag.get("start", ""))):
            out += f' start="{int(tag["start"])}"'
        if name == "abbr" and tag.get("title"):
            out += " title=" + _xa(_text(tag["title"]))
        return out

    def media_link(self, tag, inside):
        name = tag.name
        src = _first_src(tag)
        if name == "img":
            self.had_images = True
            alt = _text(tag.get("alt", ""))
            text = f"[{self.labels['image']}: {alt}]" if alt else f"[{self.labels['image']}]"
        else:
            text = f"[{self.labels['embed' if name == 'iframe' else name]}]"
        if not _http_url(src) or "a" in inside:
            return _x(text) if name == "img" else ""
        return f'<a href={_xa(_href(src))}>{_x(text)}</a>'

    def render(self, node, inside=()):
        if isinstance(node, Comment):
            return ""
        if isinstance(node, NavigableString):
            return _x(str(node))
        name = node.name
        if name in ("img", "video", "audio", "iframe", "picture"):
            target = node.find("img") if name == "picture" else node
            return self.media_link(target, inside) if target else ""
        if name in ("source", "track", "wbr"):
            return ""
        if name not in XHTML_BLOCK and name not in XHTML_INLINE:
            name = None  # unwrap unknown elements, keep their content
        elif name == "a" and "a" in inside:
            name = None  # no links inside links
        elif name in PHRASING_ONLY and _has_block(node):
            name = "div" if name in ("p", "pre") or name.startswith("h") else None
        elif name == "li" and (not inside or inside[-1] not in ("ul", "ol")):
            name = "p"
        elif name in ("dt", "dd") and (not inside or inside[-1] != "dl"):
            name = "p"
        elif name == "figcaption" and (not inside or inside[-1] != "figure"):
            name = "p"
        elif name in ("tr", "td", "th", "thead", "tbody", "tfoot", "caption") and "table" not in inside:
            name = "div" if name in ("tr", "thead", "tbody", "tfoot", "caption") else "span"
        if name is None:
            return "".join(self.render(child, inside) for child in node.children)
        if name in XHTML_VOID:
            return f"<{name}/>"
        child_inside = inside + (name,)
        if name in ("ul", "ol"):
            parts = []
            for child in node.children:
                if isinstance(child, Tag) and child.name == "li":
                    parts.append(self.render(child, child_inside))
                elif isinstance(child, Tag) or (str(child).strip() and not isinstance(child, Comment)):
                    parts.append("<li>" + self.render(child, child_inside + ("li",)) + "</li>")
            inner = "".join(parts)
            if not inner:
                return ""
        elif name == "dl":
            parts = [self.render(child, child_inside) for child in node.children
                     if isinstance(child, Tag) and child.name in ("dt", "dd")]
            inner = "".join(parts)
            if not inner:
                return "".join(self.render(child, inside) for child in node.children)
        elif name == "table":
            rows = [child for child in node.children if isinstance(child, Tag)
                    and child.name in ("caption", "thead", "tbody", "tfoot", "tr")]
            inner = "".join(self.render(child, child_inside) for child in rows)
            if not inner:
                return ""
        elif name in ("thead", "tbody", "tfoot"):
            inner = "".join(self.render(child, child_inside) for child in node.children
                            if isinstance(child, Tag) and child.name == "tr")
        elif name == "tr":
            inner = "".join(self.render(child, child_inside) for child in node.children
                            if isinstance(child, Tag) and child.name in ("td", "th"))
            if not inner:
                inner = "<td></td>"
        else:
            inner = "".join(self.render(child, child_inside) for child in node.children)
        attrs = self.attrs(node, name)
        if name in ("h1", "h2", "h3") and "a" not in inside:
            text = _text(node.get_text(" "), 200)
            if text:
                self.headings.append((int(name[1]), text))
                attrs += f' id="h{len(self.headings)}"'
        return f"<{name}{attrs}>{inner}</{name}>"


CONTAINER_XML = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="EPUB/package.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

EPUB_CSS = """body { font-family: serif; line-height: 1.5; margin: 0 5%; }
h1 { font-size: 1.6em; line-height: 1.2; margin: 1em 0 0.4em; }
h2, h3, h4 { line-height: 1.25; margin: 1.4em 0 0.5em; }
.meta { color: #555; font-size: 0.9em; margin-bottom: 1.5em; }
.meta p { margin: 0.2em 0; }
blockquote { margin: 1em 1.5em; font-style: italic; }
pre { white-space: pre-wrap; font-size: 0.85em; }
code, kbd, samp { font-family: monospace; }
table { border-collapse: collapse; }
td, th { border: 1px solid #999; padding: 0.2em 0.4em; }
.note { color: #555; font-size: 0.85em; border-top: 1px solid #999; margin-top: 2em; padding-top: 0.5em; }
"""


def _xhtml(lang, title, body, extra_ns=""):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE html>\n'
        f'<html xmlns="http://www.w3.org/1999/xhtml"{extra_ns} xml:lang={_xa(lang)} lang={_xa(lang)}>\n'
        f'<head>\n<meta charset="UTF-8"/>\n<title>{_x(title)}</title>\n'
        '<link rel="stylesheet" type="text/css" href="style.css"/>\n</head>\n'
        f"<body>\n{body}\n</body>\n</html>\n"
    )


def _iso_date(value):
    for pattern in ("%B %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), pattern).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def to_epub(meta, fragment, labels=None, now=None):
    """Return the bytes of a one-chapter EPUB 3 file."""
    labels = {**DEFAULT_LABELS, **(labels or {})}
    title = meta["title"] or "Untitled"
    lang = meta.get("lang") if LANG_RE.match(meta.get("lang") or "") else "en"
    url = _http_url(meta.get("url"))
    modified = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    identifier = "urn:uuid:" + str(uuid.uuid5(uuid.NAMESPACE_URL, url or title))

    chapter = _Chapter(labels)
    body = chapter.render(fragment)

    meta_lines = []
    if url:
        meta_lines.append(f'<p>{_x(labels["source"])}: <a href={_xa(_href(url))}>{_x(meta.get("site") or url)}</a></p>')
    if meta.get("byline"):
        meta_lines.append(f'<p>{_x(labels["by"])}: {_x(meta["byline"])}</p>')
    if meta.get("date"):
        meta_lines.append(f'<p>{_x(labels["published"])}: {_x(meta["date"])}</p>')
    head = f"<h1>{_x(title)}</h1>\n" + (f'<div class="meta">{"".join(meta_lines)}</div>\n' if meta_lines else "")
    note = f'\n<p class="note">{_x(labels["images_note"])}</p>' if chapter.had_images else ""
    chapter_xhtml = _xhtml(lang, title, f"<section>\n{head}{body}{note}\n</section>")

    sub = "".join(f'<li><a href="chapter.xhtml#h{i}">{_x(text)}</a></li>'
                  for i, (level, text) in enumerate(chapter.headings, 1) if level == 2)
    nav_body = (f'<nav epub:type="toc" id="toc">\n<h1>{_x(labels["contents"])}</h1>\n<ol>\n'
                f'<li><a href="chapter.xhtml">{_x(title)}</a>' + (f"<ol>{sub}</ol>" if sub else "") + "</li>\n</ol>\n</nav>")
    nav_xhtml = _xhtml(lang, labels["contents"], nav_body, ' xmlns:epub="http://www.idpf.org/2007/ops"')

    optional = ""
    if meta.get("byline"):
        optional += f"    <dc:creator>{_x(meta['byline'])}</dc:creator>\n"
    if meta.get("site"):
        optional += f"    <dc:publisher>{_x(meta['site'])}</dc:publisher>\n"
    if url:
        optional += f"    <dc:source>{_x(url)}</dc:source>\n"
    if meta.get("date") and _iso_date(meta["date"]):
        optional += f"    <dc:date>{_iso_date(meta['date'])}</dc:date>\n"
    opf = f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid" xml:lang={_xa(lang)}>
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="uid">{identifier}</dc:identifier>
    <dc:title>{_x(title)}</dc:title>
    <dc:language>{_x(lang)}</dc:language>
{optional}    <meta property="dcterms:modified">{modified}</meta>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>
    <item id="css" href="style.css" media-type="text/css"/>
  </manifest>
  <spine>
    <itemref idref="chapter"/>
  </spine>
</package>
"""
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as book:
        # The mimetype file must come first, uncompressed, with no extra fields.
        book.writestr(zipfile.ZipInfo("mimetype", date_time=(2020, 1, 1, 0, 0, 0)), "application/epub+zip",
                      compress_type=zipfile.ZIP_STORED)
        for name, content in (("META-INF/container.xml", CONTAINER_XML), ("EPUB/package.opf", opf),
                              ("EPUB/nav.xhtml", nav_xhtml), ("EPUB/chapter.xhtml", chapter_xhtml),
                              ("EPUB/style.css", EPUB_CSS)):
            book.writestr(name, content.encode("utf-8"), compress_type=zipfile.ZIP_DEFLATED)
    return buffer.getvalue()


# --------------------------------------------------------------------------


def read_request(data):
    """Pull the export fields out of the page's JSON. Raises ValueError."""
    if not isinstance(data, dict):
        raise ValueError("nothing to export")
    fmt = data.get("format")
    if not isinstance(fmt, str) or fmt not in FORMATS:
        raise ValueError("unknown format")
    html = data.get("html") if isinstance(data.get("html"), str) else ""
    if not html.strip():
        raise ValueError("nothing to export")
    url = _http_url(_text(data.get("url"), 4000))
    meta = {
        "title": _text(data.get("title")) or url,
        "url": url,
        "site": _text(data.get("site"), 200),
        "byline": _text(data.get("byline"), 300),
        "date": _text(data.get("date"), 100),
        "lang": _text(data.get("lang"), 40),
    }
    return fmt, meta, html


def build(fmt, meta, fragment, labels=None):
    """Return (bytes, mimetype, filename) for a sanitized article fragment."""
    if isinstance(fragment, str):
        fragment = BeautifulSoup(fragment, "html.parser")
    mimetype, extension = FORMATS[fmt]
    if fmt == "markdown":
        payload = to_markdown(meta, fragment, labels).encode("utf-8")
    else:
        payload = to_epub(meta, fragment, labels)
    return payload, mimetype, safe_filename(meta["title"], extension)
