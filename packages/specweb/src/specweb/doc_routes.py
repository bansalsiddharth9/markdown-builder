"""Document and Library routes: JSON API and pages (design: API, Pages).

Documents are addressed as <job_id>/<doc_id> (deviation WB5). Every route
starts with documents.resolve(), which checks both ids and gives 404 for an
unknown document.
"""

from __future__ import annotations

from typing import Annotated

from common.frontmatter import split_front_matter
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)
from pydantic import BaseModel, ConfigDict

from specweb import documents
from specweb.render import docx_preview, render_markdown
from specweb.settings import WebConfig
from specweb.templating import templates

router = APIRouter()
# The Document page's tabs, in display order; an unknown ?tab= shows "rendered".
TABS = ("rendered", "raw", "side-by-side", "report", "review")


def _cfg(request: Request) -> WebConfig:
    """The app's WebConfig (set by create_app)."""
    return request.app.state.config


class ApproveBody(BaseModel):
    """JSON body of POST .../approve. Unknown fields are refused (422)."""

    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    override_reason: str | None = None


class MetadataBody(BaseModel):
    """JSON body of PATCH .../metadata. A missing field is left unchanged."""

    model_config = ConfigDict(extra="forbid")
    title: str | None = None
    spec_version: str | None = None


# --------------------------------------------------------------------- API

API = "/api/documents/{job_id}/{doc_id}"


@router.get(API)
def get_document(request: Request, job_id: str, doc_id: str) -> dict:
    """GET /api/documents/<job>/<doc>: record, front matter, validation summary."""
    return documents.details(documents.resolve(_cfg(request), job_id, doc_id))


@router.get(API + "/markdown")
def get_markdown(request: Request, job_id: str, doc_id: str) -> Response:
    """GET .../markdown: the Markdown file as text/markdown."""
    ref = documents.resolve(_cfg(request), job_id, doc_id)
    return PlainTextResponse(
        ref.md_path.read_text(encoding="utf-8"), media_type="text/markdown; charset=utf-8"
    )


@router.get(API + "/source")
def get_source(request: Request, job_id: str, doc_id: str) -> Response:
    """GET .../source: the original file, shown inline. 404 if there is none.

    Used by the side-by-side tab's iframe. An HTML source gets its own
    sandbox CSP (the app middleware keeps it, see security_headers).
    """
    ref = documents.resolve(_cfg(request), job_id, doc_id)
    source = ref.source_path()
    if source is None:
        raise HTTPException(status_code=404, detail="no source file")
    headers = {"X-Content-Type-Options": "nosniff"}
    if source.suffix in (".html", ".htm"):
        # shown in a sandboxed frame: no scripts, no same-origin access
        # (a hostile page must not run in our origin, even when opened directly).
        # 'unsafe-inline' styles only, so the page still looks like itself.
        headers["Content-Security-Policy"] = (
            "sandbox; default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'"
        )
    media_type = documents.MEDIA_TYPES.get(source.suffix, "application/octet-stream")
    return FileResponse(source, media_type=media_type, headers=headers,
                        content_disposition_type="inline")  # fmt: skip


@router.get(API + "/report")
def get_report(request: Request, job_id: str, doc_id: str) -> dict:
    """GET .../report: specval's JSON report. 404 if not validated yet."""
    report = documents.read_report(documents.resolve(_cfg(request), job_id, doc_id))
    if report is None:
        raise HTTPException(status_code=404, detail="no validation report")
    return report


@router.get(API + "/download.zip")
def get_zip(request: Request, job_id: str, doc_id: str) -> Response:
    """GET .../download.zip: Markdown, media and report as an attachment (WB13)."""
    ref = documents.resolve(_cfg(request), job_id, doc_id)
    # doc_id is safe in the header: DOC_ID allows no quotes or spaces.
    return Response(
        documents.zip_bytes(ref),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{ref.doc_id}.zip"'},
    )


@router.post(API + "/approve")
def post_approve(request: Request, job_id: str, doc_id: str, body: ApproveBody) -> dict:
    """POST .../approve: approve with a name and optional override reason.

    Returns the record with library_version. 409 when specval refuses.
    """
    cfg = _cfg(request)
    ref = documents.resolve(cfg, job_id, doc_id)
    return documents.approve(cfg, ref, body.name, body.override_reason)


@router.patch(API + "/metadata")
def patch_metadata(request: Request, job_id: str, doc_id: str, body: MetadataBody) -> dict:
    """PATCH .../metadata: set title and/or spec_version; returns the front matter."""
    ref = documents.resolve(_cfg(request), job_id, doc_id)
    return documents.update_metadata(ref, body.title, body.spec_version)


@router.get("/api/library")
def get_library(request: Request) -> list[dict]:
    """GET /api/library: the latest version of each approved document."""
    return documents.library(_cfg(request))


# ------------------------------------------------------------------- pages

PAGE = "/documents/{job_id}/{doc_id}"


@router.get(PAGE, response_class=HTMLResponse)
def document_page(request: Request, job_id: str, doc_id: str, tab: str = "rendered"):
    """GET /documents/<job>/<doc>?tab=...: the Document page (document.html).

    Only the selected tab's content is built (rendering, Pandoc preview).
    ?message= shows the result of an approval. 404 for an unknown document.
    """
    ref = documents.resolve(_cfg(request), job_id, doc_id)
    tab = tab if tab in TABS else "rendered"
    text = ref.md_path.read_text(encoding="utf-8")
    body = split_front_matter(text)[1]  # the rendered tabs show the body only
    base = f"/documents/{job_id}/{doc_id}/files"
    info = documents.details(ref)
    report = documents.read_report(ref)
    source = ref.source_path()
    context = {
        "doc": info,
        "job_id": job_id,
        "doc_id": doc_id,
        "tab": tab,
        "tabs": TABS,
        "report": report,
        "queue": (report or {}).get("review_queue", []),
        # Approve starts disabled if there are fail items; review.js enables it (WB10).
        "fail_items": sum(1 for i in (report or {}).get("review_queue", []) if i["kind"] == "fail"),
        "source_kind": source.suffix.lstrip(".") if source else None,
        "api": f"/api/documents/{job_id}/{doc_id}",
        "page": f"/documents/{job_id}/{doc_id}",
        "message": request.query_params.get("message"),
    }
    if tab in ("rendered", "side-by-side"):
        context["rendered"] = render_markdown(body, base)
    if tab == "raw":
        context["lines"] = list(enumerate(text.split("\n"), start=1))
    if tab == "side-by-side" and source is not None and source.suffix == ".docx":
        context["docx_preview"] = docx_preview(source)
    if tab == "report":
        report_md = ref.validation / f"{doc_id}.md"
        context["report_html"] = (
            render_markdown(report_md.read_text(encoding="utf-8")) if report_md.is_file() else None
        )
    return templates.TemplateResponse(request, "document.html", context)


@router.get(PAGE + "/files/{path:path}")
def document_file(request: Request, job_id: str, doc_id: str, path: str) -> Response:
    """GET /documents/<job>/<doc>/files/<path>: a file of the document's output
    folder (images in media/, for the rendered Markdown). 404 outside it."""
    ref = documents.resolve(_cfg(request), job_id, doc_id)
    return FileResponse(documents.safe_file(ref.folder, path),
                        headers={"X-Content-Type-Options": "nosniff"})  # fmt: skip


@router.post(PAGE + "/approve")
def approve_form(
    request: Request,
    job_id: str,
    doc_id: str,
    name: Annotated[str | None, Form()] = None,
    override_reason: Annotated[str | None, Form()] = None,
    fails_reviewed: Annotated[str | None, Form()] = None,
):
    """POST /documents/<job>/<doc>/approve: the Review tab's Approve form.

    Always redirects (303) back to the Review tab with ?message= ("approved"
    or specval's reason for refusing). 404 for an unknown document.
    """
    cfg = _cfg(request)
    ref = documents.resolve(cfg, job_id, doc_id)
    override = (override_reason or "").strip() or None
    if override is None and fails_reviewed:
        # every fail item was ticked in the review queue (deviation WB10)
        # The server does not re-check the ticks: specval only needs an override
        # reason, and this one says what the user confirmed.
        override = f"all {fails_reviewed} fail item(s) reviewed and ticked in the web UI"
    try:
        documents.approve(cfg, ref, name, override)
        message = "approved"
    except HTTPException as exc:
        message = str(exc.detail)
    return RedirectResponse(
        f"/documents/{job_id}/{doc_id}?tab=review&message={_q(message)}", status_code=303
    )


@router.post(PAGE + "/metadata")
def metadata_form(
    request: Request,
    job_id: str,
    doc_id: str,
    title: Annotated[str | None, Form()] = None,
    spec_version: Annotated[str | None, Form()] = None,
):
    """POST /documents/<job>/<doc>/metadata: the "Title and version" form.

    Redirects (303) to the Document page. Empty fields clear the value.
    """
    ref = documents.resolve(_cfg(request), job_id, doc_id)
    documents.update_metadata(ref, title or "", spec_version or "")
    return RedirectResponse(f"/documents/{job_id}/{doc_id}", status_code=303)


@router.get("/library", response_class=HTMLResponse)
def library_page(request: Request):
    """GET /library: the Library page (library.html)."""
    return templates.TemplateResponse(
        request, "library.html", {"items": documents.library(_cfg(request))}
    )


@router.get("/library/{doc_id}/{version}/{path:path}")
def library_file(request: Request, doc_id: str, version: str, path: str) -> Response:
    """GET /library/<doc>/<version>/<path>: a file of an approved version.

    404 for a malformed id or version, or a path outside that version's
    folder (no path traversal).
    """
    if not documents.DOC_ID.match(doc_id) or not version.isdigit():
        raise HTTPException(status_code=404, detail="no such file")
    root = documents.library_root(_cfg(request)) / doc_id / version
    target = documents.safe_file(root, path)
    media_type = "text/markdown; charset=utf-8" if target.suffix == ".md" else None
    return FileResponse(
        target, media_type=media_type, headers={"X-Content-Type-Options": "nosniff"}
    )


def _q(text: str) -> str:
    """URL-quote a message for ?message=, cut to 300 characters."""
    from urllib.parse import quote

    return quote(text[:300])
