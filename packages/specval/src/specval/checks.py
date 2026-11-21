"""Checks V1, V2, V4 and V7 (design: Checks; build phase 5).

Every check gets a Document (Markdown, reference data, front matter, files)
and returns a CheckResult. Anchors added by the converter (``<a id>``,
``<span id>``) are raw HTML, so they are already outside the heading and text
the checks compare.
"""

from __future__ import annotations

import difflib
import hashlib
import html
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from common.frontmatter import FrontMatter
from common.manifest import ManifestEntry

from specval.config import AcceptanceQuestion, SpecvalConfig
from specval.markdown import MarkdownDoc
from specval.model import FAIL, NOT_APPLICABLE, PASS, WARN, CheckResult, Finding, status_from
from specval.numbering import label, section_number, sequence_problems
from specval.reference import Reference
from specval.text import CODE, TABLE, TEXT, Segment, normalize
from specval.tokens import Family, families

MAX_LISTED = 10  # findings listed per difference before "… and N more"


@dataclass
class Document:
    """One converted document and everything the checks read about it.

    conversion is the document's manifest record (options, own URLs);
    source_path is its saved source.* file, None when missing; questions are
    all acceptance questions (V9 picks this document's).
    """

    id: str
    folder: Path
    entry: ManifestEntry
    md_path: Path
    md: MarkdownDoc | None  # None when the file could not be read
    front_matter: FrontMatter | None
    front_matter_error: str | None
    reference: Reference | None
    conversion: dict[str, Any] = field(default_factory=dict)
    source_path: Path | None = None
    questions: list[AcceptanceQuestion] = field(default_factory=list)

    @property
    def from_docling(self) -> bool:
        """True when Docling (the PDF converter) made the Markdown. Docling
        escapes &, < and > inside code, which V3, V4, V6 and V8 undo."""
        return self.entry.converter == "docling"


# --------------------------------------------------------------------- V1


def _align(
    source: Sequence[tuple[str, str]],
    markdown: Sequence[tuple[str, int]],
    what: str,
) -> list[Finding]:
    """Findings for items missing from or extra in the Markdown, by aligning
    the two sequences of keys. source: (key, location); markdown: (key, line).

    All findings fail. A changed item ("replace") is reported twice: once as
    missing, once as extra.
    """
    matcher = difflib.SequenceMatcher(
        a=[k for k, _ in source], b=[k for k, _ in markdown], autojunk=False
    )
    findings: list[Finding] = []
    for op, a0, a1, b0, b1 in matcher.get_opcodes():
        if op in ("delete", "replace"):
            for key, loc in source[a0:a1]:
                findings.append(
                    Finding(
                        FAIL,
                        f"{what} in the source, not in the Markdown: {key!r}",
                        source_loc=loc,
                        excerpt=key,
                    )
                )
        if op in ("insert", "replace"):
            for key, line in markdown[b0:b1]:
                findings.append(
                    Finding(
                        FAIL,
                        f"{what} in the Markdown, not in the source: {key!r}",
                        md_line=line,
                        excerpt=key,
                    )
                )
    return findings


def _limit(findings: list[Finding], what: str) -> list[Finding]:
    """The first MAX_LISTED findings plus one "… and N more" finding, which
    takes the first finding's severity."""
    if len(findings) <= MAX_LISTED:
        return findings
    rest = len(findings) - MAX_LISTED
    return findings[:MAX_LISTED] + [
        Finding(findings[0].severity, f"… and {rest} more {what} difference(s)")
    ]


def check_v1(doc: Document, cfg: SpecvalConfig) -> CheckResult:
    """V1 structure counts: headings, tables, code blocks and images.

    Fail when a count differs from the source; the findings then name the
    items that differ. A count the source cannot give (None, e.g. PDF
    headings) is shown but not compared. PDF tables come from pdfplumber and
    are approximate: a difference up to pdf_table_count_tolerance warns,
    more fails (design V1; deviations FS-1). Not applicable without a source
    reference or readable Markdown.
    """
    ref, md = doc.reference, doc.md
    result = CheckResult("V1", "Structure counts", PASS, "")
    if ref is None or md is None:
        result.status, result.summary = NOT_APPLICABLE, "no source reference available"
        return result
    counts: dict[str, dict[str, int | None]] = {}
    findings: list[Finding] = []

    def compare(what: str, source_n: int | None, md_n: int, details: Callable[[], list[Finding]]):
        """Record both counts; when they differ, add a fail and the details.
        details is a callable so the alignment only runs when needed."""
        counts[what] = {"source": source_n, "markdown": md_n}
        if source_n is None or source_n == md_n:
            return
        findings.append(Finding(FAIL, f"{what}: source {source_n}, Markdown {md_n}"))
        findings.extend(_limit(details(), what))

    if ref.headings is not None:
        compare(
            "headings",
            len(ref.headings),
            len(md.headings),
            lambda: _align(
                [(h.text, h.loc) for h in ref.headings],
                [(h.text, h.line) for h in md.headings],
                "heading",
            ),
        )
    if ref.tables_approximate:
        n_src, n_md = len(ref.tables), len(md.tables)
        counts["tables"] = {"source": n_src, "markdown": n_md}
        if n_src != n_md:
            severity = FAIL if abs(n_src - n_md) > cfg.pdf_table_count_tolerance else WARN
            findings.append(
                Finding(
                    severity,
                    f"tables: pdfplumber finds {n_src}, Markdown has {n_md} (approximate for "
                    f"PDFs; fail when they differ by more than {cfg.pdf_table_count_tolerance})",
                )
            )
    else:
        compare(
            "tables",
            len(ref.tables),
            len(md.tables),
            lambda: _align(
                [(_row_key(t.rows), t.loc) for t in ref.tables],
                [(_row_key(t.rows), t.line) for t in md.tables],
                "table (first row)",
            ),
        )
    if ref.code_blocks is not None:
        compare(
            "code blocks",
            len(ref.code_blocks),
            len(md.code_blocks),
            lambda: _align(
                [(_code_key(c.text), c.loc) for c in ref.code_blocks],
                [(_code_key(c.text), c.line) for c in md.code_blocks],
                "code block",
            ),
        )
    if ref.images is not None:
        compare("images", len(ref.images), len(md.images), lambda: [])
    result.findings = findings
    result.details = {"counts": counts}
    result.status = status_from(findings)
    differing = [
        k for k, v in counts.items() if v["source"] is not None and v["source"] != v["markdown"]
    ]
    result.summary = (
        "counts match: " + ", ".join(f"{k} {v['markdown']}" for k, v in counts.items())
        if not differing
        else "counts differ: " + ", ".join(differing)
    )
    return result


def _row_key(rows: tuple[tuple[str, ...], ...]) -> str:
    """A table's first row, cut to 80 characters: its key for alignment."""
    return " | ".join(rows[0])[:80] if rows else ""


def _code_key(text: str) -> str:
    """A code block's first 80 normalized characters: its key for alignment."""
    return normalize(text)[:80]


# --------------------------------------------------------------------- V2


def check_v2(doc: Document, cfg: SpecvalConfig) -> CheckResult:
    """V2 section numbering: numbered headings in the right order.

    When the source has headings (HTML, DOCX), a numbered heading missing
    from or extra in the Markdown fails. A number that is skipped,
    duplicated or out of order in the Markdown fails, but only warns when
    the source has the same problem. PDF has no source headings, so only the
    Markdown order is checked (deviations V-e). Not applicable when no
    heading is numbered.
    """
    md = doc.md
    result = CheckResult("V2", "Section numbering", PASS, "")
    if md is None:
        result.status, result.summary = NOT_APPLICABLE, "Markdown not readable"
        return result
    md_numbers = [(n, h.line) for h in md.headings if (n := section_number(h.text))]
    ref = doc.reference
    src_numbers = (
        [(n, h.loc) for h in ref.headings if (n := section_number(h.text))]
        if ref is not None and ref.headings is not None
        else None
    )
    if not md_numbers and not src_numbers:
        result.status, result.summary = NOT_APPLICABLE, "no numbered headings"
        return result
    findings: list[Finding] = []
    if src_numbers is not None:
        findings += _limit(
            _align(
                [(label(n), loc) for n, loc in src_numbers],
                [(label(n), line) for n, line in md_numbers],
                "numbered heading",
            ),
            "numbered heading",
        )
    # Sequence problems the source itself has: a faithful copy only warns.
    in_source = (
        {(p.kind, p.number) for p in sequence_problems([n for n, _ in src_numbers])}
        if src_numbers is not None
        else set()
    )
    for problem in sequence_problems([n for n, _ in md_numbers]):
        line = md_numbers[problem.index][1]
        same_in_source = (problem.kind, problem.number) in in_source
        findings.append(
            Finding(
                WARN if same_in_source else FAIL,
                f"section {label(problem.number)} {problem.kind} "
                f"(after {label(problem.previous)})"
                + (" - the source numbers it the same way" if same_in_source else ""),
                md_line=line,
                excerpt=md.excerpt(line),
            )
        )
    result.findings = findings
    result.status = status_from(findings)
    result.details = {
        "markdown_numbered": len(md_numbers),
        "source_numbered": len(src_numbers) if src_numbers is not None else None,
    }
    basis = "Markdown order only (PDF)" if src_numbers is None else "source and Markdown"
    result.summary = (
        f"{len(md_numbers)} numbered headings in order ({basis})"
        if not findings
        else f"{sum(f.severity == FAIL for f in findings)} problem(s) ({basis})"
    )
    return result


# --------------------------------------------------------------------- V4


def _tokens(
    family: Family, segments: list[Segment], decode: bool
) -> tuple[Counter[str], dict[str, Segment]]:
    """Count a family's tokens in the segments, and remember the first
    segment of each token for the finding's context. decode: undo HTML
    escaping in code segments (Docling output)."""
    counts: Counter[str] = Counter()
    first: dict[str, Segment] = {}
    for segment in segments:
        if family.scope is not None and segment.ctx != family.scope:
            continue
        text = segment.text
        if decode and segment.ctx == CODE:
            text = html.unescape(text)
        for token in family.find(normalize(text)):
            counts[token] += 1
            first.setdefault(token, segment)
    return counts, first


def _context(text: str, token: str, width: int = 70) -> str:
    """Up to width characters each side of the token's first occurrence,
    with "…" where text was cut. The start of the text when not found."""
    flat = normalize(text)
    i = flat.lower().find(token.lower())
    if i < 0:
        return flat[: 2 * width]
    start, end = max(0, i - width), min(len(flat), i + len(token) + width)
    return ("…" if start else "") + flat[start:end] + ("…" if end < len(flat) else "")


def check_v4(doc: Document, cfg: SpecvalConfig) -> CheckResult:
    """V4 exact values: protected tokens (hex, timecodes, numbers with units,
    normative keywords, XML names) counted as multisets.

    Fail when any token occurs a different number of times in the source and
    the Markdown; the 10 tokens with the largest differences are listed per
    family. There is no warn. Not applicable without a source reference or
    readable Markdown.
    """
    ref, md = doc.reference, doc.md
    result = CheckResult("V4", "Exact values", PASS, "")
    if ref is None or md is None:
        result.status, result.summary = NOT_APPLICABLE, "no source reference available"
        return result
    findings: list[Finding] = []
    details: dict[str, dict[str, int | None]] = {}
    md_segments = md.segments
    pdf = ref.kind == "pdf"
    if pdf:
        # pypdf runs a table row's cells together ("25 % %"); compare Markdown table
        # rows the same way.
        md_segments = [s for s in md.segments if s.ctx != TABLE] + [
            Segment(" ".join(row), TEXT, table.line) for table in md.tables for row in table.rows
        ]
    for family in families(cfg.token_families):
        if pdf and family.scope is not None:
            # A PDF has no reliable table text on the source side; the table-only
            # family (4-digit byte pairs) would compare phone numbers and years
            # (deviations V-h). V5 compares PDF table cells instead.
            details[family.name] = {"source": None, "markdown": None}
            continue
        src, src_first = _tokens(family, ref.segments, decode=False)
        # Docling escapes &, < and > inside code (deviations: known Docling limitations).
        out, md_first = _tokens(family, md_segments, decode=doc.from_docling)
        details[family.name] = {"source": sum(src.values()), "markdown": sum(out.values())}
        diffs = sorted(
            (t for t in set(src) | set(out) if src[t] != out[t]),
            key=lambda t: (-abs(src[t] - out[t]), t),
        )
        if not diffs:
            continue
        findings.append(
            Finding(
                FAIL,
                f"{family.name}: source {sum(src.values())}, Markdown {sum(out.values())}; "
                f"{len(diffs)} token(s) differ",
            )
        )
        for token in diffs[:MAX_LISTED]:
            more_in_source = src[token] > out[token]
            seg = (src_first if more_in_source else md_first)[token]
            findings.append(
                Finding(
                    FAIL,
                    f"{family.name} {token!r}: source {src[token]}, Markdown {out[token]}",
                    md_line=None if more_in_source else seg.md_line,
                    source_loc=seg.source_loc if more_in_source else None,
                    excerpt=_context(seg.text, token),
                )
            )
        if len(diffs) > MAX_LISTED:
            findings.append(
                Finding(FAIL, f"… and {len(diffs) - MAX_LISTED} more {family.name} token(s)")
            )
    result.findings = findings
    result.details = {"families": details}
    result.status = status_from(findings)
    total = sum(v["source"] or 0 for v in details.values())
    result.summary = (
        f"all {total} protected tokens match"
        if not findings
        else "differs: "
        + ", ".join(
            k for k, v in details.items() if any(f.message.startswith(k + ":") for f in findings)
        )
    )
    return result


# --------------------------------------------------------------------- V7

LINT_DISABLED = (
    # pymarkdown rule ids (deviations V-n). Style rules the converters' output
    # breaks by design (raw HTML anchors and tables, long lines, several
    # top-level headings, Docling's flat ##). MD051 is off because it does not
    # know <a id> anchors; V7 checks "#…" links itself.
    "md001", "md003", "md004", "md007", "md009", "md010", "md012", "md013", "md022",
    "md024", "md025", "md026", "md028", "md029", "md030", "md031", "md032", "md033",
    "md034", "md036", "md040", "md041", "md046", "md051", "md047", "md049", "md050", "md055",
    "md056", "md058", "md060",
)  # fmt: skip
LINT_MAX_LINES = 10000  # pymarkdown time grows ~quadratically (4,350 lines: 27 s)


def _url_key(url: str) -> tuple[str, str, str]:
    """(host, path, query) to tell whether a link points at the document's own
    page. Scheme, default ports and fragment are ignored; ".../index.html"
    equals ".../"."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.port and parts.port not in (80, 443):
        host = f"{host}:{parts.port}"
    path = parts.path or "/"
    for index in ("index.html", "index.htm"):
        if path.endswith("/" + index):
            path = path[: -len(index)]
    return host, path, parts.query


def _local_target(href: str) -> str | None:
    """A relative file path the Markdown links to, or None for web, mail, '#' links."""
    if not href or href.startswith(("#", "//")):
        return None
    parts = urlsplit(href)
    if parts.scheme:
        return None if parts.scheme != "file" else unquote(parts.path)
    return unquote(parts.path) or None


def check_v7(doc: Document, cfg: SpecvalConfig) -> CheckResult:
    """V7 Markdown hygiene: front matter, links, media, heading levels, lint.

    Fail: invalid front matter, a wrong id or source_sha256 (deviations V-m),
    no source file, a "#…" link to an anchor that does not exist (deviations
    V-k), a missing local image or media/ file (deviations V-l). Warn: a link
    to the document's own URL not rewritten to "#…", an image still on the
    web, a heading level jump, any lint issue (deviations V-n). Unreadable
    Markdown fails.
    """
    result = CheckResult("V7", "Markdown hygiene", PASS, "")
    findings: list[Finding] = []
    md = doc.md

    # front matter
    fm = doc.front_matter
    if fm is None:
        findings.append(Finding(FAIL, f"front matter invalid: {doc.front_matter_error}", md_line=1))
    else:
        if fm.id != doc.id:
            findings.append(
                Finding(FAIL, f"front matter id {fm.id!r} is not {doc.id!r}", md_line=2)
            )
        if doc.source_path is None:
            findings.append(Finding(FAIL, "no source.* file in the document folder"))
        elif hashlib.sha256(doc.source_path.read_bytes()).hexdigest() != fm.source_sha256:
            findings.append(
                Finding(
                    FAIL,
                    f"source_sha256 in the front matter does not match {doc.source_path.name}",
                )
            )
    if md is None:
        result.findings, result.status = findings, status_from(findings) if findings else FAIL
        result.summary = "Markdown not readable"
        return result

    # internal anchors and links back to the document itself
    targets = set(md.ids) | md.heading_slugs()
    own = [u["url"] for u in (doc.conversion.get("html") or {}).get("own_urls", [])]
    if fm is not None and fm.source_url:
        own.append(fm.source_url)
    own_keys = {_url_key(u) for u in own}
    broken: list[Finding] = []
    self_links: list[Finding] = []
    for link in md.links:
        href = link.href.strip()
        if href.startswith("#"):
            target = unquote(href[1:])
            # Accept the id either percent-decoded or as written. A bare "#" is fine.
            if target and target not in targets and href[1:] not in targets:
                broken.append(
                    Finding(
                        FAIL,
                        f"link to #{target}: no such anchor in the document",
                        md_line=link.line,
                        excerpt=md.excerpt(link.line),
                    )
                )
        # Only web links with a fragment: the converter rewrites those to "#…".
        elif own_keys and urlsplit(href).fragment and urlsplit(href).scheme in ("http", "https"):
            if _url_key(href) in own_keys:
                self_links.append(
                    Finding(
                        WARN,
                        f"link to the document's own URL not rewritten to '#…': {href}",
                        md_line=link.line,
                    )
                )
    findings += _limit(broken, "broken anchor")
    findings += _limit(self_links, "self-link")

    # media
    missing: list[Finding] = []
    remote: list[Finding] = []
    root = doc.folder.resolve()
    for image in md.images:
        src = image.src.strip()
        parts = urlsplit(src)
        if parts.scheme in ("http", "https") or src.startswith("//"):
            remote.append(Finding(WARN, f"image not stored locally: {src}", md_line=image.line))
            continue
        if parts.scheme == "data":  # embedded image (e.g. an inline <svg>)
            continue
        local = _local_target(src)
        if local is None:
            continue
        # A path that leaves the document folder ("../x.png") counts as missing.
        path = (doc.folder / local).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            missing.append(
                Finding(
                    FAIL,
                    f"image file missing: {src}",
                    md_line=image.line,
                    excerpt=md.excerpt(image.line),
                )
            )
    for link in md.links:
        local = _local_target(link.href.strip())
        if local is not None and local.startswith("media/"):
            if not (doc.folder / local).is_file():
                missing.append(
                    Finding(FAIL, f"linked media file missing: {link.href}", md_line=link.line)
                )
    findings += _limit(missing, "missing media")
    findings += _limit(remote, "web image")

    # heading levels
    jumps = []
    for prev, cur in zip(md.headings, md.headings[1:], strict=False):
        if cur.level > prev.level + 1:
            jumps.append(
                Finding(
                    WARN,
                    f"heading level jumps from {prev.level} to {cur.level}",
                    md_line=cur.line,
                    excerpt=md.excerpt(cur.line),
                )
            )
    findings += _limit(jumps, "heading level")

    # lint (warn only)
    lint_note = ""
    if len(md.lines) > LINT_MAX_LINES:
        lint_note = f"; lint skipped ({len(md.lines)} lines, limit {LINT_MAX_LINES})"
    else:
        lint = _lint("\n".join(md.lines))
        findings += _limit(lint, "lint")
    result.findings = findings
    result.status = status_from(findings)
    result.details = {
        "links": len(md.links),
        "anchors": len(targets),
        "images": len(md.images),
        "lint_skipped": bool(lint_note),
    }
    result.summary = (
        f"{len(md.links)} links, {len(md.images)} images checked{lint_note}"
        if not findings
        else f"{sum(f.severity == FAIL for f in findings)} fail, "
        f"{sum(f.severity == WARN for f in findings)} warn{lint_note}"
    )
    return result


def _lint(text: str) -> list[Finding]:
    """pymarkdown findings for the Markdown, all as warnings, with the rules
    in LINT_DISABLED off. If pymarkdown itself fails, one warning says so."""
    from pymarkdown.api import PyMarkdownApi, PyMarkdownApiException

    api = PyMarkdownApi()
    for rule in LINT_DISABLED:
        api.disable_rule_by_identifier(rule)
    try:
        scan = api.scan_string(text)
    except PyMarkdownApiException as exc:
        return [Finding(WARN, f"lint could not run: {exc}")]
    return [
        Finding(
            WARN,
            f"lint {f.rule_id} ({f.rule_name}): {f.rule_description}"
            + (f" [{f.extra_error_information}]" if f.extra_error_information else ""),
            md_line=f.line_number,
        )
        for f in scan.scan_failures
    ]


CHECKS = (check_v1, check_v2, check_v4, check_v7)
