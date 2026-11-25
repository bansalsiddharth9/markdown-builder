"""V8 character confusion and V9 acceptance questions (design: Checks)."""

from __future__ import annotations

from pathlib import Path

from common.manifest import read_manifest
from specval.config import AcceptanceQuestion, SpecvalConfig
from specval.confusion import confusable_tokens
from specval.runner import validate_document

from .conftest import DOC_IDS
from .test_mutations import check, mutate, validate


def test_confusable_tokens() -> None:
    text = "Ox14 and 0x1O, l4h, 2O rows, 0x14, 9420, ISO 8859, Section S, hello, 1S"
    assert confusable_tokens(text) == ["Ox14", "0x1O", "l4h", "2O", "1S"]


def test_unmodified_conversions_have_no_confusions(out: Path) -> None:
    for kind in ("html", "docx", "pdf"):
        assert check(validate(out, DOC_IDS[kind]), "Validation8").status == "pass"


def test_letter_o_in_hex_in_text_warns_v8(out: Path) -> None:
    doc_id = DOC_IDS["html"]
    line = mutate(out, doc_id, "0x14", "Ox14")
    v8 = check(validate(out, doc_id), "Validation8")
    assert v8.status == "warn"
    assert [(f.message, f.md_line) for f in v8.findings] == [
        ("'Ox14' looks like a number or hex value with a letter in it (O, o, l, I, S)", line)
    ]


def test_letter_l_in_table_fails_v8(out: Path) -> None:
    doc_id = DOC_IDS["html"]
    line = mutate(out, doc_id, "<td>14</td>", "<td>l4</td>")
    v8 = check(validate(out, doc_id), "Validation8")
    assert v8.status == "fail"
    assert v8.findings[0].md_line == line and v8.findings[0].severity == "fail"


def test_token_also_in_source_is_not_reported(out: Path) -> None:
    doc_id = DOC_IDS["html"]
    source = out / doc_id / "source.html"
    source.write_text(
        source.read_text(encoding="utf-8").replace("0x14", "Ox14", 1), encoding="utf-8"
    )
    mutate(out, doc_id, "0x14", "Ox14")
    assert check(validate(out, doc_id), "Validation8").status == "pass"


def validate_with(out: Path, doc_id: str, questions: list[AcceptanceQuestion]):
    entry = read_manifest(out / "manifest.json").get(doc_id)
    return validate_document(entry, out, SpecvalConfig(), "t", questions=questions)


def test_v9_not_applicable_without_questions(out: Path) -> None:
    assert check(validate(out, DOC_IDS["html"]), "Validation9").status == "not_applicable"


def test_v9_expected_snippets(out: Path) -> None:
    doc_id = DOC_IDS["html"]
    questions = [
        AcceptanceQuestion(doc_id, "Maximum rows in roll-up mode?", "shall not exceed 4 rows"),
        AcceptanceQuestion(doc_id, "Region position attribute?", "tts:origin"),
        AcceptanceQuestion("other-doc", "Not this document", "anything"),
    ]
    v9 = check(validate_with(out, doc_id, questions), "Validation9")
    assert v9.status == "pass" and v9.details == {"questions": 2, "answered": 2}

    questions.append(AcceptanceQuestion(doc_id, "Frame rate?", "25 fps"))
    v9 = check(validate_with(out, doc_id, questions), "Validation9")
    assert v9.status == "fail"
    assert [f.message for f in v9.findings] == [
        "acceptance question 'Frame rate?': expected '25 fps' not found"
    ]
