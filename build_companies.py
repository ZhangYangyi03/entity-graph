"""Build companies.graph.json: the Fortune pool, plus the two Chinese entities asked for.

Every node cites its URL. Three things are deliberately visible in the output:
  - nodes that matched no registry (`registry_match: none`)
  - edges that came from a hand-entered record (`source: manual`)
  - a `caveats` list on the graph naming what this build could not establish

    python build_companies.py [--limit N]
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import sys
import time

OUT = "companies.graph.json"
# A chunk writes its own shard; merge_shards() later joins them. Chunking exists because a
# full pass over 55 companies downloads hundreds of SEC pages and cannot finish inside one
# process budget here.
SHARD_DIR = "shards"

NOW = lambda: _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")

from entitygraph.companies import (MANUAL, add_company, add_holders, add_manual, match,
                                   ticker_for)
from entitygraph.fortune import CITE as FORTUNE_CITE, fetch
from entitygraph.model import Edge, Graph, Node

# The two entities the operator named, and what they can honestly be attached to.
# 南大通用 is the operator of GBase, the domestic database. Its relationship to anything
# Fortune-listed is not established by any source in this repository, so it hangs off
# nothing -- inventing an edge would be the one thing this repository refuses to do.
LINK = {
    "tianjin-nankai-general-data": ("", "", ""),
    "xieyi": ("", "", ""),
}


def main(argv: list[str]) -> int:
    limit = None
    offset = 0
    resume = False
    only_holders = False
    shard = False
    for i, a in enumerate(argv):
        if a == "--limit" and i + 1 < len(argv):
            limit = int(argv[i + 1])
        if a == "--offset" and i + 1 < len(argv):
            offset = int(argv[i + 1])
        if a == "--resume":
            resume = True
        if a == "--only-holders":
            only_holders = True
        if a == "--shard":
            shard = True

    rows = fetch()
    rows = rows[offset:]
    if limit:
        rows = rows[:limit]
    g = Graph()
    caveats: list[str] = []
    # --resume merges into the graph already on disk, so a long build can be run in chunks
    # and a chunk that dies does not throw away the ones before it.
    shard_path = os.path.join(SHARD_DIR, f"shard_{offset:02d}_{(offset + (limit or 0)):02d}.json")
    if resume and os.path.exists(shard_path):
        prev = json.load(open(shard_path, encoding="utf-8"))
        for n in prev.get("nodes", []):
            g.add_node(Node(**n))
        for e in prev.get("edges", []):
            g.add_edge(Edge(**e))
        print(f"resumed: {len(g.nodes)} nodes, {len(g.edges)} edges", flush=True)

    holder_edges = 0
    for r in rows:
        try:
            node = add_company(g, r)
        except Exception as exc:                    # noqa: BLE001
            caveats.append(f"rank {r['rank']} {r['company']}: {type(exc).__name__}: {exc}")
            print(f"    !! {r['company']}: {type(exc).__name__}: {exc}", flush=True)
            continue
        hit = ticker_for(r["company"])
        print(f"    [{r['rank']}] {r['company'][:28]:<30} ticker={hit}", flush=True)
        if not hit:
            continue
        try:
            g.add_node(Node(key=node.key, kind="company", label=node.label,
                            attrs={"cik": str(hit[1]), "ticker": hit[0]},
                            source="SEC EDGAR",
                            url=f"https://data.sec.gov/submissions/CIK{str(hit[1]).zfill(10)}.json",
                            fetched_at=NOW()))
            # The holders belong to the company Fortune named, not to whichever legal
            # entity the GLEIF record happened to point at (Amazon B.V. is a subsidiary).
            # Prefer the node that IS the Fortune company (the GLEIF record for the name
            # "Amazon" is AMAZON.COM, INC.); fall back to the row's own node.
            target_key = f"fortune:{r['rank']}"
            g.add_node(Node(key=f"fortune:{r['rank']}", kind="company", label=r["company"],
                            attrs={"fortune_rank": r["rank"], "cik": str(hit[1]),
                                   "ticker": hit[0]},
                            source="Fortune (via Wikipedia)",
                            url=r.get("cite_url", ""), fetched_at=NOW()))
            holder_edges += add_holders(g, hit[1], target_key,
                                        issuer_name=r["company"])
        except Exception as exc:                    # noqa: BLE001
            caveats.append(f"rank {r['rank']} {r['company']} holders: "
                           f"{type(exc).__name__}: {exc}")
        time.sleep(0.1)

    for slug, (rel, target, note) in LINK.items():
        add_manual(g, slug, rel, target, note)

    # What the build could NOT establish, stated in the artifact rather than in a README
    # nobody reads next to the file.
    unmatched = sorted(n.label for n in g.nodes.values()
                       if n.attrs.get("registry_match") == "none")
    caveats.append(f"{len(unmatched)} Fortune rows matched no GLEIF legal entity: "
                   + ", ".join(unmatched))
    caveats.append("Fortune's own Global 500 page is paywalled past the top ten and the "
                   "Wikipedia ranking article carries only top-ten tables; the pool here is "
                   "the 55-row 'largest companies by revenue' list, so it is a partial 500.")
    caveats.append("GLEIF returns 404 for /direct-parent and /ultimate-parent on a "
                   "lei-record in this copy of the API, so no ownership-parent edge is "
                   "drawn from GLEIF at all. Ownership in this graph comes from SEC "
                   "13D/13G cover pages only.")
    caveats.append("中石油 matched to PetroChina Company Limited, the listed subsidiary, not "
                   "to China National Petroleum Corporation, the parent: GLEIF has no record "
                   "under the parent's English name.")
    caveats.append("南大通用 and 协医 have no LEI and the Chinese national credit registry "
                   "(国家企业信用信息公示系统) exposes no open API, so both are manual "
                   "records with no registry attributes and no edges. Their legal identities "
                   "are unverified.")
    caveats.append("No ownership edge links 南大通用 or 协医 to any Fortune company: no "
                   "source in this build establishes one, and none was invented.")

    out = {
        "nodes": [n.__dict__ for n in g.nodes.values()],
        "edges": [e.__dict__ for e in g.edges.values()],
        "caveats": caveats,
    }
    target = OUT
    if shard:
        os.makedirs(SHARD_DIR, exist_ok=True)
        target = os.path.join(SHARD_DIR, f"shard_{offset:02d}_{offset + len(rows):02d}.json")
    with open(target, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1, ensure_ascii=False)
    print(f"nodes {len(out['nodes'])}  edges {len(out['edges'])}")
    print(f"holder edges {holder_edges}")
    print(f"matched {sum(1 for n in g.nodes.values() if n.attrs.get('lei'))} "
          f"of {len(rows)} Fortune rows to a legal entity")
    for c in caveats:
        print(f"  caveat: {c[:150]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
