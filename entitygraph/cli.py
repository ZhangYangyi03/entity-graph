"""Command line: `python -m entitygraph.cli chain "Berkshire Hathaway"`."""
from __future__ import annotations

import argparse
import json
import sys

from .build import build
from .resolve import Resolver


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

    args = ap.parse_args(argv)

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
