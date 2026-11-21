"""Character confusion (design: V8): number or hex-like tokens with a letter
that is easily mistaken for a digit (O for 0, o, l and I for 1, S for 5)."""

from __future__ import annotations

import re

# A whole word of 2+ hex digits, look-alike letters (O o l I S) and x, with an
# optional trailing "h" (hex suffix). Not after a letter, digit, "_" or "."
# (so not the tail of "1.5" or a longer word), and not followed by one.
_CANDIDATE = re.compile(r"(?<![\w.])[0-9A-Fa-fOolISxX]{2,}h?(?![\w])")
# The letters easily read as digits: O for 0, o, l and I for 1, S for 5.
_CONFUSABLE = re.compile(r"[OolIS]")
_DIGIT = re.compile(r"\d")


def confusable_tokens(text: str) -> list[str]:
    """Tokens in text that look like a number or hex value with a look-alike
    letter in it, e.g. "1O" or "0x2l" (deviations V-z).

    A token counts only when it has at least one real digit and at least one
    of O, o, l, I, S. So plain words ("SOS", "Ol") and plain numbers never
    count. An "x" is allowed only as the "0x" prefix (or "Ox", itself a
    likely confusion); an "h" only at the end (the hex suffix).
    """
    found = []
    for match in _CANDIDATE.finditer(text):
        token = match.group(0)
        # The "h" suffix is not part of the value; drop it before the tests.
        body = token[:-1] if token.endswith("h") else token
        if not (_DIGIT.search(body) and _CONFUSABLE.search(body)):
            continue
        x = body.lower().find("x")
        if x >= 0 and not (x == 1 and body[0] in "0O" and body.lower().count("x") == 1):
            continue  # an x only as the 0x prefix
        found.append(token)
    return found
