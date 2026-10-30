"""Build the currency-and-banking graph and write it to disk.

Run as a script rather than an inline snippet because the whole build takes ~80s the
first time (21 ECB MIR series + 4,217 FDIC banks) and a one-shot snippet gets killed
before it finishes. A script can also be re-run to refresh the cache.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from entitygraph.banking import build_currency_graph

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "banking.graph.json")
LOG = os.path.join(HERE, "banking.build.log")


def main() -> int:
    lines = []
    t0 = time.time()
    g = build_currency_graph(limit=10000)
    st = g.stats()
    lines.append("elapsed %.1fs" % (time.time() - t0))
    lines.append("nodes %d  edges %d" % (st["nodes"], st["edges"]))
    lines.append("kinds: " + json.dumps(dict(sorted(st["by_kind"].items(),
                                                   key=lambda x: -x[1]))))
    lines.append("rels:  " + json.dumps(dict(sorted(st["by_rel"].items(),
                                                   key=lambda x: -x[1]))))
    for n in getattr(g, "_notes", []):
        lines.append("  note %-22s %-6s %s" % (n["source"], n["what"], n["error"][:110]))
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(g.to_dict(), f, ensure_ascii=False)
    lines.append("wrote %s (%d bytes)" % (OUT, os.path.getsize(OUT)))
    with open(LOG, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
