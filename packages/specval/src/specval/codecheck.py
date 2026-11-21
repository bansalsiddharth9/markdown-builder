"""Code block checks (design: V6 code block details).

- Text: each code block against its source block, after normalizing line
  endings and trailing whitespace only.
- XML: a block tagged xml/ttml, or one that starts with "<" and has XML markup
  (see is_xml_block; deviations V-x), is parsed with lxml.
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

# An XML declaration (<?xml ...?>) or a DOCTYPE, with an optional internal
# subset in [...]. Both are only allowed at the very start of a document, so
# they must go before the block is wrapped in an element.
_DECL = re.compile(r"<\?xml[^>]*\?>|<!DOCTYPE[^>\[]*(\[[^\]]*\])?\s*>", re.I)
# A namespace prefix in use: in a tag name ("<tts:x", "</tts:x"; group 1) or in
# an attribute name (" tts:origin="; group 2).
_PREFIX_USE = re.compile(
    r"</?([A-Za-z_][\w.-]*):[A-Za-z_]|\s([A-Za-z_][\w.-]*):[A-Za-z_][\w.-]*\s*="
)
# A prefix declared in the block itself: xmlns:tts=
_PREFIX_DECL = re.compile(r"xmlns:([A-Za-z_][\w.-]*)\s*=")
# A WebVTT timestamp: optional hours (2+ digits), then MM:SS.mmm.
_TIME = r"(?:\d{2,}:)?[0-5]\d:[0-5]\d\.\d{3}"
# A whole cue timing line: start --> end, then optional cue settings, each a
# name:value token (deviations V-y). Cue text glued onto the line fails.
_TIMING = re.compile(rf"^{_TIME}[ \t]+-->[ \t]+{_TIME}(?:[ \t]+[^\s:]+:[^\s]+)*[ \t]*$")
# "WEBVTT" alone, or followed by a space or tab and any text.
_HEADER = re.compile(r"^WEBVTT(?:[ \t].*)?$")
WRAPPER = "specval-fragment"  # element wrapped around a block before parsing


def is_xml_block(text: str, info: str = "") -> bool:
    """True when V6 should parse the block as XML (deviations V-x).

    Tagged xml/ttml, or starts with "<" and has XML markup: a closing tag,
    "/>", a declaration or a comment. "<url>[attr:val]" (CEA-608-E) is not XML.
    """
    # Also ttml2 and imsc. Not "html": the design lists it, the build does not.
    if info.lower() in ("xml", "ttml", "ttml2", "imsc"):
        return True
    stripped = text.lstrip()
    if not stripped.startswith("<"):
        return False
    return stripped.startswith(("<?xml", "<!--")) or "</" in text or "/>" in text


def is_webvtt_block(text: str) -> bool:
    """True when V6 should check the block as WebVTT: it starts with "WEBVTT",
    or has a "-->" and does not start with "<" (an XML comment has "-->" too).
    The caller tests is_xml_block first."""
    stripped = text.lstrip()
    return stripped.startswith("WEBVTT") or ("-->" in text and not stripped.startswith("<"))


def problem_key(message: str) -> str:
    """A problem message without line numbers, to recognize the same problem in
    the source's copy of a block even when it starts on another line (browsers
    drop the line break right after <pre>)."""
    return re.sub(r"\bline \d+", "line N", message)


def xml_problems(text: str) -> list[tuple[int, str]]:
    """(line, message) for the first parse error, or [] when well-formed.

    Lines are 1-based within the block. The block is parsed inside a WRAPPER
    element, so fragments with several top-level elements are fine. Prefixes
    the block uses but does not declare get a dummy declaration on the
    wrapper; a missing or mismatched tag is still an error. lxml stops at the
    first error, so there is at most one problem.
    """
    # Keep line numbers: the declaration and doctype become spaces (newlines kept).
    body = _DECL.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)
    declared = set(_PREFIX_DECL.findall(body))
    used = {p for pair in _PREFIX_USE.findall(body) for p in pair if p}
    # "xml" and "xmlns" are built in and must not be declared.
    missing = sorted(used - declared - {"xml", "xmlns"})
    decls = "".join(f' xmlns:{p}="urn:specval:undeclared:{p}"' for p in missing)
    wrapped = f"<{WRAPPER}{decls}>{body}</{WRAPPER}>"
    # No entity expansion and no network: blocks are untrusted text.
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True)
    try:
        etree.fromstring(wrapped.encode("utf-8"), parser)
    except etree.XMLSyntaxError as exc:
        # The wrapper's start tag adds no newline, so lxml's line is the block's.
        line = exc.lineno or 1
        message = str(exc.msg or exc).replace(WRAPPER, "(end of block)")
        # The line is reported separately; dropping it from the text also lets
        # problem_key match the source's copy of the block.
        message = re.sub(r",? line \d+, column \d+.*$", "", message)
        return [(line, message)]
    return []


def webvtt_problems(text: str) -> list[tuple[int, str]]:
    """(line, message) for a bad header and for each "-->" line that is not a
    valid cue timing line. Lines are 1-based within the block.

    The header is only checked when the first non-empty line starts with
    "WEBVT", so a snippet of cues without a header is fine.
    """
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
    """Lines with any line ending, trailing spaces removed, and blank lines at
    the start and end dropped."""
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
