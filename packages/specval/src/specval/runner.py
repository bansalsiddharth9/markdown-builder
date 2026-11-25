"""Validate the documents of one output folder (design: Validator).

specval reads only output/manifest.json, each <id>/ folder and its own
config: the Markdown, the saved source.<ext>, and conversion.json for the
options the converter used (HTML cleanup, encoding of a fetched page).
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from common.frontmatter import FrontMatterError, parse_document
from common.manifest import ManifestEntry, md_filename

from specval import __version__
from specval.checks import CHECKS as CORE_CHECKS
from specval.checks import Document
from specval.checks_more import check_v3, check_v5, check_v6, check_v8, check_v9
from specval.config import AcceptanceQuestion, SpecvalConfig
from specval.markdown import parse_markdown
from specval.model import FAIL, CheckResult, Finding, overall
from specval.reference import Reference, from_doc, from_docx, from_html, from_pdf
from specval.review import build_queue

log = logging.getLogger("specval.runner")

# Run order of the check functions; reports list results in CHECK_ORDER instead.
CHECKS = (*CORE_CHECKS, check_v3, check_v5, check_v6, check_v8, check_v9)
CHECK_ORDER = ("V1", "V2", "V3", "V4", "V5", "V6", "V7", "V8", "V9")
# Manifest statuses that mean the Markdown exists and can be checked.
CONVERTED = frozenset({"ok", "skipped_unchanged"})
# Statuses that make the run exit 1; "unsupported" does not (deviation V-o). Since
# DOC1 nothing is unsupported any more, but old manifests may still say so.
CONVERSION_FAILED = frozenset({"failed", "no_text_layer"})
NOT_CONVERTED = "not_converted"


@dataclass
class DocReport:
    """Everything specval found for one document; written as <id>.json and <id>.md."""

    id: str
    entry: ManifestEntry  # the document's manifest entry
    overall: str  # pass | warn | fail | not_converted
    checks: list[CheckResult] = field(default_factory=list)  # in CHECK_ORDER
    notes: list[str] = field(default_factory=list)  # problems that are not check findings
    validated_at: str = ""  # ISO 8601 UTC, same for every document of a run
    duration_s: float = 0.0
    review_queue: list[dict[str, Any]] = field(default_factory=list)  # see specval.review
    markdown_sha256: str | None = None  # of the <id>.md that was checked
    markdown_body_sha256: str | None = None  # of its body, without front matter (for approve)

    @property
    def is_failure(self) -> bool:
        """Counts for the exit code: a failed check, or a conversion that failed."""
        if self.overall == FAIL:
            return True
        return self.overall == NOT_CONVERTED and self.entry.status in CONVERSION_FAILED

    def to_dict(self) -> dict[str, Any]:
        """The <id>.json report. specval approve and specweb read it back."""
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
            "markdown_sha256": self.markdown_sha256,
            "markdown_body_sha256": self.markdown_body_sha256,
            "checks": [c.to_dict() for c in self.checks],
            "review_queue": self.review_queue,
        }


def body_sha256(text: str) -> str:
    """SHA-256 of the Markdown without its front matter: title, spec_version
    and manual_fixes are edited by hand and do not change what was validated
    (deviation WB11). A file whose front matter cannot be split is hashed whole."""
    from common.frontmatter import FrontMatterError, split_front_matter

    try:
        body = split_front_matter(text)[1]
    except FrontMatterError:
        body = text
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _source_file(folder: Path) -> Path | None:
    """The saved source.<ext> in a document folder, or None if there is none.

    Sorted so the choice is stable if there is more than one.
    """
    found = sorted(p for p in folder.glob("source.*") if p.is_file())
    return found[0] if found else None


def load_reference(entry: ManifestEntry, source: Path, conversion: dict[str, Any]) -> Reference:
    """Extract the reference (text, headings, tables, ...) from the saved source.

    For HTML the same cleanup the converter used is applied: strip_tags and
    the web source's clean/drop_selectors from the manifest options, and the
    encoding recorded in conversion.json for a fetched page. Otherwise the
    reference would contain text the converter removed on purpose. Raises
    ValueError for a file type with no extractor.
    """
    suffix = source.suffix.lower()
    if suffix in (".html", ".htm"):
        options = entry.options or {}
        web = options.get("source") or {}
        fetch = conversion.get("fetch") or {}
        return from_html(
            source.read_bytes(),
            # Fallback for manifests without strip_tags in the options.
            strip_tags=options.get("strip_tags") or ("script", "style", "noscript"),
            clean=web.get("clean", "none"),
            drop_selectors=web.get("drop_selectors") or (),
            encoding=fetch.get("encoding"),
        )
    if suffix == ".docx":
        return from_docx(source)
    if suffix == ".doc":
        # LibreOffice's .docx, kept by the converter next to source.doc (DOC1).
        docx_name = (conversion.get("libreoffice") or {}).get("docx") or "libreoffice.docx"
        return from_doc(source, source.parent / docx_name)
    if suffix == ".pdf":
        return from_pdf(source)
    raise ValueError(f"no reference extraction for {source.name}")


def validate_document(
    entry: ManifestEntry,
    output: Path,
    cfg: SpecvalConfig,
    now: str,
    questions: list[AcceptanceQuestion] | None = None,
) -> DocReport:
    """Run every check on one document and build its report and review queue.

    Never raises for a bad document: unreadable files become notes, a source
    that cannot be read leaves the reference empty, and a crashing check
    becomes a failed check. A document that was not converted gets overall
    not_converted and no checks (deviation V-p).
    """
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
    md_path = folder / md_filename(entry.id)  # last part of the id (NL1)
    conversion: dict[str, Any] = {}
    try:
        conversion = json.loads((folder / "conversion.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        report.notes.append(f"conversion.json not readable: {exc}")

    # md, fm and fm_error stay None when the Markdown or its front matter cannot be
    # read; the checks handle that (V7 reports the front matter error).
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

            # Bad YAML can still be split off, so the body can still be checked.
            try:
                body = split_front_matter(full)[1]
            except FrontMatterError:
                body = full
        # Both texts: the full file for line numbers, the body for the content.
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
        questions=list(questions or []),
    )
    results: dict[str, CheckResult] = {}
    for check in CHECKS:
        try:
            result = check(doc, cfg)
        except Exception as exc:  # a crashing check is a failed check, reported, not hidden
            log.exception("%s: %s crashed", entry.id, check.__name__)
            check_id = check.__name__.removeprefix("check_").upper()  # check_v3 -> V3
            result = CheckResult(
                check_id,
                check.__name__,
                FAIL,
                f"check crashed: {type(exc).__name__}: {exc}",
                [Finding(FAIL, f"internal error: {type(exc).__name__}: {exc}")],
            )
        results[result.id] = result
    report.checks = [results[c] for c in CHECK_ORDER]
    # A missing or unreadable Markdown file is a V7 failure even if V7 did not
    # report it, so the document can never pass.
    if md is None:
        report.checks[CHECK_ORDER.index("V7")].findings.insert(
            0, Finding(FAIL, f"{md_path.name} not readable")
        )
        report.checks[CHECK_ORDER.index("V7")].status = FAIL
    report.overall = overall([c.status for c in report.checks])
    report.review_queue = build_queue(entry.id, report.checks, md, reference, cfg)
    # Recorded so specval approve can refuse a file edited after this report.
    if full is not None:
        report.markdown_sha256 = hashlib.sha256(md_path.read_bytes()).hexdigest()
        report.markdown_body_sha256 = body_sha256(full)
    report.duration_s = round(time.monotonic() - start, 3)
    return report
