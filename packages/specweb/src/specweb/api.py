"""JSON API (design: API). The pages use the same functions, so jobs can
also be scripted.

Job routes are here; document and library routes are in doc_routes.py.
Errors are HTTPException with a short `detail` message.
"""

from __future__ import annotations

import shutil
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile
from sqlalchemy import select, text

from specweb import security, tasks
from specweb.db import RUNNING, Job, sessions
from specweb.jobs import (
    JobError,
    Upload,
    UrlSource,
    create_job,
    delete_job_files,
    job_folder,
    parse_split,
    parse_urls_json,
)
from specweb.settings import WebConfig

router = APIRouter()


def config(request: Request) -> WebConfig:
    """The app's WebConfig (set by create_app)."""
    return request.app.state.config


def client_ip(request: Request) -> str | None:
    """The client's address, for the per-client job limit (deviation SEC6)."""
    # behind the reverse proxy, uvicorn sets this from X-Forwarded-For
    return request.client.host if request.client else None


async def read_uploads(request: Request, files: list[UploadFile] | None) -> list[Upload]:
    """Capacity first (queue, client, disk), then the files with their limits.

    Checking capacity first means a full queue refuses the job before any
    large upload is read into memory (deviation SEC2).
    """
    cfg = config(request)
    security.check_capacity(cfg, client_ip(request))
    return await security.read_uploads(cfg, files)


def submit(
    request: Request,
    uploads: list[Upload],
    urls: list[UrlSource],
    split: str | int | None,
    submitted_by: str | None,
) -> Job:
    """Check the URLs, create the job (folder and rows) and queue it.

    Used by both the API and the Upload form. A blocked URL or a bad
    submission raises 400.
    """
    cfg = config(request)
    # Refuse private/loopback addresses now (SSRF); the worker checks again
    # when it fetches (design: Security).
    security.check_urls(cfg, urls)
    try:
        job = create_job(cfg, uploads, urls, parse_split(split), submitted_by, client_ip(request))
    except JobError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # Queue only after the job is committed, so the worker always finds it.
    tasks.enqueue(cfg, job.id)
    return job


@router.post("/api/jobs", status_code=201)
async def post_job(
    request: Request,
    files: Annotated[list[UploadFile] | None, File()] = None,
    urls: Annotated[str | None, Form()] = None,
    split: Annotated[str | None, Form()] = None,
    submitted_by: Annotated[str | None, Form()] = None,
) -> dict:
    """POST /api/jobs: multipart upload of files and/or `urls` (a JSON list of
    {url, render}). Returns the new job (201).

    400 for bad input or a blocked URL; 413, 429, 503 and 507 for the
    limits (see security.py).
    """
    try:
        url_sources = parse_urls_json(urls)
    except JobError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    job = submit(request, await read_uploads(request, files), url_sources, split, submitted_by)
    return get_job_dict(config(request), job.id)


def get_job_dict(cfg: WebConfig, job_id: str) -> dict:
    """One job with its documents, as a dict; 404 if it does not exist."""
    with sessions(cfg.data_dir)() as session:
        job = session.get(Job, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="no such job")
        return job.to_dict()


def list_jobs(cfg: WebConfig, status: str | None = None) -> list[dict]:
    """All jobs with their documents, newest first; only one status if given."""
    with sessions(cfg.data_dir)() as session:
        # newest first; SQLite's rowid orders jobs created in the same second
        query = select(Job).order_by(Job.created_at.desc(), text("jobs.rowid DESC"))
        if status:
            query = query.where(Job.status == status)
        return [job.to_dict(with_documents=True) for job in session.scalars(query)]


@router.get("/api/jobs")
def get_jobs(request: Request, status: str | None = None) -> list[dict]:
    """GET /api/jobs: list jobs, optionally filtered by ?status=."""
    return list_jobs(config(request), status)


@router.get("/api/jobs/{job_id}")
def get_job(request: Request, job_id: str) -> dict:
    """GET /api/jobs/<id>: one job and its documents. 404 if the id is malformed
    or unknown. The job pages call this too."""
    try:
        # Check the id's format first: it is also used as a folder name.
        job_folder(config(request), job_id)
    except JobError as exc:
        raise HTTPException(status_code=404, detail="no such job") from exc
    return get_job_dict(config(request), job_id)


@router.delete("/api/jobs/{job_id}", status_code=204)
def delete_job(request: Request, job_id: str) -> Response:
    """DELETE /api/jobs/<id>: delete a job (204). 404 unknown, 409 running."""
    remove_job(config(request), job_id)
    return Response(status_code=204)


def remove_job(cfg: WebConfig, job_id: str) -> None:
    """Delete a job and its folder: 404 if unknown, 409 while it runs.
    Approved documents stay in the library.

    A running job is refused because the worker is still writing into its
    folder and rows. A queued job can be deleted; the worker then skips it.
    """
    try:
        # Validate the id before it is used in a path (no ../ tricks).
        job_folder(cfg, job_id)
    except JobError as exc:
        raise HTTPException(status_code=404, detail="no such job") from exc
    with sessions(cfg.data_dir)() as session:
        job = session.get(Job, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="no such job")
        if job.status in RUNNING:
            raise HTTPException(
                status_code=409, detail=f"job is {job.status}; delete it when it is done"
            )
        session.delete(job)
        session.commit()
    # Files go after the rows, so a failure here leaves no row pointing at
    # missing files.
    delete_job_files(cfg, job_id)


@router.get("/healthz")
def healthz(request: Request, response: Response) -> dict:
    """GET /healthz: database, queue, Pandoc and free disk (deviation SEC5).

    Returns 200 when all are "ok", else 503. The Docker health check uses it.
    """
    cfg = config(request)
    checks: dict[str, str] = {}
    try:
        with sessions(cfg.data_dir)() as session:
            session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # report, do not crash the health check
        checks["database"] = f"error: {exc}"
    from specconv.deps import find_pandoc

    checks["pandoc"] = "ok" if find_pandoc() is not None else "missing"
    try:
        tasks.huey.pending_count()
        checks["queue"] = "ok"
    except Exception as exc:  # report, do not crash the health check
        checks["queue"] = f"error: {exc}"
    usage = shutil.disk_usage(cfg.data_dir)
    free_mb = usage.free / 2**20
    checks["disk"] = "ok" if free_mb >= cfg.min_free_disk_mb else f"low: {free_mb:.0f} MB free"
    ok = all(v == "ok" for v in checks.values())
    if not ok:
        response.status_code = 503
    return {"status": "ok" if ok else "error", "checks": checks, "free_disk_mb": round(free_mb)}
