"""Write the reports (design: Outputs).

    validation/
      summary.csv   one row per document: status per check, overall status
      <id>.json     full machine-readable findings
      <id>.md       human report: results table, then the findings to check

The human report lists fail findings first, then warnings, each with its
Markdown line and source location. The full review queue (pattern-matched
tables, a page sample) is added in phase 6.
"""

from __future__ import annotations

import csv
import io
import json
import os
from pathlib import Path

from specval.model import FAIL, WARN
from specval.runner import CHECK_ORDER, DocReport

SUMMARY_FIELDS = ("id", "source_type", "overall", *CHECK_ORDER, "fails", "warnings", "validated_at")


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_bytes(text.encode("utf-8"))
    os.replace(tmp, path)


def write_json(report: DocReport, folder: Path) -> Path:
    path = folder / f"{report.id}.json"
    _write_atomic(path, json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n")
    return path


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render_markdown(report: DocReport) -> str:
    e = report.entry
    out = [
        f"# Validation report: {report.id}",
        "",
        f"- Source: `{e.source}` ({e.source_type}, route `{e.route}`)",
        f"- Markdown: `{report.id}/{report.id}.md`",
        f"- Overall: **{report.overall.upper()}**",
        f"- Validated: {report.validated_at}",
    ]
    out += [f"- Note: {n}" for n in report.notes]
    out.append("")
    if not report.checks:
        return "\n".join(out) + "\n"
    out += ["| Check | Name | Status | Summary |", "| --- | --- | --- | --- |"]
    for check in report.checks:
        out.append(f"| {check.id} | {check.name} | {check.status} | {_cell(check.summary)} |")
    out.append("")
    for severity, title in ((FAIL, "Must fix or explain (fail)"), (WARN, "To check (warn)")):
        rows = [(c, f) for c in report.checks for f in c.findings if f.severity == severity]
        if not rows:
            continue
        out += [f"## {title}", ""]
        for number, (check, finding) in enumerate(rows, start=1):
            where = []
            if finding.md_line is not None:
                where.append(f"Markdown line {finding.md_line}")
            if finding.source_loc:
                where.append(f"source: {finding.source_loc}")
            out.append(
                f"{number}. **{check.id}** {finding.message}"
                + (f" ({'; '.join(where)})" if where else "")
            )
            if finding.excerpt:
                out.append(f"   > {_cell(finding.excerpt)}")
        out.append("")
    if report.overall not in (FAIL, WARN) and report.checks:
        out += ["Nothing to check from the checks run so far.", ""]
    return "\n".join(out)


def write_markdown(report: DocReport, folder: Path) -> Path:
    path = folder / f"{report.id}.md"
    _write_atomic(path, render_markdown(report))
    return path


def summary_row(report: DocReport) -> dict[str, str]:
    row = {
        "id": report.id,
        "source_type": report.entry.source_type,
        "overall": report.overall,
        "fails": str(sum(f.severity == FAIL for c in report.checks for f in c.findings)),
        "warnings": str(sum(f.severity == WARN for c in report.checks for f in c.findings)),
        "validated_at": report.validated_at,
    }
    by_id = {c.id: c.status for c in report.checks}
    for check_id in CHECK_ORDER:
        row[check_id] = by_id.get(check_id, "")
    return row


def write_summary(reports: list[DocReport], folder: Path, keep_others: set[str] | None) -> Path:
    """Write summary.csv. With keep_others (ids still in the manifest), rows of
    documents not validated in this run are kept (specval run --doc)."""
    path = folder / "summary.csv"
    rows: dict[str, dict[str, str]] = {}
    if keep_others is not None and path.is_file():
        with path.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                if row.get("id") in keep_others:
                    rows[row["id"]] = {k: row.get(k, "") for k in SUMMARY_FIELDS}
    for report in reports:
        rows[report.id] = summary_row(report)
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=SUMMARY_FIELDS, lineterminator="\n")
    writer.writeheader()
    for doc_id in sorted(rows):
        writer.writerow(rows[doc_id])
    _write_atomic(path, buffer.getvalue())
    return path
