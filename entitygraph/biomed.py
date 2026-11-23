"""Biomedical data sources: target -> compound -> drug -> label, plus trials.

The chain this module builds is the one a biomedical question actually has: a gene
(UniProt/Ensembl) is a target of compounds (ChEMBL), those compounds are drugs (RxNorm),
the drugs are prescribed under a label (DailyMed/openFDA), and diseases are attached to
the target by Open Targets' evidence score.

No source here needs a key. Measured from this host on 2026-11-23:

    source              access   what it contributes
    Open Targets        GraphQL  target -> associated diseases (scored), tractability
    ChEMBL              REST     targets by name, molecules/mechanisms by target
    UniProt             REST     protein record, function, cross-references
    PubChem             REST     compound identity: formula, weight, SMILES, CID
    RxNorm (NLM)        REST     drug names and ingredient/brand/clinical-drug relations
    DailyMed (NLM)      REST     Structured Product Labels: what is approved and for what
    openFDA drug        REST     label sections, and the adverse-event counts (FAERS)
    Europe PMC          REST     literature with DOI/PMID and open-access flag
    bioRxiv             REST     preprints by DOI
    ClinicalTrials.gov  REST v2  trials for a drug or condition

Refused on purpose, same as bci.py: api.reporter.nih.gov and search.rcsb.org serve
certificate chains this host (and certifi) reject. Turning verification off to reach
them is the trade this repository does not make.
"""
from __future__ import annotations

import datetime as _dt
import re
import urllib.parse

from .fetch import FetchError, get, post, q
from .model import Edge, Graph, Node

NOW = lambda: _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")

OT_URL = "https://api.platform.opentargets.org/api/v4/graphql"


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:60] or "unknown"


# ------------------------------------------------------------------ target side
def opentargets_target(symbol: str, diseases: int = 8) -> dict | None:
    """Resolve a gene symbol to an Ensembl id and its top scored disease associations."""
    s = post(OT_URL, {"query": '{ search(queryString:"%s", entityNames:["target"], page:{index:0,size:1})'
                               '{ hits { id name } } }' % symbol.replace('"', "")},
             ttl=86400.0, timeout=40.0)
    hits = (((s.get("data") or {}).get("search") or {}).get("hits")) or []
    if not hits:
        return None
    eid = hits[0]["id"]
    d = post(OT_URL, {"query": '{ target(ensemblId:"%s"){ id approvedSymbol approvedName '
                               'biotype functionDescriptions '
                               'associatedDiseases(page:{index:0,size:%d}){ count rows{ '
                               'disease{ id name } score } } } }' % (eid, int(diseases))},
             ttl=86400.0, timeout=40.0)
    t = ((d.get("data") or {}).get("target")) or None
    if not t:
        return None
    t["_disease_total"] = ((t.get("associatedDiseases") or {}).get("count"))
    t["_rows"] = ((t.get("associatedDiseases") or {}).get("rows")) or []
    return t


def uniprot(symbol: str, organism: int = 9606) -> dict | None:
    """One reviewed UniProt entry for a gene symbol (human by default)."""
    url = ("https://rest.uniprot.org/uniprotkb/search?query=%s+AND+organism_id:%d"
           "&format=json&size=1&fields=accession,id,protein_name,cc_function,xref_chembl,"
           "xref_pdb,gene_names,sequence" % (q(symbol), int(organism)))
    d = get(url, ttl=86400.0, timeout=40.0)
    res = d.get("results") or []
    if not res:
        return None
    r = res[0]
    name = (((r.get("proteinDescription") or {}).get("recommendedName") or {})
            .get("fullName") or {}).get("value")
    func = ""
    for c in r.get("comments") or []:
        if c.get("commentType") == "FUNCTION":
            func = " ".join(t.get("value", "") for t in (c.get("texts") or []))
            break
    return {"accession": r.get("primaryAccession"), "entry": r.get("uniProtkbId"),
            "name": name, "function": func[:600],
            "length": ((r.get("sequence") or {}).get("length")),
            "url": "https://www.uniprot.org/uniprotkb/%s/entry" % r.get("primaryAccession")}


def chembl_targets(symbol: str, limit: int = 5) -> list[dict]:
    """ChEMBL targets matching a symbol: the id that mechanisms and activities hang off."""
    url = ("https://www.ebi.ac.uk/chembl/api/data/target/search.json?q=%s&limit=%d"
           % (q(symbol), int(limit)))
    d = get(url, ttl=86400.0, timeout=40.0)
    out = []
    for t in d.get("targets") or []:
        out.append({"chembl_id": t.get("target_chembl_id"), "name": t.get("pref_name"),
                    "organism": t.get("organism"), "type": t.get("target_type"),
                    "url": "https://www.ebi.ac.uk/chembl/target_report_card/%s/"
                           % t.get("target_chembl_id")})
    return out


def chembl_drugs(target_chembl_id: str, limit: int = 10) -> list[dict]:
    """Mechanisms of action against one ChEMBL target -> the molecules that hit it."""
    url = ("https://www.ebi.ac.uk/chembl/api/data/mechanism.json?target_chembl_id=%s&limit=%d"
           % (q(target_chembl_id), int(limit)))
    d = get(url, ttl=86400.0, timeout=40.0)
    out = []
    for m in d.get("mechanisms") or []:
        out.append({"molecule_chembl_id": m.get("molecule_chembl_id"),
                    "mechanism": m.get("mechanism_of_action"),
                    "action_type": m.get("action_type"),
                    "url": "https://www.ebi.ac.uk/chembl/compound_report_card/%s/"
                           % m.get("molecule_chembl_id")})
    return out


# ------------------------------------------------------------------ drug side
def pubchem(name: str) -> dict | None:
    """Compound identity: CID, formula, weight, SMILES. This is the join key other sets use."""
    url = ("https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/%s/property/"
           "MolecularFormula,MolecularWeight,CanonicalSMILES,IsomericSMILES/JSON" % q(name))
    try:
        d = get(url, ttl=86400.0, timeout=40.0)
    except FetchError:
        return None
    props = ((d.get("PropertyTable") or {}).get("Properties")) or []
    if not props:
        return None
    p = props[0]
    return {"cid": p.get("CID"), "formula": p.get("MolecularFormula"),
            "weight": p.get("MolecularWeight"),
            "smiles": p.get("ConnectivitySMILES") or p.get("CanonicalSMILES"),
            "url": "https://pubchem.ncbi.nlm.nih.gov/compound/%s" % p.get("CID")}


def rxnorm(name: str) -> list[dict]:
    """RxNorm: ingredient / brand / clinical-drug concepts for a drug name."""
    d = get("https://rxnav.nlm.nih.gov/REST/drugs.json?name=" + q(name), ttl=86400.0)
    out = []
    for group in (d.get("drugGroup") or {}).get("conceptGroup") or []:
        for cp in group.get("conceptProperties") or []:
            out.append({"rxcui": cp.get("rxcui"), "name": cp.get("name"),
                        "tty": cp.get("tty"),
                        "url": "https://mor.nlm.nih.gov/RxNav/search?searchBy=RXCUI&searchTerm="
                               + str(cp.get("rxcui"))})
    return out


def dailymed(name: str, limit: int = 5) -> list[dict]:
    """DailyMed SPLs: the FDA-approved label set for a drug name."""
    d = get("https://dailymed.nlm.nih.gov/dailymed/services/v2/spls.json?drug_name=%s&pagesize=%d"
            % (q(name), int(limit)), ttl=86400.0, timeout=40.0)
    out = []
    for r in d.get("data") or []:
        out.append({"setid": r.get("setid"), "title": r.get("title"),
                    "published": r.get("published_date"),
                    "url": "https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid=" + str(r.get("setid"))})
    return out


def openfda_label(name: str, limit: int = 2) -> list[dict]:
    """openFDA drug label sections -- indications/mechanism text, without the 500 KB blob."""
    url = ("https://api.fda.gov/drug/label.json?search=openfda.generic_name:%%22%s%%22&limit=%d"
           % (q(name), int(limit)))
    try:
        d = get(url, ttl=86400.0, timeout=40.0)
    except FetchError:
        return []
    out = []
    for r in d.get("results") or []:
        of = r.get("openfda") or {}
        out.append({"brand": (of.get("brand_name") or [None])[0],
                    "generic": (of.get("generic_name") or [None])[0],
                    "manufacturer": (of.get("manufacturer_name") or [None])[0],
                    "indications": " ".join(r.get("indications_and_usage") or [])[:600],
                    "mechanism": " ".join(r.get("mechanism_of_action") or [])[:400]})
    return out


def openfda_adverse(name: str) -> dict | None:
    """FAERS signal, as counts: how many reports name this drug, and how many were serious."""
    base = "https://api.fda.gov/drug/event.json?search=patient.drug.medicinalproduct:%s" % q(name)
    try:
        total = get(base + "&limit=1", ttl=86400.0, timeout=40.0)
    except FetchError:
        return None
    n = ((total.get("meta") or {}).get("results") or {}).get("total")
    serious = None
    try:
        s = get(base + "&search=serious:1&limit=1", ttl=86400.0, timeout=40.0)
        serious = ((s.get("meta") or {}).get("results") or {}).get("total")
    except FetchError:
        pass
    return {"reports": n, "serious": serious,
            "url": "https://open.fda.gov/apis/drug/event/"}


def europepmc(term: str, limit: int = 5) -> list[dict]:
    """Europe PMC: literature with DOI, PMID and open-access status."""
    d = get("https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=%s&format=json"
            "&pageSize=%d&resultType=lite" % (q(term), int(limit)), ttl=86400.0, timeout=40.0)
    out = []
    for r in ((d.get("resultList") or {}).get("result")) or []:
        out.append({"id": r.get("id"), "pmid": r.get("pmid"), "doi": r.get("doi"),
                    "title": r.get("title"), "journal": r.get("journalTitle"),
                    "year": r.get("pubYear"), "open_access": r.get("isOpenAccess"),
                    "url": "https://europepmc.org/article/%s/%s" % (r.get("source"), r.get("id"))})
    return out


def biorxiv(doi: str) -> dict | None:
    """One bioRxiv preprint record by DOI (preprints are where BCI work lands first)."""
    d = get("https://api.biorxiv.org/details/biorxiv/" + q(doi), ttl=86400.0, timeout=40.0)
    col = d.get("collection") or []
    if not col:
        return None
    r = col[-1]
    return {"doi": r.get("doi"), "title": r.get("title"), "authors": r.get("authors"),
            "institution": r.get("author_corresponding_institution"),
            "date": r.get("date"), "version": r.get("version"), "type": r.get("type"),
            "url": "https://www.biorxiv.org/content/" + str(r.get("doi"))}


# ------------------------------------------------------------------ graph
def build_biomed_graph(symbol: str = "EGFR", *, drug: str | None = None,
                       diseases: int = 8, drugs: int = 8, papers: int = 4,
                       graph: Graph | None = None) -> Graph:
    """target -> diseases, target -> compounds -> drug identity -> label -> adverse events.

    Every hop cites the endpoint it came from; a dead source is a note, not an exception.
    """
    g = graph or Graph()
    notes: list[dict] = []

    def note(source, what, err):
        notes.append({"source": source, "what": what, "error": str(err)[:200]})

    tgt = None
    try:
        tgt = opentargets_target(symbol, diseases=diseases)
    except FetchError as e:
        note("Open Targets", "target", e)

    ensembl = (tgt or {}).get("id")
    symbol_used = (tgt or {}).get("approvedSymbol") or symbol
    tkey = "ensembl:%s" % ensembl if ensembl else "gene:" + _slug(symbol)

    g.add_node(Node(key=tkey, kind="target", label=symbol_used,
                    attrs={"ensembl": ensembl,
                           "full_name": (tgt or {}).get("approvedName"),
                           "biotype": (tgt or {}).get("biotype"),
                           "function": " ".join((tgt or {}).get("functionDescriptions") or [])[:600]},
                    source="Open Targets" if tgt else "operator query",
                    url="https://platform.opentargets.org/target/%s" % ensembl if ensembl else "",
                    fetched_at=NOW()))

    for row in (tgt or {}).get("_rows") or []:
        dis = row.get("disease") or {}
        if not dis.get("id"):
            continue
        dk = "disease:%s" % dis["id"]
        g.add_node(Node(key=dk, kind="disease", label=dis.get("name") or dis["id"],
                        attrs={"mondo": dis.get("id")},
                        source="Open Targets",
                        url="https://platform.opentargets.org/disease/%s" % dis.get("id"),
                        fetched_at=NOW()))
        g.add_edge(Edge(src=tkey, dst=dk, rel="associated_with",
                        attrs={"score": row.get("score")}, source="Open Targets",
                        url="https://platform.opentargets.org/target/%s/associations" % ensembl,
                        fetched_at=NOW()))

    try:
        up = uniprot(symbol_used)
    except FetchError as e:
        up = None
        note("UniProt", "search", e)
    if up:
        uk = "uniprot:%s" % up["accession"]
        g.add_node(Node(key=uk, kind="protein", label=up.get("name") or up["accession"],
                        attrs={"accession": up["accession"], "length": up.get("length"),
                               "function": up.get("function")},
                        source="UniProt", url=up["url"], fetched_at=NOW()))
        g.add_edge(Edge(src=tkey, dst=uk, rel="encodes", attrs={"gene": symbol_used},
                        source="UniProt", url=up["url"], fetched_at=NOW()))

    try:
        targets = chembl_targets(symbol_used, limit=3)
    except FetchError as e:
        targets = []
        note("ChEMBL", "target search", e)

    compounds: list[dict] = []
    for t in targets:
        ck = "chembl_target:%s" % t["chembl_id"]
        g.add_node(Node(key=ck, kind="target", label=t.get("name") or t["chembl_id"],
                        attrs={"chembl_id": t["chembl_id"], "organism": t.get("organism"),
                               "target_type": t.get("type")},
                        source="ChEMBL", url=t["url"], fetched_at=NOW()))
        # No alias merge on a name: the association to the gene is an edge, and it is labelled.
        g.add_edge(Edge(src=ck, dst=tkey, rel="same_as",
                        attrs={"basis": "ChEMBL target search by gene symbol"},
                        source="ChEMBL", url=t["url"], fetched_at=NOW()))
        try:
            mechs = chembl_drugs(t["chembl_id"], limit=drugs)
        except FetchError as e:
            mechs = []
            note("ChEMBL", "mechanisms", e)
        for m in mechs:
            compounds.append(m)
            mk = "chembl_molecule:%s" % m["molecule_chembl_id"]
            g.add_node(Node(key=mk, kind="compound", label=m["molecule_chembl_id"],
                            attrs={"mechanism": m.get("mechanism"),
                                   "action_type": m.get("action_type")},
                            source="ChEMBL", url=m["url"], fetched_at=NOW()))
            g.add_edge(Edge(src=mk, dst=ck, rel="acts_on",
                            attrs={"mechanism": m.get("mechanism"),
                                   "action_type": m.get("action_type")},
                            source="ChEMBL", url=m["url"], fetched_at=NOW()))

    # The drug half. Named drug if the caller gave one, else the first mechanism's id only
    # -- ChEMBL ids are not names, so no name is invented for them.
    focus = drug or (compounds[0]["molecule_chembl_id"] if compounds else None)
    if focus and drug:
        try:
            pc = pubchem(focus)
        except FetchError as e:
            pc = None
            note("PubChem", "lookup", e)
        if pc:
            pk = "pubchem:%s" % pc["cid"]
            g.add_node(Node(key=pk, kind="compound", label=focus,
                            attrs={"cid": pc["cid"], "formula": pc.get("formula"),
                                   "weight": pc.get("weight"), "smiles": pc.get("smiles")},
                            source="PubChem", url=pc["url"], fetched_at=NOW()))
            for c in compounds:
                g.add_edge(Edge(src="chembl_molecule:%s" % c["molecule_chembl_id"], dst=pk,
                                rel="identified_by", attrs={"method": "PubChem name lookup"},
                                source="PubChem", url=pc["url"], fetched_at=NOW()))

        try:
            for r in rxnorm(focus)[:6]:
                rk = "rxnorm:%s" % r["rxcui"]
                g.add_node(Node(key=rk, kind="drug", label=r.get("name") or r["rxcui"],
                                attrs={"rxcui": r["rxcui"], "tty": r.get("tty")},
                                source="RxNorm", url=r["url"], fetched_at=NOW()))
                g.add_edge(Edge(src=rk, dst=tkey, rel="treats_target", attrs={"tty": r.get("tty")},
                                source="RxNorm", url=r["url"], fetched_at=NOW()))
        except FetchError as e:
            note("RxNorm", "lookup", e)

        try:
            for s2 in dailymed(focus):
                sk = "spl:%s" % s2["setid"]
                g.add_node(Node(key=sk, kind="label", label=s2.get("title") or s2["setid"],
                                attrs={"setid": s2["setid"], "published": s2.get("published")},
                                source="DailyMed", url=s2["url"], fetched_at=NOW()))
                g.add_edge(Edge(src="drug:%s" % _slug(focus), dst=sk, rel="labelled_by",
                                source="DailyMed", url=s2["url"], fetched_at=NOW()))
        except FetchError as e:
            note("DailyMed", "lookup", e)

        try:
            lab = openfda_label(focus, limit=1)
        except FetchError as e:
            lab = []
            note("openFDA drug", "label", e)
        for r in lab:
            ind = (r.get("indications") or "")[:400]
            g.add_node(Node(key="label:" + _slug(focus), kind="label",
                            label="%s label" % (r.get("brand") or focus),
                            attrs={"indications": ind, "mechanism": r.get("mechanism"),
                                   "manufacturer": r.get("manufacturer")},
                            source="openFDA drug",
                            url="https://open.fda.gov/apis/drug/label/", fetched_at=NOW()))
            g.add_edge(Edge(src="drug:%s" % _slug(focus), dst="label:" + _slug(focus),
                            rel="labelled_by", source="openFDA drug",
                            url="https://open.fda.gov/apis/drug/label/", fetched_at=NOW()))

        try:
            ae = openfda_adverse(focus)
        except FetchError as e:
            ae = None
            note("openFDA FAERS", "adverse events", e)
        if ae and ae.get("reports"):
            src_key = "drug:%s" % _slug(focus)
            g.add_node(Node(key=src_key, kind="drug", label=focus,
                            attrs={"reports": ae["reports"], "serious": ae.get("serious")},
                            source="openFDA FAERS", url=ae["url"], fetched_at=NOW()))
            g.add_edge(Edge(src=src_key, dst=tkey, rel="indicated_for",
                            attrs={"reports": ae["reports"], "serious_reports": ae.get("serious")},
                            source="openFDA FAERS", url=ae["url"], fetched_at=NOW()))

    try:
        for r in europepmc("%s drug OR inhibitor" % symbol_used, limit=papers):
            pk = "epmc:%s" % r["id"]
            g.add_node(Node(key=pk, kind="publication", label=r.get("title") or r["id"],
                            attrs={"pmid": r.get("pmid"), "doi": r.get("doi"),
                                   "journal": r.get("journal"), "year": r.get("year"),
                                   "open_access": r.get("open_access")},
                            source="Europe PMC", url=r["url"], fetched_at=NOW()))
            g.add_edge(Edge(src=pk, dst=tkey, rel="relevant_to", attrs={"term": symbol_used},
                            source="Europe PMC", url=r["url"], fetched_at=NOW()))
    except FetchError as e:
        note("Europe PMC", "search", e)

    g._notes = notes  # type: ignore[attr-defined]
    return g
