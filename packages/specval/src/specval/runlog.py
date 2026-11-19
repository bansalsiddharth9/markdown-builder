"""Per-run logging: everything goes to a log file, INFO and above also to stderr."""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

LOGGER_NAME = "specval"
FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def run_timestamp() -> str:
    """UTC timestamp for file names, e.g. 20261119T104200Z."""
    return dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")


@contextmanager
def run_logging(log_file: Path, verbose: bool = False) -> Iterator[logging.Logger]:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(FORMAT))
    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    logger.addHandler(file_handler)
    logger.addHandler(console)
    try:
        yield logger
    finally:
        for handler in (file_handler, console):
            logger.removeHandler(handler)
            handler.close()
