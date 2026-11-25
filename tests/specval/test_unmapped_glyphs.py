"""PDF characters drawn with a font that has no Unicode map (deviation V-ac).

EBU Tech 3264's code-page tables use such fonts: the PDF stores the glyph
picture, not which character it is. Readers then report "(cid:1)", "/G01" or,
worse, a plain letter that is wrong. The validator finds these fonts itself
(pdfplumber), leaves the affected lines out of V3's sentence count (they
cannot be compared), and asks for a check by eye per page instead.
"""

from __future__ import annotations

from pathlib import Path

from common.manifest import ManifestEntry
from specval.checks import Document
from specval.checks_more import check_v3, check_v5
from specval.config import SpecvalConfig
from specval.markdown import parse_markdown
from specval.reference import from_pdf
from specval.review import build_queue

SENTENCE = "The table below lists two control codes for testing only."
# What Docling writes for the fixture: U+FFFD for the glyphs it cannot name,
# and the plain "A" the PDF falsely claims.
MARKDOWN = f"{SENTENCE}\n\n| Alpha | �� |\n|---|---|\n| Beta | �A |\n"


def document(tmp_path: Path, pdf: Path, body: str = MARKDOWN) -> Document:
    """A converted document for the checks: body as the Markdown, pdf as the source."""
    return Document(
        id="codes",
        folder=tmp_path,
        entry=ManifestEntry(
            id="codes", source="codes.pdf", source_type="pdf", status="ok", converter="docling"
        ),
        md_path=tmp_path / "codes.md",
        md=parse_markdown(body, body),
        front_matter=None,
        front_matter_error=None,
        reference=from_pdf(pdf),
    )


def test_reference_lists_pages_with_unmapped_fonts(unmapped_pdf: Path) -> None:
    ref = from_pdf(unmapped_pdf)
    assert len(ref.unreadable) == 1
    page = ref.unreadable[0]
    assert page.page == 1
    # Three glyphs without a name and the silent "A": all from the same font.
    assert page.chars == 4
    assert page.fonts == ("ABCDEF+MSTT31test",)
    assert page.lines_left_out == 2


def test_unreadable_lines_left_out_of_v3_text_only(unmapped_pdf: Path) -> None:
    """V3 gets the text without those lines; the other checks (V5 looks cells
    up in the whole text) still see readable words such as "Alpha"."""
    ref = from_pdf(unmapped_pdf)
    assert ref.text_segments is not None
    v3_text = " ".join(s.text for s in ref.text_segments)
    assert SENTENCE in v3_text
    assert "/G01" not in v3_text and "Alpha" not in v3_text
    assert "Alpha" in " ".join(s.text for s in ref.segments)


def test_ordinary_fonts_give_no_unreadable_pages(mapped_pdf: Path) -> None:
    ref = from_pdf(mapped_pdf)
    assert ref.unreadable == []
    assert "Alpha XY" in " ".join(s.text for s in ref.segments)


def test_v3_warns_per_page_instead_of_missing_sentences(tmp_path: Path, unmapped_pdf: Path) -> None:
    v3 = check_v3(document(tmp_path, unmapped_pdf), SpecvalConfig())
    assert v3.status == "warn"
    assert not [f for f in v3.findings if f.message == "missing sentence"]
    [page] = [f for f in v3.findings if f.source_loc == "page 1"]
    assert page.severity == "warn"
    assert "no Unicode mapping" in page.message and "by eye" in page.message
    assert v3.details["unreadable_lines_left_out"] == 2


def test_v5_unreadable_cells_not_failed_when_the_source_is_unreadable(
    tmp_path: Path, unmapped_pdf: Path
) -> None:
    v5 = check_v5(document(tmp_path, unmapped_pdf), SpecvalConfig())
    assert v5.status != "fail", [f.message for f in v5.findings]
    assert v5.details["cells_unreadable"] == 2


def test_v5_replacement_character_still_fails_for_a_readable_source(
    tmp_path: Path, mapped_pdf: Path
) -> None:
    """A U+FFFD that the PDF does not explain is a conversion error and stays reported."""
    v5 = check_v5(document(tmp_path, mapped_pdf), SpecvalConfig())
    assert v5.status in ("warn", "fail")
    assert any("�" in f.message for f in v5.findings)
    assert v5.details["cells_unreadable"] == 0


def test_review_queue_lists_the_page(tmp_path: Path, unmapped_pdf: Path) -> None:
    doc = document(tmp_path, unmapped_pdf)
    cfg = SpecvalConfig()
    queue = build_queue("codes", [check_v3(doc, cfg)], doc.md, doc.reference, cfg)
    assert any(i["kind"] == "warn" and i["source_loc"] == "page 1" for i in queue)
