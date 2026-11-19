"""Startup checks. specval needs no external programs, only Python and its packages."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version

MIN_PYTHON = (3, 11)
REQUIRED_PACKAGES = ("pypdf", "pdfplumber", "python-docx", "beautifulsoup4", "lxml",
                     "markdown-it-py", "rapidfuzz", "pymarkdownlnt", "pyyaml")


@dataclass(frozen=True)
class DependencyProblem:
    name: str
    message: str
    hint: str

    def __str__(self) -> str:
        return f"{self.name}: {self.message}\n    hint: {self.hint}"


def check_all() -> list[DependencyProblem]:
    problems: list[DependencyProblem] = []
    if sys.version_info < MIN_PYTHON:
        need = ".".join(map(str, MIN_PYTHON))
        problems.append(DependencyProblem(
            "python", f"Python {need}+ required, running {sys.version.split()[0]}",
            f"install Python {need}+ and recreate the environment with 'uv sync --all-packages'"))
    for dist in REQUIRED_PACKAGES:
        try:
            version(dist)
        except PackageNotFoundError:
            problems.append(DependencyProblem(dist, "Python package not installed",
                                              "run 'uv sync --all-packages'"))
    return problems


def tool_versions() -> dict[str, str]:
    versions = {"python": sys.version.split()[0]}
    for dist in ("specval", *REQUIRED_PACKAGES):
        try:
            versions[dist] = version(dist)
        except PackageNotFoundError:
            pass
    return versions
