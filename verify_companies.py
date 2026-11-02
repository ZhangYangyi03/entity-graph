"""Check that companies.graph.json keeps the promises the README makes.

Exits non-zero if a node or edge lost its citation, if a hand-entered record stopped saying
it was hand-entered, or if a wiki template leaked in as a company name. These are the three
ways this graph could quietly become a work of fiction, so they are checked, not reviewed.
"""
import json
import sys

d = json.load(open("companies.graph.json", encoding="utf-8"))
comp = [n for n in d["nodes"] if n["kind"] == "company"]
manual = [n for n in comp if n["source"] == "manual"]
junk = [n["label"] for n in d["nodes"] if "{{" in n["label"] or n["label"].startswith("|")]

checks = [
    ("every edge cites source and url",
     all(e.get("url") and e.get("source") for e in d["edges"])),
    ("every node names its source", all(n.get("source") for n in d["nodes"])),
    ("hand-entered nodes say they are unverified",
     all(("not machine-verified" in n["attrs"].get("note", "")
          or "not determined" in n["attrs"].get("note", "")
          or "NO registry attributes" in n["attrs"].get("note", "")) for n in manual)),
    ("the caveat list is present", len(d.get("caveats", [])) >= 4),
    ("no wiki template leaked in as a company", not junk),
]
for name, ok in checks:
    print(("PASS " if ok else "FAIL ") + name)
print(f"{len(d['nodes'])} nodes, {len(d['edges'])} edges, "
      f"{len(comp)} companies ({len(manual)} manual)")
sys.exit(0 if all(ok for _, ok in checks) else 1)
