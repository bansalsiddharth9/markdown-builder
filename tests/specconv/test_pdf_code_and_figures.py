"""Code lines and figure text from PDFs (deviations Q24 and Q25).

- Docling joins the lines of a code block with spaces (DCDM's XML samples
  came out as one line). The converter rebuilds the lines, indentation
  included, from the positions of the characters in the PDF, and uses them
  only when they hold the same characters as Docling's text (Q24).
- Docling reads the text inside a picture (a drawing's labels, an XML
  example drawn as a figure) but writes only the image. The converter
  writes that text under the image too (Q25).
"""

from __future__ import annotations

from docling_core.transforms.serializer.markdown import MarkdownDocSerializer
from docling_core.types.doc import DocItemLabel, DoclingDocument
from specconv.converters.docling import _formula_text_serializer, _picture_text_serializer
from specconv.converters.pdfcode import PdfChar, rebuild_lines, same_characters

WIDTH = 6.0  # a monospace character's width in the made-up layouts below


def line(text: str, column: int, y: float) -> list[PdfChar]:
    """The characters of text (spaces are not drawn, as in a PDF) starting at
    column, on the line at height y."""
    return [
        PdfChar(c, 50 + WIDTH * (column + i), 50 + WIDTH * (column + i + 1), y, y + 10)
        for i, c in enumerate(text)
        if c != " "
    ]


def test_lines_indentation_and_inner_spaces_rebuilt() -> None:
    chars = line("<a>", 0, 700) + line("<b x='1'/>", 2, 688) + line("x  y", 0, 676)
    assert rebuild_lines(chars) == "<a>\n  <b x='1'/>\nx  y"


def test_lines_read_top_to_bottom_whatever_the_order_given() -> None:
    chars = line("second", 0, 688) + line("first", 0, 700)
    assert rebuild_lines(chars) == "first\nsecond"


def test_same_characters_ignores_spacing_and_typographic_variants() -> None:
    """Docling turns the PDF's ’ and ― into ' and -; the rebuilt text keeps the
    PDF's characters, so they must not count as a difference."""
    assert same_characters("<p a=’1’>\n  x ―y", "<p a='1'> x -y")
    assert not same_characters("<p>", "<q>")
    assert not same_characters("<p>", "<p> extra")


def serialize(doc: DoclingDocument, code_texts=None) -> str:
    """Markdown from the converter's own text and picture serializers."""
    return (
        MarkdownDocSerializer(
            doc=doc,
            text_serializer=_formula_text_serializer(code_texts),
            picture_serializer=_picture_text_serializer(),
        )
        .serialize()
        .text
    )


def test_code_block_uses_the_rebuilt_lines() -> None:
    doc = DoclingDocument(name="t")
    code = doc.add_code(text="<a> <b/> </a>")
    md = serialize(doc, {code.self_ref: "<a>\n  <b/>\n</a>"})
    assert "```\n<a>\n  <b/>\n</a>\n```" in md


def test_code_block_unchanged_without_rebuilt_lines() -> None:
    doc = DoclingDocument(name="t")
    doc.add_code(text="<a> <b/> </a>")
    assert "```\n<a> <b/> </a>\n```" in serialize(doc, {})


def test_text_inside_a_picture_written_under_it() -> None:
    doc = DoclingDocument(name="t")
    picture = doc.add_picture()
    doc.add_text(label=DocItemLabel.TEXT, text='<Font ID="MyFont">', parent=picture)
    doc.add_text(label=DocItemLabel.TEXT, text="Left edge of the *XML* document", parent=picture)
    doc.add_text(label=DocItemLabel.TEXT, text="After the figure.")
    md = serialize(doc)
    block = md.index("*Text in the figure:*")
    assert block < md.index('- \\<Font ID="MyFont"\\>') < md.index("After the figure.")
    assert "- Left edge of the \\*XML\\* document" in md  # escaped, so no emphasis


def test_picture_without_text_gets_no_block() -> None:
    doc = DoclingDocument(name="t")
    doc.add_picture()
    assert "Text in the figure" not in serialize(doc)


def test_markdown_line_starts_in_figure_text_escaped() -> None:
    doc = DoclingDocument(name="t")
    picture = doc.add_picture()
    for text in ("# not a heading", "- not a list", "1. not numbered"):
        doc.add_text(label=DocItemLabel.TEXT, text=text, parent=picture)
    md = serialize(doc)
    assert "- \\# not a heading" in md and "- \\- not a list" in md and "- 1\\. not numbered" in md
