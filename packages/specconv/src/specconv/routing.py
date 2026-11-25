"""Find inputs, give each a stable id, and pick a converter (design: Inputs, Routing)."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from specconv.config import Source

# .doc (legacy Word) goes through LibreOffice to .docx first (deviation DOC1).
CONVERTIBLE = {".html": "html", ".htm": "html", ".docx": "docx", ".pdf": "pdf", ".doc": "doc"}


@dataclass(frozen=True)
class InputDoc:
    """One document to process: a local file or a web source."""

    id: str
    source_type: str  # pdf | html | docx | web | doc
    path: Path | None  # local file (None for web sources)
    rel_path: str | None  # path relative to the input folder, POSIX style
    source: Source | None = None  # web sources only
    # Id from before NL1 whose output folder must be moved to this id's folder.
    previous_id: str | None = None

    @property
    def display_source(self) -> str:
        """The URL for a web source, else the relative path; used in logs and messages."""
        if self.source is not None:
            return self.source.url
        assert self.rel_path is not None
        return self.rel_path


@dataclass(frozen=True)
class ScanResult:
    """Inputs found by scan, plus files skipped because of their extension."""

    docs: list[InputDoc]
    skipped: list[str]  # files with other extensions, relative paths


def slugify(name: str) -> str:
    """'CTA-608-E' -> 'cta-608-e'; 'Spec v1.2 (draft)' -> 'spec-v1-2-draft'."""
    # NFKD splits accented letters ("é" -> "e" + accent); the accent is then dropped.
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "document"


def file_id(rel: str, slug: str) -> str:
    """The id of a file at rel: its input folder path plus the slug (deviation NL1).

    'MCC/MCC White Paper.pdf' -> 'MCC/mcc-white-paper'; a file directly in the input
    folder keeps a plain slug. Folder names are kept exactly, so the output and
    validation folders mirror the input folders the user organised.
    """
    folder = PurePosixPath(rel).parent
    return slug if folder == PurePosixPath(".") else f"{folder.as_posix()}/{slug}"


def _same_folder(doc_id: str, rel: str) -> bool:
    """True when doc_id already sits in rel's input folder (an id made since NL1)."""
    return PurePosixPath(doc_id).parent == PurePosixPath(rel).parent


def _unique(slug: str, taken: set[str]) -> str:
    """Return slug, or slug-2, slug-3, ... if it is taken. Does not add it to taken."""
    if slug not in taken:
        return slug
    n = 2
    while f"{slug}-{n}" in taken:
        n += 1
    return f"{slug}-{n}"


def _in_saved_page_folder(path: Path, input_dir: Path) -> bool:
    """True for files inside 'X_files/' next to 'X.html' or 'X.htm', the folder a
    browser's "Save page as, complete" writes the page's images into."""
    for parent in path.relative_to(input_dir).parents:
        folder = input_dir / parent
        if folder.name.endswith("_files"):
            stem = folder.name[: -len("_files")]
            if any((folder.parent / f"{stem}{ext}").is_file() for ext in (".html", ".htm")):
                return True
    return False


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
      manifest (previous_ids: relative path -> id), if that id is in the
      file's folder. A flat id from before NL1 is replaced by a new one and
      returned as previous_id, so the pipeline moves its old output folder.
    - A new file gets its folder path plus a slug of its file name (file_id),
      with a numeric suffix if that id is used by another input, a previous
      manifest entry, or any other name in `reserved` (e.g. existing output
      folders). Files are visited in sorted path order, so the result does not
      depend on directory order.
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
            if _in_saved_page_folder(path, input_dir):
                continue  # images etc. of a browser-saved page, used by that page
            ext = path.suffix.lower()
            if ext in CONVERTIBLE:
                found.append((path, rel, CONVERTIBLE[ext]))
            else:
                skipped.append(rel)

    web_ids = {s.id for s in sources}
    ids: dict[str, str] = {}
    moved: dict[str, str] = {}  # rel -> flat id from before NL1
    for _, rel, _ in found:  # first: files the previous manifest already knows
        old = previous_ids.get(rel)
        if old is None or old in web_ids or old in ids.values():
            continue
        if _same_folder(old, rel):
            ids[rel] = old
        else:
            moved[rel] = old
    for path, rel, _ in found:  # then: new files (and moved ones)
        if rel not in ids:
            ids[rel] = _unique(file_id(rel, slugify(path.stem)), taken)
            taken.add(ids[rel])

    docs = [
        InputDoc(id=ids[rel], source_type=kind, path=path, rel_path=rel, previous_id=moved.get(rel))
        for path, rel, kind in found
    ]
    docs += [
        InputDoc(id=s.id, source_type="web", path=None, rel_path=None, source=s) for s in sources
    ]
    return ScanResult(docs=docs, skipped=skipped)


@dataclass(frozen=True)
class Route:
    """Which converter handles a document, and with which input format."""

    name: str  # recorded in the manifest, e.g. "pandoc:html"
    converter: str | None  # "pandoc", "docling", or None when not converted
    from_format: str | None = None


def route(doc: InputDoc) -> Route:
    """Pick the converter by source type.

    PDFs always route to Docling here; the text-layer check that can stop a PDF
    (no_text_layer) runs separately in the pipeline.
    """
    if doc.source_type in ("html", "web"):
        return Route("pandoc:html", "pandoc", "html")
    if doc.source_type == "docx":
        return Route("pandoc:docx", "pandoc", "docx")
    if doc.source_type == "doc":
        # LibreOffice saves it as .docx, then the DOCX route (deviation DOC1).
        return Route("libreoffice+pandoc:docx", "pandoc", "docx")
    if doc.source_type == "pdf":
        return Route("docling", "docling")  # text-layer routing: phase 3
    return Route("none:unsupported", None)
