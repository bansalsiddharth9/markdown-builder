"""Creating jobs (design: Jobs and workers, Storage).

Each job gets its own folder, laid out as the CLIs expect::

    data/jobs/<job_id>/
      input/          uploaded files, stored under safe names
      sources.yaml    the job's URLs (render mode per URL)
      work/           fetch cache and debug files
      output/         specconv output
      validation/     specval output
      logs/           one log per step
"""

from __future__ import annotations

import datetime as dt
import json
import re
import secrets
import shutil
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import urlsplit

import yaml

from specweb.db import Document, Job, sessions
from specweb.settings import WebConfig

# Accepted upload extensions and the source type each one maps to.
ALLOWED_EXTENSIONS = {".pdf": "pdf", ".html": "html", ".htm": "html", ".docx": "docx"}
RENDER_MODES = ("static", "browser")


class JobError(ValueError):
    """The submission is not acceptable; the message is shown to the user."""


@dataclass(frozen=True)
class Upload:
    """An uploaded file, read fully into memory (after the size checks)."""

    filename: str
    data: bytes


@dataclass(frozen=True)
class UrlSource:
    """A URL to fetch; render is "static" or "browser" (JavaScript-built pages)."""

    url: str
    render: str = "static"


def job_folder(cfg: WebConfig, job_id: str) -> Path:
    """data/jobs/<job_id>. Raises JobError unless job_id is 12 hex characters.

    This is the path-safety check for every route that takes a job id: an id
    such as "../x" can never reach the file system.
    """
    if not re.fullmatch(r"[0-9a-f]{12}", job_id):
        raise JobError("invalid job id")
    return cfg.data_dir / "jobs" / job_id


def slugify(text: str, limit: int = 60) -> str:
    """Lowercase ASCII letters and digits joined by '-', at most limit characters.

    Accents are dropped (é -> e); other characters become '-'.
    """
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:limit].strip("-")


def safe_name(filename: str, taken: set[str]) -> str:
    """A storage name from the uploaded name: its last path part only (both
    separators), ASCII letters, digits and '-', lowercase extension.

    The user's file name is never used as a path (deviation WB4): this blocks
    path traversal and odd characters. Clashes within the job get -2, -3, ...
    The name is added to taken.
    """
    base = PureWindowsPath(PurePosixPath(filename).name).name
    stem, dot, ext = base.rpartition(".")
    if not dot:
        stem, ext = base, ""
    name = (slugify(stem) or "document") + (f".{ext.lower()}" if ext else "")
    candidate, n = name, 2
    while candidate in taken:
        stem_part, _, ext_part = name.rpartition(".")
        candidate = f"{stem_part}-{n}.{ext_part}"
        n += 1
    taken.add(candidate)
    return candidate


def url_id(url: str, taken: set[str]) -> str:
    """A unique id for a URL in sources.yaml, from its host and path.

    Clashes get -2, -3, ...; the id is added to taken.
    """
    parts = urlsplit(url)
    base = slugify(f"{parts.hostname or ''} {parts.path}") or "page"
    candidate, n = base, 2
    while candidate in taken:
        candidate = f"{base}-{n}"
        n += 1
    taken.add(candidate)
    return candidate


def check_submission(uploads: list[Upload], urls: list[UrlSource], split: int | None) -> None:
    """Raise JobError for an empty job, a wrong extension, a non-http(s) URL,
    an unknown render mode or a split level outside 1 to 6.

    Only the extension is checked here; security.check_content checks the
    file content.
    """
    if not uploads and not urls:
        raise JobError("no files or URLs: add at least one")
    for upload in uploads:
        ext = PurePosixPath(upload.filename.lower()).suffix
        if ext not in ALLOWED_EXTENSIONS:
            raise JobError(
                f"{upload.filename}: only .pdf, .html, .htm and .docx files are accepted"
            )
    for source in urls:
        if not source.url.startswith(("http://", "https://")):
            raise JobError(f"{source.url}: URLs must start with http:// or https://")
        if source.render not in RENDER_MODES:
            raise JobError(f"{source.url}: render must be 'static' or 'browser'")
    if split is not None and not 1 <= split <= 6:
        raise JobError("split must be a heading level from 1 to 6")


def parse_urls_json(text: str | None) -> list[UrlSource]:
    """The API's `urls` field: a JSON list of {url, render} or plain URL strings.

    Raises JobError for anything else. render defaults to "static".
    """
    if not text:
        return []
    try:
        items = json.loads(text)
    except ValueError as exc:
        raise JobError("urls: expected a JSON list of {url, render}") from exc
    if not isinstance(items, list):
        raise JobError("urls: expected a JSON list of {url, render}")
    out = []
    for item in items:
        if isinstance(item, str):
            item = {"url": item}
        if not isinstance(item, dict) or not isinstance(item.get("url"), str):
            raise JobError("urls: each item needs a 'url'")
        out.append(UrlSource(item["url"].strip(), str(item.get("render", "static"))))
    return out


def parse_urls_text(text: str | None) -> list[UrlSource]:
    """The upload form: one URL per line, optionally followed by 'browser' or 'static'."""
    out = []
    for line in (text or "").splitlines():
        parts = line.split()
        if not parts:
            continue
        out.append(UrlSource(parts[0], parts[1] if len(parts) > 1 else "static"))
    return out


def parse_split(value: str | int | None) -> int | None:
    """The split level from a form or API field; None when empty.

    Raises JobError if it is not a number. The range is checked by
    check_submission.
    """
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise JobError("split must be a heading level from 1 to 6") from exc


def create_job(
    cfg: WebConfig,
    uploads: list[Upload],
    urls: list[UrlSource],
    split: int | None,
    submitted_by: str | None,
    client_ip: str | None = None,
) -> Job:
    """Check the submission, write the job folder and add the job and its
    documents to the database. Returns the job with its documents loaded.

    Raises JobError for an unacceptable submission. Does not queue the job
    (api.submit does that).
    """
    check_submission(uploads, urls, split)
    job_id = secrets.token_hex(6)  # 12 hex characters: random, so ids cannot be guessed
    folder = job_folder(cfg, job_id)
    (folder / "input").mkdir(parents=True)
    taken: set[str] = set()
    docs: list[Document] = []
    for upload in uploads:
        name = safe_name(upload.filename, taken)
        (folder / "input" / name).write_bytes(upload.data)
        docs.append(
            Document(
                source_name=upload.filename[:500],
                stored_name=name,
                source_type=ALLOWED_EXTENSIONS[PurePosixPath(name).suffix],
            )
        )
    if urls:
        # URL ids must not clash with file ids: both become specconv document ids.
        ids: set[str] = {PurePosixPath(n).stem for n in taken}
        sources = []
        for source in urls:
            sid = url_id(source.url, ids)
            sources.append({"id": sid, "url": source.url, "render": source.render})
            docs.append(
                Document(source_name=source.url[:500], url=source.url, render=source.render,
                         source_type="web", stored_name=sid)
            )  # fmt: skip
        (folder / "sources.yaml").write_text(
            yaml.safe_dump({"sources": sources}, sort_keys=False), encoding="utf-8"
        )
    job = Job(
        id=job_id,
        created_at=dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        submitted_by=(submitted_by or "").strip()[:200] or None,
        client_ip=client_ip,
        status="queued",
        options_json=json.dumps({"split": split} if split is not None else {}),
        documents=docs,
    )
    with sessions(cfg.data_dir)() as session:
        session.add(job)
        session.commit()
        session.refresh(job)
        _ = job.documents  # load them now; the session closes below
    return job


def delete_job_files(cfg: WebConfig, job_id: str) -> None:
    """Delete the job's folder. Missing files are not an error."""
    shutil.rmtree(job_folder(cfg, job_id), ignore_errors=True)
