"""Blocking private network addresses when fetching (design: Security, URL
fetching; phase 11). The web app fetches URLs for anyone on the network, so
private, loopback and link-local addresses are refused, also after redirects
and for page images. The command line allows everything unless asked."""

from __future__ import annotations

from pathlib import Path

import pytest
from specconv.config import FetchConfig, Source
from specconv.fetch import BlockedAddress, FetchError, HostGuard, fetch_source

PAGE = "<html><body><h1>Spec</h1><img src='{img}'></body></html>"


def guarded(site, allow=("127.0.0.1",)) -> FetchConfig:
    # The test server is on 127.0.0.1: allow exactly that host name, so a
    # redirect to "localhost" (same machine, other name) must be refused.
    return FetchConfig(timeout_s=5, retries=0, block_private_addresses=True, allow_hosts=allow)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/", "http://localhost:8080/x", "http://10.1.2.3/", "http://192.168.0.1/",
        "http://172.16.5.4/", "http://169.254.169.254/latest/meta-data/", "http://[::1]/",
        "http://0.0.0.0/", "http://[fd00::1]/",
    ],
)  # fmt: skip
def test_private_addresses_blocked(url: str) -> None:
    with pytest.raises(BlockedAddress):
        HostGuard().check(url)


def test_public_and_allowlisted_addresses_pass() -> None:
    HostGuard(resolver=lambda host: ["93.184.216.34"]).check("https://example.org/")
    HostGuard(allow_hosts=("intranet.local",)).check("http://intranet.local/spec")


def test_host_resolving_to_any_private_address_blocked() -> None:
    guard = HostGuard(resolver=lambda host: ["93.184.216.34", "10.0.0.5"])
    with pytest.raises(BlockedAddress, match="10.0.0.5"):
        guard.check("https://mixed.example/")


def test_static_fetch_redirect_to_private_address_blocked(site, tmp_path: Path) -> None:
    target = site.url("/secret").replace("127.0.0.1", "localhost")
    url = site.redirect("/go", target)
    site.add("/secret", PAGE.format(img="x.png"))
    with pytest.raises(FetchError, match="localhost.*not allowed"):
        fetch_source(Source(id="p", url=url), guarded(site), tmp_path)
    assert site.hits["/secret"] == 0  # the internal page was never requested


def test_page_image_on_private_address_not_downloaded(site, tmp_path: Path) -> None:
    img = site.add("/img.png", b"\x89PNG", content_type="image/png").replace(
        "127.0.0.1", "localhost"
    )
    url = site.add("/page", PAGE.format(img=img))
    record = fetch_source(Source(id="p", url=url), guarded(site), tmp_path).record
    assert record.images == [] and "not allowed" in record.images_failed[0]["error"]
    assert site.hits["/img.png"] == 0


def test_unguarded_command_line_fetch_still_works(site, tmp_path: Path) -> None:
    url = site.add("/page", PAGE.format(img="x.png"))
    assert (
        fetch_source(Source(id="p", url=url), FetchConfig(retries=0), tmp_path).record.status == 200
    )


@pytest.mark.usefixtures("chromium_env")
def test_browser_fetch_blocks_private_requests(site, tmp_path: Path) -> None:
    img = site.add("/img.png", b"\x89PNG", content_type="image/png").replace(
        "127.0.0.1", "localhost"
    )
    url = site.add(
        "/app",
        "<div id='a'></div><script>document.getElementById('a').innerHTML="
        f"'<img src=\"{img}\">';</script>",
    )
    fetch_source(Source(id="b", url=url, render="browser"), guarded(site), tmp_path)
    assert site.hits["/img.png"] == 0  # the browser's own request was blocked too
    target = site.url("/secret").replace("127.0.0.1", "localhost")
    go = site.redirect("/go", target)
    with pytest.raises(FetchError, match="not allowed"):
        fetch_source(Source(id="c", url=go, render="browser"), guarded(site), tmp_path)
    assert site.hits["/secret"] == 0


def test_cli_flags(site, tmp_path: Path, write) -> None:
    from specconv import cli
    from typer.testing import CliRunner

    write("s.yaml", f"sources:\n  - {{id: p, url: '{site.url('/x')}'}}\n")
    site.add("/x", PAGE.format(img="y.png"))
    args = ["fetch", "--sources", str(tmp_path / "s.yaml"), "--work", str(tmp_path / "w")]
    result = CliRunner().invoke(cli.app, [*args, "--block-private-addresses"])
    assert (
        result.exit_code == 1
        and "not allowed"
        in (tmp_path / "w")
        .joinpath(sorted(p.name for p in (tmp_path / "w").glob("fetch-*.log"))[-1])
        .read_text()
    )
    result = CliRunner().invoke(
        cli.app, [*args, "--refetch", "--block-private-addresses", "--allow-host", "127.0.0.1"]
    )
    assert result.exit_code == 0, result.output
