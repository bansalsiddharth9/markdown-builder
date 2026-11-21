"""Write the reports (design: Outputs).

    validation/
      summary.csv   one row per document: status per check, overall status
      <id>.json     full machine-readable findings
      <id>.md       human report: results table, then the findings to check

The human report has the results table, then the review queue (design:
Review queue) with Markdown line numbers and source locations, then the
findings listed for information only.
"""

from __future__ import annotations

import csv
import io
import json
import os
from pathlib import Path

from specval.model import FAIL, INFO, WARN
from specval.runner import CHECK_ORDER, DocReport

# Review queue kinds (see specval.review) and their headings, in reading order.
QUEUE_SECTIONS = (
    ("fail", "1. Failures"),
    ("table", "2. Tables to check (always reviewed)"),
    ("warn", "3. Warnings"),
    ("sample", "4. Random sample"),
)
# Columns of summary.csv; one column per check, holding its status.
SUMMARY_FIELDS = ("id", "source_type", "overall", *CHECK_ORDER, "fails", "warnings", "validated_at")


def _write_atomic(path: Path, text: str) -> None:
    """Write UTF-8 text via a hidden temp file and a rename.

    A reader (such as the web UI) never sees a half-written report.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_bytes(text.encode("utf-8"))  # bytes: no newline translation on Windows
    os.replace(tmp, path)


def write_json(report: DocReport, folder: Path) -> Path:
    """Write <folder>/<id>.json (the full report) and return its path."""
    path = folder / f"{report.id}.json"
    _write_atomic(path, json.dumps(report.to_dict(), indent=2, ensure_ascii=False) + "\n")
    return path


def _cell(text: str) -> str:
    """Make text safe for one Markdown table cell or one quoted line."""
    return text.replace("|", "\\|").replace("\n", " ")


def render_markdown(report: DocReport) -> str:
    """The human report: header, results table, review queue, info findings.

    A document without checks (not converted) gets only the header and notes.
    Every queue section is shown, with "None." when empty, so the reader
    knows nothing was left out.
    """
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
    out += ["## Review queue", "", "Work through it in order; each item says where to look.", ""]
    for kind, title in QUEUE_SECTIONS:
        items = [i for i in report.review_queue if i["kind"] == kind]
        out += [f"### {title}", ""]
        if not items:
            out += ["None.", ""]
            continue
        for number, item in enumerate(items, start=1):
            where = []
            if item.get("md_line") is not None:
                where.append(f"Markdown line {item['md_line']}")
            if item.get("source_loc"):
                where.append(f"source: {item['source_loc']}")
            check = f"**{item['check']}** " if item.get("check") else ""
            out.append(
                f"{number}. {check}{item['text']}" + (f" ({'; '.join(where)})" if where else "")
            )
            if item.get("excerpt"):
                out.append(f"   > {_cell(item['excerpt'])}")
        out.append("")
    infos = [(c, f) for c in report.checks for f in c.findings if f.severity == INFO]
    if infos:
        out += ["## For information", ""]
        for check, finding in infos:
            loc = f" ({finding.source_loc})" if finding.source_loc else ""
            line = f" (Markdown line {finding.md_line})" if finding.md_line else ""
            out.append(f"- **{check.id}** {finding.message}{line}{loc}")
            if finding.excerpt:
                out.append(f"  > {_cell(finding.excerpt)}")
        out.append("")
    return "\n".join(out)


def write_markdown(report: DocReport, folder: Path) -> Path:
    """Write <folder>/<id>.md (the human report) and return its path."""
    path = folder / f"{report.id}.md"
    _write_atomic(path, render_markdown(report))
    return path


def summary_row(report: DocReport) -> dict[str, str]:
    """One summary.csv row: overall status, status per check, finding counts.

    Check columns are empty when the document has no checks (not converted).
    """
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
    documents not validated in this run are kept (specval run --doc,
    deviation V-r). Without it the file is rebuilt from ``reports`` only, so
    documents removed from the manifest drop out. Rows are sorted by id."""
    path = folder / "summary.csv"
    rows: dict[str, dict[str, str]] = {}
    if keep_others is not None and path.is_file():
        with path.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                if row.get("id") in keep_others:
                    # Only known columns: an older file may have other ones.
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
