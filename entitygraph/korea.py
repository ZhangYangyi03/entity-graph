"""Korea: OpenDART (금융감독원 전자공시), the only registry so far that needs a key.

The key is free but personal -- it is issued against an email and carries a request
quota -- so it is read from the environment (`OPENDART_API_KEY`) rather than committed.
`python -m entitygraph.ingest` is not involved; absence of the key means the source is
skipped with a note, never silently treated as "no data".

What it actually gives, measured on Samsung Electronics (corp_code 00126380):
  - company profile: Korean name, stock code, CEO names, address
  - disclosure index: 2,782 filings in 2026 alone
  - major shareholders / 5% holders: 43 rows (지분공시)
What it does not give: the full shareholder register. That is in the annual report (사업보고서),
reachable through the same key but a separate parse, and is not wired here.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

BASE = "https://opendart.fss.or.kr/api"
UA = {"User-Agent": "entity-graph/0.1"}

# OpenDART status codes that mean "the key is wrong / not usable", as opposed to a miss.
KEY_ERRORS = {"010": "unregistered key", "011": "key suspended",
              "012": "quota exceeded", "020": "request limit exceeded"}


class DartError(RuntimeError):
    """Raised for anything that is not a clean 'no such company'."""


class DartNoKey(DartError):
    pass


def key() -> str | None:
    k = (os.environ.get("OPENDART_API_KEY") or "").strip()
    return k or None


def _call(path: str, **params) -> dict:
    k = key()
    if not k:
        raise DartNoKey("OPENDART_API_KEY is not set; the Korean source is skipped")
    params["crtfc_key"] = k
    url = f"{BASE}/{path}.json?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
        d = json.loads(r.read().decode("utf-8", "ignore"))
    status = str(d.get("status", ""))
    if status in KEY_ERRORS:
        raise DartError(f"OpenDART {status}: {KEY_ERRORS[status]}")
    if status != "000":
        # 013 = no data found; anything else is worth surfacing, but not as an exception
        # that would abort a whole graph build.
        return {"status": status, "message": d.get("message"), "list": [],
                "corp_name": d.get("corp_name")}
    return d


def _record(d: dict) -> dict:
    code = d.get("corp_code")
    return {
        "id": code, "name": d.get("corp_name") or code, "country": "KR",
        "address": d.get("adres"), "kind": "company", "officers": [],
        "attributes": {
            "stock_code": d.get("stock_code") or None,
            "ceo": d.get("ceo_nm") or None,
            "established": d.get("est_dt") or None,
            "listing_date": d.get("lstg_dt") or None,
            "industry": d.get("induty_code") or None,
            "corp_class": d.get("corp_cls"),
            "jurisdiction": d.get("jurir_no"),
            "business_no": d.get("bizr_no"),
        },
        "url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=&corpCode=" + str(code),
    }


def by_corp_code(code: str) -> list[dict]:
    d = _call("company", corp_code=code)
    if not d.get("corp_name"):
        return []
    return [_record(d)]


def by_stock_code(stock_code: str) -> list[dict]:
    """DART indexes by corp_code, so a ticker needs the company list first."""
    return [r for r in all_companies() if r["attributes"].get("stock_code") == stock_code]


def all_companies(zip_path: str | None = None) -> list[dict]:
    """corpCode.xml: a zip of every DART-registered company (~90k). Cached on disk."""
    import io
    import xml.etree.ElementTree as ET
    import zipfile

    cache = zip_path or os.path.join(os.environ.get("ENTITYGRAPH_CACHE", ".cache"), "dart_corps.xml")
    if not os.path.exists(cache):
        k = key()
        if not k:
            raise DartNoKey("OPENDART_API_KEY is not set")
        os.makedirs(os.path.dirname(cache) or ".", exist_ok=True)
        url = f"{BASE}/corpCode.xml?crtfc_key={k}"
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120) as r:
            blob = r.read()
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            name = z.namelist()[0]
            open(cache, "wb").write(z.read(name))
    rows = []
    for item in ET.parse(cache).getroot():
        get = lambda t: (item.findtext(t) or "").strip()
        rows.append({"id": get("corp_code"), "name": get("corp_name"), "country": "KR",
                     "address": None, "kind": "company", "officers": [],
                     "attributes": {"stock_code": get("stock_code") or None},
                     "url": "https://dart.fss.or.kr/dsaf001/main.do?corpCode=" + get("corp_code")})
    return rows


def lookup(name: str | None = None, corp_code: str | None = None,
           stock_code: str | None = None, limit: int = 3) -> list[dict]:
    if corp_code:
        return by_corp_code(corp_code)
    if stock_code:
        return by_stock_code(stock_code)[:limit]
    if not name:
        return []
    key_n = name.strip().replace(" ", "")
    return [r for r in all_companies() if key_n and key_n in r["name"].replace(" ", "")][:limit]


def filings(corp_code: str, start: str = "20260101", end: str = "20261231") -> list[dict]:
    d = _call("list", corp_code=corp_code, bgn_de=start, end_de=end, page_count=100)
    return d.get("list") or []


def major_holders(corp_code: str) -> list[dict]:
    """5% / insider holdings reports. Each row cites the DART receipt number."""
    d = _call("majorstock", corp_code=corp_code)
    return d.get("list") or []
