"""Code block checks (design: V6 code block details).

- Text: each code block against its source block, after normalizing line
  endings and trailing whitespace only.
- XML: a block that starts with "<" or is tagged xml/ttml is parsed with lxml.
  Spec examples are often fragments, so the block is parsed inside a wrapper
  element that declares any namespace prefix the fragment uses but does not
  declare; several top-level elements and "..." are then fine, a missing or
  mismatched tag is not.
- WebVTT: a block that starts with "WEBVTT", or has "-->" and is not XML:
  the header line and every cue timing line against the WebVTT pattern.
"""

from __future__ import annotations

import re

from lxml import etree

_DECL = re.compile(r"<\?xml[^>]*\?>|<!DOCTYPE[^>\[]*(\[[^\]]*\])?\s*>", re.I)
_PREFIX_USE = re.compile(
    r"</?([A-Za-z_][\w.-]*):[A-Za-z_]|\s([A-Za-z_][\w.-]*):[A-Za-z_][\w.-]*\s*="
)
_PREFIX_DECL = re.compile(r"xmlns:([A-Za-z_][\w.-]*)\s*=")
_TIME = r"(?:\d{2,}:)?[0-5]\d:[0-5]\d\.\d{3}"
_TIMING = re.compile(rf"^{_TIME}[ \t]+-->[ \t]+{_TIME}(?:[ \t]+[^\s:]+:[^\s]+)*[ \t]*$")
_HEADER = re.compile(r"^WEBVTT(?:[ \t].*)?$")
WRAPPER = "specval-fragment"


def is_xml_block(text: str, info: str = "") -> bool:
    """Tagged xml/ttml, or starts with "<" and has XML markup: a closing tag,
    "/>", a declaration or a comment. "<url>[attr:val]" is not XML."""
    if info.lower() in ("xml", "ttml", "ttml2", "imsc"):
        return True
    stripped = text.lstrip()
    if not stripped.startswith("<"):
        return False
    return stripped.startswith(("<?xml", "<!--")) or "</" in text or "/>" in text


def is_webvtt_block(text: str) -> bool:
    stripped = text.lstrip()
    return stripped.startswith("WEBVTT") or ("-->" in text and not stripped.startswith("<"))


def xml_problems(text: str) -> list[tuple[int, str]]:
    """(line, message) for each parse error, lines 1-based within the block."""
    # Keep line numbers: the declaration and doctype become spaces (newlines kept).
    body = _DECL.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)
    declared = set(_PREFIX_DECL.findall(body))
    used = {p for pair in _PREFIX_USE.findall(body) for p in pair if p}
    missing = sorted(used - declared - {"xml", "xmlns"})
    decls = "".join(f' xmlns:{p}="urn:specval:undeclared:{p}"' for p in missing)
    wrapped = f"<{WRAPPER}{decls}>{body}</{WRAPPER}>"
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True)
    try:
        etree.fromstring(wrapped.encode("utf-8"), parser)
    except etree.XMLSyntaxError as exc:
        line = exc.lineno or 1
        message = str(exc.msg or exc).replace(WRAPPER, "(end of block)")
        message = re.sub(r",? line \d+, column \d+.*$", "", message)
        return [(line, message)]
    return []


def webvtt_problems(text: str) -> list[tuple[int, str]]:
    problems = []
    lines = text.split("\n")
    first = next((i for i, line in enumerate(lines) if line.strip()), 0)
    if lines[first].lstrip().startswith("WEBVT") and not _HEADER.match(lines[first].strip()):
        problems.append((first + 1, "the first line must be 'WEBVTT'"))
    for i, line in enumerate(lines):
        if "-->" in line and not _TIMING.match(line.strip()):
            problems.append(
                (i + 1, f"cue timing line does not match the WebVTT pattern: {line.strip()!r}")
            )
    return problems


def _lines(text: str) -> list[str]:
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return lines


def code_differs(source: str, markdown: str) -> tuple[int, str | None, str | None] | None:
    """None if equal after normalizing line endings and trailing whitespace,
    else (first differing line, source line, Markdown line)."""
    a, b = _lines(source), _lines(markdown)
    for i in range(max(len(a), len(b))):
        x = a[i] if i < len(a) else None
        y = b[i] if i < len(b) else None
        if x != y:
            return i + 1, x, y
    return None
