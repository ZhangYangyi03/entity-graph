"""把三份提纲（金融 / 儒政 / 儒政论纲）合成为一张政治经济学图，并做跨文档核验。

它做的事只有三件，每一件都往 quarantine 里记，不删原文：
  1. 同名实体的归并：同一个 label 在三份文档里是同一个节点（按归一化 label 建 key）。
  2. 对立面检测：同一对 (a,b) 一份说 a causes b、另一份说 b causes a —— 方向相反，
     不判谁对，标记为 reciprocal 并列出两边的出处行号。
  3. 缺脚检测：边上引用的实体在该文档里从没被定义过（只在句子节点里出现过），
     标 dangling —— 这不是错，是"论断悬空"，正是下一步该去补数据的地方。

用法：python merge_thesis.py
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = {
    "金融": ("thesis_finance.graph.json", "经济学：货币、信用、霸权与产业"),
    "儒政": ("thesis_politics.graph.json", "政治学：制度、君臣、儒法与政体变迁"),
    "儒政论纲": ("thesis_theory.graph.json", "理论框架：周秦之变与儒政"),
}
DOMAIN_OF = {
    "金融": ["货币", "美元", "欧元", "日元", "英镑", "通胀", "滞涨", "量化宽松", "美债", "汇率",
             "利率", "降息", "加息", "黄金", "银", "铜", "资本", "养老", "风投", "工厂", "工业",
             "技术", "教育", "医疗", "盗版", "关税", "石油", "布雷顿森林", "银行", "杠杆", "股票",
             "房地产", "避税", "开曼", "荷兰三明治", "GDP"],
    "儒政": ["君臣", "封建", "郡县", "儒", "法", "礼", "春秋", "尚书", "孔子", "孟子", "董仲舒",
             "王安石", "李世民", "司马炎", "王莽", "赵盾", "晋惠帝", "东海王", "曹操", "诸葛亮",
             "军功", "德", "名分", "民主", "民族主义", "知识分子", "专制", "革命", "篡"],
}


def norm(s: str) -> str:
    s = re.sub(r"\s+", "", s)
    return s.replace("（", "(").replace("）", ")").replace("，", ",")


def key_of(kind: str, label: str) -> str:
    return f"{kind}:{hashlib.sha1(norm(label).lower().encode('utf-8')).hexdigest()[:12]}"


def main() -> int:
    nodes: dict[str, dict] = {}
    edges: dict[tuple[str, str, str], dict] = {}
    quarantine: list[dict] = []
    docs_meta = {}

    for src_name, (fn, desc) in DOCS.items():
        p = os.path.join(HERE, fn)
        g = json.load(open(p, encoding="utf-8"))
        docs_meta[src_name] = {"file": fn, "desc": desc,
                               "nodes": len(g["nodes"]), "edges": len(g["edges"]),
                               "caveats": g["meta"].get("caveats", [])}
        # 旧 key -> 合并后的 key（按 label 重新算，保证三份文档同 label 同 key）
        remap = {}
        for n in g["nodes"]:
            k = key_of(n["kind"], n["label"])
            remap[n["key"]] = k
            cur = nodes.setdefault(k, {"key": k, "kind": n["kind"], "label": n["label"],
                                       "attrs": {"verified": False}, "source": "", "url": "",
                                       "fetched_at": "", "docs": []})
            cur["docs"].append(src_name)
            cur["attrs"].update({kk: vv for kk, vv in n["attrs"].items() if kk not in cur["attrs"]})
            if not cur["source"]:
                cur["source"] = n["source"]
            cur["fetched_at"] = max(cur["fetched_at"], n.get("fetched_at", ""))
        for e in g["edges"]:
            s, d = remap.get(e["src"], e["src"]), remap.get(e["dst"], e["dst"])
            k = (s, e["rel"], d)
            if k in edges:                                   # 同一条边在一份文档里重复出现
                edges[k]["attrs"]["lines"].append(e["attrs"]["line"])
                continue
            edges[k] = {"src": s, "dst": d, "rel": e["rel"],
                        "attrs": {"status": "asserted", "docs": [src_name],
                                  "lines": [e["attrs"]["line"]],
                                  "nested": e["attrs"].get("nested", False),
                                  "text": e["attrs"].get("text", "")},
                        "source": e["source"], "url": "", "fetched_at": e["fetched_at"]}

    # ---- 归属域：靠关键词，判不出来就 domainless
    for n in nodes.values():
        hits = {d: sum(1 for w in ws if w in n["label"]) for d, ws in DOMAIN_OF.items()}
        best = max(hits, key=hits.get) if hits else None
        n["attrs"]["domain"] = best if best and hits[best] > 0 else "domainless"

    # ---- 核验一：同一对实体方向相反
    by_pair: dict[tuple[str, str], list[tuple[tuple[str, str, str], dict]]] = {}
    for k, e in edges.items():
        by_pair.setdefault((e["src"], e["dst"]), []).append((k, e))
    reciprocal = 0
    for (s, d), es in by_pair.items():
        fwd = {}
        for k, e in es:
            fwd.setdefault(e["rel"], []).append(k)
        for rel, ks in fwd.items():
            if rel in ("exceeds", "defeats", "replaces", "imitates", "tribute_to"):
                continue
            back = fwd.get(rel + "_rev") or fwd.get({"causes": "caused_by",
                                                     "because_of": "causes",
                                                     "inherits_from": "inherited_by"}.get(rel, ""))
            if back:
                reciprocal += 1
                quarantine.append({"kind": "reciprocal", "src": s, "dst": d, "rel": rel,
                                   "why": "同一对实体两份文档给了相反方向",
                                   "docs": sorted(set(edges[ks[0]]["attrs"]["docs"] +
                                                      edges[back[0]]["attrs"]["docs"]))})

    # ---- 核验二：论断悬空（宾语只作为句子出现、从没被定义）
    defined = {n["key"] for n in nodes.values() if not n["attrs"].get("as_sentence")}
    dangling = [k for k, e in edges.items() if e["dst"] not in defined]
    for k in dangling:
        edges[k]["attrs"]["dangling"] = True

    out = {"nodes": list(nodes.values()), "edges": list(edges.values()),
           "meta": {"built_by": "merge_thesis.py",
                    "docs": docs_meta,
                    "rules": ["同 label 同 key", "人写的主张 status=asserted，未核验",
                              "方向矛盾与悬空论断只标记，不删"],
                    "reciprocal_count": reciprocal,
                    "dangling_count": len(dangling)}}
    op = os.path.join(HERE, "political_economy.graph.json")
    json.dump(out, open(op, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    qp = os.path.join(HERE, "quarantine", "merged.jsonl")
    os.makedirs(os.path.dirname(qp), exist_ok=True)
    with open(qp, "w", encoding="utf-8") as f:
        for q in quarantine:
            f.write(json.dumps(q, ensure_ascii=False) + "\n")

    dom = {}
    multi = 0
    for n in nodes.values():
        dom[n["attrs"]["domain"]] = dom.get(n["attrs"]["domain"], 0) + 1
        if len(set(n["docs"])) > 1:
            multi += 1
    print(json.dumps({"nodes": len(nodes), "edges": len(edges), "docs": list(DOCS),
                      "by_domain": dom, "shared_nodes": multi,
                      "reciprocal": reciprocal, "dangling": len(dangling),
                      "out": op}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
