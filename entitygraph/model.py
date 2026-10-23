"""The graph itself: nodes, edges, provenance. Pure standard library."""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class Node:
    key: str                      # namespaced id: "lei:529900T8BM49AURSDO55"
    kind: str                     # company | person | address | filing
    label: str
    attrs: dict[str, Any] = field(default_factory=dict)
    source: str = ""              # where this node came from
    url: str = ""
    fetched_at: str = ""

    def merge(self, other: "Node") -> None:
        """Same key from another source: fill gaps, never overwrite a value with None."""
        for k, v in other.attrs.items():
            if v not in (None, "", [], {}) and k not in self.attrs:
                self.attrs[k] = v
        if not self.label and other.label:
            self.label = other.label
        srcs = set(self.attrs.get("_sources", [])) | {self.source, other.source}
        self.attrs["_sources"] = sorted(s for s in srcs if s)


@dataclass
class Edge:
    src: str
    dst: str
    rel: str                      # subsidiary_of | parent_of | holder_of | director_of | sanctioned
    attrs: dict[str, Any] = field(default_factory=dict)
    source: str = ""
    url: str = ""
    fetched_at: str = ""

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.src, self.rel, self.dst)


class Graph:
    """A directed multigraph keyed by (src, rel, dst).

    Deliberately not networkx: the whole point is that every edge can be traced back to
    a fetched URL, and a library object hides that.
    """

    def __init__(self) -> None:
        self.nodes: dict[str, Node] = {}
        self.edges: dict[tuple[str, str, str], Edge] = {}

    def add_node(self, node: Node) -> Node:
        if node.key in self.nodes:
            self.nodes[node.key].merge(node)
            return self.nodes[node.key]
        self.nodes[node.key] = node
        return node

    def add_edge(self, edge: Edge) -> Edge:
        if not edge.source:
            raise ValueError(f"edge {edge.key} has no source; every edge must cite one")
        self.edges[edge.key] = edge
        return edge

    def neighbours(self, key: str, rel: str | None = None, direction: str = "both"):
        out = []
        for (s, r, d), e in self.edges.items():
            if rel is not None and r != rel:
                continue
            if direction in ("both", "out") and s == key:
                out.append((e, "out"))
            if direction in ("both", "in") and d == key:
                out.append((e, "in"))
        return out

    def find(self, label_contains: str) -> list[Node]:
        q = label_contains.lower()
        return [n for n in self.nodes.values() if q in n.label.lower()]

    def to_dict(self) -> dict:
        return {
            "nodes": [asdict(n) for n in self.nodes.values()],
            "edges": [asdict(e) for e in self.edges.values()],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def dumps_lines(self) -> str:
        """JSON Lines: one node or edge per line, for streaming into a loader."""
        lines = []
        for n in self.nodes.values():
            lines.append(json.dumps({"type": "node", **asdict(n)}, ensure_ascii=False))
        for e in self.edges.values():
            lines.append(json.dumps({"type": "edge", **asdict(e)}, ensure_ascii=False))
        return "\n".join(lines)

    def stats(self) -> dict:
        kinds: dict[str, int] = {}
        for n in self.nodes.values():
            kinds[n.kind] = kinds.get(n.kind, 0) + 1
        rels: dict[str, int] = {}
        for e in self.edges.values():
            rels[e.rel] = rels.get(e.rel, 0) + 1
        return {"nodes": len(self.nodes), "edges": len(self.edges),
                "by_kind": kinds, "by_rel": rels}
