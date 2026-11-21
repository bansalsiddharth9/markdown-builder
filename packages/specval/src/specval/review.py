"""The review queue (design: Review queue): an ordered list of what a person
should check in one document.

1. Every fail finding.
2. Every table whose caption or header row matches always_review_table_patterns
   (e.g. "code", "command", "control"), even if it passed.
3. Every warn finding.
4. A random sample of source pages (PDF) or sections (HTML, DOCX), with a fixed
   seed per document, so re-runs pick the same ones.

Each item is a plain dict (see _item) so it goes into the JSON report as is.
"""

from __future__ import annotations

import random
import re
from typing import Any

from specval.config import SpecvalConfig
from specval.markdown import MarkdownDoc, MdTable
from specval.model import FAIL, WARN, CheckResult
from specval.reference import Reference

KIND_ORDER = ("fail", "table", "warn", "sample")  # queue order, as in the design


def _item(
    kind: str, text: str, check: str | None = None, md_line=None, source_loc=None, excerpt=None
):
    """One queue item. Every key is always present, so readers need no defaults."""
    return {
        "kind": kind,
        "check": check,
        "text": text,
        "md_line": md_line,
        "source_loc": source_loc,
        "excerpt": excerpt,
    }


def table_caption(md: MarkdownDoc, table: MdTable) -> str:
    """An HTML table's <caption>, or the "Table …" line just before or after a pipe
    table (Pandoc writes it after, Docling before)."""
    if table.caption:
        return table.caption
    last = table.row_lines[-1] if table.row_lines else table.line
    # +2 / -2: the caption is separated from the table by one blank line.
    # Line numbers are 1-based, md.lines is 0-based.
    for line in (last + 2, table.line - 2):
        if 1 <= line <= len(md.lines) and md.lines[line - 1].strip().lower().startswith("table"):
            return md.lines[line - 1].strip()
    return ""


def build_queue(
    doc_id: str,
    checks: list[CheckResult],
    md: MarkdownDoc | None,
    ref: Reference | None,
    cfg: SpecvalConfig,
) -> list[dict[str, Any]]:
    """Build the ordered review queue for one document (see module docstring).

    md and ref may be None when the Markdown or the source could not be read.
    Without md no tables are listed. Samples are PDF pages when ref is a PDF
    with pages, otherwise Markdown sections.
    """
    queue = []
    for check in checks:
        for f in check.findings:
            if f.severity == FAIL:
                queue.append(_item("fail", f.message, check.id, f.md_line, f.source_loc, f.excerpt))
    if md is not None and cfg.always_review_table_patterns:
        # \b only at the start: "code" also matches "codes" and "Codeset".
        patterns = [
            (word, re.compile(rf"\b{re.escape(word)}", re.IGNORECASE))
            for word in cfg.always_review_table_patterns
        ]
        for table in md.tables:
            caption = table_caption(md, table)
            # The first row with any text; Pandoc may write an empty header row.
            header = " | ".join(next((r for r in table.grid if any(r)), ()))
            words = [w for w, p in patterns if p.search(caption) or p.search(header)]
            if words:
                queue.append(
                    _item(
                        "table",
                        f"table matches {', '.join(words)}: {caption or header}",
                        md_line=table.line,
                    )
                )
    for check in checks:
        for f in check.findings:
            if f.severity == WARN:
                queue.append(_item("warn", f.message, check.id, f.md_line, f.source_loc, f.excerpt))
    # Seed "<seed>:<id>": the same pages each run, but not the same page
    # numbers for every document (deviation V-aa).
    rng = random.Random(f"{cfg.sample_pages.seed}:{doc_id}")
    count = cfg.sample_pages.count
    if ref is not None and ref.kind == "pdf" and ref.pages:
        for page in sorted(rng.sample(range(1, ref.pages + 1), min(count, ref.pages))):
            queue.append(
                _item(
                    "sample",
                    f"page {page}: compare the whole page with the Markdown",
                    source_loc=f"page {page}",
                )
            )
    elif md is not None and md.headings:
        # Sections are the headings of the top two levels present (deviation V-aa).
        top = min(h.level for h in md.headings)
        sections = [h for h in md.headings if h.level <= top + 1]
        for h in sorted(rng.sample(sections, min(count, len(sections))), key=lambda h: h.line):
            queue.append(
                _item("sample", f"section '{h.text}': compare it with the source", md_line=h.line)
            )
    return queue
