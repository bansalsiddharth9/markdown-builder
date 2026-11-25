"""Reference data from the saved source (design: Reference extraction).

Different libraries than the converter: BeautifulSoup + html5lib for HTML
(the browser reading rules exactly; the converter uses lxml),
python-docx (and its XML) for DOCX, pypdf and pdfplumber for PDF.

- HTML: visible text after the converter's cleanup rules; h1-h6; <table>
  (one-cell boxes excluded); <pre>; <img>.
- DOCX: paragraph and footnote text in document order; "Heading N" styles;
  w:tbl; paragraphs in a code style or monospace font; pictures.
- PDF: pypdf text per page without repeated header/footer lines; tables from
  pdfplumber (approximate); no headings, code blocks or images. Pages with
  characters in a font without a Unicode map are listed (deviation V-ac), and
  V3 gets the text without those lines.
- DOC (deviation DOC1): headings, tables, code and images from LibreOffice's
  .docx in the output folder (python-docx, as for DOCX); the text for V3 from
  catdoc reading the original .doc, a separate program, so text that
  LibreOffice loses is still caught.

The HTML cleanup rules (strip_tags, clean: main-content, drop_selectors) are
read from the options the converter recorded in the manifest, and applied by
code written here, never imported from specconv.
"""

from __future__ import annotations

import re
import shutil
import subprocess
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
    """A source heading; loc is a human-readable location for reports."""

    level: int
    text: str
    loc: str


@dataclass(frozen=True)
class RefTable:
    """A source table. page is set for PDF tables (pdfplumber)."""

    loc: str
    rows: tuple[tuple[str, ...], ...]  # distinct cells per row (V1 keys)
    page: int | None = None
    grid: tuple[tuple[str, ...], ...] = ()  # spans expanded (V5)


@dataclass(frozen=True)
class RefCode:
    """A source code block (<pre>, or DOCX code paragraphs)."""

    loc: str
    text: str


@dataclass(frozen=True)
class UnreadablePage:
    """A PDF page with characters drawn in a font that has no Unicode map
    (deviation V-ac): the PDF holds their pictures but not which characters
    they are, so no text reader can know them."""

    page: int
    chars: int  # characters in those fonts on this page, wrong-looking or not
    fonts: tuple[str, ...]
    lines_left_out: int  # text lines with such characters, left out of V3


@dataclass
class Reference:
    """What the checks compare the Markdown against, from one source file.

    A field that is None was not extracted for this kind of source (PDF has
    no headings, code blocks or image count); checks treat that as "not
    available", not as zero.
    """

    kind: str  # html | docx | pdf (a .doc is read as docx, see from_doc)
    headings: list[RefHeading] | None = None  # None: not available (PDF)
    tables: list[RefTable] = field(default_factory=list)
    tables_approximate: bool = False  # PDF: pdfplumber's tables
    code_blocks: list[RefCode] | None = None  # None: not available (PDF)
    images: list[str] | None = None  # locations; None: not counted (PDF)
    segments: list[Segment] = field(default_factory=list)
    pages: int | None = None
    notes: list[str] = field(default_factory=list)
    # .doc only: the text V3 compares, read independently of the converter (DOC1).
    # None: V3 uses segments.
    text_segments: list[Segment] | None = None
    text_from: str | None = None  # where V3's text came from, for the report
    # PDF only: pages with characters in fonts without a Unicode map (V-ac).
    unreadable: list[UnreadablePage] = field(default_factory=list)


# ------------------------------------------------------------------- HTML

# Page parts removed by clean: main-content, the same as the converter does.
PAGE_CHROME_ALWAYS = ("nav", "aside")
PAGE_CHROME_OUTSIDE_CONTENT = ("header", "footer")
CONTENT_TAGS = ["main", "article", "section"]


def _main_content(soup: BeautifulSoup, drop_selectors: Iterable[str]) -> None:
    """clean: main-content as recorded in deviations F7, applied in place.

    Keep only <main>, or else the outermost <article>s; if neither exists the
    whole body stays. Then drop nav and aside everywhere, header and footer
    outside main/article/section, and anything matching drop_selectors.
    """
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
            # An element inside one already removed is decomposed with it.
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


# A <meta charset=...> or <meta http-equiv=... content="...; charset=...">.
_DECLARED_CHARSET = re.compile(rb"<meta[^>]+charset", re.I)


def _decode_html(data: bytes, encoding: str | None) -> bytes | str:
    """Bytes for html5lib, or text when the page is UTF-8 without saying so.

    html5lib follows the HTML standard's last resort for pages that declare no
    encoding (windows-1252); browsers first check whether the bytes are valid
    UTF-8, and so does this. A given or declared encoding is left to html5lib.
    """
    # Look only near the top: a charset meta must be in the first 1024 bytes
    # by the HTML standard; 4096 leaves room for pages that put it later.
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
    """Reference data from a saved HTML page (deviations HP1).

    strip_tags, clean and drop_selectors are the cleanup options the
    converter recorded in the manifest; encoding is the one it recorded, if
    any. Tables with one cell are layout boxes and not counted (deviations
    V-g); every <pre> is a code block; <img> and outermost <svg> are images
    (deviations V-j).
    """
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
        """'<tag> in section ...', for findings."""
        return f"<{element.name}> in section '{section.get(id(element), 'start of document')}'"

    ref = Reference(kind="html", headings=[], code_blocks=[], images=[])
    for element in root.find_all(True):
        name = element.name
        if name in HEADING_TAGS:
            ref.headings.append(RefHeading(int(name[1]), visible_text(element), loc(element)))
        elif name == "table" and not is_layout_box(element):
            # Rows of this table only; cells of nested tables are not its cells.
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

# WordprocessingML namespace, in lxml's {uri}tag form.
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
# Word's built-in heading styles: "Heading 1" ... "Heading 9" (any case).
HEADING_STYLE = re.compile(r"^heading\s*([1-9])$", re.I)
# A style name that suggests code (deviations V-f): "Source Code", "Code Block" ...
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
    """The paragraph's style name, or "" when it has none. python-docx raises
    KeyError or ValueError for a style id that the styles part lacks."""
    try:
        return paragraph.style.name if paragraph.style is not None else ""
    except (KeyError, ValueError):
        return ""


def _is_monospace(paragraph) -> bool:
    """True when every run with text is in a monospace font (deviations V-f).

    A run without its own font uses the paragraph style's font. Fonts set
    only through a base style, the theme or document defaults are not seen.
    """
    runs = [r for r in paragraph.runs if r.text.strip()]
    if not runs:
        return False
    for run in runs:
        font = (run.font.name or "") or (paragraph.style.font.name if paragraph.style else "")
        if not any(m in (font or "").lower() for m in MONOSPACE_FONTS):
            return False
    return True


def from_docx(path: Path) -> Reference:
    """Reference data from a DOCX file, walking body content in document order.

    Consecutive code paragraphs form one code block. Paragraphs inside table
    cells are table text and never headings or code. Footnotes and endnotes
    are added as text at the end, because Pandoc writes them into the
    Markdown.
    """
    import docx
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    document = docx.Document(str(path))
    ref = Reference(kind="docx", headings=[], code_blocks=[], images=[])
    state = {"paragraph": 0, "table": 0, "section": "start of document"}
    code_lines: list[str] = []
    code_loc: list[str] = []

    def end_code() -> None:
        """Close the open run of code paragraphs as one code block."""
        if code_lines:
            text = "\n".join(code_lines)
            ref.code_blocks.append(RefCode(code_loc[0], text))
            ref.segments.append(Segment(text, CODE, None, code_loc[0]))
            code_lines.clear()
            code_loc.clear()

    def paragraph_loc() -> str:
        return f"paragraph {state['paragraph']} in section '{state['section']}'"

    def visit_paragraph(paragraph: Paragraph, in_table: bool) -> None:
        """Add one paragraph as a heading, code line or text segment."""
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
        """Add one table (V1 rows and V5 grid) and visit the paragraphs and
        nested tables in its cells."""
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
            # The V1 key and the cell contents take each merged cell only once.
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

    # Pictures: DrawingML <a:blip> (modern) and VML <v:imagedata> (legacy).
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
                # Word's built-in separator lines between body text and notes.
                if note.get(f"{W}type") in ("separator", "continuationSeparator"):
                    continue
                for p in note.iter(f"{W}p"):
                    text = _w_text(p)
                    if text.strip():
                        # location "footnote" or "endnote" (relationship type, minus the "s")
                        ref.segments.append(
                            Segment(text, TEXT, None, f"{rel.reltype.rsplit('/', 1)[-1][:-1]}")
                        )
    return ref


# -------------------------------------------------------------------- PDF

_DIGITS = re.compile(r"\d+")


# Only a page's very first and last line can be a running title: with two,
# DCDM's "Direction = ttb)" (a sentence's end) and ARIB B24's "circled number"
# (a long table's cell) were taken for headers.
EDGE_LINES = 1
EDGE_MIN_PAGES = 3  # an edge line repeated on this many pages is a running title


def _page_number_key(key: str, page: int) -> tuple[str, int] | None:
    """The line with its last number replaced by "#", and that number minus the
    page index: the same on every page for a page-number line ("Page 12")."""
    numbers = _DIGITS.findall(key)
    if not numbers:
        return None
    at = key.rfind(numbers[-1])
    return key[:at] + "#" + key[at + len(numbers[-1]) :], int(numbers[-1]) - page


def furniture_lines(pages: list[list[str]]) -> set[tuple[int, int]]:
    """(page index, line index) of running headers, footers and page numbers.

    A line is one when (deviations V-d, V-af):
    - it appears unchanged on more than half the pages, or on more than half
      the odd pages or of the even pages (headers that alternate, as in EBU
      Tech 3264: "tech 3264 EBU - …" on even, "EBU - … tech 3264" on odd pages);
    - its one number follows the page number ("Page 12", "- 12 -") on more
      than half the pages (or of the odd or even pages);
    - it is the first or last non-empty line of a page and the same at an
      edge of at least EDGE_MIN_PAGES pages (a running chapter title). A line
      repeated in the middle of pages is content (a table's column names), so
      only the edges count.
    """
    # Too few pages to tell furniture from text (on one page, every line
    # would be "on more than half the pages").
    if len(pages) < 3:
        return set()
    groups = [set(range(len(pages)))]
    for parity in (0, 1):  # page index 0 is page 1: odd pages, then even pages
        group = set(range(parity, len(pages), 2))
        if len(group) >= 3:
            groups.append(group)
    exact: dict[str, set[int]] = {}
    numbered: dict[tuple[str, int], set[int]] = {}
    at_edge: dict[str, set[int]] = {}
    keys: list[list[str]] = []
    for p, lines in enumerate(pages):
        page_keys = [normalize(line) for line in lines]
        keys.append(page_keys)
        filled = [i for i, key in enumerate(page_keys) if key]
        edges = set(filled[:EDGE_LINES] + filled[-EDGE_LINES:])
        for i, key in enumerate(page_keys):
            if not key:
                continue
            exact.setdefault(key, set()).add(p)
            number_key = _page_number_key(key, p)
            if number_key:
                numbered.setdefault(number_key, set()).add(p)
            if i in edges:
                at_edge.setdefault(key, set()).add(p)

    def frequent(on_pages: set[int]) -> bool:
        """On more than half the pages of the document, or of one parity group."""
        return any(len(on_pages & group) > len(group) / 2 for group in groups)

    repeated = {k for k, v in exact.items() if frequent(v)}
    repeated |= {k for k, v in at_edge.items() if len(v) >= EDGE_MIN_PAGES}
    paged = {k for k, v in numbered.items() if frequent(v)}
    found: set[tuple[int, int]] = set()
    for p, page_keys in enumerate(keys):
        for i, key in enumerate(page_keys):
            if key and (key in repeated or _page_number_key(key, p) in paged):
                found.add((p, i))
    return found


# pdfminer (under pdfplumber) writes "(cid:N)" for a glyph it cannot name.
_CID = "(cid:"


# A glyph name that carries its code point: "uni00E9", "u1F600".
_UNI_NAME = re.compile(r"uni(?:[0-9A-Fa-f]{4})+|u[0-9A-Fa-f]{4,6}")


def cid_fonts(chars: Iterable[dict]) -> set[str]:
    """Names of the fonts in which pdfminer could not map at least one glyph."""
    return {c["fontname"] for c in chars if c["text"].startswith(_CID)}


def has_made_up_names(font) -> bool:
    """True for a font (a pypdf font dictionary) without /ToUnicode that is a CID
    font, or whose encoding gives its glyphs names no glyph list knows ("G09",
    as in EBU Tech 3264's code-page fonts): nothing in the PDF says which
    characters it draws (deviation V-ac).

    pdfminer alone is not enough: it also writes "(cid:127)" for one stray
    code in an ordinary font (EBU's Times-Roman), whose other characters are
    fine.
    """
    from pdfminer.glyphlist import glyphname2unicode

    if "/ToUnicode" in font:
        return False
    if font.get("/Subtype") == "/Type0":  # CID font: codes are glyph numbers
        return True
    encoding = font.get("/Encoding")
    encoding = encoding.get_object() if encoding is not None else None
    if not isinstance(encoding, dict):
        return False
    names = [str(n)[1:] for n in encoding.get("/Differences", []) if isinstance(n, str)]
    return any(
        name not in glyphname2unicode and name != ".notdef" and not _UNI_NAME.fullmatch(name)
        for name in names
    )


def made_up_name_fonts(page) -> set[str]:
    """Base names of the fonts on a pypdf page that has_made_up_names (fonts
    inside form XObjects are not looked at)."""
    resources = page.get("/Resources")
    resources = resources.get_object() if resources is not None else {}
    fonts = resources.get("/Font") if isinstance(resources, dict) else None
    fonts = fonts.get_object() if fonts is not None else {}
    found = set()
    for ref in fonts.values():
        font = ref.get_object()
        if has_made_up_names(font):
            found.add(str(font.get("/BaseFont", "")).lstrip("/"))
    return found


def marked_lines(page, fonts: set[str]) -> list[tuple[str, bool]]:
    """pypdf's text lines of one page, each with True when it holds a character
    drawn in one of fonts.

    pypdf reports each piece of text with its font through visitor_text; the
    pieces add up to exactly the text extract_text returns, so the lines are
    the usual ones. If a pypdf version ever breaks that, nothing is marked.
    """
    parts: list[tuple[str, bool]] = []

    def visit(text, _cm, _tm, font, _size) -> None:
        name = str((font or {}).get("/BaseFont", "")).lstrip("/")
        parts.append((text, name in fonts))

    text = page.extract_text(visitor_text=visit) or ""
    if "".join(piece for piece, _ in parts) != text:
        return [(line, False) for line in text.split("\n")]
    lines: list[tuple[str, bool]] = []
    current: list[str] = []
    bad = False
    for piece, in_font in parts:
        for k, chunk in enumerate(piece.split("\n")):
            if k:  # a line break inside the piece ends the current line
                lines.append(("".join(current), bad))
                current, bad = [], False
            current.append(chunk)
            bad = bad or (in_font and bool(chunk.strip()))
    lines.append(("".join(current), bad))
    return lines


def from_pdf(path: Path) -> Reference:
    """Reference data from a PDF: pypdf text per page (furniture lines left
    out) and pdfplumber's tables (approximate). No headings, code blocks or
    image count: the PDF has no reliable structure for them.

    Lines with characters in a font without a Unicode map are left out of V3's
    text (ref.text_segments), and each such page is listed in ref.unreadable (V-ac):
    the PDF cannot say what those characters are, so comparing them would
    only produce noise (or, for wrong plain letters, a false pass).
    """
    import logging

    import pdfplumber
    from pypdf import PdfReader

    # Both libraries log warnings about odd objects in real-world PDFs; noise here.
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    logging.getLogger("pdfminer").setLevel(logging.ERROR)
    ref = Reference(kind="pdf", tables_approximate=True)
    font_chars: list[dict[str, int]] = []  # per page: font name -> characters
    bad_fonts: set[str] = set()  # first: fonts with a "(cid:N)"; then narrowed
    with pdfplumber.open(path) as pdf:
        for number, page in enumerate(pdf.pages, start=1):
            counts: dict[str, int] = {}
            for char in page.chars:
                counts[char["fontname"]] = counts.get(char["fontname"], 0) + 1
            font_chars.append(counts)
            bad_fonts |= cid_fonts(page.chars)
            # pdfplumber finds tables by their ruled lines (deviations FS-1):
            # boxes in diagrams count, tables without lines are missed.
            for i, table in enumerate(page.find_tables(), start=1):
                # extract() gives None for positions a merged cell covers.
                rows = tuple(
                    tuple(normalize(cell or "") for cell in row) for row in table.extract()
                )
                ref.tables.append(
                    RefTable(f"page {number}, table {i}", rows, page=number, grid=rows)
                )

    def fonts_on(index: int) -> list[str]:
        """The unmapped fonts used on page index (0-based)."""
        # pdfplumber and pypdf count the same pages; the guard is for odd files.
        counts = font_chars[index] if index < len(font_chars) else {}
        return sorted(f for f in bad_fonts if counts.get(f))

    reader = PdfReader(path)
    # Unmapped: pdfminer failed on one of its glyphs AND the font has no
    # Unicode map with made-up glyph names (both, see has_made_up_names).
    if bad_fonts:
        made_up: set[str] = set()
        for page in reader.pages:
            made_up |= made_up_name_fonts(page)
        bad_fonts &= made_up
    pages: list[list[tuple[str, bool]]] = []
    for index, page in enumerate(reader.pages):
        on_page = set(fonts_on(index))
        if on_page:
            pages.append(marked_lines(page, on_page))
        else:
            pages.append([(line, False) for line in (page.extract_text() or "").split("\n")])
    ref.pages = len(pages)
    furniture = furniture_lines([[line for line, _ in lines] for lines in pages])
    v3_segments: list[Segment] = []
    for number, lines in enumerate(pages, start=1):
        kept = [
            (line, bad) for i, (line, bad) in enumerate(lines) if (number - 1, i) not in furniture
        ]
        # Every check gets the whole text (V5 looks up readable cells such as a
        # row label in it); V3 gets it without the unreadable lines (V-ac).
        for target, text in (
            (ref.segments, "\n".join(line for line, _ in kept)),
            (v3_segments, "\n".join(line for line, bad in kept if not bad)),
        ):
            if text.strip():
                target.append(Segment(text, TEXT, None, f"page {number}"))
        fonts = fonts_on(number - 1)
        if fonts:
            ref.unreadable.append(
                UnreadablePage(
                    page=number,
                    chars=sum(font_chars[number - 1][f] for f in fonts),
                    fonts=tuple(fonts),
                    lines_left_out=sum(1 for _, bad in kept if bad),
                )
            )
    if ref.unreadable:
        ref.text_segments = v3_segments
    if furniture:
        ref.notes.append(
            f"{len(furniture)} running header, footer or page-number line(s) left out "
            "of the PDF text (on more than half the pages)"
        )
    if ref.unreadable:
        ref.notes.append(
            f"{sum(p.lines_left_out for p in ref.unreadable)} line(s) with characters in a "
            "font without a Unicode map left out of Validation3's text"
        )
    return ref


def own_cell_count(table: Tag) -> int:
    """Number of cells that belong to this table, not to nested tables."""
    return len(own_cells(table))


# -------------------------------------------------------------------- DOC

CATDOC_TIMEOUT_S = 120  # catdoc reads a .doc in well under a second


def find_catdoc() -> str | None:
    """The catdoc program, or None when it is not installed."""
    return shutil.which("catdoc")


def from_doc(path: Path, docx_path: Path) -> Reference:
    """Reference data for a legacy Word .doc (deviation DOC1).

    Structure comes from docx_path, the .docx LibreOffice made for the
    converter: nothing else reads headings and tables of a .doc reliably. The
    text for V3 comes from catdoc reading the original .doc, so a sentence
    LibreOffice dropped is still reported as missing. Table cells (tab-separated
    in catdoc's output) become separate segments. Without catdoc, V3 falls back
    to the .docx text and a note says the text check is not independent.
    """
    ref = from_docx(docx_path)
    ref.text_from = docx_path.name
    catdoc = find_catdoc()
    if catdoc is None:
        ref.notes.append(
            "catdoc not found: Validation3 compares the text with LibreOffice's .docx, not an "
            "independent reading of the .doc (install catdoc)"
        )
        return ref
    proc = subprocess.run(
        # -w: no line wrapping (one line per paragraph); -d utf-8: output encoding.
        [catdoc, "-w", "-d", "utf-8", str(path)],
        capture_output=True,
        timeout=CATDOC_TIMEOUT_S,
        check=False,
    )
    if proc.returncode != 0:
        ref.notes.append(
            f"catdoc failed (exit {proc.returncode}): Validation3 compares the text with "
            "LibreOffice's .docx instead"
        )
        return ref
    segments = []
    for number, line in enumerate(proc.stdout.decode("utf-8", "replace").splitlines(), 1):
        for cell in line.split("\t"):
            if cell.strip():
                segments.append(Segment(cell.strip(), TEXT, None, f"line {number} (catdoc)"))
    ref.text_segments = segments
    ref.text_from = "catdoc"
    return ref
