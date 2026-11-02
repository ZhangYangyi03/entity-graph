"""Join the per-chunk shards into one graph, de-duplicating and keeping every caveat."""
import glob
import json
import os

nodes: dict[str, dict] = {}
edges: dict[tuple, dict] = {}
caveats: list[str] = []
for path in sorted(glob.glob(os.path.join("shards", "shard_*.json"))):
    d = json.load(open(path, encoding="utf-8"))
    for n in d.get("nodes", []):
        nodes.setdefault(n["key"], n)
    for e in d.get("edges", []):
        edges.setdefault((e["src"], e["rel"], e["dst"]), e)
    for c in d.get("caveats", []):
        if c not in caveats:
            caveats.append(c)

kinds: dict[str, int] = {}
rels: dict[str, int] = {}
for n in nodes.values():
    kinds[n["kind"]] = kinds.get(n["kind"], 0) + 1
for e in edges.values():
    rels[e["rel"]] = rels.get(e["rel"], 0) + 1

out = {"nodes": list(nodes.values()), "edges": list(edges.values()), "caveats": caveats}
json.dump(out, open("companies.graph.json", "w", encoding="utf-8"), indent=1, ensure_ascii=False)
print(f"shards {len(glob.glob(os.path.join('shards', 'shard_*.json')))}  "
      f"nodes {len(nodes)}  edges {len(edges)}")
print("  kinds:", kinds)
print("  rels :", rels)
