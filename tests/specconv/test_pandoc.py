"""Pandoc output for the fixture spec contains every heading, table and code
block (design: Converter tests)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from specconv.converters.pandoc import ANCHORS_FILTER, SPACING_FILTER, PandocConverter
from specconv.htmlprep import prepare_html
from specconv.postprocess import postprocess

STRIP = ("script", "style", "noscript")
ANCHOR = re.compile(r'<a id="[^"]*"></a>')


def convert(src: Path, fmt: str, out_dir: Path) -> str:
    """Convert like the pipeline does: HTML is prepared first (images, cleanup)."""
    if fmt == "html":
        prepared = prepare_html(
            src.read_bytes(), strip_tags=STRIP, media_dir=out_dir / "media", html_path=src
        )
        src = out_dir.parent / f"{out_dir.name}-prepared.html"
        src.write_text(prepared.html, encoding="utf-8")
    result = PandocConverter(fmt).convert(src, out_dir)
    return postprocess(result.markdown, out_dir).markdown


@pytest.fixture(params=["html", "docx"])
def converted(request, fixtures: dict[str, Path], tmp_path: Path) -> tuple[str, str, Path]:
    fmt = request.param
    out = tmp_path / "out"
    return fmt, convert(fixtures[fmt], fmt, out), out


def test_all_headings_present(converted, fixture_mod: dict) -> None:
    fmt, md, _ = converted
    plain = ANCHOR.sub("", md)
    Heading = fixture_mod["Heading"]
    for block in fixture_mod["SPEC"]:
        if isinstance(block, Heading):
            assert f"{'#' * block.level} {block.text}\n" in plain, (fmt, block.text)


def test_heading_anchors_only_from_source_ids(converted, fixture_mod: dict) -> None:
    fmt, md, _ = converted
    for block in fixture_mod["SPEC"]:
        if isinstance(block, fixture_mod["Heading"]):
            line = f'{"#" * block.level} <a id="{block.anchor}"></a>{block.text}\n'
            if fmt == "html":
                assert line in md
            else:
                # DOCX headings carry no ids, and Pandoc must not invent any
                assert "<a id=" not in md


def test_all_paragraph_text_present(converted, fixture_mod: dict) -> None:
    fmt, md, _ = converted
    for block in fixture_mod["SPEC"]:
        if isinstance(block, fixture_mod["Para"]):
            assert block.text in md, (fmt, block.text)


def test_code_blocks_exact(converted, fixture_mod: dict) -> None:
    fmt, md, _ = converted
    for block in fixture_mod["SPEC"]:
        if isinstance(block, fixture_mod["Code"]):
            # fenced for both: DOCX "Source Code" paragraphs have no attributes,
            # so the anchors filter writes the fence itself
            assert f"\n{block.text}\n```" in md, fmt


def test_merged_cell_table_kept_as_html_with_spans(converted) -> None:
    fmt, md, _ = converted
    assert '<th colspan="2">Byte pair</th>' in md
    assert '<th rowspan="2">Command</th>' in md
    for value in ("<td>2C</td>", "<td>EDM</td>", "<td>Erase displayed memory</td>"):
        assert value in md, fmt


def test_simple_table_is_pipe_table(converted) -> None:
    _, md, _ = converted
    assert "| Columns  | 32 columns |" in md


def test_image_in_media_with_relative_link(converted) -> None:
    fmt, md, out = converted
    media = [p for p in (out / "media").rglob("*") if p.is_file()]
    assert len(media) == 1, fmt
    rel = media[0].relative_to(out).as_posix()
    assert f"({rel})" in md or f'src="{rel}"' in md


def test_values_and_unicode_preserved(converted) -> None:
    _, md, _ = converted
    for token in ("0x14", "2Ch", "9420", "01:00:00;02", "29.97 fps", "Café, naïve", "–"):
        assert token in md


def test_script_style_noscript_removed_from_html(
    fixtures: dict[str, Path], tmp_path: Path, fixture_mod: dict
) -> None:
    assert fixture_mod["STRIPPED_TEXT"].encode() in fixtures["html"].read_bytes()
    md = convert(fixtures["html"], "html", tmp_path / "out")
    assert fixture_mod["STRIPPED_TEXT"] not in md


def test_output_deterministic(fixtures: dict[str, Path], tmp_path: Path) -> None:
    for fmt in ("html", "docx"):
        a = convert(fixtures[fmt], fmt, tmp_path / f"{fmt}-a")
        b = convert(fixtures[fmt], fmt, tmp_path / f"{fmt}-b")
        assert a == b


def test_options_and_command() -> None:
    conv = PandocConverter("docx")
    assert conv.options() == {
        "from": "docx-auto_identifiers",
        "to": "gfm",
        "wrap": "none",
        "sandbox": True,
        "lua_filter": ["anchors.lua", "spacing.lua"],
        "preserve_tabs": True,
        "anchor_headings": "targeted",
        "track_changes": "accept",
        "extract_media": "media",
    }
    assert conv.command(Path("/x/in.docx"))[1:] == [
        "/x/in.docx",
        "--sandbox",
        "-f",
        "docx-auto_identifiers",
        "--track-changes=accept",
        "-t",
        "gfm",
        "--wrap=none",
        "--preserve-tabs",
        "--metadata=anchor-headings:targeted",
        f"--lua-filter={ANCHORS_FILTER}",
        f"--lua-filter={SPACING_FILTER}",
        "--extract-media=media",
    ]
    html = PandocConverter("html").command(Path("/x/in.html"))
    assert "--sandbox" in html and not any(a.startswith("--extract-media") for a in html)
    assert conv.version().startswith("3.")


def test_example_in_one_cell_table_becomes_code_block(tmp_path: Path) -> None:
    """TTML2 style: each example is a bare <pre> boxed in a one-cell table (decision Q14)."""
    src = tmp_path / "ex.html"
    src.write_text(
        '<p>See <a href="#ex1">the example</a>.</p>'
        '<table class="example" id="ex1"><caption>Example – One</caption><tbody><tr><td>'
        '<div class="exampleInner"><pre>&lt;tt&gt;\n  &lt;body/&gt;\n&lt;/tt&gt;</pre></div>'
        "</td></tr></tbody></table>"
        "<pre>plain   preformatted\n    text</pre>",
        encoding="utf-8",
    )
    md = convert(src, "html", tmp_path / "out")
    assert '<span id="ex1"></span>Example – One\n' in md
    assert "```\n<tt>\n  <body/>\n</tt>\n```" in md
    assert "```\nplain   preformatted\n    text\n```" in md
    assert "<table" not in md and "<br />" not in md


def test_content_outside_main_kept(tmp_path: Path) -> None:
    """Pandoc's HTML reader alone keeps only <main> when there is one."""
    src = tmp_path / "m.html"
    src.write_text("<p>Before</p><main><p>Inside</p></main><p>After</p>", encoding="utf-8")
    md = convert(src, "html", tmp_path / "out")
    assert "Before" in md and "Inside" in md and "After" in md
