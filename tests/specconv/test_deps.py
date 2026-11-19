from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from specconv import deps


def _fake_pandoc(folder: Path, version_line: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    exe = folder / "pandoc"
    exe.write_text(f"#!/bin/sh\necho '{version_line}'\n", encoding="utf-8")
    exe.chmod(0o755)
    return exe


@pytest.fixture
def isolated_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """PATH and the 'venv bin' both point at an empty folder."""
    empty = tmp_path / "bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    monkeypatch.setattr(sys, "executable", str(empty / "python"))
    return empty


def test_installed_pandoc_is_3x() -> None:
    assert deps.check_pandoc() == []
    assert deps.tool_versions()["pandoc"].startswith("3.")


def test_missing_pandoc_reported(isolated_path: Path) -> None:
    [problem] = deps.check_pandoc()
    assert problem.name == "pandoc" and "not found" in problem.message
    assert "github.com/jgm/pandoc" in problem.hint


def test_old_pandoc_reported(isolated_path: Path) -> None:
    _fake_pandoc(isolated_path, "pandoc 2.19.2")
    [problem] = deps.check_pandoc()
    assert "2.19.2" in problem.message and "3.x required" in problem.message


def test_pandoc_found_in_venv_bin_without_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    venv_bin = tmp_path / "venv" / "bin"
    _fake_pandoc(venv_bin, "pandoc 3.14")
    monkeypatch.setenv("PATH", str(tmp_path / "nothing"))
    monkeypatch.setattr(sys, "executable", str(venv_bin / "python"))
    assert deps.check_pandoc() == []


def test_unrecognised_pandoc_output_reported(isolated_path: Path) -> None:
    _fake_pandoc(isolated_path, "something else")
    [problem] = deps.check_pandoc()
    assert "could not be run" in problem.message


def test_missing_python_package_reported() -> None:
    [problem] = deps.check_python_package("no-such-package-xyz")
    assert "uv sync" in problem.hint


def test_installed_packages_found() -> None:
    assert deps.check_python_package("docling") == []
    assert deps.check_python() == []


def test_browsers_path_does_not_override_user_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "/user/choice")
    deps.configure_browsers_path(Path("/from/config"))
    assert os.environ["PLAYWRIGHT_BROWSERS_PATH"] == "/user/choice"
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH")
    deps.configure_browsers_path(Path("/from/config"))
    assert os.environ["PLAYWRIGHT_BROWSERS_PATH"] == "/from/config"


def test_chromium_installed_in_project(repo_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(repo_root / ".playwright"))
    assert deps.check_chromium() == []


def test_missing_chromium_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "no-browsers"))
    [problem] = deps.check_chromium()
    assert problem.name == "chromium" and "playwright install chromium" in problem.hint
