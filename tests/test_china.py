"""Offline tests for the Tianyancha (China registry) adapter.

Pinned behaviours, mirroring test_korea.py:
  * a missing token is a *skipped source*, reported as TianyanchaNoKey, not an error
  * a hit carries a credit code as its id, the legal person as an officer, and
    shareholders / investments in attributes for the build layer to expand
  * an empty result (error_code != 0) is a clean miss, not a crash
"""
import pytest

from entitygraph import china


INFO = {
    "error_code": 0,
    "result": {
        "name": "天津协医科技有限公司",
        "regNumber": "91120104592923051X",
        "legalPersonName": "杨桦",
        "orgType": "有限责任公司",
        "regStatus": "存续",
        "regCapital": "100万",
        "regLocation": "天津市南开区白堤路236号",
    },
}

HOLDERS = {"error_code": 0, "result": {"items": [
    {"name": "中国医学科学院生物医学工程研究所", "type": "法人", "percent": "100%"},
]}}

INVESTS = {"error_code": 0, "result": {"items": [
    {"name": "天开协医（天津）医学科技有限公司", "regNumber": "91120104XXXX", "percent": "10%"},
]}}


def _fake_call(path, params):
    return {"baseinfo": INFO, "holder": HOLDERS, "invest": INVESTS}.get(
        "baseinfo" if "baseinfo" in path else
        "holder" if "holder" in path else "invest")


def test_missing_token_is_a_skip(monkeypatch):
    monkeypatch.delenv("TIANYANCHA_TOKEN", raising=False)
    monkeypatch.delenv("TIANYANCHA_API_KEY", raising=False)
    with pytest.raises(china.TianyanchaNoKey):
        china.china_search("天津协医")


def test_lookup_expands_holders_and_invests(monkeypatch):
    monkeypatch.setenv("TIANYANCHA_TOKEN", "dummy")
    monkeypatch.setattr(china, "_call", _fake_call)
    hits = china.lookup("天津协医科技有限公司")
    assert len(hits) == 1
    rec = hits[0]
    assert rec["country"] == "CN"
    assert rec["id"] == "91120104592923051X"
    assert rec["name"] == "天津协医科技有限公司"
    assert rec["attributes"]["legal_person"] == "杨桦"
    assert rec["officers"][0]["name"] == "杨桦"
    holders = rec["attributes"]["shareholders"]
    assert holders[0]["name"] == "中国医学科学院生物医学工程研究所"
    assert holders[0]["percent"] == "100%"
    inv = rec["attributes"]["investments"]
    assert inv[0]["name"] == "天开协医（天津）医学科技有限公司"


def test_nonzero_error_code_is_a_clean_miss(monkeypatch):
    monkeypatch.setenv("TIANYANCHA_TOKEN", "dummy")
    monkeypatch.setattr(china, "_call", lambda path, params: {"error_code": 404, "result": None})
    assert china.lookup("不存在的公司") == []
