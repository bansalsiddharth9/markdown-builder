from __future__ import annotations

from pathlib import Path

import pytest
from common.manifest import Manifest, ManifestEntry, write_manifest
from specval import cli
from typer.testing import CliRunner

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


def test_run_stub_fails_loudly_and_writes_log(output: Path, in_tmp: Path) -> None:
    result = invoke("run", "--output", str(output), "--validation", "val")
    assert result.exit_code == 1 and "not implemented yet" in result.output
    assert list((in_tmp / "val").glob("run-*.log"))


def test_approve_empty_override_exits_2(output: Path) -> None:
    result = invoke("approve", "doc-a", "--output", str(output), "--override", "  ")
    assert result.exit_code == 2 and "non-empty reason" in result.output


def test_approve_unknown_doc_exits_2(output: Path) -> None:
    assert invoke("approve", "nope", "--output", str(output)).exit_code == 2


def test_approve_stub_fails_loudly(output: Path) -> None:
    result = invoke("approve", "doc-a", "--output", str(output), "--by", "Sam")
    assert result.exit_code == 1 and "not implemented yet" in result.output
