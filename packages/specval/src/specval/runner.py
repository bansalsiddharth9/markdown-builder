"""Validate the documents of one output folder (design: Validator).

specval reads only output/manifest.json, each <id>/ folder and its own
config: the Markdown, the saved source.<ext>, and conversion.json for the
options the converter used (HTML cleanup, encoding of a fetched page).
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from common.frontmatter import FrontMatterError, parse_document
from common.manifest import ManifestEntry

from specval import __version__
from specval.checks import CHECKS, Document
from specval.config import SpecvalConfig
from specval.markdown import parse_markdown
from specval.model import FAIL, NOT_RUN, CheckResult, Finding, overall
from specval.reference import Reference, from_docx, from_html, from_pdf

log = logging.getLogger("specval.runner")

LATER_CHECKS = (  # built in phase 6
    ("V3", "Text completeness"),
    ("V5", "Table content"),
    ("V6", "Code blocks"),
    ("V8", "Character confusion"),
    ("V9", "Acceptance questions"),
)
CHECK_ORDER = ("V1", "V2", "V3", "V4", "V5", "V6", "V7", "V8", "V9")
CONVERTED = frozenset({"ok", "skipped_unchanged"})
CONVERSION_FAILED = frozenset({"failed", "no_text_layer"})
NOT_CONVERTED = "not_converted"


@dataclass
class DocReport:
    id: str
    entry: ManifestEntry
    overall: str  # pass | warn | fail | not_converted
    checks: list[CheckResult] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    validated_at: str = ""
    duration_s: float = 0.0

    @property
    def is_failure(self) -> bool:
        """Counts for the exit code: a failed check, or a conversion that failed."""
        if self.overall == FAIL:
            return True
        return self.overall == NOT_CONVERTED and self.entry.status in CONVERSION_FAILED

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": self.entry.source,
            "source_type": self.entry.source_type,
            "route": self.entry.route,
            "converter": self.entry.converter,
            "conversion_status": self.entry.status,
            "overall": self.overall,
            "validated_at": self.validated_at,
            "specval_version": __version__,
            "duration_s": self.duration_s,
            "notes": self.notes,
            "checks": [c.to_dict() for c in self.checks],
        }


def _source_file(folder: Path) -> Path | None:
    found = sorted(p for p in folder.glob("source.*") if p.is_file())
    return found[0] if found else None


def load_reference(entry: ManifestEntry, source: Path, conversion: dict[str, Any]) -> Reference:
    suffix = source.suffix.lower()
    if suffix in (".html", ".htm"):
        options = entry.options or {}
        web = options.get("source") or {}
        fetch = conversion.get("fetch") or {}
        return from_html(
            source.read_bytes(),
            strip_tags=options.get("strip_tags") or ("script", "style", "noscript"),
            clean=web.get("clean", "none"),
            drop_selectors=web.get("drop_selectors") or (),
            encoding=fetch.get("encoding"),
        )
    if suffix == ".docx":
        return from_docx(source)
    if suffix == ".pdf":
        return from_pdf(source)
    raise ValueError(f"no reference extraction for {source.name}")


def validate_document(
    entry: ManifestEntry, output: Path, cfg: SpecvalConfig, now: str
) -> DocReport:
    start = time.monotonic()
    report = DocReport(id=entry.id, entry=entry, overall=NOT_CONVERTED, validated_at=now)
    if entry.status not in CONVERTED:
        report.notes.append(
            f"not converted (conversion status {entry.status}"
            + (f": {entry.error}" if entry.error else "")
            + "); nothing to validate"
        )
        return report
    folder = output / entry.id
    md_path = folder / f"{entry.id}.md"
    conversion: dict[str, Any] = {}
    try:
        conversion = json.loads((folder / "conversion.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        report.notes.append(f"conversion.json not readable: {exc}")

    md = None
    fm = None
    fm_error = None
    try:
        full = md_path.read_text(encoding="utf-8")
    except OSError as exc:
        full = None
        report.notes.append(f"{md_path.name} not readable: {exc}")
    if full is not None:
        try:
            fm, body = parse_document(full)
        except FrontMatterError as exc:
            fm_error = str(exc)
            from common.frontmatter import split_front_matter

            try:
                body = split_front_matter(full)[1]
            except FrontMatterError:
                body = full
        md = parse_markdown(full, body)

    source = _source_file(folder)
    reference = None
    if source is not None:
        try:
            reference = load_reference(entry, source, conversion)
            report.notes.extend(reference.notes)
        except Exception as exc:  # a damaged source must not stop the other documents
            log.exception("%s: reading %s failed", entry.id, source.name)
            report.notes.append(f"reference extraction from {source.name} failed: {exc}")

    doc = Document(
        id=entry.id,
        folder=folder,
        entry=entry,
        md_path=md_path,
        md=md,
        front_matter=fm,
        front_matter_error=fm_error,
        reference=reference,
        conversion=conversion,
        source_path=source,
    )
    results: dict[str, CheckResult] = {}
    for check in CHECKS:
        try:
            result = check(doc, cfg)
        except Exception as exc:  # a crashing check is a failed check, reported, not hidden
            log.exception("%s: %s crashed", entry.id, check.__name__)
            check_id = check.__name__.removeprefix("check_").upper()
            result = CheckResult(
                check_id,
                check.__name__,
                FAIL,
                f"check crashed: {type(exc).__name__}: {exc}",
                [Finding(FAIL, f"internal error: {type(exc).__name__}: {exc}")],
            )
        results[result.id] = result
    for check_id, name in LATER_CHECKS:
        results[check_id] = CheckResult(check_id, name, NOT_RUN, "built in phase 6")
    report.checks = [results[c] for c in CHECK_ORDER]
    if md is None:
        report.checks[CHECK_ORDER.index("V7")].findings.insert(
            0, Finding(FAIL, f"{md_path.name} not readable")
        )
        report.checks[CHECK_ORDER.index("V7")].status = FAIL
    report.overall = overall([c.status for c in report.checks])
    report.duration_s = round(time.monotonic() - start, 3)
    return report
