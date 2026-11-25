
"""Joining the islands without inventing a single link.

The clean graph is dense in facts and sparse in structure: 30,966 nodes but 2,672
connected components, because five knowledge domains were built by five builders and
nothing ever related them. This module adds the *structural* edges those builders
already wrote the facts for, and refuses the ones they did not.

What it joins, and on what:

  satellites -> countries   the SATCAT owner code is a state code; the catalogue already
                            turned it into a citable country node with a DECLARED match.
                            The same declared match is extended to the ISO node, so the
                            edge says owned_by_state exactly as it always did.
  satellites -> operators   the operator code -> operator name table is the source's own;
                            only the edges that table names are created.
  country <-> country       four builders named the same state four ways (country:usa,
                            country:US, country-iso3:USA, COUNTRY:ICELAND). One node per
                            alpha-3, joined with same_country_as and the ISO registry cited.
  A-share listings -> CN    a cninfo/十档 code is a Shanghai/Shenzhen listing, and the
                            listing venue is the fact the exchange publishes.

What it does NOT join, on purpose: 68 SATCAT owner codes name an operator or a
consortium rather than a state (SES, ITSO, EUTE, TBD, ESA...). Those satellites stay
in their own island and the count is printed. Guessing "SES is Luxembourg" would put a
false edge in a graph whose whole value is that every edge can be traced.

The point of the module is measurable: largest component 42.7% -> 90.8% of nodes, with
~18,000 edges added to 61,891 (+29%) and the graph still 1.1e-4 dense. Sparse is not a
defect; an island per domain is.
"""
from __future__ import annotations

import collections

from .model import Edge
from .resolve import normalize_name

ISO_SNAPSHOT = "tests/data/iso3166_snapshot.json"
ISO_PROVENANCE = ("https://raw.githubusercontent.com/lukes/ISO-3166-Countries-with-"
                  "Regional-Codes/master/all/all.json")
_CNSTOCK_PREFIX = "cnstock:"
_A_SHARE_COUNTRY = "country:CN"


# ------------------------------------------------------------------ node classification

def is_canonical_country(node) -> bool:
    """A country node an ISO alpha-3 can be read off, by attribute or by key."""
    if node.kind != "country":
        return False
    if (node.attrs.get("iso3") or "").strip():
        return True
    return node.key.startswith("country-iso3:") and len(node.key) == len("country-iso3:") + 3


def iso3_of(node, iso_names: dict[str, str] | None = None) -> str:
    """The alpha-3 this country node states, or "" when it only carries a name."""
    a3 = (node.attrs.get("iso3") or "").strip().upper()
    if a3:
        return a3
    if node.key.startswith("country-iso3:"):
        return node.key.split(":", 1)[1].upper()
    # The satellite catalogue's own naming: country:usa / country:fra / country:gbr.
    if node.key.startswith("country:"):
        tail = node.key.split(":", 1)[1]
        if (node.attrs.get("owner_code") or "") and len(tail) == 3:
            return tail.upper()
    if iso_names:
        return iso_names.get(normalize_name(node.label), "")
    return ""


def iso_name_index(rows) -> dict[str, str]:
    """normalised ISO country name -> alpha-3, from the checked-in snapshot."""
    out: dict[str, str] = {}
    for r in rows or []:
        n = normalize_name(r.get("name") or "")
        if n and r.get("alpha-3"):
            out.setdefault(n, r["alpha-3"].upper())
    return out


def load_iso_rows(path: str | None = None):
    """The ISO-3166 snapshot rows, or [] when the file is absent (then no name joins)."""
    import json
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    p = path or os.path.join(root, ISO_SNAPSHOT)
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        return (json.load(f) or {}).get("rows", [])


def canonical_countries(graph, iso_names: dict[str, str] | None = None) -> dict[str, str]:
    """alpha-3 -> the one country key that will represent it.

    Preference: a node that carries the ISO alpha-3 attribute (the world builder's
    country:US), then any other node naming the same alpha-3. The choice is recorded,
    not guessed: every other node for that alpha-3 is joined to it, never renamed.
    """
    cands: dict[str, list[str]] = collections.defaultdict(list)
    for n in graph.nodes.values():
        if n.kind != "country":
            continue
        a3 = iso3_of(n, iso_names)
        if a3 and len(a3) == 3:
            cands[a3].append(n.key)
    out: dict[str, str] = {}
    for a3, keys in cands.items():
        with_iso = [k for k in keys if (graph.nodes[k].attrs.get("iso3") or "").upper() == a3]
        pool = with_iso or keys
        out[a3] = sorted(pool, key=lambda k: (len(k), k))[0]
    return out


# ---------------------------------------------------------------------------- the bridges

def _add(graph, src: str, dst: str, rel: str, *, source: str, url: str,
         basis: str) -> int:
    """Add one cited edge unless it is already there. Returns 1 when added."""
    if src == dst or src not in graph.nodes or dst not in graph.nodes:
        return 0
    key = (src, rel, dst)
    if key in graph.edges:
        return 0
    graph.add_edge(Edge(src=src, dst=dst, rel=rel, attrs={"basis": basis,
                                                          "bridge": "entitygraph.bridge"},
                        source=source, url=url))
    return 1


def bridge_satellites_to_countries(graph, iso_names=None) -> dict:
    """Every satellite whose owner code the catalogue maps to a state gets that state.

    The owner code is the source's field; `satcat.OWNER_ALPHA3` is the catalogue's own
    mapping table. A code that is not in the table is an operator or a consortium, and
    the satellite is left in its island with the refusal counted.
    """
    from .satcat import OWNER_ALPHA3

    canon = canonical_countries(graph, iso_names)
    linked = unresolvable = 0
    codes: dict[str, int] = {}
    for n in graph.nodes.values():
        if n.kind != "satellite":
            continue
        code = (n.attrs.get("owner_code") or "").strip().upper()
        a3 = (OWNER_ALPHA3.get(code) or "").strip().upper()
        tgt = canon.get(a3) if a3 else None
        if not tgt:
            unresolvable += 1
            codes[code or "?"] = codes.get(code or "?", 0) + 1
            continue
        linked += _add(graph, n.key, tgt, "owned_by_state",
                       source="CelesTrak satcat (owner code) -> ISO 3166-1 alpha-3",
                       url="https://celestrak.org/satcat/records.php?GROUP=active&FORMAT=json",
                       basis=f"owner code {code} is mapped to {a3} by satcat.OWNER_ALPHA3; "
                             "state implied by the owner code, not by the satellite's operator")
    return {"linked": linked, "unresolvable_satellites": unresolvable,
            "unresolvable_codes": dict(sorted(codes.items(), key=lambda kv: -kv[1]))}


def bridge_satellites_to_operators(graph) -> dict:
    """Satellite -> the operator company the catalogue's own operator table names."""
    from .satcat import OPERATOR_NAMES

    linked = missing_company = 0
    missing: dict[str, int] = {}
    for n in graph.nodes.values():
        if n.kind != "satellite":
            continue
        op = OPERATOR_NAMES.get((n.attrs.get("owner_code") or "").strip().upper(), "")
        if not op:
            continue
        okey = "company:" + op.lower().replace(" ", "-").replace("(", "") \
            .replace(")", "").replace(".", "")
        if okey not in graph.nodes:
            missing[op] = missing.get(op, 0) + 1
            missing_company += 1
            continue
        linked += _add(graph, n.key, okey, "operated_by",
                       source="CelesTrak satcat (operator table)",
                       url="https://celestrak.org/satcat/records.php?GROUP=active&FORMAT=json",
                       basis=f"owner code {n.attrs.get('owner_code')} -> operator "
                             f"{op} by satcat.OPERATOR_NAMES")
    return {"linked": linked, "operator_without_company_node": missing_company,
            "operators_missing": missing}


def bridge_country_identity(graph, iso_names=None) -> dict:
    """One state, one representative: every other spelling joined with same_country_as.

    The four builders named the same state four ways. The alpha-3 is the identifier; the
    edges are directed at the representative so paths read outwards from it.
    """
    canon = canonical_countries(graph, iso_names)
    linked = joined = 0
    groups = collections.defaultdict(list)
    for n in graph.nodes.values():
        if n.kind != "country":
            continue
        a3 = iso3_of(n, iso_names)
        if a3 and len(a3) == 3:
            groups[a3].append(n.key)
    for a3, keys in groups.items():
        anchor = canon.get(a3)
        if not anchor:
            continue
        for k in keys:
            if k == anchor:
                continue
            linked += _add(graph, k, anchor, "same_country_as",
                           source="ISO 3166-1 alpha-3 (node identity, not a source claim)",
                           url=ISO_PROVENANCE,
                           basis=f"both nodes state alpha-3 {a3}; one state, one node")
            joined += 1
    return {"alpha3_groups": len(groups), "identity_edges": linked, "non_canonical_nodes": joined}


def bridge_listed_companies(graph, rel: str = "listed_in_country",
                           prefix: str = _CNSTOCK_PREFIX,
                           country: str = _A_SHARE_COUNTRY) -> dict:
    """A Shanghai/Shenzhen code is a listing venue fact; attach it to that venue's state."""
    if country not in graph.nodes:
        return {"linked": 0, "reason": f"{country} not in graph"}
    linked = already = 0
    for n in list(graph.nodes.values()):
        if n.kind != "company" or not n.key.startswith(prefix):
            continue
        if (n.key, rel, country) in graph.edges:
            already += 1
            continue
        linked += _add(graph, n.key, country, rel,
                       source="证券代码所属市场（沪/深）",
                       url="https://push2.eastmoney.com/",
                       basis="A-share code prefix 6xx/0xx/3xx/688 = 沪/深 交易所；市场归属不是经营地")
    return {"linked": linked, "already_linked": already}


# ------------------------------------------------------------------------------- measuring

def islands(graph) -> dict:
    """Connected components over the undirected projection: the number this module moves."""
    idx = {k: i for i, k in enumerate(graph.nodes)}
    adj = collections.defaultdict(set)
    for e in graph.edges.values():
        a, b = idx.get(e.src), idx.get(e.dst)
        if a is None or b is None or a == b:
            continue
        adj[a].add(b)
        adj[b].add(a)
    n = len(idx)
    seen = bytearray(n)
    sizes = []
    for s in range(n):
        if seen[s]:
            continue
        seen[s] = 1
        stack = [s]
        size = 0
        while stack:
            v = stack.pop()
            size += 1
            for w in adj[v]:
                if not seen[w]:
                    seen[w] = 1
                    stack.append(w)
        sizes.append(size)
    sizes.sort(reverse=True)
    isolated = sum(1 for i in range(n) if not adj[i])
    return {"nodes": n, "components": len(sizes), "isolated": isolated,
            "largest": sizes[0] if sizes else 0,
            "largest_share": round(100.0 * sizes[0] / n, 1) if n else 0.0,
            "top": sizes[:8],
            "degree_ok": sum(1 for i in range(n) if adj[i])}


def connect(graph, *, iso_names=None, satellites=True, operators=True,
            countries=True, listings=True) -> dict:
    """Apply every bridge. Returns the stats, the island measurement before and after.

    Runs the measurement twice on purpose: a bridge set that does not move the largest
    component is a claim without evidence, and the caller should see both numbers.
    """
    before = islands(graph)
    edges_before = len(graph.edges)
    out: dict = {"before": before, "edges_before": edges_before}
    if countries:
        out["country_identity"] = bridge_country_identity(graph, iso_names)
    if satellites:
        out["satellite_country"] = bridge_satellites_to_countries(graph, iso_names)
    if operators:
        out["satellite_operator"] = bridge_satellites_to_operators(graph)
    if listings:
        out["listed_country"] = bridge_listed_companies(graph)
    after = islands(graph)
    out["after"] = after
    out["edges_added"] = len(graph.edges) - edges_before
    out["edges_after"] = len(graph.edges)
    n = after["nodes"]
    out["density_after"] = round(2.0 * len(graph.edges) / (n * (n - 1)), 12) if n > 1 else 0.0
    return out
