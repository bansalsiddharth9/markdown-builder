"""PDF text-layer routing (design: Routing)."""

from __future__ import annotations

from pathlib import Path

import pytest
from specconv.config import PdfConfig
from specconv.textlayer import PdfReadError, check_text_layer

CFG = PdfConfig()


def test_born_digital_pdf_has_text_on_every_page(pdf_fixtures: dict) -> None:
    layer = check_text_layer(pdf_fixtures["pdf"], CFG)
    assert layer.pages == layer.text_pages == 2 and not layer.mixed and layer.ratio == 1.0


def test_image_only_pdf_has_no_text(pdf_fixtures: dict) -> None:
    layer = check_text_layer(pdf_fixtures["image_pdf"], CFG)
    assert layer.pages >= 1 and layer.text_pages == 0 and not layer.mixed
    assert layer.pages_without_text == list(range(1, layer.pages + 1))


@pytest.mark.parametrize(
    "name, text_pages, ratio",
    [
        ("mostly_text_pdf", 3, 0.75),
        ("mostly_image_pdf", 1, 0.25),
    ],
)
def test_mixed_pdfs(pdf_fixtures: dict, name: str, text_pages: int, ratio: float) -> None:
    layer = check_text_layer(pdf_fixtures[name], CFG)
    assert (layer.pages, layer.text_pages, layer.ratio, layer.mixed) == (4, text_pages, ratio, True)


def test_threshold_is_configurable(pdf_fixtures: dict) -> None:
    # page 2 of the fixture has fewer than 500 characters
    layer = check_text_layer(pdf_fixtures["pdf"], PdfConfig(min_chars_per_page=500))
    assert layer.text_pages == 1 and layer.pages_without_text == [2]


def test_damaged_pdf(tmp_path: Path) -> None:
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.4 not really")
    with pytest.raises(PdfReadError, match="cannot be read|no pages"):
        check_text_layer(bad, CFG)


def test_password_protected_pdf(pdf_fixtures: dict, tmp_path: Path) -> None:
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    for page in PdfReader(pdf_fixtures["pdf"]).pages:
        writer.add_page(page)
    writer.encrypt(user_password="secret", owner_password="owner")
    locked = tmp_path / "locked.pdf"
    with locked.open("wb") as fh:
        writer.write(fh)
    with pytest.raises(PdfReadError, match="password|encrypted"):
        check_text_layer(locked, CFG)
