"""Safe post-processing of converter output (design: Post-processing).

Only these steps, in this order:

1. Normalize line endings to LF and the encoding to UTF-8.
2. Remove trailing whitespace on lines outside protected regions.
3. Collapse three or more blank lines to two outside protected regions.
4. Rewrite media links so they are relative to the document folder.
5. Prepend YAML front matter (done by the caller, see specconv.frontmatter).

Protected regions are left byte-for-byte unchanged by steps 2 to 4. They are
found by a conservative line scanner: when unsure, a line is protected,
because protecting too much only skips cosmetic cleanup while protecting too
little could alter a table or code block.

Protected: fenced code (any indentation), indented code blocks, pipe tables,
and raw HTML <table> and <pre> blocks. An unclosed fence or HTML block is
protected to the end of the document.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlparse

BOM = "﻿"  # U+FEFF byte order mark; the character is invisible here

# Opening code fence at any indentation (fences inside list items are indented).
_FENCE_OPEN = re.compile(r"^[ \t]*(`{3,}|~{3,})")
# A line that starts with "|" (up to 3 spaces first): a pipe table row.
_PIPE_ROW = re.compile(r"^ {0,3}\|")
# GFM delimiter row: | --- | :---: | ---: |  (outer pipes optional, at least one '-')
_DELIMITER_ROW = re.compile(r"^ {0,3}\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")
# Indented code: 4 spaces or a tab.
_INDENTED = re.compile(r"^(?: {4}|\t)")
_HTML_BLOCKS = {
    "table": (re.compile(r"<table\b", re.I), re.compile(r"</table\s*>", re.I)),
    "pre": (re.compile(r"<pre\b", re.I), re.compile(r"</pre\s*>", re.I)),
}
# A raw HTML block starts with its tag at the beginning of a line (after
# indentation). Mentions inside prose, e.g. "the \<table\> element", do not.
_HTML_BLOCK_START = {
    "table": re.compile(r"^\s*<table\b", re.I),
    "pre": re.compile(r"^\s*<pre\b", re.I),
}
# ![alt](target "title") and [text](target); target without spaces or in <...>
_MD_LINK_TARGET = re.compile(r"(\]\()(<[^>\n]*>|[^)\s]+)")
# src="..." or href='...' in raw HTML; group 2 is the quote, group 3 the value.
_HTML_SRC = re.compile(r"""(\b(?:src|href)=)(["'])(.*?)\2""", re.I)


class PostprocessError(ValueError):
    """Converter output cannot be post-processed (e.g. not valid UTF-8)."""


@dataclass(frozen=True)
class Region:
    """A run of lines [start, end) that is either plain text or protected."""

    start: int  # first line index
    end: int  # one past the last line index
    protected: bool
    kind: str  # "text", "fence", "indented", "pipe_table", "html_table", "html_pre"


# ------------------------------------------------------------------ step 1


def normalize_text(data: bytes | str) -> str:
    """Step 1: decode UTF-8 (strictly), drop a BOM, convert CRLF and CR to LF."""
    if isinstance(data, bytes):
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise PostprocessError(f"converter output is not valid UTF-8: {exc}") from exc
    else:
        text = data
    if text.startswith(BOM):
        text = text[len(BOM) :]
    return text.replace("\r\n", "\n").replace("\r", "\n")


# ------------------------------------------------------- region scanning


def _is_blank(line: str) -> bool:
    return not line.strip()


def _scan_fence(lines: list[str], i: int, marker: str) -> int:
    """Return one past the closing fence line (or len(lines) if unclosed)."""
    char, length = marker[0], len(marker)
    # Same character, at least as many, nothing else on the line (CommonMark rule).
    close = re.compile(r"^[ \t]*" + re.escape(char) + "{" + str(length) + r",}[ \t]*$")
    for j in range(i + 1, len(lines)):
        if close.match(lines[j]):
            return j + 1
    return len(lines)


def _scan_html_block(lines: list[str], i: int, kind: str) -> int:
    """Return one past the line holding the matching closing tag (nesting aware)."""
    opener, closer = _HTML_BLOCKS[kind]
    depth = 0
    for j in range(i, len(lines)):
        # Walk opening and closing tags on this line in order.
        events = sorted(
            [(m.start(), 1) for m in opener.finditer(lines[j])]
            + [(m.start(), -1) for m in closer.finditer(lines[j])]
        )
        for _, delta in events:
            depth += delta
            if depth <= 0:
                return j + 1
    return len(lines)


def _scan_pipe_table(lines: list[str], i: int) -> int:
    """A pipe table runs to the next blank line."""
    j = i
    while j < len(lines) and not _is_blank(lines[j]):
        j += 1
    return j


def _scan_indented(lines: list[str], i: int) -> int:
    """Indented code: indented lines, including blank lines between them.

    Trailing blank lines are not part of the block, so they can still be collapsed.
    """
    j = i
    last_code = i
    while j < len(lines):
        if _INDENTED.match(lines[j]) and not _is_blank(lines[j]):
            last_code = j
        elif not _is_blank(lines[j]):
            break
        j += 1
    return last_code + 1


def split_regions(text: str) -> list[Region]:
    """Split text (LF line endings) into alternating text and protected regions."""
    lines = text.split("\n")
    regions: list[Region] = []
    text_start = 0
    i = 0

    def protect(end: int, kind: str) -> None:
        """Close the open text region at i, add a protected one up to end, continue there."""
        nonlocal text_start, i
        if text_start < i:
            regions.append(Region(text_start, i, False, "text"))
        regions.append(Region(i, end, True, kind))
        text_start = i = end

    while i < len(lines):
        line = lines[i]
        # Indented code can only start after a blank line; otherwise an indented
        # line is a paragraph continuation.
        prev_blank = i == 0 or _is_blank(lines[i - 1])
        fence = _FENCE_OPEN.match(line)
        if fence:
            protect(_scan_fence(lines, i, fence.group(1)), "fence")
        elif _HTML_BLOCK_START["table"].match(line):
            protect(_scan_html_block(lines, i, "table"), "html_table")
        elif _HTML_BLOCK_START["pre"].match(line):
            protect(_scan_html_block(lines, i, "pre"), "html_pre")
        # A pipe table either starts with "|", or is a header row followed by a
        # delimiter row. The delimiter row must hold a "|", or "---" under a line
        # with a "|" in it (a setext heading) would count.
        elif _PIPE_ROW.match(line) or (
            "|" in line
            and i + 1 < len(lines)
            and _DELIMITER_ROW.match(lines[i + 1])
            and "|" in lines[i + 1]
        ):
            protect(_scan_pipe_table(lines, i), "pipe_table")
        elif prev_blank and _INDENTED.match(line) and not _is_blank(line):
            protect(_scan_indented(lines, i), "indented")
        else:
            i += 1
    if text_start < len(lines):
        regions.append(Region(text_start, len(lines), False, "text"))
    return regions


# --------------------------------------------------------------- steps 2-4


def _clean_text_lines(lines: list[str]) -> list[str]:
    """Steps 2 and 3 on one unprotected region: strip line ends, keep at most 2 blank lines."""
    out: list[str] = []
    blank_run = 0
    for line in lines:
        line = line.rstrip()
        if line:
            blank_run = 0
        else:
            blank_run += 1
            if blank_run > 2:
                continue
        out.append(line)
    return out


def _relative_media_target(target: str, out_dir: Path) -> str | None:
    """New target if `target` is an absolute local path or file:// URL inside
    out_dir; None if it should be left alone (relative paths, web URLs, anchors)."""
    bracketed = target.startswith("<") and target.endswith(">")
    raw = target[1:-1] if bracketed else target
    if raw.startswith("file://"):
        path = Path(unquote(urlparse(raw).path))
    elif raw.startswith("/"):
        path = Path(unquote(raw))
    else:
        return None
    try:
        rel = path.resolve().relative_to(out_dir.resolve())
    except ValueError:
        return None
    new = PurePosixPath(*rel.parts).as_posix()
    # Markdown link targets cannot hold a space unless wrapped in <...>.
    return f"<{new}>" if bracketed or " " in new else new


def rewrite_media_links(line: str, out_dir: Path) -> str:
    """Step 4 on one unprotected line."""

    def md(m: re.Match[str]) -> str:
        """Replacement for a Markdown link target."""
        new = _relative_media_target(m.group(2), out_dir)
        return m.group(1) + (new if new is not None else m.group(2))

    def html(m: re.Match[str]) -> str:
        """Replacement for an HTML attribute value; <...> brackets are not used there."""
        new = _relative_media_target(m.group(3), out_dir)
        value = new.strip("<>") if new is not None else m.group(3)
        return m.group(1) + m.group(2) + value + m.group(2)

    return _HTML_SRC.sub(html, _MD_LINK_TARGET.sub(md, line))


@dataclass
class PostprocessResult:
    """Cleaned Markdown and any warnings."""

    markdown: str
    warnings: list[str]


def postprocess(data: bytes | str, out_dir: Path) -> PostprocessResult:
    """Apply steps 1 to 4. Step 5 (front matter) is added by the caller."""
    text = normalize_text(data)
    lines = text.split("\n")
    warnings: list[str] = []
    out: list[str] = []
    for region in split_regions(text):
        chunk = lines[region.start : region.end]
        if region.protected:
            # Never edit protected lines; only warn when they hold a link step 4 would fix.
            for offset, line in enumerate(chunk):
                if rewrite_media_links(line, out_dir) != line:
                    warnings.append(
                        f"line {region.start + offset + 1}: absolute media link inside a "
                        f"protected {region.kind} region left unchanged"
                    )
            out.extend(chunk)
        else:
            out.extend(rewrite_media_links(line, out_dir) for line in _clean_text_lines(chunk))
    return PostprocessResult("\n".join(out), warnings)
