"""Documents of a job, approval, metadata and the library (design: API,
Document page, Library, Storage).

A document is addressed as <job_id>/<doc_id> (deviation WB5). Approval calls
specval's own approve function, so the web and the command line follow the
same rules; an approved document is then copied into
data/library/<doc_id>/<version>/, so deleting or expiring the job never
removes it. Re-approving the same id adds a new version.
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

DOC_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


@dataclass(frozen=True)
class DocRef:
    job_id: str
    doc_id: str
    folder: Path  # output/<doc_id>
    validation: Path  # the job's validation folder
    record: dict[str, Any]

    @property
    def md_path(self) -> Path:
        return self.folder / f"{self.doc_id}.md"

    @property
    def report_path(self) -> Path:
        return self.validation / f"{self.doc_id}.json"

    def source_path(self) -> Path | None:
        found = sorted(self.folder.glob("source.*"))
        return found[0] if found else None


def resolve(cfg: WebConfig, job_id: str, doc_id: str) -> DocRef:
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
    """root/relative if it is a file inside root, else 404 (no ../ escapes)."""
    target = (root / relative).resolve()
    if not target.is_relative_to(root.resolve()) or not target.is_file():
        raise HTTPException(status_code=404, detail="no such file")
    return target


def read_report(ref: DocRef) -> dict[str, Any] | None:
    try:
        return json.loads(ref.report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def details(ref: DocRef) -> dict[str, Any]:
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
    """The Markdown, its media (and sections), and the validation report."""
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
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_bytes(text.encode("utf-8"))
    os.replace(tmp, path)


def update_metadata(ref: DocRef, title: str | None, spec_version: str | None) -> dict[str, Any]:
    """Write title and spec_version into the front matter; the body is unchanged."""
    fm, body = parse_document(ref.md_path.read_text(encoding="utf-8"))
    if title is not None:
        fm.title = title.strip()[:300]
    if spec_version is not None:
        fm.spec_version = spec_version.strip()[:100]
    _write_atomic(ref.md_path, render_document(fm, body))
    return fm.to_dict()


def approve(cfg: WebConfig, ref: DocRef, name: str | None, override: str | None) -> dict[str, Any]:
    from specval.approve import ApproveError
    from specval.approve import approve as specval_approve

    name = (name or "").strip()[:200] or None
    override = (override or "").strip() or None
    try:
        specval_approve(ref.doc_id, ref.folder.parent, ref.validation, override=override, by=name)
    except ApproveError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
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
    return cfg.data_dir / "library"


def _versions(folder: Path) -> list[int]:
    return sorted(int(p.name) for p in folder.iterdir() if p.is_dir() and p.name.isdigit())


def add_to_library(cfg: WebConfig, ref: DocRef, record: dict[str, Any]) -> int:
    folder = library_root(cfg) / ref.doc_id
    folder.mkdir(parents=True, exist_ok=True)
    version = (_versions(folder) or [0])[-1] + 1
    target = folder / str(version)
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
    """The latest version of every approved document, newest approval first."""
    root = library_root(cfg)
    items = []
    if root.is_dir():
        for folder in sorted(root.iterdir()):
            if not folder.is_dir() or not DOC_ID.match(folder.name):
                continue
            versions = _versions(folder)
            if not versions:
                continue
            meta = json.loads((folder / str(versions[-1]) / "meta.json").read_text())
            items.append({**meta, "versions": len(versions)})
    return sorted(items, key=lambda m: m.get("approved_at") or "", reverse=True)
