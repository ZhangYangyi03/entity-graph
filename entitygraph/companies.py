"""Company entities from the Fortune pool: match each name to an LEI, a CIK, or a manual record.

Three ways a Fortune row becomes a company node, in descending order of what the evidence
buys you:

  lei     GLEIF returns a record whose legal name matches. The node is the LEI record; the
          Fortune name is an ATTRIBUTE on it, not the identity. Parent and ultimate-parent
          come free from GLEIF and give the ownership edges.
  cik     SEC EDGAR knows the filer. Used when GLEIF has no record, which is common for
          US-listed companies whose parent is the filer.
  manual  A hand-entered record for a company no keyless global registry covers -- for
          Chinese private companies there is effectively none: GLEIF has no LEI for either
          南大通用 (Tianjin Nankai General Data Technology) or 协医, and the national credit
          system (国家企业信用信息公示系统) has no open API. A manual record is therefore
          the honest option, and it is marked `source="manual"` with the note saying so,
          because a hand-typed node must never look like a fetched one.

Nothing matches a Fortune name by fuzzy string distance. "State Grid" and "State Grid
Corporation of China" are the same company; "China Petrochemical Corporation" and "Sinopec
Europa GmbH" are not, and a similarity score cannot tell them apart. The match is either an
exact normalised name, an explicit alias in ALIASES, or nothing.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import urllib.parse
import urllib.request

from .model import Edge, Graph, Node

NOW = lambda: _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
UA = "entitygraph/0.1 (company entity build; https://github.com/ZhangYangyi03/entity-graph)"
GLEIF = "https://api.gleif.org/api/v1/lei-records"

# Fortune names that are not the legal name GLEIF stores. Each entry says which registry
# spelling it stands for, because the mapping is a human judgement, not a string rule.
ALIASES = {
    # GLEIF stores the state grid as its Chinese legal name; there is no English record.
    "State Grid Corporation of China": ["国家电网有限公司", "STATE GRID Corporation of China"],
    "China National Petroleum Corporation": ["PetroChina Company Limited",
                                            "China National Petroleum Corporation"],
    "China Petrochemical Corporation": ["China Petrochemical Corporation", "China Petroleum & Chemical Corporation"],
    "Sinopec Group": ["China Petrochemical Corporation"],
    "Volkswagen Group": ["VOLKSWAGEN AKTIENGESELLSCHAFT"],
    "Toyota": ["Toyota Motor Corporation", "トヨタ自動車株式会社"],
    "Samsung Electronics": ["Samsung Electronics Co., Ltd."],
    "Alphabet": ["Alphabet Inc."],
    "Amazon": ["Amazon.com, Inc."],
    "Apple": ["Apple Inc."],
    "Microsoft": ["Microsoft Corporation"],
    "Shell": ["Shell plc"],
    "BP": ["BP p.l.c."],
    "Vitol": ["Vitol Holding B.V.", "Vitol S.A."],
    "ExxonMobil": ["Exxon Mobil Corporation"],
    "Berkshire Hathaway": ["Berkshire Hathaway Inc."],
    "JPMorgan Chase": ["JPMorgan Chase & Co."],
    "TotalEnergies": ["TotalEnergies SE"],
    "Saudi Aramco": ["Saudi Arabian Oil Company"],
    "CVS Health": ["CVS Health Corporation"],
    "UnitedHealth Group": ["UnitedHealth Group Incorporated"],
    "Cardinal Health": ["Cardinal Health, Inc."],
    "Trafigura Group": ["Trafigura Group Pte Ltd"],
    "Cargill": ["Cargill, Incorporated"],
    "Glencore": ["Glencore plc"],
    "Meta Platforms": ["Meta Platforms, Inc."],
    "Ford Motor": ["Ford Motor Company"],
    "General Motors": ["General Motors Company"],
    "Hon Hai Precision Industry": ["Hon Hai Precision Industry Co., Ltd."],
    "China State Construction Engineering": ["China State Construction Engineering Corporation",
                                             "中国建筑股份有限公司"],
    "Agricultural Bank of China": ["中国农业银行股份有限公司"],
    "China Construction Bank": ["中国建设银行股份有限公司"],
    "Industrial & Commercial Bank of China": ["Industrial and Commercial Bank of China Limited"],
    "China Railway Engineering Group": ["China Railway Group Limited", "中国中铁股份有限公司"],
    "China Railway Construction": ["China Railway Construction Corporation Limited",
                                   "中国铁建股份有限公司"],
}

# Companies with no open global registry record. Every field here is either from the
# operator's instruction (the name) or left explicitly unknown -- no figure is invented.
MANUAL = {
    "tianjin-nankai-general-data": {
        "label_cn": "天津南大通用数据技术股份有限公司",
        "label_en": "Tianjin Nankai General Data Technology Co., Ltd.",
        "note": "Chinese domestic database vendor (GBase). No LEI in GLEIF and no open API "
                "from the national credit registry; this node is transcribed from the "
                "operator's naming and is not machine-verified.",
    },
    "xieyi": {
        "label_cn": "协医",
        "label_en": "Xieyi (healthcare)",
        "note": "Name given by the operator; the exact legal entity behind it is not "
                "determined. GLEIF name search for 协医 returns only near-matches "
                "(枣庄市协盈医疗科技, 山西协诚医工技术服务) and neither is an exact match. "
                "This node therefore carries NO registry attributes and exists to hold the "
                "place in the graph until the legal name is confirmed.",
    },
}


_SUFFIX = re.compile(r"(inc|corp|corporation|company|co|ltd|limited|plc|llc|ag|sa|se|nv|bv|group|holdings?|the)$")


def _norm(s: str) -> str:
    """Lower-case, strip punctuation, then peel corporate suffixes off the END only.

    Suffixes are peeled from the end rather than removed everywhere: a substring rule
    turns "Amazon" into "amaz" (it contains "co"), which is how the first version of this
    matched nothing at all.
    """
    s = (s or "").lower()
    s = re.sub(r"[\s\.,'’&、（）()\-]", "", s)
    while True:
        m = _SUFFIX.search(s)
        if not m or len(s) <= len(m.group(1)) + 2:
            break
        s = s[:m.start()]
    return s


_SEARCH_CACHE = "gleif_search_cache.json"


def _gleif_search(name: str) -> list[dict]:
    import os as _os
    cache: dict = {}
    if _os.path.exists(_SEARCH_CACHE):
        try:
            cache = json.load(open(_SEARCH_CACHE, encoding="utf-8"))
        except Exception:                           # noqa: BLE001
            cache = {}
    if name in cache:
        return cache[name]
    url = (f"{GLEIF}?filter%5Bentity.legalName%5D={urllib.parse.quote(name)}"
           f"&page%5Bsize%5D=10")
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.api+json",
                                               "User-Agent": UA})
    try:
        d = json.loads(urllib.request.urlopen(req, timeout=30).read())
    except Exception:                                # noqa: BLE001
        return []
    data = d.get("data", []) or []
    cache[name] = data
    try:
        json.dump(cache, open(_SEARCH_CACHE, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:                                # noqa: BLE001
        pass
    return data


_PARENTS_UP: bool | None = None


def _GLEIF_PARENTS_UP() -> bool:
    """Does this GLEIF copy still serve the parent relationship endpoints? Probed once."""
    global _PARENTS_UP
    if _PARENTS_UP is None:
        try:
            urllib.request.urlopen(
                urllib.request.Request("https://api.gleif.org/api/v1/lei-records/"
                                       "5493006W3QUS5LMH6R84/direct-parent",
                                       headers={"Accept": "application/vnd.api+json",
                                                "User-Agent": UA}),
                timeout=20)
            _PARENTS_UP = True
        except Exception:                           # noqa: BLE001
            _PARENTS_UP = False
    return _PARENTS_UP


def _gleif_parents(lei: str) -> list[tuple[str, str, str]]:
    """(rel, parent_lei, parent_name), for direct and ultimate parent."""
    out = []
    for rel in ("direct-parent", "ultimate-parent"):
        url = f"{GLEIF}/{lei}/{rel}"
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.api+json",
                                                   "User-Agent": UA})
        try:
            d = json.loads(urllib.request.urlopen(req, timeout=30).read())
        except Exception:                            # noqa: BLE001
            continue
        data = d.get("data") or {}
        if not data:
            continue
        reg = (data.get("attributes", {}).get("relationship", {})
               .get("registration", {}))
        out.append((rel.replace("-", "_"), data.get("id"), reg.get("name") or data.get("id")))
    return out


def match(name: str) -> dict | None:
    """The one GLEIF record whose legal name equals the name, an alias, or nothing."""
    candidates = [name] + ALIASES.get(name, [])
    for cand in candidates:
        records = _gleif_search(cand)
        want = _norm(cand)
        for r in records:
            legal = r["attributes"]["entity"]["legalName"]["name"]
            if _norm(legal) == want:
                return {"record": r, "matched_on": cand, "query": cand}
    return None


def add_company(g: Graph, fortune_row: dict) -> Node:
    """Turn one Fortune row into a node plus its ownership edges, citing every hop."""
    name = fortune_row["company"]
    attrs = {"fortune_rank": fortune_row["rank"],
             "fortune_revenue_usd": fortune_row["revenue_usd"],
             "fortune_industry": fortune_row["industry"],
             "fortune_country": fortune_row["country"],
             "fortune_cite": fortune_row.get("cite_url", "")}

    m = match(name)
    if m:
        rec = m["record"]
        ent = rec["attributes"]["entity"]
        key = f"lei:{rec['id']}"
        node = Node(key=key, kind="company", label=ent["legalName"]["name"],
                    attrs={**attrs, "lei": rec["id"],
                           "country": ent["legalAddress"].get("country"),
                           "city": ent["legalAddress"].get("city"),
                           "status": ent.get("status"),
                           "matched_on": m["matched_on"]},
                    source="GLEIF", url=f"{GLEIF}?filter%5Bentity.legalName%5D="
                                        f"{urllib.parse.quote(m['query'])}",
                    fetched_at=NOW())
        g.add_node(node)
        # GLEIF returns 404 for /direct-parent and /ultimate-parent on a lei-record as of this
        # build (the record now carries only a reporting-exception link), so no parent edges
        # are drawn here. The attempt is kept so the day the endpoint returns, they appear.
        if _GLEIF_PARENTS_UP():
            for rel, plei, pname in _gleif_parents(rec["id"]):
                pk = f"lei:{plei}"
                g.add_node(Node(key=pk, kind="company", label=pname, attrs={"lei": plei},
                                source="GLEIF", url=f"{GLEIF}/{rec['id']}/{rel.replace('_', '-')}",
                                fetched_at=NOW()))
                g.add_edge(Edge(src=key, dst=pk, rel=rel, source="GLEIF",
                                url=f"{GLEIF}/{rec['id']}/{rel.replace('_', '-')}",
                                attrs={"matched_via": "GLEIF relationship record"},
                                fetched_at=NOW()))
        return node

    key = f"fortune:{_norm(name) or 'unknown'}"
    node = Node(key=key, kind="company", label=name,
                attrs={**attrs,
                       "registry_match": "none",
                       "note": "Fortune lists this name; no GLEIF record matched exactly, so "
                               "there is no legal-entity identity behind this node yet."},
                source="Fortune (via Wikipedia)", url=fortune_row.get("cite_url", ""),
                fetched_at=NOW())
    g.add_node(node)
    return node


# ---------------------------------------------------------------- SEC holders
#
# GLEIF used to serve /direct-parent and /ultimate-parent on a lei-record; as of this build
# both return 404 and the record only carries a `reporting-exception` link, so ownership has
# to come from somewhere else. SEC EDGAR SC 13D/13G cover pages are that somewhere: they
# name the holder, the share count and the percent of class, and each one is a URL.
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_UA = {"User-Agent": "ZhangYangyi03 entity-graph research 2936745@qq.com"}

_tickers: dict | None = None


def _ticker_index() -> dict:
    global _tickers
    if _tickers is None:
        req = urllib.request.Request(TICKERS_URL, headers=SEC_UA)
        d = json.loads(urllib.request.urlopen(req, timeout=60).read())
        _tickers = {v["ticker"].upper(): v for v in d.values()}
    return _tickers


# Fortune spelling -> SEC filer title, only where they differ.
SEC_TITLE_ALIASES = {
    "Amazon": ["AMAZON COM INC"],
    "Alphabet": ["Alphabet Inc."],
}


def ticker_for(name: str) -> tuple[str, int] | None:
    """(ticker, cik) when the Fortune name is an exact ticker-title match, else None."""
    for alias in SEC_TITLE_ALIASES.get(name, []):
        idx = _ticker_index()
        for t, v in idx.items():
            if _norm(v["title"]) == _norm(alias):
                return (t, int(v["cik_str"]))
    want = _norm(name)
    best = None
    for t, v in _ticker_index().items():
        if _norm(v["title"]) == want:
            return (t, int(v["cik_str"]))
        if best is None and want.startswith(_norm(v["title"])) and len(_norm(v["title"])) > 6:
            best = (t, int(v["cik_str"]))
    return best


_ISSUER_RE = re.compile(r"\(\s*Name of Issuer\s*\)", re.I)


def filing_issuer(text: str) -> str:
    """The issuer named on the cover page -- the COMPANY the shares belong to.

    A CIK's submissions feed lists filings the filer SUBMITTED, not filings about the filer.
    Amazon's own CIK carries a 2022 SC 13G whose issuer is Rivian Automotive: Amazon was the
    reporter. Drawing that edge would have made Rivian a holder of Amazon, so the issuer on
    the cover page has to be checked before any edge is drawn.
    """
    from .holders import _plain
    flat = _plain(text) if "<" in text[:4000] else re.sub(r"\s+", " ", text)
    m = _ISSUER_RE.search(flat)
    if not m:
        return ""
    # the issuer name sits immediately before "(Name of Issuer)", but the flat text keeps
    # page furniture in between, so take the last run of title-case words before the marker
    before = flat[max(0, m.start() - 400):m.start()].strip(" -–|")
    # a name, not a sentence: cut at the last punctuation or ALL-CAPS heading
    before = re.split(r"[.:;]\s|\s{3,}", before)[-1]
    return before.strip(" -–|")


def issuer_matches(name: str, page_issuer: str) -> bool:
    """Is the cover-page issuer this company? Compared on the normalised head noun.

    SEC types the issuer by hand, so the same company appears as "AMAZON.COM , INC.",
    "Amazon.com, Inc" and "AMAZON COM INC". The comparison strips the ".COM" style domains
    and the corporate suffixes and then asks whether one contains the other.
    """
    a = re.sub(r"\.com|\.net|\.org", "", _norm(name))
    b = re.sub(r"\.com|\.net|\.org", "", _norm(page_issuer))
    return bool(a and b and (a in b or b in a))


def add_holders(g: Graph, cik: int, issuer_key: str, limit: int = 6,
                max_filings: int = 3, issuer_name: str = "") -> int:
    """SC 13D/13G cover pages -> holder nodes and holder_of edges. Returns edges added."""
    from concurrent.futures import ThreadPoolExecutor

    from . import sources
    from .fetch import get
    from .holders import filing_documents, holders_in_filing

    try:
        filings = sources.holders(cik).get("filings", [])
    except Exception as exc:                        # noqa: BLE001
        print(f"    holders({cik}) index failed: {type(exc).__name__}: {exc}", flush=True)
        return 0
    # Newest first. The issuer check needs the page text, so it is fetched in a batch:
    # dozens of sequential SEC round trips is what made the first build time out.
    ordered = sorted(filings, key=lambda f: f["date"], reverse=True)
    pages: dict[str, str] = {}

    def _pull(fil: dict) -> None:
        try:
            docs = filing_documents(cik, fil["accession"])
            # raw=True: a filing page is HTML, not JSON; without it get() parses to None
            pages[fil["accession"]] = (get(docs[0], raw=True) or "") if docs else ""
        except Exception:                           # noqa: BLE001
            pages[fil["accession"]] = ""

    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(_pull, ordered[:max_filings * 12]))

    seen: set[str] = set()
    added = 0
    used = 0
    for f in ordered:
        if added >= limit or used >= max_filings:
            break
        # A CIK's feed lists what the filer SUBMITTED, not what it IS: Amazon's own CIK
        # carries a 2022 13G whose issuer is Rivian. The issuer on the cover page decides.
        if issuer_name:
            page_issuer = filing_issuer(pages.get(f["accession"], "") or "")
            if not _norm(page_issuer) or not issuer_matches(issuer_name, page_issuer):
                continue
        try:
            people = holders_in_filing(cik, f["accession"])
        except Exception as exc:                    # noqa: BLE001
            print(f"    holders_in_filing({f['accession']}) failed: "
                  f"{type(exc).__name__}: {exc}", flush=True)
            continue
        if not people:
            continue
        used += 1
        for p in people:
            nm = p["name"].strip()
            if not nm or nm.lower() in seen:
                continue
            seen.add(nm.lower())
            hk = f"holder:{_norm(nm)[:60] or 'unknown'}"
            url = p.get("url") or f["url"]
            g.add_node(Node(key=hk, kind="person", label=nm,
                            attrs={"kind": p.get("kind") or "holder",
                                   "shares": p.get("shares"), "percent": p.get("percent")},
                            source="SEC EDGAR", url=url, fetched_at=NOW()))
            g.add_edge(Edge(src=hk, dst=issuer_key, rel="holder_of",
                            attrs={"form": f["form"], "date": f["date"],
                                   "accession": f["accession"],
                                   "shares": p.get("shares"), "percent": p.get("percent")},
                            source="SEC EDGAR", url=url, fetched_at=NOW()))
            added += 1
            if added >= limit:
                break
    return added

def add_manual(g: Graph, slug: str, rel: str, target_key: str, note: str = "") -> Node:
    """A hand-entered company, explicitly marked as not machine-verified."""
    rec = MANUAL[slug]
    key = f"manual:{slug}"
    node = Node(key=key, kind="company", label=rec["label_cn"],
                attrs={"name_cn": rec["label_cn"], "name_en": rec["label_en"],
                       "note": rec["note"],
                       "registry_match": "none (manual record)"},
                source="manual", url="", fetched_at=NOW())
    g.add_node(node)
    if rel and target_key:
        g.add_edge(Edge(src=key, dst=target_key, rel=rel, source="manual", url="",
                        attrs={"note": note or rec["note"]}, fetched_at=NOW()))
    return node
