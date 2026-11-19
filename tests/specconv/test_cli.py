"""CLI behaviour in phase 1: argument and config errors exit 2, missing
dependencies exit 2 with a hint, and the not-yet-built commands fail loudly
(exit 1) instead of pretending to succeed."""

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
    monkeypatch.setattr(deps, "check_pandoc",
                        lambda: [DependencyProblem("pandoc", "not found", "install it")])
    monkeypatch.setattr(deps, "check_python_package",
                        lambda dist, hint=None: [DependencyProblem(dist, "not installed", "uv sync")])
    result = invoke("convert", "--input", "input")
    assert result.exit_code == 2
    assert "pandoc: not found" in result.output and "docling: not installed" in result.output


def test_chromium_checked_only_for_browser_sources(write, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(deps, "check_chromium", lambda: calls.append("chromium") or [])
    write("static.yaml", "sources:\n  - {id: a, url: https://a.org/}\n")
    write("browser.yaml", "sources:\n  - {id: b, url: https://b.org/, render: browser}\n")
    invoke("fetch", "--sources", "static.yaml")
    assert calls == []
    invoke("fetch", "--sources", "browser.yaml")
    assert calls == ["chromium"]


def test_convert_stub_fails_loudly_and_writes_run_log(in_tmp: Path) -> None:
    result = invoke("convert", "--input", "input", "--output", "out")
    assert result.exit_code == 1
    assert "not implemented yet" in result.output
    [log] = (in_tmp / "out").glob("run-*.log")
    text = log.read_text(encoding="utf-8")
    assert "tool versions" in text and "pandoc" in text


def test_fetch_stub_writes_log_in_work(write, in_tmp: Path) -> None:
    write("s.yaml", "sources:\n  - {id: a, url: https://a.org/}\n")
    result = invoke("fetch", "--sources", "s.yaml", "--work", "w")
    assert result.exit_code == 1
    assert list((in_tmp / "w").glob("fetch-*.log"))


def test_check_deps_prints_versions() -> None:
    result = invoke("check-deps")
    assert result.exit_code == 0, result.output
    assert "pandoc 3." in result.output and "docling " in result.output
