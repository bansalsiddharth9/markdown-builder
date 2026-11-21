"""Startup checks. specval needs no external programs, only Python and its packages."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version

MIN_PYTHON = (3, 11)
# Distribution names (as pip knows them, not import names) that specval needs.
# They are also logged with their versions at the start of each run.
REQUIRED_PACKAGES = (
    "pypdf",
    "pdfplumber",
    "python-docx",
    "beautifulsoup4",
    "lxml",
    "html5lib",
    "markdown-it-py",
    "rapidfuzz",
    "pymarkdownlnt",
    "pyyaml",
)


@dataclass(frozen=True)
class DependencyProblem:
    """One missing or too-old dependency, with a hint on how to fix it."""

    name: str  # "python" or a package name
    message: str  # what is wrong
    hint: str  # what to run to fix it

    def __str__(self) -> str:
        """Two lines for the terminal: the problem, then the hint."""
        return f"{self.name}: {self.message}\n    hint: {self.hint}"


def check_all() -> list[DependencyProblem]:
    """Return every problem found; an empty list means specval can run.

    The CLI prints them all and exits with code 2, so the user sees every
    problem at once instead of fixing them one by one.
    """
    problems: list[DependencyProblem] = []
    if sys.version_info < MIN_PYTHON:
        need = ".".join(map(str, MIN_PYTHON))
        problems.append(
            DependencyProblem(
                "python",
                f"Python {need}+ required, running {sys.version.split()[0]}",
                f"install Python {need}+ and recreate the environment "
                "with 'uv sync --all-packages'",
            )
        )
    for dist in REQUIRED_PACKAGES:
        try:
            version(dist)
        except PackageNotFoundError:
            problems.append(
                DependencyProblem(
                    dist, "Python package not installed", "run 'uv sync --all-packages'"
                )
            )
    return problems


def tool_versions() -> dict[str, str]:
    """Versions of Python, specval and its packages, for the run log.

    Packages that are not installed are left out instead of raising.
    """
    versions = {"python": sys.version.split()[0]}
    for dist in ("specval", *REQUIRED_PACKAGES):
        try:
            versions[dist] = version(dist)
        except PackageNotFoundError:
            pass
    return versions
