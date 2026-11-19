"""The anchors Lua filter (design: "Heading and element ids"; decision I3).

Each test converts a small HTML snippet with the real Pandoc and filter.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from specconv.converters.pandoc import PandocConverter


@pytest.fixture
def gfm(tmp_path: Path):
    def _convert(html: str) -> str:
        src = tmp_path / "in.html"
        src.write_text(html, encoding="utf-8")
        return PandocConverter("html").convert(src, tmp_path / "out").markdown

    return _convert


def link_targets(md: str) -> set[str]:
    return set(re.findall(r"\]\(#([^)\s]+)\)", md))


def anchor_ids(md: str) -> list[str]:
    return re.findall(r'<(?:a|span|tr|td|th|div|figure|table)\b[^>]*\bid="([^"]+)"', md)


def assert_every_link_resolves(md: str) -> None:
    ids = anchor_ids(md)
    assert len(ids) == len(set(ids)), f"duplicate ids: {ids}"
    missing = link_targets(md) - set(ids)
    assert not missing, f"links without a target: {missing}"


def test_section_and_heading_ids_on_heading_line(gfm) -> None:
    md = gfm('<section id="sec-7"><h2 id="x-sec-7">7 Modes</h2><p>Text.</p></section>')
    assert md.startswith('## <a id="sec-7"></a><a id="x-sec-7"></a>7 Modes\n')
    assert "<div" not in md  # the plain section wrapper is removed


def test_nested_sections(gfm) -> None:
    md = gfm(
        '<section id="s7"><h2 id="h7">7 A</h2>'
        '<section id="s71"><h3 id="h71">7.1 B</h3><p>x</p></section></section>'
    )
    assert '## <a id="s7"></a><a id="h7"></a>7 A\n' in md
    assert '### <a id="s71"></a><a id="h71"></a>7.1 B\n' in md


def test_heading_and_section_ids_kept_even_without_links(gfm) -> None:
    md = gfm('<section id="sec-1"><h2 id="h-1">1 Scope</h2></section><p>No links.</p>')
    assert '<a id="sec-1"></a><a id="h-1"></a>' in md


def test_section_with_extra_class_keeps_wrapper(gfm) -> None:
    md = gfm('<section id="s" class="informative"><h2 id="h">2 Info</h2><p>x</p></section>')
    assert '<div class="section informative">' in md
    assert '## <a id="s"></a><a id="h"></a>2 Info' in md


def test_section_without_heading_gets_anchor_before(gfm) -> None:
    md = gfm('<section id="abstract"><p>Abstract text.</p></section><a href="#abstract">a</a>')
    assert md.startswith('<a id="abstract"></a>\n\nAbstract text.')
    assert_every_link_resolves(md)


def test_old_style_empty_anchor_in_heading_kept_once(gfm) -> None:
    md = gfm('<h2><a id="intro"></a>1 Introduction</h2><p><a href="#intro">i</a></p>')
    assert md.startswith('## <span id="intro"></span>1 Introduction\n')
    assert_every_link_resolves(md)


def test_targeted_elements_get_anchors_untargeted_lose_ids(gfm) -> None:
    md = gfm(
        '<p>A <dfn id="dfn-x">x</dfn>, <code id="c1">tt</code>.</p>'
        '<figure id="fig-1"><img src="f.png" alt="f"><figcaption>F</figcaption></figure>'
        '<pre id="ex-1"><code>&lt;tt/&gt;</code></pre>'
        '<div class="example" id="ex-2"><p>Example</p></div>'
        '<div class="note" id="note-1"><p>Note</p></div>'
        '<p><a href="#dfn-x">d</a> <a href="#c1">c</a> <a href="#fig-1">f</a>'
        ' <a href="#ex-1">e</a> <a href="#ex-2">e2</a></p>'
    )
    assert_every_link_resolves(md)
    assert '<a id="ex-1"></a>\n\n```' in md  # code block stays fenced
    assert '<div class="note">' in md and "note-1" not in md


def test_pipe_table_row_and_cell_ids_move_before_table(gfm) -> None:
    md = gfm(
        '<table id="t1"><caption>Codes</caption><tr><th>Code</th></tr>'
        '<tr id="row-rcl"><td id="cell-14">14</td></tr><tr id="row-x"><td>15</td></tr></table>'
        '<p><a href="#t1">t</a> <a href="#row-rcl">r</a> <a href="#cell-14">c</a></p>'
    )
    assert md.startswith('<a id="t1"></a><a id="row-rcl"></a><a id="cell-14"></a>\n\n| Code |')
    assert "{#t1}" not in md  # Pandoc would write the caption id literally
    assert "row-x" not in md  # untargeted row ids cannot be kept in a pipe table
    assert_every_link_resolves(md)


def test_html_table_keeps_row_ids(gfm) -> None:
    md = gfm(
        '<table><tr><th colspan="2">Pair</th></tr><tr id="row-edm"><td>14</td><td>2C</td></tr>'
        '</table><p><a href="#row-edm">e</a></p>'
    )
    assert '<tr id="row-edm">' in md
    assert '<a id="row-edm"></a>' not in md
    assert_every_link_resolves(md)


def test_link_targets_unchanged(gfm) -> None:
    md = gfm('<h2 id="a b">X</h2><p><a href="#a%20b">x</a> <a href="#nowhere">n</a></p>')
    assert "](#a%20b)" in md and "](#nowhere)" in md
    assert '<a id="a b"></a>' in md


def test_ids_escaped_in_anchor(gfm) -> None:
    md = gfm('<h2 id="a&quot;b">X</h2>')
    assert '<a id="a&quot;b"></a>' in md


def test_targeted_heading_mode_keeps_only_linked_headings(tmp_path: Path) -> None:
    """DOCX mode: Pandoc invents heading ids, so only linked ones are kept."""
    conv = PandocConverter("html")
    conv.anchor_headings = "targeted"
    src = tmp_path / "in.html"
    src.write_text(
        '<h2 id="toc-1">1 A</h2><h2 id="b">2 B</h2><p><a href="#toc-1">1</a></p>', encoding="utf-8"
    )
    md = conv.convert(src, tmp_path / "out").markdown
    assert '## <a id="toc-1"></a>1 A\n' in md and "## 2 B\n" in md


def test_nav_id_survives_preparation_and_conversion(tmp_path: Path) -> None:
    """End to end: the id Pandoc's reader would drop from <nav> is kept as an anchor."""
    from specconv.htmlprep import prepare_html

    raw = b'<nav id="toc"><ol><li>1 Intro</li></ol></nav><p><a href="#toc">Contents</a></p>'
    prepared = prepare_html(raw, strip_tags=(), media_dir=tmp_path / "media")
    src = tmp_path / "in.html"
    src.write_text(prepared.html, encoding="utf-8")
    md = PandocConverter("html").convert(src, tmp_path / "out").markdown
    assert_every_link_resolves(md)


def test_table_classes_and_styles_not_written_after_caption(gfm) -> None:
    """GFM has no table attributes; Pandoc would write "Caption {.common style=…}"."""
    md = gfm(
        '<a href="#t">t</a><table id="t" class="common" style="width: 5px">'
        "<caption>Table 5-1 – Namespaces</caption><tr><th>A</th></tr><tr><td>1</td></tr></table>"
    )
    assert "{" not in md and "common" not in md and "width" not in md
    assert "Table 5-1 – Namespaces\n" in md
    assert_every_link_resolves(md)
