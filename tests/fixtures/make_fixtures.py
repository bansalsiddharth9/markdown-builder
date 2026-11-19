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
    """A valid 2x2 PNG."""

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
        f"<style>body {{ font-family: serif; }} /* {STRIPPED_TEXT} */</style>",
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
