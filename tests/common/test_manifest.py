from __future__ import annotations

import json
from pathlib import Path

import pytest
from common.manifest import (
    Manifest,
    ManifestEntry,
    ManifestError,
    read_manifest,
    write_manifest,
)


def entry(doc_id: str, **kw) -> ManifestEntry:
    defaults = dict(source=f"{doc_id}.pdf", source_type="pdf", status="ok")
    return ManifestEntry(id=doc_id, **{**defaults, **kw})


def test_round_trip(tmp_path: Path) -> None:
    m = Manifest(entries=[
        entry("b", converter="docling", converter_version="2.151.0",
              tool_versions={"docling": "2.151.0"}, flags=["mixed_text_layer"],
              warnings=["w"], duration_s=1.5, source_sha256="ab" * 32),
        entry("a", source="https://example.org/x", source_type="web", status="failed", error="boom"),
    ], generator="specconv 0.1.0", generated_at="2026-11-19T10:00:00Z")
    path = tmp_path / "out" / "manifest.json"
    write_manifest(m, path)
    loaded = read_manifest(path)
    assert [e.id for e in loaded.entries] == ["a", "b"]  # written sorted by id
    assert loaded.get("b") == m.get("b")
    assert loaded.get("a").error == "boom"
    assert loaded.generator == "specconv 0.1.0"
    assert not list(path.parent.glob("*.tmp"))


def test_output_is_deterministic(tmp_path: Path) -> None:
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    write_manifest(Manifest(entries=[entry("x"), entry("y")]), a)
    write_manifest(Manifest(entries=[entry("y"), entry("x")]), b)
    assert a.read_bytes() == b.read_bytes()


def test_upsert_replaces_same_id() -> None:
    m = Manifest(entries=[entry("x")])
    m.upsert(entry("x", status="failed"))
    m.upsert(entry("y"))
    assert [(e.id, e.status) for e in m.entries] == [("x", "failed"), ("y", "ok")]


@pytest.mark.parametrize("kw, message", [
    ({"status": "done"}, "unknown status"),
    ({"source_type": "doc"}, "unknown source_type"),
])
def test_invalid_values_rejected(kw: dict, message: str) -> None:
    with pytest.raises(ManifestError, match=message):
        entry("x", **kw)


def test_empty_id_rejected() -> None:
    with pytest.raises(ManifestError, match="empty id"):
        entry("")


def _doc(**extra) -> dict:
    return {"id": "x", "source": "x.pdf", "source_type": "pdf", "status": "ok", **extra}


@pytest.mark.parametrize("data, message", [
    ({"schema_version": 99, "documents": []}, "schema_version"),
    ({"schema_version": 1, "documents": {}}, "must be a list"),
    ({"schema_version": 1, "documents": [_doc(), _doc()]}, "duplicate ids"),
    ({"schema_version": 1, "documents": [_doc(colour="red")]}, "unknown fields"),
    ({"schema_version": 1, "documents": [{"id": "x"}]}, "invalid manifest entry"),
    ([], "JSON object"),
])
def test_invalid_files_rejected(tmp_path: Path, data, message: str) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ManifestError, match=message):
        read_manifest(path)


def test_invalid_json_rejected(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ManifestError, match="invalid JSON"):
        read_manifest(path)
