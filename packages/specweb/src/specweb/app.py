"""FastAPI app and pages (design: Pages, UI technology).

Server-rendered Jinja2 pages with HTMX for live updates: while a job runs,
its rows are polled every 2 seconds. No JavaScript build step.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from specweb import __version__, api, doc_routes
from specweb.db import RUNNING, sessions
from specweb.jobs import JobError, parse_urls_text
from specweb.settings import WebConfig, load_config
from specweb.templating import HERE, templates

JOB_STATUSES = ("queued", "fetching", "converting", "validating", "done", "error")


def create_app(cfg: WebConfig | None = None) -> FastAPI:
    cfg = cfg or load_config()
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    sessions(cfg.data_dir)  # create the tables
    app = FastAPI(title="specweb", version=__version__, docs_url="/api/docs", redoc_url=None)
    app.state.config = cfg
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
    app.include_router(api.router)
    app.include_router(doc_routes.router)

    @app.get("/", response_class=HTMLResponse)
    def upload_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "upload.html", {"error": None})

    @app.post("/jobs")
    async def upload_form(
        request: Request,
        files: Annotated[list[UploadFile] | None, File()] = None,
        urls_text: Annotated[str | None, Form()] = None,
        split: Annotated[str | None, Form()] = None,
        submitted_by: Annotated[str | None, Form()] = None,
    ):
        try:
            urls = parse_urls_text(urls_text)
            job = api.submit(request, await api.read_uploads(files), urls, split, submitted_by)
        except (HTTPException, JobError) as exc:
            message = exc.detail if isinstance(exc, HTTPException) else str(exc)
            return templates.TemplateResponse(
                request, "upload.html", {"error": message}, status_code=400
            )
        return RedirectResponse(f"/jobs/{job.id}", status_code=303)

    @app.get("/jobs", response_class=HTMLResponse)
    def jobs_page(request: Request, status: str | None = None) -> HTMLResponse:
        return templates.TemplateResponse(request, "jobs.html", _jobs_context(cfg, status))

    @app.get("/jobs/table", response_class=HTMLResponse)
    def jobs_table(request: Request, status: str | None = None) -> HTMLResponse:
        return templates.TemplateResponse(request, "_jobs_table.html", _jobs_context(cfg, status))

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    def job_page(request: Request, job_id: str) -> HTMLResponse:
        job = api.get_job(request, job_id)
        return templates.TemplateResponse(request, "job.html", {"job": job, "running": RUNNING})

    @app.get("/jobs/{job_id}/documents", response_class=HTMLResponse)
    def job_documents(request: Request, job_id: str) -> HTMLResponse:
        job = api.get_job(request, job_id)
        return templates.TemplateResponse(
            request, "_job_documents.html", {"job": job, "running": RUNNING}
        )

    return app


def _jobs_context(cfg: WebConfig, status: str | None) -> dict:
    jobs = api.list_jobs(cfg, status if status in JOB_STATUSES else None)
    return {
        "jobs": jobs,
        "status": status,
        "statuses": JOB_STATUSES,
        "any_running": any(j["status"] in ("queued", *RUNNING) for j in jobs),
    }
