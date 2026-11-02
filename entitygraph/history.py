"""A relationship that changes value over time is several edges, not one edge with a
note. Graph keys edges by (src, rel, dst), so the versions cannot live inside the dict --
they live here, next to the interval, and a slice pulls the right one back in.

Why not just keep the latest? Because the question this repository now answers is
"what was knowable then". Keeping only the latest makes every past question return
today's answer, which is the failure mode that makes an archive useless.
"""
from __future__ import annotations

from dataclasses import dataclass

from .model import Edge, Graph
from .timeline import Interval, intervals_of


@dataclass
class Revision:
    """One version of one triple, with the interval it was the truth in."""

    edge: Edge
    interval: Interval
    supersedes: str = ""      # the URL that ended the previous version, if any

    def as_dict(self) -> dict:
        return {"key": list(self.edge.key), **self.interval.as_dict(),
                "source": self.edge.source, "url": self.edge.url,
                "supersedes": self.supersedes}


class SideTable:
    """Every version of every triple, indexable by date. The Graph keeps only the
    version that is current as of the latest date ingested; this keeps all of them."""

    def __init__(self) -> None:
        self._by_key: dict[tuple[str, str, str], list[Revision]] = {}

    def add(self, edge: Edge, supersedes: str = "") -> Revision:
        iv = intervals_of(edge)
        if not iv.start:
            raise ValueError(f"edge {edge.key} has no valid_from; a version needs a start date")
        rev = Revision(edge=edge, interval=iv, supersedes=supersedes)
        self._by_key.setdefault(edge.key, []).append(rev)
        return rev

    def __len__(self) -> int:
        return sum(len(v) for v in self._by_key.values())

    def keys(self):
        return self._by_key.keys()

    def versions(self, key: tuple[str, str, str]) -> list[Revision]:
        return sorted(self._by_key.get(key, []), key=lambda r: r.interval.start)

    def at(self, key: tuple[str, str, str], t: str) -> Revision | None:
        """The version that was current on t. At most one: overlapping versions of the
        same triple would mean the archive contradicts itself, and that is an error."""
        live = [r for r in self._by_key.get(key, []) if r.interval.contains(t)]
        if len(live) > 1:
            starts = ", ".join(r.interval.start for r in live)
            raise ValueError(f"{key} has {len(live)} overlapping versions on {t} (starts: {starts})")
        return live[0] if live else None

    def contradictions(self) -> list[tuple[tuple[str, str, str], list[Revision]]]:
        """Triples whose versions overlap in time."""
        bad = []
        for k, revs in self._by_key.items():
            revs = sorted(revs, key=lambda r: r.interval.start)
            for a, b in zip(revs, revs[1:]):
                if a.interval.overlaps(b.interval):
                    bad.append((k, [a, b]))
        return bad

    def as_of(self, t: str, graph: Graph | None = None, strict: bool = True) -> Graph:
        """A Graph holding exactly the versions that were current on t.

        `graph` supplies the nodes (they are not versioned yet -- a node is either known
        or not); pass the full graph and the slice carries its endpoints' labels.
        """
        sub = Graph()
        keyed = []
        for k in self._by_key:
            rev = self.at(k, t)
            if rev is not None:
                keyed.append(rev.edge)
        # The base graph is used for nodes (they are not versioned yet) and, in the lenient
        # mode, for edges the side table never saw. It is NOT a second source of truth:
        # an undated edge in it is the caller's ingest problem and Timeline.check() reports
        # it. Refusing here made the side table unusable on any graph built before dates
        # existed, which is exactly the graph this module is meant to retrofit.
        undated = [e for e in (graph.edges.values() if graph else []) if not intervals_of(e).start]
        seen = {e.key for e in keyed}
        extra = [e for e in undated if e.key not in seen]
        if strict and extra:
            raise ValueError(f"{len(extra)} edge(s) in the base graph have no valid_from and no "
                             f"version in the side table, e.g. {extra[0].key}; they cannot be "
                             f"placed in time")
        edges = keyed + ([] if strict else extra)
        if graph is not None:
            for e in edges:
                for endpoint in (e.src, e.dst):
                    if endpoint in graph.nodes:
                        sub.nodes[endpoint] = graph.nodes[endpoint]
        for e in edges:
            sub.edges[e.key] = e
        return sub

    def collapse(self) -> Graph:
        """Each triple as of its latest start. "Where things stand", not history."""
        g = Graph()
        for k, revs in self._by_key.items():
            g.edges[k] = max(revs, key=lambda r: r.interval.start).edge
        return g

    @classmethod
    def from_graph(cls, graph: Graph) -> "SideTable":
        """Every dated edge of a graph as one version. An undated edge is left out and can
        be reported with `missing()` -- silently absorbing it would defeat the point."""
        st = cls()
        for e in graph.edges.values():
            if intervals_of(e).start:
                st.add(e)
        return st

    def missing(self, graph: Graph) -> list[tuple[str, str, str]]:
        """Edges of `graph` that have no version here: the retrofit's to-do list."""
        have = set(self._by_key)
        return [k for k in graph.edges if k not in have]

    def to_dict(self) -> dict:
        return {":".join(k): [r.as_dict() for r in self.versions(k)] for k in self._by_key}
