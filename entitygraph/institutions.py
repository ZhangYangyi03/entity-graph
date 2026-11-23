"""Institutions and cross-border trials: the layer that replaces "country name in a string".

Why this module exists
----------------------
Every country number so far is a FLOOR, because it came from matching a country NAME inside
free-text affiliation blocks. The three ways to stop guessing, in order of how much they
cost:

  1. ClinicalTrials.gov v2 -- a trial record states its location countries as a FIELD, and
     its lead sponsor as a FIELD. Nothing is inferred from prose. 275 BCI trials measured,
     33 countries, 3 trials that genuinely span more than one country.
  2. ROR (Research Organization Registry) -- a free-text institution name resolves to one
     registry ID with a country and a type, so "Karolinska Institutet" becomes
     https://ror.org/056d84691 / Sweden / education+funder.
  3. OpenAlex institution IDs would be the sharpest of all, but api.openalex.org returns
     429 on every attempt from this host (measured again 2026-11-23, with and without the
     polite-pool mailto). Not used. A number built on whatever attempt happened to survive
     is worse than a documented gap.

A warning this module enforces rather than documents
---------------------------------------------------
ROR's fuzzy search returns a confident-looking top hit that is sometimes simply wrong:
"BrainQ Technologies" resolves to ARIA Technologies (France) -- a different company in a
different country. So a match is accepted only when the names agree structurally (equal
after normalisation, or one contains the other, or token overlap >= 0.7) and the rule that
fired is stored on the node. Everything else lands in `unmatched` with the reason, where it
can be seen.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from .fetch import DEFAULT_UA, SSL_CONTEXT, FetchError
from .model import Edge, Graph, Node

NOW = lambda: _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".cache")
ROR_CACHE = os.path.join(CACHE_DIR, "ror_resolved.json")

CT_FIELDS = ("NCTId,BriefTitle,LocationCountry,LeadSponsorName,LeadSponsorClass,"
             "StartDate,OverallStatus")
CT_BASE = "https://clinicaltrials.gov/api/v2/studies"

LEGAL_SUFFIX = re.compile(r"\b(inc|llc|ltd|limited|ab|as|aps|gmbh|ag|sa|sas|srl|bv|nv|oy|"
                          r"plc|corp|corporation|company|co|group|holding|holdings)\b\.?", re.I)


def _get(url: str, timeout: float = 60.0, tries: int = 3, sleep: float = 1.5,
         accept: str | None = None) -> str:
    last = None
    for i in range(tries):
        try:
            hdr = {"User-Agent": DEFAULT_UA}
            if accept:
                hdr["Accept"] = accept
            with urllib.request.urlopen(urllib.request.Request(url, headers=hdr),
                                        timeout=timeout, context=SSL_CONTEXT) as r:
                return r.read().decode("utf-8", "ignore")
        except Exception as e:
            last = e
            time.sleep(sleep * (i + 1))
    raise FetchError("%s: %s <- %s" % (type(last).__name__, last, url)) from last


def _json(url: str, **kw):
    return json.loads(_get(url, **kw))


def _load(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


# ------------------------------------------------------------------ trials
def ctgov_bci(term: str = '"brain-computer interface"', max_pages: int = 8) -> list[dict]:
    """Every ClinicalTrials.gov study matching the term, with its location FIELD and sponsor.

    Measured 2026-11-23: 275 studies over 2 pages; 33 countries; 3 multi-country trials.
    The counts here are totals for the query, not floors -- the record states its countries.
    """
    studies: list[dict] = []
    token = None
    for _ in range(max_pages):
        url = ("%s?query.term=%s&pageSize=200&fields=%s"
               % (CT_BASE, urllib.parse.quote(term), CT_FIELDS))
        if token:
            url += "&pageToken=" + urllib.parse.quote(token)
        d = _json(url)
        studies += d.get("studies") or []
        token = d.get("nextPageToken")
        if not token:
            break
    out = []
    for s in studies:
        ps = s.get("protocolSection") or {}
        locs = [l.get("country") for l in
                ((ps.get("contactsLocationsModule") or {}).get("locations") or [])
                if l.get("country")]
        sp = (ps.get("sponsorCollaboratorsModule") or {}).get("leadSponsor") or {}
        start = (ps.get("statusModule") or {}).get("startDateStruct") or {}
        out.append({
            "nct": (ps.get("identificationModule") or {}).get("nctId"),
            "title": (ps.get("identificationModule") or {}).get("briefTitle"),
            "countries": sorted(set(locs)),
            "sponsor": sp.get("name"),
            "sponsor_class": sp.get("class"),
            "start": start.get("date"),
            "status": (ps.get("statusModule") or {}).get("overallStatus"),
        })
    return out


# ------------------------------------------------------------------ institutions
def _norm(name: str) -> str:
    s = (name or "").lower()
    s = LEGAL_SUFFIX.sub(" ", s)
    s = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", s)
    return " ".join(s.split())


CONNECTOR = {"of", "the", "for", "and", "de", "di", "da", "van", "von", "del", "la", "le",
             "du", "des", "el", "al", "at", "in", "on"}


def _core(name: str) -> set[str]:
    return {t for t in _norm(name).split() if t not in CONNECTOR}


def match_rule(query: str, candidate: str) -> tuple[str, float] | None:
    """Accept a ROR candidate only on structural evidence; return (rule, score) or None.

    Two rules, both strict, because a fuzzy top hit is not a match. Measured failures this
    guards against:
        "BrainQ Technologies"  -> ARIA Technologies (France)   different company
        "Axoft, Inc."          -> Instituto Nacional de Cancerología
        "National University Hospital, Singapore" -> National University of Singapore
                                 (a hospital and a university: related, not the same entity)
    Measured case the rule must still accept:
        "University of Aarhus" -> Aarhus University            same institution, word order
                                                               and the connector "of" differ

    So: equal token sets after dropping connectors -> match. Nothing else. A Jaccard
    threshold was tried first and let NUH/NUS through at 0.75, which is a false match of
    exactly the kind this function exists to prevent.
    """
    q, c = _core(query), _core(candidate)
    if not q or not c:
        return None
    if q == c:
        if _norm(query) == _norm(candidate):
            return ("完全相同（归一化后）", 1.0)
        return ("词集合相同（仅连接词/词序不同）", 1.0)
    return None


def ror_resolve(name: str, cache: dict | None = None) -> dict:
    """Free-text institution -> ROR id + country + type, or an explicit non-match."""
    c = cache if cache is not None else _load(ROR_CACHE)
    key = "ror:" + (name or "")
    if key in c:
        return c[key]
    res = {"query": name, "matched": False, "reason": None, "ror": None, "name": None,
           "country": None, "types": None, "rule": None, "score": None,
           "candidates": 0, "top_hit": None}
    try:
        d = _json("https://api.ror.org/organizations?query=" + urllib.parse.quote(name or ""))
    except FetchError as e:
        res["reason"] = "ROR 查询失败：%s" % str(e)[:120]
        c[key] = res
        if cache is None:
            _save(ROR_CACHE, c)
        return res
    items = d.get("items") or []
    res["candidates"] = d.get("number_of_results")
    if not items:
        res["reason"] = "ROR 无候选"
        c[key] = res
        if cache is None:
            _save(ROR_CACHE, c)
        return res
    top = items[0]
    names = top.get("names") or []
    display = next((n["value"] for n in names if "ror_display" in (n.get("types") or [])), None)
    if not display and names:
        display = names[0].get("value")
    res["top_hit"] = display
    hit = match_rule(name, display or "")
    if not hit:
        res["reason"] = "首位命中「%s」结构不符，判为不匹配（不做模糊认领）" % (display,)
        c[key] = res
        if cache is None:
            _save(ROR_CACHE, c)
        return res
    res.update({"matched": True, "ror": top.get("id"), "name": display,
                "country": ((top.get("locations") or [{}])[0].get("geonames_details") or {}
                            ).get("country_name"),
                "types": top.get("types"), "rule": hit[0], "score": hit[1]})
    c[key] = res
    if cache is None:
        _save(ROR_CACHE, c)
    return res


def epmc_year_series(term: str = '("brain-computer interface" OR "brain computer interface")',
                     years: range = range(2010, 2026)) -> dict[int, int]:
    """Papers per year. A trend is worth more than any single count, and this costs 16 queries.

    Europe PMC takes YEAR:<n> in the query, so each point is the same kind of measurement as
    the country counts -- a floor, and labelled as one.
    """
    out = {}
    for y in years:
        q = "%s AND (FIRST_PDATE:[%d-01-01 TO %d-12-31])" % (term, y, y)
        url = ("https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=%s"
               "&format=json&pageSize=1&resultType=lite" % urllib.parse.quote(q))
        try:
            out[y] = _json(url, tries=3, sleep=2.0).get("hitCount")
        except FetchError:
            out[y] = None
    return out


# ------------------------------------------------------------------ the graph
def topic_lookup(term: str) -> list[dict]:
    """Records matching a looser phrase, so the year series aggregates the whole field.

    Measured: `"brain-computer interface"` alone yields 1724 papers in 2025 and misses
    "brain-computer interfaces" (plural), "BCI", and "brain-machine interface". The series
    below uses the loose term; the trial count keeps the exact phrase. Mixing the two
    silently would be the kind of number that looks precise and is not.
    """
    d = _json("https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=%s"
              "&format=json&pageSize=1&resultType=lite" % urllib.parse.quote(term))
    return d.get("hitCount")


def build_institution_graph(term: str = '"brain-computer interface"', *,
                            resolve_workers: int = 6, graph: Graph | None = None) -> Graph:
    g = graph or Graph()
    notes: list[dict] = []

    def note(source, what, err):
        notes.append({"source": source, "what": what, "error": str(err)[:200]})

    try:
        trials = ctgov_bci(term)
    except FetchError as e:
        note("ClinicalTrials.gov v2", term, e)
        trials = []
    if not trials:
        note("ClinicalTrials.gov v2", term, "0 条（或取数失败）——不再往下编造试验")
    notes.append({"source": "ClinicalTrials.gov v2", "what": term,
                  "error": "共 %d 条试验；国别与申办方是字段，不是从文本里猜的" % len(trials)})

    # country nodes, reused by key so this graph can be merged with the others
    def country(iso2, label):
        k = "country:" + iso2 if iso2 else "country:??"
        if k not in g.nodes:
            g.add_node(Node(key=k, kind="country", label=label, attrs={"iso2": iso2},
                            source="ClinicalTrials.gov v2", url="", fetched_at=NOW()))
        return k

    # trials + locations + sponsors
    rcache = _load(ROR_CACHE)
    sponsor_names = sorted({t["sponsor"] for t in trials if t["sponsor"]})
    resolved: dict[str, dict] = {}
    with ThreadPoolExecutor(resolve_workers) as ex:
        for nm, res in zip(sponsor_names, ex.map(lambda n: ror_resolve(n, cache=rcache),
                                                 sponsor_names)):
            resolved[nm] = res
    _save(ROR_CACHE, rcache)
    matched = sum(1 for r in resolved.values() if r["matched"])
    notes.append({"source": "ROR", "what": "%d 个申办方名称" % len(sponsor_names),
                  "error": "结构化匹配成功 %d，显式不匹配 %d（不匹配的照样留节点，标 unmatched）"
                           % (matched, len(resolved) - matched)})

    for t in trials:
        k = "trial:" + (t["nct"] or "")
        g.add_node(Node(key=k, kind="trial", label=(t["title"] or t["nct"] or "")[:150],
                        attrs={"nct": t["nct"], "start": t["start"], "status": t["status"],
                               "countries": t["countries"], "sponsor": t["sponsor"],
                               "sponsor_class": t["sponsor_class"]},
                        source="ClinicalTrials.gov v2",
                        url="https://clinicaltrials.gov/study/%s" % t["nct"], fetched_at=NOW()))
        for cname in t["countries"]:
            iso2 = COUNTRY_ISO2.get(cname)
            ck = country(iso2, cname)
            g.add_edge(Edge(src=k, dst=ck, rel="trial_located_in", attrs={},
                            source="ClinicalTrials.gov v2",
                            url="https://clinicaltrials.gov/study/%s" % t["nct"],
                            fetched_at=NOW()))
        if t["sponsor"]:
            r = resolved.get(t["sponsor"]) or {}
            sk = "inst:" + (r.get("ror") or ("unmatched:" + _norm(t["sponsor"])[:60]))
            if sk not in g.nodes:
                if r.get("matched"):
                    g.add_node(Node(key=sk, kind="institution",
                                    label=r["name"] or t["sponsor"],
                                    attrs={"ror": r["ror"], "country": r["country"],
                                           "types": r["types"], "match_rule": r["rule"],
                                           "match_score": r["score"],
                                           "query": t["sponsor"]},
                                    source="ROR（结构化匹配）", url=r["ror"], fetched_at=NOW()))
                else:
                    g.add_node(Node(key=sk, kind="institution",
                                    label=t["sponsor"],
                                    attrs={"ror": None, "match_status": "unmatched",
                                           "reason": r.get("reason"),
                                           "top_hit": r.get("top_hit")},
                                    source="ClinicalTrials.gov v2（ROR 未匹配）",
                                    url="", fetched_at=NOW()))
            g.add_edge(Edge(src=k, dst=sk, rel="trial_sponsored_by", attrs={},
                            source="ClinicalTrials.gov v2", url="", fetched_at=NOW()))
            # an institution sits in a country only when ROR said so
            if r.get("matched") and r.get("country"):
                iso2 = COUNTRY_ISO2.get(r["country"])
                ck = country(iso2, r["country"])
                g.add_edge(Edge(src=sk, dst=ck, rel="institution_located_in", attrs={},
                                source="ROR", url=r.get("ror") or "", fetched_at=NOW()))

    # cross-border trials: a real country-to-country relation, measured by field
    xb = {}
    for t in trials:
        cc = [c for c in t["countries"] if COUNTRY_ISO2.get(c)]
        if len(cc) < 2:
            continue
        for i, a in enumerate(cc):
            for b in cc[i + 1:]:
                key = tuple(sorted((a, b)))
                xb.setdefault(key, []).append(t["nct"])
    for (a, b), ncts in xb.items():
        g.add_edge(Edge(src=country(COUNTRY_ISO2[a], a), dst=country(COUNTRY_ISO2[b], b),
                        rel="trial_spans_country",
                        attrs={"trials": len(ncts), "ncts": ncts,
                               "basis": "同一条试验的地点国字段同时含两国"},
                        source="ClinicalTrials.gov v2", url="", fetched_at=NOW()))

    # year series -- loose term on purpose, and both terms' 2025 numbers are recorded so
    # the difference between "exact phrase" and "the field" is visible rather than implied
    SERIES_TERM = '("brain-computer interface" OR "brain computer interface" OR "BCI" OR "brain-machine interface")'
    try:
        exact = topic_lookup(term)
        loose = topic_lookup(SERIES_TERM)
        g.add_node(Node(key="concept:BCI", kind="concept", label="脑机接口 Brain-Computer Interface",
                        attrs={"qid": "Q897410", "exact_phrase_hits": exact,
                               "tolerant_hits": loose,
                               "series_term": SERIES_TERM,
                               "note": "年度序列用宽口径词；精确短语与宽口径的差距记在这里"},
                        source="Wikidata + Europe PMC",
                        url="https://www.wikidata.org/wiki/Q897410", fetched_at=NOW()))
        series = epmc_year_series(SERIES_TERM)
        for y, n in series.items():
            if n is None:
                continue
            g.add_node(Node(key="year:%d" % y, kind="year_bucket", label=str(y),
                            attrs={"year": y, "papers": n, "series_term": SERIES_TERM,
                                   "unit": "宽口径计数（含 BCI 缩写与复数），非精确短语"},
                            source="Europe PMC", url="", fetched_at=NOW()))
            g.add_edge(Edge(src="concept:BCI", dst="year:%d" % y, rel="has_year_bucket",
                            attrs={"papers": n}, source="Europe PMC", url="",
                            fetched_at=NOW()))
    except Exception as e:
        note("Europe PMC 年度序列", "2010-2025", "%s: %s" % (type(e).__name__, e))

    g._notes = notes  # type: ignore[attr-defined]
    return g


# Trial location uses English country names; mapping only the ones that appear, plus the
# aliases ClinicalTrials.gov actually emits. Everything unmapped stays a country:?? node so
# a gap is visible instead of being silently dropped.
COUNTRY_ISO2 = {
    "United States": "US", "China": "CN", "Singapore": "SG", "France": "FR",
    "Switzerland": "CH", "Canada": "CA", "United Kingdom": "GB", "Italy": "IT",
    "Taiwan": "TW", "Germany": "DE", "Russia": "RU", "Portugal": "PT", "Spain": "ES",
    "Netherlands": "NL", "Belgium": "BE", "Austria": "AT", "Sweden": "SE",
    "Denmark": "DK", "Norway": "NO", "Finland": "FI", "Poland": "PL", "Israel": "IL",
    "Japan": "JP", "South Korea": "KR", "Korea, Republic of": "KR", "India": "IN",
    "Brazil": "BR", "Australia": "AU", "Turkey": "TR", "Iran": "IR", "Egypt": "EG",
    "Greece": "GR", "Czechia": "CZ", "Czech Republic": "CZ", "Hungary": "HU",
    "Ireland": "IE", "Mexico": "MX", "Argentina": "AR", "Chile": "CL", "Colombia": "CO",
    "Malaysia": "MY", "Thailand": "TH", "Vietnam": "VN", "Philippines": "PH",
    "Indonesia": "ID", "Pakistan": "PK", "Saudi Arabia": "SA", "United Arab Emirates": "AE",
    "Qatar": "QA", "Kuwait": "KW", "Jordan": "JO", "Lebanon": "LB", "Iraq": "IQ",
    "Nigeria": "NG", "Kenya": "KE", "South Africa": "ZA", "Ghana": "GH", "Uganda": "UG",
    "Tanzania": "TZ", "Ethiopia": "ET", "New Zealand": "NZ", "Slovenia": "SI",
    "Croatia": "HR", "Serbia": "RS", "Bulgaria": "BG", "Romania": "RO", "Ukraine": "UA",
    "Slovakia": "SK", "Lithuania": "LT", "Latvia": "LV", "Estonia": "EE", "Iceland": "IS",
    "Luxembourg": "LU", "Peru": "PE", "Uruguay": "UY", "Venezuela": "VE", "Cuba": "CU",
    "Bangladesh": "BD", "Sri Lanka": "LK", "Nepal": "NP", "Kazakhstan": "KZ",
    "Georgia": "GE", "Armenia": "AM", "Azerbaijan": "AZ", "Mongolia": "MN",
}


def institution_report(g: Graph) -> str:
    trials = [n for n in g.nodes.values() if n.kind == "trial"]
    inst = [n for n in g.nodes.values() if n.kind == "institution"]
    matched = [n for n in inst if (n.attrs or {}).get("ror")]
    xb = [e for e in g.edges.values() if e.rel == "trial_spans_country"]
    from collections import Counter
    cc = Counter()
    for n in trials:
        for c in (n.attrs or {}).get("countries") or []:
            cc[c] += 1
    L = ["  试验 %d 条；涉及国家 %d 个；跨国试验边 %d 条"
         % (len(trials), len(cc), len(xb)),
         "  申办机构 %d 个，其中 ROR 结构化匹配 %d，显式不匹配 %d"
         % (len(inst), len(matched), len(inst) - len(matched)),
         "  试验最多的国家：" + "、".join("%s %d" % (c, n) for c, n in cc.most_common(10))]
    if xb:
        L.append("  跨国试验（字段级证据）：")
        for e in sorted(xb, key=lambda x: -(x.attrs.get("trials") or 0)):
            L.append("    %-14s -- %-14s %d 条 %s"
                     % (e.src.split(":")[-1], e.dst.split(":")[-1],
                        e.attrs.get("trials") or 0, ",".join(e.attrs.get("ncts") or [])))
    bad = [n for n in inst if not (n.attrs or {}).get("ror")]
    if bad:
        L.append("  ROR 未认领的机构（保留原名，标 unmatched，不猜归属）：")
        for n in bad[:8]:
            L.append("    %-46s 首位命中「%s」结构不符"
                     % ((n.label or "")[:46], (n.attrs or {}).get("top_hit") or "无"))
    yrs = sorted([n for n in g.nodes.values() if n.kind == "year_bucket"],
                 key=lambda n: n.attrs["year"])
    if yrs:
        L.append("  年度论文数（下限）：")
        L.append("    " + "  ".join("%s:%s" % (n.attrs["year"], n.attrs["papers"]) for n in yrs))
    return "\n".join(L)
