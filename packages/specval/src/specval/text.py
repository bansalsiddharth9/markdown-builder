"""Text pieces shared by source extraction and Markdown parsing.

A Segment is the text of one block (paragraph, heading, table cell, code
block, list item …) with its context, so a value split by inline markup
(``<b>shall</b> not``) is still found as one token, and with its location.

The HTML walker is used for source HTML (parsed with lxml) and for raw HTML
inside the Markdown (parsed with html.parser), so both sides are cut into
segments the same way.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable
from dataclasses import dataclass

from bs4 import BeautifulSoup, Comment, Declaration, Doctype, NavigableString, Tag
from bs4.element import CData, ProcessingInstruction

TEXT, TABLE, CODE = "text", "table", "code"

BLOCK_TAGS = frozenset(
    """address article aside blockquote body caption center dd details dialog dir div dl dt
    fieldset figcaption figure footer form h1 h2 h3 h4 h5 h6 header hgroup hr li main menu nav
    ol p pre section summary table tbody td tfoot th thead tr ul""".split()
)
HEADING_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6")
_SKIP_STRINGS = (Comment, Declaration, Doctype, CData, ProcessingInstruction)
_SKIP_TAGS = frozenset({"script", "style", "noscript", "template", "head"})


@dataclass(frozen=True)
class Segment:
    text: str
    ctx: str  # text | table | code
    md_line: int | None = None
    source_loc: str | None = None


def normalize(text: str) -> str:
    """NFKC and collapsed whitespace (design: V3 normalization, also used for V4)."""
    return " ".join(unicodedata.normalize("NFKC", text).split())


def own_cells(table: Tag) -> list[Tag]:
    """Cells whose nearest enclosing table is this one."""
    return [c for c in table.find_all(["td", "th"]) if c.find_parent("table") is table]


def is_layout_box(table: Tag) -> bool:
    """A table with exactly one cell: a box around content, unwrapped by the
    converter (deviations Q14, V-g), so it is not counted as a table."""
    return len(own_cells(table)) == 1


def element_ctx(element: Tag) -> str:
    """code inside <pre>; table inside a real table's cells; else text. A table
    caption counts as text: Pandoc writes a pipe table's caption as a paragraph."""
    if element.name == "pre" or element.find_parent("pre") is not None:
        return CODE
    if element.name == "caption" or element.find_parent("caption") is not None:
        return TEXT
    tables = ([element] if element.name == "table" else []) + element.find_parents("table")
    if any(not is_layout_box(t) for t in tables):
        return TABLE
    return TEXT


def html_segments(
    root: Tag | BeautifulSoup, locate: Callable[[Tag], tuple[int | None, str | None]]
) -> list[Segment]:
    """Cut the visible text under root into block segments, in document order.
    locate(block element) gives (md_line, source_loc) for a segment."""
    segments: list[Segment] = []
    buffer: list[str] = []
    owner: list[Tag] = [root]  # the block element the buffered text belongs to

    def flush() -> None:
        text = "".join(buffer)
        buffer.clear()
        if text.strip():
            block = owner[-1]
            line, loc = locate(block)
            segments.append(Segment(text, element_ctx(block), line, loc))

    def walk(node: Tag) -> None:
        for child in node.children:
            if isinstance(child, NavigableString):
                if not isinstance(child, _SKIP_STRINGS):
                    buffer.append(str(child))
                continue
            if not isinstance(child, Tag) or child.name in _SKIP_TAGS:
                continue
            if child.name == "br":
                buffer.append("\n")
                continue
            if child.name in BLOCK_TAGS:
                flush()
                owner.append(child)
                walk(child)
                flush()
                owner.pop()
            else:
                walk(child)

    walk(root)
    flush()
    return segments


def visible_text(element: Tag) -> str:
    """Text of an element without comments and skipped tags, whitespace collapsed."""
    parts = [
        str(s)
        for s in element.find_all(string=True)
        if not isinstance(s, _SKIP_STRINGS)
        and not any(p.name in _SKIP_TAGS for p in s.parents if isinstance(p, Tag))
    ]
    return normalize("".join(parts))
