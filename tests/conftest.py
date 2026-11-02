"""No test in this suite may touch the network. Enforced here, not left to discipline.

The suite's own docstrings claimed the live sources were stubbed; they were not. Measured
2026-11-02 on a fresh clone with a real route: 131 seconds, of which 46 s was
test_build_registry calling api.gleif.org and 83 s was test_banking waiting on the World
Bank. Pointing HTTP_PROXY at a black hole does not show this -- a connection refused and a
connection that times out both look like "offline" from inside a test. Pulling the socket
closed does, so it is pulled.
"""
import socket

import pytest


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """Any attempt to open a socket fails loudly and instantly."""
    def refuse(*args, **kwargs):
        raise RuntimeError(
            "this test tried to open a network connection; stub the source instead "
            "(see tests/test_build_registry.py::_offline for the pattern)")
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse, raising=False)
    monkeypatch.setattr(socket.socket, "connect_ex", lambda *a, **k: 1, raising=False)
