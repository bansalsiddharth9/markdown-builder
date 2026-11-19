"""Jinja2 templates shared by the page routes."""

from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

HERE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(HERE / "templates"))
