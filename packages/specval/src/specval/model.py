"""Results of the checks (design: Checks, Outputs).

Each check returns pass, warn, fail or not_applicable, plus findings with a
location (Markdown line number, source page or element) and a short excerpt.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

PASS, WARN, FAIL, NOT_APPLICABLE = "pass", "warn", "fail", "not_applicable"
NOT_RUN = "not_run"  # checks built in a later phase
STATUS_ORDER = {FAIL: 0, WARN: 1, PASS: 2, NOT_APPLICABLE: 3, NOT_RUN: 4}


@dataclass
class Finding:
    severity: str  # fail | warn
    message: str
    md_line: int | None = None  # 1-based line in <id>.md (front matter included)
    source_loc: str | None = None  # e.g. "page 3", "<h2> in section '1 Scope'"
    excerpt: str | None = None


@dataclass
class CheckResult:
    id: str  # V1 …
    name: str
    status: str
    summary: str
    findings: list[Finding] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def status_from(findings: list[Finding]) -> str:
    if any(f.severity == FAIL for f in findings):
        return FAIL
    if any(f.severity == WARN for f in findings):
        return WARN
    return PASS


def overall(statuses: list[str]) -> str:
    """fail if any check fails, warn if any warns, else pass (design: Outputs)."""
    if FAIL in statuses:
        return FAIL
    if WARN in statuses:
        return WARN
    return PASS
