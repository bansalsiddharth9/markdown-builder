"""Startup checks for system dependencies (design: Dependencies).

Each check returns a DependencyProblem with an install hint instead of
raising, so the CLI can report every missing dependency at once and then
exit with code 2.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

MIN_PYTHON = (3, 11)
MIN_PANDOC_MAJOR = 3


@dataclass(frozen=True)
class DependencyProblem:
    name: str
    message: str
    hint: str

    def __str__(self) -> str:
        return f"{self.name}: {self.message}\n    hint: {self.hint}"


def _search_path() -> str:
    """PATH plus the folder of the running interpreter, so tools installed
    into the project's .venv/bin are found even when the venv is not activated."""
    venv_bin = str(Path(sys.executable).parent)
    return os.pathsep.join([os.environ.get("PATH", ""), venv_bin])


def find_pandoc() -> Path | None:
    found = shutil.which("pandoc", path=_search_path())
    return Path(found) if found else None


def pandoc_version(pandoc: Path) -> str:
    """Return e.g. '3.14'. Raises RuntimeError if the output is not recognised."""
    out = subprocess.run(
        [str(pandoc), "--version"], capture_output=True, text=True, timeout=30, check=True
    ).stdout
    match = re.match(r"pandoc(?:\.exe)?\s+(\d+(?:\.\d+)*)", out)
    if not match:
        raise RuntimeError(f"unrecognised 'pandoc --version' output: {out[:80]!r}")
    return match.group(1)


def check_python() -> list[DependencyProblem]:
    if sys.version_info < MIN_PYTHON:
        need = ".".join(map(str, MIN_PYTHON))
        return [
            DependencyProblem(
                "python",
                f"Python {need}+ required, running {sys.version.split()[0]}",
                f"install Python {need}+ and recreate the environment "
                "with 'uv sync --all-packages'",
            )
        ]
    return []


def check_pandoc() -> list[DependencyProblem]:
    hint = (
        "download Pandoc 3.x from https://github.com/jgm/pandoc/releases and put the "
        "'pandoc' binary on PATH or in .venv/bin"
    )
    pandoc = find_pandoc()
    if pandoc is None:
        return [DependencyProblem("pandoc", "not found on PATH or in the environment", hint)]
    try:
        ver = pandoc_version(pandoc)
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        return [DependencyProblem("pandoc", f"{pandoc} could not be run: {exc}", hint)]
    if int(ver.split(".")[0]) < MIN_PANDOC_MAJOR:
        return [DependencyProblem("pandoc", f"version {ver} found at {pandoc}; 3.x required", hint)]
    return []


def check_python_package(dist: str, hint: str | None = None) -> list[DependencyProblem]:
    try:
        version(dist)
    except PackageNotFoundError:
        return [
            DependencyProblem(
                dist, "Python package not installed", hint or "run 'uv sync --all-packages'"
            )
        ]
    return []


def configure_browsers_path(browsers_path: Path | None) -> None:
    """Point Playwright at the configured browser folder, unless the user
    already set PLAYWRIGHT_BROWSERS_PATH themselves."""
    if browsers_path is not None and "PLAYWRIGHT_BROWSERS_PATH" not in os.environ:
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(browsers_path)


def check_chromium() -> list[DependencyProblem]:
    """Check that Playwright's Chromium is installed (without launching it)."""
    hint = "run 'PLAYWRIGHT_BROWSERS_PATH=.playwright uv run playwright install chromium'"
    problems = check_python_package("playwright")
    if problems:
        return problems
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as p:
            exe = Path(p.chromium.executable_path)
    except PlaywrightError as exc:
        return [DependencyProblem("chromium", f"Playwright could not start: {exc}", hint)]
    if not exe.exists():
        where = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "Playwright's default folder")
        return [DependencyProblem("chromium", f"not installed (looked in {where})", hint)]
    return []


def tool_versions() -> dict[str, str]:
    """Versions of every tool specconv may use, for the manifest and logs."""
    versions = {"python": sys.version.split()[0]}
    for dist in ("specconv", "docling", "pypdf", "httpx", "playwright"):
        try:
            versions[dist] = version(dist)
        except PackageNotFoundError:
            pass
    pandoc = find_pandoc()
    if pandoc is not None:
        try:
            versions["pandoc"] = pandoc_version(pandoc)
        except (OSError, subprocess.SubprocessError, RuntimeError):
            pass
    return versions


def check_models(models_path: Path) -> list[DependencyProblem]:
    """The pinned Docling models must be present (they are never downloaded on the fly)."""
    from specconv.models import missing_models

    missing = missing_models(models_path)
    if missing:
        names = ", ".join(m.repo_id for m in missing)
        return [
            DependencyProblem(
                "docling models",
                f"missing or at another revision in {models_path}: {names}",
                "run 'uv run specconv download-models' (needs network once)",
            )
        ]
    return []
