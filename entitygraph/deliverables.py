"""The 成果清单: for each of the 7 routes, what could actually be transferred and to whom.

The deliverable is a table a 项目组 can take into a 成果转化 meeting. It is built only
from what the graph already holds -- no new fetch, no new claim -- so every cell traces
back to a node with a URL.

Two things this module refuses to do:

    * It does not invent a 估值. The score is 证据密度, computed by a formula printed in
      the report itself, and it is labelled as density not money. A number that looks
      like a valuation and is not one is worse than no number.
    * It does not fill a route that has no evidence. A route with zero trials, zero
      registrations and zero owners is reported as 证据空白 with the reason, because a
      blank row in a transfer list is a promise nobody can keep.

    python -m entitygraph.cli deliverables --graph industry.graph.json \
        --out-md 可转让成果清单.md --out-json 成果清单.json
"""
from __future__ import annotations

import json
from collections import defaultdict

# What each evidence kind means in a 成果转化 conversation. The weight is the only
# judgement in this file, and it is printed in the report so it can be argued with.
EVIDENCE = {
    "device":        {"label": "器械注册/上市许可", "weight": 4,
                      "transfer": "合规锚点：已有主管当局受理或放行的产品，可作许可与合资标的"},
    "trial":         {"label": "在研临床试验",     "weight": 3,
                      "transfer": "在研项目：可按适应症/中心做合作或早期授权"},
    "company":       {"label": "企业技术主体",     "weight": 2,
                      "transfer": "产业化载体：可直接对接的承接方"},
    "institute":     {"label": "科研承担单位",     "weight": 2,
                      "transfer": "技术出让方：职务成果的所有权一侧"},
    "publication":   {"label": "论文/方法学",      "weight": 1,
                      "transfer": "方法学资产：可授权 know-how，也可作专利布局基础"},
    "dataset":       {"label": "数据集",           "weight": 1,
                      "transfer": "数据资产：可授权/可复现，是算法类成果的载体"},
    "disclosure":    {"label": "上市公司披露",     "weight": 1,
                      "transfer": "资本市场证据：说明已有主体押注，可用于对接买方"},
    "listed_company":{"label": "上市公司(板块)",   "weight": 1,
                      "transfer": "潜在买方/承接方（板块归类，非技术判定）"},
}
REL_TO_DIR = ("relevant_to", "works_on", "mentions_direction", "feeds", "enables", "regulates")


def _dir_slug(key: str) -> str:
    return key.split(":", 1)[1] if ":" in key else key


def build_deliverables(graph_path: str) -> dict:
    """Read a written graph and group its evidence under the 7 routes."""
    with open(graph_path, encoding="utf-8") as f:
        data = json.load(f)
    nodes = {n["key"]: n for n in data.get("nodes") or []}
    edges = data.get("edges") or []

    dirs = {k: v for k, v in nodes.items() if v["kind"] == "bci_direction"}

    # evidence attached to a route, one bucket per (route, kind)
    buckets: dict[str, dict[str, list[dict]]] = {k: defaultdict(list) for k in dirs}
    capital: dict[str, list[dict]] = defaultdict(list)
    for e in edges:
        # 资本关注度 rides alongside but never inside the score: a board is a market
        # label. It is reported as its own column, in its own words.
        if e["rel"] == "market_attention_on":
            if e["dst"] in dirs:
                capital[e["dst"]].append({"board": (e.get("attrs") or {}).get("board"),
                                          "constituents": (e.get("attrs") or {}).get("constituents"),
                                          "mcap_yi": (e.get("attrs") or {}).get("mcap_yi"),
                                          "source": e.get("source"), "url": e.get("url")})
            continue
        if e["rel"] not in REL_TO_DIR:
            continue
        src, dst = nodes.get(e["src"]), nodes.get(e["dst"])
        if src is None or dst is None:
            continue
        for a, b in ((src, dst), (dst, src)):
            if a["kind"] == "bci_direction" and b["kind"] in EVIDENCE:
                buckets[a["key"]][b["kind"]].append(
                    {"label": b["label"], "key": b["key"], "source": b["source"],
                     "url": b.get("url") or "", "attrs": b.get("attrs") or {},
                     "rel": e["rel"], "edge_attrs": e.get("attrs") or {}})

    routes = []
    for dk, dn in dirs.items():
        ev = buckets[dk]
        score = sum(EVIDENCE[k]["weight"] * len(v) for k, v in ev.items())
        kinds_present = sorted(k for k, v in ev.items() if v)
        empty = not kinds_present
        routes.append({
            "direction": _dir_slug(dk),
            "name": dn["label"],
            "tech_params": (dn.get("attrs") or {}).get("tech_params", ""),
            "score": score,
            "evidence_kinds": kinds_present,
            "counts": {k: len(v) for k, v in sorted(ev.items())},
            "transferables": [
                {"kind": k, "kind_label": EVIDENCE[k]["label"],
                 "transfer": EVIDENCE[k]["transfer"], "weight": EVIDENCE[k]["weight"],
                 "items": sorted(v, key=lambda x: x["label"])}
                for k, v in sorted(ev.items()) if v],
            "capital_attention": capital.get(dk, []),
            "empty": empty,
            "empty_reason": ("图谱里没有任何一条证据边落在该路线上：数据集/试验/论文/器械"
                             "四路抓取都没有命中该路线的关键词" if empty else ""),
        })
    routes.sort(key=lambda r: (-r["score"], r["name"]))
    total = sum(r["score"] for r in routes)
    for r in routes:
        r["share"] = round(100.0 * r["score"] / total, 1) if total else 0.0
    return {"routes": routes, "total_score": total,
            "weights": {k: {"label": v["label"], "weight": v["weight"],
                            "transfer": v["transfer"]} for k, v in EVIDENCE.items()},
            "source_graph": graph_path}


def render_markdown(bundle: dict, *, top_items: int = 6) -> str:
    L = []
    L.append("# 脑机接口 6+1 · 可转让成果清单")
    L.append("")
    L.append("来源：`%s`（由本机实测数据源生成的产业图谱）。每一条证据都能顺着 `source`/`url` 回到原始记录。"
             % bundle.get("source_graph", ""))
    L.append("")
    L.append("## 计分口径（先写清楚，免得被当成估值）")
    L.append("")
    L.append("下面每个路线的 score 是**证据密度**，不是金额、不是估值：")
    L.append("")
    L.append("    证据类型            权重   在成果转化里代表什么")
    for k, v in sorted(bundle["weights"].items(), key=lambda kv: -kv[1]["weight"]):
        L.append("    %-16s %4d   %s" % (v["label"], v["weight"], v["transfer"]))
    L.append("")
    L.append("一条路线得分 = Σ(该类证据条数 × 权重)。它衡量的是「这条路线手上已经有几张牌」，")
    L.append("不衡量牌值多少钱——估值需要收入、客户和专利族，那三样本仓库没有。")
    L.append("")
    L.append("## 总览")
    L.append("")
    L.append("    排名  路线                  得分  占比    已有证据类型")
    for i, r in enumerate(bundle["routes"], 1):
        L.append("    %4d  %-20s %5d %5.1f%%  %s"
                 % (i, r["name"], r["score"], r["share"],
                    ",".join(bundle["weights"][k]["label"] for k in r["evidence_kinds"]) or "—"))
    L.append("")
    for r in bundle["routes"]:
        L.append("## %s（得分 %d，占 %.1f%%）" % (r["name"], r["score"], r["share"]))
        L.append("")
        if r["tech_params"]:
            L.append("关键性能参数：%s" % r["tech_params"])
            L.append("")
        for ca in r.get("capital_attention") or []:
            L.append("资本关注度（不计入得分）：%s 板块 %s 只，合计总市值 %s 亿元 —— 板块归类，非技术判定。"
                     % (ca.get("board"), ca.get("constituents"),
                        "%.1f" % ca["mcap_yi"] if isinstance(ca.get("mcap_yi"), (int, float)) else "—"))
            L.append("")
        if r["empty"]:
            L.append("**证据空白。** %s" % r["empty_reason"])
            L.append("")
            L.append("这条路线在清单里留空，不编造条目。要补它，得先有该路线的专有数据源")
            L.append("（例如微针电极需要器件级的电极参数库，磁声成像需要招标/注册数据）。")
            L.append("")
            continue
        for t in r["transferables"]:
            L.append("### %s × %d — %s" % (t["kind_label"], len(t["items"]), t["transfer"]))
            L.append("")
            for it in t["items"][:top_items]:
                extra = ""
                a = it["attrs"] or {}
                conf = (it.get("edge_attrs") or {}).get("confidence")
                kw = (it.get("edge_attrs") or {}).get("keyword")
                if conf == "weak" and kw:
                    extra = " [弱命中：%s]" % kw
                if a.get("nct"):
                    extra = " [%s %s]" % (a.get("nct"), a.get("status") or "")
                elif a.get("bci6plus1"):
                    extra = " [6+1: %s]" % ",".join(a.get("bci6plus1") or [])
                elif a.get("fact"):
                    extra = " — %s" % a["fact"][:70]
                elif a.get("total_mcap_yi"):
                    extra = " [总市值 %.1f 亿]" % a["total_mcap_yi"]
                L.append("    - %s%s" % (it["label"][:96], extra))
                if it.get("url"):
                    L.append("      %s" % it["url"])
            if len(t["items"]) > top_items:
                L.append("    …… 另 %d 条见 JSON" % (len(t["items"]) - top_items))
            L.append("")
    L.append("## 这份清单回答不了的三件事")
    L.append("")
    L.append("    1. 没有专利族与许可数据，所以「能不能自由实施（FTO）」这一栏是空的。")
    L.append("    2. 没有非上市公司的估值与融资轮次，脑虎/阶梯/博睿康只能作为技术主体出现。")
    L.append("    3. 数据集条数来自 OpenNeuro/BNCI 的关键词命中，不是该路线可授权数据集的穷举。")
    L.append("")
    return "\n".join(L) + "\n"
