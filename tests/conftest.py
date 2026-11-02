"""No test in this suite may touch the network. Enforced here, not left to discipline.

The suite's own docstrings claimed the live sources were stubbed; they were not. Measured
2026-11-02 on a fresh clone with a real route: 131 seconds, of which 46 s was
test_build_registry calling api.gleif.org and 83 s was test_banking waiting on the World
Bank. Pointing HTTP_PROXY at a black hole does not show this -- a connection refused and a
connection that times out both look like "offline" from inside a test. Pulling the socket
closed does, so it is pulled.
"""
import os
import socket
import tempfile

import pytest


@pytest.fixture(autouse=True, scope="session")
def _isolated_cache():
    """Point the on-disk fetch cache at a fresh directory for the whole session.

    The cache is what still reached the World Bank after the socket was closed: a
    populated .cache in the checkout made the test pass in 1.0 s, and a clean clone made
    the same test wait 13 s for a connection it was never going to get. A test that passes
    only where somebody has already fetched is not a test of the code.
    """
    import entitygraph.fetch as fetch
    tmp = tempfile.mkdtemp(prefix="eg-cache-")
    old = fetch.CACHE_DIR
    fetch.CACHE_DIR = tmp
    yield
    fetch.CACHE_DIR = old


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
