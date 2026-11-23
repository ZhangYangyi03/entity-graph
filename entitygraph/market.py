"""What the industry map is worth: listed-company boards, market caps, disclosures.

This is the 商业价值 layer. Everything here was measured from this host on 2026-11-23:

    source                 access   what it contributes
    同花顺 概念板块         HTML     板块目录 + 成分股全名单（脑机接口 = 50 只 A 股）
    腾讯行情 qt.gtimg.cn    REST     总市值/流通市值/PE/PB/换手率，批量 60 只一次（gbk）
    巨潮资讯 cninfo         REST     公告全文检索（标题 + secCode + PDF 路径）

Three honest limits, stated here rather than discovered later:

    * 巨潮全文检索只覆盖 沪深两市 的标题/正文索引，实测关键词「脑机接口」只回 4 条，
      所以它是「有据可查的披露」而不是「全部涉脑机的公告」；返回条数原样写进节点。
    * 同花顺板块成分是**市场归类**，不是技术判定：一家公司被归进脑机接口概念，
      不代表它真有脑机产品。图谱里这种边一律 basis="板块归属，非技术判定"，并单独计数。
    * 东方财富 push2 接口在本次会话后期对本机直接断连（RemoteDisconnected），
      所以东方财富不作为依赖；同花顺/腾讯/巨潮三路是实际可用的组合。

Nothing here needs a key or an account.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import time
import urllib.parse
import urllib.request

from .fetch import DEFAULT_UA, SSL_CONTEXT, FetchError, get, q
from .model import Edge, Graph, Node

NOW = lambda: _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# 板块代码来自同花顺概念目录（http://q.10jqka.com.cn/gn/），实测取到，不是猜的。
BOARDS = {
    "bci":            {"code": "308535", "name": "脑机接口",     "market": "A股"},
    "alzheimer":      {"code": "308614", "name": "阿尔茨海默概念", "market": "A股"},
}


def _get_gbk(url: str, referer: str = "http://q.10jqka.com.cn/", timeout: float = 30.0) -> str:
    """同花顺 serves gb18030 and drops clients without a browser UA + referer."""
    req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA, "Referer": referer})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=SSL_CONTEXT) as r:
            return r.read().decode("gb18030", "ignore")
    except Exception as e:
        raise FetchError("%s: %s <- %s" % (type(e).__name__, e, url)) from e


# ------------------------------------------------------------------ boards
def ths_concepts(limit: int = 400) -> list[dict]:
    """The 同花顺 concept directory: 361 boards with their internal code."""
    html = _get_gbk("http://q.10jqka.com.cn/gn/")
    hits = re.findall(r'href="http://q\.10jqka\.com\.cn/gn/detail/code/(\d+)/"[^>]*>([^<]{2,24})</a>', html)
    return [{"code": c, "name": n.strip()} for c, n in hits[:limit]]


def ths_board_members(code: str, max_pages: int = 40) -> list[dict]:
    """Every constituent of one board, with the columns 同花顺 actually publishes.

    Paged at 10 rows per page; the loop stops on the page count in the page itself
    (page_info "n/m"), so a board that grows cannot silently truncate the list.
    """
    out, seen = [], set()
    page = _get_gbk("http://q.10jqka.com.cn/gn/detail/code/%s/" % code)
    for n in range(1, max_pages + 1):
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S):
            tds = [re.sub(r"<[^>]+>", "", t).strip()
                   for t in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
            if len(tds) >= 11 and re.fullmatch(r"\d{6}", tds[1]) and tds[1] not in seen:
                seen.add(tds[1])
                out.append({"code": tds[1], "name": tds[2], "price": tds[3],
                            "change_pct": tds[4], "turnover": tds[10]})
        m = re.search(r'page_info">(\d+)/(\d+)', page)
        if not m or int(m.group(1)) >= int(m.group(2)):
            break
        page = _get_gbk("http://q.10jqka.com.cn/gn/detail/code/%s/order/asc/page/%d/" % (code, n + 1))
        time.sleep(0.6)
    return out


# ------------------------------------------------------------------ quotes
def tencent_quotes(codes: list[str], batch: int = 60) -> dict[str, dict]:
    """总市值/流通市值/PE/PB for A-share codes, in batches. Field 45 = 总市值(亿).

    The 88-field layout was read off a live response, not from a doc: 1 name,
    2 code, 3 price, 38 换手率, 39 PE(TTM), 44 流通市值(亿), 45 总市值(亿), 46 PB.
    """
    def prefix(c: str) -> str:
        return ("sh" if c[0] in "69" else "sz") + c

    out: dict[str, dict] = {}
    for i in range(0, len(codes), batch):
        chunk = codes[i:i + batch]
        url = "https://qt.gtimg.cn/q=" + ",".join(prefix(c) for c in chunk)
        req = urllib.request.Request(url, headers={"User-Agent": DEFAULT_UA})
        try:
            with urllib.request.urlopen(req, timeout=25.0, context=SSL_CONTEXT) as r:
                text = r.read().decode("gb18030", "ignore")
        except Exception as e:
            raise FetchError("%s: %s <- tencent quote" % (type(e).__name__, e)) from e
        for line in text.split(";"):
            if '="' not in line:
                continue
            f = line.split('="', 1)[1].rstrip('"').split("~")
            if len(f) < 47 or not re.fullmatch(r"\d{6}", f[2] if len(f) > 2 else ""):
                continue
            def num(i):
                try:
                    return float(f[i])
                except (ValueError, IndexError):
                    return None
            out[f[2]] = {"name": f[1], "price": num(3), "turnover_pct": num(38),
                         "pe_ttm": num(39), "float_mcap_yi": num(44),
                         "total_mcap_yi": num(45), "pb": num(46)}
        time.sleep(0.4)
    return out


# ------------------------------------------------------------------ disclosures
def cninfo_search(keyword: str, page_size: int = 30, column: str = "szse",
                  max_pages: int = 10) -> dict:
    """巨潮资讯 公告全文检索. Returns the records AND the count, because the count is small.

    Measured: 「脑机接口」 returns 4 records, not 400. That is the real coverage of this
    endpoint, so a caller must not read a short list as "nothing is disclosed".
    """
    def page(num: int) -> dict:
        body = urllib.parse.urlencode({
            "pageNum": str(num), "pageSize": str(page_size), "column": column,
            "tabName": "fulltext", "searchkey": keyword, "sortName": "", "sortType": "",
            "isHLtitle": "true"}).encode()
        req = urllib.request.Request(
            "http://www.cninfo.com.cn/new/fulltextSearch/full", data=body,
            headers={"User-Agent": DEFAULT_UA, "Accept": "*/*",
                     "Content-Type": "application/x-www-form-urlencoded",
                     "Referer": "http://www.cninfo.com.cn/",
                     "X-Requested-With": "XMLHttpRequest"})
        try:
            with urllib.request.urlopen(req, timeout=30.0, context=SSL_CONTEXT) as r:
                return json.loads(r.read().decode("utf-8", "ignore"))
        except Exception as e:
            raise FetchError("%s: %s <- cninfo p%d" % (type(e).__name__, e, num)) from e

    first = page(1)
    total = first.get("totalAnnouncement") or 0
    raw = list(first.get("announcements") or [])
    # One page is not the whole answer: 「脑机」 alone matched 82 records. Walk the pages
    # so `returned` can be compared honestly against `total`.
    pages = min(max_pages, max(1, -(-total // page_size)))
    for n in range(2, pages + 1):
        raw += list(page(n).get("announcements") or [])
        time.sleep(0.4)

    rows, seen = [], set()
    for a in raw:
        title = re.sub(r"</?em>", "", a.get("announcementTitle") or "")
        adj = a.get("adjunctUrl") or ""
        key = (a.get("secCode"), adj)
        if key in seen:
            continue
        seen.add(key)
        rows.append({"sec_code": a.get("secCode"), "sec_name": a.get("secName"),
                     "title": title, "date": a.get("announcementTime"),
                     "url": ("http://static.cninfo.com.cn/" + adj) if adj else "",
                     "pdf": adj})
    return {"keyword": keyword, "total": total, "returned": len(rows), "rows": rows}


# ------------------------------------------------------------------ the value layer
def build_market_layer(graph: Graph, *, boards: tuple[str, ...] = ("bci", "alzheimer"),
                        keywords: tuple[str, ...] = ("脑机接口", "脑机"),
                        graph_out: bool = True) -> tuple[Graph, dict]:
    """Attach 板块 -> 上市公司 -> 市值 -> 公告 to an existing graph, and summarise it.

    The summary is the part worth money: how many listed companies the theme actually
    carries, what they are worth together, and which of them have a disclosure with the
    keyword in it. Those three numbers are computed from the responses, not estimated.
    """
    g = graph
    notes: list[dict] = []
    summary = {"boards": [], "companies": 0, "total_mcap_yi": 0.0,
               "median_mcap_yi": 0.0, "disclosed": 0, "notes": notes}

    def note(source, what, err):
        notes.append({"source": source, "what": what, "error": str(err)[:200]})

    seen_codes: set[str] = set()
    all_caps: list[float] = []

    for bid in boards:
        meta = BOARDS.get(bid)
        if not meta:
            note("同花顺板块", bid, "unknown board id; add it to market.BOARDS with the code "
                                    "read from http://q.10jqka.com.cn/gn/")
            continue
        try:
            members = ths_board_members(meta["code"])
        except FetchError as e:
            note("同花顺板块", meta["name"], e)
            continue
        if not members:
            note("同花顺板块", meta["name"], "0 constituents parsed; page layout may have changed")
            continue

        bk = "board:ths:%s" % meta["code"]
        quotes = {}
        try:
            quotes = tencent_quotes([m["code"] for m in members])
        except FetchError as e:
            note("腾讯行情", meta["name"], e)

        caps_here = []
        board_node = Node(key=bk, kind="market_board", label=meta["name"],
                          attrs={"ths_code": meta["code"], "market": meta["market"],
                                 "constituents": len(members),
                                 "quoted": len(quotes),
                                 "basis": "市场板块归类，非技术判定"},
                          source="同花顺概念板块",
                          url="http://q.10jqka.com.cn/gn/detail/code/%s/" % meta["code"],
                          fetched_at=NOW())
        g.add_node(board_node)
        for m in members:
            qt = quotes.get(m["code"]) or {}
            cap = qt.get("total_mcap_yi")
            if cap:
                caps_here.append(cap)
                all_caps.append(cap)
            ck = "cnstock:%s" % m["code"]
            g.add_node(Node(key=ck, kind="listed_company",
                            label="%s (%s)" % (m["name"], m["code"]),
                            attrs={"code": m["code"], "market": meta["market"],
                                   "price": qt.get("price"),
                                   "total_mcap_yi": cap,
                                   "float_mcap_yi": qt.get("float_mcap_yi"),
                                   "pe_ttm": qt.get("pe_ttm"), "pb": qt.get("pb"),
                                   "turnover_pct": qt.get("turnover_pct")},
                            source="同花顺概念板块 + 腾讯行情",
                            url="http://q.10jqka.com.cn/gn/detail/code/%s/" % meta["code"],
                            fetched_at=NOW()))
            # A board edge is a market claim, so it says so and is counted separately.
            g.add_edge(Edge(src=ck, dst=bk, rel="in_board",
                            attrs={"basis": "板块归属，非技术判定"},
                            source="同花顺概念板块",
                            url="http://q.10jqka.com.cn/gn/detail/code/%s/" % meta["code"],
                            fetched_at=NOW()))
            seen_codes.add(m["code"])

        # The per-board totals belong on the node as well as in the summary: the brief is
        # rebuilt later from the JSON alone, and a number that only lived in memory is lost.
        sc = sorted(caps_here)
        mid = sc[len(sc) // 2] if len(sc) % 2 else ((sc[len(sc) // 2 - 1] + sc[len(sc) // 2]) / 2.0
                                                    if sc else 0.0)
        board_node.attrs["mcap_yi"] = round(sum(caps_here), 2)
        board_node.attrs["median_mcap_yi"] = round(mid, 2)
        summary["boards"].append({"id": bid, "name": meta["name"], "constituents": len(members),
                                  "quoted": len(quotes),
                                  "mcap_yi": round(sum(caps_here), 2),
                                  "median_mcap_yi": round(mid, 2)})

    # 公告: which of these companies actually disclosed something with the keyword in it
    ann_by_code: dict[str, list[dict]] = {}
    for kw in keywords:
        try:
            res = cninfo_search(kw)
        except FetchError as e:
            note("巨潮公告", kw, e)
            continue
        if res["total"] > res["returned"]:
            note("巨潮公告", kw,
                 "total=%d but only %d returned by one page; the list is truncated, not complete"
                 % (res["total"], res["returned"]))
        for r in res["rows"]:
            ann_by_code.setdefault(r["sec_code"], []).append(r)

    for code, rows in ann_by_code.items():
        ck = "cnstock:%s" % code
        node = g.nodes.get(ck)
        label = (node.label if node else rows[0].get("sec_name") or code)
        if node is None:
            g.add_node(Node(key=ck, kind="listed_company", label=label,
                            attrs={"code": code, "market": "A股/港股",
                                   "note": "公告命中，但不在所查板块成分内"},
                            source="巨潮资讯", url=rows[0].get("url", ""), fetched_at=NOW()))
        summary["disclosed"] += 1
        for r in rows:
            ak = "announcement:cninfo:%s:%s" % (code, (r.get("pdf") or "").split("/")[-1][:40])
            g.add_node(Node(key=ak, kind="disclosure", label=r["title"][:120],
                            attrs={"sec_code": code, "sec_name": r.get("sec_name"),
                                   "date": r.get("date")},
                            source="巨潮资讯（标题索引）", url=r.get("url", ""),
                            fetched_at=NOW()))
            g.add_edge(Edge(src=ck, dst=ak, rel="discloses",
                            attrs={"note": "公告标题命中关键词，仅表示有披露，不含技术判定"},
                            source="巨潮资讯（标题索引）", url=r.get("url", ""),
                            fetched_at=NOW()))

    summary["companies"] = len(seen_codes)
    if all_caps:
        caps = sorted(all_caps)
        summary["total_mcap_yi"] = round(sum(caps), 2)
        mid = len(caps) // 2
        summary["median_mcap_yi"] = round(
            caps[mid] if len(caps) % 2 else (caps[mid - 1] + caps[mid]) / 2.0, 2)
    g._notes = notes  # type: ignore[attr-defined]
    return g, summary


def value_report(summary: dict) -> str:
    """The three numbers the layer exists to produce, plus what it could not see."""
    lines = []
    for b in summary.get("boards") or []:
        lines.append("  %-14s 成分 %2d 只  有报价 %2d 只  合计总市值 %8.1f 亿元"
                     % (b["name"], b["constituents"], b["quoted"], b["mcap_yi"]))
    lines.append("  去重后上市公司 %d 家；合计总市值 %.1f 亿元；中位数 %.1f 亿元"
                 % (summary.get("companies", 0), summary.get("total_mcap_yi", 0.0),
                    summary.get("median_mcap_yi", 0.0)))
    lines.append("  有涉主题公告的公司 %d 家（巨潮标题索引，非全量）" % summary.get("disclosed", 0))
    for n in summary.get("notes") or []:
        lines.append("  ! %-14s %-10s %s" % (n["source"], n["what"], n["error"][:110]))
    return "\n".join(lines)


# ------------------------------------------------------------------ the sellable artefact
def value_brief(graph_path: str, out_path: str, *, top: int = 20) -> str:
    """Turn a written industry graph into the one-page brief a 项目组 can hand upward.

    Everything in it is read back out of the JSON: the ranking is by the market cap in
    the file, the disclosure list is the `disclosure` nodes, the board totals are the
    `market_board` nodes. No number here is typed in by hand.
    """
    with open(graph_path, encoding="utf-8") as f:
        data = json.load(f)
    nodes = data.get("nodes") or []
    edges = data.get("edges") or []
    by_key = {n["key"]: n for n in nodes}

    boards = [n for n in nodes if n["kind"] == "market_board"]
    listed = [n for n in nodes if n["kind"] == "listed_company" and n["attrs"].get("total_mcap_yi")]
    listed.sort(key=lambda n: -n["attrs"]["total_mcap_yi"])
    discs = [n for n in nodes if n["kind"] == "disclosure"]
    dirs = [n for n in nodes if n["kind"] == "bci_direction"]
    stages = [n for n in nodes if n["kind"] == "chain_stage"]

    caps = [n["attrs"]["total_mcap_yi"] for n in listed]
    total = sum(caps)
    median = (sorted(caps)[len(caps) // 2] if len(caps) % 2 else
              (sorted(caps)[len(caps) // 2 - 1] + sorted(caps)[len(caps) // 2]) / 2) if caps else 0
    top10 = sum(sorted(caps, reverse=True)[:10])

    # 公告 -> 公司 -> 板块, using the edges rather than re-deriving from names
    ann_to_sec = {e["src"]: e["dst"] for e in edges if e["rel"] == "discloses"}
    sec_to_board = {}
    for e in edges:
        if e["rel"] == "in_board":
            sec_to_board.setdefault(e["src"], []).append(e["dst"])

    L = []
    L.append("# 脑机接口 / 生物制造 产业图谱 · 商业价值快照")
    L.append("")
    L.append("数据来自本机实测的公开源（同花顺概念板块、腾讯行情、巨潮资讯、ClinicalTrials.gov、")
    L.append("PubMed、openFDA、Open Targets/ChEMBL 等），生成时间见文件时间戳。所有金额单位：人民币亿元。")
    L.append("")
    L.append("## 一、主题的市值口径（可复核）")
    L.append("")
    L.append("    板块            成分股   有报价   合计总市值    板块内市值中位数")
    for b in sorted(boards, key=lambda n: -n["attrs"].get("constituents", 0)):
        a = b["attrs"]
        L.append("    %-14s %5d %8d %12.1f %12.1f"
                 % (b["label"], a.get("constituents", 0), a.get("quoted", 0),
                    a.get("mcap_yi", 0.0), a.get("median_mcap_yi", 0.0)))
    L.append("")
    L.append("    去重上市公司 %d 家，合计总市值 %.1f 亿元，中位数 %.1f 亿元，"
             % (len(listed), total, median))
    L.append("    前 10 家合计 %.1f 亿元，占 %.1f%%（集中度）。" % (top10, 100.0 * top10 / total if total else 0))
    L.append("")
    L.append("## 二、按市值排序的构成公司（前 %d）" % top)
    L.append("")
    L.append("    排名  代码     名称                总市值     PE(TTM)   PB    所属板块")
    for i, n in enumerate(listed[:top], 1):
        a = n["attrs"]
        bl = sec_to_board.get(n["key"], [])
        bnames = ",".join(by_key[b]["label"] for b in bl if b in by_key)
        L.append("    %4d  %-8s %-18s %8.1f %9s %6s  %s"
                 % (i, a.get("code", ""), (n["label"] or "")[:18], a["total_mcap_yi"],
                    ("%.1f" % a["pe_ttm"]) if a.get("pe_ttm") is not None else "-",
                    ("%.2f" % a["pb"]) if a.get("pb") is not None else "-", bnames))
    L.append("")
    L.append("## 三、有据可查的涉主题披露（巨潮标题索引，%d 条）" % len(discs))
    L.append("")
    for d in discs[:30]:
        a = d["attrs"]
        L.append("    %-8s %-12s %s" % (a.get("sec_code", ""), (a.get("sec_name") or "")[:12],
                                        (d["label"] or "")[:70]))
    L.append("")
    L.append("## 四、技术路线与产业链环节（图谱骨架）")
    L.append("")
    for d in dirs:
        L.append("    %-22s 关键性能参数：%s" % (d["label"], (d["attrs"].get("tech_params") or "")[:60]))
    L.append("")
    L.append("    产业链环节：" + " / ".join(s["label"] for s in stages))
    L.append("")
    L.append("## 五、这份快照不能回答什么（写在结论里，不留给读者猜）")
    L.append("")
    L.append("    1. 板块成分是市场归类，不是技术判定。一家公司在脑机接口板块里，")
    L.append("       不等于它有脑机产品；图谱里这类边已标 basis=板块归属，非技术判定。")
    L.append("    2. 巨潮只索引沪深公告标题/正文，实测关键词命中量很小，披露条数是下限不是上限。")
    L.append("    3. 没有专利族与许可数据（需智慧芽/Incopat 付费），所以 FTO 与专利壁垒这一块是空的。")
    L.append("    4. 非上市公司（脑虎科技、阶梯医疗、博睿康等）没有公开市值，只能作为技术主体出现。")
    L.append("")
    txt = "\n".join(L) + "\n"
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(txt)
    return txt
