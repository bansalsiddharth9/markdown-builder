"""A known-good conversion of the fixture specs, made once per test session.

The validator tests start from it: each test gets its own copy, so a test
can change ("mutate") a converted file and check that the right check fails
at the right place (design: Validator mutation tests).
"""

from __future__ import annotations

import os
import runpy
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

REPO = Path(__file__).resolve().parents[2]
FIXTURE_SCRIPT = REPO / "tests" / "fixtures" / "make_fixtures.py"

# Inputs are visited in sorted order: sample-spec.docx gets the plain slug.
DOC_IDS = {
    "html": "sample-spec-2",
    "docx": "sample-spec",
    "pdf": "sample-print",
    "saved": "sample-saved-spec",
}


@pytest.fixture(scope="session")
def golden(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """output/ folder with the HTML, DOCX, PDF and saved-page fixtures converted."""
    from specconv import cli as specconv_cli  # tests may use both tools

    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(REPO / ".playwright"))
    root = tmp_path_factory.mktemp("golden")
    fixtures = runpy.run_path(str(FIXTURE_SCRIPT))
    built = fixtures["build_all"](root / "fixtures")
    pdfs = fixtures["render_pdfs"](root / "fixtures", built["html"])
    inputs = root / "input"
    inputs.mkdir()
    for name in ("html", "docx", "image"):
        shutil.copy(built[name], inputs / built[name].name)
    shutil.copy(pdfs["pdf"], inputs / "sample-print.pdf")
    fixtures["build_saved_page"](inputs)
    result = CliRunner().invoke(
        specconv_cli.app,
        [
            "convert",
            "--input",
            str(inputs),
            "--output",
            str(root / "output"),
            "--work",
            str(root / "work"),
            "--config",
            str(REPO / "config" / "specconv.yaml"),
        ],
    )
    assert result.exit_code == 0, result.output
    return root / "output"


@pytest.fixture
def out(golden: Path, tmp_path: Path) -> Path:
    """A fresh copy of the golden output folder for one test."""
    target = tmp_path / "output"
    shutil.copytree(golden, target)
    return target
