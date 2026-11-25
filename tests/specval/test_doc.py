"""Validating a .doc (deviation DOC1): headings and tables are compared with
LibreOffice's .docx, the text (V3) with catdoc's reading of the original .doc,
so text that LibreOffice loses is still caught."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from specconv import cli as specconv_cli
from specval import cli, reference
from typer.testing import CliRunner

runner = CliRunner()
REPO = Path(__file__).resolve().parents[2]
DOC_ID = "ASS/sample-spec"
LOST = "The base row is the lowest row of the roll-up window."


@pytest.fixture
def out(doc_fixture: Path, tmp_path: Path) -> Path:
    """output/ with the .doc fixture converted (in input/ASS/)."""
    if shutil.which("catdoc") is None:
        pytest.skip("catdoc not installed")
    (tmp_path / "input" / "ASS").mkdir(parents=True)
    shutil.copy(doc_fixture, tmp_path / "input" / "ASS" / "sample-spec.doc")
    result = runner.invoke(
        specconv_cli.app,
        ["convert", "--input", str(tmp_path / "input"), "--output", str(tmp_path / "output"),
         "--work", str(tmp_path / "work"), "--config", str(REPO / "config" / "specconv.yaml")],
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    return tmp_path / "output"


def validate(out: Path) -> dict:
    """Run specval on the document and return its JSON report."""
    val = out.parent / "validation"
    result = runner.invoke(
        cli.app, ["run", "--output", str(out), "--validation", str(val), "--doc", DOC_ID]
    )
    assert result.exit_code in (0, 1), result.output
    return json.loads((val / f"{DOC_ID}.json").read_text(encoding="utf-8"))


def check(report: dict, check_id: str) -> dict:
    return next(c for c in report["checks"] if c["id"] == check_id)


def test_doc_validated_with_both_readings(out: Path) -> None:
    report = validate(out)
    v1, v3 = check(report, "V1"), check(report, "V3")
    assert v1["status"] != "n/a"  # headings and tables from LibreOffice's .docx
    assert v3["details"]["text_from"] == "catdoc"
    assert v3["status"] == "pass", v3


def test_text_lost_by_libreoffice_still_caught(out: Path) -> None:
    """Remove a paragraph from LibreOffice's .docx AND the Markdown, as if
    LibreOffice had lost it: only the independent catdoc reading can notice."""
    import docx

    folder = out / DOC_ID
    lo = docx.Document(str(folder / "libreoffice.docx"))
    for p in lo.paragraphs:
        if p.text == LOST:
            p._element.getparent().remove(p._element)
    lo.save(str(folder / "libreoffice.docx"))
    md = folder / "sample-spec.md"
    md.write_text(md.read_text(encoding="utf-8").replace(LOST, ""), encoding="utf-8")
    v3 = check(validate(out), "V3")
    assert any(f.get("excerpt") == LOST for f in v3["findings"]), v3


def test_without_catdoc_text_from_docx_with_note(
    out: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(reference, "find_catdoc", lambda: None)
    report = validate(out)
    assert check(report, "V3")["details"]["text_from"] == "libreoffice.docx"
    assert any("catdoc not found" in note for note in report["notes"])
