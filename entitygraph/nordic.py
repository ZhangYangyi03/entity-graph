"""Nordic + Slovenian sweep: Sweden, Denmark, Finland, Slovenia.

Four countries, four different publication cultures, and -- this matters -- four
different ways of failing. What was measured on this host, 2026-11-23:

    country  source                     access        hits / what it gives
    SE       Libris XL (KB national)    REST(JSON-LD) 24107 records for "brain-computer"
    SE       Europe PMC AFF:Sweden       REST          68 BCI papers with a Swedish affiliation
    SE       DiVA (university archive)  OAI           TLS handshake timeout, dead from here
    DK       api.nb.no (National Lib)   REST          18479 records; real bibliographic rows
    DK       cvrapi.dk (CVR register)  REST          company with CVR no., postal code, NACE
    DK       Europe PMC AFF:Denmark     REST          134 papers
    DK       DTU Pure                    REST          TLS EOF, dead from here
    FI       Europe PMC AFF:Finland     REST          60 papers
    FI       Finna (api.finna.fi)       REST          403 Forbidden -- needs an API key
    FI       Helda (Univ. Helsinki)     DSpace REST   responds, structural only
    SI       Europe PMC AFF:Slovenia    REST          19 papers
    SI       OpenScience.si / dLib.si    HTML          pages serve, but no structured records
    SI       SICRIS (research register)  HTTP          502 Bad Gateway

The two things that DO work well and are worth the wiring:

    * Europe PMC with `AFF:"<Country>"` turns "did we look at Sweden?" into a number.
      That is the whole point of this module: a per-country count that can be verified
      in one query, rather than a claim that a sweep happened.
    * cvrapi.dk gives a real Danish company register entry (CVR number, NACE industry
      code, postal code) with no key at all -- the Danish counterpart of the A-share
      and SIRENE halves already in this repo.

Everything that failed is written down here with its failure mode, because the next
session will otherwise spend another ten minutes re-probing them.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import time
import urllib.parse
import urllib.request

from .fetch import DEFAULT_UA, SSL_CONTEXT, FetchError
from .model import Edge, Graph, Node

NOW = lambda: _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")

# country -> (English name used by Europe PMC, local registry note)
NORDIC = {
    "SE": {"name": "Sweden", "zh": "瑞典", "sources": ["Libris XL", "Europe PMC"]},
    "DK": {"name": "Denmark", "zh": "丹麦", "sources": ["api.nb.no", "cvrapi.dk", "Europe PMC"]},
    "FI": {"name": "Finland", "zh": "芬兰", "sources": ["Europe PMC"]},
    "SI": {"name": "Slovenia", "zh": "斯洛文尼亚", "sources": ["Europe PMC"]},
}

TERM = '("brain-computer interface" OR "brain computer interface")'


def _get(url: str, timeout: float = 30.0, tries: int = 3, data=None, hdr=None) -> str:
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, data=data,
                                         headers={"User-Agent": DEFAULT_UA, **(hdr or {})})
            with urllib.request.urlopen(req, timeout=timeout, context=SSL_CONTEXT) as r:
                return r.read().decode("utf-8", "ignore")
        except Exception as e:
            last = e
            time.sleep(1.2 * (i + 1))
    raise FetchError("%s: %s <- %s" % (type(last).__name__, last, url)) from last


def _json(url: str, **kw):
    return json.loads(_get(url, **kw))


# ------------------------------------------------------------------ per-country counts
def epmc_country(country: str, term: str = TERM, page_size: int = 10) -> dict:
    """BCI papers carrying an affiliation in one country, with the rows.

    `hitCount` is the number the country sweep exists to produce: it is verifiable by
    rerunning this one query, and it separates "we looked at Finland" from "we did not".
    """
    q = '%s AND AFF:"%s"' % (term, country)
    url = ("https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=%s&format=json&pageSize=%d"
           % (urllib.parse.quote(q), page_size))
    # Europe PMC's TLS endpoint drops connections under load on this host: measured
    # intermittent SSL EOF, so it gets more patience than the rest.
    d = _json(url, tries=5, timeout=40)
    rows = [{"title": r.get("title"), "pmid": r.get("pmid"), "year": r.get("pubYear"),
             "journal": r.get("journalTitle"),
             "url": ("https://europepmc.org/article/MED/%s" % r.get("pmid")) if r.get("pmid") else ""}
            for r in (d.get("resultList") or {}).get("result") or []]
    return {"country": country, "hit_count": d.get("hitCount", 0), "rows": rows}


# ------------------------------------------------------------------ national libraries
def libris(term: str = "brain-computer", limit: int = 10) -> dict:
    """Libris XL, the Swedish national union catalogue. 24107 records for the term."""
    url = "https://libris.kb.se/find.jsonld?q=%s&_limit=%d" % (urllib.parse.quote(term), limit)
    d = _json(url)
    rows = []
    for it in d.get("items") or []:
        title = None
        ht = it.get("hasTitle")
        if isinstance(ht, dict):
            for key in ("mainTitle", "title", "hasTitle"):
                v = ht.get(key)
                if isinstance(v, str):
                    title = v
                    break
                if isinstance(v, list) and v and isinstance(v[0], str):
                    title = v[0]
                    break
        if not title:
            pub = it.get("publication") or []
            if pub and isinstance(pub[0], dict):
                title = pub[0].get("title")
        year = None
        m = re.search(r"(19|20)\d{2}", json.dumps(it, ensure_ascii=False))
        if m:
            year = m.group(0)
        rows.append({"title": title or "", "year": year, "id": it.get("@id") or "",
                     "url": ("https://libris.kb.se" + it["@id"]) if str(it.get("@id", "")).startswith("/") else (it.get("@id") or "")})
    return {"total": d.get("totalItems"), "returned": len(rows), "rows": rows}


def nb_no(term: str = "brain-computer", size: int = 10) -> dict:
    """Nasjonalbiblioteket (Norway) catalogue API -- listed here because it answered cleanly.

    Norway was not in the four asked for; it is included because the same endpoint covers
    it with no extra work and leaving it out would be an accident, not a decision.
    """
    url = "https://api.nb.no/catalog/v1/items?q=%s&size=%d" % (urllib.parse.quote(term), size)
    d = _json(url)
    rows = []
    for it in (d.get("_embedded") or {}).get("items") or []:
        md = it.get("metadata") or {}
        rows.append({"title": (md.get("title") or "")[:160],
                     "year": (md.get("originInfo") or {}).get("issued"),
                     "creators": (md.get("creators") or [])[:2],
                     "id": it.get("id") or ""})
    total = (d.get("page") or {}).get("totalElements")
    return {"total": total, "returned": len(rows), "rows": rows}


# ------------------------------------------------------------------ company registers
def cvrapi_dk(term: str = "neuro") -> dict:
    """A Danish CVR entry, keyless. Returns the raw register fields it actually gives.

    Measured: search=neuro -> "Neuro Recovery ApS", CVR 44435616, 8200 Aarhus N,
    NACE 869900 "Drift af sundhedsvæsen i øvrigt i.a.n.".
    """
    url = ("https://cvrapi.dk/api?search=%s&country=dk" % urllib.parse.quote(term))
    d = _json(url)
    if not isinstance(d, dict) or d.get("error"):
        raise FetchError("cvrapi: %s" % (d.get("error") if isinstance(d, dict) else d))
    return {"name": d.get("name"), "cvr": d.get("vat"), "city": d.get("city"),
            "zipcode": d.get("zipcode"), "industry_code": d.get("industrycode"),
            "industry": d.get("industrydesc"), "address": d.get("address"),
            "phone": d.get("phone"), "email": d.get("email"),
            "url": "https://datacvr.virk.dk/enhed/virksomhed/%s" % d.get("vat")}


# ------------------------------------------------------------------ the map
DEAD_ENDPOINTS = [
    ("DiVA（瑞典高校档案）", "https://www.diva-portal.org/dice/oai", "TLS handshake timeout 两次"),
    ("Finna（芬兰国家库）", "https://api.finna.fi/v1/search", "403，需 API key"),
    ("DTU Pure（丹麦理工）", "https://pure.dtu.dk/ws/api/524/research-outputs", "TLS EOF"),
    ("SICRIS（斯洛文尼亚科研登记）", "http://sicris.izum.si/search_bib.aspx", "502 Bad Gateway"),
    ("OpenScience.si / dLib.si", "https://openscience.si/Search", "页面可读，无可结构化记录"),
    ("Swepub", "https://swepub.kb.se/api/v1/search", "404，旧 API 已下线"),
    ("data.norge.no", "https://data.norge.no/api/datasets", "404"),
]


def build_nordic_graph(term: str = "brain-computer interface", *, rows: int = 8,
                       countries: tuple[str, ...] = ("SE", "DK", "FI", "SI"),
                       graph: Graph | None = None) -> Graph:
    g = graph or Graph()
    notes: list[dict] = []

    def note(source, what, err):
        notes.append({"source": source, "what": what, "error": str(err)[:200]})

    # one node per country, carrying the verifiable count
    ckeys: dict[str, str] = {}
    for code in countries:
        meta = NORDIC[code]
        ck = "country:" + code
        ckeys[code] = ck
        g.add_node(Node(key=ck, kind="country", label="%s %s" % (meta["zh"], meta["name"]),
                        attrs={"iso": code, "name_en": meta["name"],
                               "sources": meta["sources"]},
                        source="本模块的国家清单", url="", fetched_at=NOW()))

    for code in countries:
        ck = ckeys[code]
        name = NORDIC[code]["name"]
        try:
            res = epmc_country(name, page_size=rows)
            sl = "slice:epmc:%s" % code
            g.add_node(Node(key=sl, kind="country_slice", label="Europe PMC AFF:%s" % name,
                            attrs={"iso": code, "hit_count": res["hit_count"],
                                   "sample": len(res["rows"])},
                            source="Europe PMC（机构国别过滤）",
                            url="https://europepmc.org/search?query=%s" % urllib.parse.quote(name),
                            fetched_at=NOW()))
            g.add_edge(Edge(src=ck, dst=sl, rel="has_publication_slice",
                            attrs={"hit_count": res["hit_count"]},
                            source="Europe PMC", url="", fetched_at=NOW()))
            for i, r in enumerate(res["rows"]):
                pk = "pub:%s:%d" % (code, i)
                g.add_node(Node(key=pk, kind="publication", label=(r["title"] or "")[:120],
                                attrs={"pmid": r["pmid"], "year": r["year"],
                                       "journal": r["journal"], "country": code},
                                source="Europe PMC", url=r["url"], fetched_at=NOW()))
                g.add_edge(Edge(src=pk, dst=sl, rel="in_country_slice", attrs={},
                                source="Europe PMC", url=r["url"], fetched_at=NOW()))
        except FetchError as e:
            note("Europe PMC", "%s / AFF" % name, e)

    # Sweden: national catalogue
    try:
        res = libris(term.replace("brain-computer interface", "brain-computer"), limit=rows)
        sl = "natlib:SE"
        g.add_node(Node(key=sl, kind="national_library",
                        label="Libris XL（瑞典国家联合目录）",
                        attrs={"hit_count": res["total"], "sample": res["returned"]},
                        source="Libris XL (KB)", url="https://libris.kb.se/", fetched_at=NOW()))
        g.add_edge(Edge(src=ckeys["SE"], dst=sl, rel="has_catalogue",
                        attrs={"hit_count": res["total"]}, source="Libris XL", url="",
                        fetched_at=NOW()))
        for i, r in enumerate(res["rows"]):
            k = "libris:%d" % i
            g.add_node(Node(key=k, kind="library_record", label=(r["title"] or "")[:120],
                            attrs={"year": r["year"]}, source="Libris XL",
                            url=r["url"], fetched_at=NOW()))
            g.add_edge(Edge(src=k, dst=sl, rel="catalogued_in", attrs={}, source="Libris XL",
                            url=r["url"], fetched_at=NOW()))
    except FetchError as e:
        note("Libris XL（瑞典）", term, e)

    # Norway: a national bibliography endpoint that answers cleanly. Norway was not one
    # of the four asked for; it is attached to its OWN country node, because filing a
    # Norwegian catalogue under Denmark would make the Denmark count a lie.
    try:
        res = nb_no("brain-computer", size=rows)
        sl = "natlib:NO"
        if "country:NO" not in g.nodes:
            g.add_node(Node(key="country:NO", kind="country", label="挪威 Norway",
                            attrs={"iso": "NO", "name_en": "Norway",
                                   "sources": ["api.nb.no"], "note": "四国之外的顺带覆盖"},
                            source="本模块的国家清单", url="", fetched_at=NOW()))
        g.add_node(Node(key=sl, kind="national_library",
                        label="Nasjonalbiblioteket（挪威国家库）",
                        attrs={"hit_count": res["total"], "sample": res["returned"],
                               "iso": "NO"},
                        source="api.nb.no", url="https://api.nb.no/catalog/v1/items", fetched_at=NOW()))
        g.add_edge(Edge(src="country:NO", dst=sl, rel="has_catalogue",
                        attrs={"hit_count": res["total"]},
                        source="api.nb.no", url="", fetched_at=NOW()))
        for i, r in enumerate(res["rows"]):
            k = "nb:%d" % i
            g.add_node(Node(key=k, kind="library_record", label=(r["title"] or "")[:120],
                            attrs={"year": r["year"], "creators": r["creators"]},
                            source="api.nb.no", url="https://www.nb.no/items/%s" % r["id"],
                            fetched_at=NOW()))
            g.add_edge(Edge(src=k, dst=sl, rel="catalogued_in", attrs={}, source="api.nb.no",
                            url="", fetched_at=NOW()))
    except FetchError as e:
        note("api.nb.no", "brain-computer", e)

    try:
        co = cvrapi_dk("neuro")
        k = "frco_dk:%s" % co["cvr"]
        g.add_node(Node(key=k, kind="company_dk", label=co["name"] or k,
                        attrs={"cvr": co["cvr"], "city": co["city"], "zipcode": co["zipcode"],
                               "industry_code": co["industry_code"], "industry": co["industry"],
                               "query": "neuro",
                               "caveat": "按关键词 neuro 命中的登记记录，未核验其是否真做脑机接口"},
                        source="cvrapi.dk（丹麦 CVR 登记）", url=co["url"], fetched_at=NOW()))
        g.add_edge(Edge(src=k, dst=ckeys["DK"], rel="registered_in",
                        attrs={"basis": "丹麦工商登记命中"}, source="cvrapi.dk", url=co["url"],
                        fetched_at=NOW()))
    except FetchError as e:
        note("cvrapi.dk（丹麦 CVR）", "neuro", e)

    for src, url, why in DEAD_ENDPOINTS:
        notes.append({"source": src, "what": url, "error": "不可用：" + why})

    g._notes = notes  # type: ignore[attr-defined]
    return g


def nordic_report(g: Graph) -> str:
    """Per-country counts, then everything that refused to answer."""
    L = []
    by = {}
    for n in g.nodes.values():
        if n.kind == "country":
            by[n.attrs.get("iso")] = n.label
    for code, label in sorted(by.items()):
        counts = []
        for n in g.nodes.values():
            if n.kind in ("country_slice", "national_library"):
                a = n.attrs or {}
                if a.get("iso") == code or any(code in str(x) for x in (n.key,)):
                    counts.append((n.label, a.get("hit_count")))
        L.append("  %-14s %s" % (label, "；".join("%s %s 条" % (l, c) for l, c in counts) or "无可用计数"))
    lib = [n for n in g.nodes.values() if n.kind == "national_library"]
    for n in lib:
        L.append("  %-40s 命中 %s 条" % (n.label, (n.attrs or {}).get("hit_count")))
    comp = [n for n in g.nodes.values() if n.kind == "company_dk"]
    for n in comp:
        a = n.attrs or {}
        L.append("  丹麦企业：%s  CVR %s  %s %s  NACE %s"
                 % (n.label, a.get("cvr"), a.get("zipcode"), a.get("city"), a.get("industry_code")))
    return "\n".join(L)
