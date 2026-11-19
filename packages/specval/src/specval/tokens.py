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

_HEXDIGIT_WITH_DIGIT = r"(?=[0-9A-Fa-f]*[0-9])"


@dataclass(frozen=True)
class Family:
    name: str
    patterns: tuple[re.Pattern[str], ...]
    scope: str | None = None  # None: all text; "table": table cells only
    fold_case: bool = False  # compare lowercased (keywords)

    def find(self, text: str) -> list[str]:
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
            re.compile(r"(?<![\w.])0[xX][0-9A-Fa-f]+(?![\w])"),
            re.compile(rf"(?<![\w.]){_HEXDIGIT_WITH_DIGIT}[0-9A-Fa-f]{{2,4}}h(?![\w])"),
        ),
    ),
    Family(
        "hex byte pairs in tables",
        (re.compile(rf"(?<![\w.:]){_HEXDIGIT_WITH_DIGIT}[0-9A-Fa-f]{{4}}(?![\w.:])"),),
        scope=TABLE,
    ),
    Family(
        "timecodes",
        (
            re.compile(r"(?<![\d:.;])\d{2}:\d{2}:\d{2}[:;]\d{2}(?![\d:;])"),
            re.compile(r"(?<![\d:.])\d{2}:\d{2}:\d{2}\.\d{3}(?!\d)"),
            re.compile(r"(?<![\d:.])\d{2}:\d{2}\.\d{3}(?!\d)"),
        ),
    ),
    Family(
        "numbers with units",
        (
            re.compile(
                r"(?<![\w.])\d+(?:\.\d+)?\s?(?:px|%|rows|columns|fps|ms|s|c)(?![\w%])",
            ),
        ),
    ),
    Family(
        "normative keywords",
        (
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
