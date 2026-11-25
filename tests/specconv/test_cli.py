"""CLI behaviour: argument and config errors exit 2, missing dependencies
exit 2 with a hint, failures exit 1."""

from __future__ import annotations

from pathlib import Path

import pytest
from specconv import cli, deps
from specconv.deps import DependencyProblem
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture(autouse=True)
def in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, repo_root: Path) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(repo_root / ".playwright"))
    (tmp_path / "input").mkdir()
    return tmp_path


def invoke(*args: str):
    return runner.invoke(cli.app, list(args))


def test_no_arguments_shows_help() -> None:
    result = invoke()
    assert "convert" in result.output and "fetch" in result.output


def test_convert_needs_input_or_sources() -> None:
    result = invoke("convert")
    assert result.exit_code == 2
    assert "--input, --sources or both" in result.output


def test_convert_input_must_be_a_folder() -> None:
    result = invoke("convert", "--input", "missing")
    assert result.exit_code == 2 and "not a folder" in result.output


def test_bad_config_exits_2(write) -> None:
    write("bad.yaml", "pdf:\n  min_text_page_ratio: 7\n")
    result = invoke("convert", "--input", "input", "--config", "bad.yaml")
    assert result.exit_code == 2 and "min_text_page_ratio" in result.output


def test_bad_sources_exit_2(write) -> None:
    write("s.yaml", "sources:\n  - id: a\n")
    result = invoke("fetch", "--sources", "s.yaml")
    assert result.exit_code == 2 and "'url' is required" in result.output


def test_fetch_only_unknown_id_exits_2(write) -> None:
    write("s.yaml", "sources:\n  - {id: a, url: https://a.org/}\n")
    result = invoke("fetch", "--sources", "s.yaml", "--only", "b")
    assert result.exit_code == 2 and "no source with that id" in result.output


def test_split_out_of_range_exits_2() -> None:
    assert invoke("convert", "--input", "input", "--split", "9").exit_code == 2


def test_missing_dependency_exits_2_and_lists_all(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        deps, "check_pandoc", lambda: [DependencyProblem("pandoc", "not found", "install it")]
    )
    monkeypatch.setattr(
        deps,
        "check_python_package",
        lambda dist, hint=None: [DependencyProblem(dist, "not installed", "uv sync")],
    )
    result = invoke("convert", "--input", "input")
    assert result.exit_code == 2
    assert "pandoc: not found" in result.output and "docling: not installed" in result.output


def test_chromium_checked_only_for_uncached_browser_sources(
    write, monkeypatch: pytest.MonkeyPatch, site
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(deps, "check_chromium", lambda: calls.append("chromium") or [])
    url = site.add("/p", "<p>x</p>")
    write("static.yaml", f"sources:\n  - {{id: a, url: '{url}'}}\n")
    write("browser.yaml", f"sources:\n  - {{id: b, url: '{url}', render: browser}}\n")
    invoke("fetch", "--sources", "static.yaml")
    assert calls == []
    assert invoke("fetch", "--sources", "browser.yaml").exit_code == 0
    assert calls == ["chromium"]
    invoke("fetch", "--sources", "browser.yaml")  # cached: Chromium not needed
    assert calls == ["chromium"]


def test_convert_empty_input_writes_manifest_and_run_log(in_tmp: Path) -> None:
    result = invoke("convert", "--input", "input", "--output", "out")
    assert result.exit_code == 0, result.output
    assert (in_tmp / "out" / "manifest.json").exists()
    [log] = (in_tmp / "out").glob("run-*.log")
    text = log.read_text(encoding="utf-8")
    assert "tool versions" in text and "pandoc" in text and "no documents" in text


def test_fetch_failure_exits_1_and_writes_log_in_work(write, in_tmp: Path, site) -> None:
    write("s.yaml", f"sources:\n  - {{id: a, url: '{site.url('/missing')}'}}\n")
    result = invoke("fetch", "--sources", "s.yaml", "--work", "w")
    assert result.exit_code == 1
    assert list((in_tmp / "w").glob("fetch-*.log"))


def test_check_deps_prints_versions(repo_root: Path) -> None:
    result = invoke("check-deps", "--config", str(repo_root / "config/specconv.yaml"))
    assert result.exit_code == 0, result.output
    assert "pandoc 3." in result.output and "docling " in result.output
    assert "model docling-project/docling-layout-heron 8f39ad3c0b4c" in result.output


def test_check_deps_reports_libreoffice_but_does_not_need_it(
    repo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LibreOffice is only needed for .doc files (DOC1): listed, never fatal."""
    from specconv.converters import libreoffice

    config = str(repo_root / "config/specconv.yaml")
    if libreoffice.find_soffice() is not None:
        assert "libreoffice " in invoke("check-deps", "--config", config).output
    monkeypatch.setattr(libreoffice, "find_soffice", lambda: None)
    result = invoke("check-deps", "--config", config)
    assert result.exit_code == 0, result.output
    assert "libreoffice not found (needed only for .doc files)" in result.output


def test_missing_models_exit_2_with_hint(tmp_path: Path) -> None:
    (tmp_path / "input" / "a.pdf").write_bytes(b"%PDF-1.4")
    result = invoke("convert", "--input", "input")  # built-in default: ./models (absent)
    assert result.exit_code == 2 and "specconv download-models" in result.output
