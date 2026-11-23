"""One graph instead of five: union by node key, plus the links that were missing.

The five builders (industry / world / nordic / multilingual / institutions) already agree on
one convention on purpose: a country is always `country:<ISO2>`, a concept is always
`concept:BCI`. That convention is what makes this module short -- merging is a union by key,
not a fuzzy entity-resolution problem.

What this adds that no single builder could
-------------------------------------------
  has_publication_slice   world     249 country -> Europe PMC slice
  has_company_slice       world     498 country -> GLEIF keyword slice
  registered_in           world     company -> country
  trial_located_in        instit.   trial -> country
  trial_sponsored_by      instit.   trial -> institution (ROR-matched or unmatched)
  co_author_country       world     91 country -> country
  trial_spans_country     instit.   3 country -> country, field-level
  discloses / in_board    industry  listed company -> board / disclosure

After the union, one question becomes answerable that was not answerable before:
"which countries have papers AND trials AND companies?" -- the intersection of three
independent measurements. That is the whole justification for merging, and it is printed
rather than implied.
"""
from __future__ import annotations

import datetime as _dt
import json
import os

from .model import Edge, Graph, Node

NOW = lambda: _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")

DEFAULT_INPUTS = ("industry.graph.json", "world.graph.json", "nordic.graph.json",
                  "multilingual.graph.json", "institutions.graph.json")


def load_graph(path: str) -> Graph:
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    g = Graph()
    for n in d.get("nodes") or []:
        g.add_node(Node(key=n["key"], kind=n.get("kind", "unknown"),
                        label=n.get("label") or n["key"], attrs=n.get("attrs") or {},
                        source=n.get("source") or "", url=n.get("url") or "",
                        fetched_at=n.get("fetched_at") or NOW()))
    for e in d.get("edges") or []:
        g.add_edge(Edge(src=e["src"], dst=e["dst"], rel=e.get("rel", "related"),
                        attrs=e.get("attrs") or {}, source=e.get("source") or "",
                        url=e.get("url") or "", fetched_at=e.get("fetched_at") or NOW()))
    return g


def merge(paths=DEFAULT_INPUTS, *, root: str = ".", graph: Graph | None = None) -> Graph:
    """Union the builders by key. A node seen twice keeps the richer attribute set."""
    g = graph or Graph()
    notes: list[dict] = []
    present, missing = [], []
    for p in paths:
        full = p if os.path.isabs(p) else os.path.join(root, p)
        if not os.path.exists(full):
            missing.append(p)
            continue
        try:
            sub = load_graph(full)
        except (OSError, ValueError, KeyError) as e:
            notes.append({"source": p, "what": "载入", "error": "%s: %s" % (type(e).__name__, e)})
            continue
        present.append((p, len(sub.nodes), len(sub.edges)))
        for n in sub.nodes.values():
            if n.key in g.nodes:
                keep = g.nodes[n.key]
                merged = dict(keep.attrs or {})
                for k, v in (n.attrs or {}).items():
                    if merged.get(k) in (None, "", [], {}) and v not in (None, "", [], {}):
                        merged[k] = v
                keep.attrs = merged
                srcs = set(str(keep.source or "").split(" + "))
                srcs.add(str(n.source or ""))
                keep.source = " + ".join(sorted(s for s in srcs if s))
            else:
                g.add_node(n)
        for e in sub.edges.values():
            k = (e.src, e.rel, e.dst)
            if k in g.edges:
                cur = g.edges[k]
                if (e.attrs or {}).get("papers") and not (cur.attrs or {}).get("papers"):
                    cur.attrs = dict(cur.attrs or {}, **e.attrs)
            else:
                g.add_edge(e)
    if missing:
        notes.append({"source": "合并", "what": "缺少的输入图", "error": "未找到：" + ", ".join(missing)})

    # The industry graph knows an A-share board member is a Chinese company but never said
    # so as an edge -- there was no country node to point at. Now there is, so the link is
    # drawn from the market the record came from, which is a fact in the record itself
    # ("market": "A股"), not an inference about where the company operates.
    MARKET_ISO2 = {"A股": "CN", "港股": "HK", "美股": "US"}
    market_linked = 0
    for n in list(g.nodes.values()):
        if n.kind not in ("listed_company", "company"):
            continue
        iso = MARKET_ISO2.get((n.attrs or {}).get("market"))
        if not iso or ("country:" + iso) not in g.nodes:
            continue
        k = (n.key, "listed_in_country", "country:" + iso)
        if k in g.edges:
            continue
        g.add_edge(Edge(src=n.key, dst="country:" + iso, rel="listed_in_country",
                        attrs={"basis": "证券代码所属市场，不是经营地"},
                        source="同花顺概念板块", url=n.url or "", fetched_at=NOW()))
        market_linked += 1
    if market_linked:
        notes.append({"source": "合并", "what": "上市公司 -> 国家",
                      "error": "%d 条 listed_in_country 边（按上市市场，非经营地）" % market_linked})

    # the payoff: three independent measurements, intersected
    by_iso = {}
    for n in g.nodes.values():
        if n.kind != "country":
            continue
        iso = (n.attrs or {}).get("iso2") or n.key.split(":")[-1]
        d = by_iso.setdefault(iso, {"papers": 0, "trials": 0, "companies": 0, "label": n.label})
        d["papers"] = max(d["papers"], (n.attrs or {}).get("hit_count") or 0)
    for n in g.nodes.values():
        if n.kind == "publication_slice":
            iso = (n.attrs or {}).get("iso2")
            if iso in by_iso:
                by_iso[iso]["papers"] = max(by_iso[iso]["papers"],
                                            (n.attrs or {}).get("hit_count") or 0)
        elif n.kind == "company_slice":
            iso = (n.attrs or {}).get("iso2")
            if iso in by_iso:
                by_iso[iso]["companies"] += (n.attrs or {}).get("hit_count") or 0
    for e in g.edges.values():
        if e.rel == "trial_located_in" and e.dst.startswith("country:"):
            iso = e.dst.split(":")[-1]
            if iso in by_iso:
                by_iso[iso]["trials"] += 1
    triple = {iso: d for iso, d in by_iso.items()
              if d["papers"] and d["trials"] and d["companies"]}
    for iso, d in by_iso.items():
        n = g.nodes.get("country:" + iso)
        if n:
            n.attrs = dict(n.attrs or {}, papers=min(d["papers"], 10 ** 9),
                           trials=d["trials"], companies=d["companies"])
    notes.append({"source": "三源交集", "what": "有论文 且 有试验 且 有企业登记命中的国家",
                  "error": "%d 个：%s" % (len(triple), " ".join(sorted(triple)))})
    notes.append({"source": "合并", "what": "输入图",
                  "error": "；".join("%s %d节点/%d边" % p for p in present)})
    g._notes = notes  # type: ignore[attr-defined]
    return g


def merge_report(g: Graph, top: int = 12) -> str:
    from collections import Counter
    kinds = Counter(n.kind for n in g.nodes.values())
    rels = Counter(e.rel for e in g.edges.values())
    L = ["  合并后：%d 节点 / %d 边" % (len(g.nodes), len(g.edges)),
         "  节点类型：" + "  ".join("%s=%d" % (k, v) for k, v in sorted(kinds.items())),
         "  关系类型：" + "  ".join("%s=%d" % (k, v) for k, v in sorted(rels.items()))]
    rows = [(n.attrs.get("papers") or 0, n.attrs.get("trials") or 0,
             n.attrs.get("companies") or 0, n.label, n.attrs.get("iso2"))
            for n in g.nodes.values() if n.kind == "country"]
    both = [r for r in rows if r[0] and r[1]]
    L.append("  有论文又有试验的国家 %d 个" % len(both))
    L.append("  三源齐全（论文+试验+企业）的国家（按论文数）：")
    triple = sorted([r for r in rows if r[0] and r[1] and r[2]], key=lambda r: -r[0])[:top]
    for p, t, c, label, iso in triple:
        L.append("    %-34s 论文 %5d  试验 %3d  企业命中 %3d" % ((label or iso), p, t, c))
    L.append("  只有论文没有试验的国家数：%d"
             % len([r for r in rows if r[0] and not r[1]]))
    return "\n".join(L)
