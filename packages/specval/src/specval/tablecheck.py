"""Table grids and their comparison (design: V5 table content details).

A grid is a table as rows of cell texts with row and column spans expanded:
a merged cell's text is repeated in every position it covers, on both sides,
so a span that is lost or moved shows up as differing cells.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass

from bs4 import Tag

from specval.text import html_segments, normalize

Grid = list[list[str]]


def _span(cell: Tag, name: str) -> int:
    try:
        return max(1, min(int(str(cell.get(name, "1")).strip() or 1), 1000))
    except ValueError:
        return 1


def cell_text(cell: Tag) -> str:
    """Visible text of a cell; separate blocks (paragraphs, nested cells) are
    joined by a space, inline markup is not."""
    return normalize(" ".join(s.text for s in html_segments(cell, lambda _el: (None, None))))


def _expand(table: Tag, value) -> list[list]:
    """Rows of the table (not of nested tables) with spans expanded; value(cell)
    gives what each position holds."""
    grid: list[list] = []
    pending: dict[tuple[int, int], object] = {}  # (row, col) covered by a rowspan from above
    rows = [tr for tr in table.find_all("tr") if tr.find_parent("table") is table]
    for r, tr in enumerate(rows):
        row: list = []
        c = 0
        for cell in tr.find_all(["td", "th"], recursive=False):
            while (r, c) in pending:
                row.append(pending.pop((r, c)))
                c += 1
            item = value(cell)
            colspan, rowspan = _span(cell, "colspan"), _span(cell, "rowspan")
            for dc in range(colspan):
                row.append(item)
                for dr in range(1, rowspan):
                    pending[(r + dr, c + dc)] = item
            c += colspan
        while (r, c) in pending:
            row.append(pending.pop((r, c)))
            c += 1
        grid.append(row)
    return grid


def html_grid(table: Tag) -> Grid:
    """Cell texts, spans expanded."""
    return _expand(table, cell_text)


def html_grid_lines(table: Tag, line_of) -> list[list[int]]:
    """The line of each grid position (the line of the cell that fills it)."""
    return _expand(table, line_of)


@dataclass(frozen=True)
class TableProblem:
    kind: str  # cell | missing row | extra row | columns
    row: int  # 1-based row in the source grid (in the Markdown grid for "extra row")
    col: int | None
    source: str | None
    markdown: str | None
    md_row: int | None  # 0-based row in the Markdown grid, for its line number


def _key(row: list[str]) -> str:
    return " | ".join(row)


def compare_grids(source: Grid, markdown: Grid) -> list[TableProblem]:
    source = [[normalize(c) for c in row] for row in source]
    markdown = [[normalize(c) for c in row] for row in markdown]
    offset = 0
    # Pandoc writes an empty header row for a table that has none.
    while markdown and not any(markdown[0]) and len(markdown) > len(source):
        markdown = markdown[1:]
        offset += 1
    problems: list[TableProblem] = []
    matcher = difflib.SequenceMatcher(
        a=[_key(r) for r in source], b=[_key(r) for r in markdown], autojunk=False
    )
    for op, a0, a1, b0, b1 in matcher.get_opcodes():
        if op == "equal":
            continue
        paired = min(a1 - a0, b1 - b0) if op == "replace" else 0
        for k in range(paired):
            src_row, md_row = source[a0 + k], markdown[b0 + k]
            if len(src_row) != len(md_row):
                problems.append(
                    TableProblem(
                        "columns",
                        a0 + k + 1,
                        None,
                        _key(src_row),
                        _key(md_row),
                        b0 + k + offset,
                    )
                )
                continue
            for c, (s, m) in enumerate(zip(src_row, md_row, strict=True)):
                if s != m:
                    problems.append(TableProblem("cell", a0 + k + 1, c + 1, s, m, b0 + k + offset))
        for i in range(a0 + paired, a1):
            problems.append(TableProblem("missing row", i + 1, None, _key(source[i]), None, None))
        for j in range(b0 + paired, b1):
            problems.append(
                TableProblem("extra row", j + 1, None, None, _key(markdown[j]), j + offset)
            )
    return problems


def describe(number: int, p: TableProblem) -> str:
    if p.kind == "cell":
        return (
            f"table {number}, row {p.row}, column {p.col}: "
            f"source {p.source!r}, Markdown {p.markdown!r}"
        )
    if p.kind == "missing row":
        return f"table {number}, row {p.row}: missing row {p.source!r}"
    if p.kind == "extra row":
        return f"table {number}, row {p.row}: extra row {p.markdown!r}"
    return (
        f"table {number}, row {p.row}: different number of cells: source {p.source!r}, "
        f"Markdown {p.markdown!r}"
    )
