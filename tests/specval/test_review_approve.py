"""Review queue and approval (design: Review queue, Approval)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from common.frontmatter import parse_document
from specval import cli
from typer.testing import CliRunner

from .conftest import DOC_IDS
from .test_mutations import md_path, mutate, validate

runner = CliRunner()


def run(*args: str):
    return runner.invoke(cli.app, list(args))


def validate_all(out: Path, val: Path, *extra: str):
    return run("run", "--output", str(out), "--validation", str(val), *extra)


# ----------------------------------------------------------- review queue


def test_queue_order_fail_tables_warn_sample(out: Path) -> None:
    doc_id = DOC_IDS["html"]
    mutate(out, doc_id, "0x14", "0x1A")  # a fail (V4)
    mutate(out, doc_id, "0x1A and 2Ch", "0x1A and 2Ch Ox15")  # a warn (V8), also V4
    queue = validate(out, doc_id).review_queue
    kinds = [item["kind"] for item in queue]
    assert kinds == sorted(kinds, key=["fail", "table", "warn", "sample"].index)
    assert kinds.count("sample") == 3
    tables = [item for item in queue if item["kind"] == "table"]
    # "Table 1 – Miscellaneous control codes": matches "control" and "code"
    assert len(tables) == 1 and "control" in tables[0]["text"] and tables[0]["md_line"]


def test_sample_is_repeatable_and_uses_pages_for_pdf(out: Path) -> None:
    first = validate(out, DOC_IDS["pdf"]).review_queue
    second = validate(out, DOC_IDS["pdf"]).review_queue
    samples = [i["text"] for i in first if i["kind"] == "sample"]
    assert samples == [i["text"] for i in second if i["kind"] == "sample"]
    assert len(samples) == 2 and all(s.startswith("page ") for s in samples)  # 2-page PDF


def test_human_report_has_results_then_queue(out: Path, tmp_path: Path) -> None:
    validate_all(out, tmp_path / "val")
    text = (tmp_path / "val" / f"{DOC_IDS['saved']}.md").read_text(encoding="utf-8")
    assert text.index("| Check |") < text.index("## Review queue")
    assert "### 1. Failures" in text and "### 4. Random sample" in text
    report = json.loads((tmp_path / "val" / f"{DOC_IDS['saved']}.json").read_text())
    assert report["review_queue"] and len(report["markdown_sha256"]) == 64


# --------------------------------------------------------------- approve


def fm(out: Path, doc_id: str):
    return parse_document(md_path(out, doc_id).read_text(encoding="utf-8"))


def test_approve_needs_a_report(out: Path, tmp_path: Path) -> None:
    result = run("approve", DOC_IDS["html"], "--output", str(out), "--validation", str(tmp_path))
    assert result.exit_code == 1 and "specval run" in result.output


def test_approve_passed_document(out: Path, tmp_path: Path) -> None:
    doc_id = DOC_IDS["html"]
    val = tmp_path / "val"
    validate_all(out, val)
    before = fm(out, doc_id)[1]
    result = run("approve", doc_id, "--output", str(out), "--validation", str(val), "--by", "Ana")
    assert result.exit_code == 0, result.output
    front, body = fm(out, doc_id)
    assert body == before  # only the front matter changes
    assert front.validated["by"] == "Ana" and front.validated["validator"].startswith("specval ")
    assert len(front.validated["date"]) == 10 and front.manual_fixes == []


def test_approve_refuses_failed_report_without_override(out: Path, tmp_path: Path) -> None:
    doc_id = DOC_IDS["saved"]  # fails V7: missing image
    val = tmp_path / "val"
    validate_all(out, val)
    original = md_path(out, doc_id).read_bytes()
    result = run("approve", doc_id, "--output", str(out), "--validation", str(val))
    assert result.exit_code == 1 and "--override" in result.output
    assert md_path(out, doc_id).read_bytes() == original


def test_approve_with_override_records_reason(out: Path, tmp_path: Path) -> None:
    doc_id = DOC_IDS["saved"]
    val = tmp_path / "val"
    validate_all(out, val)
    result = run(
        "approve", doc_id, "--output", str(out), "--validation", str(val),
        "--override", "image missing in the source too", "--by", "Ana",
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    front, _ = fm(out, doc_id)
    assert front.validated is not None
    assert front.manual_fixes[-1]["reason"] == "image missing in the source too"
    assert front.manual_fixes[-1]["type"] == "approval override"
    assert front.manual_fixes[-1]["by"] == "Ana"


def test_approve_refuses_changed_markdown(out: Path, tmp_path: Path) -> None:
    doc_id = DOC_IDS["html"]
    val = tmp_path / "val"
    validate_all(out, val)
    mutate(out, doc_id, "The base row", "The bottom row")
    result = run("approve", doc_id, "--output", str(out), "--validation", str(val))
    assert result.exit_code == 1 and "changed since" in result.output


@pytest.mark.parametrize("status", ["failed"])
def test_approve_refuses_unconverted(out: Path, tmp_path: Path, status: str) -> None:
    from common.manifest import read_manifest, write_manifest

    manifest = read_manifest(out / "manifest.json")
    entry = manifest.get(DOC_IDS["html"])
    entry.status = status
    write_manifest(manifest, out / "manifest.json")
    val = tmp_path / "val"
    validate_all(out, val)
    result = run("approve", DOC_IDS["html"], "--output", str(out), "--validation", str(val))
    assert result.exit_code == 1 and "not converted" in result.output


def test_approve_allows_front_matter_edits_after_validation(out: Path, tmp_path: Path) -> None:
    """Title and spec_version are filled in by hand (also from the web UI) and do
    not change what was validated; only the body is compared."""
    doc_id = DOC_IDS["html"]
    val = tmp_path / "val"
    validate_all(out, val)
    path = md_path(out, doc_id)
    path.write_text(
        path.read_text(encoding="utf-8").replace("title: ''", "title: Sample spec", 1),
        encoding="utf-8",
    )
    result = run("approve", doc_id, "--output", str(out), "--validation", str(val))
    assert result.exit_code == 0, result.output
    assert fm(out, doc_id)[0].title == "Sample spec"


# ------------------------------------------- mirrored input folders (NL1)


def nest(out: Path, doc_id: str, folder: str) -> str:
    """Move a converted document to output/<folder>/<id>/, as specconv lays out a
    file from input/<folder>/ since NL1, and return its new id."""
    new_id = f"{folder}/{doc_id}"
    (out / folder).mkdir()
    (out / doc_id).rename(out / new_id)
    md = out / new_id / f"{doc_id}.md"
    text = md.read_text(encoding="utf-8").replace(f"id: {doc_id}\n", f"id: {new_id}\n", 1)
    md.write_text(text, encoding="utf-8")
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    for entry in manifest["documents"]:
        if entry["id"] == doc_id:
            entry["id"] = new_id
    (out / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return new_id


def test_nested_document_validated_and_approved(out: Path, tmp_path: Path) -> None:
    doc_id = nest(out, DOC_IDS["html"], "SCC Specs")
    val = tmp_path / "val"
    assert validate_all(out, val, "--doc", doc_id).exit_code in (0, 1)
    report = json.loads((val / f"{doc_id}.json").read_text(encoding="utf-8"))
    assert (val / f"{doc_id}.md").is_file()  # validation/ mirrors the folders too
    assert report["id"] == doc_id
    assert report["overall"] not in ("not_converted", "error")
    assert not any("not readable" in note for note in report.get("notes", []))
    result = run(
        "approve", doc_id, "--output", str(out), "--validation", str(val), "--by", "Ana",
        "--override", "test",
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    front, _ = fm(out, doc_id)
    assert front.validated["by"] == "Ana"
