"""Offline fixtures for the Asia-Pacific adapters.

The shapes below are copied from live responses on 2026-10-23; nothing here hits the
network. The point is to pin the two things that cost real debugging time: SSE's paging
window (beginPage/endPage, or you get 125 rows and think you got 25) and Taiwan's
"empty body means no such id".
"""
import json

import pytest

from entitygraph import asia


SSE_PAGE = {
    "pageHelp": {
        "pageNo": 2, "pageSize": 25, "total": 1604,
        "data": [
            {"COMPANY_CODE": "600032", "COMPANY_ABBR": "浙江新能",
             "SECURITY_CODE_A": "600032", "LISTING_DATE": "1997-05-09",
             "LISTING_BOARD": "1", "ENGLISH_ABBR": ""},
        ],
    }
}

SZSE_PAGE = [{
    "metadata": {"pagesize": 20, "pageno": 1, "recordcount": 2906},
    "data": [
        {"agdm": "000001",
         "agjc": "<a href='http://www.szse.cn/certificate/individual/index.html?code=000001' "
                 "target='_blank'><u>平安银行</u></a>",
         "agssrq": "1991-04-03", "sshymc": "J 金融業", "bk": "主板"},
    ],
}]

TW_ROW = json.dumps([{
    "Business_Accounting_NO": "04541302", "Company_Status_Desc": "核准設立",
    "Company_Name": "鴻海精密工業股份有限公司",
    "Capital_Stock_Amount": 180000000000, "Paid_In_Capital_Amount": 140757464970,
    "Company_Location": "新北市土城區", "Company_Setup_Date": "19740620",
}])


def test_sse_url_carries_the_paging_window():
    """Without beginPage == endPage the exchange returns page 1 through page N."""
    url = asia.SSE_URL.format(size=25, page=3)
    assert "pageHelp.pageNo=3" in url
    assert "pageHelp.beginPage=3" in url
    assert "pageHelp.endPage=3" in url


def test_sse_list_maps_fields(monkeypatch):
    monkeypatch.setattr(asia, "_get", lambda *a, **k: json.dumps(SSE_PAGE))
    rows = asia.sse_list(size=25, page=2)
    assert len(rows) == 1
    r = rows[0]
    assert r["id"] == "600032" and r["country"] == "CN"
    assert r["attributes"]["exchange"] == "SSE"
    assert r["attributes"]["listing_date"] == "1997-05-09"
    assert "600032" in r["url"]


def test_szse_strips_the_anchor_around_the_name(monkeypatch):
    monkeypatch.setattr(asia, "_get", lambda *a, **k: json.dumps(SZSE_PAGE))
    rows = asia.szse_list()
    assert rows[0]["name"] == "平安银行"
    assert rows[0]["attributes"]["exchange"] == "SZSE"
    assert rows[0]["attributes"]["industry"] == "J 金融業"


def test_taiwan_empty_body_is_not_found_not_an_error(monkeypatch):
    monkeypatch.setattr(asia, "_get", lambda *a, **k: "")
    assert asia.taiwan_by_id("22099166") == []


def test_taiwan_by_id_maps_fields(monkeypatch):
    seen = {}

    def fake_get(url, *a, **k):
        seen["url"] = url
        return TW_ROW

    monkeypatch.setattr(asia, "_get", fake_get)
    rows = asia.taiwan_by_id("04541302")
    assert "Business_Accounting_NO" in seen["url"]
    r = rows[0]
    assert r["id"] == "04541302" and r["country"] == "TW"
    assert r["name"] == "鴻海精密工業股份有限公司"
    assert r["attributes"]["status"] == "核准設立"
    assert r["attributes"]["capital"] == 180000000000


def test_taiwan_ignores_non_digits():
    assert asia.taiwan_by_id("统一编号") == []


def test_china_search_dedupes_by_code(monkeypatch):
    """SSE answered the same page twice during development; one company must not appear twice."""
    rows = [{"id": "600519", "name": "贵州茅台", "country": "CN", "address": None,
             "kind": "company", "officers": [], "attributes": {"exchange": "SSE"}, "url": "u"}]
    monkeypatch.setattr(asia, "sse_all", lambda *a, **k: rows + rows)
    monkeypatch.setattr(asia, "szse_all", lambda *a, **k: [])
    hits = asia.china_search("茅台")
    assert len(hits) == 1 and hits[0]["id"] == "600519"


def test_china_search_empty_name_returns_nothing(monkeypatch):
    monkeypatch.setattr(asia, "sse_all", lambda *a, **k: pytest.fail("must not fetch"))
    assert asia.china_search("  ") == []
