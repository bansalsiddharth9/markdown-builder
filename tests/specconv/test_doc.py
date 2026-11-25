"""Legacy Word .doc files: LibreOffice saves them as .docx, Pandoc converts that
(deviation DOC1)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from common.frontmatter import parse_document
from common.manifest import read_manifest
from specconv import cli
from specconv.converters import libreoffice
from typer.testing import CliRunner

runner = CliRunner()
REPO_CONFIG = Path(__file__).resolve().parents[2] / "config" / "specconv.yaml"


@pytest.fixture(autouse=True)
def in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


def convert():
    return runner.invoke(
        cli.app,
        ["convert", "--input", "input", "--output", "output", "--work", "work",
         "--config", str(REPO_CONFIG)],
    )  # fmt: skip


@pytest.fixture
def doc_input(doc_fixture: Path, fixtures: dict[str, Path]) -> Path:
    """input/ASS/sample-spec.doc and, for comparison, input/other.html."""
    (Path("input") / "ASS").mkdir(parents=True)
    shutil.copy(doc_fixture, Path("input/ASS/sample-spec.doc"))
    shutil.copy(fixtures["html"], Path("input/other.html"))
    shutil.copy(fixtures["image"], Path("input") / fixtures["image"].name)
    return Path("input")


def test_doc_converted_through_libreoffice(doc_input: Path) -> None:
    result = convert()
    assert result.exit_code == 0, result.output
    entry = read_manifest(Path("output/manifest.json")).get("ASS/sample-spec")
    assert entry.status == "ok" and entry.source_type == "doc"
    assert entry.route == "libreoffice+pandoc:docx"
    assert "libreoffice" in entry.tool_versions and "pandoc" in entry.tool_versions
    folder = Path("output/ASS/sample-spec")
    # The original is kept as source.doc; LibreOffice's copy is what Pandoc read.
    assert (folder / "source.doc").read_bytes() == doc_fixture_bytes(doc_input)
    assert (folder / libreoffice.DOCX_NAME).is_file()
    fm, body = parse_document((folder / "sample-spec.md").read_text(encoding="utf-8"))
    assert fm.source_type == "doc" and fm.source_file == "ASS/sample-spec.doc"
    assert fm.converted_with.startswith("pandoc ")
    assert "Sample Caption Display Specification" in body  # the title heading
    assert "| Rows     | 15 rows    |" in body  # the last table survived


def doc_fixture_bytes(doc_input: Path) -> bytes:
    return (doc_input / "ASS" / "sample-spec.doc").read_bytes()


def test_doc_skipped_when_unchanged(doc_input: Path) -> None:
    assert convert().exit_code == 0
    assert convert().exit_code == 0
    entry = read_manifest(Path("output/manifest.json")).get("ASS/sample-spec")
    assert entry.status == "skipped_unchanged"


def test_missing_libreoffice_fails_only_the_doc(
    doc_input: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(libreoffice, "find_soffice", lambda: None)
    result = convert()
    assert result.exit_code == 1
    manifest = read_manifest(Path("output/manifest.json"))
    assert manifest.get("ASS/sample-spec").status == "failed"
    assert "LibreOffice" in manifest.get("ASS/sample-spec").error
    assert manifest.get("other").status == "ok"
