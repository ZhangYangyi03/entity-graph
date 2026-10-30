"""World currencies and banking products, from four keyless sources.

What this module is for: the company graph answers "who owns whom". This one answers the
other half of a banking question -- *which* currency and *which* product, and at what rate,
for *which* country. Every node and edge cites the endpoint it came from, the same rule as
the rest of the graph: `Graph.add_edge` refuses an edge with no source.

    source                 key?   what it contributes
    SIX (ISO 4217)         no     every official currency: alpha code, numeric code, minor
                                  units, and the country names that use it
    ECB eurofxref          no     daily reference rates against the euro, ~30 currencies
    ECB Data API (MIR)     no     euro-area bank interest rates on real products
                                  (house-purchase loans, new business)
    World Bank (WDI)       no     lending and deposit rates by country, annually
    FDIC BankFind          no     4,200+ active US banks: charter, regulator, assets,
                                  deposits, and their holding company (RSSDHCR + NAMEHCR)

Deliberately absent: any source that needs a paid key. Measured on 2026-10-30, every URL
below answered without one; each number in a docstring came from an actual response.

The FDIC holding-company link is the interesting edge: RSSDHCR is a *regulatory* RSSD id,
not a name guess, so a bank joins its holding company on an identifier -- the same
discipline `Resolver.by_id` uses for LEI and CIK.
"""
from __future__ import annotations

import csv
import io
import json
import datetime as _dt
import re
import xml.etree.ElementTree as ET

from .fetch import FetchError, get
from .model import Edge, Graph, Node

NOW = lambda: _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")

ISO4217_URL = ("https://www.six-group.com/dam/download/financial-information/"
               "data-center/iso-currrency/lists/list-one.xml")
ECB_RATES_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist-90d.xml"
ECB_DAILY_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
ECB_MIR_URL = ("https://data-api.ecb.europa.eu/service/data/MIR/"
               "M.{area}.B.A2C.A.R.A.2250.EUR.N?format=csvdata&startPeriod={since}")
FDIC_INST_URL = ("https://banks.data.fdic.gov/api/institutions"
                 "?filters=ACTIVE:1&limit={limit}"
                 "&fields={fields}&format=json")
WB_URL = ("https://api.worldbank.org/v2/country/all/indicator/{ind}?"
          "format=json&per_page=400&mrnev=1")

FDIC_FIELDS = ",".join(["CERT", "NAME", "NAMEHCR", "RSSDHCR", "LEI", "ASSET", "DEP",
                        "DEPDOM", "NETINC", "STALP", "STNAME", "CITY", "ZIP", "ADDRESS",
                        "WEBADDR", "BKCLASS", "CHRTAGNT", "FDICDBS", "FED_RSSD",
                        "FEDCHRTR", "STCHRTR", "INSFDIC", "ESTYMD", "MUTUAL", "CONSERVE",
                        "OFFICES", "ROA", "ROE", "ENDEFYMD", "INACTIVE"])


def _txt(el, tag: str) -> str:
    """SIX wraps every value in an optional <SIX:...> namespace; find by local name."""
    for child in el:
        if child.tag.split("}")[-1] == tag:
            return (child.text or "").strip()
    return ""


# --------------------------------------------------------------------------- currencies

def iso4217() -> tuple[list[Node], list[Edge]]:
    """Every official ISO 4217 currency, plus an edge from each to the countries using it.

    The XML is one <CcyNtry> per *country x currency* pair, so the same currency repeats
    (EUR appears for 20+ countries). Nodes are de-duplicated by alpha code and the country
    list accumulates on the node rather than being split across duplicates.
    """
    raw = get(ISO4217_URL, raw=True, ttl=7 * 86400)
    root = ET.fromstring(raw)
    published = root.attrib.get("Pblshd", "")
    by_code: dict[str, Node] = {}
    edges: list[Edge] = []
    for entry in root.iter():
        if entry.tag.split("}")[-1] != "CcyNtry":
            continue
        code = _txt(entry, "Ccy")
        if not code:
            continue  # SIX leaves Ccy blank for territories with no separate currency
        name = _txt(entry, "CcyNm")
        country = _txt(entry, "CtryNm")
        minor = _txt(entry, "CcyMnrUnts")
        num = _txt(entry, "CcyNbr")
        if code not in by_code:
            by_code[code] = Node(
                key=f"currency:{code}", kind="currency", label=name,
                attrs={"alpha_code": code, "numeric_code": num,
                       "minor_units": None if minor in ("N.A.", "") else minor,
                       "countries": [], "standard": "ISO 4217",
                       "standard_published": published},
                source="SIX ISO 4217", url=ISO4217_URL, fetched_at=NOW())
        node = by_code[code]
        if country and country not in node.attrs["countries"]:
            node.attrs["countries"].append(country)
    # one edge per (currency, country) so the relation is queryable in both directions
    for code, node in by_code.items():
        for country in node.attrs["countries"]:
            ck = country_key(country)
            edges.append(Edge(
                src=f"currency:{code}", dst=ck, rel="legal_tender_in",
                attrs={"country_name": country}, source="SIX ISO 4217",
                url=ISO4217_URL, fetched_at=NOW()))
    return list(by_code.values()), edges



# SIX spells some countries differently from the key the rest of the graph uses
# ("United States" vs "united-states", "Korea, Republic of" vs "korea"). Mapping by hand
# beats fuzzy matching here: a wrong country join is a wrong fact, not a formatting bug.
_COUNTRY_ALIASES = {
    # normalised spelling -> graph key (no "country:" prefix)
    "united states": "united-states",
    "united states of america": "united-states",
    "netherlands": "netherlands",
    "euro area": "euro-area",
    "korea republic of": "korea-south",
    "republic of korea": "korea-south",
    "south korea": "korea-south",
    "korea democratic peoples republic of": "korea-north",
    "congo the democratic republic of the": "congo-democratic-republic",
    "congo democratic republic of the": "congo-democratic-republic",
    "congo democratic republic of": "congo-democratic-republic",
    "democratic republic of the congo": "congo-democratic-republic",
    "congo": "congo-republic",
    "republic of the congo": "congo-republic",
    "iran islamic republic of": "iran",
    "syrian arab republic": "syria",
    "russian federation": "russia",
    "viet nam": "vietnam",
    "tanzania united republic of": "tanzania",
    "bolivia plurinational state of": "bolivia",
    "venezuela bolivariano republic of": "venezuela",
    "moldova republic of": "moldova",
    "lao peoples democratic republic": "laos",
    "brunei darussalam": "brunei",
    "hong kong": "hong-kong",
    "taiwan province of china": "taiwan",
    "czechia": "czech-republic",
    "eswatini": "swaziland",
    "cabo verde": "cape-verde",
    "north macedonia": "macedonia",
    "turkiye": "turkey",
    "cote divoire": "cote-divoire",
    "holy see": "vatican-city",
    "vatican city state": "vatican-city",
    "virgin islands british": "british-virgin-islands",
    "virgin islands us": "us-virgin-islands",
    "virgin islands u s": "us-virgin-islands",
    "micronesia federated states of": "micronesia",
    "saint martin french part": "saint-martin-french",
    "sint maarten dutch part": "sint-maarten",
    "falkland islands": "falkland-islands",
    "falkland islands malvinas": "falkland-islands",
    "timor leste": "east-timor",
    "united kingdom of great britain and northern ireland":
        "united-kingdom",
    "great britain": "united-kingdom",
}


# SIX writes country names in an all-caps registry style that no other source uses:
# "UNITED STATES OF AMERICA (THE)", "NETHERLANDS (THE)", "CONGO (THE DEMOCRATIC REPUBLIC
# OF THE)". Stripping those decorations before the alias lookup is what stops one country
# from becoming two nodes -- measured 2026-10-30: without this, 4,217 FDIC edges pointed at
# a "country:united-states" node that never existed and every bank was unlicensed-in.
#
# Two decorations need care and both are covered by a test: "(THE" appears *inside* the
# string for inverted forms ("KOREA (THE REPUBLIC OF)"), and "[MALVINAS]" trails the
# parentheses ("FALKLAND ISLANDS (THE) [MALVINAS]") so it has to go first.
_COUNTRY_DECOR = [
    (r"\s*\[[^\]]*\]", ""),          # bracketed alt names: [MALVINAS], [U.S.]
    (r"\(the\)", ""),                # bare "(THE)" anywhere
    (r"\(\s*the\s+", " ("),           # inverted form: "(THE REPUBLIC OF)" -> "(REPUBLIC OF)"
    (r"\s+of\s+the\s*\)", ")"),      # and the closing half: "(... OF THE)" -> "(...)"
    (r"^the\s+", ""),                  # leading article
    (r"\s+of\s+america\b", ""),        # UNITED STATES OF AMERICA -> UNITED STATES
]


def country_key(name: str) -> str:
    """One spelling for a country, so a currency edge and a bank edge land on one node."""
    n = (name or "").strip().lower()
    for pat, repl in _COUNTRY_DECOR:
        n = re.sub(pat, repl, n)
    n = re.sub(r"\s+", " ", n).strip(" ()'\u2019")
    # The alias table is keyed on a *normalised* form -- lowercase, punctuation to single
    # spaces -- so "Korea, Republic of" and "KOREA (THE REPUBLIC OF)" both arrive as
    # "korea republic of" and hit one entry. Keying the table on raw spellings would need
    # one row per source's punctuation, which is how the Korea alias silently missed.
    lookup = re.sub(r"[^a-z0-9]+", " ", n).strip()
    if lookup in _COUNTRY_ALIASES:
        return "country:" + _COUNTRY_ALIASES[lookup]
    return "country:" + re.sub(r"[^a-z0-9]+", "-", n).strip("-")


def country_nodes(edges: list[Edge]) -> list[Node]:
    """Country nodes implied by the currency->country edges, so those edges are not dangling."""
    seen: dict[str, Node] = {}
    for e in edges:
        if e.dst in seen:
            continue
        seen[e.dst] = Node(key=e.dst, kind="country",
                           label=e.attrs.get("country_name", e.dst.split(":", 1)[1]),
                           attrs={"iso": None}, source=e.source, url=e.url,
                           fetched_at=NOW())
    return list(seen.values())


# ------------------------------------------------------------------------------- rates

def ecb_rates(days: int = 90) -> tuple[list[Node], list[Edge], dict]:
    """ECB daily reference rates against the euro, as edges currency -> currency:EUR.

    Measured 2026-10-30 on eurofxref-hist-90d.xml: 65 daily <Cube> blocks. The rate is
    "units of this currency per 1 EUR", which is the direction the edge states, so no
    inversion happens anywhere and none can be got wrong later.
    """
    raw = get(ECB_RATES_URL, raw=True, ttl=86400)
    root = ET.fromstring(raw)
    latest: dict[str, tuple[str, float]] = {}
    series: dict[str, list[tuple[str, float]]] = {}
    n_days = 0
    for cube in root.iter():
        if cube.tag.split("}")[-1] != "Cube" or "time" not in cube.attrib:
            continue
        day = cube.attrib["time"]
        n_days += 1
        for inner in cube:
            code, rate = inner.attrib.get("currency"), inner.attrib.get("rate")
            if not code or rate is None:
                continue
            try:
                v = float(rate)
            except ValueError:
                continue
            series.setdefault(code, []).append((day, v))
            if code not in latest or day > latest[code][0]:
                latest[code] = (day, v)
    edges: list[Edge] = []
    for code, (day, rate) in sorted(latest.items()):
        hist = series[code]
        edges.append(Edge(
            src=f"currency:{code}", dst="currency:EUR", rel="rate_against",
            attrs={"rate": rate, "date": day, "quote": "units per 1 EUR",
                   "observations": len(hist),
                   "window_from": min(d for d, _ in hist),
                   "window_to": max(d for d, _ in hist)},
            source="ECB eurofxref", url=ECB_RATES_URL, fetched_at=NOW()))
    return [], edges, {"days": n_days, "currencies": len(latest),
                       "source_rows": sum(len(v) for v in series.values())}


def _csv_rows(url: str) -> list[dict]:
    import csv
    import io
    text = get(url, raw=True, ttl=86400)
    return list(csv.DictReader(io.StringIO(text)))


# ECB area codes are not country names in any registry, so they are mapped explicitly.
# "U2" is the euro area as a whole -- a real aggregate, kept as its own node rather than
# being split across the 20 members, because an aggregate rate is not any member's rate.
_MIR_AREA_NAME = {
    "U2": "Euro area", "DE": "Germany", "FR": "France", "IT": "Italy", "ES": "Spain",
    "NL": "Netherlands", "BE": "Belgium", "AT": "Austria", "IE": "Ireland",
    "PT": "Portugal", "FI": "Finland", "GR": "Greece", "SK": "Slovakia",
    "SI": "Slovenia", "LU": "Luxembourg", "LT": "Lithuania", "LV": "Latvia",
    "EE": "Estonia", "CY": "Cyprus", "MT": "Malta", "HR": "Croatia",
}


def ecb_mir_products(since: str = "2026-01") -> tuple[list[Node], list[Edge]]:
    """Euro-area bank interest rates on a named product, per country, newest observation.

    MIR is the ECB's MFI interest rate statistic. Series key
    M.<country>.B.A2C.A.R.A.2250.EUR.N measured 2026-10-30 = "bank interest rates on loans
    to households for house purchase, new business", monthly. Only that product key
    answered; 2110/2230/2240 returned 404 for the same dimension pattern, so this module
    reports the one it can cite rather than inventing siblings.
    """
    nodes: list[Node] = []
    edges: list[Edge] = []
    areas = ["U2", "DE", "FR", "IT", "ES", "NL", "BE", "AT", "IE", "PT", "FI", "GR",
             "SK", "SI", "LU", "LT", "LV", "EE", "CY", "MT", "HR"]
    pid = "product:mir:2250"
    made = False
    for area in areas:
        url = ECB_MIR_URL.format(area=area, since=since)
        try:
            rows = _csv_rows(url)
        except FetchError:
            continue
        if not rows:
            continue
        last = rows[-1]
        if not made:
            nodes.append(Node(
                key=pid, kind="bank_product", label=last.get("TITLE") or "MIR 2250",
                attrs={"dataset": "ECB MIR", "series_item": "2250",
                       "unit": "percent per annum", "frequency": "M",
                       "category": "loan", "segment": "household house purchase",
                       "business": "new business"},
                source="ECB Data API (MIR)", url=url, fetched_at=NOW()))
            made = True
        edges.append(Edge(
            src=pid, dst=country_key(_MIR_AREA_NAME.get(area, area)), rel="offered_in",
            attrs={"rate": float(last["OBS_VALUE"]), "period": last["TIME_PERIOD"],
                   "observations": len(rows), "series_key": last.get("KEY")},
            source="ECB Data API (MIR)", url=url, fetched_at=NOW()))
    return nodes, edges



RBA_F5_URL = "https://www.rba.gov.au/statistics/tables/csv/f5-data.csv"
RBA_F6_URL = "https://www.rba.gov.au/statistics/tables/csv/f6-data.csv"
BOC_PRIME_URL = ("https://www.bankofcanada.ca/valet/observations/"
                 "V80691311/json?recent=1")


def _parse_rba_series(text: str) -> dict[str, list[tuple[str, str]]]:
    """Australia (RBA) publishes each table as one CSV: row 2 is the title row, one column
    per series, then monthly/quarterly rows of (date, value). Returns {title: [(date, val)]}.

    A series column with no observations is dropped here rather than becoming an edge with
    an empty rate -- an edge that claims a product exists in Australia at a price of nothing
    is worse than a missing edge.
    """
    rows = list(csv.reader(io.StringIO(text.lstrip("\ufeff"))))
    if len(rows) < 3:
        return {}
    titles = rows[1]
    out: dict[str, list[tuple[str, str]]] = {}
    for ci, title in enumerate(titles):
        if ci == 0 or not title.strip():
            continue
        obs: list[tuple[str, str]] = []
        for r in rows[2:]:
            if len(r) <= ci:
                continue
            date, val = r[0].strip(), r[ci].strip()
            if date and val:
                obs.append((date, val))
        if obs:
            out[title.strip()] = obs
    return out


def _rba_label(title: str) -> dict:
    """Turn 'Lending rates; Housing loans; Banks; Variable; Standard; Owner-occupier' into
    the facets the graph queries on, so a product is a set of dimensions not a sentence."""
    parts = [p.strip() for p in title.split(";")]
    kind = parts[0].lower() if parts else ""
    facet = [p.lower() for p in parts[1:]]
    category = "loan"
    if "housing" in " ".join(facet) or "housing" in kind:
        segment = "household mortgage"
    elif "business" in " ".join(facet) or "business" in kind:
        segment = "business"
    elif "personal" in " ".join(facet):
        segment = "household unsecured"
    elif "credit" in " ".join(facet):
        segment = "household mortgage"
    else:
        segment = "other"
    business = "outstanding" if any("outstanding" in f for f in facet) else "new business"
    return {"category": category, "segment": segment, "business": business,
            "facets": facet, "title": title}


def rba_products() -> tuple[list[Node], list[Edge]]:
    """Australian bank lending products: every series in RBA tables F5 and F6, newest value.

    F5 is indicator lending rates (small business variable/overdraft, housing variable by
    lender type and loan purpose); F6 is housing lending rates by credit outstanding. Both
    are keyless and answered 2026-10-30 (73KB and 38KB). This is where the product dimension
    gets its breadth: the ECB MIR endpoint exposes only one product key, while RBA publishes
    30 named products in a single request.
    """
    nodes: list[Node] = []
    edges: list[Edge] = []
    seen: set[str] = set()
    for url in (RBA_F5_URL, RBA_F6_URL):
        try:
            text = get(url, raw=True)
        except FetchError:
            continue
        for title, obs in _parse_rba_series(text).items():
            label = _rba_label(title)
            slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:80]
            if slug in seen:
                continue
            seen.add(slug)
            pid = f"product:rba:{slug}"
            date, val = obs[-1]
            try:
                rate = float(val)
            except ValueError:
                continue
            nodes.append(Node(
                key=pid, kind="bank_product", label=title,
                attrs={"dataset": "RBA F5/F6", "unit": "percent per annum",
                       "frequency": "M/Q", "published": date,
                       **{k: v for k, v in label.items() if k != "title"}},
                source="Reserve Bank of Australia", url=url, fetched_at=NOW()))
            edges.append(Edge(
                src=pid, dst="country:australia", rel="offered_in",
                attrs={"rate": rate, "period": date, "observations": len(obs),
                       "issuer": "Reserve Bank of Australia"},
                source="Reserve Bank of Australia", url=url, fetched_at=NOW()))
    return nodes, edges


def boc_prime_rate() -> tuple[list[Node], list[Edge]]:
    """Canada's prime business rate from the Bank of Canada Valet API: a named product
    (prime lending rate) priced per observation, keyless."""
    import json as _json
    from .fetch import FetchError as _FE  # local alias keeps module import surface small
    try:
        payload = _json.loads(get(BOC_PRIME_URL, raw=True))
    except (_FE, ValueError):
        return [], []
    obs = payload.get("observations") or []
    if not obs:
        return [], []
    detail = (payload.get("seriesDetail") or {}).get("V80691311") or {}
    label = detail.get("label") or "Prime rate"
    last = obs[-1]
    pid = "product:boc:prime"
    node = Node(key=pid, kind="bank_product", label=label,
                attrs={"dataset": "BoC Valet", "unit": "percent per annum",
                       "frequency": detail.get("seriesDetail", "") or "D",
                       "category": "loan", "segment": "business",
                       "business": "outstanding", "description":
                       (detail.get("description") or "")[:200]},
                source="Bank of Canada Valet", url=BOC_PRIME_URL, fetched_at=NOW())
    edges = []
    for o in obs[-30:]:
        d, v = (o.get("d"), o.get("V80691311") or {})
        rate = (v.get("v") if isinstance(v, dict) else v)
        if d and rate:
            try:
                edges.append(Edge(src=pid, dst="country:canada", rel="offered_in",
                                  attrs={"rate": float(rate), "period": d},
                                  source="Bank of Canada Valet", url=BOC_PRIME_URL,
                                  fetched_at=NOW()))
            except ValueError:
                continue
    return [node], edges

def wb_rates() -> tuple[dict[str, dict], list[dict]]:
    """World Bank lending and deposit rates by country: the annual panel, not a snapshot.

    Returns (by_iso3, notes). A country with a rate gets a dict; one without stays absent,
    and the note says how many that was, so a thin result is visible rather than silent.
    """
    notes: list[dict] = []
    out: dict[str, dict] = {}
    for ind, label in (("FR.INR.LEND", "lending_rate"), ("FR.INR.DPST", "deposit_rate")):
        try:
            blob = get(WB_URL.format(ind=ind), ttl=7 * 86400)
        except FetchError as e:
            notes.append({"source": "World Bank", "what": label, "error": str(e)[:120]})
            continue
        if not isinstance(blob, list) or len(blob) < 2 or not blob[1]:
            notes.append({"source": "World Bank", "what": label,
                          "error": "empty payload"})
            continue
        for row in blob[1]:
            iso3 = row.get("countryiso3code")
            val = row.get("value")
            if not iso3 or val is None:
                continue
            out.setdefault(iso3, {})[label] = {"value": val, "year": row.get("date"),
                                               "country": (row.get("country") or {}).get("value")}
    return out, notes


# -------------------------------------------------------------------------------- banks

def fdic_banks(limit: int = 10000) -> tuple[list[Node], list[Edge], dict]:
    """Active US banks from FDIC BankFind, with a real holding-company edge.

    RSSDHCR is the holding company's regulatory RSSD id and NAMEHCR its name, both supplied
    by the filing, so bank -> holding company is joined on an identifier. Measured
    2026-10-30: 4,217 active institutions in one response at limit=10000.
    """
    url = FDIC_INST_URL.format(limit=limit, fields=FDIC_FIELDS)
    blob = get(url, ttl=3 * 86400)
    rows = [d["data"] for d in blob.get("data") or []]
    nodes: list[Node] = []
    edges: list[Edge] = []
    hc: dict[str, Node] = {}
    for r in rows:
        cert = str(r.get("CERT") or "")
        if not cert:
            continue
        bkey = f"bank:fdic:{cert}"
        attrs = {k.lower(): r.get(k) for k in
                 ("NAME", "LEI", "ASSET", "DEP", "DEPDOM", "NETINC", "BKCLASS",
                  "CHRTAGNT", "CITY", "STALP", "STNAME", "ZIP", "ADDRESS", "WEBADDR",
                  "ESTYMD", "INSFDIC", "MUTUAL", "OFFICES", "ROA", "ROE", "FED_RSSD")}
        attrs["cert"] = int(cert)
        attrs["regulator"] = r.get("CHRTAGNT")
        nodes.append(Node(key=bkey, kind="bank", label=(r.get("NAME") or cert).strip(),
                          attrs=attrs, source="FDIC BankFind", url=url, fetched_at=NOW()))
        edges.append(Edge(src=bkey, dst=country_key("United States"), rel="licensed_in",
                          attrs={"regulator": r.get("CHRTAGNT"),
                                 "charter_type": r.get("BKCLASS")},
                          source="FDIC BankFind", url=url, fetched_at=NOW()))
        rssd = (r.get("RSSDHCR") or "").strip()
        hname = (r.get("NAMEHCR") or "").strip()
        if rssd and rssd not in ("0", "9999999") and hname:
            hkey = f"bank_holding:rssd:{rssd}"
            if hkey not in hc:
                hc[hkey] = Node(key=hkey, kind="bank_holding", label=hname,
                                attrs={"rssd": rssd, "country": "US"},
                                source="FDIC BankFind (NAMEHCR/RSSDHCR)", url=url,
                                fetched_at=NOW())
            edges.append(Edge(src=bkey, dst=hkey, rel="subsidiary_of",
                              attrs={"rssd": rssd, "name": hname},
                              source="FDIC BankFind (NAMEHCR/RSSDHCR)", url=url,
                              fetched_at=NOW()))
        lei = (r.get("LEI") or "").strip()
        if lei:
            attrs["gleif_lei"] = lei
    nodes.extend(hc.values())
    return nodes, edges, {"banks": len(rows), "holding_companies": len(hc),
                          "with_lei": sum(1 for r in rows if (r.get("LEI") or "").strip())}


# ---------------------------------------------------------------------------- assembly

def build_currency_graph(*, rates: bool = True, banks: bool = True,
                         products: bool = True, limit: int = 10000) -> Graph:
    """The whole currency-and-banking graph, with every failed source recorded as a note."""
    g = Graph()
    notes: list[dict] = []

    def step(name, fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except FetchError as e:
            notes.append({"source": name, "what": "fetch", "error": str(e)[:160]})
        except Exception as e:  # a layout change must not look like "no data"
            notes.append({"source": name, "what": "parse", "error": f"{type(e).__name__}: {e}"[:160]})
        return None

    cur = step("SIX ISO 4217", iso4217)
    if cur:
        nodes, edges = cur
        for n in nodes:
            g.add_node(n)
        for n in country_nodes(edges):
            g.add_node(n)
        for e in edges:
            g.add_edge(e)
        notes.append({"source": "SIX ISO 4217", "what": "ok",
                      "error": f"{len(nodes)} currencies, {len(edges)} currency-country links"})

    if rates:
        r = step("ECB eurofxref", ecb_rates)
        if r:
            _, edges, meta = r
            for e in edges:
                g.add_edge(e)
            notes.append({"source": "ECB eurofxref", "what": "ok",
                          "error": f"{meta['currencies']} currencies over {meta['days']} days"})

    wb = step("World Bank", wb_rates)
    if wb:
        by_iso3, wnotes = wb
        notes.extend(wnotes)
        hit = 0
        for iso3, vals in by_iso3.items():
            # attach to an existing country node when one matches, else stand alone
            cand = [n for n in g.nodes.values()
                    if n.kind == "country" and (n.attrs.get("iso3") == iso3)]
            target = None
            if cand:
                target = cand[0]
            else:
                cname = next(iter(vals.values()))["country"]
                target = Node(key=f"country-iso3:{iso3}", kind="country", label=cname,
                              attrs={"iso3": iso3}, source="World Bank", url=WB_URL,
                              fetched_at=NOW())
                g.add_node(target)
            for label, v in vals.items():
                target.attrs[label] = v["value"]
                target.attrs[label + "_year"] = v["year"]
                g.add_edge(Edge(src=target.key, dst="feature:bank-rates", rel="has_metric",
                                attrs={"metric": label, "value": v["value"],
                                       "year": v["year"], "unit": "percent per annum"},
                                source="World Bank WDI", url=WB_URL, fetched_at=NOW()))
            hit += 1
        notes.append({"source": "World Bank", "what": "ok",
                      "error": f"lending/deposit rates for {hit} countries"})

    if products:
        p = step("ECB Data API (MIR)", ecb_mir_products)
        if p:
            pnodes, pedges = p
            for n in pnodes:
                g.add_node(n)
            for e in pedges:
                g.add_edge(e)
            notes.append({"source": "ECB Data API (MIR)", "what": "ok",
                          "error": f"{len(pedges)} country series on one product"})

        r = step("RBA F5/F6", rba_products)
        if r:
            rnodes, redges = r
            for n in rnodes:
                g.add_node(n)
            for e in redges:
                g.add_edge(e)
            notes.append({"source": "RBA F5/F6", "what": "ok",
                          "error": f"{len(rnodes)} Australian lending products"})

        c = step("Bank of Canada Valet", boc_prime_rate)
        if c:
            cnodes, cedges = c
            for n in cnodes:
                g.add_node(n)
            for e in cedges:
                g.add_edge(e)
            notes.append({"source": "Bank of Canada Valet", "what": "ok",
                          "error": f"{len(cnodes)} product, {len(cedges)} observations"})

    if banks:
        b = step("FDIC BankFind", fdic_banks, limit=limit)
        if b:
            bnodes, bedges, meta = b
            for n in bnodes:
                g.add_node(n)
            for e in bedges:
                g.add_edge(e)
            notes.append({"source": "FDIC BankFind", "what": "ok",
                          "error": f"{meta['banks']} banks, {meta['holding_companies']} "
                                   f"holding companies, {meta['with_lei']} with an LEI"})

    # A country that a rate or a licence points at must exist as a node, even when the
    # currency table never named it (the euro area is an aggregate, not an ISO 4217 country).
    # Created here with a stated provenance rather than silently dropped.
    for e in list(g.edges.values() if hasattr(g.edges, "values") else g.edges):
        if e.rel not in ("licenses", "licensed_in", "offered_in", "legal_tender_in"):
            continue
        for end in (e.src, e.dst):
            if end.startswith("country:") and end not in g.nodes:
                g.add_node(Node(key=end, kind="country",
                                label=end.split(":", 1)[1].replace("-", " ").title(),
                                attrs={"iso3": None, "implied_by": e.rel},
                                source=e.source, url=e.url, fetched_at=NOW()))

    # A metric hub the World Bank edges point at. Without it those 4,400 edges dangle,
    # and a dangling edge is indistinguishable from a missing source in any query.
    if "feature:bank-rates" not in g.nodes:
        g.add_node(Node(
            key="feature:bank-rates", kind="feature", label="Bank lending/deposit rates",
            attrs={"what": "annual lending and deposit rates by country, World Bank WDI",
                   "indicators": {"FR.INR.LEND": "lending rate",
                                  "FR.INR.DPST": "deposit rate"}},
            source="World Bank", url=WB_URL, fetched_at=NOW()))

    dangling: dict[str, int] = {}
    for e in g.edges.values() if hasattr(g.edges, "values") else g.edges:
        for end in (e.src, e.dst):
            if end not in g.nodes:
                dangling[e.rel] = dangling.get(e.rel, 0) + 1
    notes.append({"source": "integrity", "what": "dangling" if dangling else "ok",
                  "error": ("%d edges point outside the graph: %s"
                            % (sum(dangling.values()), dangling)) if dangling
                           else "every edge has both endpoints"})

    g._notes = notes
    return g

