"""Schema and JSON read/write for ``output/manifest.json``.

``specconv`` writes the manifest; ``specval`` reads it to find documents.
Output is deterministic: keys and entries are written in a stable order so
re-runs can be diffed.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

# Bump when the format changes in a way old readers cannot handle; readers
# refuse any other version.
SCHEMA_VERSION = 1

STATUSES = frozenset({"ok", "failed", "no_text_layer", "unsupported", "skipped_unchanged"})
SOURCE_TYPES = frozenset({"pdf", "html", "docx", "web", "doc"})  # doc: unsupported in v1


class ManifestError(ValueError):
    """The manifest file is missing required data or has invalid values."""


@dataclass
class ManifestEntry:
    """One input document and what happened to it."""

    id: str
    source: str  # path relative to the input folder, or the URL for web sources
    source_type: str  # pdf | html | docx | web | doc (not converted)
    status: str  # see STATUSES
    source_sha256: str | None = None  # of the input file (or fetched page)
    route: str | None = None  # e.g. "pandoc:html", "docling", "none:no_text_layer"
    converter: str | None = None  # e.g. "docling"
    converter_version: str | None = None
    tool_versions: dict[str, str] = field(default_factory=dict)  # every tool involved
    options: dict[str, Any] = field(default_factory=dict)  # exact options used
    options_hash: str | None = None  # incremental runs: re-convert when options change
    flags: list[str] = field(default_factory=list)  # e.g. mixed_text_layer
    warnings: list[str] = field(default_factory=list)
    error: str | None = None  # set when status is failed
    duration_s: float | None = None

    def __post_init__(self) -> None:
        """Reject an empty id, unknown status or unknown source_type."""
        if not self.id:
            raise ManifestError("manifest entry has an empty id")
        if self.status not in STATUSES:
            raise ManifestError(
                f"manifest entry {self.id!r}: unknown status {self.status!r}; "
                f"expected one of {sorted(STATUSES)}"
            )
        if self.source_type not in SOURCE_TYPES:
            raise ManifestError(
                f"manifest entry {self.id!r}: unknown source_type {self.source_type!r}; "
                f"expected one of {sorted(SOURCE_TYPES)}"
            )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ManifestEntry:
        """Build an entry from JSON; unknown or missing fields raise ManifestError."""
        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise ManifestError(f"manifest entry has unknown fields: {sorted(unknown)}")
        try:
            return cls(**data)
        except TypeError as exc:  # missing required field
            raise ManifestError(f"invalid manifest entry: {exc}") from exc


@dataclass
class Manifest:
    """All documents of one output folder, keyed by id."""

    entries: list[ManifestEntry] = field(default_factory=list)
    generated_at: str | None = None  # ISO 8601 UTC
    generator: str | None = None  # e.g. "specconv 0.1.0"
    schema_version: int = SCHEMA_VERSION

    def get(self, doc_id: str) -> ManifestEntry | None:
        """The entry with this id, or None."""
        for entry in self.entries:
            if entry.id == doc_id:
                return entry
        return None

    def upsert(self, entry: ManifestEntry) -> None:
        """Replace the entry with the same id, or add it."""
        for i, existing in enumerate(self.entries):
            if existing.id == entry.id:
                self.entries[i] = entry
                return
        self.entries.append(entry)

    def to_dict(self) -> dict[str, Any]:
        """JSON form. Entries are sorted by id so the file is stable across runs."""
        return {
            "schema_version": self.schema_version,
            "generator": self.generator,
            "generated_at": self.generated_at,
            "documents": [asdict(e) for e in sorted(self.entries, key=lambda e: e.id)],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Manifest:
        """Build a manifest from parsed JSON, checking schema version and unique ids."""
        if not isinstance(data, dict):
            raise ManifestError("manifest must be a JSON object")
        version = data.get("schema_version")
        if version != SCHEMA_VERSION:
            raise ManifestError(
                f"unsupported manifest schema_version {version!r}; expected {SCHEMA_VERSION}"
            )
        docs = data.get("documents")
        if not isinstance(docs, list):
            raise ManifestError("manifest 'documents' must be a list")
        entries = [ManifestEntry.from_dict(d) for d in docs]
        ids = [e.id for e in entries]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ManifestError(f"manifest has duplicate ids: {dupes}")
        return cls(
            entries=entries,
            generated_at=data.get("generated_at"),
            generator=data.get("generator"),
            schema_version=version,
        )


def read_manifest(path: Path) -> Manifest:
    """Read a manifest file. Raises FileNotFoundError or ManifestError."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ManifestError(f"{path}: invalid JSON: {exc}") from exc
    return Manifest.from_dict(data)


def write_manifest(manifest: Manifest, path: Path) -> None:
    """Write the manifest atomically (temp file, then rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(manifest.to_dict(), indent=2, ensure_ascii=False, sort_keys=False) + "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")  # manifest.json.tmp, same folder
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
