"""Build a graph for a real company and print what it found, with sources.

    python examples/demo.py "Berkshire Hathaway"
"""
import sys

from entitygraph.build import build

name = sys.argv[1] if len(sys.argv) > 1 else "Berkshire Hathaway"
g = build(name=name)

st = g.stats()
print(f"{name}: {st['nodes']} nodes, {st['edges']} edges")
for e in list(g.edges.values())[:10]:
    print(f"  {e.src} --[{e.rel}]--> {e.dst}   <- {e.source}")
notes = getattr(g, "_notes", [])
if notes:
    print("gaps (sources that did not answer):")
    for n in notes:
        print(f"  {n['source']}/{n['what']}: {n['error'][:100]}")
