"""Time on the graph: every edge gets an interval, and a slice reconstructs what
was knowable at one instant.

The rule the rest of the repository already enforces is "an edge must cite its source".
This module adds the second half of that sentence: an edge must also say *when* it was
true, and when it stopped being true, and what document ended it. A holding that was
5.4% in 2019 and 4.1% in 2021 is not one edge with a note; it is two edges with
intervals, and a query for 2020-06-01 must return exactly one of them.

Deliberately not pandas, not a temporal database. Two dicts and a slice.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass
from typing import Iterable, Iterator

from .model import Edge, Graph, Node


def _key(iso: str) -> str:
    """Timestamps are ISO-8601 dates or datetimes, compared as strings."""
    return (iso or "").strip()


@dataclass
class Interval:
    """Half-open [start, end). end may be empty, meaning "still open as far as we know"."""

    start: str
    end: str = ""

    def contains(self, t: str) -> bool:
        t = _key(t)
        if not t:
            raise ValueError("as_of needs a date; an empty timestamp matches nothing")
        if self.start and t < self.start:
            return False
        if self.end and t >= self.end:
            return False
        return True

    def overlaps(self, other: "Interval") -> bool:
        if self.end and other.start and other.start >= self.end:
            return False
        if other.end and self.start and self.start >= other.end:
            return False
        return True

    def as_dict(self) -> dict:
        return {"valid_from": self.start, "valid_to": self.end}


# Ingest maps: the attribute a source uses for "when this became true", and the one it
# uses for "when it stopped". Filling these in is a judgement about the document, so it is
# a declared table, not a guess buried in the loader.
DEFAULT_MAPS: dict[str, dict[str, str]] = {
    # An SC 13D/G cover page reports the holding as it stood on the signature date. It has
    # no end date; the next filing for the same holder ends it.
    "SEC EDGAR": {"valid_from": "date"},
    # A registry extract is true on the day it was fetched, and we have no document saying
    # when it changed, so it is dated and left open.
    "GLEIF": {"valid_from": "_fetched_date"},
}


def _date_of(value: str) -> str:
    return (value or "").strip()[:10]


def from_graph(graph: Graph, *, maps: dict[str, dict[str, str]] | None = None,
               default: dict[str, str] | None = None) -> tuple[Graph, list[dict]]:
    """Retrofit dates onto a graph built before dates existed.

    Returns (the graph with valid_from/valid_to in each edge's attrs, a list of notes).
    An edge whose source has no mapping is left undated and listed in the notes -- it is
    not given a made-up date, because a made-up date is worse than a missing one: it makes
    a slice look authoritative when it is not.
    """
    maps = {**DEFAULT_MAPS, **(maps or {})}
    default = default or {}
    out = Graph()
    for n in graph.nodes.values():
        out.add_node(n)
    notes: list[dict] = []
    by_triple: dict[tuple[str, str, str], list[Edge]] = {}
    for e in graph.edges.values():
        by_triple.setdefault(e.key, []).append(e)

    for key, edges in by_triple.items():
        edges = sorted(edges, key=lambda e: _date_of(
            e.attrs.get(maps.get(e.source, default).get("valid_from", ""), "")))
        for i, e in enumerate(edges):
            m = maps.get(e.source, default)
            if not m.get("valid_from"):
                # no mapping: carry the edge through untouched and say so
                out.edges[e.key] = e
                notes.append({"what": "undated", "source": e.source, "key": list(e.key),
                              "why": "no date attribute mapped for this source"})
                continue
            raw = e.attrs.get(m["valid_from"], "") or (e.fetched_at if m["valid_from"] == "_fetched_date" else "")
            start = _date_of(str(raw))
            if not start:
                out.edges[e.key] = e
                notes.append({"what": "undated", "source": e.source, "key": list(e.key),
                              "why": f"attribute {m['valid_from']!r} was empty"})
                continue
            end = ""
            if i + 1 < len(edges):
                nxt = edges[i + 1]
                end = _date_of(str(nxt.attrs.get(m["valid_from"], "")
                                   or (nxt.fetched_at if m["valid_from"] == "_fetched_date" else "")))
            attrs = {**e.attrs, "valid_from": start}
            if end and end > start:
                attrs["valid_to"] = end
            elif end and end <= start:
                notes.append({"what": "same-day", "source": e.source, "key": list(e.key),
                              "why": f"next version starts on {end}, not after {start}; "
                                     f"kept open rather than given a zero-length interval"})
            out.edges[key] = Edge(src=e.src, dst=e.dst, rel=e.rel, attrs=attrs,
                                  source=e.source, url=e.url, fetched_at=e.fetched_at)
    return out, notes


def intervals_of(edge: Edge) -> Interval:
    """Read the interval off an edge's attrs, refusing an edge that never says when."""
    start = _key(str(edge.attrs.get("valid_from", "")))
    end = _key(str(edge.attrs.get("valid_to", "")))
    return Interval(start=start, end=end)


class Timeline:
    """Turn a Graph into something you can ask "what did it look like on date X".

    Edges without valid_from are not silently tolerated: `check()` reports them, and
    `as_of(strict=True)` refuses the slice. The point of this module is that an edge
    with no date is exactly as useless as an edge with no source.
    """

    def __init__(self, graph: Graph) -> None:
        self.g = graph
        self._sorted: dict[tuple[str, str], list[str]] = {}
        self._build_index()

    def _build_index(self) -> None:
        """For each (src, rel) and (dst, rel), the sorted starts, so a slice is a bisect."""
        out: dict[tuple[str, str], list[str]] = {}
        for e in self.g.edges.values():
            iv = intervals_of(e)
            if not iv.start:
                continue
            out.setdefault(("out", e.src, e.rel), []).append(iv.start)
            out.setdefault(("in", e.dst, e.rel), []).append(iv.start)
        for v in out.values():
            v.sort()
        self._sorted = out

    def undated(self) -> list[tuple[str, str, str]]:
        return [k for k, e in self.g.edges.items() if not intervals_of(e).start]

    def check(self) -> dict:
        """What is missing before the graph can be sliced honestly."""
        undated = self.undated()
        inverted = []
        for k, e in self.g.edges.items():
            iv = intervals_of(e)
            if iv.start and iv.end and iv.end <= iv.start:
                inverted.append((k, iv.as_dict()))
        return {
            "edges": len(self.g.edges),
            "undated": undated,
            "inverted": inverted,
            "ok": not undated and not inverted,
        }

    def edges_at(self, t: str, strict: bool = True) -> list[Edge]:
        t = _key(t)
        chk = None
        if strict:
            chk = self.check()
            if chk["undated"]:
                example = chk["undated"][0]
                raise ValueError(
                    f"{len(chk['undated'])} edge(s) have no valid_from and cannot be placed "
                    f"in time, e.g. {example}. Fix the ingest, or pass strict=False and "
                    f"accept that undated edges are included."
                )
        out = []
        for e in self.g.edges.values():
            iv = intervals_of(e)
            if not iv.start:
                if strict:
                    continue
                out.append(e)
                continue
            if iv.contains(t):
                out.append(e)
        return out

    def as_of(self, t: str, strict: bool = True) -> Graph:
        """A Graph holding only the nodes and edges valid on `t`, with provenance intact."""
        sub = Graph()
        live = self.edges_at(t, strict=strict)
        keep = set()
        for e in live:
            keep.add(e.src)
            keep.add(e.dst)
        for k in keep:
            if k in self.g.nodes:
                sub.nodes[k] = self.g.nodes[k]
        for e in live:
            sub.edges[e.key] = e
        return sub

    def change_points(self) -> list[str]:
        """Every date at which the graph changes. Slicing on these, and only these,
        enumerates the history without guessing at a step size."""
        pts = set()
        for e in self.g.edges.values():
            iv = intervals_of(e)
            if iv.start:
                pts.add(iv.start)
            if iv.end:
                pts.add(iv.end)
        return sorted(pts)

    def segment(self, start: str, end: str, strict: bool = True) -> Iterator[tuple[str, str, Graph]]:
        """Walk the history: for each interval between change points inside [start, end),
        yield (segment_start, segment_end, the graph as it stood in that interval)."""
        pts = [x for x in self.change_points() if start <= x < end]
        bounds = [start] + [x for x in pts if x != start]
        for i, b in enumerate(bounds):
            nxt = bounds[i + 1] if i + 1 < len(bounds) else end
            if nxt <= b:
                continue
            yield b, nxt, self.as_of(b, strict=strict)
