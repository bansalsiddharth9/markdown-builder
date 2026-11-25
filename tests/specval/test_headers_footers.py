"""Page headers and footers in PDFs (deviations V-af and V-ag).

A (V-af), the validator's own rule: besides lines on more than half the
pages, a line also counts as a header or footer when
- it repeats on more than half the odd pages, or of the even pages (EBU Tech
  3264 alternates "tech 3264 EBU - …" and "EBU - … tech 3264"), or
- it is the first or last line of at least 3 pages (a running chapter
  title).

B (V-ag): a "missing sentence" that the converter reports having dropped as a
page header or footer (conversion.json, docling.dropped_as_page_furniture)
becomes a warning to confirm, and does not count against the coverage.
"""

from __future__ import annotations

from pathlib import Path

from common.manifest import ManifestEntry
from specval.checks import Document
from specval.checks_more import check_v3
from specval.config import SpecvalConfig
from specval.markdown import parse_markdown
from specval.reference import Reference, furniture_lines
from specval.text import TEXT, Segment


def body(n: int) -> list[str]:
    """Two body lines that differ per page. Letters, not the page number: a line
    whose number follows the page is a page-number line (Page 12)."""
    letter = "ABCDEFGHIJKL"[n]
    return [f"Body line {letter} about captions.", f"Second line {letter} about timing."]


def removed(pages: list[list[str]]) -> set[str]:
    """The lines furniture_lines finds."""
    return {pages[p][i] for p, i in furniture_lines(pages)}


def test_alternating_odd_and_even_headers_found() -> None:
    pages = [
        ["tech 3264 EBU - Subtitling" if n % 2 else "EBU - Subtitling tech 3264", *body(n)]
        for n in range(1, 9)
    ]
    assert removed(pages) == {"tech 3264 EBU - Subtitling", "EBU - Subtitling tech 3264"}


def test_running_chapter_title_at_the_page_edge_found() -> None:
    pages = [body(n) for n in range(1, 11)]
    for n in (3, 4, 5):
        pages[n] = ["6 Subtitle elements", *pages[n]]
    for n in (7, 8, 9):
        pages[n] = [*pages[n], "Annex A Examples"]
    assert removed(pages) == {"6 Subtitle elements", "Annex A Examples"}


def test_repeated_line_inside_the_page_body_kept() -> None:
    """Repeating in the middle of pages (a table's column names) is content."""
    letters = "ABCDEFGHIJ"
    pages = [
        [*body(n), f"Text {letters[n]}.", "Code Meaning Bytes", f"More {letters[n]}.", *body(n)]
        for n in range(10)
    ]
    for n in range(4, 10):  # on 4 of 10 pages only: not "more than half"
        pages[n] = [line for line in pages[n] if line != "Code Meaning Bytes"]
    assert "Code Meaning Bytes" not in removed(pages)


def test_edge_line_on_only_two_pages_kept() -> None:
    pages = [body(n) for n in range(1, 11)]
    for n in (2, 6):
        pages[n] = ["Note on timing", *pages[n]]
    assert "Note on timing" not in removed(pages)


SENTENCE = "The decoder shall display the caption within one frame."
COPYRIGHT = "Copyright 2014 by THE SOCIETY OF MOTION PICTURE ENGINEERS"
OTHER = "This sentence was lost by the converter on its way."


def document(tmp_path: Path, dropped: list[dict]) -> Document:
    """A PDF document whose Markdown has only SENTENCE, the source also
    COPYRIGHT and OTHER, and conversion.json lists dropped as page furniture."""
    md = f"{SENTENCE}\n"
    return Document(
        id="d",
        folder=tmp_path,
        entry=ManifestEntry(
            id="d", source="d.pdf", source_type="pdf", status="ok", converter="docling"
        ),
        md_path=tmp_path / "d.md",
        md=parse_markdown(md, md),
        front_matter=None,
        front_matter_error=None,
        reference=Reference(
            kind="pdf",
            segments=[Segment(f"{SENTENCE}\n{COPYRIGHT}\n{OTHER}", TEXT, None, "page 1")],
        ),
        conversion={"docling": {"dropped_as_page_furniture": dropped}},
    )


def test_sentence_the_converter_dropped_as_header_becomes_confirm(tmp_path: Path) -> None:
    dropped = [{"pages": [1], "label": "page_header", "text": COPYRIGHT}]
    v3 = check_v3(document(tmp_path, dropped), SpecvalConfig())
    confirm = [f for f in v3.findings if "page header or footer" in f.message]
    assert len(confirm) == 1 and confirm[0].severity == "warn"
    assert confirm[0].excerpt == COPYRIGHT and confirm[0].source_loc == "page 1"
    # Still a real loss: OTHER stays a missing sentence, and only it counts.
    missing = [f for f in v3.findings if f.message == "missing sentence"]
    assert [f.excerpt for f in missing] == [OTHER]
    assert v3.details["dropped_as_header_or_footer"] == 1
    assert v3.details["missing"] == 1


def test_without_a_dropped_list_nothing_changes(tmp_path: Path) -> None:
    v3 = check_v3(document(tmp_path, []), SpecvalConfig())
    assert len([f for f in v3.findings if f.message == "missing sentence"]) == 2
    assert not [f for f in v3.findings if "page header or footer" in f.message]


def test_line_next_to_the_edge_kept() -> None:
    """Only a page's very first and last line can be a running title: DCDM's
    "Direction = ttb)" (a sentence's end) and ARIB's "circled number" (a table
    cell) sat second from the edge on several pages and are content."""
    pages = [[*body(n), f"Footer {n}."] for n in range(1, 11)]
    for n in (2, 5, 8):  # odd and even pages, so the odd/even rule does not apply
        pages[n] = [*pages[n][:-1], "Direction = ttb)", pages[n][-1]]
    assert "Direction = ttb)" not in removed(pages)
