"""Results of the checks (design: Checks, Outputs).

Each check returns pass, warn, fail or not_applicable, plus findings with a
location (Markdown line number, source page or element) and a short excerpt.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

PASS, WARN, FAIL, NOT_APPLICABLE = "pass", "warn", "fail", "not_applicable"
INFO = "info"  # a finding listed for reference that does not change the status
NOT_RUN = "not_run"  # checks built in a later phase (deviation V-q)
# Sort key for statuses: worst first, so a list sorted by it shows failures at the top.
STATUS_ORDER = {FAIL: 0, WARN: 1, PASS: 2, NOT_APPLICABLE: 3, NOT_RUN: 4}


@dataclass
class Finding:
    """One problem (or note) found by a check, with where to look for it."""

    severity: str  # fail | warn | info
    message: str
    md_line: int | None = None  # 1-based line in <id>.md (front matter included)
    source_loc: str | None = None  # e.g. "page 3", "<h2> in section '1 Scope'"
    excerpt: str | None = None  # short piece of the text concerned


@dataclass
class CheckResult:
    """The outcome of one check (V1 to V9) for one document."""

    id: str  # V1 …
    name: str  # short human name of the check
    status: str  # pass | warn | fail | not_applicable | not_run
    summary: str  # one line for the results table
    findings: list[Finding] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)  # counts etc., JSON report only

    def to_dict(self) -> dict[str, Any]:
        """Plain dict for the JSON report (findings become dicts too)."""
        return asdict(self)


def status_from(findings: list[Finding]) -> str:
    """Status a check gets from its findings: worst severity wins.

    Info findings do not count: with no fail or warn finding the check passes.
    """
    if any(f.severity == FAIL for f in findings):
        return FAIL
    if any(f.severity == WARN for f in findings):
        return WARN
    return PASS


def overall(statuses: list[str]) -> str:
    """fail if any check fails, warn if any warns, else pass (design: Outputs).

    not_applicable and not_run checks count as pass here.
    """
    if FAIL in statuses:
        return FAIL
    if WARN in statuses:
        return WARN
    return PASS
