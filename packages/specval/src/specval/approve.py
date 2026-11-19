"""Approval (design: Approval).

`specval approve ID` is the only command that writes to a converted file. It
sets `validated` in the front matter (date, validator version, who), and only
when the latest report has no fail findings, or the person gives --override
with a reason, which is recorded in `manual_fixes`. The report must be for
the Markdown as it is now (its SHA-256 is in the report), so an approval
never covers edits made after the validation.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path

from common.frontmatter import parse_document, render_document

from specval import __version__


class ApproveError(RuntimeError):
    """Approval refused; the message says why and what to do."""


def approve(
    doc_id: str,
    output: Path,
    validation: Path,
    override: str | None = None,
    by: str | None = None,
    today: str | None = None,
) -> str:
    report_path = validation / f"{doc_id}.json"
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ApproveError(
            f"no validation report {report_path}; run 'specval run --doc {doc_id}' first"
        ) from exc
    if report.get("overall") == "not_converted":
        raise ApproveError(f"{doc_id} was not converted; there is nothing to approve")
    md_path = output / doc_id / f"{doc_id}.md"
    data = md_path.read_bytes()
    if hashlib.sha256(data).hexdigest() != report.get("markdown_sha256"):
        raise ApproveError(
            f"{md_path} changed since it was validated ({report.get('validated_at')}); "
            f"run 'specval run --doc {doc_id}' again"
        )
    fails = sum(
        1
        for check in report.get("checks", [])
        for f in check["findings"]
        if f["severity"] == "fail"
    )
    if fails and not override:
        raise ApproveError(
            f"the latest report has {fails} fail finding(s); fix them and validate again, "
            'or approve with --override "reason"'
        )
    today = today or dt.date.today().isoformat()
    fm, body = parse_document(data.decode("utf-8"))
    fm.validated = {"date": today, "validator": f"specval {__version__}", "by": by}
    if override:
        fm.manual_fixes = [
            *fm.manual_fixes,
            {
                "type": "approval override",
                "reason": override,
                "date": today,
                "by": by,
                "fail_findings": fails,
            },
        ]
    tmp = md_path.with_name(f".{md_path.name}.tmp")
    tmp.write_bytes(render_document(fm, body).encode("utf-8"))
    os.replace(tmp, md_path)
    return f"approved {doc_id}" + (f" with override ({fails} fail finding(s))" if override else "")
