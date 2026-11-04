"""China A-share market-data sources, one function per feed, every datum traceable.

Why this file exists
--------------------
The graph knew companies and satellites but nothing about how the market *moved*.
Wall-street grade data means five things we did not have: order-flow (tick and
order book), abnormal money movement (money flow, 龙虎榜, 大宗), short interest
(两融), the sell-side view (analyst forecasts), and the primary documents
(announcements, PDF).  ETFs were in the graph; who actually traded them was not.

Rules
-----
* every function returns (records, source_url) so the caller can stamp provenance;
* no key, no login, no cookie: these are all public endpoints;
* a feed that does not answer must be reported as a status, never as empty data --
  an empty list means "the market had nothing", a status means "I could not see".

Feed status as measured on this host (2026-11-04, see NOTES.md for the probe log):
    tick_realtime      OK       only *today*, last ~45min buffer (gtimg detail pages)
    tick_historical    BLOCKED  新浪 tick 已下线; 巨潮/东财 无历史逐笔免费档
    orderbook_realtime OK       only 5 levels, only while the market is open
    moneyflow          OK       东财 f62/f184 by stock
    lhb (龙虎榜)        OK       东财 RPT_DAILYBILLBOARD_DETAILSNEW
    blocktrade (大宗)   OK       东财 RPT_DATA_BLOCKTRADE
    margin (两融)       OK       东财 RPTA_WEB_RZRQ_GGMX
    announcement       OK       巨潮 POST (only via POST, GET answers 500)
    report_pdf         OK       巨潮 adjunctUrl
    forecast (盈利预测)  OK       东财 RPT_WEB_RESPREDICT
    research_report    OK       东财 reportapi
    esg                KEYED    华证 smartesg API 需 token; 东财无 ESG 报表
    satellite          OK       CelesTrak (already in satcat.py)
    satellite_imagery  KEYED    Planet/Sentinel 需账号; 本机 TLS 到部分站点失败
    credit_card        NO       商业数据源(卡组织/银联)不对个人开放
    hiring (招聘)       PARTIAL  BOSS直聘 城市接口 OK, 按公司岗位抓需登录态
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from .fetch import get, q

# ----------------------------------------------------------------- sources

EM_DC = "https://datacenter-web.eastmoney.com/api/data/v1/get"
EM_PUSH = "https://push2.eastmoney.com/api"
EM_REPORT = "https://reportapi.eastmoney.com/report/list"
EM_ANN = "https://np-anotice-stock.eastmoney.com/api/security/ann"
GTIMG = "https://stock.gtimg.cn/data/index.php"
CNINFO = "http://www.cninfo.com.cn/new/hisAnnouncement/query"

_STATUS: dict[str, str] = {}
_ORG_CACHE: dict[str, str] = {}


def status() -> dict[str, str]:
    """What each feed actually answered last time it was asked."""
    return dict(_STATUS)


def _secid(code: str) -> str:
    """600000 -> 1.600000 (SH); 000001/300750 -> 0. (SZ)."""
    c = str(code).strip()
    return ("1." if c[:1] in ("6", "5", "9") else "0.") + c


def _em_dc(report: str, **params: Any) -> tuple[list[dict], str]:
    qs = {"reportName": report, "columns": "ALL", "pageSize": 50, "pageNumber": 1}
    qs.update({k: v for k, v in params.items() if v is not None})
    url = EM_DC + "?" + "&".join(f"{k}={q(str(v))}" for k, v in qs.items())
    doc = get(url)
    if not doc.get("result"):
        _STATUS[report] = "EMPTY:" + str(doc.get("message"))[:60]
        return [], url
    _STATUS[report] = "OK"
    return doc["result"].get("data") or [], url



def get_text(url: str, timeout: float = 30.0) -> str:
    """Some feeds answer JSONP, not JSON; fetch.get insists on JSON, this does not."""
    return get(url, ttl=0, timeout=timeout, raw=True)


# ------------------------------------------------------------ order flow

def orderbook(code: str) -> tuple[dict, str]:
    """Five-level book, briefly. Live only: outside the session the levels read 0.

    Measured 2026-11-04: 东财 push2 f31..f40 全部缺字段，五档实际要 f19/f20(买一)
    与 f39/f40(卖一) 系列；即便如此 10:20 之后买一/卖一也给 0 —— 免费档不保证有盘口。
    因此这里返回的 bid/ask 是"当时能看到什么就是什么"，看不看得到由 status 说明。
    """
    url = (EM_PUSH + f"/qt/stock/get?secid={_secid(code)}"
           "&fields=f43,f44,f45,f46,f47,f48,f49,f50,f51,f52,f57,f58,f59,f60,f19,f39")
    d = (get(url) or {}).get("data") or {}
    def px(v): return None if v in (None, "-", 0) else round(v / 100.0, 2)
    rec = {
        "code": code, "name": d.get("f58"),
        "last": px(d.get("f43")), "high": px(d.get("f44")), "low": px(d.get("f45")),
        "open": px(d.get("f46")), "prev_close": px(d.get("f60")),
        "volume_lots": d.get("f47"), "amount": d.get("f48"),
        "bid1": {"price": px(d.get("f19")), "lots": None},
        "ask1": {"price": px(d.get("f39")), "lots": None},
    }
    live = bool(rec["bid1"]["price"] or rec["ask1"]["price"])
    _STATUS["orderbook"] = "OK book visible" if live else "OK price only (免费档此刻无五档)"
    return rec, url


def ticks_today(code: str, max_pages: int = 60, pause: float = 0.0) -> tuple[list[dict], str]:
    """Tick-by-tick for the CURRENT session only (gtimg paged buffer, ~45 min deep).

    The buffer slides: asking again a minute later returns a window that has moved.
    Capture it while the tape is hot; after the session it is gone for good.
    Historical tick for A-shares has no free source (新浪 tick 已下线).
    """
    sym = ("sh" if str(code)[:1] == "6" else "sz") + str(code)
    base = f"{GTIMG}?appn=detail&action=data&c={sym}&p="
    out: list[dict] = []
    for p in range(max_pages):
        try:
            raw = get_text(base + str(p))
        except Exception:
            break
        m = re.search(r"=\[(.*)\]", raw)
        if not m:
            break
        parts = m.group(1).split('"')
        items = [s for s in (parts[1].split("|") if len(parts) > 1 else []) if s]
        if not items:
            break
        for it in items:
            f = it.split("/")
            if len(f) < 7:
                continue
            out.append({"seq": int(f[0]), "time": f[1], "price": float(f[2]),
                        "chg": float(f[3]), "lots": int(f[4]), "amount": float(f[5]),
                        "side": {"B": "buy", "S": "sell"}.get(f[6], "neutral")})
        if pause:
            time.sleep(pause)
    _STATUS["ticks"] = f"OK n={len(out)}" if out else "EMPTY (session not started or buffer expired)"
    return out, base + "0"


def ticks_status(code: str) -> tuple[str, str]:
    """The tick time axis: tells you whether today's tape is being served at all."""
    sym = ("sh" if str(code)[:1] == "6" else "sz") + str(code)
    url = f"{GTIMG}?appn=detail&action=range&c={sym}&p=0"
    return get_text(url), url


# ------------------------------------------------------ abnormal activity

def moneyflow(limit: int = 50, market: str = "a") -> tuple[list[dict], str]:
    """Main-force net inflow per stock: f62 = 主力净额(元), f184 = 主力净占比(%)."""
    fs = {"a": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23", "gem": "m:0+t:80", "star": "m:1+t:23"}[market]
    url = (EM_PUSH + f"/qt/clist/get?pn=1&pz={limit}&po=1&np=1&fltt=2&invt=2"
           f"&fid=f62&fs={q(fs)}&fields=f12,f14,f2,f3,f62,f184,f66,f69,f72,f75")
    d = (get(url) or {}).get("data") or {}
    diff = d.get("diff") or {}
    items = diff.values() if isinstance(diff, dict) else diff
    _STATUS["moneyflow"] = "OK" if items else "EMPTY"
    rows = [{"code": r.get("f12"), "name": r.get("f14"), "close": r.get("f2"),
             "chg_pct": r.get("f3"), "main_net_in": r.get("f62"), "main_net_pct": r.get("f184"),
             "super_net_in": r.get("f66"), "large_net_in": r.get("f72")}
            for r in items]
    return rows, url


def lhb(limit: int = 50, date: str | None = None) -> tuple[list[dict], str]:
    """龙虎榜 daily detail: who moved the price on abnormal volume."""
    # TRADE_DATE is a string field; sorting it desc alone returns the latest session.
    # (filter=(TRADE_DATE>='2026-11-04') returns 0 rows -- 东财 treats it as an exact match.)
    rows, url = _em_dc("RPT_DAILYBILLBOARD_DETAILSNEW", pageSize=limit,
                       sortColumns="TRADE_DATE", sortTypes="-1")
    if date:
        rows = [r for r in rows if str(r.get("TRADE_DATE", ""))[:10] == date]
    for r in rows:
        r["_rel"] = "on_billboard"
    return rows, url


def blocktrade(limit: int = 50) -> tuple[list[dict], str]:
    """大宗交易: off-book block prints, the clearest institutional footprint."""
    rows, url = _em_dc("RPT_DATA_BLOCKTRADE", pageSize=limit,
                       sortColumns="TRADE_DATE", sortTypes="-1")
    for r in rows:
        r["_rel"] = "block_traded"
    return rows, url


def margin(limit: int = 50) -> tuple[list[dict], str]:
    """两融: financing balance + short (securities-lending) balance per stock."""
    rows, url = _em_dc("RPTA_WEB_RZRQ_GGMX", pageSize=limit,
                       sortColumns="DATE,RZRQYE", sortTypes="-1,-1")
    for r in rows:
        r["_rel"] = "margin_balance_of"
    return rows, url


# --------------------------------------------------------- sell-side view

def forecast(limit: int = 50) -> tuple[list[dict], str]:
    """分析师一致预期: rating counts plus EPS for the next three years."""
    rows, url = _em_dc("RPT_WEB_RESPREDICT", pageSize=limit,
                       sortColumns="RATING_ORG_NUM", sortTypes="-1")
    for r in rows:
        r["_rel"] = "forecast_for"
    return rows, url


def research(limit: int = 30, begin: str | None = None, end: str | None = None) -> tuple[list[dict], str]:
    """券商研报: title, org, rating, target EPS — the revision trail behind the consensus."""
    begin = begin or time.strftime("%Y-%m-%d", time.localtime(time.time() - 30 * 86400))
    end = end or time.strftime("%Y-%m-%d")
    url = (EM_REPORT + f"?pageSize={limit}&beginTime={begin}&endTime={end}&pageNo=1&qType=0")
    d = get(url)
    rows = d.get("data") or []
    _STATUS["research"] = "OK" if rows else "EMPTY"
    for r in rows:
        r["_rel"] = "covered_by"
    return rows, url


def announcements(code: str, limit: int = 20) -> tuple[list[dict], str]:
    """东财公告列表: article code, title, date."""
    suffix = "SH" if str(code)[:1] == "6" else "SZ"
    url = (EM_ANN + f"?sr=-1&page_size={limit}&page_index=1&ann_type=A"
           f"&client_source=web&stock_list={code}")
    d = (get(url) or {}).get("data") or {}
    rows = d.get("list") or []
    _STATUS["announcements"] = "OK" if rows else "EMPTY"
    for r in rows:
        r["_rel"] = "announced"
        r["_exchange"] = suffix
    return rows, url


def cninfo_orgid(code: str) -> str:
    """巨潮的 orgId 没有规律：浦发是 gssh0600000，宁德时代是 GD165627，平安是 9900002221。

    实测 2026-11-04：gssz0/gssh0 拼出来的 id 对 300750/002594/601318/688981 全部 0 条，
    必须走 topSearch/query 拿真实 orgId 再查。这里按代码查，缓存进 _ORG_CACHE。
    """
    if code in _ORG_CACHE:
        return _ORG_CACHE[code]
    import urllib.parse
    import urllib.request
    body = urllib.parse.urlencode({"keyWord": code, "maxSecNum": 10, "maxListNum": 10}).encode()
    req = urllib.request.Request(
        "http://www.cninfo.com.cn/new/information/topSearch/query", data=body,
        headers={"User-Agent": "Mozilla/5.0",
                 "Content-Type": "application/x-www-form-urlencoded"})
    org = ""
    try:
        for item in json.loads(urllib.request.urlopen(req, timeout=20).read()) or []:
            if str(item.get("code")) == str(code):
                org = item.get("orgId") or ""
                break
    except Exception:                                        # noqa: BLE001
        org = ""
    _ORG_CACHE[code] = org
    return org


def cninfo(code: str, limit: int = 20, category: str = "") -> tuple[list[dict], str]:
    """巨潮法定披露原文（含 PDF 链接）。POST only: GET answers 500."""
    import urllib.parse
    import urllib.request
    c = str(code)
    org = cninfo_orgid(c) or (("gssh0" if c[:1] == "6" else "gssz0") + c)
    plate = "sse" if c[:1] == "6" else "szse"
    body = urllib.parse.urlencode({
        "pageNum": 1, "pageSize": limit, "column": plate, "tabName": "fulltext",
        "stock": f"{c},{org}", "type": category, "trade": "", "seDate": "",
    }).encode()
    req = urllib.request.Request(CNINFO, data=body, headers={
        "User-Agent": "Mozilla/5.0", "Content-Type": "application/x-www-form-urlencoded"})
    try:
        d = json.loads(urllib.request.urlopen(req, timeout=25).read())
    except Exception as exc:                                   # noqa: BLE001
        _STATUS["cninfo"] = f"FAILED:{type(exc).__name__}"
        return [], CNINFO
    rows = d.get("announcements") or []
    _STATUS["cninfo"] = "OK" if rows else "EMPTY"
    for r in rows:
        r["_rel"] = "filed"
        r["orgId"] = org
        r["pdf_url"] = "http://static.cninfo.com.cn/" + r.get("adjunctUrl", "")
    return rows, CNINFO


# --------------------------------------------------------------- stated gaps

def esg(code: str) -> tuple[list[dict], str]:
    """华证 ESG. The API exists but answers 401 without a token: reported, not faked."""
    url = f"https://smartesg.chindices.com/api/esgratings/common/search?keyword={code}"
    try:
        raw = get(url, ttl=0)
        _STATUS["esg"] = "OK"
        return json.loads(raw).get("data") or [], url
    except Exception as exc:                                   # noqa: BLE001
        _STATUS["esg"] = f"KEYED:{type(exc).__name__}"
        return [], url


GAPS = {
    "tick_historical": "A 股免费源无历史逐笔；新浪 tick 已下线，东财/巨潮无此档。需付费（Level-2）或交易所授权。",
    "orderbook_depth": "免费只有买一~买五；Level-2 十档与委托队列需付费行情。",
    "esg": "华证 smartesg 需 token；东财 datacenter 无 ESG 报表（RPT_ESG_* 全部 404）。",
    "credit_card": "卡组织/银联消费数据不对个人开放，商业渠道亦需企业资质。",
    "hiring": "BOSS 直聘按公司抓岗位需登录态；城市/行业维度公开。",
    "satellite_imagery": "Planet/Sentinel 影像需账号；TLE 星历免费（satcat.py）。",
}


# ------------------------------------------------------- into the graph itself

def ingest(graph, codes: list[str] | None = None, *, ticks: bool = True,
           lhb_limit: int = 50, block_limit: int = 50, margin_limit: int = 50,
           forecast_limit: int = 50, ann_limit: int = 10) -> dict:
    """Load the market feeds into a Graph, every node and edge carrying source+url.

    Node keys are namespaced so they cannot collide with the LEI/CIK namespaces:
        cnstock:600000          a listed company
        seat:机构专用            a 龙虎榜 seat (institution / branch desk)
        analyst:东海证券          a broker publishing a forecast
    Edges: on_billboard / block_traded / margin_balance_of / forecast_for /
           published / announced / filed / bid_on / ask_on.
    """
    from .model import Edge, Node
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    added = {"nodes": 0, "edges": 0, "feeds": {}}

    def stock_key(code: str, name: str = "") -> str:
        key = f"cnstock:{code}"
        if key not in graph.nodes:
            graph.add_node(Node(key=key, kind="company", label=name or code,
                                attrs={"code": code}, source="cnmarket", url="", fetched_at=now))
            added["nodes"] += 1
        return key

    def edge(src: str, rel: str, dst: str, url: str, **attrs) -> None:
        e = Edge(src=src, dst=dst, rel=rel, attrs={k: v for k, v in attrs.items() if v is not None},
                 source="cnmarket", url=url, fetched_at=now)
        if e.key not in graph.edges:
            added["edges"] += 1
        graph.add_edge(e)

    feeds = added["feeds"]
    try:
        rows, url = moneyflow(200)
        feeds["moneyflow"] = f"{len(rows)} rows"
        for r in rows:
            if not r.get("code"):
                continue
            k = stock_key(r["code"], r.get("name") or "")
            graph.nodes[k].attrs.update({
                "main_net_in": r.get("main_net_in"), "main_net_pct": r.get("main_net_pct"),
                "moneyflow_as_of": now, "main_net_source": url})
    except Exception as exc:                                    # noqa: BLE001
        feeds["moneyflow"] = f"FAILED:{type(exc).__name__}"

    for name, fn, limit, rel in (("lhb", lambda n: lhb(n), lhb_limit, "on_billboard"),
                                 ("blocktrade", blocktrade, block_limit, "block_traded"),
                                 ("margin", margin, margin_limit, "margin_balance_of")):
        try:
            rows, url = fn(limit)
            feeds[name] = f"{len(rows)} rows"
            for r in rows:
                code = r.get("SECURITY_CODE") or r.get("SCODE") or str(r.get("SECUCODE") or "")[:6]
                if not code:
                    continue
                k = stock_key(code, r.get("SECURITY_NAME_ABBR") or r.get("SECNAME") or "")
                if name == "lhb":
                    # BUY_SEAT/SELL_SEAT are "12345_营业部名称" — the number is the desk code.
                    flat = {"TRADE_DATE": r.get("TRADE_DATE"), "net_buy": r.get("BILLBOARD_NET_AMT"),
                            "buy": r.get("BILLBOARD_BUY_AMT"), "sell": r.get("BILLBOARD_SELL_AMT"),
                            "explain": r.get("EXPLANATION") or r.get("EXPLAIN"),
                            "chg_pct": r.get("CHANGE_RATE")}
                    edge(k, "on_billboard", k, url, **flat)
                    for role, raw in (("buy", r.get("BUY_SEAT")), ("sell", r.get("SELL_SEAT"))):
                        for seat in str(raw or "").split(","):
                            seat = re.sub(r"^\d+_", "", seat.strip())
                            if not seat:
                                continue
                            sk = f"seat:{seat}"
                            if sk not in graph.nodes:
                                graph.add_node(Node(key=sk, kind="seat", label=seat,
                                                    source="cnmarket", url=url, fetched_at=now))
                                added["nodes"] += 1
                            edge(sk, f"seat_{role}_on", k, url, trade_date=r.get("TRADE_DATE"),
                                 amount=r.get("SUM_BUY_AMT") if role == "buy" else r.get("SUM_SELL_AMT"))
                elif name == "blocktrade":
                    edge(k, "block_traded", f"cnstock:{code}", url,
                         trade_date=r.get("TRADE_DATE"), price=r.get("DEAL_PRICE"),
                         volume=r.get("DEAL_VOLUME"), amount=r.get("DEAL_AMT"),
                         buyer=r.get("BUYER_NAME"), seller=r.get("SELLER_NAME"))
                    for role, side in (("buyer", r.get("BUYER_NAME")), ("seller", r.get("SELLER_NAME"))):
                        if side:
                            sk = f"seat:{side}"
                            if sk not in graph.nodes:
                                graph.add_node(Node(key=sk, kind="seat", label=side, source="cnmarket",
                                                    url=url, fetched_at=now))
                                added["nodes"] += 1
                            edge(sk, f"block_{role}_of", k, url, trade_date=r.get("TRADE_DATE"),
                                 amount=r.get("DEAL_AMT"))
                else:
                    graph.nodes[k].attrs.update({
                        "rz_balance": r.get("RZYE"), "rq_balance": r.get("RQYE"),
                        "rq_volume": r.get("RQYL"), "margin_as_of": r.get("DATE")})
                    edge(k, "margin_balance_of", k, url, date=r.get("DATE"), rz=r.get("RZYE"), rq=r.get("RQYE"))
        except Exception as exc:                                # noqa: BLE001
            import traceback
            feeds[name] = f"FAILED:{type(exc).__name__}:{str(exc)[:120]}"
            traceback.print_exc()

    try:
        rows, url = forecast(forecast_limit)
        feeds["forecast"] = f"{len(rows)} rows"
        for r in rows:
            code = r.get("SECURITY_CODE")
            if not code:
                continue
            k = stock_key(code, r.get("SECURITY_NAME_ABBR") or "")
            graph.nodes[k].attrs.update({
                "rating_orgs": r.get("RATING_ORG_NUM"), "eps_next": r.get("EPS2"),
                "eps_fy1": r.get("EPS1"), "forecast_as_of": now, "forecast_source": url})
            edge(k, "forecast_for", k, url, orgs=r.get("RATING_ORG_NUM"),
                 buy=r.get("RATING_BUY_NUM"), add=r.get("RATING_ADD_NUM"),
                 neutral=r.get("RATING_NEUTRAL_NUM"))
    except Exception as exc:                                    # noqa: BLE001
        feeds["forecast"] = f"FAILED:{type(exc).__name__}"

    try:
        rows, url = research(30)
        feeds["research"] = f"{len(rows)} rows"
        for r in rows:
            code = r.get("stockCode")
            if not code:
                continue
            k = stock_key(code, r.get("stockName") or "")
            org = r.get("orgSName") or r.get("orgName")
            if not org:
                continue
            ak = f"analyst:{org}"
            if ak not in graph.nodes:
                graph.add_node(Node(key=ak, kind="broker", label=org, source="cnmarket",
                                    url=url, fetched_at=now))
                added["nodes"] += 1
            edge(ak, "published", k, url, title=r.get("title"), date=r.get("publishDate"),
                 rating=r.get("emRatingName") or r.get("sRatingName"))
    except Exception as exc:                                    # noqa: BLE001
        feeds["research"] = f"FAILED:{type(exc).__name__}"

    if codes:
        for code in codes:
            try:
                rows, url = announcements(code, ann_limit)
                feeds[f"ann:{code}"] = f"{len(rows)} rows"
                k = stock_key(code)
                for r in rows:
                    ak = f"announcement:{r.get('art_code')}"
                    if ak not in graph.nodes:
                        graph.add_node(Node(key=ak, kind="announcement",
                                            label=(r.get("title") or "")[:120],
                                            attrs={"date": r.get("notice_date")},
                                            source="cnmarket", url=url, fetched_at=now))
                        added["nodes"] += 1
                    edge(ak, "announced", k, url, date=r.get("notice_date"))
            except Exception as exc:                            # noqa: BLE001
                feeds[f"ann:{code}"] = f"FAILED:{type(exc).__name__}"
            try:
                rows, url = cninfo(code, ann_limit)
                feeds[f"cninfo:{code}"] = f"{len(rows)} rows"
                k = stock_key(code)
                for r in rows:
                    ak = f"filing:{r.get('announcementId')}"
                    if ak not in graph.nodes:
                        graph.add_node(Node(key=ak, kind="filing",
                                            label=(r.get("announcementTitle") or "")[:120],
                                            attrs={"pdf_url": r.get("pdf_url"),
                                                   "date": r.get("announcementTime")},
                                            source="cninfo", url=url, fetched_at=now))
                        added["nodes"] += 1
                    edge(ak, "filed", k, url, pdf=r.get("pdf_url"))
            except Exception as exc:                            # noqa: BLE001
                feeds[f"cninfo:{code}"] = f"FAILED:{type(exc).__name__}"
            if ticks:
                try:
                    rows, url = ticks_today(code)
                    feeds[f"ticks:{code}"] = f"{len(rows)} ticks"
                    if rows:
                        k = stock_key(code)
                        buy = sum(t["lots"] for t in rows if t["side"] == "buy")
                        sell = sum(t["lots"] for t in rows if t["side"] == "sell")
                        graph.nodes[k].attrs.update({
                            "tape_window": f"{rows[0]['time']}~{rows[-1]['time']}",
                            "tape_ticks": len(rows), "tape_buy_lots": buy,
                            "tape_sell_lots": sell,
                            "tape_buy_sell_ratio": round(buy / sell, 3) if sell else None,
                            "tape_source": url})
                        edge(k, "tape_observed", k, url, window=graph.nodes[k].attrs["tape_window"])
                except Exception as exc:                        # noqa: BLE001
                    feeds[f"ticks:{code}"] = f"FAILED:{type(exc).__name__}"

    try:
        snap, s_url = snapshot(200)
        feeds["snapshot"] = f"{len(snap)} rows"
        for r in snap:
            if not r.get("code"):
                continue
            k = stock_key(r["code"], r.get("name") or "")
            graph.nodes[k].attrs.update({
                "mktcap_total": r.get("total_mktcap"), "mktcap_float": r.get("float_mktcap"),
                "industry": r.get("industry"), "pe_ttm": r.get("pe_ttm"),
                "pb": r.get("pb"), "snapshot_source": s_url})
    except Exception as exc:                                    # noqa: BLE001
        feeds["snapshot"] = f"FAILED:{type(exc).__name__}"

    added["feed_status"] = status()
    added["gaps"] = dict(GAPS)
    return added



def snapshot(limit: int = 200) -> tuple[list[dict], str]:
    """One screen: price, market cap, industry, PE/PB for the largest float-cap names."""
    fs = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"
    url = (EM_PUSH + f"/qt/clist/get?pn=1&pz={limit}&po=1&np=1&fltt=2&invt=2&fid=f21"
           f"&fs={q(fs)}&fields=f12,f14,f2,f3,f20,f21,f9,f23,f100,f62,f184")
    d = (get(url) or {}).get("data") or {}
    diff = d.get("diff") or {}
    items = diff.values() if isinstance(diff, dict) else diff
    _STATUS["snapshot"] = "OK" if items else "EMPTY"
    return [{"code": r.get("f12"), "name": r.get("f14"), "close": r.get("f2"),
             "chg_pct": r.get("f3"), "total_mktcap": r.get("f20"), "float_mktcap": r.get("f21"),
             "pe_ttm": r.get("f9"), "pb": r.get("f23"), "industry": r.get("f100")}
            for r in items], url
