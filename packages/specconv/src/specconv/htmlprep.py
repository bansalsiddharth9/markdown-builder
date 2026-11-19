"""Prepare HTML before Pandoc converts it.

Design: "HTML cleanup before conversion", "Links back to the same document",
saved pages with a ``_files`` folder; decisions I1, I2, S1, S2 in
spec/deviations.md. Steps, in order:

1. Find the document's own URL(s): the sources.yaml URL and its final URL
   (web sources), the browser's ``<!-- saved from url=(NNNN)… -->`` comment,
   ``<link rel="canonical">``, and the ``self_urls`` config map.
2. Remove the configured strip_tags (script, style, noscript).
3. Rewrite links whose address without the fragment is the document's own
   URL into fragment-only links (``#intro``). ``…/`` and ``…/index.html``,
   and http and https, count as the same address. A link to the own URL
   without a fragment is left unchanged. Without any own URL, the flag
   ``self_url_unknown`` is set only if there are full-URL links with a
   fragment that could have been self-links.
4. Ids that links target on <nav>, <article>, <footer> and <details>, which
   Pandoc's reader drops, move to an empty <a id> at the start of the element.
5. Tables with exactly one cell (TTML2 draws a box around each code example
   this way) are replaced by their caption, as a paragraph, followed by the
   cell's contents, so a <pre> inside becomes a normal code block (decision
   Q14). Ids on the table, its rows or the cell move to empty <a id> anchors.
   A <pre> without a <code> inside gets one, because Pandoc 3.14's HTML
   reader writes a bare <pre> as plain text with line breaks, not as code.
6. Images: files on disk inside the HTML file's folder (e.g. a saved page's
   ``_files`` folder) are copied into ``media/`` and the link points there.
   Web images stay web links: conversion never uses the network. Missing
   images, and local paths outside the HTML file's folder, keep their
   original link and get a warning, so V7 still sees them.

BeautifulSoup is used with Python's html.parser, which tokenizes without
repairing the document structure, so apart from the changes above the HTML
reaching Pandoc is what the author wrote. The result is always UTF-8.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

from bs4 import BeautifulSoup, Comment

FLAG_SELF_URL_UNKNOWN = "self_url_unknown"
_SAVED_FROM = re.compile(r"saved from url=\(\d+\)(\S+)", re.IGNORECASE)
_INDEX_PAGES = ("index.html", "index.htm")
# HTML5 elements whose id Pandoc's HTML reader drops (checked with Pandoc 3.14).
ID_LOSING_TAGS = ("nav", "article", "footer", "details")


@dataclass
class OwnUrl:
    url: str
    origin: str  # "sources.yaml", "final URL", "saved-from comment", "canonical link", "self_urls"


@dataclass
class PreparedHtml:
    html: str
    removed: dict[str, int] = field(default_factory=dict)  # tag -> elements removed
    original_encoding: str | None = None
    own_urls: list[OwnUrl] = field(default_factory=list)
    self_links_rewritten: int = 0
    images_copied: list[str] = field(default_factory=list)  # media paths, e.g. media/fig.png
    images_remote: int = 0
    images_missing: list[str] = field(default_factory=list)  # original src values
    ids_moved_to_anchors: int = 0  # targeted ids on elements Pandoc would drop them from
    tables_unwrapped: int = 0  # one-cell tables replaced by their contents
    pre_given_code: int = 0  # bare <pre> elements given a <code> so they become code blocks
    warnings: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, object]:
        """What conversion.json records about the preparation."""
        return {
            "own_urls": [{"url": u.url, "origin": u.origin} for u in self.own_urls],
            "self_links_rewritten": self.self_links_rewritten,
            "ids_moved_to_anchors": self.ids_moved_to_anchors,
            "single_cell_tables_unwrapped": self.tables_unwrapped,
            "pre_given_code": self.pre_given_code,
            "removed_elements": self.removed,
            "source_encoding": self.original_encoding,
            "images": {
                "copied": self.images_copied,
                "remote": self.images_remote,
                "missing": self.images_missing,
            },
        }


# ------------------------------------------------------------- own URL


def _normalize(url: str) -> tuple[str, str, str]:
    """Address key ignoring scheme, fragment and a trailing index.html."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.port and parts.port not in (80, 443):
        host = f"{host}:{parts.port}"
    path = parts.path or "/"
    for index in _INDEX_PAGES:
        if path.endswith("/" + index):
            path = path[: -len(index)]
    return host, path, parts.query


def _is_web_url(url: str) -> bool:
    return urlsplit(url).scheme in ("http", "https")


def find_own_urls(soup: BeautifulSoup, known: Iterable[OwnUrl]) -> list[OwnUrl]:
    """Own URLs in the design's order: known (sources.yaml / config), saved-from
    comment, canonical link. Duplicates (same address) are dropped."""
    found = [u for u in known if _is_web_url(u.url)]
    for comment in soup.find_all(string=lambda s: isinstance(s, Comment)):
        match = _SAVED_FROM.search(str(comment))
        if match and _is_web_url(match.group(1)):
            found.append(OwnUrl(match.group(1), "saved-from comment"))
            break
    for link in soup.find_all("link", href=True):
        if "canonical" in (link.get("rel") or []) and _is_web_url(link["href"]):
            found.append(OwnUrl(link["href"], "canonical link"))
            break
    unique: list[OwnUrl] = []
    seen: set[tuple[str, str, str]] = set()
    for own in found:
        key = _normalize(own.url)
        if key not in seen:
            seen.add(key)
            unique.append(own)
    return unique


def rewrite_self_links(soup: BeautifulSoup, own_urls: list[OwnUrl]) -> int:
    """Rewrite links to the own URL with a fragment into '#fragment'. Returns the count."""
    own_keys = {_normalize(u.url) for u in own_urls}
    base_tag = soup.find("base", href=True)
    base = base_tag["href"] if base_tag is not None else own_urls[0].url
    count = 0
    for a in soup.find_all(["a", "area"], href=True):
        href = a["href"].strip()
        if not href or href.startswith("#"):
            continue
        absolute = urljoin(base, href)
        fragment = urlsplit(absolute).fragment
        if fragment and _normalize(absolute) in own_keys:
            a["href"] = "#" + fragment
            count += 1
    return count


def has_possible_self_links(soup: BeautifulSoup) -> bool:
    for a in soup.find_all(["a", "area"], href=True):
        parts = urlsplit(a["href"].strip())
        if parts.scheme in ("http", "https") and parts.fragment:
            return True
    return False


def keep_targeted_ids(soup: BeautifulSoup) -> int:
    """Move targeted ids off elements whose id Pandoc drops (nav, article, footer,
    details) onto an empty <a id> at the start of the element, which Pandoc keeps.
    Returns the number of ids moved."""
    targeted = {
        a["href"][1:] for a in soup.find_all(["a", "area"], href=True) if a["href"].startswith("#")
    }
    moved = 0
    for element in soup.find_all(ID_LOSING_TAGS, id=True):
        element_id = element["id"]
        if element_id in targeted:
            anchor = soup.new_tag("a", id=element_id)
            del element["id"]
            element.insert(0, anchor)
            moved += 1
    return moved


# ------------------------------------------------------- one-cell tables


def _own_cells(table) -> list:
    return [c for c in table.find_all(["td", "th"]) if c.find_parent("table") is table]


def unwrap_single_cell_tables(soup: BeautifulSoup) -> int:
    """Replace each table that has exactly one cell by its caption (as a <p>)
    followed by the cell's contents. Returns the number of tables unwrapped.

    Nested tables are handled innermost first, so a box inside a box goes too.
    """
    unwrapped = 0
    for table in reversed(soup.find_all("table")):
        cells = _own_cells(table)
        if len(cells) != 1:
            continue
        cell = cells[0]
        caption = next(
            (c for c in table.find_all("caption") if c.find_parent("table") is table), None
        )
        ids: list[str] = []
        for element in (table, *_between(table, cell), cell, caption):
            if element is not None and element.get("id") and element["id"] not in ids:
                ids.append(element["id"])
        pieces = []
        if caption is not None or ids:
            para = soup.new_tag("p")
            for element_id in ids:
                para.append(soup.new_tag("a", id=element_id))
            if caption is not None:
                for child in list(caption.contents):
                    para.append(child.extract())
            pieces.append(para)
        pieces.extend(child.extract() for child in list(cell.contents))
        for piece in pieces:
            table.insert_before(piece)
        table.decompose()
        unwrapped += 1
    return unwrapped


def code_in_pre(soup: BeautifulSoup) -> int:
    """Put a <code> around the contents of each <pre> that is not already just
    one <code>. Returns the number of <pre> elements changed."""
    changed = 0
    for pre in soup.find_all("pre"):
        children = [c for c in pre.contents if not (isinstance(c, str) and not c.strip())]
        if len(children) == 1 and getattr(children[0], "name", None) == "code":
            continue
        code = soup.new_tag("code")
        for child in list(pre.contents):
            code.append(child.extract())
        pre.append(code)
        changed += 1
    return changed


def _between(table, cell) -> list:
    out = []
    for parent in cell.parents:
        if parent is table:
            break
        out.append(parent)
    return out


# --------------------------------------------------------------- images


def _unique_name(name: str, taken: set[str]) -> str:
    if name not in taken:
        return name
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    n = 2
    while True:
        candidate = f"{stem}-{n}{dot}{ext}"
        if candidate not in taken:
            return candidate
        n += 1


def _safe_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-.")
    return cleaned or "image"


def handle_images(
    soup: BeautifulSoup, html_dir: Path | None, media_dir: Path, result: PreparedHtml
) -> None:
    root = html_dir.resolve() if html_dir is not None else None
    copied: dict[Path, str] = {}  # source file -> media name (one copy per file)
    taken: set[str] = set()
    for img in soup.find_all("img"):
        src = (img.get("src") or "").strip()
        if not src or src.startswith("data:"):
            continue
        parts = urlsplit(src)
        if parts.scheme in ("http", "https") or src.startswith("//"):
            result.images_remote += 1
            continue
        if parts.scheme == "file":
            path = Path(unquote(parts.path))
        elif parts.scheme == "" and root is not None:
            path = root / unquote(parts.path)
        else:
            result.images_missing.append(src)
            result.warnings.append(f"image {src!r}: cannot be resolved to a local file; link kept")
            continue
        resolved = path.resolve()
        if root is None or not resolved.is_relative_to(root):
            result.images_missing.append(src)
            result.warnings.append(
                f"image {src!r}: outside the HTML file's folder, not copied; link kept"
            )
            continue
        if not resolved.is_file():
            result.images_missing.append(src)
            result.warnings.append(f"image {src!r}: file not found; link kept")
            continue
        if resolved not in copied:
            name = _unique_name(_safe_name(resolved.name), taken)
            taken.add(name)
            media_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(resolved, media_dir / name)
            copied[resolved] = name
            result.images_copied.append(f"{media_dir.name}/{name}")
        img["src"] = f"{media_dir.name}/{copied[resolved]}"


# ------------------------------------------------------------------ all


def prepare_html(
    data: bytes,
    *,
    strip_tags: Iterable[str],
    media_dir: Path,
    html_path: Path | None = None,
    known_urls: Iterable[OwnUrl] = (),
) -> PreparedHtml:
    """Run steps 1 to 6 on raw HTML bytes. Images are copied into media_dir."""
    soup = BeautifulSoup(data, "html.parser")
    result = PreparedHtml(html="", original_encoding=soup.original_encoding)

    result.own_urls = find_own_urls(soup, known_urls)

    for tag in strip_tags:
        elements = soup.find_all(tag)
        if elements:
            result.removed[tag] = len(elements)
        for element in elements:
            element.decompose()

    if result.own_urls:
        result.self_links_rewritten = rewrite_self_links(soup, result.own_urls)
    elif has_possible_self_links(soup):
        result.flags.append(FLAG_SELF_URL_UNKNOWN)
        result.warnings.append(
            "the document's own URL is unknown, so full-URL links to its own sections may "
            "not have been rewritten to '#…' links; add it under self_urls in specconv.yaml"
        )

    result.ids_moved_to_anchors = keep_targeted_ids(soup)
    result.tables_unwrapped = unwrap_single_cell_tables(soup)
    result.pre_given_code = code_in_pre(soup)
    handle_images(soup, html_path.parent if html_path is not None else None, media_dir, result)
    result.html = str(soup)
    return result
