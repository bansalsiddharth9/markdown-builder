"""Design principle 5: every dependency is open source and usable in a company,
and LICENSES.md lists exactly what is installed."""

from __future__ import annotations

import runpy
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "licenses.py"
lic = runpy.run_path(str(SCRIPT))


def test_all_installed_licenses_allowed() -> None:
    assert lic["disallowed"](lic["collect"]()) == []


def test_licenses_md_up_to_date() -> None:
    expected = lic["render"](lic["collect"]())
    assert lic["LICENSES_MD"].read_text(encoding="utf-8") == expected, \
        "LICENSES.md is out of date; run: uv run python scripts/licenses.py"


def test_license_expressions_parsed() -> None:
    ids = lic["_license_ids"]
    assert ids("Apache-2.0 AND Apache-2.0 WITH LLVM-exception AND MIT") == {
        "Apache-2.0", "LLVM-exception", "MIT"}
    assert ids("(MIT OR Apache-2.0)") == {"MIT", "Apache-2.0"}
    assert lic["disallowed"]([("x", "1", "GPL-3.0-only")]) == ["x 1: GPL-3.0-only"]
    assert lic["disallowed"]([("y", "1", "CC-BY-NC-4.0")]) == ["y 1: CC-BY-NC-4.0"]
