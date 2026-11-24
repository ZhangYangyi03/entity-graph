"""The non-English half of the sweep: German and French sources, plus multilingual labels.

Why this exists: everything else in this repo is an English-language source. The
实施方案 is Chinese, the 6+1 routes are Chinese, and the two European markets that
actually fund BCI -- Germany (DRKS, GND, DFG) and France (HAL, SIRENE, Bpifrance) --
publish in their own languages. Sweeping only English silently deletes those.

Measured on this host, 2026-11-23:

    source                        access   hits for the BCI theme
    Wikidata (multi-label)        REST     1 entity, labels in en/de/fr/zh
    HAL  (French open archive)    REST     1123 publications
    lobid GND (German authority)  REST     150 records (incl. companies such as g.tec)
    DRKS (German trial registry)  HTML     0 parsed -- search is POST-only, not wired
    recherche-entreprises (FR)    REST     live, SIRENE-based company search
    Europe PMC LANG:ger/fre/chi   REST     3 / 1 / 174 papers in those languages
    PubMed [lang] filter          REST     17 German-language papers on BCI

Refused, and why (so the next session does not retry them as bugs):

    DNB SRU (services.dnb.de)           403, needs an account
    CTIS (euclinicaltrials.eu)           403, needs an API key
    OpenAIRE (api.openaire.eu)          TLS handshake timeout from this host
    CORDIS search API                   500
    data.gouv.fr "cerveau"              0 records -- the catalogue has no such dataset

DRKS is recorded as a known gap rather than faked: its search endpoint is a POST form
and a GET returns an empty shell. Writing a row for it without a working query would be
the exact kind of invention this repo refuses.

Korean, resolved 2026-11-24. KoreaMed (SearchBasic.php) and KoreaScience (search.page)
were measured again and both still hide the rows -- KoreaMed answers 200 with a byte-for-byte
identical 85,908-byte shell for every query, KoreaScience renders the list in JavaScript.
KCI's portal does return them, behind a POST to poArtiSearList.kci: 274 records for
`brain-computer interface` over 100/100/74, with title, author, journal, society, KCI
subject class, citation count and the ART id. That is what `kci()` now does.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import urllib.parse
import urllib.request
import html as _html

from .fetch import DEFAULT_UA, SSL_CONTEXT, FetchError
from .model import Edge, Graph, Node

NOW = lambda: _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")

BCI_ENTITY = "Q897410"          # 脑机接口, verified against the Wikidata API
KCI_SEARCH = "https://www.kci.go.kr/kciportal/po/search/poArtiSearList.kci"
KCI_URL = KCI_SEARCH
KCI_VIEW = "https://www.kci.go.kr/kciportal/ci/sereArticleSearch/ciSereArtiView.kci?sereArticleSearchBean.artiId="
LANG_SOURCES = {"en": "English", "de": "Deutsch", "fr": "Français", "zh": "中文"}


def _get(url: str, timeout: float = 30.0, accept: str = "*/*") -> str:
    req = urllib.request.Request(url, headers={"User-Agent": DEFAULT_UA, "Accept": accept})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=SSL_CONTEXT) as r:
            return r.read().decode("utf-8", "ignore")
    except Exception as e:
        raise FetchError("%s: %s <- %s" % (type(e).__name__, e, url)) from e


def _post(url: str, form: dict, timeout: float = 60.0) -> str:
    """POST a form and return the body. KCI's article list exists only behind a POST."""
    body = urllib.parse.urlencode(form).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={
        "User-Agent": DEFAULT_UA,
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept-Language": "ko,en;q=0.8",
        "Referer": KCI_SEARCH})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=SSL_CONTEXT) as r:
            return r.read().decode("utf-8", "ignore")
    except Exception as e:
        raise FetchError("%s: %s <- %s" % (type(e).__name__, e, url)) from e


def _json(url: str, **kw):
    return json.loads(_get(url, **kw))


# ------------------------------------------------------------------ labels
def wikidata_labels(qid: str = BCI_ENTITY, langs=("en", "de", "fr", "zh", "ja")) -> dict:
    """The same concept's name in each language, from Wikidata's own labels.

    This is what makes a bilingual map possible: one entity key (`wd:Q897410`) with a
    label per language, instead of four unrelated nodes that never join up.
    """
    url = ("https://www.wikidata.org/w/api.php?action=wbgetentities&format=json&props=labels|descriptions"
           "&ids=%s&languages=%s" % (qid, "|".join(langs)))
    d = _json(url)
    ent = (d.get("entities") or {}).get(qid)
    if not ent:
        raise FetchError("wikidata returned no entity for %s" % qid)
    out = {"qid": qid, "labels": {}, "descriptions": {}}
    for lc in langs:
        lab = ((ent.get("labels") or {}).get(lc) or {}).get("value")
        des = ((ent.get("descriptions") or {}).get(lc) or {}).get("value")
        if lab:
            out["labels"][lc] = lab
        if des:
            out["descriptions"][lc] = des
    return out


def wikidata_search(term: str, lang: str = "en", limit: int = 5) -> list[dict]:
    """Find the QIDs a term names in one language, so a de/fr query can be resolved."""
    url = ("https://www.wikidata.org/w/api.php?action=wbsearchentities&format=json"
           "&language=%s&uselang=%s&limit=%d&search=%s"
           % (lang, lang, limit, urllib.parse.quote(term)))
    d = _json(url)
    return [{"qid": x["id"], "label": x.get("label"),
             "description": (x.get("description") or ""), "lang": lang}
            for x in d.get("search") or []]


# ------------------------------------------------------------------ French
def hal(query: str = "brain-computer interface", rows: int = 20,
        lang: str = "fr") -> list[dict]:
    """HAL: the French national open archive. 1123 hits for the BCI theme.

    `lang` filters on the document language HAL records (fr/en), so a French-language
    paper can be separated from an English one published in France.
    """
    q = query if lang == "any" else '%s AND lang_s:%s' % (query, lang)
    url = ("https://api.archives-ouvertes.fr/search/?q=%s&rows=%d&wt=json"
           "&fl=title_s,uri_s,producedDateY_i,authFullName_s,docType_s,lang_s,labStructName_s"
           % (urllib.parse.quote(q), rows))
    d = _json(url)
    out = []
    for doc in (d.get("response") or {}).get("docs") or []:
        out.append({"title": (doc.get("title_s") or [""])[0],
                    "url": doc.get("uri_s") or "",
                    "year": doc.get("producedDateY_i"),
                    "authors": (doc.get("authFullName_s") or [])[:4],
                    "doc_type": doc.get("docType_s"),
                    "lang": doc.get("lang_s"),
                    "labs": (doc.get("labStructName_s") or [])[:3]})
    return out


def recherche_entreprise(q: str = "neurotech", per_page: int = 10) -> list[dict]:
    """French company register via the open recherche-entreprises API (SIRENE based).

    This is the French counterpart of the A-share half: a company with a SIREN, an
    activity code and a creation date, searchable by free text.
    """
    url = ("https://recherche-entreprises.api.gouv.fr/search?q=%s&per_page=%d"
           % (urllib.parse.quote(q), per_page))
    d = _json(url)
    out = []
    for r in d.get("results") or []:
        out.append({"name": r.get("nom_complet"), "siren": r.get("siren"),
                    "naf": r.get("activite_principale"),
                    "created": r.get("date_creation"),
                    "employees": r.get("tranche_effectif_salarie"),
                    "city": ((r.get("siege") or {}).get("libelle_commune")),
                    "section": r.get("section_activite_principale")})
    return out


# ------------------------------------------------------------------ German
def gnd(query: str = "brain-computer", size: int = 20) -> list[dict]:
    """GND: the German integrated authority file. Companies and research bodies are in it.

    Measured example: a Utrecht-based BCI company with its establishment date and place,
    which no English source in this repo had.
    """
    url = "https://lobid.org/gnd/search?q=%s&format=json&size=%d" % (urllib.parse.quote(query), size)
    d = _json(url)
    out = []
    for m in d.get("member") or []:
        names = m.get("preferredName") or m.get("variantName") or ""
        out.append({"gnd": m.get("id"), "name": names,
                    "type": (m.get("type") or ["?"])[0] if isinstance(m.get("type"), list) else m.get("type"),
                    "place": ((m.get("placeOfBusiness") or [{}])[0].get("label")
                              if m.get("placeOfBusiness") else None),
                    "established": (m.get("dateOfEstablishment") or [None])[0],
                    "url": m.get("id")})
    return out


# ------------------------------------------------------------------ multilingual papers
def europepmc_lang(term: str = '"brain-computer interface"', lang: str = "ger",
                   page_size: int = 10) -> dict:
    """Europe PMC, filtered by document language. ger/fre/chi are real values."""
    q = '%s AND LANG:"%s"' % (term, lang)
    url = ("https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=%s&format=json&pageSize=%d"
           % (urllib.parse.quote(q), page_size))
    d = _json(url)
    rows = [{"title": r.get("title"), "pmid": r.get("pmid"), "year": r.get("pubYear"),
             "lang": r.get("language"), "journal": r.get("journalTitle"),
             "url": "https://europepmc.org/article/MED/%s" % r.get("pmid") if r.get("pmid") else ""}
            for r in (d.get("resultList") or {}).get("result") or []]
    return {"lang": lang, "hit_count": d.get("hitCount", 0), "rows": rows}


# ------------------------------------------------------------------ graph
def build_multilingual_graph(term: str = "brain-computer interface", *,
                             rows: int = 10, with_fr_companies: bool = True,
                             graph: Graph | None = None) -> Graph:
    """One concept, four labels, and the German/French evidence hanging off them."""
    g = graph or Graph()
    notes: list[dict] = []

    def note(source, what, err):
        notes.append({"source": source, "what": what, "error": str(err)[:200]})

    # the concept as ONE node carrying every language's name
    labels = {}
    try:
        labels = wikidata_labels()
    except FetchError as e:
        note("Wikidata 标签", BCI_ENTITY, e)
    ck = "wd:" + BCI_ENTITY
    g.add_node(Node(key=ck, kind="concept", label=(labels.get("labels") or {}).get("en") or term,
                    attrs={"labels": labels.get("labels") or {},
                           "descriptions": labels.get("descriptions") or {},
                           "languages": sorted((labels.get("labels") or {}).keys()),
                           "qid": BCI_ENTITY},
                    source="Wikidata（多语言标签）",
                    url="https://www.wikidata.org/wiki/%s" % BCI_ENTITY, fetched_at=NOW()))

    # German: authority records
    try:
        for r in gnd("brain-computer", size=rows):
            if not r["gnd"]:
                continue
            k = "gnd:" + r["gnd"].rsplit("/", 1)[-1]
            g.add_node(Node(key=k, kind="de_authority", label=r["name"] or k,
                            attrs={"gnd_id": r["gnd"], "place": r["place"],
                                   "established": r["established"], "type": str(r["type"])},
                            source="GND（德国规范档）", url=r["url"], fetched_at=NOW()))
            g.add_edge(Edge(src=k, dst=ck, rel="about_concept",
                            attrs={"lang": "de", "basis": "GND 检索命中 brain-computer"},
                            source="GND", url=r["url"], fetched_at=NOW()))
    except FetchError as e:
        note("GND（德国）", "brain-computer", e)

    # German trial registry: named as a gap, not faked
    try:
        html = _get("https://drks.de/search/en/trial/search?query=brain-computer&page=1")
        ids = re.findall(r"DRKS\d{8}", html or "")
        if not ids:
            note("DRKS（德国试验注册）", "GET 检索", "返回空壳页面（检索是 POST 表单），"
                                          "未接入；不写假记录")
    except FetchError as e:
        note("DRKS（德国试验注册）", "GET 检索", e)

    # French: HAL publications
    try:
        fr = hal(term, rows=rows, lang="fr")
        for i, r in enumerate(fr):
            k = "hal:fr:%d" % i
            g.add_node(Node(key=k, kind="fr_publication", label=(r["title"] or "")[:120],
                            attrs={"year": r["year"], "authors": r["authors"],
                                   "doc_type": r["doc_type"], "lang": r["lang"],
                                   "labs": r["labs"]},
                            source="HAL（法国开放档案）", url=r["url"], fetched_at=NOW()))
            g.add_edge(Edge(src=k, dst=ck, rel="about_concept",
                            attrs={"lang": "fr", "basis": "HAL 检索命中"}, source="HAL",
                            url=r["url"], fetched_at=NOW()))
    except FetchError as e:
        note("HAL（法国）", term, e)

    # French companies
    if with_fr_companies:
        try:
            for r in recherche_entreprise("neurotech", per_page=rows):
                k = "frco:%s" % (r["siren"] or r["name"])
                g.add_node(Node(key=k, kind="fr_company", label=r["name"] or k,
                                attrs={"siren": r["siren"], "naf": r["naf"],
                                       "created": r["created"], "city": r["city"],
                                       "section": r["section"]},
                                source="recherche-entreprises.api.gouv.fr（SIRENE）",
                                url="https://annuaire-entreprises.data.gouv.fr/entreprise/%s" % r["siren"],
                                fetched_at=NOW()))
                g.add_edge(Edge(src=k, dst=ck, rel="about_concept",
                                attrs={"lang": "fr", "basis": "企业名检索命中 neurotech"},
                                source="recherche-entreprises", url="", fetched_at=NOW()))
        except FetchError as e:
            note("法国企业库", "neurotech", e)

    # papers by language, so "只在英语里找过" becomes a countable fact
    for lang in ("ger", "fre", "chi"):
        try:
            res = europepmc_lang(term='"brain-computer interface"', lang=lang, page_size=rows)
            lk = "lang:" + lang
            g.add_node(Node(key=lk, kind="language_slice", label=LANG_SOURCES.get(
                {"ger": "de", "fre": "fr", "chi": "zh"}.get(lang, lang), lang),
                attrs={"lang_code": lang, "hit_count": res["hit_count"],
                       "sample": len(res["rows"])},
                source="Europe PMC LANG 过滤", url="https://europepmc.org/", fetched_at=NOW()))
            g.add_edge(Edge(src=lk, dst=ck, rel="sliced_by_language",
                            attrs={"hit_count": res["hit_count"]},
                            source="Europe PMC", url="", fetched_at=NOW()))
            for i, r in enumerate(res["rows"]):
                pk = "epmc:%s:%d" % (lang, i)
                g.add_node(Node(key=pk, kind="publication_%s" % lang,
                                label=(r["title"] or "")[:120],
                                attrs={"pmid": r["pmid"], "year": r["year"],
                                       "lang": r["lang"], "journal": r["journal"]},
                                source="Europe PMC", url=r["url"], fetched_at=NOW()))
                g.add_edge(Edge(src=pk, dst=lk, rel="in_language_slice",
                                attrs={}, source="Europe PMC", url=r["url"], fetched_at=NOW()))
        except FetchError as e:
            note("Europe PMC", lang, e)

    g._notes = notes  # type: ignore[attr-defined]
    return g


def language_report(g: Graph) -> str:
    """How much of this map is not English, in numbers, so the gap is measurable."""
    L = []
    labels = {}
    for n in g.nodes.values():
        if n.kind == "concept":
            labels = (n.attrs or {}).get("labels") or {}
    if labels:
        L.append("  概念多语言标签：")
        for lc in ("en", "de", "fr", "zh", "ja"):
            if lc in labels:
                L.append("    %-3s %s" % (lc, labels[lc]))
    for n in g.nodes.values():
        if n.kind == "language_slice":
            a = n.attrs or {}
            L.append("  %-4s %-16s 语言文献命中 %4d 条（本图取样 %d 条）"
                     % (a.get("lang_code"), (a.get("source_name") or ""), 
                        a.get("hit_count", 0), a.get("sample", 0)))
    kinds: dict[str, int] = {}
    for n in g.nodes.values():
        kinds[n.kind] = kinds.get(n.kind, 0) + 1
    for k in ("de_authority", "fr_publication", "fr_company"):
        if kinds.get(k):
            L.append("  %-14s %d 条" % (k, kinds[k]))
    return "\n".join(L)


# ------------------------------------------------------------------ Japanese / Korean
def jstage(term: str = "brain-computer interface", count: int = 10) -> dict:
    """J-STAGE (Japan) Atom API, service=3 = 論文検索.

    Measured on this host: 14673 hits for the bare term `brain-computer`, 2853 for the
    quoted phrase `"brain-computer interface"` that the CLI uses by default. Both numbers
    are real; the docstring keeps both so neither reads as a contradiction later.

    Returns the hit count as well as the rows, because the count is the point: a
    Japanese-language sweep that reports 3 rows while the archive holds 14673 is
    hiding its own coverage.
    """
    url = ("https://api.jstage.jst.go.jp/searchapi/do?service=3&text=%s&count=%d"
           % (urllib.parse.quote(term), count))
    x = _get(url)
    total = None
    m = re.search(r"<opensearch:totalResults>\s*(\d+)", x)
    if m:
        total = int(m.group(1))
    rows = []
    for blk in re.findall(r"<entry>(.*?)</entry>", x, re.S):
        title = re.search(r"<title>(.*?)</title>", blk, re.S)
        link = re.search(r'<link[^>]*href="([^"]+)"', blk)
        date = re.search(r"<prism:publicationDate>(.*?)</prism:publicationDate>", blk)
        t = (title.group(1) if title else "").strip()
        t = re.sub(r"^<!\[CDATA\[|\]\]>$", "", t).strip()
        rows.append({"title": t, "url": link.group(1) if link else "",
                     "date": date.group(1) if date else ""})
    return {"total": total, "returned": len(rows), "rows": rows}


def cinii(term: str = '"brain-computer interface"', count: int = 10) -> dict:
    """CiNii Research (Japan) OpenSearch.

    Measured on this host: 4287 hits for `"brain-computer interface"` queried directly,
    4315 when the CLI's already-quoted term is passed through. Same archive, two spellings
    of the query -- recorded so a future reader does not think one of them is wrong.
    """
    url = ("https://cir.nii.ac.jp/opensearch/all?q=%s&format=json&count=%d"
           % (urllib.parse.quote(term), count))
    d = _json(url)
    rows = []
    for it in d.get("items") or []:
        rows.append({"title": (it.get("title") or "")[:200],
                     "date": it.get("dc:date") or it.get("prism:publicationDate"),
                     "url": it.get("@id") or ""})
    total = d.get("opensearch:totalResults")
    try:
        total = int(total)
    except (TypeError, ValueError):
        pass
    return {"total": total, "returned": len(rows), "rows": rows}


def kci(term: str = "brain-computer interface", per_page: int = 100,
        max_pages: int = 4) -> dict:
    """KCI (Korea Citation Index) article search.

    Measured on this host: KoreaMed's SearchBasic.php answers 200 with a fixed 85,908-byte
    shell no matter what is posted (no records, no total), and KoreaScience.kr's search.page
    returns a JavaScript shell with zero server-rendered hits. Both look like "0 results";
    both are really "the results are not in the HTML". KCI's own portal search does render
    them, but only through a POST to poArtiSearList.kci -- the GET page is the shell.

    What works: POST poArtiSearList.kci with poSearchBean.conditionList=KEYALL,
    poSearchBean.keywordList=<term>, poSearchBean.startPg=<n>, poSearchBean.docsCount=<per>.
    Measured result for `brain-computer interface`: 274 records over pages of 100/100/74,
    each row carrying title, author, journal, society, issue date, KCI subject class,
    citation count, page range and the ART id needed for the article URL.

    Records are kept as-is (Korean title or English title, whichever the journal filed);
    the language of a record is not assumed from the country of the journal.
    """
    base = {"poSearchBean.conditionList": "KEYALL", "poSearchBean.userRecommend": "N",
            "poSearchBean.searType": "all"}
    rows: list[dict] = []
    seen: set[str] = set()
    total = None
    for pg in range(1, max_pages + 1):
        form = dict(base, **{"poSearchBean.keywordList": term,
                             "poSearchBean.startPg": str(pg),
                             "poSearchBean.docsCount": str(per_page)})
        try:
            h = _post(KCI_URL, form)
        except FetchError as e:
            if rows:
                break
            raise
        if total is None:
            flat = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h))
            m = re.search(r"총\s*([\d,]+)\s*건", flat)
            total = int(m.group(1).replace(",", "")) if m else None
        got = 0
        for blk in h.split('<input type="hidden" name="R_ROWNUM"')[1:]:
            def field(name: str, _b=blk) -> str:
                m = re.search(r'name="%s" value="([^"]*)"' % name, _b)
                return m.group(1) if m else ""
            aid = field("R_SYST_LOCA_ID1")
            if not aid or aid in seen:
                continue
            seen.add(aid)
            got += 1
            rows.append({
                "id": aid, "title": _html.unescape(field("R_INDE_TITL")), "author": field("R_CRET_NM"),
                "journal": field("R_SERE_NM"), "society": field("R_PUBI_INSI_NM"),
                "date": field("R_PUBI_DT"), "major": field("R_MAJOR"),
                "cited": field("R_CITATED_IDX"),
                "pages": ("%s~%s" % (field("R_ST_PG"), field("R_END_PG"))
                          if field("R_ST_PG") else ""),
                "url": KCI_VIEW + aid})
        if got == 0:
            break
    return {"total": total, "returned": len(rows), "rows": rows}


def build_cjk_graph(term: str = '"brain-computer interface"', *, count: int = 8,
                    graph: Graph | None = None) -> Graph:
    """Japanese and Korean slices. Korea is reported as a gap, not filled with guesses."""
    g = graph or Graph()
    notes: list[dict] = []

    def note(source, what, err):
        notes.append({"source": source, "what": what, "error": str(err)[:200]})

    ck = "wd:" + BCI_ENTITY
    if ck not in g.nodes:
        g.add_node(Node(key=ck, kind="concept", label=term, attrs={"qid": BCI_ENTITY},
                        source="Wikidata", url="https://www.wikidata.org/wiki/" + BCI_ENTITY,
                        fetched_at=NOW()))

    for name, fn, lang in (("J-STAGE（日本）", jstage, "ja"), ("CiNii（日本）", cinii, "ja")):
        try:
            res = fn(term, count=count)
            if not res["rows"]:
                note(name, term, "0 rows parsed")
                continue
            slice_key = "jpslice:" + lang + ":" + name.split("（")[0].lower().replace("-", "")
            g.add_node(Node(key=slice_key, kind="language_slice", label=name,
                            attrs={"lang_code": lang, "hit_count": res["total"],
                                   "sample": res["returned"], "source_name": name},
                            source=name, url="", fetched_at=NOW()))
            g.add_edge(Edge(src=slice_key, dst=ck, rel="sliced_by_language",
                            attrs={"hit_count": res["total"]}, source=name, url="",
                            fetched_at=NOW()))
            for i, r in enumerate(res["rows"]):
                pk = "jp:%s:%d" % (slice_key.split(":")[-1], i)
                g.add_node(Node(key=pk, kind="publication_ja", label=(r["title"] or "")[:120],
                                attrs={"date": r.get("date"), "lang": "ja"},
                                source=name, url=r.get("url") or "", fetched_at=NOW()))
                g.add_edge(Edge(src=pk, dst=slice_key, rel="in_language_slice", attrs={},
                                source=name, url=r.get("url") or "", fetched_at=NOW()))
        except FetchError as e:
            note(name, term, e)

    # Korea. KoreaMed and KoreaScience were both re-measured and both still hide their
    # results (KoreaMed answers 200 with a fixed 85,908-byte shell for any query; KoreaScience
    # renders the list in JavaScript). KCI's portal does render them, behind a POST.
    try:
        kres = kci(term.strip('"') or "brain-computer interface", per_page=100, max_pages=4)
        if kres["rows"]:
            kslice = "krslice:kci"
            g.add_node(Node(key=kslice, kind="language_slice", label="KCI（韩国）",
                            attrs={"lang_code": "ko", "hit_count": kres["total"],
                                   "sample": kres["returned"], "source_name": "KCI",
                                   "note": "记录语言不按刊物国别推断：标题原文照抄"},
                            source="KCI", url="https://www.kci.go.kr/", fetched_at=NOW()))
            g.add_edge(Edge(src=kslice, dst=ck, rel="sliced_by_language",
                            attrs={"hit_count": kres["total"]}, source="KCI", url="",
                            fetched_at=NOW()))
            for r in kres["rows"]:
                pk = "kr:kci:" + r["id"]
                g.add_node(Node(key=pk, kind="publication_ko", label=(r["title"] or "")[:120],
                                attrs={"date": r.get("date"), "journal": r.get("journal"),
                                       "author": r.get("author"), "major": r.get("major"),
                                       "cited": r.get("cited"), "pages": r.get("pages"),
                                       "society": r.get("society")},
                                source="KCI", url=r.get("url") or "", fetched_at=NOW()))
                g.add_edge(Edge(src=pk, dst=kslice, rel="in_language_slice", attrs={},
                                source="KCI", url=r.get("url") or "", fetched_at=NOW()))
                if r.get("society"):
                    sk = "kr:soc:" + re.sub(r"\s+", "-", r["society"])
                    g.add_node(Node(key=sk, kind="organization", label=r["society"],
                                    attrs={"country": "KR"},
                                    source="KCI", url="https://www.kci.go.kr/", fetched_at=NOW()))
                    g.add_edge(Edge(src=sk, dst=pk, rel="publishes", attrs={},
                                    source="KCI", url=r.get("url") or "", fetched_at=NOW()))
        else:
            note("KCI（韩国）", term, "0 rows parsed")
    except FetchError as e:
        note("KCI（韩国）", term, e)
    g._notes = notes  # type: ignore[attr-defined]
    return g
