"""Checks V3, V5, V6, V8 and V9 (design: Checks; build phase 6)."""

from __future__ import annotations

import difflib
import html
from collections import Counter

from rapidfuzz import fuzz

from specval.checks import Document
from specval.codecheck import (
    code_differs,
    is_webvtt_block,
    is_xml_block,
    problem_key,
    webvtt_problems,
    xml_problems,
)
from specval.config import SpecvalConfig
from specval.confusion import confusable_tokens
from specval.model import (
    FAIL,
    INFO,
    NOT_APPLICABLE,
    PASS,
    WARN,
    CheckResult,
    Finding,
    status_from,
)
from specval.tablecheck import compare_grids, describe
from specval.text import CODE, TABLE, Segment, normalize
from specval.textcheck import coverage, join_pdf_hyphenation, normalize_for_v3

MAX_MISSING_LISTED = 50  # V3 missing sentences and V8 hits listed (deviations V-u)

# --------------------------------------------------------------------- V3


def check_v3(doc: Document, cfg: SpecvalConfig) -> CheckResult:
    """V3 text completeness: share of source sentences found in the Markdown.

    Fail when coverage is below text_coverage.fail_below (default 98%), warn
    below warn_below (default 99.5%), else pass. Missing sentences (up to 50)
    are listed with that severity, or as info when the check passes
    (deviations V-u). A PDF page with characters in a font without a Unicode
    map adds a warning to check it by eye; its lines with them are not
    counted (V-ac). Code is left out (V6 compares it). Not applicable
    without a source reference or readable Markdown.
    """
    ref, md = doc.reference, doc.md
    result = CheckResult("V3", "Text completeness", PASS, "")
    if ref is None or md is None:
        result.status, result.summary = NOT_APPLICABLE, "no source reference available"
        return result
    pdf = ref.kind == "pdf"
    # A .doc brings its own text, read with catdoc, not from LibreOffice's .docx
    # (DOC1); it includes code, which is fine: the Markdown text below does too.
    # A PDF with unmapped-font characters brings its text without those lines (V-ac).
    segments = ref.text_segments if ref.text_segments is not None else ref.segments
    source = [
        (join_pdf_hyphenation(s.text) if pdf else s.text, s.source_loc)
        for s in segments
        if s.ctx != CODE  # code blocks are compared by V6
    ]
    # All Markdown text, code included: a source sentence may sit in a code
    # block in the Markdown (presence, not position, is checked).
    markdown = " ".join(
        html.unescape(s.text) if doc.from_docling and s.ctx == CODE else s.text for s in md.segments
    )
    tc = cfg.text_coverage
    cov = coverage(source, markdown, tc, by_line=pdf)  # by_line: deviations V-t
    if cov.ratio < tc.fail_below:
        result.status = FAIL
    elif cov.ratio < tc.warn_below:
        result.status = WARN
    severity = result.status if result.status != PASS else INFO
    # Pages whose lines with font-without-Unicode characters were left out of
    # the source text (V-ac): nothing to compare, so a person must look.
    for page in ref.unreadable:
        result.findings.append(
            Finding(
                WARN,
                f"page {page.page}: {page.chars} character(s) drawn with a font that has "
                f"no Unicode mapping ({', '.join(page.fonts)}); the PDF cannot say which "
                f"characters they are, so {page.lines_left_out} line(s) were left out of "
                "this check. Compare the page with the Markdown by eye",
                source_loc=f"page {page.page}",
            )
        )
    if ref.unreadable and result.status == PASS:
        result.status = WARN
    for sentence, loc in cov.missing[:MAX_MISSING_LISTED]:
        result.findings.append(
            Finding(severity, "missing sentence", source_loc=loc, excerpt=sentence)
        )
    if len(cov.missing) > MAX_MISSING_LISTED:
        result.findings.append(
            Finding(
                severity, f"… and {len(cov.missing) - MAX_MISSING_LISTED} more missing sentence(s)"
            )
        )
    result.details = {
        "coverage": round(cov.ratio, 4),
        "sentences": cov.total,
        "present": cov.present,
        "near": cov.near,
        "missing": len(cov.missing),
        "fail_below": tc.fail_below,
        "warn_below": tc.warn_below,
    }
    if ref.text_from is not None:
        result.details["text_from"] = ref.text_from
    if ref.unreadable:
        result.details["unreadable_pages"] = [p.page for p in ref.unreadable]
        result.details["unreadable_lines_left_out"] = sum(p.lines_left_out for p in ref.unreadable)
    result.summary = (
        f"{cov.ratio:.1%} of {cov.total} sentences found "
        f"({cov.present} exact, {cov.near} near, {len(cov.missing)} missing)"
    )
    return result


# --------------------------------------------------------------------- V5

MAX_TABLE_FINDINGS = 10  # per table (PDF: 3x this for cells not in the text)


def _pair_by_header(src_keys: list[str], md_keys: list[str]) -> list[tuple[int, int]]:
    """Pairs (source index, Markdown index): by order when the counts match,
    otherwise by aligning header rows.

    Keys must match exactly to align, but a "replace" run is paired item by
    item too, so a table with a changed header row is still compared. Also
    used by V6 with code block keys.
    """
    if len(src_keys) == len(md_keys):
        return list(zip(range(len(src_keys)), range(len(md_keys)), strict=True))
    matcher = difflib.SequenceMatcher(a=src_keys, b=md_keys, autojunk=False)
    pairs = []
    for op, a0, a1, b0, b1 in matcher.get_opcodes():
        if op in ("equal", "replace"):
            pairs += list(zip(range(a0, a1), range(b0, b1), strict=False))
    return pairs


def _first_row(grid) -> str:
    """The first row with any text, as a key for pairing tables."""
    for row in grid:
        if any(row):
            return " | ".join(row)
    return ""


def check_v5(doc: Document, cfg: SpecvalConfig) -> CheckResult:
    """V5 table content: tables compared cell by cell.

    HTML and DOCX: tables paired by order (or by header row when the counts
    differ) and compared as grids (tablecheck.compare_grids; deviations V-v,
    HP2). Any difference fails; there is no warn. Source tables left
    unpaired are only counted here, since V1 reports them. PDF: see
    _check_v5_pdf. Not applicable without a source reference, readable
    Markdown, or any table.
    """
    ref, md = doc.reference, doc.md
    result = CheckResult("V5", "Table content", PASS, "")
    if ref is None or md is None:
        result.status, result.summary = NOT_APPLICABLE, "no source reference available"
        return result
    if not ref.tables and not md.tables:
        result.status, result.summary = NOT_APPLICABLE, "no tables"
        return result
    if ref.kind == "pdf":
        return _check_v5_pdf(doc, cfg, result)
    pairs = _pair_by_header(
        [_first_row(t.grid) for t in ref.tables], [_first_row(t.grid) for t in md.tables]
    )
    findings: list[Finding] = []
    differing = 0
    for si, mi in pairs:
        src, out = ref.tables[si], md.tables[mi]
        problems = compare_grids([list(r) for r in src.grid], [list(r) for r in out.grid])
        if problems:
            differing += 1
        for p in problems[:MAX_TABLE_FINDINGS]:
            # p.col is 1-based; line_of wants a 0-based column.
            col = p.col - 1 if p.col is not None else None
            line = out.line_of(p.md_row, col)
            findings.append(
                Finding(
                    FAIL,
                    describe(si + 1, p),
                    md_line=line,
                    source_loc=src.loc,
                    excerpt=md.excerpt(line),
                )
            )
        if len(problems) > MAX_TABLE_FINDINGS:
            findings.append(
                Finding(
                    FAIL,
                    f"table {si + 1}: … and {len(problems) - MAX_TABLE_FINDINGS} "
                    "more difference(s)",
                    md_line=out.line,
                    source_loc=src.loc,
                )
            )
    unpaired = len(ref.tables) - len(pairs)
    result.findings = findings
    result.status = FAIL if findings else PASS
    result.details = {
        "tables_compared": len(pairs),
        "tables_differing": differing,
        "source_tables_not_paired": unpaired,
    }
    result.summary = (
        f"{len(pairs)} table(s) compared cell by cell, all match"
        if not findings
        else f"{differing} of {len(pairs)} table(s) differ"
    ) + (f"; {unpaired} source table(s) not paired (see V1)" if unpaired else "")
    return result


def _squash(text: str) -> str:
    """V3's normalization with all whitespace removed."""
    return "".join(normalize_for_v3(text).split())


def _shape(grid) -> str:
    """'R rows x C columns' (C: the longest row), for a finding."""
    cols = max((len(r) for r in grid), default=0)
    return f"{len(grid)} rows x {cols} columns"


def pdf_cells_agree(plumber: str, markdown: str) -> bool:
    """Same text after V3's normalization (quotes, dashes) and without spaces:
    pdfplumber keeps curly quotes and sometimes drops or adds a space."""
    return _squash(plumber) == _squash(markdown)


def _check_v5_pdf(doc: Document, cfg: SpecvalConfig, result: CheckResult) -> CheckResult:
    """V5 for PDFs (design; deviations V-w): pair each Docling table with
    pdfplumber's by header similarity and report cells where they disagree
    (warn: two extractors, the second also approximate). A different table
    shape also warns. Docling tables pdfplumber did not find: every cell
    value must appear in the PDF text, otherwise fail. Cells unreadable in the
    source itself (unmapped fonts, V-ac) are only counted."""
    ref, md = doc.reference, doc.md
    assert ref is not None and md is not None
    # Deviations FS-1: pdfplumber reads boxes in diagrams as tables with only empty cells.
    plumber = [t for t in ref.tables if any(c for row in t.grid for c in row)]
    ignored = len(ref.tables) - len(plumber)
    findings: list[Finding] = []
    paired_md: set[int] = set()
    # V-ac: when the PDF has characters in fonts without a Unicode map, a
    # Markdown cell with U+FFFD (Docling's "unknown glyph") and a pdfplumber
    # cell with "(cid:N)" say the same thing: unreadable in the source. They are
    # counted, not reported; V3 asks for those pages to be checked by eye.
    # Without such fonts, U+FFFD is the converter's error and is reported.
    unreadable_source = bool(ref.unreadable)
    unreadable_cells = 0
    # Tables are in page order on both sides, so each pdfplumber table looks
    # only at the next 8 unpaired Markdown tables after the last pair, and
    # needs a header similarity of at least 80 (rapidfuzz ratio, 0-100).
    start = 0
    for t in plumber:
        key = _first_row(t.grid)
        best, best_score = None, 0.0
        for mi in range(start, min(len(md.tables), start + 8)):
            if mi in paired_md:
                continue
            score = fuzz.ratio(key, _first_row(md.tables[mi].grid))
            if score > best_score:
                best, best_score = mi, score
        if best is None or best_score < 80:
            continue
        paired_md.add(best)
        start = best + 1
        out = md.tables[best]
        src_grid = [list(r) for r in t.grid]
        md_grid = [list(r) for r in out.grid]
        if len(src_grid) != len(md_grid) or any(
            len(a) != len(b) for a, b in zip(src_grid, md_grid, strict=False)
        ):
            findings.append(
                Finding(
                    WARN,
                    f"table at Markdown line {out.line}: pdfplumber reads it with a "
                    f"different shape ({_shape(src_grid)} vs {_shape(md_grid)} in the Markdown)",
                    md_line=out.line,
                    source_loc=t.loc,
                )
            )
            continue
        disagreements = []
        for r, (a, b) in enumerate(zip(src_grid, md_grid, strict=True)):
            for c, (s, m) in enumerate(zip(a, b, strict=True)):
                # pdfplumber leaves the positions a merged cell covers empty
                if unreadable_source and ("(cid:" in s or "\ufffd" in m):
                    unreadable_cells += 1
                    continue
                if s and not pdf_cells_agree(s, m):
                    disagreements.append((r, c, s, m))
        for r, c, s, m in disagreements[:MAX_TABLE_FINDINGS]:
            line = out.row_lines[r] if r < len(out.row_lines) else out.line
            findings.append(
                Finding(
                    WARN,
                    f"row {r + 1}, column {c + 1}: pdfplumber {normalize(s)!r}, "
                    f"Markdown {normalize(m)!r}",
                    md_line=line,
                    source_loc=t.loc,
                )
            )
    # The whole PDF text without spaces: pypdf and Docling space words differently.
    text = "".join(_squash(s.text) for s in ref.segments)
    not_found = 0
    for mi, out in enumerate(md.tables):
        if mi in paired_md:
            continue
        for r, row in enumerate(out.grid):
            for cell in row:
                if unreadable_source and "\ufffd" in cell:
                    unreadable_cells += 1
                    continue
                if cell and _squash(cell) not in text:
                    not_found += 1
                    # All are counted; only the first 30 are listed.
                    if not_found <= MAX_TABLE_FINDINGS * 3:
                        line = out.row_lines[r] if r < len(out.row_lines) else out.line
                        findings.append(
                            Finding(
                                FAIL,
                                f"cell {cell!r} not found in the PDF text",
                                md_line=line,
                                excerpt=md.excerpt(line),
                            )
                        )
    result.findings = findings
    result.status = status_from(findings)
    result.details = {
        "tables_compared": len(paired_md),
        "pdfplumber_tables": len(ref.tables),
        "pdfplumber_empty_ignored": ignored,
        "cells_not_found": not_found,
        "cells_unreadable": unreadable_cells,
    }
    result.summary = (
        f"{len(paired_md)} table(s) compared with pdfplumber; "
        f"{len(md.tables) - len(paired_md)} checked against the PDF text"
        + (f"; {not_found} cell(s) not found" if not_found else "")
    )
    return result


# --------------------------------------------------------------------- V6


def check_v6(doc: Document, cfg: SpecvalConfig) -> CheckResult:
    """V6 code blocks: text against the source, XML and WebVTT syntax.

    HTML and DOCX: code blocks are paired with the source's (by order, or by
    their first 80 characters when the counts differ) and any line that
    differs after normalizing line endings and trailing spaces fails. Every
    Markdown block that looks like XML must be well-formed, and every WebVTT
    block must have valid timing lines; such an error fails, but is info when
    the paired source block has the same error (deviations V-x). PDF has no
    source code blocks, so only the syntax is checked. Not applicable when
    there are no code blocks.
    """
    ref, md = doc.reference, doc.md
    result = CheckResult("V6", "Code blocks", PASS, "")
    if md is None:
        result.status, result.summary = NOT_APPLICABLE, "Markdown not readable"
        return result
    source_blocks = ref.code_blocks if ref is not None and ref.code_blocks is not None else None
    if not md.code_blocks and not source_blocks:
        result.status, result.summary = NOT_APPLICABLE, "no code blocks"
        return result
    pairs = (
        _pair_by_header(
            [normalize(c.text)[:80] for c in source_blocks],
            [normalize(c.text)[:80] for c in md.code_blocks],
        )
        if source_blocks
        else []
    )
    source_of = {mi: si for si, mi in pairs}  # Markdown block -> its source block
    findings: list[Finding] = []
    for si, mi in pairs:
        src, out = source_blocks[si], md.code_blocks[mi]
        diff = code_differs(src.text, out.text)
        if diff is not None:
            k, a, b = diff
            line = out.first_line + k - 1  # k is the 1-based line in the block
            findings.append(
                Finding(
                    FAIL,
                    f"code block {si + 1} differs from the source at its line {k}: "
                    f"source {a!r}, Markdown {b!r}",
                    md_line=line,
                    source_loc=src.loc,
                    excerpt=md.excerpt(line),
                )
            )
    xml_blocks = webvtt_blocks = 0
    for mi, out in enumerate(md.code_blocks):
        text = html.unescape(out.text) if doc.from_docling else out.text
        source_text = source_blocks[source_of[mi]].text if mi in source_of else None
        checks = []
        if is_xml_block(text, out.info):
            xml_blocks += 1
            checks.append(("XML not well-formed", xml_problems))
        elif is_webvtt_block(text):
            webvtt_blocks += 1
            checks.append(("WebVTT", webvtt_problems))
        for label, run in checks:
            in_source = (
                {problem_key(m) for _, m in run(source_text)} if source_text is not None else set()
            )
            for k, message in run(text):
                line = out.first_line + k - 1
                same = problem_key(message) in in_source
                findings.append(
                    Finding(
                        INFO if same else FAIL,
                        f"{label}: {message}"
                        + (" (the source has the same problem)" if same else ""),
                        md_line=line,
                        excerpt=md.excerpt(line),
                    )
                )
    result.findings = findings
    result.status = status_from(findings)
    result.details = {
        "compared": len(pairs),
        "markdown_blocks": len(md.code_blocks),
        "xml_blocks": xml_blocks,
        "webvtt_blocks": webvtt_blocks,
    }
    basis = (
        f"{len(pairs)} compared with the source"
        if source_blocks is not None
        else "syntax only (PDF)"
    )
    fails = sum(f.severity == FAIL for f in findings)
    result.summary = (
        f"{len(md.code_blocks)} code block(s): {basis}; {xml_blocks} XML, {webvtt_blocks} WebVTT"
        + (f"; {fails} problem(s)" if fails else "")
    )
    return result


# --------------------------------------------------------------------- V8


def check_v8(doc: Document, cfg: SpecvalConfig) -> CheckResult:
    """V8 character confusion: tokens like "1O" or "0x2l" with a letter that
    looks like a digit (confusion.confusable_tokens; deviations V-z).

    A hit fails in a table or code block and warns elsewhere. A token the
    source has at least as often as the Markdown is a faithful copy and is
    not reported. Up to 50 hits are listed; the status counts all of them.
    Not applicable when the Markdown is unreadable.
    """
    md = doc.md
    result = CheckResult("V8", "Character confusion", PASS, "")
    if md is None:
        result.status, result.summary = NOT_APPLICABLE, "Markdown not readable"
        return result
    in_source: Counter[str] = Counter()
    if doc.reference is not None:
        for segment in doc.reference.segments:
            in_source.update(confusable_tokens(normalize(segment.text)))
    hits: list[tuple[str, Segment]] = []
    for segment in md.segments:
        text = (
            html.unescape(segment.text)
            if doc.from_docling and segment.ctx == CODE
            else segment.text
        )
        hits += [(t, segment) for t in confusable_tokens(normalize(text))]
    counts = Counter(t for t, _ in hits)
    findings = []
    for token, segment in hits:
        if counts[token] <= in_source[token]:
            continue  # the source has it as often: faithful
        severity = FAIL if segment.ctx in (TABLE, CODE) else WARN
        findings.append(
            Finding(
                severity,
                f"{token!r} looks like a number or hex value with a letter in it (O, o, l, I, S)",
                md_line=segment.md_line,
                excerpt=md.excerpt(segment.md_line),
            )
        )
    result.findings = findings[:MAX_MISSING_LISTED]
    result.status = status_from(findings)
    result.details = {
        "hits": len(findings),
        "also_in_source": sum(min(counts[t], in_source[t]) for t in counts),
    }
    result.summary = (
        "no suspicious tokens" if not findings else f"{len(findings)} suspicious token(s)"
    )
    return result


# --------------------------------------------------------------------- V9


def check_v9(doc: Document, cfg: SpecvalConfig) -> CheckResult:
    """V9 acceptance questions from acceptance.yaml for this document.

    Each expected snippet must appear in the Markdown text (both normalized:
    NFKC, whitespace collapsed; case matters), otherwise fail. Not applicable
    when no question names this document; unreadable Markdown fails.
    """
    result = CheckResult("V9", "Acceptance questions", PASS, "")
    questions = [q for q in doc.questions if q.doc == doc.id]
    if not questions:
        result.status, result.summary = NOT_APPLICABLE, "no acceptance questions for this document"
        return result
    if doc.md is None:
        result.status, result.summary = FAIL, "Markdown not readable"
        return result
    text = normalize(" ".join(s.text for s in doc.md.segments))
    findings = [
        Finding(FAIL, f"acceptance question {q.question!r}: expected {q.expect!r} not found")
        for q in questions
        if normalize(q.expect) not in text
    ]
    result.findings = findings
    result.status = status_from(findings)
    result.details = {"questions": len(questions), "answered": len(questions) - len(findings)}
    result.summary = f"{len(questions) - len(findings)} of {len(questions)} expected answers found"
    return result
