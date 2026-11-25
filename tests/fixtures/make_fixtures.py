"""Generate the synthetic fixture specs (design: Testing).

One content model, rendered as HTML and as DOCX (python-docx), so both
converters are tested against the same spec. Phase 3 adds the PDF rendering
(the HTML printed with Playwright) and an image-only PDF.

    uv run python tests/fixtures/make_fixtures.py OUT_DIR

Nothing here is copied from a real standard.
"""

from __future__ import annotations

import html
import struct
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path

# ----------------------------------------------------------------- content


@dataclass(frozen=True)
class Heading:
    level: int
    text: str
    anchor: str


@dataclass(frozen=True)
class Para:
    text: str


@dataclass(frozen=True)
class Cell:
    text: str
    colspan: int = 1
    rowspan: int = 1


@dataclass(frozen=True)
class Table:
    caption: str
    header: list[list[Cell]]  # header rows
    body: list[list[str]]


@dataclass(frozen=True)
class Code:
    lang: str
    text: str


@dataclass(frozen=True)
class Image:
    file: str
    alt: str


TTML_EXAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<tt xmlns="http://www.w3.org/ns/ttml" xmlns:tts="http://www.w3.org/ns/ttml#styling"
    xmlns:ttp="http://www.w3.org/ns/ttml#parameter" ttp:frameRate="30">
  <head>
    <layout>
      <region xml:id="r1" tts:origin="10% 80%" tts:extent="80% 10%"/>
    </layout>
  </head>
  <body>
    <div>
      <p begin="00:00:01:00" end="00:00:03:15" region="r1">Example caption</p>
    </div>
  </body>
</tt>"""

WEBVTT_EXAMPLE = """WEBVTT

1
00:00:01.000 --> 00:00:03.500
Example caption one

2
00:00:04.000 --> 00:00:06.250 line:90%
Example caption two"""

SPEC: list[Heading | Para | Table | Code | Image] = [
    Heading(1, "Sample Caption Display Specification", "title"),
    Para("This synthetic document exercises the converter. It is not a real standard."),
    Heading(1, "1 Scope", "sec-1"),
    Para(
        "A decoder shall support pop-on, roll-up and paint-on caption modes. "
        "Values such as 0x14 and 2Ch must be preserved exactly."
    ),
    Heading(2, "1.1 Conformance", "sec-1-1"),
    Para(
        "An encoder should not send more than 32 characters per row. "
        "Support for paint-on mode is optional. Café, naïve and en dashes – are kept."
    ),
    Heading(1, "2 Control codes", "sec-2"),
    Para("Table 1 lists the miscellaneous control codes; see also section 3 (#sec-3)."),
    Table(
        "Table 1 – Miscellaneous control codes",
        header=[
            [Cell("Byte pair", colspan=2), Cell("Command", rowspan=2), Cell("Meaning", rowspan=2)],
            [Cell("Byte 1"), Cell("Byte 2")],
        ],
        body=[
            ["14", "20", "RCL", "Resume caption loading"],
            ["14", "25", "RU2", "Roll-up captions, 2 rows"],
            ["14", "2C", "EDM", "Erase displayed memory"],
            ["14", "2F", "EOC", "End of caption"],
        ],
    ),
    Para("The byte pair 9420 is the same command with odd parity applied."),
    Heading(1, "3 Timed text example", "sec-3"),
    Para("The following document places one caption at the bottom of the frame."),
    Code("xml", TTML_EXAMPLE),
    Heading(1, "4 WebVTT example", "sec-4"),
    Code("text", WEBVTT_EXAMPLE),
    Heading(1, "5 Caption modes", "sec-5"),
    Heading(2, "5.1 Roll-up captions", "sec-5-1"),
    Para(
        "In roll-up mode the display shall not exceed 4 rows. A frame rate of 29.97 fps "
        "and a timecode of 01:00:00;02 are typical."
    ),
    Heading(3, "5.1.1 Base row", "sec-5-1-1"),
    Para("The base row is the lowest row of the roll-up window."),
    Heading(2, "5.2 Pop-on captions", "sec-5-2"),
    Para("Pop-on captions are loaded off screen and displayed with EOC."),
    Image("safe-area.png", "Caption safe area"),
    Table(
        "Table 2 – Safe area",
        header=[[Cell("Property"), Cell("Value")]],
        body=[["Rows", "15 rows"], ["Columns", "32 columns"], ["Margin", "10%"]],
    ),
]

STRIPPED_TEXT = "THIS TEXT MUST BE STRIPPED"  # inside <script>, <style> and <noscript>


# ------------------------------------------------------------------- image


def tiny_png(rgb: tuple[int, int, int] = (0, 128, 255)) -> bytes:
    """A valid 2x2 PNG (scaled up by CSS where a visible figure is needed)."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    row = b"\x00" + bytes(rgb) * 2
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(row * 2, 9))
        + chunk(b"IEND", b"")
    )


# -------------------------------------------------------------------- HTML


def render_html(spec: list = SPEC) -> str:
    e = html.escape
    out = [
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        "<title>Sample Caption Display Specification</title>",
        f"<style>body {{ font-family: serif; }} /* {STRIPPED_TEXT} */\n"
        "table, th, td { border: 1px solid #000; border-collapse: collapse; }\n"
        "th, td { padding: 4px 8px; } img { width: 240px; height: 160px; }\n"
        "pre { background: #f4f4f4; border: 1px solid #ccc; padding: 8px; }</style>",
        f"<script>console.log('{STRIPPED_TEXT}');</script>",
        "</head>",
        "<body>",
        f"<noscript>{STRIPPED_TEXT}</noscript>",
    ]
    for block in spec:
        if isinstance(block, Heading):
            out.append(f'<h{block.level} id="{block.anchor}">{e(block.text)}</h{block.level}>')
        elif isinstance(block, Para):
            out.append(f"<p>{e(block.text)}</p>")
        elif isinstance(block, Table):
            out.append("<table>")
            out.append(f"<caption>{e(block.caption)}</caption>")
            out.append("<thead>")
            for row in block.header:
                cells = "".join(
                    "<th"
                    + (f' colspan="{c.colspan}"' if c.colspan > 1 else "")
                    + (f' rowspan="{c.rowspan}"' if c.rowspan > 1 else "")
                    + f">{e(c.text)}</th>"
                    for c in row
                )
                out.append(f"<tr>{cells}</tr>")
            out.append("</thead>")
            out.append("<tbody>")
            for row in block.body:
                out.append("<tr>" + "".join(f"<td>{e(v)}</td>" for v in row) + "</tr>")
            out.append("</tbody>")
            out.append("</table>")
        elif isinstance(block, Code):
            out.append(f'<pre><code class="language-{block.lang}">{e(block.text)}</code></pre>')
        elif isinstance(block, Image):
            out.append(f'<p><img src="{block.file}" alt="{e(block.alt)}"></p>')
    out += ["</body>", "</html>", ""]
    return "\n".join(out)


# -------------------------------------------------------------------- DOCX


def render_docx(path: Path, image_dir: Path, spec: list = SPEC) -> None:
    import docx
    from docx.enum.style import WD_STYLE_TYPE
    from docx.shared import Inches

    document = docx.Document()
    # "Source Code" is the paragraph style Pandoc's DOCX reader treats as code.
    code_style = document.styles.add_style("Source Code", WD_STYLE_TYPE.PARAGRAPH)
    code_style.font.name = "Courier New"
    for block in spec:
        if isinstance(block, Heading):
            document.add_heading(block.text, level=block.level)
        elif isinstance(block, Para):
            document.add_paragraph(block.text)
        elif isinstance(block, Table):
            document.add_paragraph(block.caption, style="Caption")
            n_cols = sum(c.colspan for c in block.header[0])
            table = document.add_table(rows=len(block.header) + len(block.body), cols=n_cols)
            table.style = "Table Grid"
            # Lay out header cells honoring spans (cells covered by a rowspan are skipped).
            covered: set[tuple[int, int]] = set()
            for r, row in enumerate(block.header):
                c = 0
                for cell in row:
                    while (r, c) in covered:
                        c += 1
                    top_left = table.cell(r, c)
                    bottom_right = table.cell(r + cell.rowspan - 1, c + cell.colspan - 1)
                    merged = (
                        top_left.merge(bottom_right)
                        if (cell.rowspan, cell.colspan) != (1, 1)
                        else top_left
                    )
                    merged.text = cell.text
                    for dr in range(cell.rowspan):
                        for dc in range(cell.colspan):
                            covered.add((r + dr, c + dc))
                    c += cell.colspan
            for r, row in enumerate(block.body, start=len(block.header)):
                for c, value in enumerate(row):
                    table.cell(r, c).text = value
        elif isinstance(block, Code):
            for line in block.text.split("\n"):
                document.add_paragraph(line, style="Source Code")
        elif isinstance(block, Image):
            document.add_picture(str(image_dir / block.file), width=Inches(1))
            document.add_paragraph(block.alt, style="Caption")
    document.save(str(path))


# -------------------------------------------------------------- saved page

SAVED_URL = "https://www.w3.org/TR/2099/REC-sample-20990101/"
SAVED_NAME = "Sample Saved Spec"


def render_saved_page() -> str:
    """A page as a browser's "Save page as, complete" writes it (design: Testing):
    in-page links written as the document's full URL, a saved-from comment,
    images in a _files folder, ids on a section and its heading, and a table
    whose rows are link targets. Also a web image, a missing image, an old-style
    empty anchor inside a heading, a link to another spec and one to the top."""
    u = SAVED_URL
    files = "./Sample%20Saved%20Spec_files"
    return f"""<!DOCTYPE html>
<!-- saved from url=({len(u):04d}){u} -->
<html lang="en"><head><meta charset="utf-8">
<title>Sample Saved Spec</title>
<link rel="stylesheet" href="{files}/base.css">
<script src="{files}/respec.js"></script>
</head><body>
<nav id="toc"><ol>
<li><a href="{u}#sec-intro">1 Introduction</a></li>
<li><a href="{u}#sec-codes">2 Codes</a></li>
</ol></nav>
<section id="sec-intro"><h2 id="x1-introduction"><a id="intro"></a>1 Introduction</h2>
<p>See the <a href="{u.replace("https:", "http:")}index.html#tbl-row-eoc">EOC row</a> and the
<dfn id="dfn-caption">caption</dfn> definition. Another spec:
<a href="https://www.w3.org/TR/ttml2/#timing">TTML2 timing</a>. Top of
<a href="{u}">this document</a>.</p>
<p><img src="{files}/figure-1.png" alt="Figure 1">
<img src="https://www.w3.org/logo.png" alt="W3C logo">
<img src="{files}/missing.png" alt="Missing figure"></p>
</section>
<section id="sec-codes"><h2 id="x2-codes">2 Codes</h2>
<table><caption>Table 1</caption>
<tr><th>Code</th><th>Command</th></tr>
<tr id="tbl-row-rcl"><td>1420</td><td>RCL</td></tr>
<tr id="tbl-row-eoc"><td>142F</td><td>EOC</td></tr>
</table>
<p>Uses <a href="{u}#dfn-caption">caption</a> and row <a href="#tbl-row-rcl">RCL</a>.</p>
</section>
</body></html>
"""


def build_saved_page(dest: Path) -> Path:
    """Write the saved page and its _files folder into dest; return the HTML path."""
    files = dest / f"{SAVED_NAME}_files"
    files.mkdir(parents=True, exist_ok=True)
    (files / "figure-1.png").write_bytes(tiny_png((200, 0, 0)))
    (files / "base.css").write_text("body { margin: 0; }\n", encoding="utf-8")
    (files / "respec.js").write_text("// saved script\n", encoding="utf-8")
    (files / "frame.html").write_text("<p>saved iframe</p>\n", encoding="utf-8")
    page = dest / f"{SAVED_NAME}.html"
    page.write_text(render_saved_page(), encoding="utf-8")
    return page


# --------------------------------------------------------------------- PDF


def render_pdfs(dest: Path, html: Path) -> dict[str, Path]:
    """PDF fixtures (design: Testing), made with Playwright's Chromium:

    - sample-spec.pdf: the HTML fixture printed with page.pdf() (born-digital)
    - image-only.pdf: screenshots of that page, so no page has a text layer
    - mostly-text.pdf: 3 text pages + 1 image page (75% text: Docling, mixed)
    - mostly-image.pdf: 1 text page + 3 image pages (25% text: no_text_layer, mixed)
    """
    import base64

    from playwright.sync_api import sync_playwright
    from pypdf import PdfReader, PdfWriter

    text_pdf = dest / "sample-spec.pdf"
    image_pdf = dest / "image-only.pdf"
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(html.resolve().as_uri())
        page.pdf(path=str(text_pdf), format="A4", print_background=True)
        shot = base64.b64encode(page.screenshot(full_page=True)).decode("ascii")
        page.set_content(
            "<style>@page{margin:0} body{margin:0} img{width:100%}</style>"
            + "".join(f'<img src="data:image/png;base64,{shot}">' for _ in range(2))
        )
        page.pdf(path=str(image_pdf), format="A4")
        browser.close()

    def merge(target: Path, parts: list[tuple[Path, int]]) -> Path:
        writer = PdfWriter()
        for source, index in parts:
            writer.add_page(PdfReader(source).pages[index])
        with target.open("wb") as fh:
            writer.write(fh)
        return target

    # Pages are reused (modulo) because the printed fixture is only two pages long.
    n_text = len(PdfReader(text_pdf).pages)
    n_image = len(PdfReader(image_pdf).pages)
    return {
        "pdf": text_pdf,
        "image_pdf": image_pdf,
        "mostly_text_pdf": merge(
            dest / "mostly-text.pdf",
            [(text_pdf, i % n_text) for i in range(3)] + [(image_pdf, 0)],
        ),
        "mostly_image_pdf": merge(
            dest / "mostly-image.pdf",
            [(text_pdf, 0)] + [(image_pdf, i % n_image) for i in range(3)],
        ),
    }


# ---------------------------------------------------- unmapped-font PDF

UNMAPPED_SENTENCE = "The table below lists two control codes for testing only."


def unmapped_font_pdf(path: Path, unmapped: bool = True) -> Path:
    """A one-page A4 PDF with a ruled table (header and five rows) whose
    "Glyph" cells in rows 01 and 02 use a font without a Unicode map, like the
    code-page tables in EBU Tech 3264.

    The font (Type1, not embedded, no ToUnicode) names its glyphs /G01../G03,
    which no glyph list knows, so readers report "(cid:1)" or "/G01". Code
    0x41 has no entry in its Differences and falls back to a plain "A": a wrong
    letter that looks fine. With unmapped=False those cells use Helvetica
    ("XY", "ZA"), as a control. A Times-Roman character with no name in its
    encoding sits above the sentence: pdfminer cannot map it either, but its
    font is an ordinary one. Written by hand: Chromium-made fixtures always
    have a Unicode map. The table spans x 50..450 and y 550..700 (PDF points),
    the Glyph column x 250..350, the two rows y 625..675.
    """
    glyph_font = "/F2" if unmapped else "/F1"
    glyph_cells = ("<0102>", "<0341>") if unmapped else ("(XY)", "(ZA)")
    rows = [
        ("(Code)", "(Name)", "(Glyph)", "(Use)"),
        ("(01)", "(Alpha)", glyph_cells[0], "(Start)"),
        ("(02)", "(Beta)", glyph_cells[1], "(Stop)"),
        ("(03)", "(Gamma)", "(G)", "(Pause)"),
        ("(04)", "(Delta)", "(D)", "(Resume)"),
        ("(05)", "(Epsilon)", "(E)", "(Reset)"),
    ]
    ops = ["0.5 w"]
    for i in range(len(rows) + 1):  # horizontal rules, 25 pt apart from y 700 down
        ops.append(f"50 {700 - 25 * i} m 450 {700 - 25 * i} l S")
    for x in (50, 150, 250, 350, 450):  # vertical rules
        ops.append(f"{x} 550 m {x} 700 l S")
    ops.append(f"BT /F1 11 Tf 50 740 Td ({UNMAPPED_SENTENCE}) Tj ET")
    # Code 127 in plain Times-Roman: StandardEncoding has nothing there, so
    # pdfminer reports "(cid:127)" (seen in EBU's Times-Roman). The font has no
    # made-up glyph names, so it must not count as unmapped.
    ops.append("BT /F3 11 Tf 50 760 Td <7F> Tj ET")
    for r, row in enumerate(rows):
        for c, cell in enumerate(row):
            font = glyph_font if r in (1, 2) and c == 2 else "/F1"
            ops.append(f"BT {font} 11 Tf {58 + 100 * c} {683 - 25 * r} Td {cell} Tj ET")
    content = ("\n".join(ops) + "\n").encode()
    widths = " ".join(["600"] * 65)  # codes 1..65
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 5 0 R /F2 6 0 R /F3 8 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /ABCDEF+MSTT31test /FirstChar 1 "
        b"/LastChar 65 /Widths [" + widths.encode() + b"] /FontDescriptor 7 0 R "
        b"/Encoding << /Type /Encoding /Differences [1 /G01 /G02 /G03] >> >>",
        # A descriptor gives the font its name in pdfminer (without one: "unknown").
        b"<< /Type /FontDescriptor /FontName /ABCDEF+MSTT31test /Flags 4 "
        b"/FontBBox [0 0 1000 1000] /ItalicAngle 0 /Ascent 900 /Descent -100 "
        b"/CapHeight 700 /StemV 80 >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Times-Roman >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(out))
    return path


# --------------------------------------------------------------------- all


def build_all(dest: Path) -> dict[str, Path]:
    """Write all fixtures into dest; return name -> path."""
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "safe-area.png").write_bytes(tiny_png())
    (dest / "sample-spec.html").write_text(render_html(), encoding="utf-8")
    render_docx(dest / "sample-spec.docx", dest)
    return {
        "html": dest / "sample-spec.html",
        "docx": dest / "sample-spec.docx",
        "image": dest / "safe-area.png",
    }


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    for name, path in build_all(Path(sys.argv[1])).items():
        print(f"{name}: {path}")
