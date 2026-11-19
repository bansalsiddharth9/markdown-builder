"""specweb.yaml, the specweb command, and one real job end to end (no fakes)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from specweb.settings import ConfigError, WebConfig, load_config

REPO = Path(__file__).resolve().parents[2]


def test_defaults_and_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SPECWEB_DATA", raising=False)
    monkeypatch.delenv("SPECWEB_CONFIG", raising=False)
    assert load_config() == WebConfig()
    path = tmp_path / "specweb.yaml"
    path.write_text("data_dir: store\nstep_timeout_s: 60\nfetch_allowlist: [intranet.local]\n")
    cfg = load_config(path)
    assert cfg.data_dir == (tmp_path / "store").resolve() and cfg.step_timeout_s == 60.0
    assert cfg.fetch_allowlist == ("intranet.local",)
    monkeypatch.setenv("SPECWEB_DATA", str(tmp_path / "env"))
    assert load_config(path).data_dir == tmp_path / "env"


@pytest.mark.parametrize(
    "text, message",
    [("colour: red\n", "unknown keys"), ("max_files: many\n", "expected a number"),
     ("max_files: 0\n", "must be positive"), ("fetch_allowlist: x\n", "list of host names")],
)  # fmt: skip
def test_invalid_config(tmp_path: Path, text: str, message: str) -> None:
    path = tmp_path / "specweb.yaml"
    path.write_text(text)
    with pytest.raises(ConfigError, match=message):
        load_config(path)


def test_specweb_command_help() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "specweb", "--help"], capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0 and "serve" in proc.stdout and "worker" in proc.stdout


def test_real_job_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fixtures) -> None:
    """The worker runs the real `python -m specconv` and `python -m specval`."""
    from specweb.app import create_app
    from specweb.tasks import huey

    monkeypatch.setattr(huey, "immediate", True)
    cfg = WebConfig(
        data_dir=tmp_path / "data",
        specconv_config=REPO / "config" / "specconv.yaml",
        specval_config=REPO / "config" / "specval.yaml",
    )
    with TestClient(create_app(cfg)) as client:
        response = client.post(
            "/api/jobs",
            files=[("files", ("Sample Spec.html", fixtures["html"].read_bytes(), "text/html"))],
            data={"submitted_by": "Ana", "split": "2"},
        )
        assert response.status_code == 201
        job = client.get(f"/api/jobs/{response.json()['id']}").json()
    assert job["status"] == "done", job
    [doc] = job["documents"]
    assert doc["doc_id"] == "sample-spec" and doc["status"] == "ok"
    # the page's image was not uploaded with it, so V7 fails, and only V7
    assert doc["validation_status"] == "fail"
    import json

    report = json.loads(
        (tmp_path / "data/jobs" / job["id"] / "validation/sample-spec.json").read_text()
    )
    failing = {c["id"] for c in report["checks"] if c["status"] == "fail"}
    assert failing == {"V7"}
    folder = tmp_path / "data/jobs" / job["id"]
    assert (folder / "output/sample-spec/sample-spec.md").is_file()
    assert (folder / "output/sample-spec/sections/index.md").is_file()
    assert (folder / "validation/sample-spec.json").is_file()
    assert (folder / "logs/convert.log").is_file() and (folder / "logs/validate.log").is_file()
