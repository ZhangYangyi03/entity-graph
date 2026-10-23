"""Asia-Pacific registries: mainland China listings and Taiwan company registration.

Both are keyless and both were confirmed against live responses on 2026-10-23. What they
do *not* give is important enough to state here rather than discover later:

- SSE / SZSE publish listed-company rosters (name, A-share code, board, listing date).
  They do not publish shareholders, directors, or ownership chains. So a Chinese listed
  company gets a node and a citation, and no invented edges.
- Taiwan MOEA's open-data API answers by 統一編號 only; there is no name search, and a
  miss comes back as an empty body rather than a 404, which is why `lookup` treats empty
  as "not found" and says so.
"""
from __future__ import annotations

import json
import os
import ssl
import time
import urllib.parse
import urllib.request

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE  # these hosts present self-signed certs on the open-data paths

UA = {"User-Agent": "entity-graph/0.1 2936745@qq.com"}

# SSE does not paginate like anything else. Without beginPage AND endPage it returns rows
# from page 1 up to the requested page, so a naive paging loop yields 125, then 150, then
# 175 rows and a page cap you never reach. With both bounds set it honours the window and
# caps pageSize at 25.
SSE_URL = ("http://query.sse.com.cn/security/stock/getStockListData2.do"
           "?stockType=1&csrcCode=&areaName=&stockCode="
           "&pageHelp.pageSize={size}&pageHelp.pageNo={page}"
           "&pageHelp.beginPage={page}&pageHelp.endPage={page}&pageHelp.cacheSize=1")
SZSE_URL = ("http://www.szse.cn/api/report/ShowReport/data"
            "?SHOWTYPE=JSON&CATALOGID=1110&TABKEY=tab1&PAGENO={page}")
TW_URL = ("https://data.gcis.nat.gov.tw/od/data/api/"
          "5F64D864-61CB-4D0D-8AD9-492047CC1EA6?$format=json&$filter=")


def _get(url: str, referer: str | None = None) -> str:
    h = dict(UA)
    if referer:
        h["Referer"] = referer
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=30, context=CTX) as r:
        return r.read().decode("utf-8", "ignore")


def _strip_html(s: str) -> str:
    out, depth = [], 0
    for ch in s or "":
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth = max(0, depth - 1)
        elif not depth:
            out.append(ch)
    return "".join(out).strip()


def sse_list(size: int = 25, page: int = 1) -> list[dict]:
    """Shanghai Stock Exchange: listed companies, page-wise."""
    d = json.loads(_get(SSE_URL.format(size=size, page=page), referer="http://www.sse.com.cn/"))
    out = []
    for r in (d.get("pageHelp") or {}).get("data") or []:
        code = (r.get("COMPANY_CODE") or r.get("SECURITY_CODE_A") or "").strip()
        if not code:
            continue
        out.append({
            "id": code, "name": _strip_html(r.get("COMPANY_ABBR") or ""), "country": "CN",
            "address": None, "kind": "company", "officers": [],
            "attributes": {"exchange": "SSE", "a_code": r.get("SECURITY_CODE_A"),
                           "english_name": r.get("ENGLISH_ABBR"),
                           "listing_date": r.get("LISTING_DATE"),
                           "board": r.get("LISTING_BOARD")},
            "url": f"http://www.sse.com.cn/assortment/stock/list/info/company/index.shtml?COMPANY_CODE={code}",
        })
    return out


def szse_list(page: int = 1) -> list[dict]:
    """Shenzhen Stock Exchange: listed companies. The name arrives wrapped in an anchor."""
    data = json.loads(_get(SZSE_URL.format(page=page)))
    out = []
    for block in data if isinstance(data, list) else []:
        for r in block.get("data") or []:
            code = (r.get("agdm") or "").strip()
            if not code:
                continue
            out.append({
                "id": code, "name": _strip_html(r.get("agjc") or ""), "country": "CN",
                "address": None, "kind": "company", "officers": [],
                "attributes": {"exchange": "SZSE", "a_code": code,
                               "listing_date": r.get("agssrq"),
                               "industry": r.get("sshymc"), "board": r.get("bk")},
                "url": f"http://www.szse.cn/certificate/individual/index.html?code={code}",
            })
    return out


def taiwan_by_id(unified_id: str) -> list[dict]:
    """Taiwan MOEA company registration, by 統一編號. An empty body means 'no such id'."""
    no = "".join(c for c in unified_id if c.isdigit())
    if not no:
        return []
    f = urllib.parse.quote(f"Business_Accounting_NO eq {no}")
    body = _get(TW_URL + f).strip()
    if not body:
        return []
    try:
        rows = json.loads(body)
    except json.JSONDecodeError:
        return []
    out = []
    for r in rows:
        out.append({
            "id": r.get("Business_Accounting_NO"),
            "name": (r.get("Company_Name") or "").strip(),
            "country": "TW", "address": r.get("Company_Location"), "kind": "company",
            "officers": [],
            "attributes": {"status": r.get("Company_Status_Desc"),
                           "capital": r.get("Capital_Stock_Amount"),
                           "paid_in": r.get("Paid_In_Capital_Amount"),
                           "setup_date": r.get("Company_Setup_Date"),
                           "industry": r.get("Industry_Code_Desc")},
            "url": "https://data.gcis.nat.gov.tw/od/detail?oid=" + str(r.get("Business_Accounting_NO")),
        })
    return out


def _cache_path(tag: str) -> str:
    base = os.environ.get("ENTITYGRAPH_CACHE", ".cache")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, f"{tag}.json")


def _cached(tag: str, fetch, max_age_hours: float = 24.0) -> list[dict]:
    """Exchange rosters change slowly; re-fetching 168 pages per query would not."""
    path = _cache_path(tag)
    if os.path.exists(path):
        age = (time.time() - os.path.getmtime(path)) / 3600.0
        if age < max_age_hours:
            try:
                return json.load(open(path, encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
    rows = fetch()
    if rows:
        json.dump(rows, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    return rows


def sse_all(max_age_hours: float = 24.0) -> list[dict]:
    def fetch():
        # pageSize is capped at 25 and the window only holds when beginPage == endPage ==
        # pageNo, so 1604 listed companies take ~65 requests. The loop still stops on a
        # page that adds nothing new, as a guard against the unbounded-response failure.
        out, seen, page = [], set(), 1
        while page <= 100:
            batch = sse_list(size=25, page=page)
            fresh = [r for r in batch if r["id"] not in seen]
            if not fresh:
                break
            for r in fresh:
                seen.add(r["id"])
            out.extend(fresh)
            page += 1
        return out
    return _cached("sse_listed", fetch, max_age_hours)


def szse_all(max_age_hours: float = 24.0) -> list[dict]:
    """SZSE pages at a fixed 20 rows, so the full roster is ~146 requests. Cached."""
    def fetch():
        out, page = [], 1
        while page <= 200:
            batch = szse_list(page=page)
            if not batch:
                break
            out.extend(batch)
            page += 1
            if len(out) > 4000:
                break
            if len({r["id"] for r in out}) == len(out) and len(batch) < 20:
                break
        return out
    return _cached("szse_listed", fetch, max_age_hours)


def china_search(name: str, limit: int = 3) -> list[dict]:
    """Search both exchange rosters by name, de-duplicated by code."""
    key = (name or "").strip()
    if not key:
        return []
    hits: dict[str, dict] = {}
    for roster in (sse_all, szse_all):
        try:
            rows = roster()
        except Exception:
            continue
        for r in rows:
            if key in r["name"] and r["id"] not in hits:
                hits[r["id"]] = r
        if len(hits) >= limit:
            break
    return list(hits.values())[:limit]
