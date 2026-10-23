import os
import tempfile

import pytest

from entitygraph import fetch


def test_cache_hit_avoids_network(monkeypatch, tmp_path):
    monkeypatch.setattr(fetch, "CACHE_DIR", str(tmp_path))
    calls = {"n": 0}

    class FakeResp:
        def read(self):
            return b'{"ok": 1}'
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        return FakeResp()

    monkeypatch.setattr(fetch.urllib.request, "urlopen", fake_urlopen)
    assert fetch.get("https://example.invalid/x") == {"ok": 1}
    assert fetch.get("https://example.invalid/x") == {"ok": 1}
    assert calls["n"] == 1


def test_ttl_zero_bypasses_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(fetch, "CACHE_DIR", str(tmp_path))
    calls = {"n": 0}

    class FakeResp:
        def read(self):
            return b'{"ok": 1}'
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    monkeypatch.setattr(fetch.urllib.request, "urlopen",
                        lambda req, timeout=None: (calls.__setitem__("n", calls["n"] + 1), FakeResp())[1])
    fetch.get("https://example.invalid/y", ttl=0)
    fetch.get("https://example.invalid/y", ttl=0)
    assert calls["n"] == 2


def test_404_is_not_retried(monkeypatch, tmp_path):
    monkeypatch.setattr(fetch, "CACHE_DIR", str(tmp_path))
    calls = {"n": 0}

    def boom(req, timeout=None):
        calls["n"] += 1
        raise fetch.urllib.error.HTTPError(req.full_url, 404, "nope", {}, None)

    monkeypatch.setattr(fetch.urllib.request, "urlopen", boom)
    with pytest.raises(fetch.FetchError):
        fetch.get("https://example.invalid/z")
    assert calls["n"] == 1
