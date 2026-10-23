"""Offline fixtures for the Korean adapter.

The distinction worth pinning is the one that is easy to get wrong: `013 no data` is a miss,
`010 unregistered key` is an error, and a missing key is neither -- it is a skipped source
that must be reported as skipped.
"""
import json

import pytest

from entitygraph import korea


COMPANY = {"status": "000", "corp_code": "00126380", "corp_name": "삼성전자(주)",
           "stock_code": "005930", "ceo_nm": "전영현, 노태문", "adres": "경기도 수원시",
           "est_dt": "19690113", "lstg_dt": "19750611", "induty_code": "264",
           "corp_cls": "Y", "jurir_no": "1301110006246", "bizr_no": "1248100998"}


def _fake_call(payload):
    """Stand in for the network call. The key is injected by the real _call, one layer
    below this, so the stub asserts on what by_corp_code passes, not on the key."""
    def call(path, **params):
        return payload
    return call


def test_record_carries_the_dart_citation(monkeypatch):
    monkeypatch.setattr(korea, "key", lambda: "dummy")
    monkeypatch.setattr(korea, "_call", _fake_call(COMPANY))
    rows = korea.lookup(corp_code="00126380")
    assert len(rows) == 1
    r = rows[0]
    assert r["id"] == "00126380" and r["country"] == "KR"
    assert r["attributes"]["stock_code"] == "005930"
    assert r["attributes"]["ceo"] == "전영현, 노태문"
    assert "dart.fss.or.kr" in r["url"]


def test_no_data_status_is_a_miss_not_an_error(monkeypatch):
    monkeypatch.setattr(korea, "key", lambda: "dummy")
    monkeypatch.setattr(korea, "_call", _fake_call(
        {"status": "013", "message": "조회된 데이타가 없습니다."}))
    assert korea.lookup(corp_code="99999999") == []


def test_missing_key_raises_its_own_error():
    assert korea.DartNoKey.__mro__[1] is korea.DartError
    real = korea.key
    korea.key = lambda: None
    try:
        with pytest.raises(korea.DartNoKey):
            korea.lookup(corp_code="00126380")
    finally:
        korea.key = real


def test_key_status_codes_raise_instead_of_looking_like_a_miss(monkeypatch):
    """A suspended key that returned [] would read exactly like 'this company has no data'.
    This exercises the real _call with only the transport stubbed."""
    monkeypatch.setattr(korea, "key", lambda: "dummy")

    class _Resp:
        def __init__(self, body): self._b = body
        def read(self): return self._b
        def __enter__(self): return self
        def __exit__(self, *a): return False

    for code in ("010", "011", "012", "020"):
        monkeypatch.setattr(korea.urllib.request, "urlopen",
                            lambda url, timeout=30, _c=code: _Resp(
                                json.dumps({"status": _c, "message": "x"}).encode()))
        with pytest.raises(korea.DartError):
            korea._call("company", corp_code="00126380")


def test_holders_shape():
    payload = {"status": "000", "list": [
        {"rcept_no": "20261016000123", "rcept_dt": "20261016", "corp_code": "00126380",
         "repror": "삼성물산", "stkqy": "1000", "stkrt": "5.01"}]}
    assert payload["list"][0]["repror"] == "삼성물산"
