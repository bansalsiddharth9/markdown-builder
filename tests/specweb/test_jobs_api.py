"""Job API, worker steps and the Upload and Jobs pages (build phase 9)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

from .conftest import upload

PDF = b"%PDF-1.7\n%fake\n"
HTML = b"<html><body><h1>Spec</h1></body></html>"


def test_submit_file_job_runs_convert_then_validate(client, fake_runner) -> None:
    response = upload(client, files=[("CTA 608-E.pdf", PDF)], submitted_by="Ana", split="2")
    assert response.status_code == 201, response.text
    job = response.json()
    assert job["submitted_by"] == "Ana" and job["options"] == {"split": 2}

    job = client.get(f"/api/jobs/{job['id']}").json()
    assert job["status"] == "done"
    [doc] = job["documents"]
    assert doc["source_name"] == "CTA 608-E.pdf"
    assert doc["doc_id"] == "cta-608-e" and doc["status"] == "ok"
    assert doc["validation_status"] == "pass" and doc["converter"] == "pandoc"
    steps = [call[0] for call in fake_runner.calls]
    assert steps == ["convert", "run"]
    convert = fake_runner.calls[0]
    assert convert[convert.index("--split") + 1] == "2"


def test_files_stored_under_safe_names(client, tmp_path: Path) -> None:
    job = upload(client, files=[("../../etc/passwd.html", HTML), ("a b.html", HTML)]).json()
    stored = sorted(p.name for p in (tmp_path / "data/jobs" / job["id"] / "input").iterdir())
    assert stored == ["a-b.html", "passwd.html"]


def test_url_job_fetches_first(client, fake_runner, tmp_path: Path) -> None:
    urls = [{"url": "https://www.w3.org/TR/ttml2/", "render": "static"},
            {"url": "https://example.org/app", "render": "browser"}]  # fmt: skip
    job = upload(client, urls=urls).json()
    sources = yaml.safe_load((tmp_path / "data/jobs" / job["id"] / "sources.yaml").read_text())
    assert sources["sources"] == [
        {"id": "www-w3-org-tr-ttml2", "url": "https://www.w3.org/TR/ttml2/", "render": "static"},
        {"id": "example-org-app", "url": "https://example.org/app", "render": "browser"},
    ]
    assert [call[0] for call in fake_runner.calls] == ["fetch", "convert", "run"]
    job = client.get(f"/api/jobs/{job['id']}").json()
    assert job["status"] == "done"
    assert {d["status"] for d in job["documents"]} == {"failed"}
    assert job["documents"][0]["error"] == "HTTP 404"


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({}, "no files or URLs"),
        ({"files": [("notes.txt", b"x")]}, "notes.txt: only .pdf, .html, .htm and .docx"),
        ({"urls": [{"url": "ftp://x.org/a"}]}, "must start with http:// or https://"),
        ({"urls": [{"url": "https://x.org/", "render": "magic"}]}, "render must be"),
        ({"files": [("a.pdf", PDF)], "split": "9"}, "split must be"),
    ],
)
def test_bad_submissions_rejected(client, kwargs, message) -> None:
    response = upload(client, **kwargs)
    assert response.status_code == 400 and message in response.json()["detail"]


def test_bad_urls_json_rejected(client) -> None:
    response = client.post("/api/jobs", data={"urls": "not json"})
    assert response.status_code == 400 and "urls" in response.json()["detail"]


def test_step_error_and_timeout_mark_job_error(client, fake_runner) -> None:
    fake_runner.fail_step = "convert"
    job = upload(client, files=[("a.pdf", PDF)]).json()
    job = client.get(f"/api/jobs/{job['id']}").json()
    assert job["status"] == "error" and "convert exited with code 2" in job["error"]
    assert job["documents"][0]["status"] == "error"

    fake_runner.fail_step, fake_runner.timeout_step = None, "run"
    job = upload(client, files=[("b.pdf", PDF)]).json()
    job = client.get(f"/api/jobs/{job['id']}").json()
    assert job["status"] == "error" and "did not finish" in job["error"]


def test_list_newest_first_and_filter(client, fake_runner) -> None:
    first = upload(client, files=[("a.pdf", PDF)]).json()
    fake_runner.fail_step = "convert"
    second = upload(client, files=[("b.pdf", PDF)]).json()
    jobs = client.get("/api/jobs").json()
    assert [j["id"] for j in jobs][:2] == [second["id"], first["id"]]
    errors = client.get("/api/jobs", params={"status": "error"}).json()
    assert [j["id"] for j in errors] == [second["id"]]


def test_delete_job(client, tmp_path: Path) -> None:
    job = upload(client, files=[("a.pdf", PDF)]).json()
    assert (tmp_path / "data/jobs" / job["id"]).is_dir()
    assert client.delete(f"/api/jobs/{job['id']}").status_code == 204
    assert not (tmp_path / "data/jobs" / job["id"]).exists()
    assert client.get(f"/api/jobs/{job['id']}").status_code == 404
    assert client.delete(f"/api/jobs/{job['id']}").status_code == 404


def test_healthz(client) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok" and body["checks"]["database"] == "ok"


# ------------------------------------------------------------------ pages


def test_upload_page_states_open_access_and_copyright(client) -> None:
    page = client.get("/").text
    assert "no accounts" in page.lower() and "internal reference only" in page.lower()
    assert 'type="file"' in page and 'name="urls_text"' in page


def test_upload_form_redirects_to_job(client) -> None:
    response = client.post(
        "/jobs",
        files=[("files", ("a.pdf", PDF, "application/pdf"))],
        data={"urls_text": "https://www.w3.org/TR/ttml2/\nhttps://example.org/app browser",
              "submitted_by": "Ana"},
        follow_redirects=False,
    )  # fmt: skip
    assert response.status_code == 303
    job_page = client.get(response.headers["location"]).text
    assert "a.pdf" in job_page and "https://example.org/app" in job_page


def test_jobs_page_lists_jobs(client) -> None:
    upload(client, files=[("a.pdf", PDF)], submitted_by="Ana")
    page = client.get("/jobs").text
    assert "Ana" in page and "done" in page


# ----------------------------------------------------------------- worker


def test_run_step_runs_subprocess_with_timeout(tmp_path: Path) -> None:
    from specweb import worker

    log = tmp_path / "step.log"
    code = worker.run_step_command([sys.executable, "-c", "print('hello')"], 30, log)
    assert code == 0 and "hello" in log.read_text()
    with pytest.raises(worker.StepTimeout):
        worker.run_step_command([sys.executable, "-c", "import time; time.sleep(5)"], 0.5, log)


def _set_status(tmp_path: Path, job_id: str, status: str) -> None:
    from specweb.db import Job, sessions

    with sessions(tmp_path / "data")() as session:
        session.get(Job, job_id).status = status
        session.commit()


def test_job_page_has_delete_button(client) -> None:
    """Design, Job detail: 'delete the job'. The button is visible at once;
    it opens a confirmation dialog, and only the dialog's button deletes."""
    job = upload(client, files=[("a.pdf", PDF)]).json()
    page = client.get(f"/jobs/{job['id']}").text
    assert "<details" not in page
    assert '<button type="button" class="danger" data-open-dialog="confirm-delete">' in page
    dialog = page[page.index('<dialog id="confirm-delete"') : page.index("</dialog>")]
    assert f'<form method="post" action="/jobs/{job["id"]}/delete">' in dialog
    assert "Approved documents stay in the Library" in dialog
    assert 'formmethod="dialog"' in dialog  # Cancel closes it without a request
    assert '<script src="/static/dialog.js"></script>' in page


def test_dialog_script_served(client) -> None:
    """Our own file, so the CSP (script-src 'self') needs no change."""
    script = client.get("/static/dialog.js")
    assert script.status_code == 200
    assert "showModal" in script.text and "data-open-dialog" in script.text


def test_delete_button_removes_job_and_files(client, tmp_path: Path) -> None:
    job = upload(client, files=[("a.pdf", PDF)]).json()
    response = client.post(f"/jobs/{job['id']}/delete", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/jobs?message=")
    assert not (tmp_path / "data/jobs" / job["id"]).exists()
    assert client.get(f"/api/jobs/{job['id']}").status_code == 404
    jobs_page = client.get(response.headers["location"]).text
    assert f"Job {job['id']} deleted" in jobs_page


def test_running_job_cannot_be_deleted(client, tmp_path: Path) -> None:
    job = upload(client, files=[("a.pdf", PDF)]).json()
    _set_status(tmp_path, job["id"], "converting")
    assert 'data-open-dialog="confirm-delete"' not in client.get(f"/jobs/{job['id']}").text
    response = client.post(f"/jobs/{job['id']}/delete")  # follows the redirect
    assert "delete it when it is done" in response.text
    assert (tmp_path / "data/jobs" / job["id"]).is_dir()


def test_delete_unknown_job_is_404(client) -> None:
    assert client.post("/jobs/000000000000/delete").status_code == 404


def test_delete_button_appears_when_polling_sees_the_job_finish(client, tmp_path: Path) -> None:
    """The button is in the polled fragment, so it shows up without a reload
    once the job is done; the dialog stays outside it, so polling never
    closes an open dialog."""
    job = upload(client, files=[("a.pdf", PDF)]).json()
    _set_status(tmp_path, job["id"], "converting")
    assert "data-open-dialog" not in client.get(f"/jobs/{job['id']}/documents").text
    _set_status(tmp_path, job["id"], "done")
    fragment = client.get(f"/jobs/{job['id']}/documents").text
    assert '<button type="button" class="danger" data-open-dialog="confirm-delete">' in fragment
    assert "<dialog" not in fragment


def test_unexpected_worker_error_does_not_leave_job_running(
    client, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Any error ends the job as "error" (not stuck in "converting"), so it can
    be read, deleted and expired."""
    from specweb import worker

    def broken_manifest(*_args) -> None:
        raise KeyError("id")

    monkeypatch.setattr(worker, "_record_conversion", broken_manifest)
    job = upload(client, files=[("a.pdf", PDF)]).json()
    job = client.get(f"/api/jobs/{job['id']}").json()
    assert job["status"] == "error"
    assert "KeyError" in job["error"]
    assert [d["status"] for d in job["documents"]] == ["error"]
    assert client.delete(f"/api/jobs/{job['id']}").status_code == 204


def test_worker_start_ends_jobs_cut_off_by_a_stop(client, tmp_path: Path) -> None:
    """A job that was running when the worker stopped is lost from the queue;
    at the next start it becomes "error" instead of running for ever (HP6).
    Queued jobs stay: the queue still has them."""
    from specweb.settings import WebConfig
    from specweb.worker import recover_interrupted_jobs

    cut_off, waiting, finished = (upload(client, files=[("a.pdf", PDF)]).json() for _ in range(3))
    _set_status(tmp_path, cut_off["id"], "converting")
    _set_status(tmp_path, waiting["id"], "queued")

    assert recover_interrupted_jobs(WebConfig(data_dir=tmp_path / "data")) == [cut_off["id"]]
    job = client.get(f"/api/jobs/{cut_off['id']}").json()
    assert job["status"] == "error" and "worker stopped" in job["error"]
    assert client.get(f"/api/jobs/{waiting['id']}").json()["status"] == "queued"
    assert client.get(f"/api/jobs/{finished['id']}").json()["status"] == "done"


def test_worker_command_recovers_before_consuming(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`specweb worker` runs the recovery before it takes the first task."""
    import huey.consumer
    from specweb import cli, worker

    calls: list[str] = []
    monkeypatch.setattr(worker, "recover_interrupted_jobs", lambda cfg: calls.append("recover"))

    class FakeConsumer:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def run(self) -> None:
            calls.append("consume")

    monkeypatch.setattr(huey.consumer, "Consumer", FakeConsumer)
    config = tmp_path / "specweb.yaml"
    config.write_text(f"data_dir: {tmp_path / 'data'}\n", encoding="utf-8")
    cli.worker(config)
    assert calls == ["recover", "consume"]
