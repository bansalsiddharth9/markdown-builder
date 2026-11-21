"""Download the web pages listed in sources.yaml (design: Web sources).

- ``render: static`` uses httpx: redirects followed, ``fetch.timeout_s``,
  ``fetch.retries`` retries on network errors, timeouts and HTTP 408, 425,
  429, 500, 502, 503, 504 (deviation F3).
- ``render: browser`` loads the page in Playwright Chromium, waits for network
  idle and saves ``page.content()`` (UTF-8). Same timeout and retries.

Each page is saved to ``work/fetched/<id>.html`` with a sidecar
``<id>.fetch.json`` (URL, final URL, HTTP status, fetch time, render mode,
plus content type, encoding and SHA-256). A cached page is reused unless
``refetch`` is set, or the cache no longer matches the source: its URL or
render mode changed in sources.yaml, or the saved HTML was changed (SHA-256).

Images (decision F16): every ``<img src>`` image of the page, from any
site, is downloaded into ``work/fetched/<id>_files/`` and listed in the
sidecar, so ``convert`` can copy it into ``media/`` without the network. An
image that cannot be downloaded does not fail the fetch: it is listed under
``images_failed`` and keeps its web link.

Only HTTP 200 with an HTML content type counts as success. A failed fetch
never replaces a good cached copy; the cache is written atomically, sidecar
last, so a half-written fetch is never mistaken for a complete one.

With ``fetch.block_private_addresses`` (the web app sets it), every request is
checked by HostGuard first, so a URL cannot be used to reach internal hosts
(deviation F17).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import ipaddress
import json
import logging
import mimetypes
import os
import shutil
import socket
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

from specconv import __version__
from specconv.config import FetchConfig, Source
from specconv.htmlprep import page_image_urls, safe_name, unique_name

log = logging.getLogger("specconv.fetch")

FETCHED_DIR = "fetched"
RETRY_BACKOFF_S = 1.0  # first wait between attempts; doubles each time
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
HTML_TYPES = ("text/html", "application/xhtml+xml")
MAX_IMAGE_BYTES = 50 * 1024 * 1024  # larger images are not downloaded (stops huge or endless files)


class FetchError(RuntimeError):
    """A page could not be downloaded, or what came back is not an HTML page."""


class BlockedAddress(FetchError):
    """The URL's host is a private, loopback or link-local address (deviations F17)."""


class HostGuard:
    """Refuses URLs whose host resolves to an address that is not globally
    routable (private, loopback, link-local, unspecified, multicast). Checked
    before every request: the page, each redirect hop, every image."""

    def __init__(self, allow_hosts=(), resolver=None) -> None:
        """allow_hosts are host names never checked; resolver is replaceable for tests."""
        self.allow = {h.lower() for h in allow_hosts}
        self.resolver = resolver or _resolve

    def check(self, url: str) -> None:
        """Raise BlockedAddress if the URL's host is, or resolves to, a non-global address.

        Every address the name resolves to must be global, not just one. The check
        resolves the name itself; the HTTP client resolves it again when it
        connects, so a DNS answer that changes in between is not caught.
        """
        host = (urlsplit(url).hostname or "").lower()
        if not host or host in self.allow:
            return
        try:
            addresses = [str(ipaddress.ip_address(host))]  # host is a literal IP
        except ValueError:
            addresses = self.resolver(host)
        for address in addresses:
            # Drop an IPv6 zone id ("fe80::1%eth0"); ip_address() rejects it.
            ip = ipaddress.ip_address(address.split("%")[0])
            # "::ffff:127.0.0.1" is IPv4 loopback in disguise; judge the IPv4 address.
            if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
                ip = ip.ipv4_mapped
            if not ip.is_global or ip.is_multicast:
                raise BlockedAddress(
                    f"{host} ({address}) is a private or local address; fetching it is not "
                    "allowed (add the host to the allowlist if it is an intended internal host)"
                )


def _resolve(host: str) -> list[str]:
    """All addresses a host name resolves to (IPv4 and IPv6), sorted."""
    try:
        return sorted({info[4][0] for info in socket.getaddrinfo(host, None)})
    except OSError:
        return []  # unknown host: the request itself fails with a clear error


def guard_for(cfg: FetchConfig) -> HostGuard | None:
    """A HostGuard when private addresses are blocked, else None (no checks)."""
    return HostGuard(cfg.allow_hosts) if cfg.block_private_addresses else None


def _request_hook(cfg: FetchConfig) -> dict:
    """httpx event_hooks that check every request, including each redirect hop.

    httpx calls "request" hooks before sending each request it makes while following
    redirects, so an internal redirect target is refused before it is contacted.
    """
    guard = guard_for(cfg)
    if guard is None:
        return {}
    return {"request": [lambda request: guard.check(str(request.url))]}


@dataclass(frozen=True)
class FetchRecord:
    """The sidecar <id>.fetch.json."""

    id: str
    url: str
    final_url: str
    status: int
    fetched_at: str
    render: str
    content_type: str | None
    encoding: str | None  # encoding of the saved bytes, when known (header or browser)
    sha256: str
    bytes: int
    user_agent: str
    specconv_version: str
    duration_s: float
    # Downloaded images: {url, file (name in <id>_files/), sha256, bytes, content_type}
    images: list[dict]
    images_failed: list[dict]  # {url, error}

    def downloaded_images(self, work: Path) -> dict[str, Path]:
        """image URL -> downloaded file, for prepare_html."""
        folder = images_dir(work, self.id)
        return {image["url"]: folder / image["file"] for image in self.images}


@dataclass(frozen=True)
class FetchOutcome:
    """Result of fetch_source: the sidecar, the saved page, and whether it was cached."""

    record: FetchRecord
    html_path: Path
    from_cache: bool


@dataclass(frozen=True)
class _Page:
    """A downloaded page before it is saved."""

    data: bytes
    final_url: str
    status: int
    content_type: str | None
    encoding: str | None


def cache_paths(work: Path, source_id: str) -> tuple[Path, Path]:
    """Paths of the saved page and its sidecar: (<id>.html, <id>.fetch.json)."""
    folder = work / FETCHED_DIR
    return folder / f"{source_id}.html", folder / f"{source_id}.fetch.json"


def images_dir(work: Path, source_id: str) -> Path:
    """Folder for the page's downloaded images, named like a browser-saved page's."""
    return work / FETCHED_DIR / f"{source_id}_files"


def load_cached(work: Path, source: Source) -> FetchOutcome | None:
    """The cached fetch for this source, or None if missing or stale (deviation F1).

    Stale means: the sidecar is unreadable, the URL or render mode changed, or the
    page or one of its images no longer matches the SHA-256 in the sidecar.
    """
    html_path, meta_path = cache_paths(work, source.id)
    if not (html_path.is_file() and meta_path.is_file()):
        return None
    try:
        record = FetchRecord(**json.loads(meta_path.read_text(encoding="utf-8")))
    except (ValueError, TypeError) as exc:
        log.warning(
            "%s: unreadable fetch record %s (%s); fetching again", source.id, meta_path, exc
        )
        return None
    if record.url != source.url or record.render != source.render:
        log.info("%s: url or render mode changed since the cached fetch; fetching again", source.id)
        return None
    if hashlib.sha256(html_path.read_bytes()).hexdigest() != record.sha256:
        log.warning("%s: cached page %s was changed; fetching again", source.id, html_path)
        return None
    folder = images_dir(work, source.id)
    for image in record.images:
        path = folder / image["file"]
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != image["sha256"]:
            log.warning(
                "%s: cached image %s is missing or changed; fetching again", source.id, path
            )
            return None
    return FetchOutcome(record=record, html_path=html_path, from_cache=True)


def fetch_source(
    source: Source, cfg: FetchConfig, work: Path, refetch: bool = False
) -> FetchOutcome:
    """Return the cached page, or download it. Raises FetchError.

    Image download failures do not fail the fetch; they are listed in the sidecar.
    On failure nothing in the cache is changed (deviation F4).
    """
    if not refetch:
        cached = load_cached(work, source)
        if cached is not None:
            return cached
    start = time.monotonic()
    fetcher = _fetch_browser if source.render == "browser" else _fetch_static
    page = _with_retries(source.id, cfg, lambda: fetcher(source.url, cfg))
    _check_page(page, source.url)
    html_path, meta_path = cache_paths(work, source.id)
    html_path.parent.mkdir(parents=True, exist_ok=True)
    folder = images_dir(work, source.id)
    # Images go to a hidden temporary folder first and replace the old ones only
    # once the page itself is known to be good.
    tmp_images = folder.with_name(f".{folder.name}.tmp")
    shutil.rmtree(tmp_images, ignore_errors=True)
    images, failed = _download_images(source.id, page, cfg, tmp_images)
    record = FetchRecord(
        id=source.id,
        url=source.url,
        final_url=page.final_url,
        status=page.status,
        fetched_at=dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        render=source.render,
        content_type=page.content_type,
        encoding=page.encoding,
        sha256=hashlib.sha256(page.data).hexdigest(),
        bytes=len(page.data),
        user_agent=cfg.user_agent,
        specconv_version=__version__,
        duration_s=round(time.monotonic() - start, 3),
        images=images,
        images_failed=failed,
    )
    meta_path.unlink(missing_ok=True)  # the old sidecar must not vouch for new files
    _write_atomic(html_path, page.data)
    shutil.rmtree(folder, ignore_errors=True)
    if tmp_images.exists():
        tmp_images.rename(folder)
    _write_atomic(meta_path, (json.dumps(asdict(record), indent=2) + "\n").encode("utf-8"))
    return FetchOutcome(record=record, html_path=html_path, from_cache=False)


# ------------------------------------------------------------------ retries


class _Retryable(FetchError):
    """A failure worth another attempt (network error, timeout, 429, 5xx)."""


def _with_retries(label: str, cfg: FetchConfig, attempt_once):
    """Call attempt_once() up to fetch.retries + 1 times while it raises _Retryable."""
    attempts = cfg.retries + 1
    wait = RETRY_BACKOFF_S
    for attempt in range(1, attempts + 1):
        try:
            return attempt_once()
        except _Retryable as exc:
            if attempt == attempts:
                raise FetchError(f"{exc} (after {attempts} attempt(s))") from exc
            log.warning("%s: attempt %d of %d failed: %s; retrying", label, attempt, attempts, exc)
            time.sleep(wait)
            wait *= 2
    raise AssertionError("unreachable")  # the loop always returns or raises


def _check_page(page: _Page, url: str) -> None:
    """Accept only HTTP 200 with an HTML content type (or none given) (deviation F2)."""
    if page.status != 200:
        raise FetchError(f"HTTP {page.status} for {url}")
    if page.content_type is not None:
        mime = page.content_type.split(";")[0].strip().lower()
        if mime and mime not in HTML_TYPES:
            raise FetchError(
                f"{url} is not an HTML page (content type {mime}); "
                "download the file into the input folder instead"
            )


def _status_error(status: int, url: str) -> FetchError:
    """The error for a non-200 status: retryable for RETRYABLE_STATUS, final otherwise."""
    if status in RETRYABLE_STATUS:
        return _Retryable(f"HTTP {status} for {url}")
    return FetchError(f"HTTP {status} for {url}")


# ------------------------------------------------------------------- images


def _download_images(
    source_id: str, page: _Page, cfg: FetchConfig, folder: Path
) -> tuple[list[dict], list[dict]]:
    """Download the page's images into folder. Returns (downloaded, failed).

    The folder is created only when at least one image downloads. Image URLs are
    resolved against the page's final URL (after redirects).
    """
    import httpx
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(page.data, "html.parser", from_encoding=page.encoding)
    urls = page_image_urls(soup, page.final_url)
    if not urls:
        return [], []
    images: list[dict] = []
    failed: list[dict] = []
    taken: set[str] = set()
    with httpx.Client(
        follow_redirects=True,
        timeout=cfg.timeout_s,
        headers={"User-Agent": cfg.user_agent},
        event_hooks=_request_hook(cfg),
    ) as client:
        for url in urls:
            try:
                # u=url binds the current URL; a plain closure would see the loop variable.
                data, content_type = _with_retries(
                    f"{source_id}: image {url}", cfg, lambda u=url: _get_image(client, u, cfg)
                )
            except FetchError as exc:
                failed.append({"url": url, "error": str(exc)})
                log.warning("%s: image not downloaded: %s", source_id, exc)
                continue
            name = unique_name(_image_file_name(url, content_type), taken)
            taken.add(name)
            folder.mkdir(parents=True, exist_ok=True)
            (folder / name).write_bytes(data)
            images.append(
                {
                    "url": url,
                    "file": name,
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "bytes": len(data),
                    "content_type": content_type,
                }
            )
    log.info(
        "%s: %d of %d image(s) downloaded%s",
        source_id,
        len(images),
        len(urls),
        f", {len(failed)} failed" if failed else "",
    )
    return images, failed


def _get_image(client, url: str, cfg: FetchConfig) -> tuple[bytes, str | None]:
    """Download one image. Returns (bytes, content type header).

    Streamed, so a file larger than MAX_IMAGE_BYTES is stopped early instead of
    being read into memory whole. An HTML answer (e.g. a login or error page)
    is refused.
    """
    import httpx

    try:
        with client.stream("GET", url) as response:
            if response.status_code != 200:
                raise _status_error(response.status_code, url)
            content_type = response.headers.get("content-type")
            mime = (content_type or "").split(";")[0].strip().lower()
            if mime in HTML_TYPES:
                raise FetchError(f"{url} returned an HTML page, not an image")
            data = bytearray()
            for chunk in response.iter_bytes():
                data += chunk
                if len(data) > MAX_IMAGE_BYTES:
                    raise FetchError(f"{url} is larger than {MAX_IMAGE_BYTES // 2**20} MB")
            return bytes(data), content_type
    except httpx.TimeoutException as exc:
        raise _Retryable(f"timed out after {cfg.timeout_s:g} s: {url}") from exc
    except httpx.TransportError as exc:
        raise _Retryable(f"network error for {url}: {type(exc).__name__}: {exc}") from exc
    except httpx.HTTPError as exc:
        raise FetchError(f"{url}: {type(exc).__name__}: {exc}") from exc


def _image_file_name(url: str, content_type: str | None) -> str:
    """File name from the URL's last path part; adds an extension from the content
    type when the URL has none."""
    name = safe_name(unquote(urlsplit(url).path.rsplit("/", 1)[-1]))
    if "." not in name:
        mime = (content_type or "").split(";")[0].strip().lower()
        # Fixed for the common types: guess_extension depends on the system's
        # mime.types files and can give e.g. ".jpe".
        ext = {"image/svg+xml": ".svg", "image/jpeg": ".jpg"}.get(mime) or (
            mimetypes.guess_extension(mime) if mime else None
        )
        name += ext or ""
    return name


# ------------------------------------------------------------------- static


def _fetch_static(url: str, cfg: FetchConfig) -> _Page:
    """Download a page with httpx. Network errors, timeouts and some statuses are retryable."""
    import httpx

    try:
        with httpx.Client(
            follow_redirects=True,
            timeout=cfg.timeout_s,
            headers={"User-Agent": cfg.user_agent},
            event_hooks=_request_hook(cfg),  # checks the page and every redirect hop
        ) as client:
            response = client.get(url)
    except httpx.TimeoutException as exc:
        raise _Retryable(f"timed out after {cfg.timeout_s:g} s: {url}") from exc
    except httpx.TransportError as exc:
        raise _Retryable(f"network error for {url}: {type(exc).__name__}: {exc}") from exc
    except httpx.HTTPError as exc:
        raise FetchError(f"{url}: {type(exc).__name__}: {exc}") from exc
    if response.status_code != 200:
        raise _status_error(response.status_code, url)
    return _Page(
        data=response.content,
        final_url=str(response.url),
        status=response.status_code,
        content_type=response.headers.get("content-type"),
        encoding=response.charset_encoding,  # only from the Content-Type header
    )


# ------------------------------------------------------------------ browser


def _fetch_browser(url: str, cfg: FetchConfig) -> _Page:
    """Load a page in headless Chromium and save the DOM after network idle.

    With address blocking on, every request the page makes (scripts, images,
    redirects) goes through a route handler that checks it first.
    """
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    from playwright.sync_api import sync_playwright

    # Unlike deps.configure_browsers_path, this overrides an existing
    # PLAYWRIGHT_BROWSERS_PATH.
    if cfg.browsers_path is not None:
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(cfg.browsers_path)
    guard = guard_for(cfg)
    blocked: list[str] = []
    if guard is not None:
        guard.check(url)

    def route(request_route) -> None:
        """Every request the page makes. Chromium does not show redirect hops to
        route handlers, so the request is made here without following
        redirects; a redirect target is checked before the browser follows it
        (and its request comes through here again)."""
        # Only registered when guard is not None (see context.route below).
        request_url = request_route.request.url
        try:
            guard.check(request_url)
            response = request_route.fetch(max_redirects=0)
            location = response.headers.get("location")
            if 300 <= response.status < 400 and location:
                guard.check(urljoin(request_url, location))
        except BlockedAddress as exc:
            blocked.append(str(exc))
            # The browser only sees a generic error; the reason is kept in `blocked`.
            request_route.abort("blockedbyclient")
            return
        request_route.fulfill(response=response)

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                context = browser.new_context(user_agent=cfg.user_agent)
                if guard is not None:
                    context.route("**/*", route)
                page = context.new_page()
                try:
                    response = page.goto(
                        url, wait_until="networkidle", timeout=cfg.timeout_s * 1000
                    )
                except PlaywrightError:
                    # Report the block, not the browser's generic navigation error.
                    if blocked:
                        raise BlockedAddress(blocked[0]) from None
                    raise
                if guard is not None and response is not None:
                    # redirects of the page itself: refuse the result if a hop was internal
                    hop = response.request
                    while hop is not None:
                        guard.check(hop.url)
                        hop = hop.redirected_from
                if response is None:
                    raise FetchError(f"no response for {url}")
                if response.status != 200:
                    raise _status_error(response.status, url)
                html = page.content()
                return _Page(
                    data=html.encode("utf-8"),
                    final_url=page.url,
                    status=response.status,
                    content_type=response.headers.get("content-type"),
                    encoding="utf-8",  # page.content() is text; saved as UTF-8
                )
            finally:
                browser.close()
    except FetchError:
        raise
    except PlaywrightTimeout as exc:
        raise _Retryable(f"timed out after {cfg.timeout_s:g} s: {url}") from exc
    except PlaywrightError as exc:
        message = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
        raise _Retryable(f"browser error for {url}: {message}") from exc


# -------------------------------------------------------------------- files


def _write_atomic(path: Path, data: bytes) -> None:
    """Write to a temporary file next to path, then rename it into place.

    os.replace is atomic on the same file system, so readers see the old or the new
    file, never half of one.
    """
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)
