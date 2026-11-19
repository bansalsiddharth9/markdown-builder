"""V3 text completeness (design: V3 text completeness details).

- Both sides are normalized: NFKC, collapsed whitespace, dash and quote
  variants unified (Docling turns "–" into "-"; exact characters are V4's
  job), words hyphenated across PDF line breaks joined.
- The source is split into sentences; fragments under min_sentence_chars are
  skipped (V4 covers short values).
- A sentence is present on an exact substring match, near when rapidfuzz's
  partial_ratio is at least fuzzy_ratio, otherwise missing. Coverage is
  (present + near) / sentences. Presence, not a linear diff, because PDF
  extraction order can differ from Docling's reading order.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from specval.config import TextCoverage

_DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−"), "-")
_QUOTES = {ord(c): '"' for c in "“”„‟«»"} | {ord(c): "'" for c in "‘’‚‛"}
_SENTENCE_END = re.compile(r"(?<=[.!?])[\"')\]]*\s+(?=[A-Z0-9\"'(\[])")
_HYPHEN_BREAK = re.compile(r"(\w)-\n(?=[a-z])")
_WORD = re.compile(r"\w{5,}")
_LEADER = re.compile(r"(?:\.\s?){3,}")  # dot leaders in contents lines


def normalize_for_v3(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_DASHES).translate(_QUOTES)
    text = _LEADER.sub(" ... ", text)
    return " ".join(text.split())


def join_pdf_hyphenation(text: str) -> str:
    """'exam-\\nple' -> 'example' (a lowercase word continued on the next line)."""
    return _HYPHEN_BREAK.sub(r"\1", text)


def sentences(text: str, min_chars: int) -> list[str]:
    parts = _SENTENCE_END.split(normalize_for_v3(text))
    return [p.strip() for p in parts if len(p.strip()) >= min_chars]


@dataclass
class Coverage:
    total: int = 0
    present: int = 0
    near: int = 0
    missing: list[tuple[str, str | None]] = field(default_factory=list)  # (sentence, location)

    @property
    def ratio(self) -> float:
        return (self.present + self.near) / self.total if self.total else 1.0


def _near(sentence: str, haystack: str, threshold: float) -> bool:
    """partial_ratio against windows around the sentence's longest words; the
    whole Markdown can be megabytes, too slow to scan for every sentence."""
    words = sorted(set(_WORD.findall(sentence)), key=len, reverse=True)[:3]
    span = len(sentence)
    for word in words:
        start = haystack.find(word)
        hits = 0
        while start >= 0 and hits < 20:
            window = haystack[max(0, start - span) : start + 2 * span]
            if fuzz.partial_ratio(sentence, window, score_cutoff=threshold):
                return True
            start = haystack.find(word, start + 1)
            hits += 1
    return False


def coverage(
    source: Iterable[tuple[str, str | None]],
    markdown: str,
    cfg: TextCoverage,
    by_line: bool = False,
) -> Coverage:
    """source: (text, location) pieces; markdown: all Markdown text.

    by_line (PDF): every line is split on its own. PDF text has no paragraphs,
    so a heading without a full stop would glue onto the next sentence, and
    Docling may put blocks in another order than the page's text layer.
    """
    haystack = normalize_for_v3(markdown)
    result = Coverage()
    for text, loc in source:
        pieces = text.split("\n") if by_line else [text]
        units = [u for piece in pieces for u in sentences(piece, cfg.min_sentence_chars)]
        for sentence in units:
            result.total += 1
            if sentence in haystack:
                result.present += 1
            elif _near(sentence, haystack, cfg.fuzzy_ratio):
                result.near += 1
            else:
                result.missing.append((sentence, loc))
    return result
