"""Offline tests for the Qichacha (China registry) adapter.

Pinned behaviours, mirroring test_korea.py:
  * a missing key is a *skipped source*, reported as QccNoKey, not an error
  * a hit carries a credit code as its id, the legal person as an officer, and
    shareholders / investments in attributes for the build layer to expand
  * a non-200 Status is a clean miss, not a crash
"""
import pytest

from entitygraph import china


INFO = {
    "Status": "200",
    "Result": {
        "Name": "天津协医科技有限公司",
        "CreditCode": "91120104592923051X",
        "OperName": "杨桦",
        "EconKind": "有限责任公司",
        "Status": "存续",
        "RegistCapi": "100万",
        "Address": "天津市南开区白堤路236号",
    },
}

HOLDERS = {"Status": "200", "Result": {"Partners": [
    {"StockName": "中国医学科学院生物医学工程研究所", "StockType": "法人", "StockPercent": "100%"},
]}}

INVESTS = {"Status": "200", "Result": {"Data": [
    {"Name": "天开协医（天津）医学科技有限公司", "CreditCode": "91120104XXXX", "FundedRatio": "10%"},
]}}


def _fake_call(path, params):
    if "GetBasicDetails" in path or "GetInfo" in path:
        return INFO
    if "Partner" in path or "Holder" in path:
        return HOLDERS
    return INVESTS


def test_missing_key_is_a_skip(monkeypatch):
    monkeypatch.delenv("QCC_APP_KEY", raising=False)
    monkeypatch.delenv("QCC_SECRET_KEY", raising=False)
    monkeypatch.delenv("TIANYANCHA_TOKEN", raising=False)
    with pytest.raises(china.QccNoKey):
        china.china_search("天津协医")


def test_lookup_expands_holders_and_invests(monkeypatch):
    monkeypatch.setenv("QCC_APP_KEY", "appkey")
    monkeypatch.setenv("QCC_SECRET_KEY", "secretkey")
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


def test_non_200_status_is_a_clean_miss(monkeypatch):
    monkeypatch.setenv("QCC_APP_KEY", "appkey")
    monkeypatch.setenv("QCC_SECRET_KEY", "secretkey")
    monkeypatch.setattr(china, "_call", lambda path, params: {"Status": "404", "Result": None})
    assert china.lookup("不存在的公司") == []
