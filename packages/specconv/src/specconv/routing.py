"""Find inputs, give each a stable id, and pick a converter (design: Inputs, Routing)."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from specconv.config import Source

CONVERTIBLE = {".html": "html", ".htm": "html", ".docx": "docx", ".pdf": "pdf"}
UNSUPPORTED = {".doc"}  # legacy Word: reported as unsupported, not converted


@dataclass(frozen=True)
class InputDoc:
    """One document to process: a local file or a web source."""

    id: str
    source_type: str  # pdf | html | docx | web | doc (unsupported)
    path: Path | None  # local file (None for web sources)
    rel_path: str | None  # path relative to the input folder, POSIX style
    source: Source | None = None  # web sources only

    @property
    def display_source(self) -> str:
        if self.source is not None:
            return self.source.url
        assert self.rel_path is not None
        return self.rel_path


@dataclass(frozen=True)
class ScanResult:
    docs: list[InputDoc]
    skipped: list[str]  # files with other extensions, relative paths


def slugify(name: str) -> str:
    """'CTA-608-E' -> 'cta-608-e'; 'Spec v1.2 (draft)' -> 'spec-v1-2-draft'."""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "document"


def _unique(slug: str, taken: set[str]) -> str:
    if slug not in taken:
        return slug
    n = 2
    while f"{slug}-{n}" in taken:
        n += 1
    return f"{slug}-{n}"


def scan(
    input_dir: Path | None,
    sources: list[Source],
    previous_ids: dict[str, str] | None = None,
    reserved: set[str] | None = None,
) -> ScanResult:
    """List all inputs with ids.

    Ids must stay stable across runs, or a document could be written into
    another document's folder and inherit its hand-edited front matter:

    - Web sources keep their sources.yaml ids.
    - A local file keeps the id recorded for its path in the previous
      manifest (previous_ids: relative path -> id).
    - A new file gets a slug of its file name, with a numeric suffix if the
      slug is used by another input, a previous manifest entry, or any other
      name in `reserved` (e.g. existing output folders). Files are visited in
      sorted path order, so the result does not depend on directory order.
    """
    previous_ids = previous_ids or {}
    taken = {s.id for s in sources} | set(previous_ids.values()) | (reserved or set())
    skipped: list[str] = []
    found: list[tuple[Path, str, str]] = []  # (path, rel, kind)
    if input_dir is not None:
        files = sorted(
            (p for p in input_dir.rglob("*") if p.is_file()),
            key=lambda p: p.relative_to(input_dir).as_posix(),
        )
        for path in files:
            rel = path.relative_to(input_dir).as_posix()
            if any(part.startswith(".") for part in path.relative_to(input_dir).parts):
                continue  # hidden files and folders, e.g. .gitkeep, .DS_Store
            ext = path.suffix.lower()
            if ext in CONVERTIBLE:
                found.append((path, rel, CONVERTIBLE[ext]))
            elif ext in UNSUPPORTED:
                found.append((path, rel, "doc"))
            else:
                skipped.append(rel)

    web_ids = {s.id for s in sources}
    ids: dict[str, str] = {}
    for _, rel, _ in found:  # first: files the previous manifest already knows
        old = previous_ids.get(rel)
        if old is not None and old not in web_ids and old not in ids.values():
            ids[rel] = old
    for path, rel, _ in found:  # then: new files
        if rel not in ids:
            ids[rel] = _unique(slugify(path.stem), taken)
            taken.add(ids[rel])

    docs = [
        InputDoc(id=ids[rel], source_type=kind, path=path, rel_path=rel)
        for path, rel, kind in found
    ]
    docs += [
        InputDoc(id=s.id, source_type="web", path=None, rel_path=None, source=s) for s in sources
    ]
    return ScanResult(docs=docs, skipped=skipped)


@dataclass(frozen=True)
class Route:
    name: str  # recorded in the manifest, e.g. "pandoc:html"
    converter: str | None  # "pandoc", "docling", or None when not converted
    from_format: str | None = None


def route(doc: InputDoc) -> Route:
    if doc.source_type in ("html", "web"):
        return Route("pandoc:html", "pandoc", "html")
    if doc.source_type == "docx":
        return Route("pandoc:docx", "pandoc", "docx")
    if doc.source_type == "pdf":
        return Route("docling", "docling")  # text-layer routing: phase 3
    return Route("none:unsupported", None)
