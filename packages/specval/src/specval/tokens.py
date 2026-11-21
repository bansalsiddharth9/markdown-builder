"""Protected token families for V4 (design: V4 protected tokens).

Each family is a list of regexes; a token is one match, compared as a
multiset between source and Markdown. Matching is done per text segment
after NFKC and whitespace normalization; ``scope`` limits a family to table
cells (the 4-digit byte pairs: elsewhere they would match years).

Decisions (deviations V-a, V-b, V-c):
- Hex values are whole words and must contain at least one digit, so "each"
  or "Bach" never count; ``0x…`` always has digits after it.
- Normative keywords: the longest phrase wins ("shall not" is one token,
  not also a "shall"); case-insensitive; "may" also matches the month.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from specval.text import TABLE

# Lookahead: the hex run that follows has at least one decimal digit. Stops
# words made only of a-f ("each", "Bach", "face") from counting as hex.
_HEXDIGIT_WITH_DIGIT = r"(?=[0-9A-Fa-f]*[0-9])"


@dataclass(frozen=True)
class Family:
    """One named group of token regexes, counted together in V4."""

    name: str
    patterns: tuple[re.Pattern[str], ...]
    scope: str | None = None  # None: all text; "table": table cells only
    fold_case: bool = False  # compare lowercased (keywords)

    def find(self, text: str) -> list[str]:
        """Every match of every pattern, inner whitespace collapsed to one
        space ("shall\n not" -> "shall not"), lowercased when fold_case."""
        found: list[str] = []
        for pattern in self.patterns:
            for match in pattern.finditer(text):
                token = " ".join(match.group(0).split())
                found.append(token.lower() if self.fold_case else token)
        return found


DEFAULT_FAMILIES: tuple[Family, ...] = (
    Family(
        "hex",
        (
            # 0x1F, 0X7f: a whole word (not inside "a0x1" or "1.0x2")
            re.compile(r"(?<![\w.])0[xX][0-9A-Fa-f]+(?![\w])"),
            # 14h, 7Fh, 1A2Bh: 2-4 hex digits (one a decimal digit) and an "h" suffix
            re.compile(rf"(?<![\w.]){_HEXDIGIT_WITH_DIGIT}[0-9A-Fa-f]{{2,4}}h(?![\w])"),
        ),
    ),
    Family(
        "hex byte pairs in tables",
        # exactly 4 hex digits ("9420"), not part of a longer word, a decimal
        # number or a time ("12:34")
        (re.compile(rf"(?<![\w.:]){_HEXDIGIT_WITH_DIGIT}[0-9A-Fa-f]{{4}}(?![\w.:])"),),
        scope=TABLE,
    ),
    Family(
        "timecodes",
        (
            # SMPTE timecode HH:MM:SS:FF, or HH:MM:SS;FF (drop-frame)
            re.compile(r"(?<![\d:.;])\d{2}:\d{2}:\d{2}[:;]\d{2}(?![\d:;])"),
            # HH:MM:SS.mmm
            re.compile(r"(?<![\d:.])\d{2}:\d{2}:\d{2}\.\d{3}(?!\d)"),
            # MM:SS.mmm; the lookbehind stops it matching the tail of HH:MM:SS.mmm
            re.compile(r"(?<![\d:.])\d{2}:\d{2}\.\d{3}(?!\d)"),
        ),
    ),
    Family(
        "numbers with units",
        (
            # "15px", "25 %", "4 rows", "29.97fps", "2s", "32c" (cells); one optional
            # space; the unit must end the word, so "4 sec" or "5 colors" do not count
            re.compile(
                r"(?<![\w.])\d+(?:\.\d+)?\s?(?:px|%|rows|columns|fps|ms|s|c)(?![\w%])",
            ),
        ),
    ),
    Family(
        "normative keywords",
        (
            # The two-word forms come first in the alternation, so the regex
            # takes "shall not" as one token, not "shall" (deviations V-b).
            re.compile(
                r"\b(?:shall\s+not|should\s+not|must\s+not|shall|should|must|may|"
                r"required|recommended|optional)\b",
                re.IGNORECASE,
            ),
        ),
        fold_case=True,
    ),
    Family(
        "XML names",
        (
            # prefixed names such as tts:origin or ttp:frameRate: not part of a
            # longer name, path or URL, and not followed by "//"
            re.compile(r"(?<![\w:/.-])[A-Za-z_][\w.-]*:[A-Za-z_][\w.-]*(?![\w:])(?!//)"),
            # element tags; a name may not end in ':' so <http://…> is not a tag
            re.compile(r"</?[A-Za-z_](?:[\w.:-]*[\w.-])?(?=[\s/>])"),
        ),
    ),
)


def families(extra: dict[str, tuple[str, ...]]) -> tuple[Family, ...]:
    """The built-in families plus token_families from specval.yaml (same name: replaced)."""
    custom = {
        name: Family(name, tuple(re.compile(p) for p in patterns))
        for name, patterns in extra.items()
    }
    merged = [custom.pop(f.name, f) for f in DEFAULT_FAMILIES]
    return tuple(merged + list(custom.values()))
