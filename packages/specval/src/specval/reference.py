"""Reference data from the saved source (design: Reference extraction).

Different libraries than the converter: BeautifulSoup + html5lib for HTML
(the browser reading rules exactly; the converter uses lxml),
python-docx (and its XML) for DOCX, pypdf and pdfplumber for PDF.

- HTML: visible text after the converter's cleanup rules; h1-h6; <table>
  (one-cell boxes excluded); <pre>; <img>.
- DOCX: paragraph and footnote text in document order; "Heading N" styles;
  w:tbl; paragraphs in a code style or monospace font; pictures.
- PDF: pypdf text per page without repeated header/footer lines; tables from
  pdfplumber (approximate); no headings, code blocks or images.

The HTML cleanup rules (strip_tags, clean: main-content, drop_selectors) are
read from the options the converter recorded in the manifest, and applied by
code written here, never imported from specconv.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from bs4 import BeautifulSoup, Tag

from specval.tablecheck import html_grid
from specval.text import (
    CODE,
    HEADING_TAGS,
    TABLE,
    TEXT,
    Segment,
    html_segments,
    is_layout_box,
    normalize,
    own_cells,
    visible_text,
)


@dataclass(frozen=True)
class RefHeading:
    level: int
    text: str
    loc: str


@dataclass(frozen=True)
class RefTable:
    loc: str
    rows: tuple[tuple[str, ...], ...]  # distinct cells per row (V1 keys)
    page: int | None = None
    grid: tuple[tuple[str, ...], ...] = ()  # spans expanded (V5)


@dataclass(frozen=True)
class RefCode:
    loc: str
    text: str


@dataclass
class Reference:
    kind: str  # html | docx | pdf
    headings: list[RefHeading] | None = None  # None: not available (PDF)
    tables: list[RefTable] = field(default_factory=list)
    tables_approximate: bool = False  # PDF: pdfplumber's tables
    code_blocks: list[RefCode] | None = None  # None: not available (PDF)
    images: list[str] | None = None  # locations; None: not counted (PDF)
    segments: list[Segment] = field(default_factory=list)
    pages: int | None = None
    notes: list[str] = field(default_factory=list)


# ------------------------------------------------------------------- HTML

PAGE_CHROME_ALWAYS = ("nav", "aside")
PAGE_CHROME_OUTSIDE_CONTENT = ("header", "footer")
CONTENT_TAGS = ["main", "article", "section"]


def _main_content(soup: BeautifulSoup, drop_selectors: Iterable[str]) -> None:
    """clean: main-content as recorded in deviations F7."""
    body = soup.body or soup
    main = soup.find("main")
    keep = (
        [main]
        if main is not None
        else [a for a in soup.find_all("article") if a.find_parent("article") is None]
    )
    if keep:
        for element in keep:
            element.extract()
        body.clear()
        for element in keep:
            body.append(element)
    for tag in PAGE_CHROME_ALWAYS:
        for element in soup.find_all(tag):
            if not element.decomposed:
                element.decompose()
    for tag in PAGE_CHROME_OUTSIDE_CONTENT:
        for element in soup.find_all(tag):
            if not element.decomposed and element.find_parent(CONTENT_TAGS) is None:
                element.decompose()
    for selector in drop_selectors:
        for element in soup.select(selector):
            if not element.decomposed:
                element.decompose()


_DECLARED_CHARSET = re.compile(rb"<meta[^>]+charset", re.I)


def _decode_html(data: bytes, encoding: str | None) -> bytes | str:
    """Bytes for html5lib, or text when the page is UTF-8 without saying so.

    html5lib follows the HTML standard's last resort for pages that declare no
    encoding (windows-1252); browsers first check whether the bytes are valid
    UTF-8, and so does this. A given or declared encoding is left to html5lib.
    """
    if encoding is not None or _DECLARED_CHARSET.search(data[:4096]):
        return data
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data


def from_html(
    data: bytes,
    *,
    strip_tags: Iterable[str] = ("script", "style", "noscript"),
    clean: str = "none",
    drop_selectors: Iterable[str] = (),
    encoding: str | None = None,
) -> Reference:
    soup = BeautifulSoup(_decode_html(data, encoding), "html5lib", from_encoding=encoding)
    for tag in strip_tags:
        for element in soup.find_all(tag):
            element.decompose()
    if clean == "main-content":
        _main_content(soup, drop_selectors)
    root = soup.body or soup

    # A human-readable location: the element and the heading it comes after.
    section: dict[int, str] = {}
    current = "start of document"
    for element in root.find_all(True):
        if element.name in HEADING_TAGS:
            current = visible_text(element)[:60] or current
        section[id(element)] = current

    def loc(element: Tag) -> str:
        return f"<{element.name}> in section '{section.get(id(element), 'start of document')}'"

    ref = Reference(kind="html", headings=[], code_blocks=[], images=[])
    for element in root.find_all(True):
        name = element.name
        if name in HEADING_TAGS:
            ref.headings.append(RefHeading(int(name[1]), visible_text(element), loc(element)))
        elif name == "table" and not is_layout_box(element):
            rows = tuple(
                tuple(visible_text(c) for c in tr.find_all(["td", "th"]))
                for tr in element.find_all("tr")
                if tr.find_parent("table") is element
            )
            grid = tuple(tuple(r) for r in html_grid(element))
            ref.tables.append(RefTable(loc(element), rows, grid=grid))
        elif name == "pre":
            ref.code_blocks.append(RefCode(loc(element), element.get_text()))
        elif name == "img" or (name == "svg" and element.find_parent("svg") is None):
            # Pandoc turns an inline <svg> drawing into an image (a data: URI).
            ref.images.append(loc(element))
    ref.segments = html_segments(root, lambda el: (None, loc(el)))
    return ref


# ------------------------------------------------------------------- DOCX

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
HEADING_STYLE = re.compile(r"^heading\s*([1-9])$", re.I)
CODE_STYLE = re.compile(r"code|source", re.I)
MONOSPACE_FONTS = ("courier", "consolas", "menlo", "monaco", "source code", "lucida console")


def _w_text(element) -> str:
    """Text of a w:p (or any part) in document order: w:t, tabs, breaks.
    Deleted and moved-away text (tracked changes) is skipped, inserted text
    kept, as Pandoc's default (accept changes) does."""
    parts: list[str] = []
    for node in element.iter():
        tag = node.tag
        if tag in (f"{W}t",):
            if any(a.tag in (f"{W}del", f"{W}moveFrom") for a in node.iterancestors()):
                continue
            parts.append(node.text or "")
        elif tag == f"{W}tab":
            parts.append("\t")
        elif tag in (f"{W}br", f"{W}cr"):
            parts.append("\n")
    return "".join(parts)


def _style_name(paragraph) -> str:
    try:
        return paragraph.style.name if paragraph.style is not None else ""
    except (KeyError, ValueError):
        return ""


def _is_monospace(paragraph) -> bool:
    runs = [r for r in paragraph.runs if r.text.strip()]
    if not runs:
        return False
    for run in runs:
        font = (run.font.name or "") or (paragraph.style.font.name if paragraph.style else "")
        if not any(m in (font or "").lower() for m in MONOSPACE_FONTS):
            return False
    return True


def from_docx(path: Path) -> Reference:
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    document = docx.Document(str(path))
    ref = Reference(kind="docx", headings=[], code_blocks=[], images=[])
    state = {"paragraph": 0, "table": 0, "section": "start of document"}
    code_lines: list[str] = []
    code_loc: list[str] = []

    def end_code() -> None:
        if code_lines:
            text = "\n".join(code_lines)
            ref.code_blocks.append(RefCode(code_loc[0], text))
            ref.segments.append(Segment(text, CODE, None, code_loc[0]))
            code_lines.clear()
            code_loc.clear()

    def paragraph_loc() -> str:
        return f"paragraph {state['paragraph']} in section '{state['section']}'"

    def visit_paragraph(paragraph: Paragraph, in_table: bool) -> None:
        state["paragraph"] += 1
        style = _style_name(paragraph)
        text = _w_text(paragraph._p)
        heading = HEADING_STYLE.match(style)
        # Deviations V-f: a style named like code, or every run in a monospace font.
        is_code = (
            not in_table
            and heading is None
            and (CODE_STYLE.search(style) is not None or _is_monospace(paragraph))
        )
        if is_code:
            code_lines.append(text)
            code_loc.append(paragraph_loc())
            return
        end_code()
        if heading and not in_table:
            state["section"] = normalize(text)[:60] or state["section"]
            ref.headings.append(RefHeading(int(heading.group(1)), normalize(text), paragraph_loc()))
        if text.strip():
            ref.segments.append(Segment(text, TABLE if in_table else TEXT, None, paragraph_loc()))

    def cell_text(tc) -> str:
        """Paragraphs (also of nested tables) joined by a space."""
        return normalize(" ".join(_w_text(p) for p in tc.iter(f"{W}p")))

    def visit_table(table: Table) -> None:
        end_code()
        state["table"] += 1
        loc = f"table {state['table']} in section '{state['section']}'"
        index = len(ref.tables)
        ref.tables.append(RefTable(loc, ()))  # filled below; nested tables come after it
        rows = []
        grid = []
        for row in table.rows:
            cells = []
            seen = set()
            # row.cells repeats a merged cell in every grid column it covers
            grid.append(tuple(cell_text(cell._tc) for cell in row.cells))
            for cell in row.cells:
                if id(cell._tc) in seen:
                    continue
                seen.add(id(cell._tc))
                cells.append(cell_text(cell._tc))
                for block in cell.iter_inner_content():
                    if isinstance(block, Table):
                        visit_table(block)
                    else:
                        visit_paragraph(block, in_table=True)
            rows.append(tuple(cells))
        ref.tables[index] = RefTable(loc, tuple(rows), grid=tuple(grid))

    for block in document.iter_inner_content():
        if isinstance(block, Table):
            visit_table(block)
        else:
            visit_paragraph(block, in_table=False)
    end_code()

    body = document.element.body
    blips = body.findall(".//{http://schemas.openxmlformats.org/drawingml/2006/main}blip")
    imagedata = body.findall(".//{urn:schemas-microsoft-com:vml}imagedata")
    ref.images = [f"picture {i + 1}" for i in range(len(blips) + len(imagedata))]

    # Pandoc writes footnotes and endnotes into the Markdown, so they are reference text.
    for rel in document.part.rels.values():
        if rel.reltype.endswith(("/footnotes", "/endnotes")):
            from lxml import etree

            root = etree.fromstring(rel.target_part.blob)
            for note in root:
                if note.get(f"{W}type") in ("separator", "continuationSeparator"):
                    continue
                for p in note.iter(f"{W}p"):
                    text = _w_text(p)
                    if text.strip():
                        ref.segments.append(
                            Segment(text, TEXT, None, f"{rel.reltype.rsplit('/', 1)[-1][:-1]}")
                        )
    return ref


# -------------------------------------------------------------------- PDF

_DIGITS = re.compile(r"\d+")


def furniture_lines(pages: list[list[str]]) -> set[tuple[int, int]]:
    """(page index, line index) of running headers, footers and page numbers
    (deviations V-d): lines that appear unchanged on more than half the pages,
    and lines with one number that follows the page number ("Page 12", "- 12 -",
    "CEA-608-E 12") on more than half the pages."""
    if len(pages) < 3:
        return set()
    half = len(pages) / 2
    exact: dict[str, set[int]] = {}
    numbered: dict[tuple[str, int], set[int]] = {}
    for p, lines in enumerate(pages):
        for line in lines:
            key = normalize(line)
            if not key:
                continue
            exact.setdefault(key, set()).add(p)
            numbers = _DIGITS.findall(key)
            if numbers:
                # the last number, relative to the page index, stays the same for page numbers
                pattern = (
                    key[: key.rfind(numbers[-1])]
                    + "#"
                    + key[key.rfind(numbers[-1]) + len(numbers[-1]) :]
                )
                numbered.setdefault((pattern, int(numbers[-1]) - p), set()).add(p)
    repeated = {k for k, v in exact.items() if len(v) > half}
    paged = {k for k, v in numbered.items() if len(v) > half}
    found: set[tuple[int, int]] = set()
    for p, lines in enumerate(pages):
        for i, line in enumerate(lines):
            key = normalize(line)
            if not key:
                continue
            if key in repeated:
                found.add((p, i))
                continue
            numbers = _DIGITS.findall(key)
            if numbers:
                pattern = (
                    key[: key.rfind(numbers[-1])]
                    + "#"
                    + key[key.rfind(numbers[-1]) + len(numbers[-1]) :]
                )
                if (pattern, int(numbers[-1]) - p) in paged:
                    found.add((p, i))
    return found


def from_pdf(path: Path) -> Reference:
    import logging

    import pdfplumber
    from pypdf import PdfReader

    logging.getLogger("pypdf").setLevel(logging.ERROR)
    logging.getLogger("pdfminer").setLevel(logging.ERROR)
    reader = PdfReader(path)
    pages = [(page.extract_text() or "").split("\n") for page in reader.pages]
    furniture = furniture_lines(pages)
    ref = Reference(kind="pdf", tables_approximate=True, pages=len(pages))
    for number, lines in enumerate(pages, start=1):
        kept = [line for i, line in enumerate(lines) if (number - 1, i) not in furniture]
        text = "\n".join(kept)
        if text.strip():
            ref.segments.append(Segment(text, TEXT, None, f"page {number}"))
    if furniture:
        ref.notes.append(
            f"{len(furniture)} running header, footer or page-number line(s) left out "
            "of the PDF text (on more than half the pages)"
        )
    with pdfplumber.open(path) as pdf:
        for number, page in enumerate(pdf.pages, start=1):
            for i, table in enumerate(page.find_tables(), start=1):
                rows = tuple(
                    tuple(normalize(cell or "") for cell in row) for row in table.extract()
                )
                ref.tables.append(
                    RefTable(f"page {number}, table {i}", rows, page=number, grid=rows)
                )
    return ref


def own_cell_count(table: Tag) -> int:
    return len(own_cells(table))
