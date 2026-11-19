from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from common.manifest import Manifest, ManifestEntry, write_manifest
from specval import cli
from typer.testing import CliRunner

from .conftest import DOC_IDS

runner = CliRunner()


@pytest.fixture(autouse=True)
def in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def output(in_tmp: Path) -> Path:
    out = in_tmp / "output"
    write_manifest(
        Manifest(
            entries=[ManifestEntry(id="doc-a", source="a.pdf", source_type="pdf", status="ok")]
        ),
        out / "manifest.json",
    )
    return out


def invoke(*args: str):
    return runner.invoke(cli.app, list(args))


def test_run_without_manifest_exits_2() -> None:
    result = invoke("run", "--output", "output")
    assert result.exit_code == 2 and "specconv convert" in result.output


def test_run_unknown_doc_exits_2(output: Path) -> None:
    result = invoke("run", "--output", str(output), "--doc", "nope")
    assert result.exit_code == 2 and "not in" in result.output


def test_run_bad_config_exits_2(output: Path, write) -> None:
    write("v.yaml", "sample_pages: {count: -2}\n")
    result = invoke("run", "--output", str(output), "--config", "v.yaml")
    assert result.exit_code == 2 and "sample_pages.count" in result.output


def test_run_corrupt_manifest_exits_2(in_tmp: Path, write) -> None:
    write("output/manifest.json", "{")
    assert invoke("run", "--output", "output").exit_code == 2


def test_run_missing_files_fail_and_log_is_written(output: Path, in_tmp: Path) -> None:
    """doc-a is in the manifest as converted, but its folder does not exist."""
    result = invoke("run", "--output", str(output), "--validation", "val")
    assert result.exit_code == 1
    assert list((in_tmp / "val").glob("run-*.log"))
    report = json.loads((in_tmp / "val" / "doc-a.json").read_text(encoding="utf-8"))
    assert report["overall"] == "fail"


def test_run_writes_reports_and_summary(out: Path, in_tmp: Path) -> None:
    result = invoke("run", "--output", str(out), "--validation", "val")
    # the saved-page fixture fails V7 on its deliberately missing image
    assert result.exit_code == 1, result.output
    val = in_tmp / "val"
    with (val / "summary.csv").open(encoding="utf-8") as fh:
        rows = {r["id"]: r for r in csv.DictReader(fh)}
    assert set(rows) == set(DOC_IDS.values())
    assert rows["sample-spec"]["overall"] == "pass" and rows["sample-spec"]["V3"] == "not_run"
    assert rows["sample-saved-spec"]["overall"] == "fail"
    assert rows["sample-saved-spec"]["V7"] == "fail" and rows["sample-saved-spec"]["fails"] == "1"
    report = json.loads((val / "sample-saved-spec.json").read_text(encoding="utf-8"))
    assert [c["id"] for c in report["checks"]] == [f"V{i}" for i in range(1, 10)]
    human = (val / "sample-saved-spec.md").read_text(encoding="utf-8")
    assert "Overall: **FAIL**" in human and "## Must fix or explain (fail)" in human
    assert "image file missing: ./Sample%20Saved%20Spec_files/missing.png (Markdown line" in human


def test_run_one_doc_keeps_other_summary_rows(out: Path, in_tmp: Path) -> None:
    assert invoke("run", "--output", str(out), "--validation", "val").exit_code == 1
    result = invoke("run", "--output", str(out), "--validation", "val", "--doc", "sample-spec")
    assert result.exit_code == 0, result.output
    with (in_tmp / "val" / "summary.csv").open(encoding="utf-8") as fh:
        assert {r["id"] for r in csv.DictReader(fh)} == set(DOC_IDS.values())


@pytest.mark.parametrize(
    "status, exit_code", [("failed", 1), ("no_text_layer", 1), ("unsupported", 0)]
)
def test_documents_not_converted(in_tmp: Path, status: str, exit_code: int) -> None:
    out = in_tmp / "output"
    write_manifest(
        Manifest(
            entries=[
                ManifestEntry(
                    id="d", source="d.pdf", source_type="pdf", status=status, error="boom"
                )
            ]
        ),
        out / "manifest.json",
    )
    result = invoke("run", "--output", str(out), "--validation", "val")
    assert result.exit_code == exit_code
    report = json.loads((in_tmp / "val" / "d.json").read_text(encoding="utf-8"))
    assert report["overall"] == "not_converted" and report["checks"] == []
    assert f"conversion status {status}: boom" in report["notes"][0]


def test_approve_empty_override_exits_2(output: Path) -> None:
    result = invoke("approve", "doc-a", "--output", str(output), "--override", "  ")
    assert result.exit_code == 2 and "non-empty reason" in result.output


def test_approve_unknown_doc_exits_2(output: Path) -> None:
    assert invoke("approve", "nope", "--output", str(output)).exit_code == 2


def test_approve_stub_fails_loudly(output: Path) -> None:
    result = invoke("approve", "doc-a", "--output", str(output), "--by", "Sam")
    assert result.exit_code == 1 and "not implemented yet" in result.output
