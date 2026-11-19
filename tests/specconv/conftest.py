from __future__ import annotations

import shutil
from pathlib import Path

import pytest


@pytest.fixture
def input_dir(tmp_path: Path, fixtures: dict[str, Path]) -> Path:
    """A fresh input folder with the HTML and DOCX fixture (and the image the HTML uses)."""
    folder = tmp_path / "input"
    folder.mkdir()
    for name in ("html", "docx", "image"):
        shutil.copy(fixtures[name], folder / fixtures[name].name)
    return folder


@pytest.fixture(scope="session")
def pdf_fixtures(fixtures: dict[str, Path], fixture_mod: dict, repo_root_session: Path) -> dict:
    """PDF fixtures printed with Playwright's Chromium (generated once per session)."""
    import os

    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(repo_root_session / ".playwright"))
    return fixture_mod["render_pdfs"](fixtures["html"].parent, fixtures["html"])


@pytest.fixture(scope="session")
def repo_root_session() -> Path:
    return Path(__file__).resolve().parents[2]


# ------------------------------------------------------------ local web site


class Site:
    """Pages served by a local HTTP server on 127.0.0.1 (tests never use the internet).

    add() registers a path; hits counts requests per path. A page can fail
    `fail_times` times with `fail_status` before it succeeds, or wait `delay` s.
    """

    def __init__(self) -> None:
        from collections import Counter

        self.routes: dict[str, dict] = {}
        self.hits: Counter[str] = Counter()
        self.base = ""
        self.user_agents: list[str] = []

    def url(self, path: str) -> str:
        return self.base + path

    def add(
        self,
        path: str,
        body: bytes | str = b"",
        *,
        status: int = 200,
        content_type: str | None = "text/html; charset=utf-8",
        headers: dict[str, str] | None = None,
        fail_times: int = 0,
        fail_status: int = 503,
        delay: float = 0.0,
    ) -> str:
        self.routes[path] = dict(
            body=body.encode("utf-8") if isinstance(body, str) else body,
            status=status,
            content_type=content_type,
            headers=headers or {},
            fail_times=fail_times,
            fail_status=fail_status,
            delay=delay,
        )
        return self.url(path)

    def redirect(self, path: str, to: str, status: int = 301) -> str:
        return self.add(path, status=status, content_type=None, headers={"Location": to})


@pytest.fixture
def site(monkeypatch: pytest.MonkeyPatch):
    import threading
    import time
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.delenv(name, raising=False)
    s = Site()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (http.server API)
            path = self.path.split("?")[0]
            s.hits[path] += 1
            s.user_agents.append(self.headers.get("User-Agent", ""))
            route = s.routes.get(path)
            if route is None:
                self.send_error(404)
                return
            if route["delay"]:
                time.sleep(route["delay"])
            if route["fail_times"] > 0:
                route["fail_times"] -= 1
                self.send_error(route["fail_status"])
                return
            self.send_response(route["status"])
            if route["content_type"]:
                self.send_header("Content-Type", route["content_type"])
            for key, value in route["headers"].items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(route["body"])))
            self.end_headers()
            try:
                self.wfile.write(route["body"])
            except (BrokenPipeError, ConnectionResetError):
                pass  # the client gave up (timeout tests)

        def log_message(self, *args) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    s.base = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield s
    server.shutdown()
    server.server_close()


@pytest.fixture
def no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retries without waiting between attempts."""
    from specconv import fetch

    monkeypatch.setattr(fetch, "RETRY_BACKOFF_S", 0.0)


@pytest.fixture(scope="session")
def chromium_env(repo_root_session: Path) -> None:
    """Point Playwright at the project's Chromium (installed under .playwright/)."""
    import os

    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(repo_root_session / ".playwright"))
