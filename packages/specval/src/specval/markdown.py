"""Read a converted Markdown file (design: Independence rules).

Parsed with markdown-it-py (CommonMark plus pipe tables), raw HTML blocks
with BeautifulSoup. Line numbers are 1-based lines of the whole <id>.md file,
front matter included, so a report points at the exact line to open.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, Tag
from markdown_it import MarkdownIt
from markdown_it.token import Token

from specval.tablecheck import html_grid, html_grid_lines
from specval.text import (
    CODE,
    HEADING_TAGS,
    TABLE,
    TEXT,
    Segment,
    html_segments,
    is_layout_box,
    normalize,
)

_TAG_ID = re.compile(r"""\bid\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""", re.I)
_TAG_NAME = re.compile(r"^<\s*([A-Za-z][A-Za-z0-9]*)")


@dataclass(frozen=True)
class MdHeading:
    level: int
    text: str  # anchors stripped, whitespace collapsed
    line: int


@dataclass(frozen=True)
class MdTable:
    line: int
    kind: str  # pipe | html
    rows: tuple[tuple[str, ...], ...]
    grid: tuple[tuple[str, ...], ...] = ()  # spans expanded (V5)
    row_lines: tuple[int, ...] = ()  # line of each grid row
    cell_lines: tuple[tuple[int, ...], ...] = ()  # line of each grid cell (HTML tables)
    caption: str = ""  # an HTML table's <caption>

    def line_of(self, row: int | None, col: int | None = None) -> int:
        """Line of a grid cell (or row), falling back to the table's first line."""
        if row is not None and col is not None and row < len(self.cell_lines):
            cells = self.cell_lines[row]
            if 0 <= col < len(cells):
                return cells[col]
        if row is not None and row < len(self.row_lines):
            return self.row_lines[row]
        return self.line


@dataclass(frozen=True)
class MdCode:
    line: int
    text: str
    info: str  # fence info string ("xml"), "" for indented or HTML <pre>
    first_line: int = 0  # line of the code's first line (after a fence)


@dataclass(frozen=True)
class MdImage:
    line: int
    src: str
    alt: str


@dataclass(frozen=True)
class MdLink:
    line: int
    href: str


@dataclass
class MarkdownDoc:
    headings: list[MdHeading] = field(default_factory=list)
    tables: list[MdTable] = field(default_factory=list)
    code_blocks: list[MdCode] = field(default_factory=list)
    images: list[MdImage] = field(default_factory=list)
    links: list[MdLink] = field(default_factory=list)
    ids: dict[str, int] = field(default_factory=dict)  # explicit ids (<a id>, …) -> line
    segments: list[Segment] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)  # the whole file, for excerpts

    def heading_slugs(self) -> set[str]:
        """GitHub-style heading slugs, the other kind of valid '#…' target."""
        seen: dict[str, int] = {}
        slugs = set()
        for heading in self.headings:
            base = github_slug(heading.text)
            n = seen.get(base, 0)
            seen[base] = n + 1
            slugs.add(base if n == 0 else f"{base}-{n}")
        return slugs

    def excerpt(self, line: int | None, width: int = 160) -> str | None:
        if line is None or not 1 <= line <= len(self.lines):
            return None
        text = self.lines[line - 1].strip()
        return text if len(text) <= width else text[: width - 1] + "…"


def github_slug(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def _parser() -> MarkdownIt:
    md = MarkdownIt("commonmark", {"html": True}).enable("table")
    # markdown-it refuses javascript: and data: links (a rendering safety rule) and
    # would read them as plain text; links are only read here, never followed.
    md.validateLink = lambda url: True
    return md


def parse_markdown(full_text: str, body: str) -> MarkdownDoc:
    """Parse body (the file without front matter); line numbers count from the
    top of full_text."""
    offset = full_text.count("\n") - body.count("\n")
    doc = MarkdownDoc(lines=full_text.split("\n"))
    tokens = _parser().parse(body)
    _Walker(doc, offset).run(tokens)
    return doc


class _Walker:
    def __init__(self, doc: MarkdownDoc, offset: int) -> None:
        self.doc = doc
        self.offset = offset

    def line(self, token: Token | None) -> int | None:
        if token is None or token.map is None:
            return None
        return token.map[0] + 1 + self.offset

    def run(self, tokens: list[Token]) -> None:
        heading: Token | None = None
        table_rows: list[list[str]] | None = None
        row_lines: list[int] = []
        table_line: int | None = None
        in_cell = False
        for i, token in enumerate(tokens):
            kind = token.type
            if kind == "heading_open":
                heading = token
            elif kind == "heading_close":
                heading = None
            elif kind == "table_open":
                table_rows, table_line, row_lines = [], self.line(token), []
            elif kind == "table_close":
                assert table_rows is not None and table_line is not None
                rows = tuple(tuple(r) for r in table_rows)
                self.doc.tables.append(
                    MdTable(table_line, "pipe", rows, grid=rows, row_lines=tuple(row_lines))
                )
                table_rows = None
            elif kind == "tr_open" and table_rows is not None:
                table_rows.append([])
                row_lines.append(self.line(token) or table_line or 0)
            elif kind in ("th_open", "td_open"):
                in_cell = True
            elif kind in ("th_close", "td_close"):
                in_cell = False
            elif kind == "inline":
                text = self.inline(token, ctx=TABLE if in_cell else TEXT)
                if heading is not None:
                    self.doc.headings.append(
                        MdHeading(int(heading.tag[1]), normalize(text), self.line(heading) or 0)
                    )
                if in_cell and table_rows is not None:
                    table_rows[-1].append(normalize(text))
            elif kind in ("fence", "code_block"):
                line = self.line(token) or 0
                first = line + 1 if kind == "fence" else line
                self.doc.code_blocks.append(MdCode(line, token.content, token.info.strip(), first))
                self.doc.segments.append(Segment(token.content, CODE, line))
            elif kind == "html_block":
                self.html_block(token)
            del i

    # ------------------------------------------------------------- inline

    def inline(self, token: Token, ctx: str) -> str:
        """Collect links, images and ids; return the visible text (image alt
        text and raw HTML tags left out)."""
        line = self.line(token)
        parts: list[str] = []
        skip_depth = 0  # inside an image's alt text
        for child in token.children or []:
            kind = child.type
            if kind == "image":
                self.doc.images.append(
                    MdImage(line or 0, str(child.attrs.get("src", "")), child.content)
                )
                continue
            if kind == "link_open":
                self.doc.links.append(MdLink(line or 0, str(child.attrs.get("href", ""))))
            elif kind == "html_inline":
                self.html_inline(child.content, line)
            elif kind in ("text", "code_inline") and not skip_depth:
                parts.append(child.content)
            elif kind in ("softbreak", "hardbreak"):
                parts.append("\n")
        text = "".join(parts)
        if text.strip():
            self.doc.segments.append(Segment(text, ctx, line))
        return text

    def html_inline(self, html: str, line: int | None) -> None:
        name = _TAG_NAME.match(html)
        if name is None:
            return
        match = _TAG_ID.search(html)
        if match:
            self.doc.ids.setdefault(next(g for g in match.groups() if g is not None), line or 0)
        tag = name.group(1).lower()
        if tag in ("img", "a"):
            soup = BeautifulSoup(html, "html.parser")
            element = soup.find(tag)
            if isinstance(element, Tag):
                if tag == "img":
                    self.doc.images.append(
                        MdImage(line or 0, element.get("src", ""), element.get("alt", ""))
                    )
                elif element.get("href"):
                    self.doc.links.append(MdLink(line or 0, element["href"]))

    # -------------------------------------------------------------- blocks

    def html_block(self, token: Token) -> None:
        base = self.line(token) or 1
        soup = BeautifulSoup(token.content, "html.parser")

        def line_of(element: Tag) -> int:
            return base + ((element.sourceline or 1) - 1)

        for element in soup.find_all(True):
            if element.get("id"):
                self.doc.ids.setdefault(element["id"], line_of(element))
            name = element.name
            if name in HEADING_TAGS:
                self.doc.headings.append(
                    MdHeading(int(name[1]), normalize(element.get_text()), line_of(element))
                )
            elif name == "table" and not is_layout_box(element):
                rows = tuple(
                    tuple(normalize(c.get_text()) for c in tr.find_all(["td", "th"]))
                    for tr in element.find_all("tr")
                    if tr.find_parent("table") is element
                )
                own_rows = [
                    tr for tr in element.find_all("tr") if tr.find_parent("table") is element
                ]
                caption = next(
                    (c for c in element.find_all("caption") if c.find_parent("table") is element),
                    None,
                )
                self.doc.tables.append(
                    MdTable(
                        line_of(element),
                        "html",
                        rows,
                        grid=tuple(tuple(r) for r in html_grid(element)),
                        row_lines=tuple(line_of(tr) for tr in own_rows),
                        cell_lines=tuple(tuple(r) for r in html_grid_lines(element, line_of)),
                        caption=normalize(caption.get_text()) if caption is not None else "",
                    )
                )
            elif name == "pre":
                self.doc.code_blocks.append(
                    MdCode(line_of(element), element.get_text(), "", line_of(element))
                )
            elif name == "img":
                self.doc.images.append(
                    MdImage(line_of(element), element.get("src", ""), element.get("alt", ""))
                )
            elif name == "a" and element.get("href"):
                self.doc.links.append(MdLink(line_of(element), element["href"]))
        self.doc.segments.extend(html_segments(soup, lambda el: (line_of(el), None)))


__all__ = [
    "CODE",
    "TABLE",
    "TEXT",
    "MarkdownDoc",
    "MdCode",
    "MdHeading",
    "MdImage",
    "MdLink",
    "MdTable",
    "github_slug",
    "parse_markdown",
]
