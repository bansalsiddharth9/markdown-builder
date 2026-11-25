from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_SCRIPT = REPO_ROOT / "tests" / "fixtures" / "make_fixtures.py"


@pytest.fixture(scope="session")
def fixture_mod() -> dict:
    import runpy

    return runpy.run_path(str(FIXTURE_SCRIPT))


@pytest.fixture(scope="session")
def fixtures(tmp_path_factory: pytest.TempPathFactory, fixture_mod: dict) -> dict[str, Path]:
    """Synthetic fixture files, generated once per test session."""
    return fixture_mod["build_all"](tmp_path_factory.mktemp("fixtures"))


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture
def write(tmp_path: Path):
    """Write a text file under tmp_path and return its path."""

    def _write(name: str, text: str) -> Path:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    return _write


@pytest.fixture(autouse=True)
def _no_internet(monkeypatch: pytest.MonkeyPatch) -> None:
    """Design: no network access in tests. Python code may only connect to the
    local machine (the test HTTP server, Playwright's driver)."""
    import socket

    real_connect = socket.socket.connect

    def guarded(self, address):  # type: ignore[no-untyped-def]
        if self.family in (socket.AF_INET, socket.AF_INET6):
            host = address[0]
            if host not in ("127.0.0.1", "::1", "localhost"):
                raise OSError(f"test tried to reach the network: {address!r}")
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)


@pytest.fixture(scope="session")
def doc_fixture(tmp_path_factory: pytest.TempPathFactory, fixtures: dict[str, Path]) -> Path:
    """sample-spec.doc: the DOCX fixture saved as legacy Word by LibreOffice.

    Skips the test where LibreOffice is not installed. A private profile
    folder keeps it from clashing with another running LibreOffice.
    """
    import shutil
    import subprocess

    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if soffice is None:
        pytest.skip("LibreOffice (soffice) not installed")
    import docx

    out = tmp_path_factory.mktemp("doc")
    profile = tmp_path_factory.mktemp("lo-profile")
    # LibreOffice garbles a table that ends the document when it WRITES .doc (the
    # cells come out as one run of text); Word always ends with a paragraph.
    staged = tmp_path_factory.mktemp("doc-src") / "sample-spec.docx"
    document = docx.Document(str(fixtures["docx"]))
    document.add_paragraph("")
    document.save(str(staged))
    subprocess.run(
        [soffice, f"-env:UserInstallation={profile.as_uri()}", "--headless", "--norestore",
         "--convert-to", "doc:MS Word 97", "--outdir", str(out), str(staged)],
        check=True, capture_output=True, timeout=300,
    )  # fmt: skip
    return out / "sample-spec.doc"


@pytest.fixture(scope="session")
def unmapped_pdf(tmp_path_factory: pytest.TempPathFactory, fixture_mod: dict) -> Path:
    """A one-page PDF whose table cells use a font without a Unicode map
    (the EBU Tech 3264 code-page case; deviations Q23 and V-ac)."""
    return fixture_mod["unmapped_font_pdf"](tmp_path_factory.mktemp("unmapped") / "codes.pdf")


@pytest.fixture(scope="session")
def mapped_pdf(tmp_path_factory: pytest.TempPathFactory, fixture_mod: dict) -> Path:
    """The same page with ordinary Helvetica in every cell (control case)."""
    return fixture_mod["unmapped_font_pdf"](
        tmp_path_factory.mktemp("mapped") / "codes.pdf", unmapped=False
    )
