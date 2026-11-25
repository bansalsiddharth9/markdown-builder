"""V3 text completeness (design: V3 text completeness details)."""

from __future__ import annotations

from pathlib import Path

from specval.config import TextCoverage
from specval.textcheck import coverage, join_pdf_hyphenation, normalize_for_v3, sentences

from .conftest import DOC_IDS
from .test_mutations import check, delete_lines, md_path, validate


def test_sentences_split_and_short_fragments_skipped() -> None:
    text = (
        "A decoder shall support pop-on. Values such as 0x14 must be kept! Short. "
        "Next one is long enough?"
    )
    assert sentences(text, min_chars=20) == [
        "A decoder shall support pop-on.",
        "Values such as 0x14 must be kept!",
        "Next one is long enough?",
    ]


def test_sentence_split_keeps_decimals_and_abbreviations_together() -> None:
    text = "A frame rate of 29.97 fps is typical, e.g. in the U.S. market today."
    assert sentences(text, min_chars=20) == [text]


def test_pdf_source_lines_are_separate_units() -> None:
    """PDF text has no paragraphs: a heading without a full stop would glue onto
    the next sentence, and Docling may reorder blocks, so each line is a unit."""
    cfg = TextCoverage()
    page = (
        "00:00:04.000 --> 00:00:06.250 line:90%\n5 Caption modes\n"
        "In roll-up mode the display shall not."
    )
    markdown = (
        "In roll-up mode the display shall not. ## 5 Caption modes "
        "``` 00:00:04.000 --> 00:00:06.250 line:90% ```"
    )
    assert coverage([(page, "page 2")], markdown, cfg).ratio < 1
    assert coverage([(page, "page 2")], markdown, cfg, by_line=True).ratio == 1


def test_normalization() -> None:
    # NFKC, whitespace, dash and quote variants
    assert normalize_for_v3("Café  – “quoted” ﬁle") == 'Café - "quoted" file'


def test_pdf_hyphenation_joined() -> None:
    assert join_pdf_hyphenation("exam-\nple and well-\nKnown") == "example and well-\nKnown"


def test_coverage_present_near_missing() -> None:
    cfg = TextCoverage()
    source = [
        ("A decoder shall support pop-on, roll-up and paint-on modes.", "p1"),
        ("An encoder should not send more than thirty-two characters per row.", "p2"),
        ("This whole sentence is missing from the converted Markdown text.", "p3"),
    ]
    markdown = (
        "A decoder shall support pop-on, roll-up and paint-on modes. "
        "An encoder should not send more than thirty-two characters per rows."
    )
    result = coverage(source, markdown, cfg)
    assert (result.present, result.near, len(result.missing)) == (1, 1, 1)
    assert result.missing[0] == (
        "This whole sentence is missing from the converted Markdown text.",
        "p3",
    )
    assert round(result.ratio, 3) == round(2 / 3, 3)


def test_unmodified_conversions_have_full_coverage(out: Path) -> None:
    for kind in ("html", "docx", "pdf"):
        v3 = check(validate(out, DOC_IDS[kind]), "Validation3")
        assert v3.status == "pass", (kind, [f.message for f in v3.findings])
        assert v3.details["coverage"] == 1.0


def test_deleted_paragraph_fails_v3_and_names_it(out: Path) -> None:
    doc_id = DOC_IDS["html"]
    delete_lines(out, doc_id, r"^The base row is the lowest row")
    v3 = check(validate(out, doc_id), "Validation3")
    assert v3.status == "fail"
    missing = [f for f in v3.findings if f.message.startswith("missing")]
    assert len(missing) == 1
    assert "The base row is the lowest row of the roll-up window." in missing[0].excerpt
    assert "5.1.1 Base row" in missing[0].source_loc


def test_pdf_deleted_paragraph_names_page(out: Path) -> None:
    doc_id = DOC_IDS["pdf"]
    text = md_path(out, doc_id).read_text(encoding="utf-8")
    assert "The base row is the lowest row" in text
    delete_lines(out, doc_id, r"The base row is the lowest row")
    v3 = check(validate(out, doc_id), "Validation3")
    assert any(f.source_loc and f.source_loc.startswith("page ") for f in v3.findings)


def test_dot_leaders_compare_equal() -> None:
    """pypdf and Docling read a different number of leader dots in contents lines."""
    assert normalize_for_v3("8.5 Text Mode ........................ 46") == normalize_for_v3(
        "8.5 Text Mode ....... 46"
    )


def test_contents_line_without_page_number_found() -> None:
    cfg = TextCoverage()
    source = [("7.5 Other Real-Time Display Methods ................", "page 6")]
    markdown = "- 7.5 Other Real-Time Display Methods...........................32"
    assert coverage(source, markdown, cfg).ratio == 1
