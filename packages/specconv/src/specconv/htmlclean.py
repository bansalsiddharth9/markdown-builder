"""HTML cleanup before conversion (design: HTML cleanup before conversion).

clean: none (default, and always for local HTML files): only remove the
configured strip_tags (script, style, noscript). The main-content mode for
web sources is added with fetching in phase 4.

BeautifulSoup is used with Python's html.parser, which tokenizes without
repairing the document structure, so apart from the removed elements the
HTML reaching Pandoc is what the author wrote. The result is always UTF-8,
which is what Pandoc expects, whatever the source encoding was.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from bs4 import BeautifulSoup


@dataclass
class CleanedHtml:
    html: str
    removed: dict[str, int]  # tag -> number of elements removed
    original_encoding: str | None


def clean_html(data: bytes, strip_tags: Iterable[str]) -> CleanedHtml:
    soup = BeautifulSoup(data, "html.parser")
    removed: dict[str, int] = {}
    for tag in strip_tags:
        elements = soup.find_all(tag)
        if elements:
            removed[tag] = len(elements)
        for element in elements:
            element.decompose()
    return CleanedHtml(html=str(soup), removed=removed, original_encoding=soup.original_encoding)
