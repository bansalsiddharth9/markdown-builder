from __future__ import annotations

import runpy
import shutil
from pathlib import Path

import pytest

FIXTURE_SCRIPT = Path(__file__).resolve().parent.parent / "fixtures" / "make_fixtures.py"


@pytest.fixture(scope="session")
def fixture_mod() -> dict:
    return runpy.run_path(str(FIXTURE_SCRIPT))


@pytest.fixture(scope="session")
def fixtures(tmp_path_factory: pytest.TempPathFactory, fixture_mod: dict) -> dict[str, Path]:
    """Synthetic fixture files, generated once per test session."""
    return fixture_mod["build_all"](tmp_path_factory.mktemp("fixtures"))


@pytest.fixture
def input_dir(tmp_path: Path, fixtures: dict[str, Path]) -> Path:
    """A fresh input folder with the HTML and DOCX fixture (and the image the HTML uses)."""
    folder = tmp_path / "input"
    folder.mkdir()
    for name in ("html", "docx", "image"):
        shutil.copy(fixtures[name], folder / fixtures[name].name)
    return folder


@pytest.fixture(scope="session")
def pdf_fixtures(fixtures: dict[str, Path], fixture_mod: dict, repo_root_session: Path) -> dict:
    """PDF fixtures printed with Playwright's Chromium (generated once per session)."""
    import os

    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(repo_root_session / ".playwright"))
    return fixture_mod["render_pdfs"](fixtures["html"].parent, fixtures["html"])


@pytest.fixture(scope="session")
def repo_root_session() -> Path:
    return Path(__file__).resolve().parents[2]
