"""Crawl -> clean, in one call, with the specialist model on the geographic part.

Two halves that were separate and now are not:

  crawl     autoforge's crawler fetches pages (fast / stealth / render).
  clean     this repository attaches them to a graph, and refuses to guess.

`crawl_into_graph` is the join. It takes whatever the crawler returned -- the same
result dict the crawl_fast / crawl_stealth / crawl_render tools print -- and ingests
it through `web.add_web`, so a fetched page becomes a cited node and a URL that
failed becomes a caveat with its reason, never a silent absence.

`geotag_locations` is the other join. Every node that already carries a coordinate
gets the nearest indexed Sentinel-2 granule, looked up through the earthexplorer
specialist service. What that service is good at is "which scene covers this point",
and that is what gets recorded: a granule id, the score, and the endpoint that said
so. It is NOT a land-cover classifier -- measured on 2026-11-24, a text query for
"satellite ground station with dish antennas" returned pure desert tiles at 0.31,
and the model cannot tell you what is in the pixels. So nothing here writes a
land-cover attribute; a claim like that would be invented, and this repository does
not do invented claims.

Both joins keep the repository's rule: every node and edge carries the URL it came
from, and every refusal is written down.
"""
from __future__ import annotations

import os
import re

from . import clean as _clean
from .model import Graph, Node
from .web import add_web, fetch_web

# The report line the earthexplorer service prints for one hit:
#   "  0.7978  lat  40.1943  lon   99.8112  S2A_MSIL2A_20220120T041101_..."
_HIT_LINE = re.compile(
    r"(?P<score>\d+\.\d+)\s+lat\s+(?P<lat>-?\d+\.\d+)\s+lon\s+"
    r"(?P<lon>-?\d+\.\d+)\s+(?P<pid>S2[AB]_\S+)")
_GRANULE = re.compile(r"^S2[AB]_MSIL2A_\d{8}T\d{6}_N\d{4}_R\d{3}_T\w{5}_\S+$")


def exists(paths) -> tuple[list[str], list[str]]:
    """Split a list of graph names into (present, missing) without guessing extensions."""
    here, gone = [], []
    for name in paths:
        q = name if str(name).endswith(".json") else str(name) + ".graph.json"
        (here if os.path.exists(q) else gone).append(q)
    return here, gone


def crawl_into_graph(urls, *, graph: Graph | None = None, mode: str = "fast",
                     concurrency: int = 12, timeout: float = 10.0,
                     with_text: bool = True, crawler=None,
                     source: str = "web") -> tuple[Graph, dict]:
    """Fetch URLs and ingest them. Returns (graph, report) -- a failure is a row, not an exception."""
    g = graph if graph is not None else Graph()
    if isinstance(urls, str):
        urls = [urls]
    urls = [str(u).strip() for u in urls if str(u).strip()]
    if not urls:
        return g, {"engine": "none", "pages": 0, "hosts": 0, "edges": 0, "caveats": [],
                   "requested": 0}
    fetcher = crawler
    if fetcher is None:
        try:
            from autoforge import crawler as fetcher  # type: ignore
        except Exception:
            fetcher = None
    result, engine = fetch_web(urls, mode=mode, concurrency=concurrency,
                              timeout=timeout, with_text=with_text, crawler=fetcher)
    report = add_web(g, result, engine=engine, source=source)
    report["engine"] = engine
    return g, report


def parse_locator_report(text: str) -> list[dict]:
    """The service's answer as rows. A line that does not match is dropped, not coerced."""
    out = []
    for m in _HIT_LINE.finditer(text or ""):
        pid = m.group("pid")
        out.append({"score": float(m.group("score")), "lat": float(m.group("lat")),
                    "lon": float(m.group("lon")), "product_id": pid,
                    "granule_wellformed": bool(_GRANULE.match(pid))})
    return out


def earthexplorer_locator(*, base_url: str = "", timeout: float = 300.0,
                          top_permille: int = 3):
    """A locator backed by the specialist service, using autoforge's own client.

    Returns None when autoforge is not importable here, so a caller can say "no
    specialist available" instead of quietly falling back to a guess.
    """
    try:
        import sys
        for cand in (os.environ.get("AUTOFORGE_SRC", ""),
                     r"D:\\Users\\china\\Desktop\\项目_开发\\autoforge"):
            if cand and cand not in sys.path:
                sys.path.insert(0, cand)
        from autoforge.earthexplorer import DEFAULT_BASE_URL, EarthExplorerClient  # type: ignore
    except Exception:
        return None
    client = EarthExplorerClient(base_url=base_url or DEFAULT_BASE_URL, timeout=timeout)
    top = int(top_permille or 3)

    def locate(lat: float, lon: float) -> list[dict]:
        text = client.search_location(float(lat), float(lon), top_permille=top)
        return parse_locator_report(text)

    locate.model = "location->image (model-free)"
    locate.endpoint = client.base_url
    return locate


def geotag_locations(graph: Graph, locator, *, top_permille: int = 3,
                     limit: int = 25) -> dict:
    """Nearest Sentinel-2 scene for every node that already has a coordinate.

    No locator, or a locator that throws, leaves the node without the attribute and
    is counted -- an absent lookup must not read like a lookup that found nothing.
    """
    if locator is None:
        return {"geotagged": 0, "no_coordinate": 0, "candidates": 0, "failed": 0,
                "skipped_by_limit": 0, "locator": "none"}
    stats = {"geotagged": 0, "no_coordinate": 0, "candidates": 0, "failed": 0,
             "skipped_by_limit": 0, "locator": getattr(locator, "endpoint", "custom")}
    done = 0
    for n in graph.nodes.values():
        lat = n.attrs.get("lat", n.attrs.get("sub_lat"))
        lon = n.attrs.get("lon", n.attrs.get("sub_lon"))
        if lat is None or lon is None:
            stats["no_coordinate"] += 1
            continue
        if done >= limit:
            stats["skipped_by_limit"] += 1
            continue
        try:
            hits = locator(float(lat), float(lon))
        except Exception as exc:                       # a dead service is a stat, not a crash
            n.attrs["geo_scene_error"] = str(exc)[:200]
            stats["failed"] += 1
            continue
        if not hits:
            stats["failed"] += 1
            continue
        done += 1
        best = max(hits, key=lambda h: h["score"])
        n.attrs["geo_scene"] = best["product_id"]
        n.attrs["geo_scene_score"] = best["score"]
        n.attrs["geo_scene_count"] = len(hits)
        n.attrs["geo_scene_method"] = (
            "earthexplorer search_location: nearest indexed Sentinel-2 tile; "
            "this is scene coverage, NOT a land-cover label")
        n.attrs["geo_scene_model"] = getattr(locator, "model", "unknown")
        n.attrs["geo_scene_endpoint"] = getattr(locator, "endpoint", "")
        stats["geotagged"] += 1
        stats["candidates"] += len(hits)
    return stats


def run_clean(*, graphs=(), urls=(), tle: str = "", geocode: bool = True,
              geotag: bool = False, out: str = "clean/clean.graph.json",
              refused: str = "clean/clean_refused.json", locator=None,
              crawler=None, mode: str = "fast", geotag_limit: int = 25,
              gazetteer: str = "") -> dict:
    """The whole pipeline in one call, and the only place the order is written down."""
    present, missing = exists(graphs)
    g, refusals, per_source = _clean.merge_graphs(present)
    dead = _clean.strip_symbol_labels(g)

    report: dict = {"sources": per_source, "missing_graphs": missing,
                    "symbol_labels_removed": len(dead), "refusals": len(refusals)}

    crawls = []
    for u in ([urls] if isinstance(urls, str) else list(urls or [])):
        if str(u).strip():
            crawls.append(str(u).strip())
    if crawls:
        g, rep = crawl_into_graph(crawls, graph=g, mode=mode, crawler=crawler)
        report["crawl"] = rep

    if tle:
        if "://" in tle:
            import urllib.request
            req = urllib.request.Request(tle, headers={"User-Agent": "entitygraph"})
            text = urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "ignore")
        else:
            text = open(tle, encoding="utf-8", errors="ignore").read()
        report["subpoints"] = _clean.attach_subpoints(g, text)

    if geocode:
        report["geocode"] = _clean.geocode_cities(g, _clean.load_gazetteer(gazetteer or None))

    report["geotag"] = geotag_locations(g, locator if geotag else None,
                                        top_permille=3, limit=geotag_limit)

    for q in (refused, out):
        d = os.path.dirname(q)
        if d:
            os.makedirs(d, exist_ok=True)
    import json
    with open(refused, "w", encoding="utf-8") as f:
        json.dump({"refusals": refusals}, f, ensure_ascii=False, indent=1)
    report["bytes_written"] = _clean.dump_graph(g, out)
    report["out"] = out
    report["refused_out"] = refused
    report["nodes"] = len(g.nodes)
    report["edges"] = len(g.edges)
    return report
