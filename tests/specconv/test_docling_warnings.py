"""Docling's own log warnings are collected into the document's warnings."""

from __future__ import annotations

import logging

from specconv.converters.docling import _WarningCollector


def record(path: str, msg: str, level: int = logging.WARNING) -> logging.LogRecord:
    return logging.LogRecord("MatchingPostProcessor", level, path, 1, msg, None, None)


def test_collects_only_docling_package_warnings() -> None:
    c = _WarningCollector()
    site = "/x/.venv/lib/python3.12/site-packages/"
    c.handle(record(site + "docling_ibm_models/tableformer/m.py", "2 of 217 pdf cells dropped"))
    c.handle(record(site + "docling/pipeline/p.py", "page 3 failed"))
    c.handle(record(site + "docling/pipeline/p.py", "page 3 failed"))  # duplicates once
    c.handle(record(site + "pypdf/_reader.py", "unrelated"))
    c.handle(record("/repo/packages/specconv/src/specconv/converters/docling.py", "ours"))
    c.handle(record(site + "docling/x.py", "debug detail", level=logging.INFO))
    assert c.messages == ["2 of 217 pdf cells dropped", "page 3 failed"]
