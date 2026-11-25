"""Tables with characters in a font without a Unicode map (deviation Q23).

The PDF holds the picture of such a character but not which character it is
(EBU Tech 3264's code-page tables), so Docling writes U+FFFD or a wrong
letter. The converter finds those characters with pdfium and puts a picture
of each affected table, cut from the PDF page, under the table with a note.
Tested without running Docling: small DoclingDocuments stand in for its output.
"""

from __future__ import annotations

from pathlib import Path

from docling_core.transforms.serializer.markdown import MarkdownDocSerializer
from docling_core.types.doc import (
    BoundingBox,
    CoordOrigin,
    DoclingDocument,
    ProvenanceItem,
    Size,
    TableCell,
    TableData,
)
from specconv.converters.docling import _span_aware_table_serializer
from specconv.converters.glyphs import table_images, unmapped_chars


def add_table(doc: DoclingDocument, rows: list[list[str]], box: tuple[float, float, float, float]):
    """Add a table on page 1 whose bounding box is (left, bottom, right, top) in
    PDF points (origin bottom left, as in the PDF)."""
    cells = [
        TableCell(
            text=text,
            start_row_offset_idx=r,
            end_row_offset_idx=r + 1,
            start_col_offset_idx=c,
            end_col_offset_idx=c + 1,
        )
        for r, row in enumerate(rows)
        for c, text in enumerate(row)
    ]
    left, bottom, right, top = box
    prov = ProvenanceItem(
        page_no=1,
        bbox=BoundingBox(l=left, b=bottom, r=right, t=top, coord_origin=CoordOrigin.BOTTOMLEFT),
        charspan=(0, 0),
    )
    return doc.add_table(
        data=TableData(num_rows=len(rows), num_cols=len(rows[0]), table_cells=cells), prov=prov
    )


def codes_document() -> tuple[DoclingDocument, object, object]:
    """What Docling makes of the fixture page, plus a second table elsewhere on
    the page that has only ordinary characters."""
    doc = DoclingDocument(name="codes")
    doc.add_page(page_no=1, size=Size(width=595, height=842))
    codes = add_table(doc, [["Alpha", "��"], ["Beta", "�A"]], (50, 550, 450, 700))
    other = add_table(doc, [["Plain", "text"]], (50, 100, 200, 140))
    return doc, codes, other


def test_unmapped_characters_found_with_their_position(unmapped_pdf: Path) -> None:
    found = unmapped_chars(unmapped_pdf)
    assert list(found) == [1]
    # Three glyphs without a name and the plain "A" from the same font.
    assert len(found[1]) == 4
    assert all(250 <= x <= 350 and 625 <= y <= 675 for x, y in found[1])


def test_ordinary_fonts_have_no_unmapped_characters(mapped_pdf: Path) -> None:
    assert unmapped_chars(mapped_pdf) == {}


def test_picture_made_only_for_the_table_with_unmapped_characters(
    tmp_path: Path, unmapped_pdf: Path
) -> None:
    doc, codes, other = codes_document()
    found = table_images(doc, unmapped_pdf, tmp_path, unmapped_chars(unmapped_pdf))
    assert list(found) == [codes.self_ref]
    info = found[codes.self_ref]
    assert info["page"] == 1 and info["chars"] == 4
    image = tmp_path / info["image"]
    assert info["image"].startswith("media/") and image.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_note_and_picture_written_under_the_table(tmp_path: Path, unmapped_pdf: Path) -> None:
    doc, codes, other = codes_document()
    found = table_images(doc, unmapped_pdf, tmp_path, unmapped_chars(unmapped_pdf))
    md = (
        MarkdownDocSerializer(doc=doc, table_serializer=_span_aware_table_serializer(found))
        .serialize()
        .text
    )
    image = found[codes.self_ref]["image"]
    table_end = md.index("| Beta ")
    note = md.index("no Unicode map")
    picture = md.index(f"![Table on page 1 of the PDF]({image})")
    assert table_end < note < picture < md.index("| Plain ")
    assert md.count("![") == 1  # nothing added to the ordinary table


MODELS = Path(__file__).resolve().parents[2] / "models"


def test_docling_conversion_adds_note_picture_and_warning(
    tmp_path: Path, unmapped_pdf: Path
) -> None:
    """End to end with Docling on the fixture page (about 20 s)."""
    from specconv.converters.docling import DoclingConverter

    result = DoclingConverter(MODELS).convert(unmapped_pdf, tmp_path)
    [info] = result.details["tables_with_unmapped_glyphs"]
    assert info["page"] == 1 and info["chars"] == 4
    assert (tmp_path / info["image"]).is_file()
    assert Path(info["image"]) in result.media
    assert f"![Table on page 1 of the PDF]({info['image']})" in result.markdown
    assert any("no Unicode map" in w and "page 1" in w for w in result.warnings)
    assert result.options["unmapped_glyph_tables"] == "picture added under the table"
