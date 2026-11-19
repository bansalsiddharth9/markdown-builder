"""--split LEVEL (design: Output layout; build phase 7)."""

from __future__ import annotations

from pathlib import Path

import yaml
from common.frontmatter import parse_document
from common.manifest import read_manifest
from specconv import cli
from specconv.split import split_sections
from typer.testing import CliRunner

BODY = """Intro text before any heading.

# <a id="sec-1"></a>1 Scope

See [section 2](#sec-2) and [here](#sec-1-1) and ![fig](media/a.png).

```
# not a heading
[x](#sec-2)
```

## <a id="sec-1-1"></a>1.1 Conformance

Text <a href="#sec-2">two</a> <img src="media/b.png">.

| A | B |
|---|---|
| [t](#sec-1) | 1 |

# <a id="sec-2"></a>2 Control codes

Back to [scope](#sec-1), [nowhere](#missing), [web](https://x.org/#a).

# Introduction

Plain.

# Introduction

Again.
"""


def sections(level: int = 1):
    return split_sections(BODY, level)


def test_split_at_level_and_above() -> None:
    result = sections(1)
    assert [s.title for s in result.sections] == [
        "1 Scope",
        "2 Control codes",
        "Introduction",
        "Introduction",
    ]
    assert [s.number for s in result.sections] == ["1", "2", None, None]
    assert result.preamble.strip() == "Intro text before any heading."
    # level 2 also starts a file at "## 1.1"
    assert [s.title for s in sections(2).sections][:3] == [
        "1 Scope",
        "1.1 Conformance",
        "2 Control codes",
    ]


def test_file_names() -> None:
    names = [s.filename for s in sections(2).sections]
    assert names == [
        "01-scope.md",
        "01-1-conformance.md",
        "02-control-codes.md",
        "s04-introduction.md",
        "s05-introduction.md",
    ]


def test_heading_in_code_block_is_not_a_section() -> None:
    first = sections(1).sections[0]
    assert "# not a heading" in first.body


def test_links_rewritten_across_sections() -> None:
    result = sections(2)
    scope, conformance, codes = result.sections[:3]
    assert "[section 2](02-control-codes.md#sec-2)" in scope.body
    assert "[here](01-1-conformance.md#sec-1-1)" in scope.body
    assert '<a href="02-control-codes.md#sec-2">two</a>' in conformance.body
    assert "| [t](01-scope.md#sec-1) | 1 |" in conformance.body  # links in tables too
    assert "[scope](01-scope.md#sec-1)" in codes.body
    assert "[nowhere](#missing)" in codes.body  # unknown target: left for V7
    assert "[web](https://x.org/#a)" in codes.body


def test_same_section_links_stay_and_code_untouched() -> None:
    result = sections(1)
    scope = result.sections[0]
    assert "[here](#sec-1-1)" in scope.body  # 1.1 is inside "1 Scope" at level 1
    assert "[x](#sec-2)\n```" in scope.body  # inside code: unchanged


def test_media_paths_point_up_one_folder() -> None:
    scope, conformance = sections(2).sections[:2]
    assert "![fig](../media/a.png)" in scope.body
    assert '<img src="../media/b.png">' in conformance.body


def test_index_lists_sections() -> None:
    index = sections(2).index_body("spec")
    assert "Intro text before any heading." in index
    assert "- [1 Scope](01-scope.md)\n  - [1.1 Conformance](01-1-conformance.md)" in index
    assert "- [2 Control codes](02-control-codes.md)" in index


# ------------------------------------------------------------ end to end

runner = CliRunner()
REPO_CONFIG = Path(__file__).resolve().parents[2] / "config" / "specconv.yaml"


def convert(input_dir: Path, out: Path, *extra: str):
    args = ["convert", "--input", str(input_dir), "--output", str(out)]
    args += ["--work", str(out.parent / "w"), "--config", str(REPO_CONFIG), *extra]
    return runner.invoke(cli.app, args)


def test_convert_with_split_writes_sections(input_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "out"
    assert convert(input_dir, out, "--split", "2").exit_code == 0
    doc = out / "sample-spec-2"  # the HTML fixture
    files = sorted(p.name for p in (doc / "sections").iterdir())
    assert "index.md" in files and "05-1-roll-up-captions.md" in files
    text = (doc / "sections" / "05-1-roll-up-captions.md").read_text(encoding="utf-8")
    head, body = text.split("\n---\n", 1)
    meta = yaml.safe_load(head.removeprefix("---\n"))
    assert meta["parent"] == "sample-spec-2" and meta["section"] == "5.1"
    assert meta["title"] == "5.1 Roll-up captions" and meta["parent_file"] == "../sample-spec-2.md"
    assert "5.1.1 Base row" in body  # deeper headings stay in their section
    full_with_split = parse_document((doc / "sample-spec-2.md").read_text(encoding="utf-8"))[1]
    conversion = (doc / "conversion.json").read_text(encoding="utf-8")
    assert '"split"' in conversion
    entry = read_manifest(out / "manifest.json").get("sample-spec-2")
    assert entry.options["split"] == 2

    # without --split: converted again (option changed), no sections folder
    assert convert(input_dir, out).exit_code == 0
    assert not (doc / "sections").exists()
    assert (
        parse_document((doc / "sample-spec-2.md").read_text(encoding="utf-8"))[1] == full_with_split
    )


def test_links_and_ids_in_nested_list_items_are_not_code() -> None:
    """Pandoc indents nested list content by 4 spaces; that is not a code block."""
    body = (
        '# 1 Terms\n\n- term\n\n    <span id="t-a"></span>Anonymous span. See [b](#t-b).\n\n'
        "    ```\n    [kept](#t-b)\n    ```\n\n"
        '# 2 More\n\n- other\n\n    <span id="t-b"></span>Back to [a](#t-a).\n'
    )
    first, second = split_sections(body, 1).sections
    assert "See [b](02-more.md#t-b)" in first.body
    assert "    [kept](#t-b)\n" in first.body  # inside the fenced block: unchanged
    assert "Back to [a](01-terms.md#t-a)" in second.body
