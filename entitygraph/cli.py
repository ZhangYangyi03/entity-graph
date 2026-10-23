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
    b.add_argument("--format", choices=["json", "lines", "summary"], default="summary")
    b.add_argument("--out")

    s = sub.add_parser("search", help="resolve a name against an existing graph file")
    s.add_argument("query")
    s.add_argument("--graph", required=True)

    args = ap.parse_args(argv)

    if args.cmd == "build":
        g = build(name=args.name, lei=args.lei, cik=args.cik)
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
