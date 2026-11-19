"""JSON API (design: API). The pages use the same functions, so jobs can
also be scripted."""

from __future__ import annotations

import shutil
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile
from sqlalchemy import select, text

from specweb import tasks
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
    return request.app.state.config


async def read_uploads(files: list[UploadFile] | None) -> list[Upload]:
    uploads = []
    for f in files or []:
        if not f.filename:
            continue
        uploads.append(Upload(f.filename, await f.read()))
    return uploads


def submit(
    request: Request,
    uploads: list[Upload],
    urls: list[UrlSource],
    split: str | int | None,
    submitted_by: str | None,
) -> Job:
    cfg = config(request)
    client_ip = request.client.host if request.client else None
    try:
        job = create_job(cfg, uploads, urls, parse_split(split), submitted_by, client_ip)
    except JobError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
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
    try:
        url_sources = parse_urls_json(urls)
    except JobError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    job = submit(request, await read_uploads(files), url_sources, split, submitted_by)
    return get_job_dict(config(request), job.id)


def get_job_dict(cfg: WebConfig, job_id: str) -> dict:
    with sessions(cfg.data_dir)() as session:
        job = session.get(Job, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="no such job")
        return job.to_dict()


def list_jobs(cfg: WebConfig, status: str | None = None) -> list[dict]:
    with sessions(cfg.data_dir)() as session:
        # newest first; SQLite's rowid orders jobs created in the same second
        query = select(Job).order_by(Job.created_at.desc(), text("jobs.rowid DESC"))
        if status:
            query = query.where(Job.status == status)
        return [job.to_dict(with_documents=True) for job in session.scalars(query)]


@router.get("/api/jobs")
def get_jobs(request: Request, status: str | None = None) -> list[dict]:
    return list_jobs(config(request), status)


@router.get("/api/jobs/{job_id}")
def get_job(request: Request, job_id: str) -> dict:
    try:
        job_folder(config(request), job_id)
    except JobError as exc:
        raise HTTPException(status_code=404, detail="no such job") from exc
    return get_job_dict(config(request), job_id)


@router.delete("/api/jobs/{job_id}", status_code=204)
def delete_job(request: Request, job_id: str) -> Response:
    cfg = config(request)
    try:
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
    delete_job_files(cfg, job_id)
    return Response(status_code=204)


@router.get("/healthz")
def healthz(request: Request, response: Response) -> dict:
    cfg = config(request)
    checks: dict[str, str] = {}
    try:
        with sessions(cfg.data_dir)() as session:
            session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # report, do not crash the health check
        checks["database"] = f"error: {exc}"
    usage = shutil.disk_usage(cfg.data_dir)
    free_mb = usage.free / 2**20
    checks["disk"] = "ok" if free_mb >= cfg.min_free_disk_mb else f"low: {free_mb:.0f} MB free"
    ok = all(v == "ok" for v in checks.values())
    if not ok:
        response.status_code = 503
    return {"status": "ok" if ok else "error", "checks": checks, "free_disk_mb": round(free_mb)}
