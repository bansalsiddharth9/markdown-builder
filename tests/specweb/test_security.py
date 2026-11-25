"""Hardening (design: Security for an open internal port, Retention; phase 11)."""

from __future__ import annotations

import datetime as dt
import io
import re
import zipfile
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from .conftest import upload

PDF = b"%PDF-1.7\n%fake\n"
HTML = b"<!doctype html><html><body><p>x</p></body></html>"
REPO = Path(__file__).resolve().parents[2]


def docx_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", "<w:document/>")
    return buffer.getvalue()


@pytest.fixture
def make_client(tmp_path: Path, fake_runner, monkeypatch: pytest.MonkeyPatch):
    from specweb.app import create_app
    from specweb.settings import WebConfig
    from specweb.tasks import huey

    monkeypatch.setattr(huey, "immediate", True)

    def _make(**overrides) -> TestClient:
        cfg = WebConfig(data_dir=tmp_path / "data", **overrides)
        return TestClient(create_app(cfg))

    return _make


# ----------------------------------------------------------------- uploads


def test_too_many_files(make_client) -> None:
    client = make_client(max_files=2)
    response = upload(client, files=[(f"{i}.pdf", PDF) for i in range(3)])
    assert response.status_code == 400 and "at most 2 files" in response.json()["detail"]


def test_file_too_large(make_client) -> None:
    client = make_client(max_file_mb=0.001)  # about 1 kB
    response = upload(client, files=[("big.pdf", PDF + b"0" * 4000)])
    assert response.status_code == 413 and "larger than" in response.json()["detail"]


@pytest.mark.parametrize(
    "name, content, ok",
    [
        ("a.pdf", PDF, True),
        ("a.pdf", HTML, False),
        ("a.html", HTML, True),
        ("a.htm", b"<p>plain fragment</p>", True),
        ("a.html", PDF, False),
        ("a.html", b"\x00\x01binary", False),
        ("a.docx", docx_bytes(), True),
        ("a.docx", PDF, False),
        # Word 97-2003: the OLE2 signature (DOC1); a renamed text file is refused.
        ("a.doc", bytes.fromhex("d0cf11e0a1b11ae1") + b"\x00" * 504, True),
        ("a.doc", b"just text", False),
        ("a.doc", docx_bytes(), False),
    ],
)
def test_content_checked_not_just_extension(make_client, name, content, ok) -> None:
    response = upload(make_client(), files=[(name, content)])
    assert (response.status_code == 201) == ok, response.text
    if not ok:
        assert "content does not match" in response.json()["detail"]


# ------------------------------------------------------------------- URLs


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1/", "http://10.0.0.8/spec", "http://169.254.169.254/", "http://localhost/"],
)
def test_private_urls_refused_on_submission(make_client, url: str) -> None:
    response = upload(make_client(), urls=[{"url": url}])
    assert response.status_code == 400 and "not allowed" in response.json()["detail"]


def test_allowlisted_internal_host_accepted(make_client, fake_runner) -> None:
    client = make_client(fetch_allowlist=("10.0.0.8",))
    assert upload(client, urls=[{"url": "http://10.0.0.8/spec"}]).status_code == 201


def test_worker_always_blocks_private_addresses_in_fetch_and_convert(make_client, fake_runner):
    client = make_client(fetch_allowlist=("intranet.local",))
    upload(client, urls=[{"url": "https://www.w3.org/TR/ttml2/"}])
    fetch, convert = fake_runner.calls[0], fake_runner.calls[1]
    for args in (fetch, convert):
        assert "--block-private-addresses" in args
        assert args[args.index("--allow-host") + 1] == "intranet.local"


# ---------------------------------------------------------- abuse, disk


@pytest.fixture
def no_processing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Jobs stay queued (the worker is not running)."""
    from specweb import tasks

    monkeypatch.setattr(tasks, "enqueue", lambda cfg, job_id: None)


@pytest.mark.usefixtures("no_processing")
def test_queued_jobs_per_client_limited(make_client) -> None:
    client = make_client(max_jobs_per_ip=2)
    assert upload(client, files=[("a.pdf", PDF)]).status_code == 201
    assert upload(client, files=[("b.pdf", PDF)]).status_code == 201
    response = upload(client, files=[("c.pdf", PDF)])
    assert response.status_code == 429 and "2 jobs waiting" in response.json()["detail"]


@pytest.mark.usefixtures("no_processing")
def test_total_queue_limited(make_client) -> None:
    client = make_client(max_queue=1)
    assert upload(client, files=[("a.pdf", PDF)]).status_code == 201
    response = upload(client, files=[("b.pdf", PDF)])
    assert response.status_code == 503 and "queue is full" in response.json()["detail"]


def test_low_disk_refuses_new_jobs(make_client) -> None:
    client = make_client(min_free_disk_mb=10**12)
    response = upload(client, files=[("a.pdf", PDF)])
    assert response.status_code == 507 and "disk" in response.json()["detail"]
    assert client.get("/healthz").status_code == 503


def test_jobs_page_shows_disk_usage(make_client) -> None:
    page = make_client().get("/jobs").text
    assert "Disk:" in page and "free" in page


# --------------------------------------------------------------- retention


def test_expired_jobs_deleted_library_kept(make_client, tmp_path: Path) -> None:
    from specweb.db import Job, sessions
    from specweb.retention import purge_expired
    from specweb.settings import WebConfig

    client = make_client()
    old = upload(client, files=[("old.pdf", PDF)]).json()
    new = upload(client, files=[("new.pdf", PDF)]).json()
    approved = client.post(f"/api/documents/{old['id']}/old/approve", json={"name": "Ben"})
    assert approved.status_code == 200, approved.text
    cfg = WebConfig(data_dir=tmp_path / "data", retention_days=30)
    with sessions(cfg.data_dir)() as session:
        session.get(Job, old["id"]).created_at = "2026-01-01T00:00:00Z"
        session.commit()
    deleted = purge_expired(cfg, now=dt.datetime(2026, 11, 19, tzinfo=dt.UTC))
    assert deleted == [old["id"]]
    assert client.get(f"/api/jobs/{old['id']}").status_code == 404
    assert not (tmp_path / "data/jobs" / old["id"]).exists()
    assert client.get(f"/api/jobs/{new['id']}").status_code == 200
    assert (tmp_path / "data/library/old/1/old.md").is_file()  # approved: kept


# ----------------------------------------------------------------- headers


def test_security_headers(make_client) -> None:
    response = make_client().get("/")
    csp = response.headers["content-security-policy"]
    assert (
        "script-src 'self'" in csp
        and "object-src 'none'" in csp
        and "frame-ancestors 'self'" in csp
    )
    assert "unsafe-inline" not in csp.split("style-src")[0]  # no inline scripts allowed
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_healthz_checks_tools(make_client) -> None:
    body = make_client().get("/healthz").json()
    assert body["checks"]["pandoc"] == "ok" and body["checks"]["queue"] == "ok"


# -------------------------------------------------------------- deployment


def test_deployment_files() -> None:
    compose = yaml.safe_load((REPO / "deploy/docker-compose.yml").read_text())
    services = compose["services"]
    assert set(services) == {"web", "worker", "proxy"}
    for name in ("web", "worker"):
        assert "healthcheck" in services[name] or name == "worker"
        assert any("/data" in v for v in services[name]["volumes"])
    assert services["proxy"]["image"].startswith("caddy:")
    caddy = (REPO / "deploy/Caddyfile").read_text()
    assert "reverse_proxy web:8000" in caddy and "tls internal" in caddy
    dockerfile = (REPO / "deploy/Dockerfile").read_text()
    assert "pandoc-3.14" in dockerfile and "uv sync --frozen" in dockerfile
    assert "playwright install" in dockerfile and "download-models" in dockerfile
    assert "USER " in dockerfile  # not root
    assert re.search(r"ENV [^\n]*(\\\n[^\n]*)*SPECWEB_DATA=/data\b", dockerfile)  # on its volume


def test_single_settings_file_for_local_and_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    from specweb.settings import load_config

    assert [p.name for p in (REPO / "deploy").glob("*.yaml")] == ["specweb.yaml"]
    monkeypatch.delenv("SPECWEB_DATA", raising=False)
    local = load_config(REPO / "deploy/specweb.yaml")
    assert local.data_dir == REPO / "data"  # git-ignored project folder
    assert local.specconv_config == REPO / "config/specconv.yaml"
    monkeypatch.setenv("SPECWEB_DATA", "/data")
    assert load_config(REPO / "deploy/specweb.yaml").data_dir == Path("/data")
