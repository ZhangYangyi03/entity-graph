"""Brain-computer-interface data sources: datasets, trials, papers, devices.

Every endpoint below was measured from this host on 2026-11-23 and needs no API key.
Two of them have no API at all (PhysioNet, BNCI Horizon 2020) and are read off their
public index pages; that weaker claim is printed on every node they produce, because
"I parsed a table" and "the API said so" are not the same evidence.

    source              access      what it contributes
    OpenNeuro           GraphQL     BIDS datasets (EEG/MEG/iEEG/fMRI), id + title
    DANDI               REST        NWB dandisets, cellular neurophysiology
    NeuroVault          REST        18k+ statistical maps, collections by concept
    PhysioNet           HTML        classic EEG corpora (eegmmidb, eegbci, ...)
    BNCI Horizon 2020   HTML        the BCI-specific catalogue, 34 sets with paradigm
    ClinicalTrials.gov  REST v2     interventional trials: phase, status, sponsor
    PubMed E-utilities  REST        papers, titles and journals
    openFDA device      REST        510(k) clearances for EEG/BCI devices

Tried and refused, so nobody re-tries them and calls it a bug:

    NIH RePORTER        api.reporter.nih.gov serves a self-signed chain; both the host
                        trust store and certifi reject it. Real data, unusable without
                        switching verification off, which this repository does not do.
    RCSB PDB search     search.rcsb.org fails here with "unable to get local issuer
                        certificate" -- same class of problem, same refusal.
"""
from __future__ import annotations

import datetime as _dt
import re
import urllib.parse

from .fetch import FetchError, get, post, q
from .model import Edge, Graph, Node

NOW = lambda: _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")

# The terms that actually select BCI work rather than "brain" anything.
BCI_TERMS = ("brain-computer interface", "brain computer interface",
             "motor imagery", "P300", "SSVEP", "electrocorticography",
             "intracortical", "neuroprosthesis")


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:60] or "unknown"


# ------------------------------------------------------------------ datasets
def openneuro(keywords=None, modality: str = "eeg", first: int = 10) -> list[dict]:
    """BIDS datasets. Keyword search when keywords are given, else list by modality."""
    if keywords:
        kw = ", ".join('"%s"' % k.replace('"', "") for k in keywords)
        query = ('{ advancedSearch(query:{keywords:[%s], publicOnly:true}, first:%d)'
                 '{ edges { node { id name } } } }' % (kw, int(first)))
        url = "https://openneuro.org/crn/graphql"
    else:
        query = ('{ datasets(first:%d, modality:"%s"){ edges { node { id name } } } }'
                 % (int(first), modality))
        url = "https://openneuro.org/crn/graphql"
    d = post(url, {"query": query}, ttl=3600.0, timeout=40.0)
    if d.get("errors"):
        raise FetchError("openneuro graphql: %s" % str(d["errors"])[:160])
    edges = (((d.get("data") or {}).get("advancedSearch")
              or (d.get("data") or {}).get("datasets") or {}).get("edges")) or []
    return [{"id": e["node"]["id"], "name": e["node"]["name"],
             "url": "https://openneuro.org/datasets/" + e["node"]["id"]} for e in edges]


def dandi(search: str, limit: int = 5) -> list[dict]:
    """DANDI: NWB dandisets (electrophysiology, mostly non-human and human iEEG)."""
    url = ("https://api.dandiarchive.org/api/dandisets/?search=%s&page_size=%d"
           % (q(search), int(limit)))
    d = get(url, ttl=3600.0)
    out = []
    for r in (d.get("results") or [])[:limit]:
        ver = r.get("most_recent_published_version") or r.get("draft_version") or {}
        out.append({"id": r.get("identifier"), "name": ver.get("name") or "",
                    "contact": r.get("contact_person"), "assets": ver.get("asset_count"),
                    "size": ver.get("size"), "status": ver.get("status"),
                    "url": "https://dandiarchive.org/dandiset/" + str(r.get("identifier"))})
    return out


def neurovault(search: str, limit: int = 5) -> list[dict]:
    """NeuroVault collections: published statistical maps, searchable by concept."""
    url = "https://neurovault.org/api/collections/?format=json&limit=%d&search=%s" % (
        int(limit), q(search))
    d = get(url, ttl=3600.0)
    return [{"id": str(r.get("id")), "name": r.get("name"),
             "owner": r.get("owner_name"), "images": r.get("number_of_images"),
             "url": "https://neurovault.org/collections/%s/" % r.get("id")}
            for r in (d.get("results") or [])[:limit]]


def physionet(term: str, limit: int = 6) -> list[dict]:
    """PhysioNet has no documented JSON API: parse the search page, then each project page.

    Recorded as access="html" on the resulting node so the provenance is visible.
    """
    page = get("https://physionet.org/content/?q=" + q(term), raw=True, ttl=3600.0, timeout=40.0)
    seen, out = set(), []
    for href, name, ver, text in re.findall(
            r'href="(/content/([a-z0-9\-]+)/([0-9.]+)/)"[^>]*>\s*(.*?)\s*</a>', page, re.S):
        if href in seen:
            continue
        seen.add(href)
        out.append({"slug": name, "version": ver, "title": re.sub(r"<[^>]+>", "", text).strip()})
        if len(out) >= limit:
            break
    # The title on the search page is truncated with "...", so the project page supplies it.
    for rec in out:
        try:
            html = get("https://physionet.org/content/%s/%s/" % (rec["slug"], rec["version"]),
                  raw=True, ttl=3600.0, timeout=40.0)
        except FetchError:
            continue
        t = re.search(r"<title>(.*?)</title>", html, re.S)
        if t:
            rec["title"] = re.sub(r"\s+", " ", t.group(1)).strip()
        desc = re.search(r'<meta name="description" content="(.*?)"', html)
        rec["description"] = re.sub(r"\s+", " ", desc.group(1)).strip() if desc else ""
        pub = re.search(r"Published\s+([A-Z][a-z]+\.? \d+, \d{4})", html)
        rec["published"] = pub.group(1) if pub else ""
        rec["url"] = ("https://physionet.org/content/%s/%s/" % (rec["slug"], rec["version"]))
    return out


def bnci(limit: int = 40) -> list[dict]:
    """BNCI Horizon 2020 catalogue: the BCI-specific dataset list, HTML only.

    Each card carries a paradigm title ("Four class motor imagery (001-2014)"), a short
    description with participant/signal counts, and the directory the .mat files live in.
    """
    page = get("https://bnci-horizon-2020.eu/database/data-sets", raw=True, ttl=86400.0,
               timeout=60.0)
    out = []
    for chunk in page.split('card-title')[1:]:
        title = re.sub(r"<[^>]+>", "", chunk.split("</h3>")[0]).strip()
        title = re.sub(r"^[\">\s]+", "", title).strip()
        body = chunk.split("</h3>")[1] if "</h3>" in chunk else ""
        desc = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body.split("</p>")[0])).strip()
        m = re.search(r"/database/data-sets/(\d{3}-\d{4})/", chunk)
        if not title or not m:
            continue
        sid = m.group(1)
        participants = re.search(r"Participants\s+(\d+)", desc)
        signals = re.search(r"Signals\s+(\d+)", desc)
        out.append({"id": sid, "title": title, "description": desc[:300],
                    "participants": int(participants.group(1)) if participants else None,
                    "signals": int(signals.group(1)) if signals else None,
                    "url": "https://bnci-horizon-2020.eu/database/data-sets/%s/" % sid})
        if len(out) >= limit:
            break
    return out


# ------------------------------------------------------------------ clinical and funding
def ctgov(term: str, limit: int = 5, status: str | None = None) -> list[dict]:
    """ClinicalTrials.gov REST v2: the fields a graph needs, not the whole record."""
    url = ("https://clinicaltrials.gov/api/v2/studies?query.term=%s&pageSize=%d&format=json"
           % (q(term), int(limit)))
    if status:
        url += "&filter.overallStatus=" + q(status)
    d = get(url, ttl=3600.0, timeout=40.0)
    out = []
    for s in d.get("studies") or []:
        p = s.get("protocolSection") or {}
        idm = p.get("identificationModule") or {}
        st = p.get("statusModule") or {}
        des = p.get("designModule") or {}
        con = p.get("conditionsModule") or {}
        spo = p.get("sponsorCollaboratorsModule") or {}
        out.append({
            "nct": idm.get("nctId"),
            "title": idm.get("briefTitle") or idm.get("officialTitle"),
            "status": st.get("overallStatus"),
            "phase": (des.get("phases") or [None])[0],
            "enrollment": (des.get("enrollmentInfo") or {}).get("count"),
            "conditions": con.get("conditions") or [],
            "sponsor": (spo.get("leadSponsor") or {}).get("name"),
            "url": "https://clinicaltrials.gov/study/" + str(idm.get("nctId")),
        })
    return out


def pubmed(term: str, limit: int = 5) -> list[dict]:
    """PubMed: esearch for ids, esummary for titles. Two calls, both free, no key."""
    ids = get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&term=%s"
              "&retmode=json&retmax=%d&sort=relevance" % (q(term), int(limit)), ttl=3600.0)
    idl = ((ids.get("esearchresult") or {}).get("idlist")) or []
    if not idl:
        return []
    s = get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?db=pubmed&id=%s"
            "&retmode=json" % ",".join(idl), ttl=3600.0)
    res = s.get("result") or {}
    out = []
    for pmid in idl:
        r = res.get(pmid) or {}
        if not r:
            continue
        out.append({"pmid": pmid, "title": (r.get("title") or "").rstrip("."),
                    "journal": r.get("source"), "date": r.get("pubdate"),
                    "authors": [a.get("name") for a in (r.get("authors") or [])][:6],
                    "url": "https://pubmed.ncbi.nlm.nih.gov/%s/" % pmid})
    return out


def openfda_device(term: str, limit: int = 5) -> list[dict]:
    """openFDA 510(k): which EEG/BCI-adjacent devices are legally marketed, and to whom."""
    url = ("https://api.fda.gov/device/510k.json?search=device_name:%%22%s%%22&limit=%d"
           % (q(term), int(limit)))
    d = get(url, ttl=86400.0, timeout=40.0)
    out = []
    for r in d.get("results") or []:
        of = r.get("openfda") or {}
        out.append({
            "k_number": r.get("k_number"), "device_name": r.get("device_name"),
            "applicant": r.get("applicant"), "decision_date": r.get("decision_date"),
            "decision": r.get("decision_code"),
            "class": (of.get("device_class") or [None])[0],
            "specialty": (of.get("medical_specialty_description") or [None])[0],
            "url": "https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfPMN/pmn.cfm?ID=" + str(r.get("k_number")),
        })
    return out


# ------------------------------------------------------------------ graph
def build_bci_graph(term: str = "brain-computer interface", *, limit: int = 5,
                    datasets: bool = True, trials: bool = True, papers: bool = True,
                    devices: bool = True, neurovault_search: bool = True,
                    graph: Graph | None = None) -> Graph:
    """Assemble the BCI half: one hub term, the datasets, trials, papers and devices on it.

    A source that fails becomes a note on the graph (same contract as build.py), never an
    exception, so a dead catalogue degrades the answer instead of erasing it.
    """
    g = graph or Graph()
    notes: list[dict] = []

    def note(source, what, err):
        notes.append({"source": source, "what": what, "error": str(err)[:200]})

    hub = g.add_node(Node(key="topic:" + _slug(term), kind="topic", label=term,
                          attrs={"terms": list(BCI_TERMS)}, source="operator query",
                          url="", fetched_at=NOW()))

    def dataset_node(rec: dict, source: str, access: str, url: str, extra: dict) -> str:
        key = "dataset:%s:%s" % (source.replace(" ", "-").lower(), rec["id"])
        g.add_node(Node(key=key, kind="dataset", label=rec.get("name") or rec.get("title") or rec["id"],
                        attrs={"id": rec["id"], "access": access, **extra},
                        source=source, url=url, fetched_at=NOW()))
        g.add_edge(Edge(src=key, dst=hub.key, rel="relevant_to", attrs={"term": term},
                        source=source, url=url, fetched_at=NOW()))
        return key

    if datasets:
        try:
            for r in openneuro(["brain computer interface", "EEG"], first=limit) or \
                     openneuro(None, "eeg", limit):
                dataset_node(r, "OpenNeuro", "graphql", r["url"], {"format": "BIDS"})
        except FetchError as e:
            note("OpenNeuro", "search", e)

        try:
            for r in dandi("electrophysiology", limit):
                dataset_node(r, "DANDI", "rest", r["url"],
                             {"format": "NWB", "contact": r.get("contact"),
                              "assets": r.get("assets"), "status": r.get("status")})
        except FetchError as e:
            note("DANDI", "search", e)

        try:
            for r in bnci(limit=40):
                key = dataset_node({"id": r["id"], "title": r["title"]}, "BNCI Horizon 2020",
                                   "html", r["url"],
                                   {"participants": r.get("participants"),
                                    "signals": r.get("signals"),
                                    "description": r.get("description", "")[:160]})
                g.nodes[key].kind = "dataset"
        except FetchError as e:
            note("BNCI Horizon 2020", "catalogue", e)

        try:
            for r in physionet("eeg", limit=limit):
                dataset_node({"id": r["slug"], "title": r["title"]}, "PhysioNet", "html",
                             r["url"], {"published": r.get("published"),
                                        "description": r.get("description", "")[:160]})
        except FetchError as e:
            note("PhysioNet", "search", e)

    if neurovault_search:
        try:
            for r in neurovault("motor imagery", limit=limit):
                dataset_node(r, "NeuroVault", "rest", r["url"],
                             {"images": r.get("images"), "owner": r.get("owner")})
        except FetchError as e:
            note("NeuroVault", "search", e)

    if trials:
        try:
            for r in ctgov(term, limit=limit):
                k = "nct:%s" % r["nct"]
                g.add_node(Node(key=k, kind="trial", label=r["title"] or r["nct"],
                                attrs={"nct": r["nct"], "status": r.get("status"),
                                       "phase": r.get("phase"),
                                       "enrollment": r.get("enrollment"),
                                       "conditions": r.get("conditions")},
                                source="ClinicalTrials.gov", url=r["url"], fetched_at=NOW()))
                g.add_edge(Edge(src=k, dst=hub.key, rel="relevant_to", attrs={"term": term},
                                source="ClinicalTrials.gov", url=r["url"], fetched_at=NOW()))
                if r.get("sponsor"):
                    sk = "org:" + _slug(r["sponsor"])
                    g.add_node(Node(key=sk, kind="organization", label=r["sponsor"],
                                    source="ClinicalTrials.gov", url=r["url"], fetched_at=NOW()))
                    g.add_edge(Edge(src=sk, dst=k, rel="sponsors", attrs={"role": "lead"},
                                    source="ClinicalTrials.gov", url=r["url"], fetched_at=NOW()))
        except FetchError as e:
            note("ClinicalTrials.gov", "search", e)

    if papers:
        try:
            for r in pubmed(term, limit=limit):
                pk = "pmid:%s" % r["pmid"]
                g.add_node(Node(key=pk, kind="publication", label=r["title"] or r["pmid"],
                                attrs={"pmid": r["pmid"], "journal": r.get("journal"),
                                       "date": r.get("date"), "authors": r.get("authors")},
                                source="PubMed", url=r["url"], fetched_at=NOW()))
                g.add_edge(Edge(src=pk, dst=hub.key, rel="relevant_to", attrs={"term": term},
                                source="PubMed", url=r["url"], fetched_at=NOW()))
        except FetchError as e:
            note("PubMed", "search", e)

    if devices:
        try:
            for r in openfda_device("electroencephalograph", limit=limit):
                dk = "fda510k:%s" % r["k_number"]
                g.add_node(Node(key=dk, kind="device",
                                label="%s (%s)" % (r.get("device_name"), r["k_number"]),
                                attrs={"k_number": r["k_number"], "class": r.get("class"),
                                       "specialty": r.get("specialty"),
                                       "decision": r.get("decision"),
                                       "decision_date": r.get("decision_date")},
                                source="openFDA 510(k)", url=r["url"], fetched_at=NOW()))
                g.add_edge(Edge(src=dk, dst=hub.key, rel="relevant_to", attrs={"term": term},
                                source="openFDA 510(k)", url=r["url"], fetched_at=NOW()))
                if r.get("applicant"):
                    ak = "org:" + _slug(r["applicant"])
                    g.add_node(Node(key=ak, kind="organization", label=r["applicant"],
                                    source="openFDA 510(k)", url=r["url"], fetched_at=NOW()))
                    g.add_edge(Edge(src=ak, dst=dk, rel="manufactures", attrs={"k_number": r["k_number"]},
                                    source="openFDA 510(k)", url=r["url"], fetched_at=NOW()))
        except FetchError as e:
            note("openFDA 510(k)", "search", e)

    g._notes = notes  # type: ignore[attr-defined]
    return g
