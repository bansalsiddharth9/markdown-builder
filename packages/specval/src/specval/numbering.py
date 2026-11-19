"""Section numbers in headings (design: V2).

A heading is numbered when its text starts with a number such as ``7``,
``7.3.2`` or ``7.3.2.`` (each part at most 3 digits, so a year like "2020
overview" is not a section number), with ``A.1``-style annex numbers, or with
"Annex B" / "Appendix B". A bare letter ("A Title") is not a number: too many
headings start with a one-letter word.

Reading order rules: after ``7.3`` the next number may be its first child
``7.3.1``, the next sibling ``7.4`` or the next sibling of an ancestor
(``8``). When an annex's own heading has no number (``Reduced XML Infoset``
under "A"), its first subsection may start the annex: ``A.1`` after ``13.2``,
``B.1`` after ``A.4``. An annex without numbered subsections leaves no trace,
so any later annex letter may follow (``E.1`` after ``C.2``).

One stray number (e.g. a list item turned into a heading) is reported once:
the number after it is also accepted when it follows the last good number.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_NUMBER = re.compile(
    r"^\s*(?:(?:annex|appendix)\s+(?P<annex>[A-Z])\b"
    r"|(?P<num>(?:\d{1,3}|[A-Z])(?:\.\d{1,3})*)\.?(?=\s|$))",
    re.IGNORECASE,
)

SKIPPED, DUPLICATED, OUT_OF_ORDER = "skipped", "duplicated", "out of order"


def section_number(text: str) -> tuple[str, ...] | None:
    match = _NUMBER.match(text)
    if not match:
        return None
    if match.group("annex"):
        return (match.group("annex").upper(),)
    parts = tuple(match.group("num").split("."))
    if parts[0].isalpha():
        if len(parts) == 1 or not parts[0].isupper():
            return None  # "A Title" / "a.1" are not section numbers
    return parts


def label(number: tuple[str, ...]) -> str:
    return ".".join(number)


def _succ(part: str) -> str:
    return chr(ord(part) + 1) if part.isalpha() else str(int(part) + 1)


def _key(number: tuple[str, ...]) -> tuple:
    # numeric sections before annexes; numbers compare as numbers
    return tuple((1, ord(p), 0) if p.isalpha() else (0, 0, int(p)) for p in number)


def allowed_next(prev: tuple[str, ...], cur: tuple[str, ...]) -> bool:
    if cur == prev + ("1",):
        return True
    for k in range(len(prev)):
        if cur == prev[:k] + (_succ(prev[k]),):
            return True
    if cur[0].isalpha() and all(p == "1" for p in cur[1:]):
        # a new annex whose own heading carries no number: A.1 after 13.2, E.1 after C.2
        if not prev[0].isalpha() or cur[0] > prev[0]:
            return True
    return False


@dataclass(frozen=True)
class SequenceProblem:
    index: int  # position in the list checked
    kind: str  # skipped | duplicated | out of order
    number: tuple[str, ...]
    previous: tuple[str, ...]


def sequence_problems(numbers: list[tuple[str, ...]]) -> list[SequenceProblem]:
    problems: list[SequenceProblem] = []
    seen: set[tuple[str, ...]] = set()
    prev: tuple[str, ...] | None = None
    last_good: tuple[str, ...] | None = None
    for i, cur in enumerate(numbers):
        ok = prev is None or allowed_next(prev, cur)
        if not ok and last_good is not None and allowed_next(last_good, cur):
            ok = True
        if not ok:
            assert prev is not None
            if cur in seen:
                kind = DUPLICATED
            elif _key(cur) < _key(prev):
                kind = OUT_OF_ORDER
            else:
                kind = SKIPPED
            problems.append(SequenceProblem(i, kind, cur, prev))
        else:
            last_good = cur
        seen.add(cur)
        prev = cur
    return problems
