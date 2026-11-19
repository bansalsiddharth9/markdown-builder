"""Whitespace at the edges of inline code and emphasis, and tabs in code
(deviations Q21, Q22). Found by V5, V6 and V3 on TTML2."""

from __future__ import annotations

from pathlib import Path

import pytest
from markdown_it import MarkdownIt
from specconv.converters.pandoc import PandocConverter


@pytest.fixture
def gfm(tmp_path: Path):
    def _convert(html: str) -> str:
        src = tmp_path / "in.html"
        src.write_text(html, encoding="utf-8")
        return PandocConverter("html").convert(src, tmp_path / "out").markdown

    return _convert


def rendered_text(md: str) -> str:
    """The text a CommonMark viewer shows."""
    html = MarkdownIt("commonmark").render(md)
    from bs4 import BeautifulSoup

    return BeautifulSoup(html, "html.parser").get_text()


def test_tabs_in_code_kept(gfm) -> None:
    md = gfm("<pre><code>&lt;tt&gt;\n\t&lt;p/&gt;\n&lt;/tt&gt;</code></pre>")
    assert "\n\t<p/>\n" in md


def test_code_span_with_spaces_on_both_sides_keeps_them(gfm) -> None:
    md = gfm("<p>left <code>x</code><code> top </code><code>y</code></p>")
    assert "` top `" not in md
    assert rendered_text(md).strip() == "left x top y"


def test_emphasis_with_trailing_nbsp_stays_emphasis(gfm) -> None:
    md = gfm("<p>add <em>S'&nbsp;</em>to the set</p>")
    assert "*S'*" in md and "* " not in md.replace("*S'*", "")
    assert rendered_text(md).strip() == "add S' to the set"


def test_strong_with_leading_space(gfm) -> None:
    md = gfm("<p>a<b> bold</b> word</p>")
    assert rendered_text(md).strip() == "a bold word"
    assert "**bold**" in md
