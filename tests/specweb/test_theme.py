"""Dark theme with a left sidebar (user request 2026-11-20: dark only, the
navigation in a left sidebar, statuses as pills)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from .conftest import upload

PDF = b"%PDF-1.7\n%fake\n"
CSS = (
    Path(__file__).resolve().parents[2] / "packages/specweb/src/specweb/static/style.css"
).read_text()


@pytest.mark.parametrize(
    "path, active",
    [("/", "Upload"), ("/jobs", "Jobs"), ("/library", "Library")],
)
def test_sidebar_navigation_marks_current_page(client, path: str, active: str) -> None:
    page = client.get(path).text
    sidebar = re.search(r'<aside class="sidebar".*?</aside>', page, re.S)
    assert sidebar, "the navigation is in a left sidebar"
    links = re.findall(r"<a [^>]*>.*?</a>", sidebar.group(0), re.S)
    current = [link for link in links if 'aria-current="page"' in link]
    assert len(current) == 1 and active in current[0]
    for name in ("Upload", "Jobs", "Library"):
        assert any(name in link for link in links)
    assert 'class="top"' not in page  # the old top menu is gone


def test_job_pages_mark_jobs_as_current(client) -> None:
    job = upload(client, files=[("a.pdf", PDF)]).json()
    page = client.get(f"/jobs/{job['id']}").text
    current = re.search(r'<a [^>]*aria-current="page"[^>]*>.*?</a>', page, re.S)
    assert current and "Jobs" in current.group(0)


def test_statuses_shown_as_pills(client) -> None:
    upload(client, files=[("a.pdf", PDF)])
    page = client.get("/jobs").text
    assert re.search(r'<span class="pill s-done">\s*<span class="dot"></span>\s*done', page)
    assert re.search(r'<span class="pill s-pass">\s*<span class="dot"></span>\s*pass', page)


def test_stylesheet_is_dark_only() -> None:
    root = re.search(r":root\s*\{(.*?)\}", CSS, re.S).group(1)
    assert "color-scheme: dark" in root
    background = re.search(r"--bg:\s*#([0-9a-f]{6})", root).group(1)
    assert sum(int(background[i : i + 2], 16) for i in (0, 2, 4)) < 3 * 0x20  # near black
    assert "prefers-color-scheme" not in CSS  # dark only, no light variant
    # No white panels left. Only the original source and the diagrams keep a
    # white background: they are drawn for white paper and unreadable on black.
    white = [
        rule.split("{")[0].strip()
        for rule in re.findall(r"[^{}]+\{[^}]*background:\s*#fff[^}]*\}", CSS)
    ]
    assert white == ["iframe.source", "article.markdown img"]


def test_creator_credit_in_footer(client) -> None:
    for path in ("/", "/jobs", "/library"):
        footer = re.search(r'<footer class="note">.*?</footer>', client.get(path).text, re.S)
        assert footer and "Crafted by" in footer.group(0)
        assert "Siddharth Bansal" in footer.group(0)
        assert "even AI gives up on page 247 of a PDF" in footer.group(0)
