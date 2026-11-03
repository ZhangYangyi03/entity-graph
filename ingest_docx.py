"""thesis 文档入库：把一份人写的因果提纲（docx/md/txt）拆成 entity-graph 的节点与边。

铁律，来自这个仓库 model.py 自身的规则，不是这里新发明的：
  1. 每条边必须 cite 一个来源。所以这里的 source 是
     "operator:<相对路径>#L<行号>"，url 为空，fetched_at 是文件 mtime。
  2. 人写的主张在核验之前不许装成事实。所以：
       - 节点 kind = "concept" / "actor"，attrs.verified 默认 False
       - 边 attrs.status 默认 "asserted"（操作者主张），只有带外部来源的才升 "verified"
       - 一眼可查的错误（年份、人名）不删，进 attrs.caveat，同时进 quarantine/
  3. 矛盾不静默丢弃：同一对主语-关系的相反极性、或同一论断的不同数字，
     全部落进 quarantine/*.jsonl 并写明为什么，等人判。

用法：
  python ingest_docx.py <文件> [--out thesis.graph.json] [--quarantine quarantine/]
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import zipfile
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------- 读文本

def read_any(path: str) -> list[str]:
    """返回按段落（行）切开的原文，空行保留为 ''，行号即索引+1。"""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".docx":
        with zipfile.ZipFile(path) as z:
            xml = z.read("word/document.xml").decode("utf-8", "replace")
        paras = re.findall(r"<w:p[ >].*?</w:p>", xml, re.S)
        out = []
        for q in paras:
            t = "".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", q, re.S))
            t = (t.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
                  .replace("&quot;", '"').replace("&apos;", "'"))
            out.append(t.strip())
        return out
    with open(path, encoding="utf-8", errors="replace") as f:
        return [l.strip() for l in f.read().splitlines()]


# ---------------------------------------------------------------- 归一与 id

def norm(s: str) -> str:
    s = re.sub(r"\s+", "", s)
    s = s.replace("（", "(").replace("）", ")").replace("，", ",")
    return s


def key_of(kind: str, label: str) -> str:
    h = hashlib.sha1(norm(label).lower().encode("utf-8")).hexdigest()[:12]
    return f"{kind}:{h}"


# 连接词：左边主语，右边宾语。按长度降序匹配，避免"靠"被"靠的是"截胡。
CONNECTIVES = [
    ("导致", "causes"), ("产生", "causes"), ("引发", "causes"), ("造成", "causes"),
    ("意味着", "means"), ("意思是", "means"), ("相当于", "means"), ("本质是", "is_a"),
    ("本质", "is_a"), ("就是", "is_a"), ("是", "is_a"),
    ("靠的是", "relies_on"), ("靠", "relies_on"), ("依赖", "relies_on"),
    ("继承", "inherits_from"), ("致敬", "tribute_to"), ("模仿", "imitates"),
    ("超过", "exceeds"), ("打败", "defeats"), ("取代", "replaces"),
    ("反对", "opposes"), ("否定", "opposes"), ("反", "opposes"),
    ("补充", "adds_to"), ("加了", "adds_to"), ("多了", "adds_to"),
    ("因为", "because_of"), ("所以", "therefore"), ("为了", "purpose_is"),
    ("变成", "becomes"), ("成为", "becomes"), ("孵化", "incubates"),
    ("控股", "controls"), ("投资", "invests_in"), ("借", "borrows"), ("卖", "sells"),
]

# 每一行开头若命中这些词，就当作"章节/论题"而不是一条边
SECTION_WORDS = ("货币", "教育科研", "医疗制度", "自由贸易", "工业产生", "工业过剩",
                 "古典自由", "重商主义", "美元霸权潮汐", "信息")

# 一眼可查的错误：写成人话，随行进 caveat，不删原文
KNOWN_ERRORS = [
    (re.compile(r"李嘉图边际主义|李嘉图3边际主义|李嘉图3"),
     "李嘉图是地租与比较优势，边际主义归杰文斯/门格尔/瓦尔拉斯；此行把两人对调了"),
    (re.compile(r"亚当斯密看了习学记言写出了国富论"),
     "叶适《习学记言》早于《国富论》约六百年，无传播路径证据；这是一条须标注为假说的类比"),
    (re.compile(r"马克思韦伯看了明夷待访录"),
     "同上：黄宗羲《明夷待访录》与韦伯《新教伦理》无文献链，属假说"),
    (re.compile(r"清朝?康熙.*马克思|马克思.*康熙"),
     "马克思生卒 1818-1883，与康熙（1654-1722）不同时代，年份不成立"),
]

# 名字级实体：凡是这些词出现在一句里，就为它建 actor 节点（不论是否参与关系）
ACTOR_HINTS = re.compile(
    r"亚当斯密|斯密|李嘉图|马尔萨斯|萨伊|马克思|凯恩斯|哈耶克|尤斯蒂|汉密尔顿|李斯特|"
    r"拉萨尔|丘吉尔|尼克松|里根|沃尔克|格林斯潘|伯南克|鲍威尔|索罗斯|安倍|中本聪|"
    r"牛顿|雍正|周小川|蒋介石|埃尔多安|斯莱特|克虏伯|王茂荫|邱濬|唐大陶|管仲|陆九渊|"
    r"马云|李泽楷|韦伯|黄宗羲|叶适|特朗普|奥巴马")


def _strip_paren(s: str) -> str:
    return re.sub(r"[（(][^）)]*[）)]", "", s)


def split_relation(line: str):
    """返回 (left, rel, right) 或 None。

    主语/宾语被"削到能站住的长度"：
      - 连接词两侧的括号补语删掉（"亚当斯密（1723）" -> "亚当斯密"）
      - 左侧：中文里名词短语很少超过 12 字，超了就取最后一个小句
      - 右侧：遇到第一个逗号/句号就停（那是另一个论断）
    削不出来就不出边，交给句子节点 —— 宁可少一条假边，不要一条长句子冒充边。
    """
    body = _strip_paren(line.lstrip("•-*·　 "))
    for word, rel in sorted(CONNECTIVES, key=lambda x: -len(x[0])):
        i = body.find(word)
        if i <= 0:
            continue
        left = body[:i].strip(" ：:，,。；;")
        right = body[i + len(word):].strip(" ：:，,。；;")
        if left:
            left = re.split(r"[，,。；;：:]", left)[-1].strip()
            left = re.sub(r"^(于是|所以|但是|但|而且|且|并且|其实|另外|还有一点|然后|因此|因为|前者|后者)", "", left)
            left = re.sub(r"(其实|根本|当然|大概|基本上|很多都|都|不)$", "", left)
        if right:
            right = re.split(r"[，,。；;]", right)[0].strip()
            right = re.sub(r"^(了|的|在|是)", "", right)
        if len(left) < 2 or re.fullmatch(r"(于是|所以|但是|它|这|那|其|此)", left):
            continue
        if not (2 <= len(left) <= 14) or not (2 <= len(right) <= 20):
            continue
        if re.match(r"^(就是|还有|于是|所以|但是|而且|并且|因为|因此)", left):
            continue
        if re.fullmatch(r"[\u4e00-\u9fa5]{0,2}", right):
            continue
        if word == "是" and re.search(r"[a-zA-Z0-9]", left):
            continue
        return left, rel, right
    return None


def classify(label: str) -> str:
    if ACTOR_HINTS.fullmatch(label) or ACTOR_HINTS.match(label):
        return "actor"
    if re.search(r"公司|集团|银行|政府|央行|议会|国会|党|国企|大学|基金|交易所|欧?盟|欧盟|苏联|美国|英国|德国|日本|中国|法国|荷兰|希腊|印度", label):
        return "actor"
    return "concept"


# ---------------------------------------------------------------- 主流程

def build(path: str, out: str, qdir: str) -> dict:
    try:
        rel_path = os.path.relpath(path, HERE).replace("\\", "/")
    except ValueError:            # 跨盘符（C: 的微信目录 -> D: 的仓库）
        rel_path = "inbox/" + os.path.basename(path)
    mtime = datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc).isoformat(timespec="seconds")
    lines = read_any(path)

    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    quarantine: list[dict] = []
    caveats: list[dict] = []

    def add_node(label: str, key: str | None = None) -> str:
        kind = classify(label)
        k = key or key_of(kind, label)
        if k not in nodes:
            nodes[k] = {"key": k, "kind": kind, "label": label,
                        "attrs": {"verified": False},
                        "source": f"operator:{rel_path}", "url": "", "fetched_at": mtime}
        return k

    for i, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line:
            continue
        if len(line) <= 8 and any(line.startswith(s) for s in SECTION_WORDS):
            k = add_node(line)
            nodes[k]["attrs"]["section"] = True
            continue

        for pat, why in KNOWN_ERRORS:
            if pat.search(normalize_for_match(line)):
                caveats.append({"line": i, "text": line, "caveat": why})

        for name in set(ACTOR_HINTS.findall(line)):
            add_node(name)

        got = split_relation(line)
        if not got:
            k = add_node(line if len(line) <= 40 else line[:40] + "…")
            nodes[k]["attrs"].setdefault("as_sentence", line)
            continue
        left, rel, right = got
        a, b = add_node(left), add_node(right)
        edges.append({"src": a, "dst": b, "rel": rel,
                      "attrs": {"status": "asserted", "line": i, "text": line},
                      "source": f"operator:{rel_path}#L{i}", "url": "", "fetched_at": mtime})

    # ---- 核验一：同一 (src, rel, dst) 出现相反极性（是 / 反对，本质是 / 否定）
    inverse = {"is_a": "opposes", "opposes": "is_a", "causes": "prevents"}
    by_pair: dict[tuple[str, str], list[dict]] = {}
    for e in edges:
        by_pair.setdefault((e["src"], e["dst"]), []).append(e)
    for (s, d), es in by_pair.items():
        rels = {e["rel"] for e in es}
        for r, inv in inverse.items():
            if r in rels and inv in rels:
                quarantine.append({"kind": "contradiction", "src": s, "dst": d,
                                   "rels": sorted(rels),
                                   "why": f"同一对实体同时被断言 {r} 与 {inv}",
                                   "lines": sorted(e["attrs"]["line"] for e in es),
                                   "texts": [e["attrs"]["text"] for e in es]})
                for e in es:
                    e["attrs"]["quarantined"] = True

    # ---- 核验二：同一行的数字自相矛盾（比如 15%+25%+15% 已经不剩）
    for i, line in enumerate(lines, 1):
        pcts = [int(x) for x in re.findall(r"(\d{1,3})\s*%", line)]
        if len(pcts) >= 2 and sum(pcts) > 100:
            quarantine.append({"kind": "bad_numbers", "line": i, "pct": pcts,
                               "why": f"同一句里百分比合计 {sum(pcts)}% > 100%",
                               "text": line})

    graph = {"nodes": list(nodes.values()), "edges": edges,
             "meta": {"input": rel_path, "sha1": hashlib.sha1(open(path, "rb").read()).hexdigest(),
                      "fetched_at": mtime,
                      "rules": ["edge 必须有 source", "人写的主张默认 status=asserted",
                                "错误与矛盾进 quarantine，不静默丢弃"],
                      "caveats": caveats}}
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(graph, f, ensure_ascii=False, indent=1)
    os.makedirs(qdir, exist_ok=True)
    qp = os.path.join(qdir, os.path.splitext(os.path.basename(path))[0] + ".jsonl")
    with open(qp, "w", encoding="utf-8") as f:
        for q in quarantine:
            f.write(json.dumps(q, ensure_ascii=False) + "\n")

    return {"nodes": len(nodes), "edges": len(edges), "caveats": len(caveats),
            "quarantine": len(quarantine), "out": out, "quarantine_file": qp,
            "by_rel": _count(e["rel"] for e in edges),
            "by_kind": _count(n["kind"] for n in nodes.values())}


def normalize_for_match(s: str) -> str:
    return s.replace(" ", "")


def _count(it):
    d: dict[str, int] = {}
    for x in it:
        d[x] = d.get(x, 0) + 1
    return dict(sorted(d.items(), key=lambda kv: -kv[1]))


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(__doc__)
        return 2
    path = argv[0]
    out = os.path.join(HERE, "thesis.graph.json")
    qdir = os.path.join(HERE, "quarantine")
    for i, a in enumerate(argv):
        if a == "--out":
            out = argv[i + 1]
        if a == "--quarantine":
            qdir = argv[i + 1]
    st = build(path, out, qdir)
    print(json.dumps(st, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
