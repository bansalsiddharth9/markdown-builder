"""How the converter writes Docling items, tested on small DoclingDocuments
without running Docling.

- Undecoded formulas keep their original text (deviations Q18): without its
  formula model Docling would write "<!-- formula-not-decoded -->" and the
  text would be lost (CEA-608-E page 41).
- A document index (table of contents) becomes a bullet list (Q20); real
  tables stay tables.
"""

from __future__ import annotations

from docling_core.transforms.serializer.markdown import MarkdownDocSerializer
from docling_core.types.doc import DocItemLabel, DoclingDocument, TableCell, TableData
from specconv.converters.docling import _formula_text_serializer, _span_aware_table_serializer


def serialize(doc: DoclingDocument) -> str:
    """Serialize with the converter's own text and table serializers."""
    serializer = MarkdownDocSerializer(
        doc=doc,
        text_serializer=_formula_text_serializer(),
        table_serializer=_span_aware_table_serializer(),
    )
    return serializer.serialize().text


def test_undecoded_formula_written_as_code_block() -> None:
    doc = DoclingDocument(name="t")
    doc.add_text(label=DocItemLabel.TEXT, text="General format:")
    doc.add_text(
        label=DocItemLabel.FORMULA,
        text="",
        orig="<url>[attr1:val1][attr2:val2]...[checksum]",
    )
    md = serialize(doc)
    assert "formula-not-decoded" not in md
    assert "```\n<url>[attr1:val1][attr2:val2]...[checksum]\n```" in md


def test_decoded_formula_and_plain_text_unchanged() -> None:
    doc = DoclingDocument(name="t")
    doc.add_text(label=DocItemLabel.FORMULA, text="x^2", orig="x2")
    doc.add_text(label=DocItemLabel.TEXT, text="Plain <b> text")
    md = serialize(doc)
    assert "$$x^2$$" in md and "Plain &lt;b&gt; text" in md


def table(doc: DoclingDocument, rows: list[list[str]], label: DocItemLabel) -> None:
    cells = [
        TableCell(
            text=text,
            start_row_offset_idx=r,
            end_row_offset_idx=r + 1,
            start_col_offset_idx=c,
            end_col_offset_idx=c + 1,
            column_header=r == 0 and label == DocItemLabel.TABLE,
        )
        for r, row in enumerate(rows)
        for c, text in enumerate(row)
    ]
    data = TableData(num_rows=len(rows), num_cols=len(rows[0]), table_cells=cells)
    doc.add_table(data=data, label=label)


def test_document_index_written_as_bullet_list() -> None:
    doc = DoclingDocument(name="t")
    table(
        doc,
        [
            ["1 Purpose and Scope.........1"],
            ["1.1 Purpose ...........1"],
            ["E.8 XDS_Text <opt> ..42"],
        ],
        DocItemLabel.DOCUMENT_INDEX,
    )
    table(doc, [["Code", "Meaning"], ["AC", "Article Clear"]], DocItemLabel.TABLE)
    md = serialize(doc)
    assert (
        "- 1 Purpose and Scope.........1\n- 1.1 Purpose ...........1\n"
        "- E.8 XDS\\_Text \\<opt\\> ..42"
    ) in md
    assert "| Code   | Meaning       |" in md and "| AC     | Article Clear |" in md
