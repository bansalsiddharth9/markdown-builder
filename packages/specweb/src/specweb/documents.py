"""Documents of a job, approval, metadata and the library (design: API,
Document page, Library, Storage).

A document is addressed as <job_id>/<doc_id> (deviation WB5). Approval calls
specval's own approve function, so the web and the command line follow the
same rules; an approved document is then copied into
data/library/<doc_id>/<version>/, so deleting or expiring the job never
removes it. Re-approving the same id adds a new version (deviation WB12).

Path safety: job ids, document ids and library versions are checked against
strict patterns before they are used in a path, and file paths from URLs go
through safe_file.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import os
import re
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from common.frontmatter import FrontMatterError, parse_document, render_document
from fastapi import HTTPException

from specweb.db import Document, sessions
from specweb.jobs import JobError, job_folder
from specweb.settings import WebConfig

# Allowed document ids: start with a letter or digit, so never "." or "..";
# no slashes, so an id is always one folder name.
DOC_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
# Content types for the source download, by extension.
MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


@dataclass(frozen=True)
class DocRef:
    """A converted document that exists, with the paths to its files.

    Made only by resolve(), after the ids were checked. record is the
    document's database row as a dict.
    """

    job_id: str
    doc_id: str
    folder: Path  # output/<doc_id>
    validation: Path  # the job's validation folder
    record: dict[str, Any]

    @property
    def md_path(self) -> Path:
        """output/<doc_id>/<doc_id>.md"""
        return self.folder / f"{self.doc_id}.md"

    @property
    def report_path(self) -> Path:
        """validation/<doc_id>.json (may not exist yet)."""
        return self.validation / f"{self.doc_id}.json"

    def source_path(self) -> Path | None:
        """The copy of the original file that specconv keeps (source.pdf,
        source.html, ...), or None."""
        found = sorted(self.folder.glob("source.*"))
        return found[0] if found else None


def resolve(cfg: WebConfig, job_id: str, doc_id: str) -> DocRef:
    """Find a converted document by <job_id>/<doc_id> (deviation WB5).

    404 if either id is malformed, the document is not in the database, or
    its Markdown file does not exist (e.g. conversion failed). All document
    routes start here.
    """
    if not DOC_ID.match(doc_id):
        raise HTTPException(status_code=404, detail="no such document")
    try:
        folder = job_folder(cfg, job_id)
    except JobError as exc:
        raise HTTPException(status_code=404, detail="no such document") from exc
    with sessions(cfg.data_dir)() as session:
        doc = (
            session.query(Document)
            .filter(Document.job_id == job_id, Document.doc_id == doc_id)
            .first()
        )
        record = doc.to_dict() if doc else None
    out = folder / "output" / doc_id
    if record is None or not (out / f"{doc_id}.md").is_file():
        raise HTTPException(status_code=404, detail="no such document")
    return DocRef(job_id, doc_id, out, folder / "validation", record)


def safe_file(root: Path, relative: str) -> Path:
    """root/relative if it is a file inside root, else 404 (no ../ escapes).

    Prevents path traversal: relative comes from the URL. resolve() also
    follows symlinks, so a link pointing outside root is refused too.
    """
    target = (root / relative).resolve()
    if not target.is_relative_to(root.resolve()) or not target.is_file():
        raise HTTPException(status_code=404, detail="no such file")
    return target


def read_report(ref: DocRef) -> dict[str, Any] | None:
    """specval's JSON report for the document, or None if missing or unreadable."""
    try:
        return json.loads(ref.report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def details(ref: DocRef) -> dict[str, Any]:
    """The document's record plus its front matter and a validation summary
    (overall, date, status per check). Used by the API and the Document page.

    front_matter is None if it cannot be parsed; validation is None before
    validation.
    """
    text = ref.md_path.read_text(encoding="utf-8")
    try:
        fm, _ = parse_document(text)
        front_matter: dict[str, Any] | None = fm.to_dict()
    except FrontMatterError:
        front_matter = None
    report = read_report(ref)
    validation = (
        {
            "overall": report.get("overall"),
            "validated_at": report.get("validated_at"),
            "checks": {c["id"]: c["status"] for c in report.get("checks", [])},
        }
        if report
        else None
    )
    return {**ref.record, "front_matter": front_matter, "validation": validation}


def zip_bytes(ref: DocRef) -> bytes:
    """The Markdown, its media (and sections), and the validation report.

    Everything sits in a <doc_id>/ folder inside the zip. The source is not
    included: it can be large and has its own download (deviation WB13).
    Built in memory.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(ref.folder.rglob("*")):
            rel = path.relative_to(ref.folder)
            if path.is_file() and (rel.parts[0] in ("media", "sections") or path == ref.md_path):
                zf.write(path, f"{ref.doc_id}/{rel.as_posix()}")
        for suffix in ("json", "md"):
            report = ref.validation / f"{ref.doc_id}.{suffix}"
            if report.is_file():
                zf.write(report, f"{ref.doc_id}/validation.{suffix}")
    return buffer.getvalue()


def _write_atomic(path: Path, text: str) -> None:
    """Write via a temporary file and rename, so readers never see half a file."""
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_bytes(text.encode("utf-8"))
    os.replace(tmp, path)


def update_metadata(ref: DocRef, title: str | None, spec_version: str | None) -> dict[str, Any]:
    """Write title and spec_version into the front matter; the body is unchanged.

    None leaves a field as it is. Values are trimmed and cut to the form's
    maxlength. Changing only the front matter does not invalidate the
    validation (deviation WB11).
    """
    fm, body = parse_document(ref.md_path.read_text(encoding="utf-8"))
    if title is not None:
        fm.title = title.strip()[:300]
    if spec_version is not None:
        fm.spec_version = spec_version.strip()[:100]
    _write_atomic(ref.md_path, render_document(fm, body))
    return fm.to_dict()


def approve(cfg: WebConfig, ref: DocRef, name: str | None, override: str | None) -> dict[str, Any]:
    """Approve a document with specval's own rules, record it and copy it into
    the library. Returns the record with library_version.

    409 with specval's message when approval is refused (for example fail
    findings and no override reason, or the Markdown changed since it was
    validated).
    """
    from specval.approve import ApproveError
    from specval.approve import approve as specval_approve

    name = (name or "").strip()[:200] or None
    override = (override or "").strip() or None
    try:
        specval_approve(ref.doc_id, ref.folder.parent, ref.validation, override=override, by=name)
    except ApproveError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    # Recorded only after specval accepted the approval.
    now = dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    with sessions(cfg.data_dir)() as session:
        doc = (
            session.query(Document)
            .filter(Document.job_id == ref.job_id, Document.doc_id == ref.doc_id)
            .one()
        )
        doc.approved_by, doc.approved_at, doc.override_reason = name, now, override
        session.commit()
        record = doc.to_dict()
    version = add_to_library(cfg, ref, record)
    return {**record, "library_version": version}


# ----------------------------------------------------------------- library


def library_root(cfg: WebConfig) -> Path:
    """data/library: approved documents, kept outside the job folders."""
    return cfg.data_dir / "library"


def _versions(folder: Path) -> list[int]:
    """The version numbers in a library folder, sorted (only all-digit names).

    The ".<n>.tmp" folders of unfinished copies are skipped.
    """
    return sorted(int(p.name) for p in folder.iterdir() if p.is_dir() and p.name.isdigit())


def add_to_library(cfg: WebConfig, ref: DocRef, record: dict[str, Any]) -> int:
    """Copy an approved document into data/library/<doc_id>/<next version>/.
    Returns the new version number.

    Copied: the Markdown, media/, sections/, the source, the validation
    report and a meta.json describing the approval.
    """
    folder = library_root(cfg) / ref.doc_id
    folder.mkdir(parents=True, exist_ok=True)
    version = (_versions(folder) or [0])[-1] + 1
    target = folder / str(version)
    # Build the version in a hidden temp folder, then rename it into place:
    # the library never shows a half-copied version.
    tmp = folder / f".{version}.tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    shutil.copy2(ref.md_path, tmp / ref.md_path.name)
    for name in ("media", "sections"):
        if (ref.folder / name).is_dir():
            shutil.copytree(ref.folder / name, tmp / name)
    source = ref.source_path()
    if source is not None:
        shutil.copy2(source, tmp / source.name)
    for suffix in ("json", "md"):
        report = ref.validation / f"{ref.doc_id}.{suffix}"
        if report.is_file():
            shutil.copy2(report, tmp / f"validation.{suffix}")
    fm, _ = parse_document(ref.md_path.read_text(encoding="utf-8"))
    meta = {
        "doc_id": ref.doc_id,
        "version": version,
        "job_id": ref.job_id,
        "source_name": record["source_name"],
        "title": fm.title,
        "spec_version": fm.spec_version,
        "approved_by": record["approved_by"],
        "approved_at": record["approved_at"],
        "override_reason": record["override_reason"],
    }
    (tmp / "meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    tmp.rename(target)
    return version


def library(cfg: WebConfig) -> list[dict[str, Any]]:
    """The latest version of every approved document, newest approval first.

    Each item is that version's meta.json plus "versions" (how many there are).
    """
    root = library_root(cfg)
    items = []
    if root.is_dir():
        for folder in sorted(root.iterdir()):
            # Skip anything that is not a document folder.
            if not folder.is_dir() or not DOC_ID.match(folder.name):
                continue
            versions = _versions(folder)
            if not versions:
                continue
            meta = json.loads((folder / str(versions[-1]) / "meta.json").read_text())
            items.append({**meta, "versions": len(versions)})
    return sorted(items, key=lambda m: m.get("approved_at") or "", reverse=True)
