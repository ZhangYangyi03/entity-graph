"""Assemble a graph from a company name or identifier, citing every hop."""
from __future__ import annotations

import datetime as _dt
import os

from .fetch import FetchError, get, q
from .model import Edge, Graph, Node
from . import sources
from .holders import classify, holders_in_filing
from . import registries
from . import uk as ukmod

NOW = lambda: _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _slug(s: str) -> str:
    """Stable key fragment for a name that has no identifier yet."""
    import re as _re
    return _re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:60] or "unknown"


def _lei_node(match: dict, url: str) -> tuple[Node, list[Node], list[Edge]]:
    a = match["attributes"]["entity"]
    lei = match["id"]
    node = Node(key=f"lei:{lei}", kind="company", label=a["legalName"]["name"],
                attrs={"lei": lei,
                       "country": a["legalAddress"].get("country"),
                       "city": a["legalAddress"].get("city"),
                       "status": a.get("status"),
                       "legal_form": (a.get("legalForm") or {}).get("id")},
                source="GLEIF", url=url, fetched_at=NOW())
    nodes, edges = [node], []
    for rel in ("direct-parent", "ultimate-parent"):
        try:
            p = get(f"https://api.gleif.org/api/v1/lei-records/{lei}/{rel}")
        except FetchError:
            continue
        d = p.get("data") or {}
        if not d:
            continue
        reg = d.get("attributes", {}).get("relationship", {}).get("registration", {})
        plei = d.get("id")
        pname = reg.get("name") or plei
        pn = Node(key=f"lei:{plei}", kind="company", label=pname,
                  attrs={"lei": plei}, source="GLEIF",
                  url=f"https://api.gleif.org/api/v1/lei-records/{lei}/{rel}", fetched_at=NOW())
        nodes.append(pn)
        edges.append(Edge(src=node.key, dst=pn.key, rel=rel.replace("-", "_"),
                          attrs={"registration_status": reg.get("registrationStatus")},
                          source="GLEIF", url=f"https://api.gleif.org/api/v1/lei-records/{lei}/{rel}",
                          fetched_at=NOW()))
    return node, nodes, edges


def build(name: str | None = None, *, lei: str | None = None, cik: str | None = None,
          depth: int = 1, sanction_check: bool = True, graph: Graph | None = None,
          registry_id: str | None = None, registry_country: str | None = None,
          countries: list[str] | None = None, officers: bool = True) -> Graph:
    """Build (or extend) a graph around one entity.

    Only sources that need no paid key are called. Each call is wrapped so one dead
    registry degrades the result instead of aborting it -- and the failure is recorded
    on the graph so the caller can see what was missing rather than assume it was clean.
    """
    g = graph or Graph()
    notes: list[dict] = []

    def note(source, what, err):
        notes.append({"source": source, "what": what, "error": str(err)})

    url = ""
    matches = []
    if lei or name:
        url = (f"https://api.gleif.org/api/v1/lei-records/{lei}" if lei else
               f"https://api.gleif.org/api/v1/lei-records?filter%5Bentity.legalName%5D={q(name)}&page%5Bsize%5D=5")
        try:
            r = get(url)
            matches = (r.get("data", []) if not lei else [r.get("data")])
            matches = [m for m in matches if m]
        except FetchError as e:
            note("GLEIF", "lookup", e)

    for m in matches[:3]:
        try:
            node, nodes, edges = _lei_node(m, url)
        except Exception as e:  # malformed record
            note("GLEIF", "parse", e)
            continue
        for n in nodes:
            g.add_node(n)
        for e in edges:
            g.add_edge(e)

    if cik:
        parent_key = f"cik:{cik}"
        g.add_node(Node(key=parent_key, kind="company", label=f"CIK {cik}",
                        attrs={"cik": str(cik)}, source="SEC EDGAR",
                        url=f"https://data.sec.gov/submissions/CIK{str(cik).zfill(10)}.json",
                        fetched_at=NOW()))

        # SC 13D / 13G: holders above 5%. Available for every US-listed issuer, unlike
        # Exhibit 21, which many filers simply do not include (Apple is one).
        # The cover page is parsed, so the edge goes issuer <- holder, not issuer <- filing.
        try:
            h = sources.holders(cik)
            parsed_filings = 0
            for f in h.get("filings", [])[:12]:
                try:
                    people = holders_in_filing(cik, f["accession"])
                except Exception as e:
                    note("SEC EDGAR", f"parse {f['accession']}", e)
                    continue
                if not people:
                    continue
                parsed_filings += 1
                for person in people:
                    hk = "holder:" + _slug(person["name"])
                    kind = "person" if person.get("kind") == "person" else "company"
                    g.add_node(Node(key=hk, kind=kind, label=person["name"],
                                    attrs={"shares": person["shares"], "percent": person["percent"]},
                                    source="SEC EDGAR", url=person.get("url", ""), fetched_at=NOW()))
                    g.add_edge(Edge(src=hk, dst=parent_key, rel="holder_of",
                                    attrs={"form": f["form"], "date": f["date"],
                                           "accession": f["accession"],
                                           "shares": person["shares"],
                                           "percent": person["percent"]},
                                    source="SEC EDGAR", url=person.get("url", ""), fetched_at=NOW()))
            if not parsed_filings:
                note("SEC EDGAR", "SC 13D/13G", "filings present but no cover page parsed")
        except Exception as e:
            note("SEC EDGAR", "SC 13D/13G", e)

        try:
            s = sources.subsidiaries(cik)
        except (SystemExit, Exception) as e:
            note("SEC EDGAR", "Exhibit 21", e)
            s = {}
        if s:
            parent = g.nodes[parent_key]
            if False:
                pass
            parent = g.add_node(Node(
                key=f"cik:{cik}", kind="company", label=f"CIK {cik}",
                attrs={"cik": str(cik)}, source="SEC EDGAR",
                url=f"https://data.sec.gov/submissions/CIK{str(cik).zfill(10)}.json",
                fetched_at=NOW()))
            for row in s.get("subsidiaries", []):
                if not row.get("name"):
                    continue
                child = g.add_node(Node(
                    key=f"sub:{s['accession']}:{row['name'][:40]}", kind="company",
                    label=row["name"], attrs={"domicile": row.get("domicile")},
                    source="SEC EDGAR", url=s.get("url", ""), fetched_at=NOW()))
                g.add_edge(Edge(src=child.key, dst=parent.key, rel=rel,
                                attrs={"filing": s.get("filing"), "accession": s.get("accession")},
                                source="SEC EDGAR", url=s.get("url", ""), fetched_at=NOW()))

    # ---- non-US national registries ------------------------------------------------
    # Each hit becomes a company node keyed by its own national id, plus person nodes for
    # the officers the registry actually publishes. No name-only joins: a registry hit is
    # attached by its own identifier and carries its own URL.
    reg_hits: list[dict] = []
    if registry_id and registry_country:
        reg_hits.extend(registries.lookup_id(registry_id, registry_country))
    elif name:
        reg_hits.extend(registries.lookup_name(name, countries))

    # UK lives in a local SQLite index (494 MB + 2.5 GB of bulk data cannot sit on a
    # request path). Absent index means "not loaded", which is reported, not guessed.
    uk_db = os.environ.get("ENTITYGRAPH_UK_DB", "uk.sqlite")
    if registry_country in (None, "GB") and (name or registry_id):
        got = ukmod.lookup(uk_db, number=registry_id, name=name)
        if got:
            reg_hits.extend(got)
        elif name and not os.path.exists(uk_db):
            note("GB registry", "lookup", f"no index at {uk_db}; run python -m entitygraph.ingest")

    for rec in reg_hits:
        cc = rec.get("country") or "XX"
        key = f"reg:{cc}:{rec.get('id')}"
        if not rec.get("name"):
            continue
        node = g.add_node(Node(key=key, kind="company", label=rec["name"], attrs={
            "country": cc, "address": rec.get("address"), **(rec.get("attributes") or {})},
            source=f"{cc} registry", url=rec.get("url") or "", fetched_at=NOW()))
        if lei and len(matches) == 1:
            # Same entity seen by two sources: an alias edge, not a merge. Merging on a
            # name would be a guess; the alias can be checked by a human and is labelled.
            g.add_edge(Edge(src=key, dst=f"lei:{matches[0]['id']}", rel="same_as",
                            attrs={"basis": "registry lookup by name"}, source=f"{cc} registry",
                            url=rec.get("url") or "", fetched_at=NOW()))
        if officers:
            try:
                loader = registries.OFFICER_LOADERS.get(cc)
                roster = loader(rec) if loader else [(o.get("name"), o.get("role"))
                                                     for o in rec.get("officers", [])]
            except Exception as e:
                note(f"{cc} registry", "officers", e)
                roster = []
            for who, role in roster:
                if not who:
                    continue
                pk = "person:" + _slug(who)
                g.add_node(Node(key=pk, kind="person", label=who, attrs={"role": role},
                                source=f"{cc} registry", url=rec.get("url") or "", fetched_at=NOW()))
                g.add_edge(Edge(src=pk, dst=key, rel="officer_of", attrs={"role": role},
                                source=f"{cc} registry", url=rec.get("url") or "", fetched_at=NOW()))

    if sanction_check:
        target = name or (matches[0]["attributes"]["entity"]["legalName"]["name"] if matches else None)
        if target:
            try:
                hits = sources.sanction(target)
                for res in (hits.get("results") or [])[:3]:
                    key = f"sanctions:{res.get('id', res.get('caption', 'x'))[:40]}"
                    g.add_node(Node(key=key, kind="company", label=res.get("caption") or target,
                                    attrs={"schemas": res.get("schema"), "datasets": res.get("datasets")},
                                    source="OpenSanctions",
                                    url=f"https://data.opensanctions.org/datasets/latest/default/targets.simple.csv",
                                    fetched_at=NOW()))
                    if matches:
                        g.add_edge(Edge(src=f"lei:{matches[0]['id']}", dst=key, rel="sanctioned",
                                        attrs={"match": res.get("caption")},
                                        source="OpenSanctions",
                                        url=f"https://www.opensanctions.org/entities/{res.get('id')}/",
                                        fetched_at=NOW()))
            except Exception as e:
                note("OpenSanctions", "search", e)

    g._notes = notes  # type: ignore[attr-defined]
    return g
