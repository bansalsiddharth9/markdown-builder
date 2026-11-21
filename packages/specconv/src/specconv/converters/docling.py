"""DoclingConverter for born-digital PDFs (design: Per-tool settings).

- PdfPipelineOptions: do_ocr=False, do_table_structure=True, TableFormer in
  ACCURATE mode with cell matching.
- Models are loaded from the local, pinned copies in pdf.models_path, with
  Hugging Face downloads switched off: conversion never uses the network.
- The DocumentConverter is created once and reused (model loading is slow).
- Images are exported as files in media/ and referenced from the Markdown.
- Tables with a row or column span are written as Docling's own HTML export
  of that table (rowspan/colspan), so a merged cell's text is never repeated
  into the cells it covers. Other tables stay pipe tables, written by
  Docling's default Markdown table serializer.
- Tables Docling labels as a document index (table of contents, list of
  tables or figures) are written as a bullet list, one entry per row, with
  the text exactly as in the PDF, dot leaders and page numbers included
  (deviations Q20).
- Text Docling classifies as a formula is not decoded (that needs another
  model); Docling would write ``<!-- formula-not-decoded -->`` and lose it.
  Its original text from the PDF is written instead, as a code block, so
  characters like ``<`` and ``[`` stay exact (deviations Q18).
- The Docling document is also saved as JSON for debugging.
- Text Docling classifies as page header or footer ("furniture") is left out
  of the Markdown, as Docling does; it is listed in conversion.json so a
  reviewer can see exactly what was dropped (a code line at the bottom of a
  page can be misclassified as a footer).
"""

from __future__ import annotations

import logging
import os
import re
import time
from importlib.metadata import version as dist_version
from pathlib import Path
from typing import Any

from specconv.converters.base import MEDIA_DIR, ConversionError, ConversionResult
from specconv.models import PINNED_MODELS, missing_models

IMAGES_SCALE = 2.0  # picture images rendered at 144 dpi (Docling's default 1.0 is 72 dpi; Q7)


def is_document_index(item: Any) -> bool:
    """A table Docling labels as a document index (contents list)."""
    # label is an enum in Docling, but a plain string also works.
    return getattr(item.label, "value", item.label) == "document_index"


def table_has_spans(item: Any) -> bool:
    """True when any cell spans more than one row or column (merged cells)."""
    return any(c.row_span > 1 or c.col_span > 1 for c in item.data.table_cells)


# Characters that could start Markdown or HTML markup in an index entry.
_MD_SPECIAL = re.compile(r"([\\`*_\[\]<>])")


def index_as_list(item: Any) -> str:
    """A document index (contents list) as a Markdown bullet list: the cells of
    each row joined by a space, Markdown characters escaped (deviation Q20).

    Empty rows are left out. A cell spanning several rows appears only in its first row.
    """
    rows: dict[int, list[tuple[int, str]]] = {}
    for cell in item.data.table_cells:
        text = " ".join(cell.text.split())
        if text:
            rows.setdefault(cell.start_row_offset_idx, []).append((cell.start_col_offset_idx, text))
    lines = []
    for r in sorted(rows):
        entry = " ".join(text for _, text in sorted(rows[r]))
        lines.append("- " + _MD_SPECIAL.sub(r"\\\1", entry))
    return "\n".join(lines)


def _span_aware_table_serializer() -> Any:
    """A Markdown table serializer that writes merged-cell tables as HTML and
    document indexes as bullet lists."""
    from docling_core.transforms.serializer.base import SerializationResult
    from docling_core.transforms.serializer.common import create_ser_result
    from docling_core.transforms.serializer.markdown import MarkdownTableSerializer

    class SpanAwareTableSerializer(MarkdownTableSerializer):
        """Docling's Markdown table serializer with two special cases (see module doc)."""

        def serialize(self, *, item, doc_serializer, doc, **kwargs) -> SerializationResult:
            """Index -> bullet list; merged cells -> HTML; anything else -> Docling's default.

            Nested and excluded tables always go to the default serializer, which
            already handles them.
            """
            nested = kwargs.get("_nested_in_table")
            excluded = item.self_ref in doc_serializer.get_excluded_refs(**kwargs)
            if not nested and not excluded and is_document_index(item):
                caption = doc_serializer.serialize_captions(item=item, **kwargs).text
                text = index_as_list(item)
                return create_ser_result(
                    text=f"{caption}\n\n{text}" if caption else text, span_source=item
                )
            if nested or excluded or not table_has_spans(item):
                return super().serialize(
                    item=item, doc_serializer=doc_serializer, doc=doc, **kwargs
                )
            # The caption goes inside the HTML table as <caption>, like Pandoc's
            # HTML tables. Serializing the captions marks them as written, so
            # they are not repeated as a separate paragraph.
            doc_serializer.serialize_captions(item=item, **kwargs)
            html = item.export_to_html(doc=doc, add_caption=True)
            return create_ser_result(text=html, span_source=item)

    return SpanAwareTableSerializer()


def _formula_text_serializer() -> Any:
    """Markdown text serializer that writes undecoded formulas as their original
    text in a code block (inline: code span) instead of a placeholder comment."""
    # _code_block and _code_span are private Docling helpers; they choose a fence or
    # backtick run long enough for the text. Check them when upgrading Docling.
    from docling_core.transforms.serializer.common import create_ser_result
    from docling_core.transforms.serializer.markdown import (
        MarkdownTextSerializer,
        _code_block,
        _code_span,
    )
    from docling_core.types.doc import FormulaItem

    class FormulaAsTextSerializer(MarkdownTextSerializer):
        """Docling's text serializer, except for formulas it could not decode."""

        def serialize(self, *, item, doc_serializer, doc, is_inline_scope=False, **kwargs):
            """Write an undecoded formula (no text, but PDF text in orig) as code (Q18)."""
            if isinstance(item, FormulaItem) and not item.text and item.orig:
                if item.self_ref in doc_serializer.get_excluded_refs(**kwargs):
                    return create_ser_result()
                text = _code_span(item.orig) if is_inline_scope else _code_block(item.orig)
                return create_ser_result(text=text, span_source=item)
            return super().serialize(
                item=item,
                doc_serializer=doc_serializer,
                doc=doc,
                is_inline_scope=is_inline_scope,
                **kwargs,
            )

    return FormulaAsTextSerializer()


class _WarningCollector(logging.Handler):
    """Collects Docling's own log warnings (e.g. table cells it dropped) so they
    end up in the document's warnings instead of only on the console."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        """Keep a warning from Docling code; duplicates are dropped."""
        # Docling's packages use loggers named after modules ("docling.…") and
        # after bare class names ("MatchingPostProcessor"); match by source file.
        if record.levelno < logging.WARNING:
            return
        path = record.pathname.replace("\\", "/")
        if "site-packages/" not in path or not path.split("site-packages/")[-1].startswith(
            "docling"
        ):
            return
        message = record.getMessage().strip()
        if message and message not in self.messages:
            self.messages.append(message)


def _furniture_texts(doc: Any) -> list[dict[str, object]]:
    """Texts Docling left out of the body as page headers or footers (deviation Q8)."""
    out = []
    for item in doc.texts:
        if getattr(item.content_layer, "value", item.content_layer) == "furniture":
            pages = sorted({p.page_no for p in item.prov})
            out.append({"pages": pages, "label": item.label.value, "text": item.text})
    return out


class DoclingConverter:
    """Convert born-digital PDFs with Docling to Markdown."""

    name = "docling"

    def __init__(self, models_path: Path) -> None:
        """Check the pinned models are present; raises ConversionError if not.

        Docling itself is loaded later, on the first conversion.
        """
        missing = missing_models(models_path)
        if missing:
            raise ConversionError(
                f"Docling models missing in {models_path}: "
                f"{', '.join(m.repo_id for m in missing)}; run 'specconv download-models'"
            )
        self.models_path = models_path.resolve()
        self._converter: Any = None

    def version(self) -> str:
        """Installed Docling version."""
        return dist_version("docling")

    @classmethod
    def options(cls) -> dict[str, object]:
        """Fixed options; known before converting (used for incremental runs)."""
        return {
            "do_ocr": False,
            "do_table_structure": True,
            "table_structure_mode": "accurate",
            "do_cell_matching": True,
            "generate_picture_images": True,
            "images_scale": IMAGES_SCALE,
            "image_export_mode": "referenced",
            "merged_cell_tables": "html",
            "undecoded_formulas": "original text as code",
            "document_index": "bullet list",
            "models": {m.repo_id: m.revision for m in PINNED_MODELS},
        }

    def _get_converter(self) -> Any:
        """Create the DocumentConverter on first use and reuse it (models load slowly).

        The settings here must match options().
        """
        if self._converter is None:
            # Never download anything: the pinned models are already local (E8).
            # Set before the Docling imports below, as huggingface_hub reads it on import.
            os.environ["HF_HUB_OFFLINE"] = "1"
            from docling.datamodel.base_models import InputFormat
            from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
            from docling.document_converter import DocumentConverter, PdfFormatOption

            opts = PdfPipelineOptions(artifacts_path=self.models_path)
            opts.do_ocr = False
            opts.do_table_structure = True
            opts.table_structure_options.mode = TableFormerMode.ACCURATE
            opts.table_structure_options.do_cell_matching = True
            opts.generate_picture_images = True
            opts.images_scale = IMAGES_SCALE
            self._converter = DocumentConverter(
                format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
            )
        return self._converter

    def convert(self, src: Path, out_dir: Path, json_path: Path | None = None) -> ConversionResult:
        """Convert a PDF; pictures go to out_dir/media.

        Raises ConversionError when Docling raises or reports failure. A partial
        success is accepted with a warning. Docling's log warnings for this run are
        added to the result's warnings (deviation Q13). json_path, if given, gets
        the Docling document as JSON, without image data.
        """
        from docling.datamodel.base_models import ConversionStatus
        from docling_core.transforms.serializer.markdown import (
            MarkdownDocSerializer,
            MarkdownParams,
        )
        from docling_core.types.doc import ImageRefMode

        out_dir.mkdir(parents=True, exist_ok=True)
        start = time.monotonic()
        converter = self._get_converter()
        collector = _WarningCollector()
        docling_log = logging.getLogger()  # root: records from every Docling logger propagate
        docling_log.addHandler(collector)
        try:
            # raises_on_error=False: failures come back in res.status and res.errors.
            res = converter.convert(src, raises_on_error=False)
        except Exception as exc:  # Docling raises various types for unreadable input
            raise ConversionError(f"docling failed: {type(exc).__name__}: {exc}") from exc
        finally:
            docling_log.removeHandler(collector)
        warnings = [e.error_message for e in (res.errors or [])] + collector.messages
        if res.status not in (ConversionStatus.SUCCESS, ConversionStatus.PARTIAL_SUCCESS):
            raise ConversionError(
                f"docling status {res.status.name}: {'; '.join(warnings) or 'no details'}"
            )
        if res.status == ConversionStatus.PARTIAL_SUCCESS:
            warnings.insert(0, "partial success, some pages may be incomplete")
        doc = res.document

        if json_path is not None:
            json_path.parent.mkdir(parents=True, exist_ok=True)
            doc.save_as_json(json_path, image_mode=ImageRefMode.PLACEHOLDER)

        # Save pictures into media/ and reference them as "media/<file>".
        # (_with_pictures_refs is the helper Docling's own save_as_markdown uses.)
        doc = doc._with_pictures_refs(
            image_dir=out_dir / MEDIA_DIR, page_no=None, uri_prefix=f"{MEDIA_DIR}/"
        )
        serializer = MarkdownDocSerializer(
            doc=doc,
            text_serializer=_formula_text_serializer(),
            table_serializer=_span_aware_table_serializer(),
            params=MarkdownParams(image_mode=ImageRefMode.REFERENCED),
        )
        markdown = serializer.serialize().text
        furniture = _furniture_texts(doc)
        if not markdown.endswith("\n"):
            markdown += "\n"

        media_root = out_dir / MEDIA_DIR
        media = (
            sorted(p.relative_to(out_dir) for p in media_root.rglob("*") if p.is_file())
            if media_root.exists()
            else []
        )
        return ConversionResult(
            markdown=markdown,
            tool=self.name,
            tool_version=self.version(),
            options=self.options(),
            media=media,
            warnings=warnings,
            duration_s=round(time.monotonic() - start, 3),
            details={
                "pages": len(doc.pages),
                "tables": len(doc.tables),
                "tables_as_html": sum(
                    1 for t in doc.tables if not is_document_index(t) and table_has_spans(t)
                ),
                "document_index_as_list": sum(1 for t in doc.tables if is_document_index(t)),
                "formulas_as_text": sum(
                    1
                    for t in doc.texts
                    if getattr(t.label, "value", t.label) == "formula" and not t.text and t.orig
                ),
                "pictures": len(doc.pictures),
                "dropped_as_page_furniture": furniture,
            },
        )
