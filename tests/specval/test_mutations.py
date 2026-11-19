"""Validator mutation tests (design: Testing, the most important tests).

Start from a known-good conversion, inject one defect at a time and assert
that the expected check fails and names the right location. The unmodified
conversions must pass, so the checks do not produce false failures.

Phase 5 covers V1, V2, V4 and V7; the V3, V5, V6, V8 and V9 rows of the
design's table follow in phase 6.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from common.manifest import read_manifest
from specval.config import SpecvalConfig
from specval.runner import DocReport, validate_document

from .conftest import DOC_IDS

KINDS = ("html", "docx", "pdf")


def validate(out: Path, doc_id: str) -> DocReport:
    entry = read_manifest(out / "manifest.json").get(doc_id)
    assert entry is not None
    return validate_document(entry, out, SpecvalConfig(), "2026-11-19T00:00:00Z")


def check(report: DocReport, check_id: str):
    return next(c for c in report.checks if c.id == check_id)


def md_path(out: Path, doc_id: str) -> Path:
    return out / doc_id / f"{doc_id}.md"


def mutate(out: Path, doc_id: str, old: str, new: str) -> int:
    """Replace the first occurrence of old in the body; return its 1-based line."""
    path = md_path(out, doc_id)
    text = path.read_text(encoding="utf-8")
    body_start = text.index("\n---\n", 4) + 5
    index = text.index(old, body_start)
    path.write_text(text[:index] + new + text[index + len(old) :], encoding="utf-8")
    return text[:index].count("\n") + 1


def delete_lines(out: Path, doc_id: str, pattern: str) -> int:
    """Delete the first line matching pattern; return its line number."""
    path = md_path(out, doc_id)
    lines = path.read_text(encoding="utf-8").split("\n")
    number = next(i for i, line in enumerate(lines) if re.search(pattern, line))
    del lines[number]
    path.write_text("\n".join(lines), encoding="utf-8")
    return number + 1


def summary(report: DocReport) -> str:
    return "\n".join(
        f"{c.id} {c.status}: " + "; ".join(f.message for f in c.findings) for c in report.checks
    )


# ------------------------------------------------------------ baseline


@pytest.mark.parametrize("kind", ["html", "docx"])
def test_unmodified_conversion_passes(out: Path, kind: str) -> None:
    report = validate(out, DOC_IDS[kind])
    assert report.overall == "pass", summary(report)
    assert {c.status for c in report.checks} <= {"pass", "not_applicable"}


def test_unmodified_pdf_fails_only_on_lost_code_line_breaks(out: Path) -> None:
    """Known Docling limitation: line breaks in code are lost, so the WebVTT
    example's timing lines carry the cue text. Everything else passes."""
    report = validate(out, DOC_IDS["pdf"])
    statuses = {c.id: c.status for c in report.checks}
    assert statuses.pop("V6") == "fail", summary(report)
    assert set(statuses.values()) <= {"pass", "not_applicable"}, summary(report)


def test_saved_page_reports_its_missing_image(out: Path) -> None:
    """The saved-page fixture has a deliberately missing image (decision I2)."""
    report = validate(out, DOC_IDS["saved"])
    v7 = check(report, "V7")
    assert v7.status == "fail"
    assert [f.message for f in v7.findings if f.severity == "fail"] == [
        "image file missing: ./Sample%20Saved%20Spec_files/missing.png"
    ]
    assert any("not stored locally: https://www.w3.org/logo.png" in f.message for f in v7.findings)
    assert {c.id: c.status for c in report.checks}["V1"] == "pass"


# ----------------------------------------------------------------- V4


@pytest.mark.parametrize("kind", KINDS)
def test_changed_hex_value_fails_v4(out: Path, kind: str) -> None:
    doc_id = DOC_IDS[kind]
    line = mutate(out, doc_id, "0x14", "0x1A")
    v4 = check(validate(out, doc_id), "V4")
    assert v4.status == "fail"
    lost = next(f for f in v4.findings if "'0x14'" in f.message)
    added = next(f for f in v4.findings if "'0x1A'" in f.message)
    assert "source 1, Markdown 0" in lost.message and lost.source_loc
    assert added.md_line == line and "0x1A" in (added.excerpt or "")


@pytest.mark.parametrize("kind", KINDS)
def test_shall_changed_to_should_fails_v4(out: Path, kind: str) -> None:
    doc_id = DOC_IDS[kind]
    line = mutate(out, doc_id, "A decoder shall support", "A decoder should support")
    v4 = check(validate(out, doc_id), "V4")
    assert v4.status == "fail"
    assert any(f.message.startswith("normative keywords: ") for f in v4.findings)
    assert any("'should'" in f.message and f.md_line == line for f in v4.findings)
    # "shall not" is its own token, so the fixture has one plain "shall"
    assert any("'shall'" in f.message and "source 1, Markdown 0" in f.message for f in v4.findings)


@pytest.mark.parametrize("kind", KINDS)
def test_zero_replaced_by_letter_o_in_hex_fails_v4(out: Path, kind: str) -> None:
    doc_id = DOC_IDS[kind]
    mutate(out, doc_id, "0x14", "Ox14")
    v4 = check(validate(out, doc_id), "V4")
    assert v4.status == "fail"
    assert any("hex '0x14': source 1, Markdown 0" in f.message for f in v4.findings)


def test_changed_timecode_and_unit_fail_v4(out: Path) -> None:
    doc_id = DOC_IDS["html"]
    mutate(out, doc_id, "01:00:00;02", "01:00:00;20")
    mutate(out, doc_id, "29.97 fps", "29.97 fpx")
    v4 = check(validate(out, doc_id), "V4")
    messages = "\n".join(f.message for f in v4.findings)
    assert "timecodes '01:00:00;02'" in messages and "numbers with units '29.97 fps'" in messages


def test_changed_byte_pair_in_table_fails_v4(out: Path) -> None:
    doc_id = DOC_IDS["html"]
    mutate(out, doc_id, "9420", "9421")  # in a paragraph: not a table family token
    assert check(validate(out, doc_id), "V4").status == "pass"


# ----------------------------------------------------------------- V2


@pytest.mark.parametrize("kind", KINDS)
def test_removed_section_heading_fails_v2(out: Path, kind: str) -> None:
    doc_id = DOC_IDS[kind]
    delete_lines(out, doc_id, r"^#+ .*5\.1 Roll-up captions")
    report = validate(out, doc_id)
    v2 = check(report, "V2")
    assert v2.status == "fail"
    lines = md_path(out, doc_id).read_text(encoding="utf-8").split("\n")
    next_heading = next(i + 1 for i, line in enumerate(lines) if "5.1.1 Base row" in line)
    assert any(
        "section 5.1.1 skipped (after 5)" in f.message and f.md_line == next_heading
        for f in v2.findings
    )
    if kind != "pdf":  # the source has headings to compare with
        assert any("not in the Markdown: '5.1'" in f.message and f.source_loc for f in v2.findings)
        assert check(report, "V1").status == "fail"  # one heading fewer


def test_numbering_irregular_in_source_is_only_a_warning(out: Path) -> None:
    """If the source itself skips a number, the Markdown is faithful: warn, not fail."""
    doc_id = DOC_IDS["html"]
    source = out / doc_id / "source.html"
    source.write_text(
        source.read_text(encoding="utf-8").replace(
            ">5.1 Roll-up captions<", ">5.3 Roll-up captions<"
        ),
        encoding="utf-8",
    )
    mutate(out, doc_id, "5.1 Roll-up captions", "5.3 Roll-up captions")
    v2 = check(validate(out, doc_id), "V2")
    assert v2.status == "warn"
    assert all(f.severity == "warn" for f in v2.findings)
    assert any("the source numbers it the same way" in f.message for f in v2.findings)


# ----------------------------------------------------------------- V1


@pytest.mark.parametrize("kind", ["html", "docx"])
def test_deleted_code_block_fails_v1(out: Path, kind: str) -> None:
    doc_id = DOC_IDS[kind]
    path = md_path(out, doc_id)
    text = path.read_text(encoding="utf-8")
    block = re.search(r"\n```[a-z]*\nWEBVTT\n.*?\n```\n", text, re.S)
    assert block is not None
    path.write_text(text.replace(block.group(0), "\n"), encoding="utf-8")
    v1 = check(validate(out, doc_id), "V1")
    assert v1.status == "fail"
    assert any(f.message == "code blocks: source 2, Markdown 1" for f in v1.findings)
    assert any(
        "code block in the source, not in the Markdown: 'WEBVTT" in f.message and f.source_loc
        for f in v1.findings
    )


@pytest.mark.parametrize("kind", ["html", "docx"])
def test_deleted_table_fails_v1(out: Path, kind: str) -> None:
    doc_id = DOC_IDS[kind]
    path = md_path(out, doc_id)
    lines = path.read_text(encoding="utf-8").split("\n")
    kept = [
        line for line in lines if not re.match(r"^\|(-| (Property|Rows|Columns|Margin) )", line)
    ]
    assert len(kept) == len(lines) - 5
    path.write_text("\n".join(kept), encoding="utf-8")
    v1 = check(validate(out, doc_id), "V1")
    assert v1.status == "fail" and any(
        f.message == "tables: source 2, Markdown 1" for f in v1.findings
    )


def test_pdf_table_count_within_tolerance_warns(out: Path) -> None:
    doc_id = DOC_IDS["pdf"]
    path = md_path(out, doc_id)
    lines = path.read_text(encoding="utf-8").split("\n")
    kept = [
        line for line in lines if not re.match(r"^\|(-| (Property|Rows|Columns|Margin) )", line)
    ]
    path.write_text("\n".join(kept), encoding="utf-8")
    v1 = check(validate(out, doc_id), "V1")
    assert v1.status == "warn" and "pdfplumber finds 2, Markdown has 1" in v1.findings[0].message


# ----------------------------------------------------------------- V7


@pytest.mark.parametrize("kind", KINDS)
def test_image_link_to_missing_file_fails_v7(out: Path, kind: str) -> None:
    doc_id = DOC_IDS[kind]
    text = md_path(out, doc_id).read_text(encoding="utf-8")
    image = re.search(r"\]\((media/[^)]+)\)|<img src=\"(media/[^\"]+)\"", text)
    assert image is not None
    line = mutate(out, doc_id, image.group(1) or image.group(2), "media/nope.png")
    v7 = check(validate(out, doc_id), "V7")
    assert v7.status == "fail"
    assert any(
        f.message == "image file missing: media/nope.png" and f.md_line == line for f in v7.findings
    )


def test_broken_anchor_fails_v7_and_existing_anchors_pass(out: Path) -> None:
    doc_id = DOC_IDS["html"]
    line = mutate(
        out, doc_id, "The base row is", "See [5.1](#sec-5-1) and [x](#nowhere). The base row is"
    )
    v7 = check(validate(out, doc_id), "V7")
    assert v7.status == "fail"
    assert [f.message for f in v7.findings if f.severity == "fail"] == [
        "link to #nowhere: no such anchor in the document"
    ]
    assert v7.findings[0].md_line == line


def test_unrewritten_self_link_warns(out: Path) -> None:
    doc_id = DOC_IDS["saved"]
    mutate(
        out,
        doc_id,
        "](#sec-intro)",
        "](https://www.w3.org/TR/2099/REC-sample-20990101/#sec-intro)",
    )
    v7 = check(validate(out, doc_id), "V7")
    assert any("own URL not rewritten" in f.message and f.severity == "warn" for f in v7.findings)


def test_front_matter_problems_fail_v7(out: Path) -> None:
    doc_id = DOC_IDS["html"]
    mutate(out, doc_id, "", "")  # no-op: just checks the helper on the copy
    path = md_path(out, doc_id)
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace(f"id: {doc_id}\n", "id: other\n", 1), encoding="utf-8")
    v7 = check(validate(out, doc_id), "V7")
    assert any(f.message == f"front matter id 'other' is not '{doc_id}'" for f in v7.findings)

    path.write_text(text.replace("source_type: html", "source_type: pptx", 1), encoding="utf-8")
    v7 = check(validate(out, doc_id), "V7")
    assert any(f.message.startswith("front matter invalid:") for f in v7.findings)


def test_changed_source_file_fails_v7(out: Path) -> None:
    doc_id = DOC_IDS["docx"]
    source = out / doc_id / "source.docx"
    source.write_bytes(source.read_bytes() + b"\0")
    v7 = check(validate(out, doc_id), "V7")
    assert any(
        "source_sha256 in the front matter does not match source.docx" in f.message
        for f in v7.findings
    )
