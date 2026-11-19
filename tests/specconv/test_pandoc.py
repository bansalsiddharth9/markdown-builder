"""Pandoc output for the fixture spec contains every heading, table and code
block (design: Converter tests)."""

from __future__ import annotations

from pathlib import Path

import pytest
from specconv.converters.pandoc import PandocConverter
from specconv.htmlclean import clean_html
from specconv.postprocess import postprocess


def convert(src: Path, fmt: str, out_dir: Path) -> str:
    result = PandocConverter(fmt).convert(src, out_dir)
    return postprocess(result.markdown, out_dir).markdown


@pytest.fixture(params=["html", "docx"])
def converted(request, fixtures: dict[str, Path], tmp_path: Path) -> tuple[str, str, Path]:
    fmt = request.param
    out = tmp_path / "out"
    return fmt, convert(fixtures[fmt], fmt, out), out


def test_all_headings_present(converted, fixture_mod: dict) -> None:
    fmt, md, _ = converted
    Heading = fixture_mod["Heading"]
    for block in fixture_mod["SPEC"]:
        if isinstance(block, Heading):
            assert f"{'#' * block.level} {block.text}\n" in md, (fmt, block.text)


def test_all_paragraph_text_present(converted, fixture_mod: dict) -> None:
    fmt, md, _ = converted
    for block in fixture_mod["SPEC"]:
        if isinstance(block, fixture_mod["Para"]):
            assert block.text in md, (fmt, block.text)


def test_code_blocks_exact(converted, fixture_mod: dict) -> None:
    fmt, md, _ = converted
    for block in fixture_mod["SPEC"]:
        if isinstance(block, fixture_mod["Code"]):
            if fmt == "html":
                # fenced: the code text appears verbatim
                assert f"\n{block.text}\n```" in md, fmt
            else:
                # DOCX "Source Code" paragraphs become an indented code block
                indented = "\n".join(
                    ("    " + line) if line else "" for line in block.text.split("\n")
                )
                assert indented in md, fmt


def test_merged_cell_table_kept_as_html_with_spans(converted) -> None:
    fmt, md, _ = converted
    assert '<th colspan="2">Byte pair</th>' in md
    assert '<th rowspan="2">Command</th>' in md
    for value in ("<td>2C</td>", "<td>EDM</td>", "<td>Erase displayed memory</td>"):
        assert value in md, fmt


def test_simple_table_is_pipe_table(converted) -> None:
    _, md, _ = converted
    assert "| Columns  | 32 columns |" in md


def test_image_extracted_with_relative_link(converted) -> None:
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
    raw = fixtures["html"].read_bytes()
    assert fixture_mod["STRIPPED_TEXT"].encode() in raw
    cleaned = clean_html(raw, ("script", "style", "noscript"))
    assert cleaned.removed == {"script": 1, "style": 1, "noscript": 1}
    src = tmp_path / "clean.html"
    src.write_text(cleaned.html, encoding="utf-8")
    result = PandocConverter("html").convert(
        src, tmp_path / "out", resource_dir=fixtures["html"].parent
    )
    assert fixture_mod["STRIPPED_TEXT"] not in result.markdown


def test_clean_html_converts_other_encodings_to_utf8() -> None:
    raw = '<html><head><meta charset="iso-8859-1"></head><body><p>Café</p></body></html>'
    cleaned = clean_html(raw.encode("iso-8859-1"), ())
    assert "Café" in cleaned.html
    assert cleaned.original_encoding == "iso-8859-1"


def test_clean_html_leaves_pre_whitespace() -> None:
    raw = b"<pre>  a  \n\n   b\t</pre><script>x</script>"
    assert clean_html(raw, ("script",)).html == "<pre>  a  \n\n   b\t</pre>"


def test_output_deterministic(fixtures: dict[str, Path], tmp_path: Path) -> None:
    for fmt in ("html", "docx"):
        a = convert(fixtures[fmt], fmt, tmp_path / f"{fmt}-a")
        b = convert(fixtures[fmt], fmt, tmp_path / f"{fmt}-b")
        assert a == b


def test_options_and_command() -> None:
    conv = PandocConverter("docx")
    assert conv.options() == {
        "from": "docx",
        "to": "gfm",
        "wrap": "none",
        "extract_media": "media",
        "track_changes": "accept",
    }
    cmd = conv.command(Path("/x/in.docx"))
    assert cmd[1:] == [
        "/x/in.docx",
        "-f",
        "docx",
        "--track-changes=accept",
        "-t",
        "gfm",
        "--wrap=none",
        "--extract-media=media",
        "--resource-path=/x",
    ]
    assert conv.version().startswith("3.")
