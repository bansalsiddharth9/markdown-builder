"""The review queue (design: Review queue): an ordered list of what a person
should check in one document.

1. Every fail finding.
2. Every table whose caption or header row matches always_review_table_patterns
   (e.g. "code", "command", "control"), even if it passed.
3. Every warn finding.
4. A random sample of source pages (PDF) or sections (HTML, DOCX), with a fixed
   seed per document, so re-runs pick the same ones.
"""

from __future__ import annotations

import random
import re
from typing import Any

from specval.config import SpecvalConfig
from specval.markdown import MarkdownDoc, MdTable
from specval.model import FAIL, WARN, CheckResult
from specval.reference import Reference

KIND_ORDER = ("fail", "table", "warn", "sample")


def _item(
    kind: str, text: str, check: str | None = None, md_line=None, source_loc=None, excerpt=None
):
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
    queue = []
    for check in checks:
        for f in check.findings:
            if f.severity == FAIL:
                queue.append(_item("fail", f.message, check.id, f.md_line, f.source_loc, f.excerpt))
    if md is not None and cfg.always_review_table_patterns:
        patterns = [
            (word, re.compile(rf"\b{re.escape(word)}", re.IGNORECASE))
            for word in cfg.always_review_table_patterns
        ]
        for table in md.tables:
            caption = table_caption(md, table)
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
        top = min(h.level for h in md.headings)
        sections = [h for h in md.headings if h.level <= top + 1]
        for h in sorted(rng.sample(sections, min(count, len(sections))), key=lambda h: h.line):
            queue.append(
                _item("sample", f"section '{h.text}': compare it with the source", md_line=h.line)
            )
    return queue
