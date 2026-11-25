"""Document and Library pages, approval, metadata and downloads (build phase 10)."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from common.frontmatter import parse_document

from .conftest import upload

PDF = b"%PDF-1.7\n%fake\n"


@pytest.fixture
def doc(client):
    """A converted PDF document: (job_id, doc_id)."""
    job = upload(client, files=[("Spec A.pdf", PDF)], submitted_by="Ana").json()
    job = client.get(f"/api/jobs/{job['id']}").json()
    return job["id"], job["documents"][0]["doc_id"]


def md_file(tmp_path: Path, job_id: str, doc_id: str) -> Path:
    return tmp_path / "data/jobs" / job_id / "output" / doc_id / f"{doc_id}.md"


# --------------------------------------------------------------------- API


def test_document_metadata(client, doc) -> None:
    job_id, doc_id = doc
    data = client.get(f"/api/documents/{job_id}/{doc_id}").json()
    assert data["doc_id"] == "spec-a" and data["source_name"] == "Spec A.pdf"
    assert data["front_matter"]["id"] == "spec-a" and data["front_matter"]["validated"] is None
    assert data["validation"]["overall"] == "pass"
    assert data["validation"]["checks"] == {"Validation1": "pass", "Validation7": "pass"}


def test_raw_markdown_source_and_report(client, doc) -> None:
    job_id, doc_id = doc
    base = f"/api/documents/{job_id}/{doc_id}"
    md = client.get(f"{base}/markdown")
    assert md.headers["content-type"].startswith("text/markdown") and "# Title" in md.text
    source = client.get(f"{base}/source")
    assert source.content == PDF and source.headers["content-type"] == "application/pdf"
    assert client.get(f"{base}/report").json()["overall"] == "pass"


def test_download_zip(client, doc) -> None:
    job_id, doc_id = doc
    response = client.get(f"/api/documents/{job_id}/{doc_id}/download.zip")
    assert response.headers["content-type"] == "application/zip"
    names = set(zipfile.ZipFile(io.BytesIO(response.content)).namelist())
    assert {"spec-a/spec-a.md", "spec-a/media/fig.png", "spec-a/validation.json"} <= names


@pytest.mark.parametrize(
    "path",
    [
        "/api/documents/000000000000/x",
        "/api/documents/{job}/nope",
        "/api/documents/{job}/..%2F..%2Fx",
    ],
)
def test_unknown_documents_404(client, doc, path: str) -> None:
    assert client.get(path.format(job=doc[0])).status_code == 404


def test_media_files_served_and_traversal_refused(client, doc) -> None:
    job_id, doc_id = doc
    base = f"/documents/{job_id}/{doc_id}/files"
    assert client.get(f"{base}/media/fig.png").content == b"\x89PNG fake"
    assert client.get(f"{base}/../../../specweb.sqlite").status_code == 404
    assert client.get(f"{base}/..%2F..%2Fmanifest.json").status_code == 404


# ------------------------------------------------------- approve, metadata


def test_approve_passed_document_and_library_copy(client, doc, tmp_path: Path) -> None:
    job_id, doc_id = doc
    response = client.post(f"/api/documents/{job_id}/{doc_id}/approve", json={"name": "Ben"})
    assert response.status_code == 200, response.text
    data = client.get(f"/api/documents/{job_id}/{doc_id}").json()
    assert data["approved_by"] == "Ben" and data["approved_at"]
    assert data["front_matter"]["validated"]["by"] == "Ben"
    copy = tmp_path / "data/library/spec-a/1"
    assert (copy / "spec-a.md").is_file() and (copy / "media/fig.png").is_file()
    meta = json.loads((copy / "meta.json").read_text())
    assert meta["approved_by"] == "Ben" and meta["job_id"] == job_id and meta["version"] == 1
    library = client.get("/api/library").json()
    assert [(d["doc_id"], d["version"], d["approved_by"]) for d in library] == [
        ("spec-a", 1, "Ben")
    ]


def test_approve_failed_document_needs_override(client, fake_runner) -> None:
    fake_runner.validation = "fail"
    job = upload(client, files=[("b.pdf", PDF)]).json()
    url = f"/api/documents/{job['id']}/b/approve"
    response = client.post(url, json={"name": "Ben"})
    assert response.status_code == 409 and "--override" in response.json()["detail"]
    response = client.post(url, json={"name": "Ben", "override_reason": "image is decorative"})
    assert response.status_code == 200
    data = client.get(f"/api/documents/{job['id']}/b").json()
    assert data["override_reason"] == "image is decorative"
    assert data["front_matter"]["manual_fixes"][-1]["reason"] == "image is decorative"


def test_reapproval_is_a_new_library_version(client, doc, tmp_path: Path) -> None:
    job_id, doc_id = doc
    for _ in range(2):
        assert (
            client.post(
                f"/api/documents/{job_id}/{doc_id}/approve", json={"name": "Ben"}
            ).status_code
            == 200
        )
    assert (tmp_path / "data/library/spec-a/2/meta.json").is_file()
    library = client.get("/api/library").json()
    assert [(d["doc_id"], d["version"]) for d in library] == [("spec-a", 2)]


def test_metadata_patch_keeps_body_and_approval_still_works(client, doc, tmp_path: Path) -> None:
    job_id, doc_id = doc
    before = parse_document(md_file(tmp_path, job_id, doc_id).read_text())[1]
    response = client.patch(
        f"/api/documents/{job_id}/{doc_id}/metadata",
        json={"title": "Sample Spec", "spec_version": "E (2011)"},
    )
    assert response.status_code == 200
    fm, body = parse_document(md_file(tmp_path, job_id, doc_id).read_text())
    assert (fm.title, fm.spec_version) == ("Sample Spec", "E (2011)") and body == before
    assert client.post(f"/api/documents/{job_id}/{doc_id}/approve", json={}).status_code == 200
    assert client.get("/api/library").json()[0]["title"] == "Sample Spec"


def test_metadata_patch_rejects_other_fields(client, doc) -> None:
    job_id, doc_id = doc
    response = client.patch(f"/api/documents/{job_id}/{doc_id}/metadata", json={"validated": {}})
    assert response.status_code == 422


# ------------------------------------------------------------------ pages


def test_document_page_rendered_and_sanitized(client, doc) -> None:
    job_id, doc_id = doc
    page = client.get(f"/documents/{job_id}/{doc_id}").text
    assert "<script>alert" not in page  # raw HTML from the Markdown is sanitized
    assert f'src="/documents/{job_id}/{doc_id}/files/media/fig.png"' in page
    assert 'id="x"' in page and 'href="#x"' in page  # in-page anchors kept
    assert "<table>" in page
    for tab in ("rendered", "raw", "side-by-side", "report", "review"):
        assert f"tab={tab}" in page


def test_side_by_side_shows_pdf_in_viewer(client, doc) -> None:
    job_id, doc_id = doc
    page = client.get(f"/documents/{job_id}/{doc_id}?tab=side-by-side").text
    assert f'<iframe class="source" src="/api/documents/{job_id}/{doc_id}/source"' in page


def test_html_source_shown_sandboxed(client) -> None:
    job = upload(client, files=[("p.html", b"<p>Hi</p><script>x()</script>")]).json()
    page = client.get(f"/documents/{job['id']}/p?tab=side-by-side").text
    assert 'sandbox=""' in page
    source = client.get(f"/api/documents/{job['id']}/p/source")
    assert "sandbox" in source.headers["content-security-policy"]


def test_docx_source_preview(client, fixtures) -> None:
    job = upload(client, files=[("s.docx", fixtures["docx"].read_bytes())]).json()
    page = client.get(f"/documents/{job['id']}/s?tab=side-by-side").text
    assert "Sample Caption Display Specification" in page  # Pandoc preview of the DOCX
    assert f"/api/documents/{job['id']}/s/source" in page  # and a download link


def test_doc_source_preview_uses_libreoffice_copy(client, fake_runner, fixtures) -> None:
    """A .doc has no preview of its own; LibreOffice's .docx is shown (DOC1)."""
    fake_runner.doc_docx = fixtures["docx"].read_bytes()
    doc = bytes.fromhex("d0cf11e0a1b11ae1") + b"\x00" * 504
    job = upload(client, files=[("old.doc", doc)]).json()
    page = client.get(f"/documents/{job['id']}/old?tab=side-by-side").text
    assert "Sample Caption Display Specification" in page
    assert "Download the DOC" in page and "LibreOffice" in page
    source = client.get(f"/api/documents/{job['id']}/old/source")
    assert source.content == doc and source.headers["content-type"] == "application/msword"


def test_review_queue_tab_links_and_checkboxes(client, fake_runner) -> None:
    fake_runner.validation = "fail"
    job = upload(client, files=[("b.pdf", PDF)]).json()
    page = client.get(f"/documents/{job['id']}/b?tab=review").text
    assert 'type="checkbox"' in page and 'data-kind="fail"' in page
    assert "?tab=raw#L9" in page  # the Markdown line
    assert f"/api/documents/{job['id']}/b/source#page=2" in page  # the source page
    assert "/static/review.js" in page


def test_raw_tab_has_line_anchors(client, doc) -> None:
    job_id, doc_id = doc
    page = client.get(f"/documents/{job_id}/{doc_id}?tab=raw").text
    assert 'id="L1"' in page and "&lt;script&gt;" in page


def test_approve_and_metadata_forms(client, doc) -> None:
    job_id, doc_id = doc
    base = f"/documents/{job_id}/{doc_id}"
    response = client.post(f"{base}/metadata", data={"title": "T", "spec_version": "1"},
                           follow_redirects=False)  # fmt: skip
    assert response.status_code == 303
    response = client.post(f"{base}/approve", data={"name": "Ben"}, follow_redirects=False)
    assert response.status_code == 303
    page = client.get(base).text
    assert "Approved by Ben" in page


def test_library_page(client, doc) -> None:
    job_id, doc_id = doc
    client.patch(f"/api/documents/{job_id}/{doc_id}/metadata", json={"title": "Spec A title"})
    client.post(f"/api/documents/{job_id}/{doc_id}/approve", json={"name": "Ben"})
    page = client.get("/library").text
    assert "Spec A title" in page and "Ben" in page and "/library/spec-a/1/spec-a.md" in page
    assert client.get("/library/spec-a/1/spec-a.md").text.startswith("---\n")
