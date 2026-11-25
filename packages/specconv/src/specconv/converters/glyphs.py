"""Tables with characters drawn in a font without a Unicode map (deviation Q23).

Some PDFs (EBU Tech 3264's code-page tables, made by an old Windows printer
driver) draw symbols with fonts whose glyphs have no Unicode meaning: the PDF
holds each glyph's picture, but not which character it is. Docling then writes
U+FFFD, or a plain letter where the font reuses an ordinary code, which looks
right but is wrong. No text reader can do better, so the converter adds a
picture of every such table, cut from the PDF page, under the table.

- pdfium (pypdfium2, already installed with Docling) reports per character
  whether its font could not map it to Unicode. A font with even one such
  character is treated as unmapped as a whole: its other characters may be
  the wrong plain letters.
- A table is affected when the centre of an unmapped character lies inside
  its bounding box. The picture has a margin around the box, so labels just
  outside the grid (a code table's "First byte" axis) are in it too.
"""

from __future__ import annotations

import ctypes
from pathlib import Path
from typing import Any

from specconv.converters.base import MEDIA_DIR

IMAGE_SCALE = 2.0  # 144 dpi, the same as Docling's pictures (Q7)
MARGIN_PT = 28.0  # about 1 cm around the table, for labels outside its grid
_FONT_NAME_BUF = 256  # bytes for a font name from pdfium

Point = tuple[float, float]


def _font_name(textpage: Any, index: int, buf: Any) -> str:
    """pdfium's name for the font of character index on textpage."""
    import pypdfium2.raw as pdfium_c

    flags = ctypes.c_int()
    pdfium_c.FPDFText_GetFontInfo(textpage, index, buf, _FONT_NAME_BUF, ctypes.byref(flags))
    return buf.value.decode("utf-8", "replace")


def unmapped_chars(pdf: Path) -> dict[int, list[Point]]:
    """Page number (1-based) -> centres (x, y in PDF points, origin bottom left)
    of the characters drawn in fonts without a Unicode map. Empty for most PDFs.

    Two passes: the first only asks pdfium which characters failed to map
    (one call per character, so ordinary PDFs stay fast); the second, only
    when there are such fonts, collects every character in them.
    """
    import pypdfium2 as pdfium
    import pypdfium2.raw as pdfium_c

    buf = ctypes.create_string_buffer(_FONT_NAME_BUF)
    document = pdfium.PdfDocument(pdf)
    try:
        bad_fonts: set[str] = set()
        for page in document:
            textpage = page.get_textpage()
            for i in range(textpage.count_chars()):
                if pdfium_c.FPDFText_HasUnicodeMapError(textpage, i) == 1:
                    bad_fonts.add(_font_name(textpage, i, buf))
        if not bad_fonts:
            return {}
        found: dict[int, list[Point]] = {}
        for number, page in enumerate(document, start=1):
            textpage = page.get_textpage()
            for i in range(textpage.count_chars()):
                # pdfium adds line breaks and spaces of its own, in the font of
                # the character before them; they are not in the PDF.
                if pdfium_c.FPDFText_IsGenerated(textpage, i) == 1:
                    continue
                if _font_name(textpage, i, buf) in bad_fonts:
                    left, bottom, right, top = textpage.get_charbox(i)
                    found.setdefault(number, []).append(((left + right) / 2, (bottom + top) / 2))
        return found
    finally:
        document.close()


def _render(pdf: Path, page_no: int, box: tuple[float, float, float, float], dest: Path) -> None:
    """Save the area box (left, bottom, right, top in PDF points) of page page_no,
    plus MARGIN_PT on every side within the page, as a PNG at dest."""
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(pdf)
    try:
        page = document[page_no - 1]
        width, height = page.get_size()
        left, bottom, right, top = box
        # pdfium's crop is how much to cut off each side: left, bottom, right, top.
        crop = (
            max(0.0, left - MARGIN_PT),
            max(0.0, bottom - MARGIN_PT),
            max(0.0, width - right - MARGIN_PT),
            max(0.0, height - top - MARGIN_PT),
        )
        dest.parent.mkdir(parents=True, exist_ok=True)
        page.render(scale=IMAGE_SCALE, crop=crop).to_pil().save(dest)
    finally:
        document.close()


def table_images(
    doc: Any, pdf: Path, out_dir: Path, chars: dict[int, list[Point]]
) -> dict[str, dict[str, Any]]:
    """Picture every table of doc (a DoclingDocument) that holds characters from
    chars (see unmapped_chars) and save it in out_dir/media.

    Returns table self_ref -> {"page", "chars", "image"} with image relative to
    out_dir, for the Markdown serializer and conversion.json.
    """
    found: dict[str, dict[str, Any]] = {}
    for table in doc.tables:
        for prov in table.prov[:1]:  # a table split over pages: its first part
            points = chars.get(prov.page_no)
            page = doc.pages.get(prov.page_no)
            if not points or page is None:
                continue
            box = prov.bbox.to_bottom_left_origin(page_height=page.size.height)
            inside = sum(1 for x, y in points if box.l <= x <= box.r and box.b <= y <= box.t)
            if not inside:
                continue
            image = f"{MEDIA_DIR}/unmapped-table-p{prov.page_no}-{len(found) + 1}.png"
            _render(pdf, prov.page_no, (box.l, box.b, box.r, box.t), out_dir / image)
            found[table.self_ref] = {"page": prov.page_no, "chars": inside, "image": image}
    return found


def note_and_picture(info: dict[str, Any]) -> str:
    """The Markdown put under an affected table: a short note and the picture."""
    page = info["page"]
    return (
        "*Some symbols in this table use a font with no Unicode map in the PDF, so "
        "the text above shows � or wrong characters for them. The picture below "
        f"is the table as printed on page {page}; use it as the reference.*\n\n"
        f"![Table on page {page} of the PDF]({info['image']})"
    )
