"""Judging whether a crawl is worth cleaning, and cleaning it when it is.

The rule this file implements: a crawl that returns one thin page is not data, and
running the whole cleaner over it produces a graph nobody will ever query. A crawl that
returns several pages, or one thick one, or anything with a location in it, is worth
ingesting -- and then it should be ingested without anyone having to ask.

So the gate comes first and says WHY, in both directions:

  worth      several successful pages, or enough text to be a document, or a page that
             mentions a coordinate / a place -- the three signals that a page will
             later be joined to something.
  not worth  a single short page, an empty result, a pile of failures. The reason is
             returned so the decision is auditable rather than a silent no.

`clean_result` takes a crawl result that has ALREADY been fetched. It never re-fetches:
a hook that fetched again would double the traffic and, on a live crawl, recurse.
"""
from __future__ import annotations

import json
import os
import re
import time

from . import clean as _clean
from .model import Graph
from .web import add_web

# The store is a rolling graph of crawled pages, not the 31k-node domain graph: a crawl
# hook must stay cheap, and merging 33 MB on every fetch is not a hook.
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_STORE = os.path.join(_REPO, "clean", "auto", "web.graph.json")
DEFAULT_LOG = os.path.join(_REPO, "clean", "auto", "auto.log")

MIN_PAGES = 2          # two pages is already a set, not an accident
MIN_TEXT_CHARS = 2000  # one long document is worth as much as several short ones
MAX_STORE_NODES = 20000

# A page that mentions a place can be joined to the geography layer later, which is the
# whole reason the cleaner is here. This is a signal, not a parse.
GEO_SIGNAL = re.compile(
    r"(?:\blat(?:itude)?\b|\blon(?:gitude)?\b|\bcoordinate|\bmap\b|\bsatellite\b|"
    r"\bremote sens|\bepsg\b|\butm zone\b|\bboundary\b|\bpostcode\b|\bzip code\b|"
    r"[-+]?\d{1,3}\.\d{3,}\s*[,;]\s*[-+]?\d{1,3}\.\d{3,})", re.I)


def judge(result: dict) -> dict:
    """Is this crawl worth cleaning? Returns a verdict with its reason, either way."""
    rows = [r for r in (result.get("results") or []) if isinstance(r, dict)]
    ok = [r for r in rows
          if not r.get("error") and isinstance(r.get("status"), int)
          and 200 <= r["status"] < 300]
    text = sum(len(r.get("text") or "") + len(r.get("markdown") or "") for r in ok)
    links = sum(len(r.get("links") or []) for r in ok)
    hosts = set()
    for r in ok:
        h = (r.get("url") or "").split("//")[-1].split("/")[0].lower()
        if h:
            hosts.add(h)
    geo_hits = sum(1 for r in ok
                   if GEO_SIGNAL.search(" ".join([
                       str(r.get("title") or ""), str(r.get("text") or "")[:4000]])))
    # "cleaned" is present from the start so a caller never has to tell a missing key
    # from a decision: the verdict has one shape whether it kept the crawl or not.
    verdict = {"pages": len(ok), "chars": text, "links": links, "hosts": len(hosts),
               "geo_signal_pages": geo_hits, "failed": len(rows) - len(ok),
               "worth": False, "cleaned": False, "reason": ""}

    if not ok:
        verdict["reason"] = ("nothing was fetched successfully (%d row(s), all failed)"
                            % len(rows))
        return verdict
    if len(ok) >= MIN_PAGES:
        verdict["worth"] = True
        verdict["reason"] = "%d pages fetched -- a set, not one page" % len(ok)
    elif text >= MIN_TEXT_CHARS:
        verdict["worth"] = True
        verdict["reason"] = ("one page but %d chars of text -- a document, not a stub"
                            % text)
    elif geo_hits:
        verdict["worth"] = True
        verdict["reason"] = ("one thin page, but it carries a location signal -- "
                            "joinable to the geography layer")
    else:
        verdict["reason"] = ("one page, %d chars, no location signal -- too little to "
                            "join anything to" % text)
    return verdict


def _load_store(path: str) -> Graph:
    if os.path.exists(path) and os.path.getsize(path) > 2:
        try:
            return _clean.load_graph(path)
        except Exception:
            return Graph()
    return Graph()


def _cap(graph: Graph, limit: int = MAX_STORE_NODES) -> int:
    """Drop the oldest webpages when the rolling store grows past the cap."""
    pages = [n for n in graph.nodes.values() if n.kind == "webpage"]
    if len(pages) <= limit:
        return 0
    pages.sort(key=lambda n: n.fetched_at or "")
    for n in pages[:len(pages) - limit]:
        graph.nodes.pop(n.key, None)
    for key in [k for k, e in graph.edges.items()
                if e.src not in graph.nodes or e.dst not in graph.nodes]:
        graph.edges.pop(key, None)
    return len(pages) - limit


def add_web_by_host(graph: Graph, result: dict, *, engine: str = "") -> dict:
    """web.add_web, applied once per host so one crawl does not fuse unrelated sites.

    links_to runs from a page to the domains it links out to; grouping rows by the page's
    own host first keeps that meaning. The ingestion rules are not restated here -- they
    are web.add_web's, called repeatedly, because a second copy would drift from the
    first.
    """
    rows = list(result.get("results") or [])
    groups: dict[str, list[dict]] = {}
    for row in rows:
        host = (str(row.get("url") or "").split("//")[-1].split("/")[0]).lower() or "(none)"
        groups.setdefault(host, []).append(row)
    total = {"pages": 0, "hosts": 0, "edges": 0, "by_host": {}, "caveats": [],
             "requested": result.get("count", len(rows))}
    for host, group in groups.items():
        sub = add_web(graph, {**result, "results": group, "count": len(group)},
                      engine=engine, source="autoforge.crawler")
        total["pages"] += sub["pages"]
        total["edges"] += sub["edges"]
        total["caveats"] += sub["caveats"]
        total["by_host"].update(sub["by_host"])
        total["hosts"] = len(total["by_host"])
    return total


def clean_result(result: dict, *, engine: str = "", store: str = "",
                 log: str = "") -> dict:
    """Ingest an already-fetched crawl result into the rolling store. Returns the report."""
    store = store or os.environ.get("ENTITYGRAPH_AUTO_STORE") or DEFAULT_STORE
    log = log or os.environ.get("ENTITYGRAPH_AUTO_LOG") or DEFAULT_LOG
    graph = _load_store(store)
    before = (len(graph.nodes), len(graph.edges))
    report = add_web_by_host(graph, result, engine=engine)
    dropped = _cap(graph)
    size = _clean.dump_graph(graph, store)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
    line = {"at": stamp, "engine": engine, "pages": report["pages"],
            "hosts": report["hosts"], "edges": report["edges"],
            "caveats": len(report["caveats"]), "dropped_oldest": dropped,
            "store_nodes": len(graph.nodes), "store_bytes": size}
    os.makedirs(os.path.dirname(log), exist_ok=True)
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")
    report["store"] = store
    report["store_nodes_before"] = before[0]
    report["store_nodes_after"] = len(graph.nodes)
    report["dropped_oldest"] = dropped
    report["log"] = log
    return report


def maybe_clean(result: dict, *, engine: str = "", store: str = "", log: str = "",
                force: bool = False) -> dict:
    """Judge, then clean if it is worth it. Never raises; the verdict is the answer."""
    try:
        verdict = judge(result)
        if not (verdict["worth"] or force):
            return verdict
        report = clean_result(result, engine=engine, store=store, log=log)
        # "worth cleaning" and "something was ingested" are two different facts. A page
        # that passed the gate can still be refused by the ingestion rules (a 2xx stub is
        # a bot wall). Saying cleaned=True with 0 pages would hide exactly that.
        ingested = report["pages"] > 0
        verdict.update({
            "cleaned": ingested,
            "store": report["store"],
            "store_nodes_after": report["store_nodes_after"],
            "dropped_oldest": report["dropped_oldest"],
            "caveats": len(report["caveats"]),
        })
        if not ingested:
            why = report["caveats"][0]["why"] if report["caveats"] else "no rows"
            verdict["reason"] = ("worth cleaning, but ingestion took 0 pages (%s) -- "
                                 "the soft-block rule refused them" % why)
        return verdict
    except Exception as exc:                       # a hook must not break the caller
        return {"worth": False, "cleaned": False,
                "reason": "auto-clean failed: %s" % str(exc)[:200]}
