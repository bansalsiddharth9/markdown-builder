"""`specconv fetch` and `specconv convert --sources` end to end (build phase 4).

Pages come from a local HTTP server on 127.0.0.1: tests never use the internet.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from common.frontmatter import parse_document
from common.manifest import read_manifest
from specconv import cli
from typer.testing import CliRunner

runner = CliRunner()
REPO_CONFIG = Path(__file__).resolve().parents[2] / "config" / "specconv.yaml"

SELF_LINKING = (
    "<html><head><title>X</title></head><body>"
    '<h2 id="intro">Introduction</h2><p>{text}</p>'
    '<p>See <a href="{own}#intro">the introduction</a> and <a href="#intro">again</a>, '
    '<a href="../other/#a">another spec</a>.</p></body></html>'
)
LONG_TEXT = "This paragraph is long enough to be real specification text. " * 12
JS_PAGE = (
    "<html><body><div id='app'></div><script>"
    "document.getElementById('app').innerHTML = '<h1>Built</h1><p>' + "
    f"'{LONG_TEXT}' + '</p>';</script></body></html>"
)


@pytest.fixture(autouse=True)
def in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_backoff) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


def sources(*items: str) -> str:
    Path("s.yaml").write_text("sources:\n" + "".join(f"  - {i}\n" for i in items), "utf-8")
    return "s.yaml"


def run(command: str, *extra: str):
    return runner.invoke(cli.app, [command, "--config", str(REPO_CONFIG), *extra])


def convert(*extra: str):
    return run("convert", "--sources", "s.yaml", *extra)


def entries() -> dict:
    return {e.id: e for e in read_manifest(Path("output/manifest.json")).entries}


@pytest.fixture
def spec_site(site, fixtures: dict[str, Path]):
    """The fixture spec published at a dated URL, with a 'latest' URL redirecting to it."""
    dated = site.add("/TR/2026/REC-sample-20261119/", fixtures["html"].read_bytes())
    site.add(
        "/TR/2026/REC-sample-20261119/safe-area.png",
        fixtures["image"].read_bytes(),
        content_type="image/png",
    )
    site.redirect("/TR/sample/", dated)
    return site


# ------------------------------------------------------------------ convert


def test_web_source_converted_from_fetched_page(spec_site, fixture_mod: dict) -> None:
    latest = spec_site.url("/TR/sample/")
    dated = spec_site.url("/TR/2026/REC-sample-20261119/")
    sources(f"{{id: sample, url: '{latest}'}}")
    result = convert()
    assert result.exit_code == 0, result.output

    md = Path("output/sample/sample.md").read_text(encoding="utf-8")
    fm, body = parse_document(md)
    assert fm.source_type == "web" and fm.source_url == latest and fm.source_file == ""
    assert fm.fetched_at and fm.fetched_at.endswith("Z")
    assert fm.converted_with.startswith("pandoc ")
    for block in fixture_mod["SPEC"]:
        if isinstance(block, fixture_mod["Heading"]):
            assert block.text in body
    # the page's image was downloaded by fetch and copied into media/ (F16)
    assert "](media/safe-area.png)" in body
    assert (
        Path("output/sample/media/safe-area.png").read_bytes()
        == spec_site.routes["/TR/2026/REC-sample-20261119/safe-area.png"]["body"]
    )
    fetched = Path("work/fetched/sample.html").read_bytes()
    assert Path("output/sample/source.html").read_bytes() == fetched
    conversion = json.loads(Path("output/sample/conversion.json").read_text(encoding="utf-8"))
    assert conversion["fetch"]["final_url"] == dated and conversion["fetch"]["status"] == 200
    assert conversion["html"]["links_made_absolute"] >= 1
    assert [u["origin"] for u in conversion["html"]["own_urls"]] == ["sources.yaml", "final URL"]

    entry = entries()["sample"]
    assert entry.status == "ok" and entry.source == latest and entry.source_type == "web"
    assert "httpx" in entry.tool_versions
    assert entry.options["source"]["render"] == "static"
    assert entry.options["fetch"]["final_url"] == dated


def test_self_links_rewritten_with_source_url(site) -> None:
    url = site.url("/TR/x/")
    site.add("/TR/x/", SELF_LINKING.format(text=LONG_TEXT, own=url))
    sources(f"{{id: x, url: '{url}'}}")
    assert convert().exit_code == 0
    body = parse_document(Path("output/x/x.md").read_text(encoding="utf-8"))[1]
    assert body.count("](#intro)") == 2
    assert f"]({site.url('/TR/other/#a')})" in body
    assert "self_url_unknown" not in body


def test_unchanged_page_skipped_changed_page_converted(site) -> None:
    url = site.add("/p/", SELF_LINKING.format(text=LONG_TEXT, own="x"))
    sources(f"{{id: p, url: '{url}'}}")
    assert convert().exit_code == 0 and site.hits["/p/"] == 1
    # cached fetch reused, nothing changed: skipped without a request
    assert convert().exit_code == 0 and entries()["p"].status == "skipped_unchanged"
    assert site.hits["/p/"] == 1
    # refetching the same bytes changes nothing
    assert run("fetch", "--sources", "s.yaml", "--refetch").exit_code == 0
    assert convert().exit_code == 0 and entries()["p"].status == "skipped_unchanged"
    # a changed page converts again
    site.add("/p/", SELF_LINKING.format(text=LONG_TEXT + " Changed.", own="x"))
    assert run("fetch", "--sources", "s.yaml", "--refetch").exit_code == 0
    assert convert().exit_code == 0 and entries()["p"].status == "ok"
    assert "Changed." in Path("output/p/p.md").read_text(encoding="utf-8")


def test_changed_clean_mode_converts_again(site) -> None:
    url = site.add("/p/", f"<nav>Menu</nav><main><p>{LONG_TEXT}</p></main>")
    sources(f"{{id: p, url: '{url}'}}")
    assert convert().exit_code == 0
    assert "Menu" in Path("output/p/p.md").read_text(encoding="utf-8")
    sources(f"{{id: p, url: '{url}', clean: main-content}}")
    assert convert().exit_code == 0 and entries()["p"].status == "ok"
    assert "Menu" not in Path("output/p/p.md").read_text(encoding="utf-8")
    assert site.hits["/p/"] == 1  # clean mode is a conversion option: no new fetch


def test_failed_source_reported_others_converted(site, input_dir: Path) -> None:
    good = site.add("/good/", SELF_LINKING.format(text=LONG_TEXT, own="x"))
    sources(f"{{id: good, url: '{good}'}}", f"{{id: bad, url: '{site.url('/missing/')}'}}")
    result = convert("--input", str(input_dir))
    assert result.exit_code == 1
    status = {i: e.status for i, e in entries().items()}
    assert status["good"] == "ok" and status["bad"] == "failed"
    assert status["sample-spec"] == "ok" and status["sample-spec-2"] == "ok"
    assert "HTTP 404" in entries()["bad"].error
    assert not Path("output/bad").exists()


def test_empty_static_page_flagged_suspect_empty(site) -> None:
    url = site.add("/app/", JS_PAGE)
    sources(f"{{id: app, url: '{url}'}}")
    assert convert().exit_code == 0  # converted, but flagged
    entry = entries()["app"]
    assert entry.flags == ["suspect_empty"]
    assert any("set render: browser" in w for w in entry.warnings)
    fm, _ = parse_document(Path("output/app/app.md").read_text(encoding="utf-8"))
    assert fm.flags == ["suspect_empty"]


@pytest.mark.usefixtures("chromium_env")
def test_browser_render_converts_script_built_page(site) -> None:
    url = site.add("/app/", JS_PAGE)
    sources(f"{{id: app, url: '{url}', render: browser}}")
    assert convert().exit_code == 0
    entry = entries()["app"]
    assert entry.flags == [] and "playwright" in entry.tool_versions
    body = parse_document(Path("output/app/app.md").read_text(encoding="utf-8"))[1]
    assert "# Built" in body and "long enough to be real" in body


# -------------------------------------------------------------------- fetch


def test_fetch_command_caches_and_reports(site, in_tmp: Path) -> None:
    url = site.add("/p/", "<p>x</p>")
    sources(f"{{id: p, url: '{url}'}}", f"{{id: q, url: '{site.url('/q/')}'}}")
    result = run("fetch", "--sources", "s.yaml", "--work", "w")
    assert result.exit_code == 1
    assert (in_tmp / "w/fetched/p.html").is_file() and (in_tmp / "w/fetched/p.fetch.json").is_file()
    assert not (in_tmp / "w/fetched/q.html").exists()
    log = sorted((in_tmp / "w").glob("fetch-*.log"))[-1].read_text(encoding="utf-8")
    assert "p: fetched" in log and "q: fetch failed: HTTP 404" in log
    assert "1 fetched or cached, 1 failed (q)" in log

    result = run("fetch", "--sources", "s.yaml", "--work", "w", "--only", "p")
    assert result.exit_code == 0 and site.hits["/p/"] == 1
    log = sorted((in_tmp / "w").glob("fetch-*.log"))[-1].read_text(encoding="utf-8")
    assert "p: cached" in log


def test_failed_refetch_keeps_cache_and_says_so(site, in_tmp: Path) -> None:
    url = site.add("/p/", "<p>x</p>")
    sources(f"{{id: p, url: '{url}'}}")
    assert run("fetch", "--sources", "s.yaml", "--work", "w").exit_code == 0
    site.add("/p/", "<p>x</p>", fail_times=99)
    assert run("fetch", "--sources", "s.yaml", "--work", "w", "--refetch").exit_code == 1
    log = sorted((in_tmp / "w").glob("fetch-*.log"))[-1].read_text(encoding="utf-8")
    assert "the cached copy from" in log and "was kept" in log
    assert (in_tmp / "w/fetched/p.html").read_text(encoding="utf-8") == "<p>x</p>"


def test_failed_image_keeps_web_link_with_warning(site) -> None:
    page = (
        f"<p>{LONG_TEXT}</p><img src='ok.png' alt='ok'><img src='gone.png' alt='gone'>"
        f"<img src='{site.url('/cdn/logo.png')}' alt='other host'>"
    )
    url = site.add("/p/", page)
    site.add("/p/ok.png", b"\x89PNG ok", content_type="image/png")
    site.add("/cdn/logo.png", b"\x89PNG logo", content_type="image/png")
    sources(f"{{id: p, url: '{url}'}}")
    assert convert().exit_code == 0
    body = parse_document(Path("output/p/p.md").read_text(encoding="utf-8"))[1]
    assert "![ok](media/ok.png)" in body and "![other host](media/logo.png)" in body
    assert f"![gone]({site.url('/p/gone.png')})" in body
    entry = entries()["p"]
    assert any("gone.png: not downloaded" in w and "HTTP 404" in w for w in entry.warnings)
    conversion = json.loads(Path("output/p/conversion.json").read_text(encoding="utf-8"))
    assert conversion["html"]["images"]["from_fetch"] == 2
    assert conversion["html"]["images"]["remote"] == 1


def test_changed_image_converts_again(site) -> None:
    url = site.add("/p/", f"<p>{LONG_TEXT}</p><img src='a.png' alt='a'>")
    site.add("/p/a.png", b"one", content_type="image/png")
    sources(f"{{id: p, url: '{url}'}}")
    assert convert().exit_code == 0
    site.add("/p/a.png", b"two", content_type="image/png")
    assert run("fetch", "--sources", "s.yaml", "--refetch").exit_code == 0
    assert convert().exit_code == 0 and entries()["p"].status == "ok"  # same page, new image
    assert Path("output/p/media/a.png").read_bytes() == b"two"
