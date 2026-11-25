"""Code blocks with their line breaks, rebuilt from the PDF (deviation Q24).

Docling joins the lines of a code block with single spaces, so an XML sample
from a spec (DCDM, SMPTE ST 428-7) became one long line. The PDF itself has no
"line break" either: each character is drawn at a position. So the lines are
rebuilt from those positions with pdfium (pypdfium2, installed with Docling):

- the characters whose centre lies inside the code block's box (Docling's
  layout), spaces left out because PDFs often do not draw them;
- grouped into lines by height, read top to bottom, left to right;
- indentation and the spaces inside a line from the gaps, measured in
  character widths (code is set in a monospace font).

The rebuilt text is used only when it has exactly the characters of Docling's
text, ignoring spacing and typographic quote/dash variants (Docling writes
' for the PDF's ’). Anything else (a box that caught a neighbouring line, a
proportional font that confuses the spacing) keeps Docling's text.
"""

from __future__ import annotations

import statistics
from pathlib import Path
from typing import Any, NamedTuple

BOX_PAD_PT = 2.0  # Docling's boxes can clip a character's edge
SPACE_GAP = 0.5  # a gap wider than half a character is a space
# Typographic variants Docling (and readers) replace with plain ASCII.
_FOLD = str.maketrans({"‘": "'", "’": "'", "‚": "'", "‛": "'", "“": '"', "”": '"', "„": '"'})
_DASHES = "‐‑‒–—―−"


class PdfChar(NamedTuple):
    """One drawn character and its box in PDF points (origin bottom left)."""

    text: str
    left: float
    right: float
    bottom: float
    top: float


def same_characters(a: str, b: str) -> bool:
    """True when a and b differ only in whitespace and quote/dash variants."""

    def key(text: str) -> str:
        text = text.translate(_FOLD)
        for dash in _DASHES:
            text = text.replace(dash, "-")
        return "".join(text.split())

    return key(a) == key(b)


def rebuild_lines(chars: list[PdfChar]) -> str:
    """The text of chars as lines: indentation and inner spaces from the gaps
    between characters, in units of the typical character width."""
    if not chars:
        return ""
    width = statistics.median(c.right - c.left for c in chars) or 1.0
    height = statistics.median(c.top - c.bottom for c in chars) or 1.0
    rows: list[tuple[float, list[PdfChar]]] = []
    for char in sorted(chars, key=lambda c: -(c.bottom + c.top) / 2):
        middle = (char.bottom + char.top) / 2
        # Same line when the middles are less than half a line apart.
        if rows and abs(rows[-1][0] - middle) < height / 2:
            rows[-1][1].append(char)
        else:
            rows.append((middle, [char]))
    left = min(c.left for c in chars)
    lines = []
    for _, row in rows:
        row.sort(key=lambda c: c.left)
        text = " " * round((row[0].left - left) / width)
        previous = row[0].left
        for char in row:
            gap = char.left - previous
            if gap > width * SPACE_GAP:
                text += " " * max(1, round(gap / width))
            text += char.text
            previous = char.right
        lines.append(text.rstrip())
    return "\n".join(lines)


def _chars_in(textpage: Any, box: tuple[float, float, float, float]) -> list[PdfChar]:
    """The drawn, non-space characters of textpage whose centre is in box
    (left, bottom, right, top), padded by BOX_PAD_PT."""
    import pypdfium2.raw as pdfium_c

    left, bottom, right, top = box
    found = []
    for i in range(textpage.count_chars()):
        # pdfium adds spaces and line breaks of its own; they are not drawn.
        if pdfium_c.FPDFText_IsGenerated(textpage, i) == 1:
            continue
        text = chr(pdfium_c.FPDFText_GetUnicode(textpage, i))
        if text.isspace() or text == "\x00":
            continue
        x0, y0, x1, y1 = textpage.get_charbox(i, loose=True)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        if (
            left - BOX_PAD_PT <= cx <= right + BOX_PAD_PT
            and bottom - BOX_PAD_PT <= cy <= top + BOX_PAD_PT
        ):
            found.append(PdfChar(text, x0, x1, y0, y1))
    return found


def code_texts(doc: Any, pdf: Path) -> dict[str, str]:
    """Code item self_ref -> its text with line breaks, for every code block of
    doc (a DoclingDocument) whose rebuilt text passes same_characters."""
    import pypdfium2 as pdfium

    codes = [t for t in doc.texts if getattr(t.label, "value", t.label) == "code" and t.prov]
    if not codes:
        return {}
    found: dict[str, str] = {}
    document = pdfium.PdfDocument(pdf)
    try:
        textpages: dict[int, Any] = {}
        for item in codes:
            parts = []
            for prov in item.prov:  # a block split over pages: one part per page
                page = doc.pages.get(prov.page_no)
                if page is None:
                    break
                if prov.page_no not in textpages:
                    textpages[prov.page_no] = document[prov.page_no - 1].get_textpage()
                box = prov.bbox.to_bottom_left_origin(page_height=page.size.height)
                parts.append(
                    rebuild_lines(_chars_in(textpages[prov.page_no], (box.l, box.b, box.r, box.t)))
                )
            text = "\n".join(parts)
            if text and same_characters(text, item.text):
                found[item.self_ref] = text
    finally:
        document.close()
    return found
