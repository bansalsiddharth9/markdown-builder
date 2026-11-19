"""V5 table content (design: V5 table content details)."""

from __future__ import annotations

import re
from pathlib import Path

from bs4 import BeautifulSoup
from specval.tablecheck import compare_grids, html_grid

from .conftest import DOC_IDS
from .test_mutations import check, md_path, mutate, validate


def grid(html: str):
    return html_grid(BeautifulSoup(html, "html.parser").find("table"))


def test_html_grid_expands_spans() -> None:
    table = (
        "<table><tr><th colspan='2'>Byte pair</th><th rowspan='2'>Cmd</th></tr>"
        "<tr><th>B1</th><th>B2</th></tr><tr><td>14</td><td>20</td><td>RCL</td></tr></table>"
    )
    assert grid(table) == [
        ["Byte pair", "Byte pair", "Cmd"],
        ["B1", "B2", "Cmd"],
        ["14", "20", "RCL"],
    ]


def test_html_grid_ignores_nested_tables_cells() -> None:
    table = (
        "<table><tr><td>a</td><td><table><tr><td>x</td><td>y</td></tr></table></td></tr></table>"
    )
    assert grid(table) == [["a", "x y"]]


def test_compare_identical_grids() -> None:
    rows = [["A", "B"], ["1", "2"]]
    assert compare_grids(rows, [r[:] for r in rows]) == []


def test_compare_reports_cell_with_row_and_column() -> None:
    problems = compare_grids([["A", "B"], ["1", "2"]], [["A", "B"], ["1", "3"]])
    assert [(p.row, p.col, p.source, p.markdown) for p in problems] == [(2, 2, "2", "3")]


def test_compare_reports_missing_and_extra_rows() -> None:
    src = [["A", "B"], ["1", "2"], ["3", "4"], ["5", "6"]]
    problems = compare_grids(src, [["A", "B"], ["1", "2"], ["5", "6"], ["7", "8"]])
    kinds = [(p.kind, p.row, p.source, p.markdown) for p in problems]
    assert ("missing row", 3, "3 | 4", None) in kinds
    assert ("extra row", 4, None, "7 | 8") in kinds


def test_pandoc_empty_header_row_ignored() -> None:
    """Pandoc writes an empty header row for a table without one."""
    assert compare_grids([["BPD", "Block"]], [["", ""], ["BPD", "Block"]]) == []


def test_unmodified_html_and_docx_tables_match(out: Path) -> None:
    for kind in ("html", "docx"):
        v5 = check(validate(out, DOC_IDS[kind]), "V5")
        assert v5.status == "pass", (kind, [f.message for f in v5.findings])
        assert v5.details["tables_compared"] == 2


def test_swapped_cells_fail_v5(out: Path) -> None:
    for kind in ("html", "docx"):
        doc_id = DOC_IDS[kind]
        path = md_path(out, doc_id)
        text = path.read_text(encoding="utf-8")
        row = re.search(r"^\| Rows +\| 15 rows +\|$", text, re.M)
        assert row is not None
        path.write_text(text.replace(row.group(0), "| 15 rows | Rows |"), encoding="utf-8")
        v5 = check(validate(out, doc_id), "V5")
        assert v5.status == "fail"
        messages = [f.message for f in v5.findings]
        assert "table 2, row 2, column 1: source 'Rows', Markdown '15 rows'" in messages, messages


def test_removed_row_fails_v5(out: Path) -> None:
    doc_id = DOC_IDS["html"]
    path = md_path(out, doc_id)
    lines = path.read_text(encoding="utf-8").split("\n")
    number = next(i for i, line in enumerate(lines) if line.startswith("| Margin"))
    del lines[number]
    path.write_text("\n".join(lines), encoding="utf-8")
    v5 = check(validate(out, doc_id), "V5")
    assert v5.status == "fail"
    assert any("table 2, row 4: missing row 'Margin | 10%'" in f.message for f in v5.findings)


def test_changed_merged_cell_table_fails_v5_with_line(out: Path) -> None:
    doc_id = DOC_IDS["html"]
    line = mutate(out, doc_id, "<td>EDM</td>", "<td>EDN</td>")
    v5 = check(validate(out, doc_id), "V5")
    finding = next(f for f in v5.findings if "'EDM'" in f.message)
    assert finding.message == "table 1, row 5, column 3: source 'EDM', Markdown 'EDN'"
    assert finding.md_line == line


def test_pdf_tables_compared_with_pdfplumber(out: Path) -> None:
    doc_id = DOC_IDS["pdf"]
    v5 = check(validate(out, doc_id), "V5")
    assert v5.status in ("pass", "warn"), [f.message for f in v5.findings]
    assert v5.details["tables_compared"] >= 1


def test_pdf_cell_not_in_source_text_fails_v5(out: Path) -> None:
    doc_id = DOC_IDS["pdf"]
    mutate(out, doc_id, "32 columns", "33 columns")
    v5 = check(validate(out, doc_id), "V5")
    assert any("'33 columns'" in f.message for f in v5.findings)
    assert v5.status in ("warn", "fail")


def test_pandoc_empty_header_row_ignored_for_image_only_table() -> None:
    """A table whose cells hold only images has no text in any row."""
    assert compare_grids([[""], [""]], [[""], [""], [""]]) == []


def test_pdf_cells_compared_like_v3() -> None:
    from specval.checks_more import pdf_cells_agree

    assert pdf_cells_agree("Mnemonic1", "Mnemonic 1")
    assert pdf_cells_agree("“G”", '"G"') and pdf_cells_agree("—", "-")
    assert not pdf_cells_agree("MPA4", "MPA")
