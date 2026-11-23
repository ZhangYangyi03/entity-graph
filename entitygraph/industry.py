"""The 产业图谱 the 实施方案 asks for: 脑机接口6+1 and 生物制造 on one graph.

This is the assembler. It answers "which 6+1 direction does this evidence belong to, and
which chain does it sit on", using the taxonomy in directions.py and the measured sources
in bci.py / biomed.py. Nothing here invents a company or a patent: what it cannot fetch,
it reports as a gap.

    python -m entitygraph.cli industry --out industry.graph.json

Shape:
    topic  -- 6+1 direction nodes (神经调控 ... +1 类器官) and 5 生物制造 domain nodes
    evidence -> direction (relevant_to, with the keyword that decided it on the edge)
    direction -> chain_stage (upstream/midstream/downstream) where the text says so
Every evidence node keeps source + url, so the map is auditable the same way the company
graph is.

Named gaps, so nobody reads absence as emptiness:
    * 企业节点 needs a company database. GLEIF/SEC cover listed firms; the 6+1 pipeline
      companies in the document (脑虎科技, 阶梯医疗, 博睿康, 华大智造, 金斯瑞, 凯莱英)
      are mostly private, and there is no free 工商 source here without QCC keys.
    * 专利 needs 智慧芽/Incopat or a paid API; no free full-text patent endpoint is used.
    * 资金链 needs a funding database; NIH RePORTER would help and is refused (bad TLS).
"""
from __future__ import annotations

import datetime as _dt

from .fetch import FetchError
from .model import Edge, Graph, Node
from . import directions as D
from . import bci as BC
from . import biomed as BM

NOW = lambda: _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _slug(s: str) -> str:
    return BC._slug(s)


def build_industry_map(term: str = "brain-computer interface", *, limit: int = 4,
                       bio_symbol: str = "EGFR", with_bio: bool = True,
                       graph: Graph | None = None,
                       market: bool = False) -> Graph:
    """Tag every fetched record with its 6+1 direction and chain stage, then link them."""
    g = graph or Graph()
    notes: list[dict] = []

    def note(source, what, err):
        notes.append({"source": source, "what": what, "error": str(err)[:200]})

    # --- the seven BCI directions, as real nodes ------------------------------------
    dir_keys: dict[str, str] = {}
    for did, d in D.DIRECTIONS.items():
        k = "dir:" + did
        dir_keys[did] = k
        g.add_node(Node(key=k, kind="bci_direction", label=d["name"],
                        attrs={"en": d["en"], "tech_params": d["params"],
                               "definition": " ".join(d["keywords"][:6])},
                        source="实施方案 6+1 布局", url="", fetched_at=NOW()))

    # --- the five bio-manufacturing domains ------------------------------------------
    bio_keys: dict[str, str] = {}
    if with_bio:
        for bid, b in D.BIO_MANUFACTURING.items():
            k = "bio:" + bid
            bio_keys[bid] = k
            g.add_node(Node(key=k, kind="bio_domain", label=b["name"],
                            attrs={"en": bid}, source="实施方案 生物制造定义",
                            url="", fetched_at=NOW()))

    # --- chain stages ---------------------------------------------------------------
    stage_keys: dict[str, str] = {}
    for sid, s in D.CHAIN_STAGES.items():
        k = "stage:" + sid
        stage_keys[sid] = k
        g.add_node(Node(key=k, kind="chain_stage", label=s["name"], attrs={"en": sid},
                        source="实施方案 产业链环节", url="", fetched_at=NOW()))

    def tag_and_link(key: str, text: str, source: str, url: str) -> None:
        """Attach direction / bio / stage edges to one evidence node, with the basis."""
        node = g.nodes.get(key)
        if node is None:
            return
        hits = D.classify(text)
        for h in hits:
            dk = dir_keys[h["direction"]]
            g.add_edge(Edge(src=key, dst=dk, rel="relevant_to",
                            attrs={"match": h["match"], "confidence": h["confidence"]},
                            source=source, url=url, fetched_at=NOW()))
            node.attrs.setdefault("bci6plus1", []).append(h["direction"])
        if with_bio:
            for b in D.classify_bio(text):
                bk = bio_keys[b["domain"]]
                g.add_edge(Edge(src=key, dst=bk, rel="relevant_to",
                                attrs={"match": b["match"], "confidence": b["confidence"]},
                                source=source, url=url, fetched_at=NOW()))
                node.attrs.setdefault("bio_domain", []).append(b["domain"])
        st = D.chain_stage(text)
        if st:
            g.add_edge(Edge(src=key, dst=stage_keys[st["stage"]], rel="sits_in",
                            attrs={"match": st["match"]}, source=source, url=url,
                            fetched_at=NOW()))
            node.attrs["chain_stage"] = st["stage"]
        node.attrs["chains"] = list(D.FOUR_CHAINS)
        if not hits and not D.classify_bio(text):
            node.attrs["tagging"] = "no 6+1 keyword matched; left untagged rather than guessed"

    # --- the named actors: institutes, universities, hospitals, companies, parks -------
    # These come from the project document, not from a fetch, so the source string says
    # so and the `fact` attribute carries the sentence each one rests on.
    DOC = "实施方案（项目文档）"
    for a in D.ACTORS:
        ak = "actor:" + _slug(a["short"] or a["name"])
        g.add_node(Node(key=ak, kind=a["kind"], label=a["name"],
                        attrs={"short": a["short"], "chain": a["chain"],
                               "fact": a["fact"], "chains": list(D.FOUR_CHAINS)},
                        source=DOC, url="", fetched_at=NOW()))
        for did in a.get("dirs") or []:
            if did in dir_keys:
                g.add_edge(Edge(src=ak, dst=dir_keys[did], rel="works_on",
                                attrs={"basis": a["fact"]}, source=DOC, url="",
                                fetched_at=NOW()))
        for bid in a.get("bio") or []:
            if bid in bio_keys:
                g.add_edge(Edge(src=ak, dst=bio_keys[bid], rel="works_on",
                                attrs={"basis": a["fact"]}, source=DOC, url="",
                                fetched_at=NOW()))
        if a["chain"] in D.FOUR_CHAINS:
            ck = "four_chain:" + _slug(a["chain"])
            g.add_node(Node(key=ck, kind="four_chain", label=a["chain"],
                            attrs={"note": "产业链/创新链/人才链/资金链 四链融合标注"},
                            source=DOC, url="", fetched_at=NOW()))
            g.add_edge(Edge(src=ak, dst=ck, rel="counts_toward",
                            attrs={"basis": a["fact"]}, source=DOC, url="", fetched_at=NOW()))

    for dept in D.GOVERNMENT:
        gk = "gov:" + _slug(dept)
        g.add_node(Node(key=gk, kind="government", label=dept, attrs={"scope": "京津冀"},
                        source=DOC, url="", fetched_at=NOW()))
        g.add_edge(Edge(src=gk, dst=dir_keys["brain_control"], rel="regulates",
                        attrs={"basis": "全产业调研对象：审批、质量控制、市场准入"},
                        source=DOC, url="", fetched_at=NOW()))

    # 协和创新港天津医工基地 is the platform the whole map hangs off.
    g.add_node(Node(key="platform:xiehe-tianjin", kind="platform",
                    label="协和创新港天津医工基地",
                    attrs={"fact": "基础研究→关键技术→工程化→临床验证→产业转化的平台",
                           "chains": list(D.FOUR_CHAINS)},
                    source=DOC, url="", fetched_at=NOW()))
    for sid, sk in stage_keys.items():
        g.add_edge(Edge(src="platform:xiehe-tianjin", dst=sk, rel="covers_stage",
                        attrs={"stage": sid}, source=DOC, url="", fetched_at=NOW()))

    # --- evidence: datasets, trials, papers, devices --------------------------------
    try:
        for r in BC.openneuro(["brain computer interface", "EEG"], first=limit):
            k = "dataset:openneuro:%s" % r["id"]
            g.add_node(Node(key=k, kind="dataset", label=r["name"], attrs={"id": r["id"],
                            "format": "BIDS"}, source="OpenNeuro", url=r["url"],
                            fetched_at=NOW()))
            tag_and_link(k, r["name"], "OpenNeuro", r["url"])
    except FetchError as e:
        note("OpenNeuro", "search", e)

    try:
        for r in BC.bnci(limit=30):
            k = "dataset:bnci:%s" % r["id"]
            g.add_node(Node(key=k, kind="dataset", label=r["title"],
                            attrs={"id": r["id"], "participants": r.get("participants"),
                                   "signals": r.get("signals")},
                            source="BNCI Horizon 2020", url=r["url"], fetched_at=NOW()))
            tag_and_link(k, r["title"] + " " + (r.get("description") or ""),
                         "BNCI Horizon 2020", r["url"])
    except FetchError as e:
        note("BNCI Horizon 2020", "catalogue", e)

    try:
        for r in BC.ctgov(term, limit=limit):
            k = "nct:%s" % r["nct"]
            g.add_node(Node(key=k, kind="trial", label=r["title"] or r["nct"],
                            attrs={"nct": r["nct"], "phase": r.get("phase"),
                                   "status": r.get("status")},
                            source="ClinicalTrials.gov", url=r["url"], fetched_at=NOW()))
            tag_and_link(k, (r["title"] or "") + " " + " ".join(r.get("conditions") or []),
                         "ClinicalTrials.gov", r["url"])
            if r.get("sponsor"):
                sk = "org:" + _slug(r["sponsor"])
                g.add_node(Node(key=sk, kind="organization", label=r["sponsor"],
                                source="ClinicalTrials.gov", url=r["url"], fetched_at=NOW()))
                g.add_edge(Edge(src=sk, dst=k, rel="sponsors", attrs={"role": "lead"},
                                source="ClinicalTrials.gov", url=r["url"], fetched_at=NOW()))
    except FetchError as e:
        note("ClinicalTrials.gov", "search", e)

    try:
        for r in BC.pubmed(term, limit=limit):
            k = "pmid:%s" % r["pmid"]
            g.add_node(Node(key=k, kind="publication", label=r["title"] or r["pmid"],
                            attrs={"pmid": r["pmid"], "journal": r.get("journal"),
                                   "date": r.get("date")},
                            source="PubMed", url=r["url"], fetched_at=NOW()))
            tag_and_link(k, r["title"] or "", "PubMed", r["url"])
    except FetchError as e:
        note("PubMed", "search", e)

    try:
        for r in BC.openfda_device("electroencephalograph", limit=limit):
            k = "fda510k:%s" % r["k_number"]
            g.add_node(Node(key=k, kind="device",
                            label="%s (%s)" % (r.get("device_name"), r["k_number"]),
                            attrs={"k_number": r["k_number"], "class": r.get("class"),
                                   "decision_date": r.get("decision_date")},
                            source="openFDA 510(k)", url=r["url"], fetched_at=NOW()))
            tag_and_link(k, r.get("device_name") or "", "openFDA 510(k)", r["url"])
            if r.get("applicant"):
                ak = "org:" + _slug(r["applicant"])
                g.add_node(Node(key=ak, kind="organization", label=r["applicant"],
                                source="openFDA 510(k)", url=r["url"], fetched_at=NOW()))
                g.add_edge(Edge(src=ak, dst=k, rel="manufactures",
                                attrs={"k_number": r["k_number"]},
                                source="openFDA 510(k)", url=r["url"], fetched_at=NOW()))
    except FetchError as e:
        note("openFDA 510(k)", "search", e)

    # --- bio half: a target's disease/compound chain, tagged into the bio domains ----
    if with_bio:
        try:
            bg = BM.build_biomed_graph(bio_symbol, diseases=5, drugs=5, papers=2)
            for n in bg.nodes.values():
                n.key = "bio_" + n.key if not n.key.startswith(("dir:", "bio:", "stage:")) else n.key
                g.add_node(n)
                tag_and_link(n.key, n.label, n.source, n.url)
            for e in bg.edges.values():
                if not e.src.startswith(("dir:", "bio:", "stage:")) \
                        and not e.dst.startswith(("dir:", "bio:", "stage:")):
                    e.src = "bio_" + e.src
                    e.dst = "bio_" + e.dst
                g.add_edge(e)
            for n2 in getattr(bg, "_notes", []):
                notes.append(n2)
        except Exception as e:
            note("biomed", "build", e)

    # 类器官 direction is where the two domains actually meet -- say it with an edge.
    for bid, bk in bio_keys.items():
        if bid == "organoid_manufacturing":
            g.add_edge(Edge(src=bk, dst=dir_keys["organoid"], rel="enables",
                            attrs={"basis": "类器官制造是 +1 片上脑机接口的供给端"},
                            source="实施方案 图1", url="", fetched_at=NOW()))
        g.add_edge(Edge(src=bk, dst="dir:neuromodulation", rel="feeds",
                        attrs={"basis": "生物制造提供材料/器件上游"},
                        source="实施方案 生物制造定义", url="", fetched_at=NOW()))

    # --- the 商业价值 layer: 板块 -> 上市公司 -> 市值 -> 涉主题公告 ---------------------
    # Off by default so the offline test suite never touches the wire; the CLI turns it on.
    if market:
        from . import market as MK
        try:
            g, mk_summary = MK.build_market_layer(
                g, boards=("bci", "alzheimer"),
                keywords=("脑机接口", "脑机"),
                graph_out=True)
            notes.extend(getattr(g, "_notes", []) or [])
            g._market_summary = mk_summary  # type: ignore[attr-defined]
        except Exception as e:                      # a market fetch must not sink the map
            note("market layer", "board/quotes/disclosures", e)

    # --- bridge: 披露标题 -> 6+1 路线 ------------------------------------------------
    # A disclosure title is the only public sentence I can read without opening the PDF,
    # so the edge says exactly that: title-keyword evidence, and the keyword rides on it.
    bridged = 0
    for n in list(g.nodes.values()):
        if n.kind != "disclosure":
            continue
        for hit in D.classify(n.label):
            dk = dir_keys.get(hit["direction"])
            if not dk:
                continue
            g.add_edge(Edge(src=n.key, dst=dk, rel="mentions_direction",
                            attrs={"keyword": hit["match"],
                                   "confidence": hit["confidence"],
                                   "basis": "仅公告标题命中，未读全文"},
                            source=n.source, url=n.url, fetched_at=NOW()))
            bridged += 1
    if bridged:
        notes.append({"source": "披露-路线桥", "what": "公告标题 -> 6+1",
                      "error": "%d 条边；仅标题，未读全文" % bridged})

    g._notes = notes  # type: ignore[attr-defined]
    return g
