"""V6 code blocks (design: V6 code block details)."""

from __future__ import annotations

from pathlib import Path

from specval.codecheck import code_differs, problem_key, webvtt_problems, xml_problems

from .conftest import DOC_IDS
from .test_mutations import check, mutate, validate


def test_well_formed_xml_and_fragments_pass() -> None:
    assert xml_problems('<?xml version="1.0"?>\n<tt xmlns="urn:x"><p/></tt>') == []
    # spec examples are often fragments: several roots, prefixes declared elsewhere
    assert (
        xml_problems('<region xml:id="r1" tts:origin="10% 80%"/>\n<region tts:extent="1"/>') == []
    )
    assert xml_problems("<p>\n  ...\n</p>") == []


def test_missing_closing_tag_reported_with_line() -> None:
    problems = xml_problems("<tt>\n  <body>\n    <p>x</p>\n</tt>")
    assert len(problems) == 1
    line, message = problems[0]
    assert line == 4 and "body" in message


def test_webvtt_valid() -> None:
    text = (
        "WEBVTT\n\n1\n00:00:01.000 --> 00:00:03.500\nHi\n\n"
        "00:04.000 --> 00:06.250 line:90% align:start\nYo"
    )
    assert webvtt_problems(text) == []


def test_webvtt_broken_timing_and_header() -> None:
    assert webvtt_problems("WEBVTT\n\n00:00:01,000 --> 00:00:03.500\nHi") == [
        (3, "cue timing line does not match the WebVTT pattern: '00:00:01,000 --> 00:00:03.500'")
    ]
    assert webvtt_problems("WEBVTX\n\n00:01.000 --> 00:02.000") == [
        (1, "the first line must be 'WEBVTT'")
    ]
    # lost line breaks (Docling): the cue text sits on the timing line
    assert webvtt_problems("00:00:01.000 --> 00:00:03.500 Example caption one")[0][0] == 1


def test_code_comparison_ignores_trailing_space_and_line_endings_only() -> None:
    assert code_differs("a  \r\nb\n\n", "a\nb") is None
    assert code_differs("a\n  b", "a\nb") == (2, "  b", "b")
    assert code_differs("a\nb", "a") == (2, "b", None)


def test_unmodified_html_and_docx_code_pass(out: Path) -> None:
    for kind in ("html", "docx"):
        v6 = check(validate(out, DOC_IDS[kind]), "Validation6")
        assert v6.status == "pass", (kind, [f.message for f in v6.findings])
        assert v6.details["compared"] == 2


def test_deleted_closing_xml_tag_fails_v6(out: Path) -> None:
    for kind in ("html", "docx"):
        doc_id = DOC_IDS[kind]
        line = mutate(out, doc_id, "    </div>\n", "")
        v6 = check(validate(out, doc_id), "Validation6")
        assert v6.status == "fail"
        messages = [f.message for f in v6.findings]
        assert any(m.startswith("XML not well-formed") for m in messages), messages
        assert any(m.startswith("code block 1 differs from the source") for m in messages)
        assert any(f.md_line == line for f in v6.findings)


def test_broken_webvtt_timing_fails_v6(out: Path) -> None:
    doc_id = DOC_IDS["html"]
    line = mutate(out, doc_id, "00:00:01.000 --> 00:00:03.500", "00:00:01,000 --> 00:00:03.500")
    v6 = check(validate(out, doc_id), "Validation6")
    assert v6.status == "fail"
    assert any(
        "cue timing line does not match" in f.message and f.md_line == line for f in v6.findings
    )


def test_pdf_code_checked_for_syntax_only(out: Path) -> None:
    """No code blocks in PDF text: XML and WebVTT syntax only. Docling loses
    line breaks in code (known limitation), so the WebVTT example fails."""
    v6 = check(validate(out, DOC_IDS["pdf"]), "Validation6")
    assert v6.details["compared"] == 0
    assert v6.status == "fail"
    assert all("WebVTT" in f.message or "cue timing" in f.message for f in v6.findings)


def test_xml_block_detection() -> None:
    from specval.codecheck import is_xml_block

    assert is_xml_block("<tt>\n</tt>") and is_xml_block('<region xml:id="r1"/>')
    assert is_xml_block("anything", "xml") and is_xml_block('<?xml version="1.0"?><a>')
    # a URL syntax with angle brackets is not XML (CEA-608-E page 41)
    assert not is_xml_block("<url>[attr1:val1][attr2:val2]...[checksum]")
    assert not is_xml_block("<http://advsponsor.net>[type:sponsor][0xEA77]")


def test_same_source_problem_recognized_despite_line_shift() -> None:
    # Browsers drop the line break right after <pre>, so the source's copy of
    # a code block can start one line earlier than the Markdown's. The same
    # XML problem must still count as "the source has the same problem".
    src = "<SAMI>\n<STYLE>p {}\n</Style>\n</SAMI>"
    (_, from_source), (_, from_markdown) = xml_problems(src)[0], xml_problems("\n" + src)[0]
    assert from_source != from_markdown  # the messages name different lines
    assert problem_key(from_source) == problem_key(from_markdown)
    assert problem_key(from_source) != problem_key(xml_problems("<a><b></a>")[0][1])
