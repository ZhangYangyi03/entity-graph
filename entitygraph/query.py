"""Asking the graph a question that a person would actually ask.

An entity graph earns its keep at one moment: somebody types a name on a command line
and gets an answer that is true, cited, and aggregated the way the question means it.
Two things make that harder than it sounds, and both are handled here.

  The same entity has several nodes. The satellite catalogue calls a state country:usa,
  the ISO layer calls it country:US, the bank layer calls it COUNTRY:ICELAND, and a
  country-iso3: node may carry the alpha-3. Counting "satellites per country" over the
  raw edges therefore double counts, and the fold through same_country_as is not an
  optional nicety -- `fold` is the default in every aggregate below.

  A count is only worth printing with its denominator. "US 13,101 satellites" is a lie
  if 684 objects have no state at all. Every answer here carries the population it was
  computed over and the nodes it could not classify.

Nothing in this module invents data: it reads what the builders wrote, folds identity,
and states the denominator.
"""
from __future__ import annotations

import collections

from . import bridge


# ------------------------------------------------------------------------------- identity

def fold_map(graph) -> dict[str, str]:
    """node key -> the representative key of its identity group.

    same_country_as edges are directed at the representative, so following them one hop
    is enough; the union-find below is only there for a graph written by an older
    builder where a chain happened to form.
    """
    parent: dict[str, str] = {k: k for k in graph.nodes}

    def find(k: str) -> str:
        root = k
        while parent.get(root, root) != root:
            root = parent[root]
        while parent.get(k, k) != root:
            parent[k], k = root, parent[k]
        return root

    for e in graph.edges.values():
        if e.rel not in ("same_country_as", "same_as"):
            continue
        if e.src in parent and e.dst in parent:
            a, b = find(e.src), find(e.dst)
            if a != b:
                parent[a] = b
    return {k: find(k) for k in graph.nodes}


def pref_label(graph, key: str) -> str:
    """The label a person would recognise: prefer the one with Chinese/English, not a code."""
    n = graph.nodes.get(key)
    if n is None:
        return key
    lab = (n.label or "").strip()
    return lab or key


# ------------------------------------------------------------------------------- adjacency

class Neighbourhood:
    """An undirected index over the graph, built once, cheap to query many times."""

    def __init__(self, graph):
        self.g = graph
        self.fold = fold_map(graph)
        self.edges_by_src = collections.defaultdict(list)
        self.edges_by_dst = collections.defaultdict(list)
        self._adj: dict[str, set[str]] = collections.defaultdict(set)
        for e in graph.edges.values():
            if e.src not in graph.nodes or e.dst not in graph.nodes:
                continue
            self.edges_by_src[e.src].append(e)
            self.edges_by_dst[e.dst].append(e)
            if e.rel in ("same_country_as", "same_as"):
                continue                     # identity is a fold, not a relationship
            a, b = self.fold.get(e.src, e.src), self.fold.get(e.dst, e.dst)
            if a != b:
                self._adj[a].add(b)
                self._adj[b].add(a)

    def degree(self, key: str) -> int:
        return len(self._adj.get(self.fold.get(key, key), ()))


def resolve(graph, text: str, limit: int = 8) -> list[str]:
    """Keys a name could mean: exact key, exact folded label, then substring."""
    q = (text or "").strip().lower()
    if not q:
        return []
    if q in graph.nodes:
        return [q]
    # A key typed in the wrong case (country:US vs country:us) is still that key; without
    # this the search falls through to labels and returns every node whose label is a
    # substring of the key, which is how "country:US" came back with two answers.
    for k in graph.nodes:
        if k.lower() == q:
            return [k]
    exact, loose = [], []
    for k, n in graph.nodes.items():
        lab = (n.label or "").lower()
        if not lab:
            continue
        if lab == q:
            exact.append(k)
        elif q in lab or lab in q:
            loose.append(k)
    out = exact + [k for k in loose if k not in exact]
    return out[:limit]


# ------------------------------------------------------------------------------- the answers

def satellites_by_country(graph, top: int = 15) -> dict:
    """In-orbit objects per state, folded, with the population and the refusals stated."""
    nb = Neighbourhood(graph)
    counts: collections.Counter = collections.Counter()
    satellites = [n for n in graph.nodes.values() if n.kind == "satellite"]
    for e in graph.edges.values():
        if e.rel != "owned_by_state":
            continue
        src = graph.nodes.get(e.src)
        dst = graph.nodes.get(e.dst)
        if src is None or src.kind != "satellite" or dst is None or dst.kind != "country":
            continue
        counts[nb.fold.get(e.dst, e.dst)] += 1
    rows = [(pref_label(graph, k), v) for k, v in counts.most_common(top)]
    return {
        "question": "每个国家名下有多少在轨物体（折叠同一国家的多套写法）",
        "population": len(satellites),
        "classified": sum(counts.values()),
        "unclassified": len(satellites) - sum(counts.values()),
        "rows": rows,
        "note": "unclassified 是 owner code 属运营商/财团（SES/EUTE/TBD/ESA 等）或目录未给国家的物体；"
                "它们不并入任何国家，见 connect 的 unresolvable_codes",
        "operated_by_rows": [(pref_label(graph, d), c) for d, c in collections.Counter(
            e.dst for e in graph.edges.values()
            if e.rel == "operated_by" and graph.nodes.get(e.src) is not None
            and graph.nodes[e.src].kind == "satellite").most_common(8)],
    }


def satellites_by_operator(graph, top: int = 10) -> dict:
    cnt = collections.Counter()
    sats = 0
    for e in graph.edges.values():
        if e.rel != "operated_by":
            continue
        src = graph.nodes.get(e.src)
        if src is None or src.kind != "satellite":
            continue
        cnt[e.dst] += 1
        sats += 1
    total = sum(1 for n in graph.nodes.values() if n.kind == "satellite")
    return {"population": total, "classified": sats, "unclassified": total - sats,
            "rows": [(pref_label(graph, k), v) for k, v in cnt.most_common(top)]}


def banks_by_state(graph, top: int = 10) -> dict:
    """US bank assets by state. The unit is the feed's: thousands of USD."""
    agg: collections.Counter = collections.Counter()
    n_by: collections.Counter = collections.Counter()
    banks = [n for n in graph.nodes.values() if n.kind == "bank"]
    for n in banks:
        st = (n.attrs.get("stname") or "").strip() or "(未给州)"
        a = n.attrs.get("asset")
        if isinstance(a, (int, float)):
            agg[st] += a
            n_by[st] += 1
    total = sum(agg.values())
    return {"question": "美国各州银行资产合计（千美元，FDIC 口径）",
            "population": len(banks), "classified": sum(n_by.values()),
            "unclassified": len(banks) - sum(n_by.values()),
            "total_assets_thousand_usd": total,
            "rows": [("%s" % st, n_by[st], agg[st]) for st, _ in agg.most_common(top)]}


def board_members(graph, board: str) -> dict:
    """A concept board's constituents with the figures the feed actually carried."""
    keys = resolve(graph, board)
    if not keys:
        return {"error": f"找不到 {board!r}"}
    bk = keys[0]
    members = [e.src for e in graph.edges.values() if e.rel == "in_board" and e.dst == bk]
    rows = []
    with_fig = 0
    tot = 0.0
    for m in members:
        n = graph.nodes.get(m)
        if n is None:
            continue
        mcap = n.attrs.get("total_mcap_yi")
        pe = n.attrs.get("pe_ttm")
        flow = n.attrs.get("main_net_in")
        if isinstance(mcap, (int, float)):
            tot += mcap
            with_fig += 1
        rows.append((n.label, n.attrs.get("code"), mcap, pe, flow))
    rows.sort(key=lambda r: -(r[2] or 0))
    return {"question": f"{pref_label(graph, bk)} 板块成分", "board": bk,
            "population": len(members), "with_figures": with_fig,
            "total_mcap_yi": round(tot, 1), "rows": rows[:25],
            "note": "市值/PE 来自同花顺+腾讯行情快照，带 moneyflow_as_of 时间戳；不是实时"}


def profile(graph, text: str) -> dict:
    """Everything the graph knows about one name, grouped by relation and folded."""
    keys = resolve(graph, text)
    if not keys:
        return {"error": f"找不到 {text!r}", "tried": text}
    k = keys[0]
    nb = Neighbourhood(graph)
    n = graph.nodes[k]
    out_edges = nb.edges_by_src.get(k, [])
    in_edges = nb.edges_by_dst.get(k, [])
    groups: dict[str, list] = collections.defaultdict(list)
    for e in out_edges:
        groups[e.rel].append(pref_label(graph, e.dst))
    for e in in_edges:
        groups["<-" + e.rel].append(pref_label(graph, e.src))
    return {"key": k, "kind": n.kind, "label": n.label, "attrs": n.attrs,
            "degree": nb.degree(k), "groups": {g: v[:12] for g, v in groups.items()}}


def path(graph, a: str, b: str, max_hops: int = 6) -> dict:
    """Shortest path between two names, with the edges named so the hops can be read."""
    ka, kb = resolve(graph, a, 1), resolve(graph, b, 1)
    if not ka or not kb:
        return {"error": "一端找不到", "a": a, "b": b, "found_a": ka, "found_b": kb}
    nb = Neighbourhood(graph)
    src, dst = nb.fold.get(ka[0], ka[0]), nb.fold.get(kb[0], kb[0])
    prev: dict[str, str] = {src: ""}
    frontier = [src]
    depth = 0
    while frontier and depth < max_hops:
        nxt = []
        for v in frontier:
            for w in nb._adj.get(v, ()):
                if w in prev:
                    continue
                prev[w] = v
                nxt.append(w)
        if dst in prev:
            break
        frontier = nxt
        depth += 1
    if dst not in prev:
        return {"connected": False, "hop_limit": max_hops, "a": ka[0], "b": kb[0]}
    chain = [dst]
    while prev[chain[-1]]:
        chain.append(prev[chain[-1]])
    chain.reverse()
    return {"connected": True, "hops": len(chain) - 1,
            "chain": [pref_label(graph, x) for x in chain]}


def issuer_evidence(graph, board: str = "脑机接口", limit: int = 30) -> dict:
    """One listed company, three layers joined: what it did, what it is worth, what moved.

    This is the report the graph was built to be able to produce and could not before:
    a cninfo announcement naming a project, the concept board the company sits on, the
    price/valuation snapshot, and the margin/forecast figures -- all on one row, because
    the three feeds share a 6-digit exchange code and nothing had joined them.

    Every row states which layer supplied which field, and a field a layer did not give
    is None rather than 0: "no announcement found" and "announcement says nothing" are
    different facts.
    """
    rows = board_members(graph, board)
    if "error" in rows:
        return rows
    nb = Neighbourhood(graph)
    fold = nb.fold
    out = []
    for label, code, mcap, pe, flow in rows["rows"][:limit]:
        if not code:
            continue
        issuer = fold.get(f"cnstock:{code}") or f"cnstock:{code}"
        node = graph.nodes.get(issuer) or graph.nodes.get(f"listed_company:{code}")
        # Two spellings of the same link in this graph: the cninfo feed writes
        # cnstock:X --discloses--> announcement:..., the 巨潮 index writes
        # announcement:... --announced--> cnstock:X. Both are the same fact, so both
        # directions are read rather than one being silently missed.
        disc = []
        for e in graph.edges.values():
            if e.rel in ("discloses", "announced", "filed"):
                if e.src == issuer:
                    disc.append(e.dst)
                elif e.dst == issuer:
                    disc.append(e.src)
        texts = []
        for d in dict.fromkeys(disc):
            n = graph.nodes.get(d)
            if n is not None and n.label:
                texts.append(n.label)
        margin = [e for e in graph.edges.values()
                  if e.src == issuer and e.rel == "margin_balance_of"]
        forecast = [e for e in graph.edges.values()
                    if e.src == issuer and e.rel == "forecast_for"]
        out.append({
            "label": label, "code": code, "issuer_key": issuer,
            "mcap_yi": mcap, "pe_ttm": pe,
            "announcements": texts[:4],
            "announcement_count": len(texts),
            "margin": margin[0].attrs if margin else None,
            "forecast": forecast[0].attrs if forecast else None,
            "has_market_node": f"cnstock:{code}" in graph.nodes,
        })
    covered = sum(1 for r in out if r["announcement_count"])
    return {"question": f"{board} 板块：公司 + 公告 + 估值 + 两融/预测，按交易所代码归一",
            "population": rows["population"], "with_figures": rows["with_figures"],
            "with_announcements": covered,
            "unmatched_rows": len(rows["rows"]) - len(out),
            "total_mcap_yi": rows["total_mcap_yi"], "rows": out,
            "note": "公告来自巨潮标题索引（原文 PDF 在节点 url 上），估值来自同花顺+腾讯快照；"
                    "字段为 None 表示该层没有给这条数据，不是 0"}
