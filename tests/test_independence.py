"""Design rule: specconv and specval never import each other, and common
only depends on the standard library and PyYAML."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGES = Path(__file__).resolve().parent.parent / "packages"


def imported_top_level_modules(src: Path) -> dict[str, set[str]]:
    """Map each top-level module name imported anywhere under src to the files importing it."""
    found: dict[str, set[str]] = {}
    for py in src.rglob("*.py"):
        for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                found.setdefault(name.split(".")[0], set()).add(str(py.relative_to(PACKAGES)))
    return found


@pytest.mark.parametrize("package, forbidden", [
    ("specconv", "specval"),
    ("specval", "specconv"),
])
def test_converter_and_validator_do_not_import_each_other(package: str, forbidden: str) -> None:
    imports = imported_top_level_modules(PACKAGES / package / "src")
    assert forbidden not in imports, f"{package} imports {forbidden} in {imports[forbidden]}"


def test_common_imports_no_tools() -> None:
    imports = imported_top_level_modules(PACKAGES / "common" / "src")
    assert not {"specconv", "specval", "specweb"} & set(imports)
    third_party = set(imports) - {"__future__", "common", "dataclasses", "datetime", "json",
                                  "pathlib", "typing"}
    assert third_party == {"yaml"}
