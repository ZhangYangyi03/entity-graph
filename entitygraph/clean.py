"""Cleaning one graph out of many, without inventing a single fact.

Three jobs, all of them the same shape: take what a source states, attach it to a key
that already exists, and REFUSE when the link would be a guess.

  merge_graphs     several .graph.json into one, joined on key, never on name.
  attach_subpoints satellite -> latitude/longitude, propagated with SGP4.
  geocode_cities   bank -> city -> coordinate, from a real gazetteer.

What it will not do is the point of the file. Two companies with the same normalised
name are not one company; a city name that matches four places in four countries is not
a location. Each refusal is written down with its reason and left out of the graph, so
the gap is visible in the output instead of quietly filled.
"""
from __future__ import annotations

import datetime
import json
import math
import os
import re

from .model import Edge, Graph, Node
from .resolve import normalize_name

R_EARTH_KM = 6378.137          # WGS84 equatorial radius
WGS84_F = 1.0 / 298.257223563

# A label that is punctuation only carries no entity. Junk from a scrape, not a node.
_SYMBOLS = re.compile(r"[\W_]{1,3}$")


def load_graph(path: str) -> Graph:
    """One .graph.json into a Graph. Edges keep their cited source: add_edge enforces it."""
    with open(path, encoding="utf-8") as f:
        blob = json.load(f)
    g = Graph()
    for n in blob.get("nodes", []):
        key = n.get("key")
        if not key:
            continue
        g.add_node(Node(key, n.get("kind", ""), n.get("label", ""), n.get("attrs") or {},
                        n.get("source", ""), n.get("url", ""), n.get("fetched_at", "")))
    for e in blob.get("edges", []):
        src, dst, rel = e.get("src"), e.get("dst"), e.get("rel")
        if not (src and dst and rel):
            continue
        try:
            g.add_edge(Edge(src, dst, rel, e.get("attrs") or {},
                            e.get("source", ""), e.get("url", ""), e.get("fetched_at", "")))
        except ValueError:
            continue            # an edge with no citation is dropped, not repaired
    return g


def merge_graphs(paths, kinds_for_identity: tuple[str, ...] = ()) -> tuple[Graph, list[dict]]:
    """Union of several graphs by node key. Returns (graph, refusals).

    The refusals are same-normalised-name groups on entity kinds. They are exactly the
    rows a name-joining merge would have welded together, so they are printed rather
    than applied: one company must be one node, and the only way to know it is an
    identifier, not a name.
    """
    out = Graph()
    per_source = {}
    for p in paths:
        g = load_graph(p)
        per_source[os.path.basename(p)] = {"nodes": len(g.nodes), "edges": len(g.edges)}
        for n in g.nodes.values():
            out.add_node(n)
        for e in g.edges.values():
            try:
                out.add_edge(e)
            except ValueError:
                pass

    refusals: list[dict] = []
    wanted = kinds_for_identity or ("company", "listed_company", "company_gleif",
                                    "institution", "organization", "bank")
    by_name: dict[str, set[str]] = {}
    for n in out.nodes.values():
        if n.kind in wanted and n.label:
            by_name.setdefault(normalize_name(n.label), set()).add(n.key)
    for name, keys in sorted(by_name.items()):
        if not name or len(keys) < 2:
            continue
        # One of the keys being a strict superset of another is still two keys; only an
        # identifier in the attributes could join them, and that is a human's call.
        refusals.append({
            "reason": "same normalised name under different keys; identifier join required",
            "name": name,
            "keys": sorted(keys),
        })
    return out, refusals, per_source


# --------------------------------------------------------------------------- satellites

def tle_records(text: str):
    """Yield (name, line1, line2) from TLE text: name line plus two 69-column lines."""
    lines = [l.rstrip() for l in text.splitlines() if l.strip()]
    for i in range(0, len(lines) - 2):
        if not lines[i + 1].startswith("1 ") or not lines[i + 2].startswith("2 "):
            continue
        yield lines[i].strip(), lines[i + 1], lines[i + 2]


def _gp_from_tle(name: str, l1: str, l2: str) -> dict:
    """A CelesTrak-shaped GP record out of a TLE, so orbit.from_gp can take it."""
    def num(s: str) -> float:
        s = s.strip()
        return float(s) if s else 0.0

    epy, epd = int(l1[18:20]), float(l1[20:32])
    year = 2000 + epy if epy < 57 else 1900 + epy
    epoch = datetime.datetime(year, 1, 1, tzinfo=datetime.timezone.utc) \
        + datetime.timedelta(days=epd - 1)
    return {
        "OBJECT_NAME": name,
        "OBJECT_ID": "",
        "EPOCH": epoch.isoformat(),
        "MEAN_MOTION": num(l2[52:63]),
        "ECCENTRICITY": "0." + l2[26:33].strip(),
        "INCLINATION": num(l2[8:16]),
        "RA_OF_ASC_NODE": num(l2[17:25]),
        "ARG_OF_PERICENTER": num(l2[34:42]),
        "MEAN_ANOMALY": num(l2[43:51]),
        "BSTAR": 0.0,
        "MEAN_MOTION_DOT": 0.0,
        "NORAD_CAT_ID": l1[2:7].strip(),
    }


def _gmst_rad(t: float) -> float:
    """Greenwich mean sidereal angle at unix time t. The earth's rotation, nothing else."""
    d = (t / 86400.0) + 2440587.5 - 2451545.0
    centuries = d / 36525.0
    deg = 280.46061837 + 360.98564736629 * d + 0.000387933 * centuries * centuries
    return math.radians(deg % 360.0)


def eci_to_geodetic(pos, t: float):
    """TEME position (km) at unix time t -> (lat, lon, altitude) on WGS84.

    Rotation about the pole is exact: rotate by GMST, then solve latitude
    iteratively because geodetic latitude is not the angle at the geocentre.
    """
    x, y, z = pos
    th = _gmst_rad(t)
    xe = x * math.cos(th) + y * math.sin(th)
    ye = -x * math.sin(th) + y * math.cos(th)
    lon = math.degrees(math.atan2(ye, xe))
    r = math.hypot(xe, ye)
    e2 = WGS84_F * (2 - WGS84_F)
    lat = math.atan2(z, r * (1 - e2))
    for _ in range(8):
        n = R_EARTH_KM / math.sqrt(1 - e2 * math.sin(lat) ** 2)
        h = r / math.cos(lat) - n
        lat = math.atan2(z, r * (1 - e2 * n / (n + h)))
    return math.degrees(lat), lon, math.hypot(r, z) - R_EARTH_KM


def attach_subpoints(graph: Graph, tle_text: str, when: float | None = None) -> dict:
    """Attach a real propagated subpoint to every satellite the catalogue already has.

    The satellite is found by label, because the catalogue and the GP feed share no key.
    That is a declared match, not an identifier join, so it is recorded in the attribute
    that carries it and unmatched entries are counted rather than dropped silently.
    """
    from . import orbit

    t = when if when is not None else datetime.datetime.now(datetime.timezone.utc).timestamp()
    stamp = datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    by_label: dict[str, list[Node]] = {}
    for n in graph.nodes.values():
        if n.kind == "satellite":
            by_label.setdefault((n.label or "").strip().upper(), []).append(n)

    matched = unmatched = errored = 0
    for name, l1, l2 in tle_records(tle_text):
        nodes = by_label.get(name.strip().upper())
        if not nodes:
            unmatched += 1
            continue
        try:
            el = orbit.from_gp(_gp_from_tle(name, l1, l2))
            el.line1, el.line2 = l1, l2
            pos, err = orbit.sgp4_eci(el, t)
        except Exception:
            errored += 1
            continue
        if err:
            errored += 1
            continue
        lat, lon, alt = eci_to_geodetic(pos, t)
        for n in nodes:
            n.attrs["sub_lat"] = round(lat, 4)
            n.attrs["sub_lon"] = round(lon, 4)
            n.attrs["sub_alt_km"] = round(alt, 1)
            n.attrs["sub_at"] = stamp
            n.attrs["sub_method"] = ("entitygraph.orbit.sgp4_eci -> GMST -> WGS84 geodetic; "
                                     f"TLE {l1[2:7].strip()}")
            n.attrs["sub_match"] = "declared: catalogue label == TLE name, no shared id"
        matched += len(nodes)
    return {"matched_nodes": matched, "tle_unmatched": unmatched, "propagation_errors": errored}


# ---------------------------------------------------------------------------- gazetteer

NE_PLACES_URL = ("https://raw.githubusercontent.com/nvkelso/natural-earth-vector/"
                 "master/geojson/ne_50m_populated_places.geojson")


def load_gazetteer(path: str | None = None, cache_dir: str = ".cache") -> dict:
    """city name (lower) -> list of candidate coordinates, from Natural Earth.

    Kept as a *list* per name on purpose: Salem is in Arkansas, Illinois, Missouri and
    several other places, and collapsing that to one point is how a map acquires a lie.
    """
    if path is None:
        os.makedirs(cache_dir, exist_ok=True)
        path = os.path.join(cache_dir, "ne_50m_populated_places.geojson")
        if not os.path.exists(path) or os.path.getsize(path) < 1000:
            import urllib.request
            req = urllib.request.Request(NE_PLACES_URL, headers={"User-Agent": "entitygraph"})
            with urllib.request.urlopen(req, timeout=60) as r:
                with open(path, "wb") as f:
                    f.write(r.read())
    with open(path, encoding="utf-8") as f:
        blob = json.load(f)
    index: dict[str, list[dict]] = {}
    for feat in blob.get("features", []):
        pr = feat.get("properties") or {}
        coords = (feat.get("geometry") or {}).get("coordinates") or []
        if len(coords) < 2:
            continue
        index.setdefault((pr.get("NAME") or "").strip().lower(), []).append({
            "lat": coords[1], "lon": coords[0],
            "country": (pr.get("ADM0NAME") or "").strip().lower(),
            "pop": pr.get("POP_MAX") or 0,
        })
    return index


def geocode_cities(graph: Graph, gazetteer: dict, city_attr: str = "city",
                   region_attr: str = "stname",
                   kinds: tuple[str, ...] = ("bank",)) -> dict:
    """lat/lon onto nodes that carry a city, and a written reason when it is refused."""
    stats = {"unique": 0, "by_region": 0, "refused_ambiguous": 0, "not_in_gazetteer": 0,
             "no_city": 0}
    for n in graph.nodes.values():
        if kinds and n.kind not in kinds:
            continue
        city = (n.attrs.get(city_attr) or "").strip().lower()
        if not city:
            stats["no_city"] += 1
            continue
        cands = gazetteer.get(city)
        if not cands:
            stats["not_in_gazetteer"] += 1
            continue
        if len(cands) == 1:
            c = cands[0]
            n.attrs["lat"], n.attrs["lon"] = round(c["lat"], 4), round(c["lon"], 4)
            n.attrs["geo_match"] = ("gazetteer: Natural Earth 50m populated places, "
                                    "unique city name")
            stats["unique"] += 1
            continue
        region = (n.attrs.get(region_attr) or "").strip().lower()
        pool = [c for c in cands if region and region in c["country"]]
        if len(pool) == 1:
            c = pool[0]
            n.attrs["lat"], n.attrs["lon"] = round(c["lat"], 4), round(c["lon"], 4)
            n.attrs["geo_match"] = "gazetteer: city name + admin area"
            stats["by_region"] += 1
        else:
            n.attrs["geo_match"] = (f"refused: {len(cands)} places share this city name "
                                    "and the admin area does not pick one")
            stats["refused_ambiguous"] += 1
    return stats


def strip_symbol_labels(graph: Graph) -> list[str]:
    """Drop nodes whose label is punctuation only. Returns the keys removed."""
    dead = [k for k, n in graph.nodes.items() if _SYMBOLS.match(n.label or "")]
    for k in dead:
        del graph.nodes[k]
    for key in [key for key, e in graph.edges.items() if e.src in dead or e.dst in dead]:
        del graph.edges[key]
    return dead


def dump_graph(graph: Graph, path: str) -> int:
    """Write a graph to disk without holding it twice in memory.

    g.to_json() pretty-prints the whole document as one Python string; on a graph this
    size that is a MemoryError, not a style choice. So the JSON is streamed: nodes one
    at a time, then edges one at a time. The bytes on disk are the same document.
    """
    import os as _os
    d = _os.path.dirname(path)
    if d:
        _os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write('{"nodes": [')
        first = True
        for n in graph.nodes.values():
            if not first:
                f.write(",")
            first = False
            f.write("\n")
            json.dump({"key": n.key, "kind": n.kind, "label": n.label, "attrs": n.attrs,
                       "source": n.source, "url": n.url, "fetched_at": n.fetched_at},
                      f, ensure_ascii=False)
        f.write("\n],\n\"edges\": [")
        first = True
        for e in graph.edges.values():
            if not first:
                f.write(",")
            first = False
            f.write("\n")
            json.dump({"src": e.src, "dst": e.dst, "rel": e.rel, "attrs": e.attrs,
                       "source": e.source, "url": e.url, "fetched_at": e.fetched_at},
                      f, ensure_ascii=False)
        f.write("\n]}\n")
    return _os.path.getsize(path)
