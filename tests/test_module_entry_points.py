"""`python -m specconv` and `python -m specval` run the CLIs; the web worker
runs every step this way, in a subprocess with a timeout (design: Jobs and workers)."""

from __future__ import annotations

import subprocess
import sys

import pytest


@pytest.mark.parametrize("module, command", [("specconv", "convert"), ("specval", "run")])
def test_python_dash_m(module: str, command: str) -> None:
    proc = subprocess.run(
        [sys.executable, "-m", module, "--help"], capture_output=True, text=True, timeout=120
    )
    assert proc.returncode == 0, proc.stderr
    assert command in proc.stdout
