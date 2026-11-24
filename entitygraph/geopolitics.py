"""Commercial, political and financial relations between countries.

Academic co-authorship was the only country-to-country relation the graph had. Three more
layers, each from a source that states the relation as a FIELD, plus a measured note on what
could not be reached.

  commercial   UN Comtrade public preview API. A customs declaration names the partner
               country as a code, so `exports_to` with a USD value is a record, not an
               inference. 200+ reporters, one request each, cached per reporter-year.
  political    Wikidata P530 (diplomatic relations) between sovereign states, 7307 triples,
               paired with P2936 (language of work) and the start time. Free, no key,
               SPARQL endpoint reachable from this host.
  political    UN General Assembly ideal points (Voeten/UN voting, Harvard Dataverse,
               doi:10.7910/DVN/LEJUQZ). Two states whose ideal points are close vote alike;
               that is measured agreement, not an alliance claim.
  financial    Country ETF daily returns over one year, pairwise Pearson correlation. This
               is market co-movement. It is NOT capital flow, and it is labelled as
               co-movement everywhere it appears.

What was tried and refused, so the gap is on the record instead of being papered over
------------------------------------------------------------------------------
  BIS locational banking statistics   api.bis.org returns 406 on v1 and 404 on v2, with and
                                      without an SDMX Accept header. Blocked, not skipped.
  IMF CDIS / CPIS (bilateral FDI and portfolio holdings -- the ideal source for a financial
                                      layer)  dataservices.imf.org fails TLS (UNEXPECTED_EOF);
                                      the new api.imf.org/external/sdmx/2.1 answers the
                                      dataflow listing but every data query returns 500.
  OECD FDI flows                      sdmx.oecd.org returns 406 for every structure and data
                                      request tried. Blocked by content negotiation.
  GDELT event/tone data               429 on every attempt from this host.

A blocked source is written into the report as blocked. A number that came from whatever
attempt happened to survive is worse than a hole, because the hole is visible.
"""
from __future__ import annotations

import datetime as _dt
import io
import json
import math
import os
import re
import urllib.parse
import re
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

from .fetch import DEFAULT_UA, SSL_CONTEXT, FetchError
from .model import Edge, Graph, Node

NOW = lambda: _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(ROOT, ".cache")

TRADE_YEAR = 2024
# The theme, not all merchandise. HS codes chosen because they are what a brain-computer
# interface and a biopharma product actually cross a border as:
#   9018  electro-medical instruments (EEG/EMG/EMG-driven prosthetics live here)
#   9021  orthopaedic, prosthetic and artificial-body-part appliances
#   9027  instruments for physical/chemical analysis (lab, diagnostics)
#   3004  medicaments, put up in measured doses (the pharma trade stream)
# Queried one code at a time: the public preview caps a response at 500 rows, and four
# codes at once silently truncates at that cap (measured: 500 rows), while one code stays
# under it (240-295 rows). Truncation would look exactly like "this country has no trade".
THEME_HS = {"9018": "电医疗仪器(EEG/EMG)", "9021": "假体/矫形器械",
            "9027": "理化分析仪(诊断)", "3004": "药品(定量包装)"}
GLEIF = "https://api.gleif.org/api/v1/lei-records/%s"
GLEIF_PARENT = "https://api.gleif.org/api/v1/lei-records/%s/direct-parent-relationship"
GLEIF_ULTIMATE = "https://api.gleif.org/api/v1/lei-records/%s/ultimate-parent-relationship"
TRADE_URL = ("https://comtradeapi.un.org/public/v1/preview/C/A/HS"
             "?reporterCode=%s&period=%s&cmdCode=%s")
REF_URL = "https://comtradeapi.un.org/files/v1/app/reference/%s.json"
WD_SPARQL = "https://query.wikidata.org/sparql"
UN_IDEAL_URL = "https://dataverse.harvard.edu/api/access/datafile/11824371"

# Theme-scoped financial layer. Two kinds of instrument, kept apart on purpose:
#   country_basket  MSCI single-country ETF, all from one issuer and one currency of listing,
#                   so a correlation between two of them is comparable.
#   theme_basket    biotech / medical-device sector funds (XBI, IBB, BBH, FBT, IHJ, IXJ).
#                   These say where the biopharma market is, not which country it is in.
# Measured 2026-11-23: 46 of 48 symbols return a full 250-day year; GXG and PGAL return
# nothing and are dropped by the code rather than by hand.
COUNTRY_ETFS = {
    "US": "SPY", "JP": "EWJ", "CN": "FXI", "GB": "EWU", "DE": "EWG", "FR": "EWQ",
    "IT": "EWI", "ES": "EWP", "NL": "EWN", "AT": "EWO", "CH": "EWL", "SE": "EWD",
    "DK": "EDEN", "NO": "ENOR", "FI": "EFNL", "IE": "EIRL", "BE": "EWK",
    "PL": "EPOL", "TR": "TUR", "IN": "INDA", "KR": "EWY", "TW": "EWT", "HK": "EWH",
    "SG": "EWS", "AU": "EWA", "CA": "EWC", "MX": "EWW", "BR": "EWZ", "AR": "ARGT",
    "CL": "ECH", "PE": "EPU", "ZA": "EZA", "EG": "EGPT", "SA": "KSA", "AE": "UAE",
    "IL": "EIS", "ID": "EIDO", "TH": "THD", "MY": "EWM", "PH": "EPHE", "VN": "VNM",
}
THEME_ETFS = {"XBI": "SPDR 生物科技", "IBB": "纳斯达克生物科技", "BBH": "生物科技(BBH)",
              "FBT": "生物科技(FBT)", "IHI": "医疗器械", "IXJ": "全球医疗健康"}
ETFS = dict(COUNTRY_ETFS)
ETFS.update({"_theme_" + k: v for k, v in THEME_ETFS.items()})


def _get(url: str, timeout: float = 45.0, tries: int = 3, accept: str | None = None,
         sleep: float = 2.0) -> bytes:
    import time
    last = None
    for i in range(tries):
        try:
            hdr = {"User-Agent": DEFAULT_UA}
            if accept:
                hdr["Accept"] = accept
            with urllib.request.urlopen(urllib.request.Request(url, headers=hdr),
                                        timeout=timeout, context=SSL_CONTEXT) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001 - every transport error is the same here
            last = e
            time.sleep(sleep * (i + 1))
    raise FetchError("%s: %s <- %s" % (type(last).__name__, last, url)) from last


def _json(url: str, **kw):
    return json.loads(_get(url, **kw))


_LOCK = None


def _write_lock():
    """One writer at a time. Measured: 8 trade fetchers each rewriting the cache file raced
    on os.replace and one raised WinError 5. A JSON cache written by N threads needs either
    a lock or a per-thread file; the lock is smaller and keeps the cache readable."""
    global _LOCK
    if _LOCK is None:
        import threading
        # RLock, not Lock. Measured: with a plain Lock the GLEIF fetcher hung at 32/110
        # records for minutes with no output -- the thread took the lock in gleif_record,
        # called _save, and _save took the same lock again. A self-deadlock reports nothing,
        # so it looks exactly like a slow network.
        _LOCK = threading.RLock()
    return _LOCK


def _cache_path(name: str) -> str:
    os.makedirs(CACHE, exist_ok=True)
    return os.path.join(CACHE, name)


def _load(name: str, default):
    try:
        with open(_cache_path(name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _save(name: str, data) -> None:
    """Atomic-ish save that survives a Windows handle held elsewhere.

    Measured: os.replace onto .cache/trade_flows.json raises WinError 5 while another thread
    or a desktop sync client holds the target open. Retrying the replace fixes the transient
    case; falling back to an in-place write keeps the data when it does not, and a truncated
    cache is caught by the json.load error path that already exists.
    """
    import time as _t
    p = _cache_path(name)
    payload = json.dumps(data, ensure_ascii=False)
    with _write_lock():
        tmp = "%s.%d.tmp" % (p, os.getpid())
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(payload)
        for i in range(6):
            try:
                os.replace(tmp, p)
                return
            except PermissionError:
                _t.sleep(0.05 * (i + 1))
        try:
            os.remove(tmp)
        except OSError:
            pass
        with open(p, "w", encoding="utf-8") as f:
            f.write(payload)


def country_index(g: Graph) -> dict[str, str]:
    """iso2 -> node key, from whatever country nodes the graph already has."""
    idx = {}
    for n in g.nodes.values():
        if n.kind != "country":
            continue
        iso = ((n.attrs or {}).get("iso2") or n.key.split(":")[-1] or "").upper()
        if len(iso) == 2:
            idx[iso] = n.key
    return idx


# ------------------------------------------------------------------ commercial
def comtrade_reference() -> tuple[dict, dict]:
    """iso2 -> numeric reporter/partner code. Comtrade wants the numeric code."""
    c = _load("comtrade_reference.json", None)
    if c:
        return {k: v["code"] for k, v in c["reporters"].items()}, \
               {k: v["code"] for k, v in c["partners"].items()}
    rep = _json(REF_URL % "Reporters")["results"]
    par = _json(REF_URL % "partnerAreas")["results"]
    reporters, partners, names = {}, {}, {}
    # A retired code and a live code share an ISO2: measured, the reporter list has both
    # 841 "USA and Puerto Rico (...1980)" with entryExpiredDate 1980-12-31 and 842 "USA".
    # Keeping whichever came last gave 841, and 841 returns zero rows for 2024 -- a whole
    # country of trade silently missing. So an expired entry never wins, and it never
    # overwrites a live one.
    def expired(row):
        d = row.get("entryExpiredDate")
        return bool(d) and not str(d).startswith("9999")

    for r in rep:
        iso = (r.get("reporterCodeIsoAlpha2") or "").upper()
        if len(iso) != 2 or r.get("isGroup"):
            continue
        cur = reporters.get(iso)
        if cur is None or (cur.get("expired") and not expired(r)):
            reporters[iso] = {"code": r["reporterCode"], "name": r.get("reporterDesc"),
                              "expired": expired(r)}
            names[iso] = r.get("reporterDesc")
    for p in par:
        iso = (p.get("PartnerCodeIsoAlpha2") or "").upper()
        if len(iso) != 2 or p.get("isGroup"):
            continue
        cur = partners.get(iso)
        if cur is None or (cur.get("expired") and not expired(p)):
            partners[iso] = {"code": p["PartnerCode"], "name": p.get("PartnerDesc"),
                             "expired": expired(p)}
    _save("comtrade_reference.json", {"reporters": reporters, "partners": partners})
    return ({k: v["code"] for k, v in reporters.items()},
            {k: v["code"] for k, v in partners.items()})


def trade_rows(iso2: str) -> dict:
    """One reporter's bilateral THEME trade (BCI/biopharma HS codes) for TRADE_YEAR.

    Cached per reporter-year in one file, so a run interrupted by the public quota resumes
    where it stopped instead of re-fetching 200 countries.
    """
    cache = _load("trade_flows.json", {})
    key = "%s:%d" % (iso2, TRADE_YEAR)
    if key in cache:
        return cache[key]
    rep_codes, par_codes = comtrade_reference()
    code = rep_codes.get(iso2)
    res = {"iso2": iso2, "year": TRADE_YEAR, "rows": [], "error": None,
           "hs": list(THEME_HS)}
    if not code and iso2 in par_codes:
        # The reporter and partner reference lists disagree for a few countries. Measured:
        # US is 842 in partnerAreas and 841 in Reporters; 841 returns zero rows, 842 returns
        # 202. So the partner list is tried as a fallback rather than assumed equal.
        code = par_codes[iso2]
        res["reporter_code_from"] = "partnerAreas 回退"
    if not code:
        res["error"] = "Comtrade 报告国清单里没有 %s" % iso2
        cache[key] = res
        _save("trade_flows.json", cache)
        return res
    import time as _t
    # Requests per HS code, both flow directions at once. Measured on this host
    # 2026-11-24: the public preview caps a response at 500 rows, and the cap is silent --
    # 500 arriving looks like a complete answer. Four codes in one request therefore
    # truncates (DE 4-HS both-flows: exactly 500, some codes missing), while one code with
    # both flows stays under it (DE 9018/9021/9027/3004 = 338/279/340/282). So the unit of
    # request is one HS code; a 500 is treated as truncation and that code is re-fetched
    # split by flow (US 9018 returns 500 with flows M/RX/X and must be split).
    by_code: dict[str, list] = {}
    quota = None
    for hs in THEME_HS:
        rows: list = []
        try:
            d = _json(TRADE_URL % (code, TRADE_YEAR, hs), timeout=90, tries=3)
        except FetchError as e:
            quota = str(e)
            break
        rows = d.get("data") or []
        if len(rows) >= 500:
            rows = []
            split_failed = False
            for flow in ("X", "M"):
                try:
                    d2 = _json(TRADE_URL % (code, TRADE_YEAR, hs) + "&flowCode=" + flow,
                               timeout=90, tries=2)
                except FetchError as e:
                    quota = str(e)
                    split_failed = True
                    break
                part = d2.get("data") or []
                if len(part) >= 500:
                    res["truncated"] = (res.get("truncated") or []) + ["%s/%s" % (hs, flow)]
                rows.extend(part)
                _t.sleep(2)
            if split_failed:
                break
        if not rows and res.get("reporter_code_from") is None:
            alt = res.get("_alt_code")
            if alt is None:
                par_codes = comtrade_reference()[1]
                alt = par_codes.get(iso2)
                res["_alt_code"] = alt or False
            if alt and alt != code:
                try:
                    d3 = _json(TRADE_URL % (alt, TRADE_YEAR, hs), timeout=90, tries=2)
                    rows = d3.get("data") or []
                    if rows:
                        res["reporter_code_from"] = "partnerAreas 回退(%s)" % alt
                        code = alt
                except FetchError:
                    pass
        by_code[hs] = rows
        _t.sleep(3)
    if quota is not None:
        msg = quota
        if "429" in msg or "Quota" in msg or "403" in msg:
            # Not cached: a quota answer is not "this country has no trade". Caching it would
            # silently zero the reporter out on the next run.
            res["error"] = "限流（%s）——不计入结果，下次运行重试" % msg[:60]
            return res
        res["error"] = msg[:160]
        cache[key] = res
        _save("trade_flows.json", cache)
        return res
    if not by_code:
        res["error"] = "四个 HS 编码全部取数失败"
        return res
    if not any(by_code.values()):
        # A reporter that answers 200 with zero rows for every theme code is an EMPTY
        # reporter, not an unfetched one -- RU does exactly this for 2023 and 2024. Recorded
        # so the next run does not re-spend quota on it, and so nobody reads the silence as
        # "we just have not got to Russia yet".
        res["verified_empty"] = True
        res["error"] = "Comtrade 该报告国对此四个 HS 编码返回 0 行（实测空，非限流、非解析失败）"
        cache[key] = res
        _save("trade_flows.json", cache)
        return res
    d = {"data": [x for rows in by_code.values() for x in rows]}
    by_partner: dict[int, dict] = {}
    for r in d.get("data") or []:
        pc = r.get("partnerCode")
        if not pc:
            continue
        e = by_partner.setdefault(pc, {})
        v = r.get("primaryValue")
        if v is None:
            continue
        if r.get("flowCode") == "X":
            e["x"] = (e.get("x") or 0) + v
        elif r.get("flowCode") == "M":
            e["m"] = (e.get("m") or 0) + v
    par_by_code = {v: k for k, v in par_codes.items()}
    for pc, e in by_partner.items():
        iso = par_by_code.get(pc)
        if not iso:
            continue
        res["rows"].append({"partner": iso, "exports_usd": e.get("x"),
                            "imports_usd": e.get("m"), "hs": list(THEME_HS)})
    res.pop("_alt_code", None)
    cache[key] = res
    _save("trade_flows.json", cache)
    return res


def attach_trade(g: Graph, iso_index: dict[str, str] | None = None,
                 workers: int = 4, limit: int | None = None,
                 cached_only: bool = False) -> dict:
    """`cached_only` is how the graph gets rebuilt while the quota is exhausted: it draws
    every reporter already in .cache/trade_flows.json and reports the rest as missing,
    instead of blocking the whole build on a rate-limited source."""
    idx = iso_index if iso_index is not None else country_index(g)
    isos = [i for i in idx if i in comtrade_reference()[0]]
    if cached_only:
        have = _load("trade_flows.json", {})
        pref = [i for i in isos if have.get("%s:%d" % (i, TRADE_YEAR), {}).get("rows")]
        return {"reporters": 0, "failed": 0, "edges": 0, "attempted": len(pref),
                "cached_only": True, "isos": pref}
    if limit:
        isos = isos[:limit]
    done = failed = edges = 0
    with ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(trade_rows, i): i for i in isos}
        for f in as_completed(futs):
            r = f.result()
            if r.get("error"):
                failed += 1
                continue
            done += 1
            total_x = total_m = 0.0
            for row in r["rows"]:
                dst = idx.get(row["partner"])
                vx, vm = row.get("exports_usd") or 0, row.get("imports_usd") or 0
                total_x += vx
                total_m += vm
                if not dst:
                    continue
                hs = {"9018": "电医疗仪器(EEG/EMG)", "9021": "假体/矫形器械",
                      "9027": "理化分析仪(诊断)", "3004": "药品(定量包装)"}
                if vx:
                    g.add_edge(Edge(src=idx[r["iso2"]], dst=dst, rel="bci_biopharma_exports_to",
                                    attrs={"usd": vx, "year": TRADE_YEAR, "hs_codes": hs,
                                           "note": "主题口径：医疗器械+假体+诊断仪+药品"},
                                    source="UN Comtrade", fetched_at=NOW(),
                                    url="https://comtradeplus.un.org/TradeFlow"))
                    edges += 1
                if vm:
                    g.add_edge(Edge(src=idx[r["iso2"]], dst=dst, rel="bci_biopharma_imports_from",
                                    attrs={"usd": vm, "year": TRADE_YEAR, "hs_codes": hs,
                                           "note": "主题口径：医疗器械+假体+诊断仪+药品"},
                                    source="UN Comtrade", fetched_at=NOW(),
                                    url="https://comtradeplus.un.org/TradeFlow"))
                    edges += 1
            n = g.nodes.get(idx[r["iso2"]])
            if n:
                n.attrs = dict(n.attrs or {}, trade_year=TRADE_YEAR,
                               theme_exports_usd=round(total_x, 0),
                               theme_imports_usd=round(total_m, 0))
    return {"reporters": done, "failed": failed, "edges": edges,
            "attempted": len(isos), "covered": sorted(
                k.split(":")[0] for k, v in _load("trade_flows.json", {}).items()
                if v.get("rows")), "year": TRADE_YEAR}


# ------------------------------------------------------------------ political
WD_DIPLO_Q = """SELECT ?srcIso ?dstIso ?since ?lang WHERE {
  ?src wdt:P31 wd:Q3624078 ; wdt:P297 ?srcIso ; wdt:P530 ?dst .
  ?dst wdt:P297 ?dstIso .
  OPTIONAL { ?src p:P530 ?st . ?st ps:P530 ?dst .
             OPTIONAL { ?st pq:P580 ?since } OPTIONAL { ?st pq:P2936 ?lang } }
} ORDER BY ?srcIso ?dstIso LIMIT %d OFFSET %d"""


def wikidata_diplomatic(page: int = 2000, max_pages: int = 12) -> list[dict]:
    cached = _load("diplo_relations.json", None)
    if cached:
        return cached
    out: list[dict] = []
    for i in range(max_pages):
        q = WD_DIPLO_Q % (page, i * page)
        try:
            d = _json(WD_SPARQL + "?format=json&query=" + urllib.parse.quote(q),
                      timeout=120, tries=2,
                      accept="application/sparql-results+json")
        except FetchError:
            break
        rows = d["results"]["bindings"]
        if not rows:
            break
        for b in rows:
            out.append({
                "a": b["srcIso"]["value"].upper(),
                "b": b["dstIso"]["value"].upper(),
                "since": b.get("since", {}).get("value"),
                "lang": b.get("lang", {}).get("value"),
            })
        if len(rows) < page:
            break
    # a pair is one relation: dedupe, keeping the earliest evidence and every language
    rel: dict[tuple[str, str], dict] = {}
    for r in out:
        k = (r["a"], r["b"])
        e = rel.setdefault(k, {"since": None, "langs": set()})
        if r["since"] and (not e["since"] or r["since"] < e["since"]):
            e["since"] = r["since"]
        if r["lang"] and r["lang"].startswith("http"):
            e["langs"].add(r["lang"].rsplit("/", 1)[-1])
    res = [{"a": k[0], "b": k[1], "since": v["since"], "langs": sorted(v["langs"])}
           for k, v in rel.items()]
    _save("diplo_relations.json", res)
    return res


def attach_diplomatic(g: Graph, idx: dict[str, str] | None = None) -> dict:
    idx = idx if idx is not None else country_index(g)
    try:
        rows = wikidata_diplomatic()
    except FetchError as e:
        return {"edges": 0, "error": str(e)[:150]}
    edges = unmatched = 0
    # 先把度数清零。否则每构建一次就在旧值上再加一遍：实测同一张表跑两轮，中国的建交度
    # 742 -> 1484，而边数一直是 6276。strip_geo() 摘的是边，摘不掉节点属性里的这个计数。
    for n in g.nodes.values():
        if n.kind == "country" and (n.attrs or {}).get("diplomatic_relations") is not None:
            n.attrs = dict(n.attrs, diplomatic_relations=0)
    for r in rows:
        a, b = idx.get(r["a"]), idx.get(r["b"])
        if not a or not b:
            unmatched += 1
            continue
        g.add_edge(Edge(src=a, dst=b, rel="has_diplomatic_relations_with",
                        attrs={"since": r["since"], "languages": r["langs"],
                               "property": "Wikidata P530"},
                        source="Wikidata", fetched_at=NOW(),
                        url="https://www.wikidata.org/wiki/Property:P530"))
        edges += 1
    for r in rows:
        for iso in (r["a"], r["b"]):
            k = idx.get(iso)
            if k and k in g.nodes:
                n = g.nodes[k]
                c = (n.attrs or {}).get("diplomatic_relations") or 0
                n.attrs = dict(n.attrs or {}, diplomatic_relations=c + 1)
    return {"edges": edges, "unmatched": unmatched}


# ------------------------------------------------------------------ UN voting
def un_ideal_points() -> dict[str, dict]:
    """Latest session's ideal point per ISO3. One 2.4 MB download, parsed once."""
    cached = _load("un_ideal_points.json", None)
    if cached:
        return cached
    raw = _get(UN_IDEAL_URL, timeout=180, tries=2)
    import csv
    text = raw.decode("utf-8-sig", "ignore")
    out: dict[str, dict] = {}
    # Two files in the same Dataverse release, two schemas:
    #   IdealpointsJuly2025.tab      iso3c, year, idealpointall   (one row per country-year)
    #   AgreementScoresAll_*.csv     ccode, session, IdealPointAll (aggregated dyads)
    # The smaller .tab is used, so both spellings are accepted rather than one being assumed.
    def pick(row, *names):
        for n in names:
            if n in row and row[n] not in ("", None):
                return row[n]
        return None

    # The .tab file is tab-separated and carries a .tab extension, not .csv -- csv.Sniffer
    # is not needed because the delimiter is stated, but DictReader must be told, otherwise
    # it returns one column per line and every row is silently skipped (measured: 0 points).
    delim = "\t" if "\t" in text.split("\n", 1)[0] else ","
    for row in csv.DictReader(io.StringIO(text), delimiter=delim):
        iso = (pick(row, "iso3c", "ISO3", "iso3") or "").strip('"').upper()
        sess_raw = pick(row, "session", "year")
        ip_raw = pick(row, "idealpointall", "IdealPointAll", "idealpoint", "Idealpoint")
        q_raw = pick(row, "qoall", "QO%All")
        if not iso or len(iso) != 3:
            continue
        try:
            sess = int(float(sess_raw or 0))
            ip = float(ip_raw if ip_raw is not None else "nan")
            q = float(q_raw) if q_raw not in (None, "") else float("nan")
        except (TypeError, ValueError):
            continue
        if not iso or math.isnan(ip):
            continue
        cur = out.get(iso)
        if not cur or sess > cur["session"]:
            out[iso] = {"session": sess, "ideal_point": ip, "qo": None if math.isnan(q) else q}
    _save("un_ideal_points.json", out)
    return out


def attach_un_votes(g: Graph, idx: dict[str, str] | None = None,
                    min_agreement: float = 0.75) -> dict:
    """Agreement between two states' UNGA ideal points, as 1 - (normalised spread).

    The ideal-point scale is roughly [-3, 3]; two states in the same camp sit within ~0.2.
    Measured at 0.5: 11252 edges out of 18336 pairs -- that is 61% of all pairs, which is a
    fact about the UNGA but useless as a relation, because "most countries mostly agree"
    swamps every other layer. At 0.75 the edge means "these two vote as a bloc" and the
    dropped count is printed, so the cut is a stated choice rather than a silent filter.
    """
    idx = idx if idx is not None else country_index(g)
    try:
        pts = un_ideal_points()
    except FetchError as e:
        return {"edges": 0, "error": str(e)[:150]}
    iso3 = _load("wb_iso3_by_iso2.json", None)
    if not iso3:
        rows = _json("https://api.worldbank.org/v2/country?format=json&per_page=400")[1]
        iso3 = {r["iso2Code"]: r["id"] for r in rows if r.get("iso2Code")}
        _save("wb_iso3_by_iso2.json", iso3)
    live = {}
    for iso2, key in idx.items():
        i3 = iso3.get(iso2)
        p = pts.get(i3) if i3 else None
        if p:
            live[iso2] = p
            n = g.nodes.get(key)
            if n:
                n.attrs = dict(n.attrs or {}, un_ideal_point=p["ideal_point"],
                               un_session=p["session"])
    keys = sorted(live)
    edges = dropped = 0
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            spread = abs(live[a]["ideal_point"] - live[b]["ideal_point"])
            agreement = max(0.0, 1.0 - spread / 2.0)   # 2.0 points apart ~ opposite camps
            if agreement < min_agreement:
                dropped += 1
                continue
            g.add_edge(Edge(src=idx[a], dst=idx[b], rel="votes_with",
                            attrs={"agreement": round(agreement, 3),
                                   "ideal_point_gap": round(spread, 3),
                                   "session": max(live[a]["session"], live[b]["session"]),
                                   "basis": "联合国大会理想点距离，不是同盟声明"},
                            source="UNGA ideal points (Voeten, Harvard Dataverse)",
                            fetched_at=NOW(),
                            url="https://doi.org/10.7910/DVN/LEJUQZ"))
            edges += 1
    return {"countries": len(keys), "edges": edges, "dropped_below_threshold": dropped}


# ------------------------------------------------------------------ financial
def etf_returns(symbol: str) -> dict:
    cached = _load("etf_returns.json", {})
    if symbol in cached:
        return cached[symbol]
    import csv
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/%s"
           "?range=1y&interval=1d" % symbol)
    res = {"symbol": symbol, "dates": [], "returns": [], "error": None}
    try:
        d = _json(url, timeout=40, tries=2)
        r = (d.get("chart") or {}).get("result") or []
        if not r:
            res["error"] = "Yahoo 无数据"
        else:
            res0 = r[0]
            ts = res0.get("timestamp") or []
            close = (((res0.get("indicators") or {}).get("adjclose") or [{}])[0]
                     .get("adjclose") or (res0.get("indicators") or {}).get("quote", [{}])[0]
                     .get("close") or [])
            pairs = [(t, c) for t, c in zip(ts, close) if c]
            for (t0, c0), (t1, c1) in zip(pairs, pairs[1:]):
                if c0:
                    res["dates"].append(t1)
                    res["returns"].append(c1 / c0 - 1.0)
    except FetchError as e:
        res["error"] = str(e)[:140]
    cached[symbol] = res
    _save("etf_returns.json", cached)
    return res


def _pearson(x: list[float], y: list[float]) -> tuple[float, int]:
    if len(x) != len(y) or len(x) < 30:
        return (float("nan"), len(x))
    n = len(x)
    mx, my = sum(x) / n, sum(y) / n
    sxx = sum((a - mx) ** 2 for a in x)
    syy = sum((b - my) ** 2 for b in y)
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    if sxx <= 0 or syy <= 0:
        return (float("nan"), n)
    return (sxy / math.sqrt(sxx * syy), n)


def attach_theme_market(g: Graph, idx: dict[str, str] | None = None,
                        workers: int = 8, min_corr: float = 0.5) -> dict:
    """Two market relations, both theme-scoped, both labelled as co-movement not capital flow.

    1. country_basket: one MSCI single-country ETF per market -> pairwise daily-return
       Pearson over one year. `market_comoves_with`. Comparable because every leg is the
       same issuer, same currency, same calendar.
    2. theme_basket: the biotech/device sector funds -> how each country market moves with
       the sector itself. `moves_with_bci_biopharma_sector`. This is the answer to "which
       country's equity market actually tracks the biopharma cycle".
    """
    idx = idx if idx is not None else country_index(g)
    country = {iso: sym for iso, sym in COUNTRY_ETFS.items() if iso in idx}
    theme = dict(THEME_ETFS)
    wanted = list(country.values()) + list(theme)
    series: dict[str, dict] = {}
    with ThreadPoolExecutor(workers) as ex:
        for res in ex.map(etf_returns, wanted):
            if res.get("returns"):
                series[res["symbol"]] = res
    syms = sorted(s for s in country.values() if s in series)
    edges = dropped = 0
    for i, s1 in enumerate(syms):
        for s2 in syms[i + 1:]:
            a, b = series[s1], series[s2]
            n = min(len(a["returns"]), len(b["returns"]))
            corr, ndays = _pearson(a["returns"][-n:], b["returns"][-n:])
            if corr != corr or corr < min_corr:
                dropped += 1
                continue
            iso1 = next(k for k, v in country.items() if v == s1)
            iso2 = next(k for k, v in country.items() if v == s2)
            g.add_edge(Edge(src=idx[iso1], dst=idx[iso2], rel="market_comoves_with",
                            attrs={"pearson": round(corr, 3), "days": ndays,
                                   "window": "1 年日收益",
                                   "basis": "两国股指同步涨跌，不是资本流动",
                                   "instruments": [s1, s2]},
                            source="Yahoo Finance (country ETF)", fetched_at=NOW(),
                            url="https://finance.yahoo.com/quote/%s" % s1))
            edges += 1

    theme_edges = 0
    tsyms = [s for s in theme if s in series]
    for iso, sym in country.items():
        if sym not in series:
            continue
        for t in tsyms:
            a, b = series[sym], series[t]
            n = min(len(a["returns"]), len(b["returns"]))
            corr, ndays = _pearson(a["returns"][-n:], b["returns"][-n:])
            if corr != corr or corr < min_corr:
                continue
            g.add_edge(Edge(src=idx[iso], dst="theme_basket:" + t,
                            rel="moves_with_bci_biopharma_sector",
                            attrs={"pearson": round(corr, 3), "days": ndays,
                                   "window": "1 年日收益",
                                   "basis": "该国股指与生物医药板块 ETF 同步涨跌"},
                            source="Yahoo Finance (ETF)", fetched_at=NOW(),
                            url="https://finance.yahoo.com/quote/%s" % t))
            theme_edges += 1
    for t in tsyms:
        k = "theme_basket:" + t
        if k not in g.nodes:
            g.add_node(Node(key=k, kind="theme_basket", label="%s %s" % (t, THEME_ETFS[t]),
                            attrs={"etf": t, "theme": "脑机接口/生物医药"},
                            source="Yahoo Finance", fetched_at=NOW(),
                            url="https://finance.yahoo.com/quote/%s" % t))
    return {"series": len(series), "edges": edges, "dropped_below_corr": dropped,
            "theme_edges": theme_edges, "theme_baskets": len(tsyms),
            "missing_symbols": [s for s in wanted if s not in series][:12]}



# ---------------------------------------------------------------- 企业层：GLEIF
# GLEIF 没有 NACE 行业码（实测 lei-records 的 entity 里只有 legalName/address/status 等），
# 所以"按行业码过滤"这条路不存在。剩下的判据只有两条，都很硬：
#   1. 名字要真的落在医疗/神经科技上，而不是把 brain 当字面词——实测命中里有
#      "BRAIN PETROLEUM PRODUCTS TRADING L.L.C"（迪拜石油贸易），这类必须剔掉；
#   2. 母公司关系直接取 GLEIF 的 direct-parent / ultimate-parent，这两条端点实测 200，
#      于是"跨境母子公司"这一层是权威登记数据，不是名字猜测。
# 名字里"像医疗"要分档，因为裸的 brain 会把软件公司、太阳能公司都拉进来。实测命中里
# IT-Brain Software GmbH 是软件、BRAIN-SOL Foltinek GmbH 是太阳能、BRAIN PETROLEUM 是
# 石油贸易——三个都含 brain，一个都不是主题。所以判据是"医疗锚词"，brain 只能在旁边陪跑：
#   strong: neuro/biotech/pharma/therapeut/medical/medtech/diagnos/surgic/implant/prosthe/
#           rehab/genom/genetic/clinic/hospital/bio-/bioscien/biomed/biopharm
#   weak  : brain/cell/health/device/sensor/stimul/cortex/synapse/electrode —— 单独不算
THEME_NAME_STRONG = (
    "neuro", "biotech", "pharma", "therapeut", "medical", "medtech", "diagnos", "surgic",
    "implant", "prosthe", "rehab", "genom", "genetic", "clinic", "hospital", "bio-",
    "bioscien", "biomed", "biopharm", "medicin", "neural", "neuromod", "synchron",
    "prosthes",
)
THEME_NAME_WEAK = ("brain", "cell", "health", "device", "sensor", "stimul", "cortex",
                   "synapse", "electrode", "bci")
THEME_NAME_VETO = (
    "petroleum", "oil", "gas", "shipping", "cargo", "logistic", "freight", "general trading",
    "trading", "real estate", "property", "food", "restaurant", "hotel", "textile",
    "construction", "cement", "steel", "mining", "agricultur", "insurance", "bank",
    "holding", "investment", "capital", "finance", "software", "solar", "energy",
    "consulting", "media", "advertis", "travel", "auto", "motor",
)


def gleif_record(lei: str) -> dict:
    cache = _load("gleif_records.json", {})
    if lei in cache:
        return cache[lei]
    try:
        # tries=1: GLEIF answers 404 for a LEI it does not know and that is an answer, not a
        # transport failure. Measured: the retry ladder (2+4+6s sleeps x3 endpoints) cost 72s
        # per record, i.e. two hours for 110 companies.
        d = _json(GLEIF % lei, timeout=25, tries=1, accept="application/vnd.api+json")
        a = (d.get("data") or {}).get("attributes") or {}
        ent = a.get("entity") or {}
        names = [ent["legalName"]["name"]] + [x.get("name") for x in ent.get("otherNames") or []]
        rec = {"lei": lei, "names": [n for n in names if n],
               "country": (ent.get("legalAddress") or {}).get("country"),
               "status": ent.get("status"),
               "parent": None, "ultimate": None}
    except FetchError as e:
        return {"lei": lei, "names": [], "country": None, "status": None,
                "parent": None, "ultimate": None, "error": str(e)[:80]}
    for endpoint, key in ((GLEIF_PARENT, "parent"), (GLEIF_ULTIMATE, "ultimate")):
        try:
            r = _json(endpoint % lei, timeout=25, tries=1,
                      accept="application/vnd.api+json")
            one = (r.get("data") or {}).get("attributes") or {}
            start = ((one.get("relationship") or {}).get("startNode") or {})
            end = ((one.get("relationship") or {}).get("endNode") or {})
            # The field is `id`, not `nodeID`. Measured: reading nodeID gave None on every
            # record, so 110 companies came back with zero parent relationships and the
            # whole cross-border layer was silently empty while the request returned 200.
            if end.get("id"):
                rec[key] = {"lei": end.get("id"), "name": end.get("id"),
                            "period": (one.get("registration") or {}).get("period")}
        except FetchError:
            pass
    # 逐条读-改-写，并且加锁：实测 32/110 条丢失，原因是 6 个线程各自持有旧字典、最后
    # 一个写盘的人把别人刚拿到的记录覆盖掉了。缓存文件是共享状态，不能整字典覆盖。
    cache = _load("gleif_records.json", {})
    cache[lei] = rec
    _save("gleif_records.json", cache)      # _save already holds the (re-entrant) lock
    return rec


def theme_name_tier(names: list[str] | None) -> str:
    """'strong' / 'weak' / 'off'. Only 'strong' counts as a theme company."""
    low = " ".join(n for n in (names or []) if n).lower()
    if not low or any(t in low for t in THEME_NAME_VETO):
        return "off"
    if any(t in low for t in THEME_NAME_STRONG):
        return "strong"
    if any(t in low for t in THEME_NAME_WEAK):
        return "weak"
    return "off"


def _theme_name_ok(names: list[str]) -> bool:
    return theme_name_tier(names) == "strong"


def attach_gleif_parents(g: Graph, idx: dict[str, str] | None = None,
                         workers: int = 6, include_leads: bool = True) -> dict:
    """企业 → 注册国，企业 → 母公司（跨境合并），并把名字不落在主题上的企业标出来。

    交叉核验的另一半在 attach_gleif_theme()：一家公司名字像医疗，但如果没有临床试验、
    没有论文、也没有母公司关系，它就只是一个字符串，不参与主题结论。
    """
    idx = idx if idx is not None else country_index(g)
    leis = [n.attrs["lei"] for n in g.nodes.values()
            if n.kind == "company_gleif" and (n.attrs or {}).get("lei")]
    if include_leads:
        found = 0
        with ThreadPoolExecutor(workers) as ex:
            for hit in ex.map(lambda c: gleif_find(c[0], c[1]), THEME_LEAD_COMPANIES):
                if not hit:
                    continue
                found += 1
                key = "lei:" + hit["lei"]
                if key not in g.nodes:
                    g.add_node(Node(key=key, kind="company_gleif",
                                    label=hit["names"][0], attrs=dict(hit, lead=True),
                                    source="GLEIF 名称检索", fetched_at=NOW(),
                                    url="https://search.gleif.org/#/record/" + hit["lei"]))
                if hit["lei"] not in leis:
                    leis.append(hit["lei"])
    recs = {}
    with ThreadPoolExecutor(workers) as ex:
        for rec in ex.map(gleif_record, leis):
            recs[rec["lei"]] = rec
    reg_edges = parent_edges = cross_border = 0
    kept, dropped = [], []
    for lei, rec in recs.items():
        node = g.nodes.get("lei:" + lei)
        if node is None:
            continue
        ok = _theme_name_ok(rec.get("names") or [])
        node.attrs = dict(node.attrs or {}, names=rec.get("names"), country=rec.get("country"),
                          status=rec.get("status"), theme_name_match=ok)
        label = (rec.get("names") or [node.label or lei])[0]
        node.label = label if len(label) > 0 else node.label
        (kept if ok else dropped).append(lei)
        if not ok:
            continue
        iso = rec.get("country")
        if iso in idx:
            g.add_edge(Edge(src="lei:" + lei, dst=idx[iso], rel="registered_in",
                            attrs={"as_of": (rec.get("status") or "")},
                            source="GLEIF", fetched_at=NOW(),
                            url="https://search.gleif.org/#/record/" + lei))
            reg_edges += 1
        par = rec.get("parent") or rec.get("ultimate")
        if par and par.get("lei") and par["lei"] != lei:
            pk = "lei:" + par["lei"]
            if pk not in g.nodes:
                prec = gleif_record(par["lei"])
                g.add_node(Node(key=pk, kind="company_gleif", label=(prec.get("names") or [par["lei"]])[0],
                                attrs=dict(prec, parent_of=lei), source="GLEIF",
                                fetched_at=NOW(),
                                url="https://search.gleif.org/#/record/" + par["lei"]))
                iso2 = prec.get("country")
                if iso2 in idx:
                    g.add_edge(Edge(src=pk, dst=idx[iso2], rel="registered_in",
                                    attrs={}, source="GLEIF", fetched_at=NOW(),
                                    url="https://search.gleif.org/#/record/" + par["lei"]))
            g.add_edge(Edge(src="lei:" + lei, dst=pk, rel="consolidated_by",
                            attrs={"level": "direct" if rec.get("parent") else "ultimate"},
                            source="GLEIF RR", fetched_at=NOW(),
                            url="https://search.gleif.org/#/record/" + lei))
            parent_edges += 1
            if iso and rec.get("country") and _parent_country(par["lei"]) not in (None, iso):
                cross_border += 1
    return {"companies": len(leis), "leads_found": locals().get("found", 0),
            "theme_name_match": len(kept),
            "dropped_off_theme": len(dropped), "registered_in": reg_edges,
            "consolidated_by": parent_edges, "cross_border_hint": cross_border,
            "dropped_sample": (dropped[:8])}


def _parent_country(lei: str) -> str | None:
    return (gleif_record(lei) or {}).get("country")


GLEIF_SEARCH = "https://api.gleif.org/api/v1/lei-records?page[size]=8&filter[fulltext]=%s"


# 法律后缀允许叠加（"Holding AG"、"Technologies, Inc."），但每一个词都必须是后缀词，
# 这样 "Medtronic Europe Sàrl" 会因为 Europe 不是后缀而被拒，"F. Hoffmann-La Roche AG"
# 这种名字里带非后缀词的则整条不匹配、直接跳过（漏掉比安错好）。
_SUFFIX_WORDS = {
    "plc", "ag", "sa", "nv", "bv", "se", "inc", "incorporated", "corp", "corporation",
    "co", "company", "ltd", "limited", "llc", "lp", "gmbh", "kg", "kgaa", "sarl", "srl",
    "spa", "ab", "asa", "oyj", "aps", "holding", "holdings", "group", "groupe", "the",
    "international", "technologies", "technology", "medtech", "healthcare", "sciences",
    "systems", "laboratories", "labs", "pharma", "pharmaceuticals", "and", "&", "deutschland",
}


class _LegalTail:
    """Match a trailing fragment made only of legal-suffix words.

    Measured: a single-token regex rejected "Holding AG" and "Technologies, Inc.", which are
    exactly the shapes a group head takes, while "Medtronic Europe Sàrl" must be rejected.
    So the test is token-based: strip punctuation, then every remaining word must be a suffix
    word. Anything else (Europe, PENSION, Consumer, India) means it is not the group head.
    """

    DOTTED = {"n.v.": "nv", "b.v.": "bv", "s.a.": "sa", "s.r.l.": "srl", "p.l.c.": "plc",
              "l.l.c.": "llc", "s.p.a.": "spa", "a.s.": "as", "a.b.": "ab", "s.a.s.": "sas"}

    def match(self, tail: str):
        low = (tail or "").lower()
        for k, v in self.DOTTED.items():
            low = low.replace(k, v)
        words = [w for w in re.split(r"[\s,.&]+", low) if w]
        if not words:
            return None
        if all(w in _SUFFIX_WORDS for w in words):
            return True
        return None


LEGAL_SUFFIX = _LegalTail()


def gleif_find(name: str, country: str | None = None) -> dict | None:
    """Find the group head for `name`, or return None rather than guessing.

    Measured, and each one is a real wrong answer this function first gave:
      "Medtronic"         -> "Medtronic Defined Contribution Plan Master Trust" (a pension plan)
      "Boston Scientific" -> "BOSTON SCIENTIFIC PENSION PLAN" (IE)
      "Pfizer"            -> "PFIZER CONSUMER HEALTHCARE" (GB)
      after adding a 404-means-no-parent rule:
      "Medtronic"         -> "Medtronic Europe Sàrl" (CH)   <- 404 means NOT REPORTED, not
                                                              'no parent'. A subsidiary that
                                                              files no parent is indistinguishable
                                                              from a group head by that test.

    So the rule is now a shape test, not a graph test: the candidate's legal name must be the
    queried name plus nothing but legal suffixes (AG/SA/PLC/Inc/GmbH/Holding...), and when a
    country is supplied it must match. Anything else returns None. A lead that GLEIF does not
    confirm stays absent, which is the correct outcome -- inventing it would be worse.
    """
    try:
        d = _json(GLEIF_SEARCH % urllib.parse.quote(name), timeout=25, tries=1,
                  accept="application/vnd.api+json")
    except FetchError:
        return None
    want = name.strip().lower()
    best = None
    for rec in d.get("data") or []:
        a = (rec.get("attributes") or {}).get("entity") or {}
        names = [(a.get("legalName") or {}).get("name")] + [x.get("name") for x in a.get("otherNames") or []]
        names = [n for n in names if n]
        if a.get("status") != "ACTIVE":
            continue
        iso = (a.get("legalAddress") or {}).get("country")
        if country and iso != country:
            continue
        for n in names:
            low = n.strip().lower()
            if not low.startswith(want):
                continue
            tail = low[len(want):].strip(" ,.")
            if tail and not LEGAL_SUFFIX.match(" " + tail):
                continue
            cand = {"lei": rec["id"], "names": names, "country": iso,
                    "matched_name": n, "tail": tail}
            if best is None or len(tail) < len(best["tail"]):
                best = cand
            break
    return best




# 主题龙头：脑机接口与生物医药里真正有全球母子公司结构的公司。名字用来查 LEI，
# 不是用来当证据——查不到就跳过，绝不用近似名硬套。
THEME_LEAD_COMPANIES = (
    ("Medtronic", "IE"), ("Boston Scientific", "US"), ("Abbott Laboratories", "US"),
    ("Stryker", "US"), ("Becton Dickinson", "US"), ("Siemens Healthineers", "DE"),
    ("Nihon Kohden", "JP"), ("NeuroPace", "US"), ("LivaNova", "GB"), ("Elekta", "SE"),
    ("Roche Holding", "CH"), ("Novartis", "CH"), ("Pfizer", "US"),
    ("Johnson & Johnson", "US"), ("Merck", "US"), ("Sanofi", "FR"),
    ("AstraZeneca", "GB"), ("GSK", "GB"), ("Takeda", "JP"), ("Daiichi Sankyo", "JP"),
    ("UCB", "BE"), ("Biogen", "US"), ("Amgen", "US"), ("Gilead Sciences", "US"),
    ("Baxter International", "US"), ("Terumo", "JP"), ("Olympus", "JP"),
    ("Koninklijke Philips", "NL"), ("Fresenius", "DE"), ("B. Braun", "DE"),
    ("Synchron", "US"), ("Blackrock Neurotech", "US"), ("ONWARD Medical", "NL"),
    ("Saluda Medical", "AU"), ("iRhythm Technologies", "US"), ("Inari Medical", "US"),
    ("Nevro", "US"), ("Soterix Medical", "US"),
)


# ------------------------------------------------------------------ driver
LAYERS = ("trade", "diplomatic", "unvotes", "market", "gleif")

# 这一层自己产出的关系。重复构建时必须先摘掉，否则每跑一次就在旧图上再叠一遍：
# 实测中国建交度从 742 涨到 2968，看起来像数据变多了，其实是同一批边被加了四次。
GEO_RELS = (
    "bci_biopharma_exports_to", "bci_biopharma_imports_from", "registered_in",
    "consolidated_by", "has_diplomatic_relations_with", "votes_with",
    "market_comoves_with", "moves_with_bci_biopharma_sector",
)


def strip_geo(g: Graph) -> int:
    before = len(g.edges)
    for key in [k for k, e in g.edges.items() if e.rel in GEO_RELS]:
        g.edges.pop(key, None)
    return before - len(g.edges)


def attach_trade_cached(g: Graph, idx: dict[str, str] | None = None) -> dict:
    """Turn whatever is already in the trade cache into edges. No network."""
    idx = idx if idx is not None else country_index(g)
    have = _load("trade_flows.json", {})
    edges = reporters = 0
    skipped = []
    for key, res in have.items():
        if not res.get("rows"):
            continue
        iso2 = res.get("iso2") or key.split(":")[0]
        if iso2 not in idx:
            skipped.append(iso2)
            continue
        reporters += 1
        total_x = total_m = 0.0
        hs = res.get("hs") or list(THEME_HS)
        hs_map = {c: THEME_HS.get(c, c) for c in hs}
        for row in res["rows"]:
            dst = idx.get(row["partner"])
            vx, vm = row.get("exports_usd") or 0, row.get("imports_usd") or 0
            total_x += vx
            total_m += vm
            if not dst:
                continue
            if vx:
                g.add_edge(Edge(src=idx[iso2], dst=dst, rel="bci_biopharma_exports_to",
                                attrs={"usd": vx, "year": res.get("year"), "hs_codes": hs_map,
                                       "note": "主题口径：医疗器械+假体+诊断仪+药品"},
                                source="UN Comtrade", fetched_at=NOW(),
                                url="https://comtradeplus.un.org/TradeFlow"))
                edges += 1
            if vm:
                g.add_edge(Edge(src=idx[iso2], dst=dst, rel="bci_biopharma_imports_from",
                                attrs={"usd": vm, "year": res.get("year"), "hs_codes": hs_map,
                                       "note": "主题口径：医疗器械+假体+诊断仪+药品"},
                                source="UN Comtrade", fetched_at=NOW(),
                                url="https://comtradeplus.un.org/TradeFlow"))
                edges += 1
        n = g.nodes.get(idx[iso2])
        if n:
            n.attrs = dict(n.attrs or {}, theme_exports_usd=round(total_x, 0),
                           theme_imports_usd=round(total_m, 0),
                           theme_trade_year=res.get("year"))
    return {"reporters": reporters, "edges": edges, "skipped_no_node": skipped[:10],
            "cached_reporter_years": len([1 for v in have.values() if v.get("rows")])}


def attach_all(g: Graph, layers=LAYERS) -> dict:
    idx = country_index(g)
    notes: list[dict] = []
    stats: dict[str, dict] = {}
    if "trade" in layers:
        stats["trade"] = attach_trade_cached(g, idx)
        notes.append({"source": "UN Comtrade", "what": "主题贸易（HS 9018/9021/9027/3004）",
                      "error": "%d 个报告国有数据，%d 条边；公共额度限流，覆盖率见 README"
                               % (stats["trade"]["reporters"], stats["trade"]["edges"])})
    if "diplomatic" in layers:
        stats["diplomatic"] = attach_diplomatic(g, idx)
        notes.append({"source": "Wikidata P530", "what": "外交关系",
                      "error": stats["diplomatic"].get("error")
                               or "%d 条有向边，%d 条因一端无国家节点未落"
                               % (stats["diplomatic"]["edges"],
                                  stats["diplomatic"].get("unmatched", 0))})
    if "unvotes" in layers:
        stats["unvotes"] = attach_un_votes(g, idx)
        notes.append({"source": "UNGA ideal points", "what": "投票一致性",
                      "error": stats["unvotes"].get("error")
                               or "%d 国，%d 条边，低于阈值丢弃 %d 对"
                               % (stats["unvotes"]["countries"], stats["unvotes"]["edges"],
                                  stats["unvotes"]["dropped_below_threshold"])})
    if "market" in layers:
        stats["market"] = attach_theme_market(g, idx)
    if "gleif" in layers:
        try:
            stats["gleif"] = attach_gleif_parents(g, idx)
        except Exception as e:                      # a dead registry must not kill the build
            stats["gleif"] = {"error": str(e)[:120]}
        if "market" in stats:
            notes.append({"source": "Yahoo Finance", "what": "股指同步性（主题口径）",
                          "error": "%d 个序列；国家间 %d 条边；国家-生物医药板块 %d 条边"
                                   % (stats["market"]["series"], stats["market"]["edges"],
                                      stats["market"].get("theme_edges", 0))})
        if "gleif" in stats:
            gl = stats["gleif"]
            notes.append({"source": "GLEIF", "what": "企业注册国与跨境母子公司",
                          "error": gl.get("error") or
                                   "%d 家企业，名字强命中主题 %d 家，剔掉 %d 家；"
                                   "注册国边 %d 条，合并关系 %d 条"
                                   % (gl["companies"], gl["theme_name_match"],
                                      gl["dropped_off_theme"], gl["registered_in"],
                                      gl["consolidated_by"])})
    notes.append({"source": "BIS / IMF CDIS / OECD FDI", "what": "官方双边金融流量",
                  "error": "三个源在本机全部不可达（BIS 406/404，IMF data 500，OECD 406），"
                           "所以金融层用股指同步性代替，并标明它不是资本流"})
    g._notes = notes  # type: ignore[attr-defined]
    g._geo_stats = stats  # type: ignore[attr-defined]
    return stats


def geo_report(g: Graph, top: int = 8) -> str:
    from collections import Counter
    rels = Counter(e.rel for e in g.edges.values())
    L = ["  关系类型计数：" + "  ".join(
        "%s=%d" % (k, v) for k, v in sorted(rels.items())
        if k in ("bci_biopharma_exports_to", "bci_biopharma_imports_from",
                 "has_diplomatic_relations_with",
                 "votes_with", "market_comoves_with", "co_author_country",
                 "trial_spans_country")),
         "  总图：%d 节点 / %d 边" % (len(g.nodes), len(g.edges))]

    def lab(k):
        n = g.nodes.get(k)
        return (n.label if n else k)[:22]

    exp = sorted([e for e in g.edges.values()
                  if e.rel == "bci_biopharma_exports_to"],
                 key=lambda e: -(e.attrs.get("usd") or 0))[:top]
    if exp:
        L.append("  主题出口额最大的边（%d 年，HS 9018/9021/9027/3004，USD）：" % TRADE_YEAR)
        for e in exp:
            L.append("    %-22s -> %-22s %8.1f 十亿" % (lab(e.src), lab(e.dst),
                                                        (e.attrs.get("usd") or 0) / 1e9))
    dip = [e for e in g.edges.values() if e.rel == "has_diplomatic_relations_with"]
    if dip:
        deg = Counter()
        for e in dip:
            deg[e.src] += 1
            deg[e.dst] += 1
        L.append("  建交最多的国家：" + "、".join("%s %d" % (lab(k), v)
                                                for k, v in deg.most_common(6)))
    vote = sorted([e for e in g.edges.values() if e.rel == "votes_with"],
                  key=lambda e: -(e.attrs.get("agreement") or 0))[:top]
    if vote:
        L.append("  联大投票最一致的对（同一年届）：")
        for e in vote:
            L.append("    %-22s -- %-22s 一致性 %.3f" % (lab(e.src), lab(e.dst),
                                                        e.attrs.get("agreement") or 0))
    mk = sorted([e for e in g.edges.values() if e.rel == "market_comoves_with"],
                key=lambda e: -(e.attrs.get("pearson") or 0))[:top]
    if mk:
        L.append("  股指同步性最高的对（1 年日收益，相关系数）：")
        for e in mk:
            L.append("    %-22s -- %-22s %.3f（%d 天）"
                     % (lab(e.src), lab(e.dst), e.attrs.get("pearson") or 0,
                        e.attrs.get("days") or 0))
    # 企业层：只报名字强命中主题、且真的连到注册国的那批，并把被剔掉的点名三例——
    # 一个"过滤了什么"的句子，比多一行计数更能说明这层数据能不能用。
    gleif = [n for n in g.nodes.values() if n.kind == "company_gleif"
             and (n.attrs or {}).get("theme_name_match")]
    if gleif:
        by_c = Counter((n.attrs or {}).get("country") for n in gleif)
        leads = [n for n in gleif if (n.attrs or {}).get("lead")]
        L.append("  企业（GLEIF，名字强命中主题）%d 家，分布于 %d 个国家/地区；"
                 "其中主题龙头经名称+国别+法律后缀三重确认 %d 家"
                 % (len(gleif), len(by_c), len(leads)))
        L.append("    最多的注册地：" + "、".join("%s %d" % (c, k) for c, k in by_c.most_common(6)))
        if leads:
            L.append("    已确认龙头及注册国：" + "、".join(
                "%s(%s)" % ((n.attrs.get("matched_name") or n.label)[:26],
                            (n.attrs or {}).get("country")) for n in leads[:8]))
        off = [n for n in g.nodes.values() if n.kind == "company_gleif"
               and (n.attrs or {}).get("theme_name_match") is False]
        if off:
            L.append("    因名字不落在医疗锚词上被剔除 %d 家，例如：%s"
                     % (len(off), "、".join((n.label or "")[:24] for n in off[:3])))
    cons = [e for e in g.edges.values() if e.rel == "consolidated_by"]
    if cons:
        L.append("  母子公司关系（GLEIF RR）：%d 条" % len(cons))
    return "\n".join(L)
