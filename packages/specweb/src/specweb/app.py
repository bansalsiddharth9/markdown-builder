"""FastAPI app and pages (design: Pages, UI technology).

Server-rendered Jinja2 pages with HTMX for live updates: while a job runs,
its rows are polled every 2 seconds. No JavaScript build step.

The page routes for jobs live here; document and library routes are in
doc_routes.py, the JSON API in api.py. Pages call the same functions as the
API, so both follow the same rules.
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from specweb import __version__, api, doc_routes, security
from specweb.db import RUNNING, sessions
from specweb.jobs import JobError, parse_urls_text
from specweb.settings import WebConfig, load_config
from specweb.templating import HERE, templates

# Strict policy (design: Security): own scripts and styles only, no plugins,
# frames only from this site (source previews), forms post only here.
# This is why templates have no inline <script> or style="" (deviation SEC3).
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "frame-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; "
    "frame-ancestors 'self'"
)
# All job states, in order; used to check the Jobs page's status filter.
JOB_STATUSES = ("queued", "fetching", "converting", "validating", "done", "error")


def create_app(cfg: WebConfig | None = None) -> FastAPI:
    """Build the web app: create the data folder and tables, mount static files,
    add the API and document routers, the security headers and the job pages.

    cfg defaults to load_config() (specweb.yaml / environment). Tests pass
    their own config.
    """
    cfg = cfg or load_config()
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    sessions(cfg.data_dir)  # create the tables
    app = FastAPI(title="specweb", version=__version__, docs_url="/api/docs", redoc_url=None)
    # Routes in other modules read the config from here (api.config, doc_routes._cfg).
    app.state.config = cfg
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")
    app.include_router(api.router)
    app.include_router(doc_routes.router)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        """Add the CSP and other security headers to every response (SEC3).

        The CSP blocks injected scripts and styles (XSS) and framing by other
        sites (clickjacking); nosniff stops the browser from guessing a
        different content type; no-referrer keeps job URLs out of other
        sites' logs. setdefault: a route that sets its own header wins (the
        HTML source route sends a stricter sandbox CSP).
        """
        response = await call_next(request)
        # A source PDF is shown by the browser's own viewer, which a CSP can break.
        if response.headers.get("content-type", "").startswith("application/pdf"):
            response.headers.setdefault("X-Content-Type-Options", "nosniff")
            return response
        response.headers.setdefault("Content-Security-Policy", CSP)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        return response

    @app.get("/", response_class=HTMLResponse)
    def upload_page(request: Request) -> HTMLResponse:
        """GET /: the Upload page (upload.html), with no error."""
        return templates.TemplateResponse(request, "upload.html", {"error": None})

    @app.post("/jobs")
    async def upload_form(
        request: Request,
        files: Annotated[list[UploadFile] | None, File()] = None,
        urls_text: Annotated[str | None, Form()] = None,
        split: Annotated[str | None, Form()] = None,
        submitted_by: Annotated[str | None, Form()] = None,
    ):
        """POST /jobs: the Upload form. Creates and queues a job, then redirects
        (303) to its page /jobs/<id>.

        Any refusal (bad input, limits, blocked URL) shows the Upload page
        again with the message and status 400, whatever status the API
        would have used (413, 429, 503, 507).
        """
        try:
            urls = parse_urls_text(urls_text)
            # Limits are checked before anything is stored (SEC2).
            uploads = await api.read_uploads(request, files)
            job = api.submit(request, uploads, urls, split, submitted_by)
        except (HTTPException, JobError) as exc:
            message = exc.detail if isinstance(exc, HTTPException) else str(exc)
            return templates.TemplateResponse(
                request, "upload.html", {"error": message}, status_code=400
            )
        # 303 so the browser follows with a GET (no resubmit on reload).
        return RedirectResponse(f"/jobs/{job.id}", status_code=303)

    @app.get("/jobs", response_class=HTMLResponse)
    def jobs_page(
        request: Request, status: str | None = None, message: str | None = None
    ) -> HTMLResponse:
        """GET /jobs: the Jobs page (jobs.html), newest first, optionally filtered
        by status. message is a notice from a redirect (e.g. "Job … deleted")."""
        context = {**_jobs_context(cfg, status), "message": message}
        return templates.TemplateResponse(request, "jobs.html", context)

    @app.get("/jobs/table", response_class=HTMLResponse)
    def jobs_table(request: Request, status: str | None = None) -> HTMLResponse:
        """GET /jobs/table: just the jobs table fragment (_jobs_table.html).
        HTMX polls this every 2 s while a job is queued or running."""
        return templates.TemplateResponse(request, "_jobs_table.html", _jobs_context(cfg, status))

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    def job_page(request: Request, job_id: str, message: str | None = None) -> HTMLResponse:
        """GET /jobs/<id>: the Job detail page (job.html). 404 for an unknown or
        malformed id. message is an error from a refused delete."""
        job = api.get_job(request, job_id)
        return templates.TemplateResponse(
            request, "job.html", {"job": job, "running": RUNNING, "message": message}
        )

    @app.post("/jobs/{job_id}/delete")
    def delete_form(job_id: str):
        """POST /jobs/<id>/delete: the Delete button in the job page's confirm
        dialog (design: Job detail; deviation HP3).

        Deleted: redirect to /jobs with a notice. Unknown job: 404. Running
        job (409): back to the job page with the message.
        """
        try:
            api.remove_job(cfg, job_id)
        except HTTPException as exc:
            if exc.status_code == 404:
                raise
            return RedirectResponse(f"/jobs/{job_id}?message={quote(exc.detail)}", 303)
        return RedirectResponse(f"/jobs?message={quote(f'Job {job_id} deleted')}", 303)

    @app.get("/jobs/{job_id}/documents", response_class=HTMLResponse)
    def job_documents(request: Request, job_id: str) -> HTMLResponse:
        """GET /jobs/<id>/documents: the job's status and documents table
        (_job_documents.html). HTMX polls this every 2 s while the job runs.
        404 for an unknown job."""
        job = api.get_job(request, job_id)
        return templates.TemplateResponse(
            request, "_job_documents.html", {"job": job, "running": RUNNING}
        )

    return app


def _jobs_context(cfg: WebConfig, status: str | None) -> dict:
    """Template context for the Jobs page and its table fragment.

    An unknown status filter shows all jobs. any_running turns on HTMX
    polling; disk is the usage line (design: Retention).
    """
    jobs = api.list_jobs(cfg, status if status in JOB_STATUSES else None)
    return {
        "jobs": jobs,
        "status": status,
        "statuses": JOB_STATUSES,
        "any_running": any(j["status"] in ("queued", *RUNNING) for j in jobs),
        "disk": security.disk_usage(cfg),
    }
