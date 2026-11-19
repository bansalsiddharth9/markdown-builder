from __future__ import annotations

import itertools
import random
from pathlib import Path

import pytest
from specconv.postprocess import (
    PostprocessError,
    normalize_text,
    postprocess,
    rewrite_media_links,
    split_regions,
)

OUT = Path("/tmp/doc-out")


def pp(text: str, out_dir: Path = OUT) -> str:
    return postprocess(text, out_dir).markdown


def kinds(text: str) -> list[tuple[str, int, int]]:
    return [(r.kind, r.start, r.end) for r in split_regions(text)]


# ------------------------------------------------------------------ step 1


def test_line_endings_bom_and_utf8() -> None:
    assert normalize_text("﻿a\r\nb\rc\n".encode()) == "a\nb\nc\n"
    assert normalize_text("é".encode()) == "é"
    with pytest.raises(PostprocessError, match="not valid UTF-8"):
        normalize_text(b"caf\xe9")


# ------------------------------------------------------------- step 2 + 3


def test_trailing_whitespace_removed_outside_protected() -> None:
    assert pp("Text   \n\t\nMore\t \n") == "Text\n\nMore\n"


def test_blank_lines_collapsed_to_two() -> None:
    assert pp("a\n\n\n\n\nb\n\n\nc\n") == "a\n\n\nb\n\n\nc\n"
    # two blank lines = three newlines; three or more blank lines become two


def test_blank_lines_inside_code_kept() -> None:
    text = "```\nx\n\n\n\n\ny   \n```\n"
    assert pp(text) == text


# -------------------------------------------------------- protected regions


FENCED = "``` xml\n<tt>   \n\n\n\n  <body/>\t\n</tt>\n```"
TILDE = "~~~~\ncode  \n~~~ not closing (too short)\n~~~~"
INDENTED = "    <tt>   \n\n\n\n      <body/>  \n    </tt>"
PIPE = "| Code | Meaning   |\n|------|-----------|\n| 14   | RCL       |"
PIPE_NO_OUTER = "Code | Meaning  \n---- | -------\n14 | RCL  "
HTML_TABLE = (
    "<table>  \n<tr>\n<td>14  </td>\n</tr>\n\n\n\n"
    "<tr><td><table><tr><td>nested </td></tr></table></td></tr>\n</table>"
)
HTML_PRE = "<pre>  a  \n\n\n\n  b  \n</pre>"


@pytest.mark.parametrize(
    "block, kind",
    [
        (FENCED, "fence"),
        (TILDE, "fence"),
        (INDENTED, "indented"),
        (PIPE, "pipe_table"),
        (PIPE_NO_OUTER, "pipe_table"),
        (HTML_TABLE, "html_table"),
        (HTML_PRE, "html_pre"),
    ],
)
def test_protected_block_unchanged(block: str, kind: str) -> None:
    text = f"Intro   \n\n{block}\n\nOutro   \n"
    out = pp(text)
    assert f"\n{block}\n" in out, out
    assert out.startswith("Intro\n") and out.endswith("Outro\n")
    assert kind in [k for k, _, _ in kinds(text)]


def test_unclosed_fence_protects_to_end() -> None:
    text = "a  \n```\ncode   \n\n\n\n\nmore   \n"
    assert pp(text) == "a\n```\ncode   \n\n\n\n\nmore   \n"


def test_unclosed_html_table_protects_to_end() -> None:
    text = "<table>\n<tr><td>x  </td></tr>\n\n\n\n"
    assert pp(text) == text


def test_inline_mentions_of_table_do_not_start_a_region() -> None:
    text = "The \\<table\\> element   \n\nmore   \n"
    assert pp(text) == "The \\<table\\> element\n\nmore\n"
    assert all(k == "text" for k, _, _ in kinds(text))


def test_indented_line_after_text_is_not_code() -> None:
    # CommonMark: an indented line cannot interrupt a paragraph
    assert pp("para\n    continued   \n") == "para\n    continued\n"


def test_dash_rule_is_not_a_table() -> None:
    assert all(k == "text" for k, _, _ in kinds("Heading\n---\n\ntext\n"))


def test_regions_cover_every_line_once() -> None:
    text = f"a\n\n{FENCED}\n\n{PIPE}\n\n{INDENTED}\n\n{HTML_TABLE}\nend\n"
    regions = split_regions(text)
    assert regions[0].start == 0 and regions[-1].end == len(text.split("\n"))
    for a, b in itertools.pairwise(regions):
        assert a.end == b.start


def test_random_documents_keep_protected_regions_byte_identical() -> None:
    """Shuffle protected blocks between messy text; every block must survive exactly."""
    rng = random.Random(1234)
    blocks = [FENCED, TILDE, INDENTED, PIPE, HTML_TABLE, HTML_PRE]
    for _ in range(200):
        chosen = rng.sample(blocks, k=rng.randint(1, len(blocks)))
        parts = []
        for block in chosen:
            parts.append("Some text" + " " * rng.randint(0, 3) + "\n" * rng.randint(2, 5))
            parts.append(block + "\n" + "\n" * rng.randint(1, 4))
        out = pp("".join(parts))
        for block in chosen:
            assert block in out


# ------------------------------------------------------------------ step 4


def test_absolute_media_links_made_relative(tmp_path: Path) -> None:
    out_dir = tmp_path / "doc"
    img = out_dir / "media" / "a b.png"
    text = (
        f"![x]({out_dir}/media/fig.png) and [y](file://{out_dir}/media/t.svg)\n"
        f'<img src="{img}" />\n'
        "![web](https://example.org/i.png) ![rel](media/r.png) [anchor](#sec-1)\n"
        "![outside](/etc/hosts)\n"
    )
    assert pp(text, out_dir) == (
        "![x](media/fig.png) and [y](media/t.svg)\n"
        '<img src="media/a b.png" />\n'
        "![web](https://example.org/i.png) ![rel](media/r.png) [anchor](#sec-1)\n"
        "![outside](/etc/hosts)\n"
    )


def test_media_link_inside_protected_region_left_and_warned(tmp_path: Path) -> None:
    text = f'<table>\n<tr><td><img src="{tmp_path}/media/x.png"></td></tr>\n</table>\n'
    result = postprocess(text, tmp_path)
    assert result.markdown == text
    assert len(result.warnings) == 1 and "protected html_table" in result.warnings[0]


def test_bracketed_target_with_spaces(tmp_path: Path) -> None:
    line = f"![a](<{tmp_path}/media/my fig.png>)"
    assert rewrite_media_links(line, tmp_path) == "![a](<media/my fig.png>)"
