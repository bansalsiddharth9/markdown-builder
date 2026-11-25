"""Approval (design: Approval).

`specval approve ID` is the only command that writes to a converted file. It
sets `validated` in the front matter (date, validator version, who), and only
when the latest report has no fail findings, or the person gives --override
with a reason, which is recorded in `manual_fixes`. The report must be for
the Markdown as it is now (the SHA-256 of its body is in the report), so an
approval never covers edits to the text made after the validation; edits to
the front matter (title, spec_version) are fine.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

from common.frontmatter import parse_document, render_document
from common.manifest import md_filename

from specval import __version__
from specval.runner import body_sha256


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
    """Approve one document and return a one-line message for the user.

    Raises ApproveError when approval is refused (see module docstring); the
    CLI turns that into exit code 1. ``today`` (ISO date) is for tests; it
    defaults to the local date. The Markdown is rewritten atomically, with
    the body unchanged.
    """
    report_path = validation / f"{doc_id}.json"
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ApproveError(
            f"no validation report {report_path}; run 'specval run --doc {doc_id}' first"
        ) from exc
    if report.get("overall") == "not_converted":
        raise ApproveError(f"{doc_id} was not converted; there is nothing to approve")
    md_path = output / doc_id / md_filename(doc_id)  # last part of the id (NL1)
    data = md_path.read_bytes()
    # Body only, so front matter edits (title, spec_version) after validation
    # do not block approval (deviation WB11).
    if body_sha256(data.decode("utf-8")) != report.get("markdown_body_sha256"):
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
    # Mapping instead of the design's free string, to record who (deviation D2).
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
    # Temp file and rename: a crash never leaves a half-written Markdown file.
    tmp = md_path.with_name(f".{md_path.name}.tmp")
    tmp.write_bytes(render_document(fm, body).encode("utf-8"))
    os.replace(tmp, md_path)
    return f"approved {doc_id}" + (f" with override ({fails} fail finding(s))" if override else "")
