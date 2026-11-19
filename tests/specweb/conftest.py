"""specweb test setup (design: phase 9 API tests with FastAPI's test client
and a fake converter).

The fake runner stands in for the `specconv` and `specval` subprocesses: it
writes the files they would write (manifest, Markdown, reports), so the web
app is tested without Docling or Pandoc. Huey runs tasks immediately.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

# The queue module reads its location when imported; keep it out of the repo.
os.environ.setdefault("SPECWEB_DATA", tempfile.mkdtemp(prefix="specweb-test-"))

from common.frontmatter import FrontMatter, render_document  # noqa: E402
from common.manifest import Manifest, ManifestEntry, write_manifest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


class FakeRunner:
    """Records the steps and writes what the real tools would write."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.fail_step: str | None = None  # "fetch" | "convert" | "run": exit code 2
        self.timeout_step: str | None = None
        self.validation = "pass"

    def __call__(self, args: list[str], timeout: float, log_path: Path) -> int:
        from specweb.worker import StepTimeout

        self.calls.append(args)
        step = args[0]
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(f"fake {step}\n", encoding="utf-8")
        if step == self.timeout_step:
            raise StepTimeout(f"{step} did not finish within {timeout:g} s")
        if step == self.fail_step:
            return 2
        opts = dict(zip(args[1::2], args[2::2], strict=False))
        if step == "convert":
            self.convert(opts)
        elif step == "run":
            self.validate(opts)
        return 0

    def convert(self, opts: dict[str, str]) -> None:
        output = Path(opts["--output"])
        entries = []
        inputs = sorted(Path(opts["--input"]).iterdir()) if "--input" in opts else []
        for path in inputs:
            doc_id = path.stem
            folder = output / doc_id
            folder.mkdir(parents=True, exist_ok=True)
            fm = FrontMatter(id=doc_id, source_type=path.suffix[1:], source_file=path.name)
            (folder / f"{doc_id}.md").write_text(render_document(fm, "# Title\n\nText.\n"))
            (folder / f"source{path.suffix}").write_bytes(path.read_bytes())
            entries.append(
                ManifestEntry(id=doc_id, source=path.name, source_type=path.suffix[1:],
                              status="ok", converter="pandoc")
            )  # fmt: skip
        if "--sources" in opts:
            import yaml

            for source in yaml.safe_load(Path(opts["--sources"]).read_text())["sources"]:
                entries.append(
                    ManifestEntry(id=source["id"], source=source["url"], source_type="web",
                                  status="failed", error="HTTP 404")
                )  # fmt: skip
        write_manifest(Manifest(entries=entries), output / "manifest.json")

    def validate(self, opts: dict[str, str]) -> None:
        validation = Path(opts["--validation"])
        validation.mkdir(parents=True, exist_ok=True)
        manifest = json.loads((Path(opts["--output"]) / "manifest.json").read_text())
        for entry in manifest["documents"]:
            overall = self.validation if entry["status"] == "ok" else "not_converted"
            (validation / f"{entry['id']}.json").write_text(json.dumps({"overall": overall}))


@pytest.fixture
def fake_runner(monkeypatch: pytest.MonkeyPatch) -> FakeRunner:
    from specweb import worker

    runner = FakeRunner()
    monkeypatch.setattr(worker, "run_step", runner)
    return runner


@pytest.fixture
def client(tmp_path: Path, fake_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch):
    from specweb.app import create_app
    from specweb.settings import WebConfig
    from specweb.tasks import huey

    monkeypatch.setattr(huey, "immediate", True)
    app = create_app(WebConfig(data_dir=tmp_path / "data"))
    with TestClient(app) as test_client:
        yield test_client


def upload(client, files=(), urls=None, **form):
    data = dict(form)
    if urls is not None:
        data["urls"] = json.dumps(urls)
    return client.post(
        "/api/jobs",
        files=[("files", (name, content, "application/octet-stream")) for name, content in files],
        data=data,
    )
