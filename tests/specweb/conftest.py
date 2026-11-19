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

BODY = (
    "# Title\n\n"
    'Text with ![fig](media/fig.png) and <span id="x"></span>[a link](#x).\n\n'
    "<script>alert('x')</script>\n\n"
    "| A | B |\n|---|---|\n| 1 | 2 |\n"
)


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
        opts: dict[str, str] = {}
        i = 1
        while i < len(args):  # options with values, and flags such as --block-private-addresses
            if i + 1 < len(args) and not args[i + 1].startswith("--"):
                opts[args[i]] = args[i + 1]
                i += 2
            else:
                opts[args[i]] = ""
                i += 1
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
            (folder / f"{doc_id}.md").write_text(render_document(fm, BODY))
            (folder / "media").mkdir(exist_ok=True)
            (folder / "media" / "fig.png").write_bytes(b"\x89PNG fake")
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
        from specval.runner import body_sha256

        validation = Path(opts["--validation"])
        validation.mkdir(parents=True, exist_ok=True)
        output = Path(opts["--output"])
        manifest = json.loads((output / "manifest.json").read_text())
        for entry in manifest["documents"]:
            doc_id = entry["id"]
            if entry["status"] != "ok":
                report = {
                    "id": doc_id,
                    "overall": "not_converted",
                    "checks": [],
                    "review_queue": [],
                }
            else:
                text = (output / doc_id / f"{doc_id}.md").read_text()
                fail = self.validation == "fail"
                finding = {
                    "severity": "fail",
                    "message": "image file missing: media/x.png",
                    "md_line": 9,
                    "source_loc": "page 2",
                    "excerpt": "![x](media/x.png)",
                }
                report = {
                    "id": doc_id,
                    "overall": self.validation,
                    "validated_at": "2026-11-19T00:00:00Z",
                    "markdown_sha256": "",
                    "markdown_body_sha256": body_sha256(text),
                    "checks": [
                        {"id": "V1", "name": "Structure counts", "status": "pass",
                         "summary": "counts match", "findings": [], "details": {}},
                        {"id": "V7", "name": "Markdown hygiene",
                         "status": "fail" if fail else "pass", "summary": "",
                         "findings": [finding] if fail else [], "details": {}},
                    ],
                    "review_queue": (
                        [{"kind": "fail", "check": "V7", "text": finding["message"],
                          "md_line": 9, "source_loc": "page 2", "excerpt": None}] if fail else []
                    ) + [{"kind": "sample", "check": None, "text": "page 1: compare",
                          "md_line": None, "source_loc": "page 1", "excerpt": None}],
                }  # fmt: skip
            (validation / f"{doc_id}.json").write_text(json.dumps(report))
            (validation / f"{doc_id}.md").write_text(f"# Validation report: {doc_id}\n")


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
