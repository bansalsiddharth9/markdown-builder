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
    """Log to ``log_file`` (DEBUG and up) and stderr while the block runs.

    The log file is validation/run-<timestamp>.log (deviation C6). stderr
    shows INFO and up, or DEBUG with ``verbose``. The handlers are removed and
    closed on exit, so repeated runs in one process (tests, the web app) do not
    print every message several times.
    """
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
    # The Markdown linter adds a handler to the root logger; without this every
    # message would be printed twice.
    propagate, logger.propagate = logger.propagate, False
    try:
        yield logger
    finally:
        logger.propagate = propagate
        for handler in (file_handler, console):
            logger.removeHandler(handler)
            handler.close()
