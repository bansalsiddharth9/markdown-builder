"""Protections for an open internal port (design: Security).

- Uploads: at most max_files per job and max_file_mb per file; only .pdf,
  .html, .htm and .docx, and the content must match the extension (checked
  with filetype and the file's own structure, not the name).
- URLs: private, loopback and link-local addresses are refused on submission
  (specconv's HostGuard); the worker also makes the fetch itself refuse them,
  after every redirect and for page images. fetch_allowlist permits named
  internal hosts.
- Abuse limits: queued or running jobs per client address, and in total.
- Disk: new jobs are refused below min_free_disk_mb.
"""

from __future__ import annotations

import io
import shutil
import zipfile
from pathlib import Path, PurePosixPath

import filetype
from fastapi import HTTPException, UploadFile
from sqlalchemy import func, select

from specweb.db import RUNNING, Job, sessions
from specweb.jobs import Upload, UrlSource
from specweb.settings import WebConfig

CHUNK = 1 << 20


async def read_uploads(cfg: WebConfig, files: list[UploadFile] | None) -> list[Upload]:
    files = [f for f in files or [] if f.filename]
    if len(files) > cfg.max_files:
        raise HTTPException(400, f"at most {cfg.max_files} files per job, got {len(files)}")
    limit = int(cfg.max_file_mb * 2**20)
    uploads = []
    for f in files:
        data = bytearray()
        while chunk := await f.read(CHUNK):
            data += chunk
            if len(data) > limit:
                raise HTTPException(
                    413, f"{f.filename}: larger than {cfg.max_file_mb:g} MB (the limit per file)"
                )
        uploads.append(Upload(f.filename, bytes(data)))
    for upload in uploads:
        check_content(upload)
    return uploads


def _is_docx(data: bytes) -> bool:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            return "word/document.xml" in zf.namelist()
    except zipfile.BadZipFile:
        return False


def _is_html(data: bytes) -> bool:
    head = data[:8192]
    if b"\x00" in head or filetype.guess(head) is not None:
        return False  # binary, or a known binary type (PDF, ZIP, image …)
    text = head.decode("utf-8", errors="replace").lower()
    return "<" in text and ">" in text


def check_content(upload: Upload) -> None:
    ext = PurePosixPath(upload.filename.lower()).suffix
    kind = filetype.guess(upload.data[:8192])
    ok = {
        ".pdf": kind is not None and kind.mime == "application/pdf",
        ".docx": _is_docx(upload.data),
        ".html": _is_html(upload.data),
        ".htm": _is_html(upload.data),
    }.get(ext, True)  # other extensions are refused by the job checks
    if not ok:
        raise HTTPException(
            400, f"{upload.filename}: the content does not match the {ext} extension"
        )


def check_urls(cfg: WebConfig, urls: list[UrlSource]) -> None:
    from specconv.fetch import BlockedAddress, HostGuard

    guard = HostGuard(cfg.fetch_allowlist)
    for source in urls:
        try:
            guard.check(source.url)
        except BlockedAddress as exc:
            raise HTTPException(400, f"{source.url}: {exc}") from exc


def check_capacity(cfg: WebConfig, client_ip: str | None) -> None:
    free_mb = shutil.disk_usage(cfg.data_dir).free / 2**20
    if free_mb < cfg.min_free_disk_mb:
        raise HTTPException(507, f"not enough free disk space ({free_mb:.0f} MB); try again later")
    waiting = (Job.status == "queued") | Job.status.in_(RUNNING)
    with sessions(cfg.data_dir)() as session:
        total = session.scalar(select(func.count()).select_from(Job).where(waiting)) or 0
        mine = (
            session.scalar(
                select(func.count()).select_from(Job).where(waiting, Job.client_ip == client_ip)
            )
            or 0
        )
    if total >= cfg.max_queue:
        raise HTTPException(503, f"the queue is full ({total} jobs); try again later")
    if client_ip is not None and mine >= cfg.max_jobs_per_ip:
        raise HTTPException(
            429, f"you already have {mine} jobs waiting or running; wait for them to finish"
        )


def disk_usage(cfg: WebConfig) -> dict[str, float]:
    usage = shutil.disk_usage(cfg.data_dir)
    used = sum(p.stat().st_size for p in Path(cfg.data_dir).rglob("*") if p.is_file())
    return {"data_mb": round(used / 2**20, 1), "free_mb": round(usage.free / 2**20)}
