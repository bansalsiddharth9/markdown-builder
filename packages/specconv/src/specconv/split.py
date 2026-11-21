"""--split LEVEL: one file per section (design: Output layout; build phase 7).

The full <id>.md is always written; this adds ``sections/``:

- A section starts at every heading of level LEVEL or higher (a "# 2" heading
  also ends a "## 1.3" section), outside code blocks, pipe tables and raw
  HTML blocks. Deeper headings stay inside their section.
- File names: numbered headings use their number, first part zero-padded
  ("7.3.2 Pop-on captions" -> ``07-3-2-pop-on-captions.md``); others use
  their position (``s04-introduction.md``). Repeated names get "-2", "-3".
- Links: a ``#id`` link to an anchor in another section points to that
  section's file (``07-3-2-pop-on-captions.md#id``); one within the section,
  or to an unknown id, is left as it is (V7 checks the full document).
  Relative paths (``media/…``) get ``../``. Links inside code blocks are
  never changed; links inside tables are (the cell text is unchanged).
- ``index.md`` has the text before the first section and a nested list of
  the sections.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from urllib.parse import unquote, urlsplit

from specconv.postprocess import split_regions

# ATX heading: "## Title" or "## Title ##"; group 1 is the #s, group 2 the text.
_HEADING = re.compile(r"^(#{1,6})[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$")
# Any HTML tag, e.g. the <a id="..."></a> anchors the anchors filter puts in headings.
_TAG = re.compile(r"<[^>]+>")
# A section number at the start of a title: "7.3.2", "A.1" (annex), or a lone "7",
# with an optional trailing dot. Parts are at most 3 digits, so years such as
# "2024 Edition" are not taken for numbers.
_NUMBER = re.compile(r"^((?:\d{1,3}|[A-Z])(?:\.\d{1,3})+|\d{1,3})\.?[ \t]+(.*)$")
# id="..." or id='...' on any HTML element in the line.
_ID = re.compile(r"""\bid\s*=\s*(?:"([^"]*)"|'([^']*)')""")
# Target of a Markdown link or image: "](target" or "](<target with spaces>".
_MD_LINK = re.compile(r"(\]\()(<[^>\n]*>|[^)\s]+)")
# href="..." or src='...' in raw HTML; group 2 is the quote, group 3 the value.
_HTML_LINK = re.compile(r"""(\b(?:href|src)\s*=\s*)(["'])(.*?)\2""")


def slug(text: str, limit: int = 60) -> str:
    """ASCII slug for a section file name, at most limit characters (deviation SP2)."""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:limit].strip("-") or "section"


# Fence line at any indentation; group 1 is the fence, group 2 the info string.
_FENCE = re.compile(r"^[ \t]*(`{3,}|~{3,})(.*)$")


def _code_lines(lines: list[str]) -> list[bool]:
    """Lines of fenced code blocks (at any indentation, e.g. inside list items)
    and of raw <pre> blocks. Indented lines are not code here: the converters
    always fence code, and Pandoc indents nested list content by 4 spaces."""
    code = [False] * len(lines)
    fence: str | None = None
    in_pre = False
    for i, line in enumerate(lines):
        match = _FENCE.match(line)
        if fence is not None:
            code[i] = True
            # A closing fence uses the same character, is at least as long, and has
            # no info string.
            closes = (
                match is not None
                and match.group(1)[0] == fence[0]
                and len(match.group(1)) >= len(fence)
                and not match.group(2).strip()
            )
            if closes:
                fence = None
        elif in_pre:
            code[i] = True
            in_pre = "</pre>" not in line
        elif match:
            fence, code[i] = match.group(1), True
        elif "<pre" in line:
            code[i] = True
            # A <pre> that closes on the same line does not continue.
            in_pre = "</pre>" not in line[line.index("<pre") :]
    return code


@dataclass
class Section:
    """One section file: its heading, line range in the full document, and body."""

    order: int  # 1-based
    level: int
    title: str  # heading text without anchors
    number: str | None  # "7.3.2" for numbered headings
    filename: str
    start: int  # first line index in the document
    end: int
    body: str = ""


@dataclass
class SplitResult:
    """The sections of a document plus the text before the first one."""

    level: int
    preamble: str
    sections: list[Section] = field(default_factory=list)

    def index_body(self, doc_id: str) -> str:
        """Body of index.md: the preamble, then a nested list of links (deviation SP5).

        Nesting is relative to the highest heading level present. doc_id is unused.
        """
        lines = []
        if self.preamble.strip():
            lines += [self.preamble.strip(), ""]
        lines += ["## Sections", ""]
        top = min((s.level for s in self.sections), default=1)
        for s in self.sections:
            # Escape brackets so a title like "[TTML2]" does not break the link text.
            title = s.title.replace("[", "\\[").replace("]", "\\]")
            lines.append(f"{'  ' * (s.level - top)}- [{title}]({s.filename})")
        return "\n".join(lines) + "\n"


def _file_name(number: str | None, title: str, order: int) -> str:
    """File name for a section: number-based when numbered, else position-based (SP2).

    Only a numeric first part is zero-padded, so "7.3" -> "07-3" and "A.1" -> "a-1".
    """
    if number:
        parts = number.split(".")
        if parts[0].isdigit():
            parts[0] = f"{int(parts[0]):02d}"
        rest = title[len(number) :].lstrip(". \t")
        return "-".join(p.lower() for p in parts) + "-" + slug(rest) + ".md"
    return f"s{order:02d}-{slug(title)}.md"


def split_sections(markdown: str, level: int) -> SplitResult:
    """Split converted Markdown into sections at headings of level `level` or higher.

    Links are rewritten so they still work from the section files (deviation SP4).
    The caller writes the files; this only computes names and bodies.
    """
    lines = markdown.split("\n")
    code_line = _code_lines(lines)
    # Headings are looked for only in unprotected text (not in code, tables or raw HTML).
    text_line = [False] * len(lines)
    for region in split_regions(markdown):
        if not region.protected:
            for i in range(region.start, region.end):
                text_line[i] = True

    starts: list[tuple[int, int, str]] = []  # (line, level, title)
    for i, line in enumerate(lines):
        if not text_line[i]:
            continue
        match = _HEADING.match(line)
        if match and len(match.group(1)) <= level:
            title = " ".join(_TAG.sub("", match.group(2)).split())
            starts.append((i, len(match.group(1)), title))

    result = SplitResult(level=level, preamble="")
    taken: set[str] = set()
    for order, (start, heading_level, title) in enumerate(starts, start=1):
        end = starts[order][0] if order < len(starts) else len(lines)
        number_match = _NUMBER.match(title)
        number = number_match.group(1) if number_match else None
        name = _file_name(number, title, order)
        base, n = name, 2
        # "index.md" is reserved for the table of contents.
        while name in taken or name == "index.md":
            name = f"{base[:-3]}-{n}.md"
            n += 1
        taken.add(name)
        result.sections.append(Section(order, heading_level, title, number, name, start, end))

    # Which section holds each id (ids in code blocks are not anchors). The first
    # occurrence wins. Ids in the preamble are not recorded, so links to them stay as is.
    first = result.sections[0].start if result.sections else len(lines)
    owner: dict[str, str] = {}
    for section in result.sections:
        for i in range(section.start, section.end):
            if code_line[i]:
                continue
            for match in _ID.finditer(lines[i]):
                owner.setdefault(match.group(1) or match.group(2), section.filename)

    def rewrite(i: int, here: str | None) -> str:
        """Line i with its link targets fixed for the file `here` (None = index.md)."""
        line = lines[i]
        if code_line[i]:
            return line

        def target(value: str) -> str:
            """Retarget one link value, keeping <...> brackets if it had them."""
            bracketed = value.startswith("<") and value.endswith(">")
            inner = value[1:-1] if bracketed else value
            new = _retarget(inner, here, owner)
            return f"<{new}>" if bracketed else new

        line = _MD_LINK.sub(lambda m: m.group(1) + target(m.group(2)), line)
        return _HTML_LINK.sub(
            lambda m: m.group(1) + m.group(2) + target(m.group(3)) + m.group(2), line
        )

    result.preamble = "\n".join(rewrite(i, None) for i in range(first))
    for section in result.sections:
        body = "\n".join(rewrite(i, section.filename) for i in range(section.start, section.end))
        section.body = body.rstrip("\n") + "\n"
    return result


def _retarget(value: str, here: str | None, owner: dict[str, str]) -> str:
    """New link target as seen from a file in sections/.

    "#id" points to the section file that holds the id, unless that is the current
    file or the id is unknown. A relative path gets "../" because section files are
    one folder deeper. Absolute paths and URLs with a scheme are left alone.
    """
    if value.startswith("#"):
        anchor = value[1:]
        # Also try the percent-decoded form ("#caf%C3%A9" for the id "café").
        file = owner.get(anchor) or owner.get(unquote(anchor))
        if file is None or file == here:
            return value
        return file + value
    parts = urlsplit(value)
    if parts.scheme or value.startswith(("/", "//")) or not value:
        return value
    return "../" + value
