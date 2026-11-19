"""Fetching web sources (design: Web sources; build phase 4).

All pages come from a local HTTP server on 127.0.0.1: tests never use the internet.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from specconv import fetch
from specconv.config import FetchConfig, Source
from specconv.fetch import FetchError, cache_paths, fetch_source, images_dir, load_cached

PAGE = "<html><head><title>T</title></head><body><h1>Spec</h1><p>Text.</p></body></html>"
JS_PAGE = (
    "<html><body><div id='app'></div><script>"
    "document.getElementById('app').innerHTML = '<h1>Built by ' + 'script</h1>';"
    "</script></body></html>"
)


def cfg(**kw) -> FetchConfig:
    return FetchConfig(**{"timeout_s": 5.0, "retries": 2, "user_agent": "specconv-test", **kw})


def src(url: str, sid: str = "page", render: str = "static") -> Source:
    return Source(id=sid, url=url, render=render)


# ------------------------------------------------------------------ static


def test_static_fetch_saves_page_and_sidecar(site, tmp_path: Path) -> None:
    url = site.add("/spec/", PAGE, content_type="text/html; charset=iso-8859-1")
    outcome = fetch_source(src(url), cfg(), tmp_path)
    html_path, meta_path = cache_paths(tmp_path, "page")
    assert outcome.html_path == html_path and not outcome.from_cache
    assert html_path.read_bytes() == PAGE.encode("utf-8")  # exact bytes
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["url"] == url and meta["final_url"] == url and meta["status"] == 200
    assert meta["render"] == "static" and meta["encoding"] == "iso-8859-1"
    assert meta["fetched_at"].endswith("Z") and meta["bytes"] == len(PAGE)
    assert site.user_agents == ["specconv-test"]


def test_redirect_followed_and_final_url_recorded(site, tmp_path: Path) -> None:
    final = site.add("/TR/2018/REC-x/", PAGE)
    url = site.redirect("/TR/x/", final)
    record = fetch_source(src(url), cfg(), tmp_path).record
    assert record.url == url and record.final_url == final


def test_cache_reused_until_refetch(site, tmp_path: Path) -> None:
    url = site.add("/p", PAGE)
    fetch_source(src(url), cfg(), tmp_path)
    again = fetch_source(src(url), cfg(), tmp_path)
    assert again.from_cache and site.hits["/p"] == 1
    fresh = fetch_source(src(url), cfg(), tmp_path, refetch=True)
    assert not fresh.from_cache and site.hits["/p"] == 2


@pytest.mark.parametrize("change", ["url", "render", "edited", "sidecar"])
def test_stale_cache_is_not_used(site, tmp_path: Path, change: str) -> None:
    url = site.add("/p", PAGE)
    fetch_source(src(url), cfg(), tmp_path)
    html_path, meta_path = cache_paths(tmp_path, "page")
    source = src(url)
    if change == "url":
        source = src(url + "?v=2")
    elif change == "render":
        source = src(url, render="browser")
    elif change == "edited":
        html_path.write_text(PAGE + "<!-- edited -->", encoding="utf-8")
    else:
        meta_path.write_text("{", encoding="utf-8")
    assert load_cached(tmp_path, source) is None


def test_not_found_fails_without_retry(site, tmp_path: Path, no_backoff) -> None:
    with pytest.raises(FetchError, match="HTTP 404"):
        fetch_source(src(site.url("/missing")), cfg(), tmp_path)
    assert site.hits["/missing"] == 1
    assert not cache_paths(tmp_path, "page")[0].exists()


def test_server_errors_retried_then_succeed(site, tmp_path: Path, no_backoff) -> None:
    url = site.add("/flaky", PAGE, fail_times=2, fail_status=503)
    assert fetch_source(src(url), cfg(retries=2), tmp_path).record.status == 200
    assert site.hits["/flaky"] == 3


def test_server_errors_give_up_after_retries(site, tmp_path: Path, no_backoff) -> None:
    url = site.add("/down", PAGE, fail_times=99, fail_status=500)
    with pytest.raises(FetchError, match=r"HTTP 500 .*after 3 attempt\(s\)"):
        fetch_source(src(url), cfg(retries=2), tmp_path)
    assert site.hits["/down"] == 3


def test_retry_waits_double(site, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    waits: list[float] = []
    monkeypatch.setattr(fetch.time, "sleep", waits.append)
    url = site.add("/down", PAGE, fail_times=99)
    with pytest.raises(FetchError):
        fetch_source(src(url), cfg(retries=3), tmp_path)
    assert waits == [1.0, 2.0, 4.0]


def test_connection_refused_is_a_fetch_error(tmp_path: Path, no_backoff) -> None:
    import socket

    with socket.socket() as sock:  # a free port nobody listens on
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with pytest.raises(FetchError, match="network error"):
        fetch_source(src(f"http://127.0.0.1:{port}/"), cfg(retries=1), tmp_path)


def test_timeout(site, tmp_path: Path) -> None:
    url = site.add("/slow", PAGE, delay=2.5)
    with pytest.raises(FetchError, match="timed out after 1 s"):
        fetch_source(src(url), cfg(timeout_s=1.0, retries=0), tmp_path)


def test_non_html_content_is_rejected(site, tmp_path: Path) -> None:
    url = site.add("/spec.pdf", b"%PDF-1.7", content_type="application/pdf")
    with pytest.raises(FetchError, match="not an HTML page .*application/pdf"):
        fetch_source(src(url), cfg(), tmp_path)


def test_failed_refetch_keeps_good_cache(site, tmp_path: Path, no_backoff) -> None:
    url = site.add("/p", PAGE)
    first = fetch_source(src(url), cfg(), tmp_path)
    site.add("/p", PAGE, fail_times=99)
    with pytest.raises(FetchError):
        fetch_source(src(url), cfg(retries=0), tmp_path, refetch=True)
    kept = load_cached(tmp_path, src(url))
    assert kept is not None and kept.record == first.record


# ----------------------------------------------------------------- browser


@pytest.mark.usefixtures("chromium_env")
def test_browser_render_runs_scripts(site, tmp_path: Path) -> None:
    url = site.add("/app", JS_PAGE)
    static = fetch_source(src(url, "s"), cfg(), tmp_path)
    assert b"Built by script" not in static.html_path.read_bytes()
    outcome = fetch_source(src(url, "b", render="browser"), cfg(), tmp_path)
    html = outcome.html_path.read_text(encoding="utf-8")
    assert "<h1>Built by script</h1>" in html
    assert outcome.record.render == "browser" and outcome.record.encoding == "utf-8"
    assert site.user_agents[-1] == "specconv-test"


@pytest.mark.usefixtures("chromium_env")
def test_browser_redirect_and_errors(site, tmp_path: Path, no_backoff) -> None:
    final = site.add("/new", PAGE)
    url = site.redirect("/old", final)
    assert fetch_source(src(url, render="browser"), cfg(), tmp_path).record.final_url == final
    with pytest.raises(FetchError, match="HTTP 404"):
        fetch_source(src(site.url("/gone"), "g", render="browser"), cfg(), tmp_path)


# ------------------------------------------------------------------ images

PNG = b"\x89PNG\r\n\x1a\n fake"


def image_page(site) -> str:
    site.add("/spec/img/fig.png", PNG, content_type="image/png")
    site.add("/spec/logo", b"<svg/>", content_type="image/svg+xml")
    site.add("/assets/b.png", PNG + b"b", content_type="image/png")
    site.add("/spec/page.png", b"<html>login</html>", content_type="text/html")
    return site.add(
        "/spec/",
        '<img src="img/fig.png"><img src="img/fig.png#x"><img src="logo">'
        f'<img src="{site.url("/assets/b.png")}"><img src="data:image/png;base64,AA==">'
        '<img src="missing.png"><img src="page.png"><p>text</p>',
    )


def test_images_downloaded_with_page(site, tmp_path: Path, no_backoff) -> None:
    outcome = fetch_source(src(image_page(site)), cfg(retries=0), tmp_path)
    record = outcome.record
    folder = images_dir(tmp_path, "page")
    files = {i["url"].removeprefix(site.base): i["file"] for i in record.images}
    assert files == {
        "/spec/img/fig.png": "fig.png",
        "/spec/logo": "logo.svg",
        "/assets/b.png": "b.png",
    }
    assert (folder / "fig.png").read_bytes() == PNG
    assert site.hits["/spec/img/fig.png"] == 1  # same image twice: one download
    failed = {f["url"].removeprefix(site.base): f["error"] for f in record.images_failed}
    assert "HTTP 404" in failed["/spec/missing.png"]
    assert "HTML page, not an image" in failed["/spec/page.png"]
    assert record.downloaded_images(tmp_path)[site.url("/spec/img/fig.png")] == folder / "fig.png"


def test_same_file_name_from_two_urls(site, tmp_path: Path) -> None:
    site.add("/a/x.png", b"1", content_type="image/png")
    site.add("/b/x.png", b"2", content_type="image/png")
    url = site.add("/p", '<img src="/a/x.png"><img src="/b/x.png">')
    record = fetch_source(src(url), cfg(), tmp_path).record
    assert [i["file"] for i in record.images] == ["x.png", "x-2.png"]


def test_cache_stale_when_image_changed_or_missing(site, tmp_path: Path, no_backoff) -> None:
    url = image_page(site)
    fetch_source(src(url), cfg(retries=0), tmp_path)
    assert load_cached(tmp_path, src(url)) is not None
    (images_dir(tmp_path, "page") / "fig.png").write_bytes(b"edited")
    assert load_cached(tmp_path, src(url)) is None
    fetch_source(src(url), cfg(retries=0), tmp_path)
    (images_dir(tmp_path, "page") / "fig.png").unlink()
    assert load_cached(tmp_path, src(url)) is None


def test_refetch_replaces_images_and_failed_refetch_keeps_them(
    site, tmp_path: Path, no_backoff
) -> None:
    url = image_page(site)
    fetch_source(src(url), cfg(retries=0), tmp_path)
    site.add("/spec/", "<p>no images now</p>")
    fetch_source(src(url), cfg(retries=0), tmp_path, refetch=True)
    assert not images_dir(tmp_path, "page").exists()
    url = image_page(site)
    fetch_source(src(url), cfg(retries=0), tmp_path, refetch=True)
    site.add("/spec/", "", fail_times=99)
    with pytest.raises(FetchError):
        fetch_source(src(url), cfg(retries=0), tmp_path, refetch=True)
    assert (images_dir(tmp_path, "page") / "fig.png").read_bytes() == PNG
    assert load_cached(tmp_path, src(url)) is not None


def test_image_retried_on_server_error(site, tmp_path: Path, no_backoff) -> None:
    site.add("/i.png", PNG, content_type="image/png", fail_times=1)
    url = site.add("/p", '<img src="/i.png">')
    record = fetch_source(src(url), cfg(retries=1), tmp_path).record
    assert [i["file"] for i in record.images] == ["i.png"] and site.hits["/i.png"] == 2


@pytest.mark.usefixtures("chromium_env")
def test_browser_render_downloads_script_added_images(site, tmp_path: Path) -> None:
    site.add("/fig.png", PNG, content_type="image/png")
    url = site.add(
        "/app",
        "<div id='a'></div><script>document.getElementById('a').innerHTML ="
        " '<img src=\"fig.png\">';</script>",
    )
    record = fetch_source(src(url, render="browser"), cfg(), tmp_path).record
    assert [i["file"] for i in record.images] == ["fig.png"]
