"""PDF text-layer check for routing (design: Routing).

A page "has a text layer" when pypdf extracts at least min_chars_per_page
non-whitespace characters from it. A PDF is converted with Docling when at
least min_text_page_ratio of its pages have one; otherwise it is reported as
no_text_layer, so a damaged or image-only file never passes silently. PDFs
with text on some but not all pages are flagged mixed_text_layer, whichever
way they are routed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from specconv.config import PdfConfig

FLAG_MIXED = "mixed_text_layer"


class PdfReadError(ValueError):
    """The PDF cannot be opened or read."""


@dataclass
class TextLayer:
    pages: int
    text_pages: int
    pages_without_text: list[int] = field(default_factory=list)  # 1-based
    chars_per_page: list[int] = field(default_factory=list)

    @property
    def ratio(self) -> float:
        return self.text_pages / self.pages if self.pages else 0.0

    @property
    def mixed(self) -> bool:
        return 0 < self.text_pages < self.pages

    def summary(self) -> dict[str, object]:
        return {
            "pages": self.pages,
            "text_pages": self.text_pages,
            "ratio": round(self.ratio, 4),
            "pages_without_text": self.pages_without_text,
        }


def check_text_layer(path: Path, cfg: PdfConfig) -> TextLayer:
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError

    # pypdf logs harmless warnings for many real-world PDFs; keep them out of the run log.
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    try:
        reader = PdfReader(path)
        if reader.is_encrypted:
            try:
                if not reader.decrypt(""):
                    raise PdfReadError("PDF is password protected")
            except PyPdfError as exc:
                raise PdfReadError(f"PDF is encrypted and cannot be opened: {exc}") from exc
        counts = []
        for page in reader.pages:
            try:
                text = page.extract_text() or ""
            except Exception:  # a single unreadable page counts as having no text
                text = ""
            counts.append(sum(1 for ch in text if not ch.isspace()))
    except PdfReadError:
        raise
    except Exception as exc:  # pypdf raises many types for damaged files
        raise PdfReadError(f"PDF cannot be read: {type(exc).__name__}: {exc}") from exc
    if not counts:
        raise PdfReadError("PDF has no pages")
    without = [i + 1 for i, n in enumerate(counts) if n < cfg.min_chars_per_page]
    return TextLayer(
        pages=len(counts),
        text_pages=len(counts) - len(without),
        pages_without_text=without,
        chars_per_page=counts,
    )
