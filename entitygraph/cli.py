"""Command line: `python -m entitygraph.cli chain "Berkshire Hathaway"`."""
from __future__ import annotations

import argparse
import json
import sys

from .build import build
from .resolve import Resolver



def _history(args) -> int:
    from .history import SideTable
    from .model import Edge, Graph, Node
    from .timeline import Timeline, from_graph

    raw = json.load(open(args.graph, encoding="utf-8"))
    g = Graph()
    for n in raw["nodes"]:
        g.add_node(Node(**n))
    if raw["edges"]:
        st, notes = _date_raw(raw, g)      # already dated, already versioned
    else:
        dated, notes = from_graph(g)
        st = SideTable.from_graph(dated)

    if args.check:
        chk = Timeline(_collapse(st, g)).check()
        print(f"edges {chk['edges']}  undated {len(chk['undated'])}  inverted {len(chk['inverted'])}")
        for n in notes[:10]:
            print(f"  undated  {n['source']}  {n['key'][0]} -{n['key'][1]}-> {n['key'][2]}")
        return 0

    if args.as_of:
        return _emit(st.as_of(args.as_of, graph=g), args.format)
    if args.between:
        start, end = args.between
        for b, nxt, sub in Timeline(_collapse(st, g)).segment(start, end):
            print(f"{b} .. {nxt}   edges {len(sub.edges):>4}  nodes {len(sub.nodes):>4}")
        return 0
    ap_err = "pass --as-of, --between or --check"
    print(ap_err)
    return 2


def _date_raw(raw: dict, g: Graph) -> tuple[Graph, list[dict]]:
    """Load the edges with their dates straight out of the JSON.

    A Graph keys edges by (src, rel, dst), so loading a four-year history into one throws
    away every version but the last. The history commands therefore date the *raw* records
    and hand the side table the versions, using the graph only for nodes.
    """
    from .history import SideTable
    from .model import Edge, Graph
    from .timeline import DEFAULT_MAPS, _date_of

    by_triple: dict[tuple[str, str, str], list[Edge]] = {}
    notes: list[dict] = []
    for rec in raw["edges"]:
        e = Edge(**rec)
        # A date the edge already carries wins over the source's default attribute: the
        # satellite catalogue writes valid_from per edge (the launch date) while the default
        # map for its source expects `date`, and using the default would have silently
        # dropped all 29,488 of them.
        if (e.attrs.get("valid_from") or e.attrs.get("date")
                or (e.attrs.get("valid_to") is not None)):
            by_triple.setdefault(e.key, []).append(e)
            continue
        m = DEFAULT_MAPS.get(e.source)
        if not m:
            notes.append({"what": "undated", "source": e.source, "key": list(e.key),
                          "why": "no date attribute mapped for this source"})
            continue
        by_triple.setdefault(e.key, []).append(e)

    out = Graph()
    for n in g.nodes.values():
        out.add_node(n)
    st = SideTable()
    def _start(e) -> str:
        return _date_of(str(e.attrs.get("valid_from") or e.attrs.get("date") or ""))

    for key, edges in by_triple.items():
        edges = sorted(edges, key=_start)
        for i, e in enumerate(edges):
            start = _start(e)
            end = (_date_of(str(edges[i + 1].attrs.get("valid_from")
                                or edges[i + 1].attrs.get("date") or ""))
                   if i + 1 < len(edges) else "")
            attrs = {**e.attrs, "valid_from": start}
            if end and end > start:
                attrs["valid_to"] = end
            st.add(Edge(src=e.src, dst=e.dst, rel=e.rel, attrs=attrs,
                        source=e.source, url=e.url, fetched_at=e.fetched_at),
                   supersedes=(edges[i + 1].url if i + 1 < len(edges) else ""))
    return st, notes


def _collapse(st, g) -> object:
    """A dated Graph for segment walking: nodes from g, edges as of the latest start."""
    from .model import Graph
    out = Graph()
    for n in g.nodes.values():
        out.add_node(n)
    for e in st.collapse().edges.values():
        out.edges[e.key] = e
    return out


def _emit(sub: Graph, fmt: str) -> int:
    if fmt == "json":
        print(sub.to_json())
    elif fmt == "lines":
        print(sub.dumps_lines())
    else:
        d = sub.stats()
        print(f"nodes {d['nodes']}  edges {d['edges']}")
        print("kinds: " + " ".join(f"{k}={v}" for k, v in sorted(d["by_kind"].items())))
        print("rels:  " + " ".join(f"{k}={v}" for k, v in sorted(d["by_rel"].items())))
    return 0


def _orbit(args) -> int:
    """Where things are, and which two of them are close. Reads files, never the network."""
    import json as _json
    from . import orbit as _o

    # `military` reads only the catalogue; the element files are not needed for it.
    elements = []
    for path in (args.elements or []):
        for rec in _json.load(open(path, encoding="utf-8")):
            elements.append(_o.from_gp(rec))
    if not elements and args.what != "military":
        print("need at least one --elements file (CelesTrak GP JSON)", file=sys.stderr)
        return 2
    t0 = max((e.epoch_ts for e in elements), default=0.0)

    if args.what == "catalog":
        import collections as _c
        alts = [e.altitude for e in elements]
        bands = _c.Counter("LEO" if h < 2000 else ("MEO" if h < 30000 else "GEO+") for h in alts)
        out = {
            "objects": len(elements),
            "epoch": max(e.epoch for e in elements),
            "altitude_km": {"min": min(alts), "median": sorted(alts)[len(alts) // 2],
                            "max": max(alts)},
            "bands": dict(bands),
        }
        if args.satcat:
            from . import satcat as _sat
            rows = _sat.parse_rows(_json.load(open(args.satcat, encoding="utf-8")))
            out["catalogue"] = _sat.summary(rows)
        if args.format == "json":
            print(_json.dumps(out, ensure_ascii=False, indent=2))
        else:
            print(f"objects {out['objects']}  epoch {out['epoch'][:19]}")
            print(f"altitude_km  min {out['altitude_km']['min']:.0f}  "
                  f"median {out['altitude_km']['median']:.0f}  max {out['altitude_km']['max']:.0f}")
            print("bands: " + " ".join(f"{k}={v}" for k, v in sorted(bands.items())))
            cat = out.get("catalogue")
            if cat:
                print(f"on orbit {cat['on_orbit']}  types: " +
                      " ".join(f"{k}={v}" for k, v in cat["by_object_type"].items()))
                print("top owners:   " + "  ".join(
                    f"{k}={v}" for k, v in list(cat["by_owner"].items())[:8]))
                print("top operators:" + "  ".join(
                    f"{k}={v}" for k, v in list(cat["by_operator"].items())[:8]))
        return 0

    if args.what == "military":
        from . import satcat as _sat
        if not args.satcat:
            print("military needs --satcat", file=sys.stderr)
            return 2
        rows = [r for r in _sat.parse_rows(_json.load(open(args.satcat, encoding="utf-8")))
                if not r.decay_date]
        a = _sat.mission_audit(rows)
        if args.format == "json":
            out = dict(a)
            out["launch_timeline"] = _sat.launch_timeline(rows)
            print(_json.dumps(out, ensure_ascii=False, indent=2))
            return 0
        no_orbit = sum(1 for r in rows
                       if _sat.military_series_of(r.name)[0] and _sat.band_of(r) == "?")
        print(f"declared military / dual-use objects: {a['declared_objects']}"
              f"   (no orbit stated: {no_orbit})")
        print()
        print("role                     count   bands")
        for role, v in a["by_role"].items():
            b = " ".join(f"{k}={n}" for k, n in sorted(v["bands"].items()))
            print(f"{role:<24} {v['count']:>5}   {b}")
        tl = _sat.launch_timeline(rows)
        cum = {"PRC": 0, "US": 0, "CIS": 0, "other": 0}
        print()
        print("declared military / dual-use, still on orbit, by launch year")
        print("year   new   PRC    US   CIS  other   cumulative PRC / US")
        for y, d in tl.items():
            for k in cum:
                cum[k] += d[k]
            print(f"{y}  {d['total']:>4}  {d['PRC']:>4}  {d['US']:>4}  {d['CIS']:>4}  "
                  f"{d['other']:>5}   {sum(cum.values()):>5}  ({cum['PRC']} / {cum['US']})")
        print()
        print(f"orbit disagrees with declared role: {len(a['mismatches'])}")
        for m in a["mismatches"]:
            print(f"  {m['name'][:28]:<28} {m['role']:<22} {m['band']:<4} "
                  f"expected {'/'.join(m['expected'])}")
        return 0

    # close approaches
    pairs = _o.candidate_pairs(elements)
    real = [(a, b) for a, b in pairs if not _o.is_duplicate_geometry(a, b)]
    # Centre the window on the freshest element epoch rather than running forward from it:
    # element sets of different ages all describe the same instant well at their own epoch,
    # and a forward window spends its time extrapolating the oldest ones.
    t_start = t0 - args.window_min * 60.0
    hours = (args.window_min * 2) / 60.0
    hits = _o.screen(real, t_start, hours=hours, step_s=args.step,
                     threshold_km=args.threshold)
    best = {}
    for h in hits:
        k = (h.a, h.b)
        if k not in best or h.km < best[k].km:
            best[k] = h
    ranked = sorted(best.values(), key=lambda h: h.km)[:args.top]

    if args.format == "json":
        print(_json.dumps([{"a": h.a, "b": h.b, "km": round(h.km, 3), "t": h.iso}
                           for h in ranked], ensure_ascii=False, indent=2))
        return 0

    print(f"objects {len(elements)}  candidates {len(pairs)} "
          f"(identical geometry {len(pairs) - len(real)})  "
          f"window ±{args.window_min:g}min  step {args.step:g}s  "
          f"threshold {args.threshold:g}km")
    print(f"pairs within threshold: {len(best)}")
    if not ranked:
        print("none at this sampling. That is not the same as none at all: the minimum "
              "between two samples is lower than anything reported here.")
        return 0
    for h in ranked:
        print(f"{h.km:9.3f} km  {h.iso}  {h.a[:28]:<28} <-> {h.b[:28]}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="entitygraph")
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="build a graph and print it")
    b.add_argument("--name")
    b.add_argument("--lei")
    b.add_argument("--cik")
    b.add_argument("--country", action="append",
                   help="restrict registry lookup to one or more ISO codes (FR, NO, DK, FI, CH, CZ, BR)")
    b.add_argument("--registry-id", metavar="ID",
                   help="look up by national id instead of name (needs --country)")
    b.add_argument("--no-officers", action="store_true", help="skip officer/director expansion")
    b.add_argument("--format", choices=["json", "lines", "summary"], default="summary")
    b.add_argument("--out")

    s = sub.add_parser("search", help="resolve a name against an existing graph file")
    s.add_argument("query")
    s.add_argument("--graph", required=True)


    bk = sub.add_parser("banking", help="the currency-and-banking graph (currencies, products, rates, banks)")
    bk.add_argument("--out", default="banking.graph.json")
    bk.add_argument("--limit", type=int, default=10000, help="max FDIC banks")
    bk.add_argument("--no-rates", action="store_true", help="skip ECB/World Bank rates")
    bk.add_argument("--no-banks", action="store_true", help="skip FDIC banks")
    bk.add_argument("--no-products", action="store_true", help="skip product feeds")
    bk.add_argument("--format", choices=["summary", "lines"], default="summary")

    q = sub.add_parser("query", help="ask a built banking graph a question")
    q.add_argument("--graph", default="banking.graph.json")
    q.add_argument("--top", type=int, default=10)
    q.add_argument("what", choices=["banks", "products", "currencies", "countries", "rates"])
    q.add_argument("--country", help="ISO-ish country key, e.g. united-states")

    h = sub.add_parser("history", help="ask a graph what it looked like on a date")
    h.add_argument("--graph", required=True, help="graph JSON built earlier (e.g. apple.graph.json)")
    h.add_argument("--as-of", metavar="YYYY-MM-DD", help="slice on this date")
    h.add_argument("--between", nargs=2, metavar=("START", "END"),
                   help="walk every segment between two dates")
    h.add_argument("--check", action="store_true", help="only report which edges carry no date")
    h.add_argument("--format", choices=["summary", "lines", "json"], default="summary")

    orb = sub.add_parser("orbit", help="positions and close approaches from CelesTrak elements")
    orb.add_argument("what", choices=["catalog", "close", "military"])
    orb.add_argument("--elements", action="append",
                     help="GP JSON file(s) from CelesTrak; repeatable")
    orb.add_argument("--satcat", help="satcat JSON file, for owner/operator joins")
    orb.add_argument("--hours", type=float, default=2.0, help="window length for `close`")
    orb.add_argument("--step", type=float, default=60.0, help="sampling step in seconds")
    orb.add_argument("--threshold", type=float, default=25.0, help="report pairs within this many km")
    orb.add_argument("--window-min", type=float, default=30.0,
                     help="minutes before and after the epoch to sample")
    orb.add_argument("--top", type=int, default=15)
    orb.add_argument("--format", choices=["summary", "json"], default="summary")

    args = ap.parse_args(argv)

    if args.cmd == "orbit":
        return _orbit(args)

    if args.cmd == "build":
        g = build(name=args.name, lei=args.lei, cik=args.cik, countries=args.country,
                  registry_id=args.registry_id, registry_country=args.country[0] if args.country else None,
                  officers=not args.no_officers)
        if args.format == "json":
            out = g.to_json()
        elif args.format == "lines":
            out = g.dumps_lines()
        else:
            st = g.stats()
            lines = [f"nodes {st['nodes']}  edges {st['edges']}",
                     "kinds: " + ", ".join(f"{k}={v}" for k, v in st["by_kind"].items()),
                     "rels:  " + ", ".join(f"{k}={v}" for k, v in st["by_rel"].items())]
            for n in list(g.nodes.values())[:12]:
                lines.append(f"  [{n.kind}] {n.label}  ({n.source})")
            notes = getattr(g, "_notes", [])
            if notes:
                lines.append("gaps:")
                lines += [f"  ! {x['source']} {x['what']}: {x['error'][:90]}" for x in notes]
            out = "\n".join(lines)
        if args.out:
            open(args.out, "w", encoding="utf-8").write(out)
            print(f"wrote {args.out}")
        else:
            print(out)
        return 0


    if args.cmd == "banking":
        from .banking import build_currency_graph
        g = build_currency_graph(limit=args.limit, rates=not args.no_rates,
                                 banks=not args.no_banks, products=not args.no_products)
        if args.format == "lines":
            out = g.dumps_lines()
        else:
            st = g.stats()
            lines = [f"nodes {st['nodes']}  edges {st['edges']}",
                     "kinds: " + ", ".join(f"{k}={v}" for k, v in
                                           sorted(st["by_kind"].items(), key=lambda x: -x[1])),
                     "rels:  " + ", ".join(f"{k}={v}" for k, v in
                                           sorted(st["by_rel"].items(), key=lambda x: -x[1])),
                     "sources:"]
            lines += [f"  {n['source']:<24} {n['what']:<6} {n['error'][:70]}"
                      for n in getattr(g, "_notes", [])]
            out = "\n".join(lines)
        open(args.out, "w", encoding="utf-8").write(g.to_json() if hasattr(g, "to_json") else json.dumps(g.to_dict(), ensure_ascii=False))
        print(out)
        print(f"wrote {args.out}")
        return 0

    if args.cmd == "query":
        blob = json.load(open(args.graph, encoding="utf-8"))
        nodes = {n["key"]: n for n in blob["nodes"]}
        edges = blob["edges"]
        if args.what == "banks":
            rows = [(n["attrs"].get("asset") or 0, n) for n in blob["nodes"] if n["kind"] == "bank"]
            rows.sort(key=lambda r: -r[0])
            print("asset (thousand USD)  name  state  holding company")
            parent = {e["src"]: e["attrs"].get("name") or e["dst"]
                      for e in edges if e["rel"] == "subsidiary_of"}
            for a, n in rows[:args.top]:
                hc = (parent.get(n["key"]) or "")[:26]
                print(f"{a:>20,}  {n['label'][:38]:<38}  {n['attrs'].get('stalp',''):<3} {hc}")
        elif args.what == "products":
            for n in [x for x in blob["nodes"] if x["kind"] == "bank_product"][:args.top]:
                print(f"{n['key'][:46]:<46} {str(n['attrs'].get('segment'))[:20]:<20} {n['label'][:56]}")
        elif args.what == "currencies":
            for n in [x for x in blob["nodes"] if x["kind"] == "currency"][:args.top]:
                cs = n["attrs"].get("countries") or []
                print(f"{n['attrs'].get('alpha_code',''):<4} {n['label'][:38]:<38} minor={n['attrs'].get('minor_units')} countries={len(cs)}")
        elif args.what == "countries":
            for n in [x for x in blob["nodes"] if x["kind"] == "country"][:args.top]:
                print(f"{n['key'][:40]:<40} {n['label'][:40]}")
        elif args.what == "rates":
            want = args.country
            shown = 0
            for e in edges:
                if e["rel"] != "offered_in" or "rate" not in e["attrs"]:
                    continue
                if want and want not in e["dst"]:
                    continue
                src = nodes.get(e["src"], {})
                print(f"{e['attrs']['rate']:>7}%  {e['attrs'].get('period','')}  {src.get('label','')[:58]} @ {e['dst']}")
                shown += 1
                if shown >= args.top:
                    break
        return 0

    if args.cmd == "history":
        return _history(args)

    if args.cmd == "search":
        from .model import Graph, Node, Edge
        blob = json.load(open(args.graph, encoding="utf-8"))
        g = Graph()
        for n in blob["nodes"]:
            g.add_node(Node(**n))
        for e in blob["edges"]:
            g.add_edge(Edge(**e))
        for m in Resolver(g).candidates(args.query, threshold=0.3):
            print(f"{m.score:.2f}  {m.key}  {m.label}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
