"""End-to-end `specconv convert` for HTML and DOCX: output layout, front
matter, carry-over on re-conversion, manifest, failures and exit codes."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest
from common.frontmatter import parse_document, render_document
from common.manifest import read_manifest
from specconv import cli
from specconv.config import Source
from specconv.routing import scan, slugify
from typer.testing import CliRunner

runner = CliRunner()

# Inputs are visited in sorted path order: sample-spec.docx comes before
# sample-spec.html, so the DOCX gets the plain slug and the HTML the suffix.
DOCX_ID, HTML_ID = "sample-spec", "sample-spec-2"


@pytest.fixture(autouse=True)
def in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


def convert(*extra: str):
    return runner.invoke(
        cli.app, ["convert", "--input", "input", "--output", "output", "--work", "work", *extra]
    )


def md_path(doc_id: str) -> Path:
    return Path("output") / doc_id / f"{doc_id}.md"


def edit_front_matter(doc_id: str, **changes) -> None:
    fm, body = parse_document(md_path(doc_id).read_text(encoding="utf-8"))
    for key, value in changes.items():
        setattr(fm, key, value)
    md_path(doc_id).write_text(render_document(fm, body), encoding="utf-8")


# ---------------------------------------------------------------- layout


def test_output_layout_and_manifest(input_dir: Path) -> None:
    result = convert()
    assert result.exit_code == 0, result.output
    html_dir, docx_dir = Path("output") / HTML_ID, Path("output") / DOCX_ID
    assert sorted(p.name for p in html_dir.iterdir()) == [
        "conversion.json",
        "media",
        f"{HTML_ID}.md",
        "source.html",
    ]
    assert sorted(p.name for p in docx_dir.iterdir()) == [
        "conversion.json",
        "media",
        f"{DOCX_ID}.md",
        "source.docx",
    ]
    assert (html_dir / "source.html").read_bytes() == (input_dir / "sample-spec.html").read_bytes()
    assert (docx_dir / "source.docx").read_bytes() == (input_dir / "sample-spec.docx").read_bytes()
    assert (html_dir / "media" / "safe-area.png").exists()

    manifest = read_manifest(Path("output/manifest.json"))
    assert [(e.id, e.source, e.status, e.route) for e in manifest.entries] == [
        (DOCX_ID, "sample-spec.docx", "ok", "pandoc:docx"),
        (HTML_ID, "sample-spec.html", "ok", "pandoc:html"),
    ]
    entry = manifest.get(HTML_ID)
    assert entry.converter == "pandoc" and entry.converter_version.startswith("3.")
    assert entry.tool_versions["pandoc"] == entry.converter_version
    assert (
        entry.source_sha256
        == hashlib.sha256((input_dir / "sample-spec.html").read_bytes()).hexdigest()
    )
    assert entry.options_hash and entry.duration_s is not None
    assert list(Path("output").glob("run-*.log"))
    assert not list(Path("output").glob(".*"))  # no temp folders left behind

    conversion = json.loads((html_dir / "conversion.json").read_text(encoding="utf-8"))
    assert conversion["tool"] == "pandoc" and conversion["media"] == ["media/safe-area.png"]
    assert conversion["options"]["strip_tags"] == ["script", "style", "noscript"]


def test_front_matter(input_dir: Path) -> None:
    convert()
    fm, body = parse_document(md_path(DOCX_ID).read_text(encoding="utf-8"))
    assert fm.id == DOCX_ID and fm.source_type == "docx"
    assert fm.source_file == "sample-spec.docx" and fm.source_url is None
    assert (
        fm.source_sha256
        == hashlib.sha256((input_dir / "sample-spec.docx").read_bytes()).hexdigest()
    )
    assert fm.converted_with.startswith("pandoc 3.")
    assert fm.title == "" and fm.validated is None and fm.flags == []
    assert body.startswith("# Sample Caption Display Specification\n")


def test_subfolders_and_source_file(input_dir: Path) -> None:
    (input_dir / "w3c").mkdir()
    shutil.move(input_dir / "sample-spec.docx", input_dir / "w3c" / "Draft Spec v1.2.docx")
    assert convert().exit_code == 0
    fm, _ = parse_document(md_path("draft-spec-v1-2").read_text(encoding="utf-8"))
    assert fm.source_file == "w3c/Draft Spec v1.2.docx"


# ------------------------------------------------------------- carry-over


def test_hand_edited_fields_carried_over(input_dir: Path) -> None:
    convert()
    validated = {"date": "2026-11-19", "validator": "specval 0.1.0", "by": "Sam"}
    edit_front_matter(
        "sample-spec",
        title="Sample",
        spec_version="1.0",
        manual_fixes=["fixed table 1"],
        validated=validated,
    )
    assert convert().exit_code == 0
    fm, _ = parse_document(md_path("sample-spec").read_text(encoding="utf-8"))
    assert (fm.title, fm.spec_version, fm.manual_fixes, fm.validated) == (
        "Sample",
        "1.0",
        ["fixed table 1"],
        validated,
    )


def test_validated_reset_when_body_changes(input_dir: Path) -> None:
    convert()
    edit_front_matter(
        HTML_ID,
        title="Sample",
        manual_fixes=["x"],
        validated={"date": "2026-11-19", "validator": "specval 0.1.0", "by": "S"},
    )
    html = input_dir / "sample-spec.html"
    html.write_text(
        html.read_text(encoding="utf-8").replace("</body>", "<p>A new paragraph.</p></body>"),
        encoding="utf-8",
    )
    assert convert().exit_code == 0
    fm, body = parse_document(md_path(HTML_ID).read_text(encoding="utf-8"))
    assert "A new paragraph." in body
    assert fm.title == "Sample" and fm.validated is None
    warnings = read_manifest(Path("output/manifest.json")).get(HTML_ID).warnings
    assert any("'validated' reset" in w for w in warnings)
    assert any("manual_fixes" in w for w in warnings)


def test_unreadable_existing_front_matter_fails_without_overwriting(input_dir: Path) -> None:
    convert()
    md_path("sample-spec").write_text("---\nid: [broken\n---\nhand edited\n", encoding="utf-8")
    result = convert()
    assert result.exit_code == 1
    assert md_path("sample-spec").read_text(encoding="utf-8").endswith("hand edited\n")
    entry = read_manifest(Path("output/manifest.json")).get("sample-spec")
    assert entry.status == "failed" and "fix or delete" in entry.error
    assert read_manifest(Path("output/manifest.json")).get("sample-spec-2").status == "ok"


def test_reconversion_replaces_folder(input_dir: Path) -> None:
    convert()
    (Path("output/sample-spec/media/stale.png")).write_bytes(b"old")
    convert()
    assert not Path("output/sample-spec/media/stale.png").exists()


def test_failed_reconversion_keeps_previous_output(input_dir: Path) -> None:
    convert()
    before = md_path(DOCX_ID).read_bytes()
    (input_dir / "sample-spec.docx").write_bytes(b"not a zip file")
    result = convert()
    assert result.exit_code == 1
    assert md_path(DOCX_ID).read_bytes() == before
    entry = read_manifest(Path("output/manifest.json")).get(DOCX_ID)
    assert entry.status == "failed" and "pandoc exited" in entry.error
    assert not list(Path("output").glob(".*"))


# ------------------------------------------------------ statuses and ids


def test_unsupported_skipped_pdf_and_exit_code(input_dir: Path) -> None:
    (input_dir / "legacy.doc").write_bytes(b"x")
    (input_dir / "notes.txt").write_text("x", encoding="utf-8")
    (input_dir / "scan.pdf").write_bytes(b"%PDF-1.4")
    result = convert()
    assert result.exit_code == 1  # the PDF fails until phase 3
    manifest = read_manifest(Path("output/manifest.json"))
    assert manifest.get("legacy").status == "unsupported"
    assert manifest.get("legacy").source_type == "doc"
    assert manifest.get("scan").status == "failed" and "phase 3" in manifest.get("scan").error
    assert manifest.get("notes") is None
    assert "skipped (not a PDF, HTML or DOCX file): notes.txt" in result.output


def test_only_converts_one_and_keeps_other_entries(input_dir: Path) -> None:
    convert()
    shutil.rmtree("output/sample-spec")
    result = convert("--only", "sample-spec-2")
    assert result.exit_code == 0
    assert not Path("output/sample-spec").exists()
    assert read_manifest(Path("output/manifest.json")).get("sample-spec").status == "ok"


def test_only_unknown_id_exits_2(input_dir: Path) -> None:
    result = convert("--only", "nope")
    assert result.exit_code == 2 and "sample-spec-2" in result.output


def test_full_run_drops_entries_of_removed_inputs(input_dir: Path) -> None:
    convert()
    (input_dir / "sample-spec.docx").unlink()
    result = convert()
    assert read_manifest(Path("output/manifest.json")).get(DOCX_ID) is None
    assert "input no longer present" in result.output


def test_corrupt_manifest_exits_2(input_dir: Path) -> None:
    Path("output").mkdir()
    Path("output/manifest.json").write_text("{", encoding="utf-8")
    assert convert().exit_code == 2


def test_split_not_yet_available(input_dir: Path) -> None:
    result = convert("--split", "2")
    assert result.exit_code == 2 and "phase 7" in result.output


def test_web_sources_fail_until_fetching_exists(input_dir: Path, write) -> None:
    write("s.yaml", "sources:\n  - {id: ttml2, url: https://www.w3.org/TR/ttml2/}\n")
    result = convert("--sources", "s.yaml")
    assert result.exit_code == 1
    entry = read_manifest(Path("output/manifest.json")).get("ttml2")
    assert entry.status == "failed" and "phase 4" in entry.error
    assert entry.source == "https://www.w3.org/TR/ttml2/"


@pytest.mark.parametrize(
    "name, slug",
    [
        ("CTA-608-E", "cta-608-e"),
        ("Spec v1.2 (draft)", "spec-v1-2-draft"),
        ("Café_Spec", "cafe-spec"),
        ("---", "document"),
    ],
)
def test_slugify(name: str, slug: str) -> None:
    assert slugify(name) == slug


def test_ids_unique_and_web_ids_reserved(tmp_path: Path) -> None:
    folder = tmp_path / "in"
    (folder / "sub").mkdir(parents=True)
    for rel in ("ttml2.html", "a.html", "a.docx", "sub/a.pdf", ".hidden.html"):
        (folder / rel).write_bytes(b"x")
    result = scan(folder, [Source(id="ttml2", url="https://www.w3.org/TR/ttml2/")])
    assert [(d.id, d.rel_path) for d in result.docs] == [
        ("a", "a.docx"),
        ("a-2", "a.html"),
        ("a-3", "sub/a.pdf"),
        ("ttml2-2", "ttml2.html"),
        ("ttml2", None),
    ]


# ------------------------------------------------------------ stable ids


def test_ids_stable_when_files_added_or_removed(input_dir: Path) -> None:
    convert()
    # A new file whose name sorts first must not take an existing id ...
    shutil.copy(input_dir / "sample-spec.html", input_dir / "Sample-Spec.htm")
    assert convert().exit_code == 0
    manifest = read_manifest(Path("output/manifest.json"))
    assert manifest.get(DOCX_ID).source == "sample-spec.docx"
    assert manifest.get(HTML_ID).source == "sample-spec.html"
    assert manifest.get("sample-spec-3").source == "Sample-Spec.htm"
    # ... and removing a file must not shift the others into its folder.
    (input_dir / "sample-spec.docx").unlink()
    assert convert().exit_code == 0
    manifest = read_manifest(Path("output/manifest.json"))
    assert manifest.get(HTML_ID).source == "sample-spec.html"
    assert manifest.get(DOCX_ID) is None


def test_new_file_never_reuses_a_leftover_output_folder(input_dir: Path) -> None:
    convert()
    Path("output/manifest.json").unlink()  # e.g. deleted by hand
    (input_dir / "sample-spec.docx").unlink()
    assert convert().exit_code == 0
    # sample-spec and sample-spec-2 folders exist, so the HTML gets a fresh id
    assert read_manifest(Path("output/manifest.json")).get("sample-spec-3").status == "ok"


def test_scan_reuses_previous_ids(tmp_path: Path) -> None:
    folder = tmp_path / "in"
    folder.mkdir()
    for name in ("a.html", "b.html", "c.html"):
        (folder / name).write_bytes(b"x")
    result = scan(folder, [], previous_ids={"b.html": "a", "gone.html": "c"}, reserved={"old"})
    assert [(d.id, d.rel_path) for d in result.docs] == [
        ("a-2", "a.html"),
        ("a", "b.html"),
        ("c-2", "c.html"),
    ]
