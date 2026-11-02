"""Who owns a satellite, and what that has to do with a company.

CelesTrak's satellite catalogue answers this partway: it carries OWNER (a state or an
operator code), LAUNCH_DATE, OBJECT_TYPE and the orbit. What it does not carry is the
operator's legal identity, the manufacturer, or the launch provider -- so the join into a
corporate entity graph is a *declared* mapping, not a guess. A code is mapped because a
document says so, and the mapping is data in this file with the reason beside it.

That distinction matters. An entity graph whose value is "every edge has a URL" cannot
start joining on a name similarity and call it ownership.
"""
from __future__ import annotations

import collections
from dataclasses import dataclass

# State codes are ISO-3166-ish alpha-3 as CelesTrak writes them; operator codes are their
# own vocabulary. Every entry below is a thing a reader can check, not an inference.
OWNER_ALPHA3 = {
    "US": "USA", "PRC": "CHN", "CIS": "RUS", "UK": "GBR", "JPN": "JPN", "IT": "ITA",
    "FR": "FRA", "IND": "IND", "GER": "DEU", "SKOR": "KOR", "ESA": "", "SPN": "ESP",
    "CA": "CAN", "TURK": "TUR", "AUS": "AUS", "FIN": "FIN", "BRA": "BRA", "MEX": "MEX",
    "ISRA": "ISR", "NOR": "NOR", "SWDN": "SWE", "NETH": "NLD", "POL": "POL", "ARGN": "ARG",
    "CHLE": "CHL", "PAKI": "PAK", "THAI": "THA", "VTNM": "VNM", "SAUD": "SAU", "UAE": "ARE",
    "EGYP": "EGY", "NIGR": "NGA", "KOR": "KOR", "TAIW": "TWN", "IRAN": "IRN",
}

# Operator codes that name a company rather than a state. These are the ones worth a node.
OPERATOR_NAMES = {
    "SES": "SES S.A.",
    "ITSO": "Intelsat",
    "O3B": "O3b Networks (SES)",
    "EUTE": "Eutelsat",
    "INMARSAT": "Inmarsat",
    "ORBCOMM": "ORBCOMM",
    "IRIDIUM": "Iridium Communications",
    "GLOBALSTAR": "Globalstar",
    "PLANET": "Planet Labs",
    "SPIRE": "Spire Global",
    "ICEYE": "ICEYE",
    "TBD": "",
}

# Satellite series prefixes that identify the operator even where OWNER is a state.
# Ordered longest-first so "STARLINK" is not matched by a shorter prefix first.
SERIES_OPERATOR = {
    "STARLINK": "SpaceX",
    "ONEWEB": "Eutelsat OneWeb",
    "QIANFAN": "Shanghai Spacecom (Qianfan / Thousand Sails)",
    "GUOWANG": "China SatNet (Guowang)",
    "HULIANWANG": "China SatNet (Guowang)",
    "IRIDIUM": "Iridium Communications",
    "GLOBALSTAR": "Globalstar",
    "ORBCOMM": "ORBCOMM",
    "PLANET": "Planet Labs",
    "SKYSAT": "Planet Labs",
    "FLOCK": "Planet Labs",
    "SPIRE": "Spire Global",
    "ICEYE": "ICEYE",
    "CAPELLA": "Capella Space",
}


def country_node(owner_code: str) -> str:
    """Graph key for a state owner. Empty when the code does not name a state."""
    iso3 = OWNER_ALPHA3.get((owner_code or "").upper(), "")
    return f"country:{iso3.lower()}" if iso3 else ""


def operator_of(object_name: str, owner_code: str) -> str:
    """The operator a catalogue row points at, from the series name first, then the code.

    Returns "" when neither says anything -- notably for "TBD", which CelesTrak uses for
    objects whose owner is not established. An empty answer is the honest one there.
    """
    up = (object_name or "").upper()
    for prefix, label in SERIES_OPERATOR.items():
        if up.startswith(prefix) or f" {prefix}" in up:
            return label
    return OPERATOR_NAMES.get((owner_code or "").upper(), "")


@dataclass
class CatalogueRow:
    name: str
    object_id: str
    object_type: str       # PAY | R/B | DEB | UNK
    owner: str
    launch_date: str
    decay_date: str
    apogee: float
    perigee: float
    period: float


def parse_rows(records) -> list[CatalogueRow]:
    out = []
    for r in records:
        def num(k):
            try:
                return float(r.get(k) or 0)
            except (TypeError, ValueError):
                return 0.0
        out.append(CatalogueRow(
            name=(r.get("OBJECT_NAME") or "").strip(),
            object_id=(r.get("OBJECT_ID") or "").strip(),
            object_type=(r.get("OBJECT_TYPE") or "").strip(),
            owner=(r.get("OWNER") or "").strip(),
            launch_date=(r.get("LAUNCH_DATE") or "").strip(),
            decay_date=(r.get("DECAY_DATE") or "").strip(),
            apogee=num("APOGEE"), perigee=num("PERIGEE"), period=num("PERIOD"),
        ))
    return out


def summary(rows: list[CatalogueRow]) -> dict:
    """What the catalogue actually says, as counts rather than adjectives."""
    on_orbit = [r for r in rows if not r.decay_date]
    by_type = collections.Counter(r.object_type for r in on_orbit)
    by_owner = collections.Counter(r.owner for r in on_orbit)
    by_operator = collections.Counter(operator_of(r.name, r.owner) for r in on_orbit)
    by_operator.pop("", None)
    years = collections.Counter(r.launch_date[:4] for r in on_orbit if r.launch_date)
    return {
        "on_orbit": len(on_orbit),
        "by_object_type": dict(by_type),
        "by_owner": dict(by_owner.most_common()),
        "by_operator": dict(by_operator.most_common()),
        "launches_by_year": dict(sorted(years.items())),
    }


def to_graph(rows: list[CatalogueRow]):
    """Satellite nodes plus owner and operator edges, every edge citing CelesTrak.

    No date is invented. LAUNCH_DATE is what the catalogue states, so valid_from comes from
    it; the launch provider and manufacturer are simply not in this source, and no edge is
    created for them.
    """
    from .model import Edge, Graph, Node

    SRC = "CelesTrak satcat"
    URL = "https://celestrak.org/satcat/records.php?GROUP=active&FORMAT=json"
    g = Graph()
    for r in rows:
        if r.decay_date:
            continue
        key = f"sat:{r.object_id}"
        g.add_node(Node(key=key, kind="satellite", label=r.name, source=SRC, url=URL,
                        attrs={"object_id": r.object_id, "object_type": r.object_type,
                               "owner_code": r.owner, "apogee_km": r.apogee,
                               "perigee_km": r.perigee, "period_min": r.period}))
        ck = country_node(r.owner)
        if ck:
            g.add_node(Node(key=ck, kind="country", label=r.owner, source=SRC, url=URL,
                            attrs={"owner_code": r.owner}))
            g.add_edge(Edge(src=key, dst=ck, rel="owned_by_state", source=SRC, url=URL,
                            attrs={"valid_from": r.launch_date} if r.launch_date else {}))
        op = operator_of(r.name, r.owner)
        if op:
            okey = "company:" + op.lower().replace(" ", "-").replace("(", "").replace(")", "").replace(".", "")
            g.add_node(Node(key=okey, kind="company", label=op, source=SRC, url=URL))
            g.add_edge(Edge(src=key, dst=okey, rel="operated_by", source=SRC, url=URL,
                            attrs={"valid_from": r.launch_date} if r.launch_date else {}))
    return g
