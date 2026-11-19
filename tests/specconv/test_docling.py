"""PDF conversion with Docling, end to end through `specconv convert`
(design: Converter tests; build phase 3).

Docling is slow, so one module-scoped run converts all PDF fixtures and
most tests inspect its output.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest
from common.frontmatter import parse_document
from common.manifest import read_manifest
from specconv import cli
from typer.testing import CliRunner

runner = CliRunner()
REPO_CONFIG = Path(__file__).resolve().parents[2] / "config" / "specconv.yaml"


def convert(root: Path, *extra: str, config: Path = REPO_CONFIG):
    return runner.invoke(
        cli.app,
        [
            "convert",
            "--input",
            str(root / "input"),
            "--output",
            str(root / "output"),
            "--work",
            str(root / "work"),
            "--config",
            str(config),
            *extra,
        ],
    )


def body(root: Path, doc_id: str) -> str:
    md = root / "output" / doc_id / f"{doc_id}.md"
    return parse_document(md.read_text(encoding="utf-8"))[1]


def squash(text: str) -> str:
    return " ".join(text.split())


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory, pdf_fixtures: dict) -> tuple[Path, object]:
    root = tmp_path_factory.mktemp("docling")
    (root / "input").mkdir()
    for name in ("pdf", "image_pdf", "mostly_text_pdf", "mostly_image_pdf"):
        shutil.copy(pdf_fixtures[name], root / "input" / pdf_fixtures[name].name)
    return root, convert(root)


@pytest.fixture(scope="module")
def manifest(run):
    root, _ = run
    return read_manifest(root / "output" / "manifest.json")


# ---------------------------------------------------------------- routing


def test_exit_code_reports_no_text_layer(run) -> None:
    _, result = run
    assert result.exit_code == 1, result.output  # no_text_layer counts as a failure


def test_routing(manifest) -> None:
    status = {e.id: (e.status, e.route, e.flags) for e in manifest.entries}
    assert status == {
        "sample-spec": ("ok", "docling", []),
        "mostly-text": ("ok", "docling", ["mixed_text_layer"]),
        "image-only": ("no_text_layer", "none:no_text_layer", []),
        "mostly-image": ("no_text_layer", "none:no_text_layer", ["mixed_text_layer"]),
    }


def test_no_text_layer_is_reported_not_converted(run, manifest) -> None:
    root, _ = run
    entry = manifest.get("mostly-image")
    assert "1 of 4 pages" in entry.error and "need 70%" in entry.error
    assert not (root / "output" / "mostly-image").exists()
    assert not (root / "output" / "image-only").exists()


def test_mixed_flag_in_front_matter_and_warning(run, manifest) -> None:
    root, _ = run
    md = (root / "output" / "mostly-text" / "mostly-text.md").read_text(encoding="utf-8")
    assert parse_document(md)[0].flags == ["mixed_text_layer"]
    assert any("text layer on 3 of 4 pages" in w for w in manifest.get("mostly-text").warnings)


# ----------------------------------------------------------------- output


def test_manifest_and_front_matter(run, manifest) -> None:
    root, _ = run
    entry = manifest.get("sample-spec")
    assert (
        entry.converter == "docling" and entry.tool_versions["docling"] == entry.converter_version
    )
    assert entry.options["converter"]["models"]["docling-project/docling-layout-heron"].startswith(
        "8f39ad3c"
    )
    fm, _ = parse_document(
        (root / "output" / "sample-spec" / "sample-spec.md").read_text(encoding="utf-8")
    )
    assert fm.source_type == "pdf" and fm.converted_with.startswith("docling ")
    assert (root / "output" / "sample-spec" / "source.pdf").is_file()
    assert (root / "work" / "sample-spec.docling.json").is_file()


def test_headings_and_paragraphs(run, fixture_mod: dict) -> None:
    root, _ = run
    md = squash(body(root, "sample-spec"))
    for block in fixture_mod["SPEC"]:
        if isinstance(block, fixture_mod["Heading"]):
            assert block.text in md
        elif isinstance(block, fixture_mod["Para"]):
            # Known Docling limitation: its PDF parser turns the en dash into "-".
            assert squash(block.text.replace("–", "-")) in md


def test_merged_cell_table_is_html_with_spans_and_no_repeats(run) -> None:
    root, _ = run
    md = body(root, "sample-spec")
    assert '<th colspan="2">Byte pair</th>' in md
    assert '<th rowspan="2">Command</th>' in md and '<th rowspan="2">Meaning</th>' in md
    assert md.count("Byte pair") == 1  # never repeated into the cells it covers
    assert md.count("Table 1 - Miscellaneous control codes") == 1  # caption once, in the table
    for cell in ("<td>2C</td>", "<td>EDM</td>", "<td>Erase displayed memory</td>"):
        assert cell in md


def test_table_without_spans_stays_pipe_table(run) -> None:
    root, _ = run
    md = body(root, "sample-spec")
    assert re.search(r"^\| Columns\s+\| 32 columns\s+\|$", md, re.M)


def test_picture_in_media(run) -> None:
    root, _ = run
    md = body(root, "sample-spec")
    media = sorted((root / "output" / "sample-spec" / "media").glob("*.png"))
    assert len(media) == 1
    assert f"](media/{media[0].name})" in md


def test_xml_example_text_complete_and_in_order(run, fixture_mod: dict) -> None:
    root, _ = run
    assert squash(fixture_mod["TTML_EXAMPLE"]) in squash(body(root, "sample-spec"))


def test_values_preserved(run) -> None:
    root, _ = run
    md = body(root, "sample-spec")
    for token in ("0x14", "2Ch", "9420", "01:00:00;02", "29.97 fps", "15 rows", "Café, naïve"):
        assert token in md


def test_page_furniture_listed_in_conversion_json(run, manifest) -> None:
    """Docling leaves page headers/footers out of the Markdown; the fixture's
    WebVTT header falls at the bottom of page 1 and is dropped as a footer."""
    root, _ = run
    conversion = json.loads(
        (root / "output" / "sample-spec" / "conversion.json").read_text(encoding="utf-8")
    )
    dropped = conversion["docling"]["dropped_as_page_furniture"]
    assert {"pages": [1], "label": "page_footer", "text": "WEBVTT"} in dropped
    assert conversion["text_layer"] == {
        "pages": 2,
        "text_pages": 2,
        "ratio": 1.0,
        "pages_without_text": [],
    }
    assert conversion["docling"]["tables_as_html"] == 1
    assert any("page headers or footers" in w for w in manifest.get("sample-spec").warnings)


@pytest.mark.xfail(strict=True, reason="known Docling limitation: line breaks in code are lost")
def test_code_block_line_breaks_kept(run, fixture_mod: dict) -> None:
    root, _ = run
    assert fixture_mod["TTML_EXAMPLE"] in body(root, "sample-spec")


# ------------------------------------------------------------ incremental


@pytest.fixture
def small(tmp_path: Path, pdf_fixtures: dict) -> Path:
    (tmp_path / "input").mkdir()
    shutil.copy(pdf_fixtures["pdf"], tmp_path / "input" / "spec.pdf")
    assert convert(tmp_path).exit_code == 0
    return tmp_path


def statuses(root: Path) -> dict[str, str]:
    manifest = read_manifest(root / "output" / "manifest.json")
    return {e.id: e.status for e in manifest.entries}


def test_unchanged_pdf_skipped_then_forced(small: Path) -> None:
    first = body(small, "spec")
    result = convert(small)
    assert result.exit_code == 0 and statuses(small) == {"spec": "skipped_unchanged"}
    # skipping keeps the previous entry's details
    assert read_manifest(small / "output" / "manifest.json").get("spec").converter == "docling"
    assert convert(small).exit_code == 0 and statuses(small) == {"spec": "skipped_unchanged"}
    assert convert(small, "--force").exit_code == 0 and statuses(small) == {"spec": "ok"}
    assert body(small, "spec") == first  # deterministic


def test_changed_source_or_option_converts_again(small: Path, write) -> None:
    pdf = small / "input" / "spec.pdf"
    pdf.write_bytes(pdf.read_bytes() + b"\n%% appended comment\n")
    assert convert(small).exit_code == 0 and statuses(small) == {"spec": "ok"}
    config = write(
        "c.yaml",
        REPO_CONFIG.read_text(encoding="utf-8")
        .replace("min_text_page_ratio: 0.7", "min_text_page_ratio: 0.6")
        .replace("models_path: ../models", f"models_path: {REPO_CONFIG.parent.parent / 'models'}")
        .replace("browsers_path: ../.playwright", "browsers_path: /nonexistent"),
    )
    assert convert(small, config=config).exit_code == 0 and statuses(small) == {"spec": "ok"}


def test_failed_documents_are_retried(small: Path) -> None:
    manifest_path = small / "output" / "manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["documents"][0]["status"] = "failed"
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    assert convert(small).exit_code == 0 and statuses(small) == {"spec": "ok"}
