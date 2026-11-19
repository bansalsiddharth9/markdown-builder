"""Showing Markdown and sources in the browser (design: Document page,
Security: Rendering).

Markdown is rendered with markdown-it-py and sanitized with nh3. The id
attribute is kept on anchors and spans so in-page links work, table spans are
kept, and relative links (media/...) point at the document's file route.
DOCX sources are previewed as Pandoc HTML (display only, decision W-b).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import nh3
from markdown_it import MarkdownIt

TAGS = {
    "a", "abbr", "b", "blockquote", "br", "caption", "code", "col", "colgroup", "dd", "del",
    "details", "div", "dl", "dt", "em", "figcaption", "figure", "h1", "h2", "h3", "h4", "h5", "h6",
    "hr", "i", "img", "ins", "kbd", "li", "mark", "ol", "p", "pre", "q", "s", "samp", "small",
    "span", "strong", "sub", "summary", "sup", "table", "tbody", "td", "tfoot", "th", "thead",
    "tr", "u", "ul", "var",
}  # fmt: skip
ATTRIBUTES = {
    "a": {"href", "id", "title"},
    "span": {"id"},
    "img": {"src", "alt", "title", "width", "height"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan"},
    "col": {"span"},
    "code": {"class"},
    "ol": {"start"},
}
URL_SCHEMES = {"http", "https", "mailto"}


def _markdown() -> MarkdownIt:
    md = MarkdownIt("commonmark", {"html": True}).enable("table")
    md.validateLink = lambda url: True  # nh3 decides which links survive
    return md


def sanitize(html: str, file_base: str | None = None) -> str:
    """nh3 with the allowlist above; relative src/href become file_base + path."""

    def rewrite(element: str, attribute: str, value: str) -> str | None:
        if attribute in ("src", "href") and file_base is not None:
            parts = urlsplit(value)
            if not parts.scheme and not value.startswith(("#", "/")) and value:
                return f"{file_base}/{value}"
        return value

    return nh3.clean(
        html,
        tags=TAGS,
        attributes=ATTRIBUTES,
        url_schemes=URL_SCHEMES,
        attribute_filter=rewrite,
        link_rel="noopener noreferrer",
        strip_comments=True,
    )


def render_markdown(body: str, file_base: str | None = None) -> str:
    return sanitize(_markdown().render(body), file_base)


def docx_preview(path: Path, timeout: float = 60.0) -> str:
    """Pandoc's HTML for a DOCX, sanitized; for display only (W-b)."""
    from specconv.deps import find_pandoc

    pandoc = find_pandoc()
    if pandoc is None:
        return "<p>Preview not available: Pandoc is not installed.</p>"
    try:
        proc = subprocess.run(
            [str(pandoc), str(path), "--sandbox", "-f", "docx", "-t", "html", "--wrap=none"],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return "<p>Preview not available: Pandoc took too long.</p>"
    if proc.returncode != 0:
        return "<p>Preview not available: Pandoc could not read the file.</p>"
    return sanitize(proc.stdout.decode("utf-8", errors="replace"))
