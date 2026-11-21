"""Jinja2 templates shared by the page routes."""

from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

HERE = Path(__file__).resolve().parent  # the package folder (templates/, static/)
# Jinja2Templates turns on HTML autoescaping for .html files, so values from users
# (names, file names, URLs) are escaped. Only `| safe` output skips it, and
# that HTML is sanitized first (render.py).
templates = Jinja2Templates(directory=str(HERE / "templates"))
