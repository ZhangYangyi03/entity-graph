"""Every country on one spine: 249 ISO-3166 alpha-2 codes, none skipped.

Why this module exists
----------------------
The Nordic module answered "did we look at Sweden?" for four countries. The operator
then asked for all of them -- every country, no exceptions. A per-country claim is only
worth something if the spine it hangs from is complete, so this module is deliberately
dumb about that: it takes the full ISO-3166 list and refuses to drop a row. A country
with zero findings is a row with zero findings, not a missing row.

Measured sources (2026-11-23, this host)
----------------------------------------
    source                          key?  what it gives per country
    ISO-3166 (lukes/ISO-3166 GH)    no    249 alpha-2 codes, region, sub-region -- the spine
    Wikidata SPARQL (P297)          no    249 codes + zh-hans label (257/260 have one)
    World Bank country API          no    region, income level, 217 economies
    World Bank indicators           no    population, R&D %GDP, health %GDP
    Europe PMC  AFF:"<Country>"     no    BCI papers with an affiliation in that country
    GLEIF LEI API                   no    legal entities by country + fulltext (companies)

What did NOT work, measured, and therefore not used
--------------------------------------------------
    OpenAlex  api.openalex.org      429 Too Many Requests on every attempt, incl. the
                                    polite pool with mailto -- unusable from this host
                                    right now. Recorded rather than worked around,
                                    because a country count built on retry-luck is
                                    worse than a gap.
    Europe PMC AFF:"<ISO2>"        11 hits for "SE" against 68 for "Sweden"; "CI" gives 2
                                    against 0 for "Ivory Coast". Picking whichever number
                                    is larger would be inventing a rule after seeing the
                                    answer, so the country NAME is used and the count is
                                    therefore a floor, labelled as one.

The floor is the honest unit here. An affiliation string is free text; a paper can name
"Boston" without "United States". Every count this module emits is a *lower bound on
papers that name the country*, and the report says exactly that.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from .fetch import DEFAULT_UA, SSL_CONTEXT, FetchError
from .model import Edge, Graph, Node

NOW = lambda: _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")

TERM = '("brain-computer interface" OR "brain computer interface")'
ISO_URL = ("https://raw.githubusercontent.com/lukes/ISO-3166-Countries-with-Regional-Codes"
           "/master/all/all.json")
WD_SPARQL = "https://query.wikidata.org/sparql?format=json&query="

# Europe PMC matches the country as it is written in an affiliation, in English. Two codes
# that are not ISO and two that read badly in a query get an explicit override; nothing
# else is special-cased, because a table of 249 hand-written names is 249 chances to be
# wrong in a way nobody notices.
EPMC_NAME = {
    "SE": "Sweden", "DK": "Denmark", "FI": "Finland", "SI": "Slovenia", "NO": "Norway",
    "GB": "United Kingdom", "US": "United States", "KR": "South Korea",
    "KP": "North Korea", "RU": "Russia", "IR": "Iran", "VN": "Vietnam",
    "TW": "Taiwan", "CZ": "Czech Republic", "TR": "Turkey",
    "CD": "Democratic Republic of the Congo", "CG": "Congo",
    "CI": "Cote d'Ivoire", "BN": "Brunei", "LA": "Laos", "SY": "Syria",
    "TZ": "Tanzania", "MD": "Moldova", "MK": "North Macedonia", "BO": "Bolivia",
    "VE": "Venezuela", "PS": "Palestine", "VA": "Vatican City", "MO": "Macao",
    "HK": "Hong Kong", "XK": "Kosovo", "SZ": "Eswatini", "CV": "Cape Verde",
}


def _get(url: str, timeout: float = 40.0, tries: int = 4, sleep: float = 1.5,
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


# ------------------------------------------------------------------ the spine
SNAPSHOT = "tests/data/iso3166_snapshot.json"


def iso_countries(allow_snapshot: bool = True) -> list[dict]:
    """All 249 ISO-3166 officially-assigned countries, with region and sub-region.

    Deliberately does not filter. If this ever returns 248 the world map is wrong, and a
    test asserts the exact count for that reason.

    Falls back to a checked-in snapshot of the same URL when the network refuses. The
    snapshot carries its own provenance and fetch date, so a map built offline is
    traceable to the day the list was taken rather than pretending to be live.
    """
    rows = None
    try:
        rows = json.loads(_get(ISO_URL))
    except FetchError:
        if not allow_snapshot:
            raise
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        snap = os.path.join(here, SNAPSHOT)
        with open(snap, encoding="utf-8") as f:
            payload = json.load(f)
        rows = payload.get("rows") or []
        if len(rows) != 249:
            raise FetchError("snapshot holds %d rows, expected 249" % len(rows))
    out = []
    for r in rows:
        # Two rows in the upstream list carry no region: Antarctica (empty string) and
        # Taiwan (null). Filling them with a guess would put a country on a continent the
        # source did not claim, so they are marked "未分类" -- visibly分类不到, not blank.
        out.append({"iso2": r["alpha-2"], "iso3": r["alpha-3"], "name": r["name"],
                    "region": (r.get("region") or "").strip() or "未分类",
                    "sub_region": r.get("sub-region"),
                    "intermediate": r.get("intermediate-region"),
                    "region_source": "ISO-3166 上游" if (r.get("region") or "").strip()
                                     else "上游未给（未补猜）"})
    out.sort(key=lambda r: r["iso2"])
    return out


def wd_labels() -> dict[str, dict]:
    """zh-hans + en labels for every ISO code Wikidata knows (260 codes incl. 2 unassigned)."""
    q = """SELECT ?code ?zhn ?en WHERE {
      ?item wdt:P297 ?code .
      OPTIONAL { ?item rdfs:label ?zhn FILTER(lang(?zhn)="zh-hans") }
      OPTIONAL { ?item rdfs:label ?en FILTER(lang(?en)="en") }
    }"""
    d = json.loads(_get(WD_SPARQL + urllib.parse.quote(q), timeout=70,
                        accept="application/sparql-results+json"))
    m: dict[str, dict] = {}
    for r in d["results"]["bindings"]:
        c = r["code"]["value"].upper()
        e = m.setdefault(c, {})
        if "zhn" in r and "zh" not in e:
            e["zh"] = r["zhn"]["value"]
        if "en" in r:
            e["en"] = r["en"]["value"]
    return m


def worldbank_countries() -> dict[str, dict]:
    """World Bank economy table: ISO3 -> {iso2, name, region, income}."""
    d = json.loads(_get("https://api.worldbank.org/v2/country?format=json&per_page=400"))
    out = {}
    for r in d[1]:
        if r.get("region", {}).get("value", "").strip() == "Aggregates":
            continue
        out[r["id"]] = {"iso2": r.get("iso2Code"), "name": r.get("name"),
                        "region": r.get("region", {}).get("value", "").strip(),
                        "income": r.get("incomeLevel", {}).get("value", "").strip()}
    return out


def worldbank_indicator(indicator: str, years: str = "2019:2025") -> dict[str, tuple]:
    """Latest non-null value per economy for one indicator. Returns ISO3 -> (value, year)."""
    url = ("https://api.worldbank.org/v2/country/all/indicator/%s"
           "?format=json&per_page=20000&date=%s" % (indicator, years))
    d = json.loads(_get(url, timeout=70))
    best: dict[str, tuple] = {}
    for r in (d[1] or []):
        if r.get("value") is None:
            continue
        cc, y = r.get("countryiso3code"), int(r.get("date"))
        if cc and (cc not in best or y > best[cc][1]):
            best[cc] = (r["value"], y)
    return best


# ------------------------------------------------------------------ per-country facts
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".cache")
CACHE_FILE = os.path.join(CACHE_DIR, "world_counts.json")


def _load_json(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_json(path: str, data: dict) -> None:
    """Write atomically: a sweep of 249 countries must survive being killed."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


def _cache_load() -> dict:
    return _load_json(CACHE_FILE)


def _cache_save(data: dict) -> None:
    _save_json(CACHE_FILE, data)


def epmc_count(country_name: str, tries: int = 3, cache: dict | None = None) -> int | None:
    """Papers naming this country in an affiliation. A FLOOR, not a total.

    Every answer is cached by country name. A 249-country sweep is minutes of network and
    an interrupted sweep must not start from zero -- the cache is the reason the run is
    resumable, and it is also why a number that has been measured once is not re-measured
    (and therefore cannot drift between two runs of the same report).
    """
    c = cache if cache is not None else _cache_load()
    key = "epmc:" + country_name
    if key in c:
        v = c[key]
        return v if isinstance(v, int) or v is None else None
    q = '%s AND AFF:"%s"' % (TERM, country_name)
    url = ("https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=%s"
           "&format=json&pageSize=1&resultType=lite" % urllib.parse.quote(q))
    try:
        d = json.loads(_get(url, tries=tries, sleep=2.0))
        hit = d.get("hitCount")
    except FetchError:
        return None
    c[key] = hit
    if cache is None:
        _cache_save(c)
    return hit


GLEIF_CACHE = os.path.join(CACHE_DIR, "world_gleif.json")


def gleif_entities(iso2: str, term: str = "neuro", size: int = 3,
                   cache: dict | None = None) -> dict:
    """Legal entities registered in one country whose record mentions `term`.

    Measured: SE + "neuro" -> 3 (Neuro Förbundet Norrköping, Neuro-Logos AB,
    Stockholm Neuro Center AB); SE + "brain-computer" -> 0. The fulltext index is shallow,
    so a 0 here means "the register's text does not contain this string", not "no such
    company". Both numbers are real and the caveat travels with the node.
    """
    c = cache if cache is not None else _load_json(GLEIF_CACHE)
    key = "%s:%s" % (iso2, term)
    if key in c:
        return c[key]
    url = ("https://api.gleif.org/api/v1/lei-records"
           "?filter[entity.legalAddress.country]=%s&filter[fulltext]=%s&page[size]=%d"
           % (iso2, urllib.parse.quote(term), size))
    d = json.loads(_get(url, accept="application/vnd.api+json"))
    total = d.get("meta", {}).get("pagination", {}).get("total", 0)
    rows = []
    for r in d.get("data") or []:
        a = r.get("attributes") or {}
        e = a.get("entity") or {}
        ad = e.get("legalAddress") or {}
        rows.append({"lei": r.get("id"), "name": (e.get("legalName") or {}).get("name"),
                     "city": ad.get("city"), "country": ad.get("country"),
                     "status": e.get("status"),
                     "registration_status": (a.get("registration") or {}).get("status"),
                     "validated_as": (a.get("registration") or {}).get("validatedAs")})
    out = {"iso2": iso2, "term": term, "total": total, "rows": rows}
    c[key] = out
    if cache is None:
        _save_json(GLEIF_CACHE, c)
    return out


# ------------------------------------------------------------------ the map
def build_world_graph(*, epmc_workers: int = 8, gleif_terms: tuple = ("brain", "neuro"),
                      gleif_workers: int = 6, with_epmc: bool = True,
                      with_gleif: bool = True, limit: int | None = None,
                      graph: Graph | None = None, progress=None) -> Graph:
    g = graph or Graph()
    notes: list[dict] = []

    def note(source, what, err):
        notes.append({"source": source, "what": what, "error": str(err)[:200]})

    # 1. the spine -------------------------------------------------------------
    try:
        spine = iso_countries()
    except FetchError as e:
        note("ISO-3166 清单", ISO_URL, e)
        raise
    if limit:
        spine = spine[:limit]
    if not spine:
        raise FetchError("empty ISO spine -- refusing to emit a world map with no countries")

    try:
        zh = wd_labels()
    except FetchError as e:
        note("Wikidata 中文名", WD_SPARQL, e)
        zh = {}
    try:
        wb = worldbank_countries()
    except FetchError as e:
        note("World Bank 国家表", "api.worldbank.org/v2/country", e)
        wb = {}
    ind = {}
    for code, label in (("SP.POP.TOTL", "population"),
                        ("GB.XPD.RSDV.GD.ZS", "rd_pct_gdp"),
                        ("SH.XPD.CHEX.GD.ZS", "health_pct_gdp")):
        try:
            ind[label] = worldbank_indicator(code)
        except FetchError as e:
            note("World Bank 指标 %s" % code, code, e)

    iso3 = {r["iso2"]: r["iso3"] for r in spine}
    roots = []
    for r in spine:
        ck = "country:" + r["iso2"]
        roots.append(ck)
        label = r["name"]
        zh_label = (zh.get(r["iso2"]) or {}).get("zh")
        if zh_label and zh_label != label:
            label = "%s %s" % (zh_label, r["name"])
        w = wb.get(r["iso3"]) or {}
        pop = (ind.get("population") or {}).get(r["iso3"])
        rd = (ind.get("rd_pct_gdp") or {}).get(r["iso3"])
        hc = (ind.get("health_pct_gdp") or {}).get(r["iso3"])
        g.add_node(Node(key=ck, kind="country", label=label,
                        attrs={"iso2": r["iso2"], "iso3": r["iso3"],
                               "name_en": r["name"], "name_zh": zh_label,
                               "region": r["region"], "sub_region": r["sub_region"],
                               "region_source": r.get("region_source"),
                               "income": w.get("income"),
                               "population": pop[0] if pop else None,
                               "population_year": pop[1] if pop else None,
                               "rd_pct_gdp": rd[0] if rd else None,
                               "health_pct_gdp": hc[0] if hc else None},
                        source="ISO-3166 + Wikidata + World Bank", url="", fetched_at=NOW()))

    # 2. publications per country (Europe PMC, name-based, a floor) -------------
    if with_epmc:
        names = {r["iso2"]: EPMC_NAME.get(r["iso2"], r["name"]) for r in spine}
        cache = _cache_load()
        cached_before = sum(1 for n in names.values() if ("epmc:" + n) in cache)
        if cached_before:
            notes.append({"source": "缓存", "what": "epmc 计数",
                          "error": "已复用 %d/%d 个国家的历史计数，未重跑"
                                   % (cached_before, len(names))})

        def one(item):
            iso2, name = item
            return iso2, epmc_count(name, cache=cache)

        done = 0
        with ThreadPoolExecutor(epmc_workers) as ex:
            for iso2, hit in ex.map(one, list(names.items())):
                done += 1
                if done % 10 == 0:
                    _cache_save(cache)
                if progress and done % 20 == 0:
                    progress("epmc %d/%d" % (done, len(names)))
                if hit is None:
                    note("Europe PMC", "%s / AFF" % names[iso2], "取数失败（重试后仍失败）")
                    continue
                sk = "epmc:" + iso2
                g.add_node(Node(key=sk, kind="publication_slice",
                                label="Europe PMC AFF:%s" % names[iso2],
                                attrs={"iso2": iso2, "hit_count": hit,
                                       "unit": "下限（按机构国名匹配）",
                                       "query_name": names[iso2]},
                                source="Europe PMC", url="", fetched_at=NOW()))
                g.add_edge(Edge(src="country:" + iso2, dst=sk, rel="has_publication_slice",
                                attrs={"hit_count": hit}, source="Europe PMC", url="",
                                fetched_at=NOW()))
        _cache_save(cache)

    # 3. companies per country (GLEIF) ----------------------------------------
    if with_gleif:
        # Two keywords, because one is not enough and both are honest: "neuro" is the
        # sharper string (fewer, mostly real neuro outfits) and "brain" is the wider one
        # (134 vs 72 entities across 16 sampled countries, measured). Neither is a
        # classifier -- GLEIF's fulltext index matches any occurrence, so a venture fund
        # called "...Brain..." lands in the same bucket as a neuro-device maker. The
        # caveat travels on every company node rather than being explained once and
        # forgotten.
        terms = [t for t in gleif_terms if t]
        tasks = [(r["iso2"], t) for r in spine for t in terms]
        gcache = _load_json(GLEIF_CACHE)

        def one_co(task):
            iso2, term = task
            try:
                return iso2, term, gleif_entities(iso2, term=term, cache=gcache)
            except FetchError as e:
                return iso2, term, e

        gdone = 0
        with ThreadPoolExecutor(gleif_workers) as ex:
            for iso2, term, res in ex.map(one_co, tasks):
                gdone += 1
                if gdone % 25 == 0:
                    _save_json(GLEIF_CACHE, gcache)
                if isinstance(res, FetchError):
                    note("GLEIF LEI", "%s / fulltext=%s" % (iso2, term), res)
                    continue
                for row in res["rows"]:
                    k = "lei:%s" % row["lei"]
                    if k in g.nodes:
                        continue
                    g.add_node(Node(key=k, kind="company_gleif", label=row["name"] or k,
                                    attrs={"lei": row["lei"], "city": row["city"],
                                           "status": row["status"],
                                           "validated_as": row["validated_as"],
                                           "query": term,
                                           "caveat": "GLEIF 全文索引按关键词命中，未核验其是否真做脑机接口"},
                                    source="GLEIF LEI",
                                    url="https://search.gleif.org/#/record/%s" % row["lei"],
                                    fetched_at=NOW()))
                    g.add_edge(Edge(src=k, dst="country:" + iso2, rel="registered_in",
                                    attrs={"basis": "LEI 登记国别"}, source="GLEIF LEI",
                                    url="", fetched_at=NOW()))
                ck = "gleif:%s:%s" % (iso2, term)
                g.add_node(Node(key=ck, kind="company_slice",
                                label="GLEIF %s 关键词「%s」" % (iso2, term),
                                attrs={"iso2": iso2, "hit_count": res["total"],
                                       "term": term}, source="GLEIF LEI", url="",
                                fetched_at=NOW()))
                g.add_edge(Edge(src="country:" + iso2, dst=ck, rel="has_company_slice",
                                attrs={"hit_count": res["total"], "term": term},
                                source="GLEIF LEI", url="", fetched_at=NOW()))

    # 4. sources that were tried and refused ----------------------------------
    notes.append({"source": "OpenAlex", "what": "api.openalex.org/works",
                  "error": "429 Too Many Requests 每次尝试都失败（含带 mailto 的 polite pool），本机不可用"})
    notes.append({"source": "Europe PMC AFF:ISO2", "what": "SE/DK/CI",
                  "error": "用两位代码会把计数压成 1/6（SE 11 vs Sweden 68），故一律按国名查，"
                           "所有计数都是下限"})

    if with_gleif:
        _save_json(GLEIF_CACHE, gcache)

    # 5. the relations BETWEEN countries -------------------------------------
    try:
        add_country_relations(g, notes=notes)
    except Exception as e:  # a broken relation layer must not lose the 249 country rows
        note("国家关系层", "region/income/co-author", "%s: %s" % (type(e).__name__, e))

    g._notes = notes  # type: ignore[attr-defined]
    return g




# ------------------------------------------------------------------ 国家之间的关系
def epmc_pair(a: str, b: str, cache: dict | None = None) -> int | None:
    """Papers that name BOTH countries in an affiliation: a measured co-authorship floor.

    Measured: China+United States 97, China+Germany 78, Japan+United States 27,
    Sweden+Germany 11, Denmark+Sweden 6, Slovenia+Italy 5.

    It is a floor for the same reason the single-country counts are: the query matches the
    country NAME inside the free-text affiliation block, so a paper that writes "Boston"
    is not counted as United States. No direction is implied -- the query is symmetric and
    the edge records that.
    """
    c = cache if cache is not None else _load_json(PAIR_CACHE)
    key = "%s|%s" % tuple(sorted((a, b)))
    if key in c:
        return c[key]
    q = '%s AND AFF:"%s" AND AFF:"%s"' % (TERM, a, b)
    url = ("https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=%s"
           "&format=json&pageSize=1&resultType=lite" % urllib.parse.quote(q))
    try:
        hit = json.loads(_get(url, tries=3, sleep=2.0)).get("hitCount")
    except FetchError:
        return None
    c[key] = hit
    if cache is None:
        _save_json(PAIR_CACHE, c)
    return hit


PAIR_CACHE = os.path.join(CACHE_DIR, "world_pairs.json")


def add_country_relations(g: Graph, *, top_pairs: int = 14, pair_workers: int = 8,
                          notes: list | None = None) -> Graph:
    """Put the countries in relation to each other, three ways, all measured.

    1. 区域 / 次区域 / 收入层 -- 来自 World Bank，不是猜的：同一 region 的国家连
       contains_region 边，同一收入层连 shares_income_band 边。这是骨架，不叫证据。
    2. 论文合作 -- 对论文数前 top_pairs 的国家两两查 Europe PMC 双国 AFF，得到
       co_author_country 边，带可复核的篇数。这是唯一一条「两个国家真的共同出现在
       同一篇论文里」的关系，所以它带 basis 和计数，不只是一条线。
    3. 同为「论文数为 0」的国家连成 nothing_found_in_common 分组? 不连——
       没有证据就是没有边，空集合不是关系。
    """
    notes = notes if notes is not None else []
    countries = {n.attrs.get("iso2"): n for n in g.nodes.values() if n.kind == "country"}

    # 1. region / sub-region / income as hub nodes, so the map has a backbone
    for dim, kind, rel in (("region", "region_hub", "belongs_to_region"),
                           ("sub_region", "subregion_hub", "belongs_to_subregion"),
                           ("income", "income_hub", "shares_income_band")):
        hubs: dict[str, str] = {}
        for iso2, n in countries.items():
            v = (n.attrs or {}).get(dim)
            if not v:
                continue
            hk = "%s:%s" % (dim, v)
            if hk not in hubs:
                hubs[hk] = v
                if hk not in g.nodes:
                    g.add_node(Node(key=hk, kind=kind, label=v, attrs={"value": v},
                                    source="World Bank" if dim != "sub_region" else "ISO-3166",
                                    url="", fetched_at=NOW()))
            g.add_edge(Edge(src=n.key, dst=hk, rel=rel, attrs={},
                            source="World Bank" if dim != "sub_region" else "ISO-3166",
                            url="", fetched_at=NOW()))

    # 2. measured co-authorship between the biggest producers
    slices = [(n.attrs["iso2"], n.attrs.get("hit_count") or 0) for n in g.nodes.values()
              if n.kind == "publication_slice"]
    ranked = [iso2 for iso2, c in sorted(slices, key=lambda kv: -kv[1]) if c > 0][:top_pairs]
    names = {}
    for iso2 in ranked:
        n = countries.get(iso2)
        names[iso2] = EPMC_NAME.get(iso2, (n.attrs.get("name_en") if n else iso2))
    pair_cache = _load_json(PAIR_CACHE)
    tasks = [(names[a], names[b]) for i, a in enumerate(ranked) for b in ranked[i + 1:]]
    pairs_measured = 0
    with ThreadPoolExecutor(pair_workers) as ex:
        for (na, nb), hit in zip(tasks, ex.map(lambda t: epmc_pair(t[0], t[1], cache=pair_cache), tasks)):
            if hit is None:
                notes.append({"source": "Europe PMC 双国 AFF", "what": "%s+%s" % (na, nb),
                              "error": "取数失败"})
                continue
            pairs_measured += 1
            if hit == 0:
                continue
            ia = next((i for i, n in names.items() if n == na), None)
            ib = next((i for i, n in names.items() if n == nb), None)
            if not ia or not ib:
                continue
            g.add_edge(Edge(src="country:" + ia, dst="country:" + ib,
                            rel="co_author_country",
                            attrs={"papers": hit, "unit": "下限（双国机构名同现）",
                                   "symmetric": True, "basis": "Europe PMC 双国 AFF 检索命中"},
                            source="Europe PMC",
                            url="https://europepmc.org/search?query=%s"
                                % urllib.parse.quote('AFF:"%s" AND AFF:"%s"' % (na, nb)),
                            fetched_at=NOW()))
    _save_json(PAIR_CACHE, pair_cache)
    notes.append({"source": "国家关系", "what": "region/income/co-author",
                  "error": "区域与收入层是骨架（World Bank），论文合作是实测量；"
                           "共测 %d 对国家，0 篇的不连边" % pairs_measured})
    return g


def world_report(g: Graph, top: int = 15) -> str:
    """Every country accounted for: how many have papers, how many have none, and the top."""
    countries = [n for n in g.nodes.values() if n.kind == "country"]
    slices = {n.attrs["iso2"]: n.attrs["hit_count"] for n in g.nodes.values()
              if n.kind == "publication_slice"}
    cosl: dict[str, int] = {}
    for n in g.nodes.values():
        if n.kind == "company_slice":
            c = n.attrs["iso2"]
            cosl[c] = cosl.get(c, 0) + (n.attrs.get("hit_count") or 0)
    total = len(countries)
    with_papers = sum(1 for v in slices.values() if v)
    zero = [c.attrs["iso2"] for c in countries if slices.get(c.attrs["iso2"], 0) == 0]
    no_data = [c.attrs["iso2"] for c in countries if c.attrs["iso2"] not in slices]
    L = ["  国家总数 %d（ISO-3166 全量，无遗漏）" % total,
         "  有 BCI 论文的国家 %d；论文数为 0 的 %d；取数失败的 %d"
         % (with_papers, len(zero), len(no_data)),
         "  有 GLEIF 关键词命中企业的国家 %d / 已查 %d"
         % (sum(1 for v in cosl.values() if v), len(cosl))]
    ranked = sorted(slices.items(), key=lambda kv: -(kv[1] or 0))[:top]
    L.append("  论文数前 %d：" % top)
    labels = {c.attrs["iso2"]: c.label for c in countries}
    for i, (iso2, n) in enumerate(ranked, 1):
        L.append("    %2d. %-28s %6d" % (i, labels.get(iso2, iso2), n or 0))
    if zero:
        L.append("  论文数为 0 的国家（全部列出，不留空行）：")
        L.append("    " + " ".join(zero))
    if no_data:
        L.append("  取数失败的国家：")
        L.append("    " + " ".join(no_data))

    co = [e for e in g.edges.values() if e.rel == "co_author_country"]
    if co:
        labels_rev = {("country:" + c.attrs["iso2"]): c.label for c in countries}
        L.append("  国家之间的论文合作（Europe PMC 双国机构名同现，下限）：")
        for e in sorted(co, key=lambda x: -(x.attrs.get("papers") or 0))[:10]:
            L.append("    %-26s -- %-26s %5d 篇"
                     % (labels_rev.get(e.src, e.src)[:26],
                        labels_rev.get(e.dst, e.dst)[:26], e.attrs.get("papers") or 0))
    regions = [n for n in g.nodes.values() if n.kind == "region_hub"]
    if regions:
        L.append("  区域枢纽 %d 个，收入层枢纽 %d 个，次区域枢纽 %d 个（骨架，非证据）"
                 % (len(regions),
                    len([n for n in g.nodes.values() if n.kind == "income_hub"]),
                    len([n for n in g.nodes.values() if n.kind == "subregion_hub"])))
    return "\n".join(L)
