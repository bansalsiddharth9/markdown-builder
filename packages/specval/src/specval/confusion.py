"""Character confusion (design: V8): number or hex-like tokens with a letter
that is easily mistaken for a digit (O for 0, o, l and I for 1, S for 5)."""

from __future__ import annotations

import re

_CANDIDATE = re.compile(r"(?<![\w.])[0-9A-Fa-fOolISxX]{2,}h?(?![\w])")
_CONFUSABLE = re.compile(r"[OolIS]")
_DIGIT = re.compile(r"\d")


def confusable_tokens(text: str) -> list[str]:
    found = []
    for match in _CANDIDATE.finditer(text):
        token = match.group(0)
        body = token[:-1] if token.endswith("h") else token
        if not (_DIGIT.search(body) and _CONFUSABLE.search(body)):
            continue
        x = body.lower().find("x")
        if x >= 0 and not (x == 1 and body[0] in "0O" and body.lower().count("x") == 1):
            continue  # an x only as the 0x prefix
        found.append(token)
    return found
