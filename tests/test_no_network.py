"""The autouse guard in tests/conftest.py keeps tests off the internet."""

from __future__ import annotations

import socket

import pytest


def test_outside_connections_blocked() -> None:
    with pytest.raises(OSError, match="test tried to reach the network"):
        socket.create_connection(("192.0.2.1", 80), timeout=1)  # TEST-NET-1, never routed
