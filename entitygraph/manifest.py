
"""manifest.py -- 这张图谱到底有几层、哪些进了总图、每层覆盖多少。

为什么要它：层数以前是我读目录数出来的（12 层、6 层进总图），下一次换个人读同一批
文件可能数出别的。这里把答案写成表，merge / gaps / CLI 都从这张表读，层数就不再依赖
谁去数一遍。

三个字段是有意分开的，因为它们回答的是三个不同的问题：
    merged   这层的边在不在 all.graph.json 里
    rels     这层往图上写的关系名（重复构建时 strip_geo 要按它摘掉旧边）
    coverage 覆盖率的分子/分母，分母是"已知总数"，不是"我们已经试过的数"——否则
             覆盖率永远接近 100%，等于没说。
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# name, 构建命令, 产物, 是否进 all.graph.json, 关系名, 数据源, 覆盖说明
LAYERS = [
    {"name": "航天轨道", "cmd": "orbit", "out": "satcat.graph.json", "merged": False,
     "rels": (), "sources": ("CelesTrak",),
     "coverage": "近地目标全量；与脑机接口不同域，故不进总图"},
    {"name": "货币银行", "cmd": "banking", "out": "banking.graph.json", "merged": False,
     "rels": (), "sources": ("BIS/央行公开表",),
     "coverage": "货币与产品全量；独立问题域，不进总图"},
    {"name": "中国行情", "cmd": "cnmarket", "out": "cn_market.graph.json", "merged": False,
     "rels": (), "sources": ("交易所公开行情",), "coverage": "A 股板块切片；独立问题域"},
    {"name": "全球国家", "cmd": "world", "out": "world.graph.json", "merged": True,
     "rels": ("has_company_slice", "co_author_country"),
     "sources": ("ISO-3166 249 国", "EPMC", "GLEIF"),
     "coverage": "249/249 国（ISO-3166 一个不落）"},
    {"name": "机构与试验", "cmd": "institutions", "out": "institutions.graph.json",
     "merged": True, "rels": ("trial_sponsored_by", "institution_located_in"),
     "sources": ("ROR", "ClinicalTrials.gov"), "coverage": "按主题词命中；无固定分母"},
    {"name": "多语言文献", "cmd": "multilingual", "out": "multilingual.graph.json",
     "merged": True, "rels": ("publishes", "in_language_slice", "sliced_by_language"),
     "sources": ("KCI(韩)", "J-STAGE/CiNii(日)", "CNKI 类(中)", "BASE(德/法)"),
     "coverage": "6/6 已接入语种（德法中日韩英）"},
    {"name": "产业地图", "cmd": "industry", "out": "industry.graph.json", "merged": True,
     "rels": (), "sources": ("主题词检索+人工锚词",), "coverage": "6+1 链环全占位"},
    {"name": "网页", "cmd": "web", "out": "web.graph.json", "merged": False, "rels": (),
     "sources": ("任意 URL",), "coverage": "按需爬，无分母"},
    {"name": "北欧", "cmd": "nordic", "out": "nordic.graph.json", "merged": True,
     "rels": ("catalogued_in", "registered_in"), "sources": ("国家图书馆/机构",),
     "coverage": "4 国（SE/DK/FI/SI）"},
    {"name": "地缘：外交", "cmd": "geo --layers diplomatic", "out": "all.graph.json",
     "merged": True, "rels": ("has_diplomatic_relations_with",), "sources": ("Wikidata P530",),
     "coverage": "有向建交边；无固定分母"},
    {"name": "地缘：联大投票", "cmd": "geo --layers unvotes", "out": "all.graph.json",
     "merged": True, "rels": ("votes_with",), "sources": ("UNGA ideal points (Harvard)",),
     "coverage": "同一年届、一致性≥阈值才落边"},
    {"name": "地缘：主题贸易", "cmd": "geo --layers trade", "out": "all.graph.json",
     "merged": True, "rels": ("bci_biopharma_exports_to", "bci_biopharma_imports_from"),
     "sources": ("UN Comtrade public preview",),
     "cache_key": "trade_flows.json", "denom_from": "comtrade_reporters",
     "coverage": "HS 9018/9021/9027/3004，按单个 HS 编码请求"},
    {"name": "地缘：股指同步", "cmd": "geo --layers market", "out": "all.graph.json",
     "merged": True, "rels": ("market_comoves_with", "moves_with_bci_biopharma_sector"),
     "sources": ("Yahoo Finance 国别/主题 ETF",),
     "coverage": "46/48 只 ETF；标明是共振不是资本流"},
    {"name": "地缘：企业注册", "cmd": "geo --layers gleif", "out": "all.graph.json",
     "merged": True, "rels": ("consolidated_by",), "sources": ("GLEIF LEI",),
     "coverage": "名字强命中主题才落节点"},
    {"name": "地缘：官方资本流", "cmd": "geo --layers bis", "out": "all.graph.json",
     "merged": True, "rels": ("bank_claims_on", "bank_liabilities_to"),
     "sources": ("BIS Locational Banking Statistics",),
     "cache_key": "bis_lbs.json", "denom_key": "reporters_available_claims",
     "have_key": ("claims",),
     "coverage": "跨境银行头寸；两条腿分开，非 FDI、非证券投资"},
]


def layer_table():
    return list(LAYERS)


def merged_layers():
    return [l for l in LAYERS if l["merged"]]


def all_geo_rels():
    """Every relation a layer writes onto the geo graph, for strip_geo / idempotence."""
    out = []
    for l in LAYERS:
        out.extend(l["rels"])
    return tuple(out)


def coverage():
    """Per-layer 分子/分母, read from the caches rather than from prose.

    A denominator is the KNOWN universe, never "the number we happened to try": 41/41 would
    look complete while 39 countries were never fetched.
    """
    import json
    rows = []
    for l in LAYERS:
        have = denom = None
        ck = l.get("cache_key")
        if ck:
            try:
                with open(os.path.join(ROOT, ".cache", ck), encoding="utf-8") as f:
                    cache = json.load(f)
            except (OSError, ValueError):
                cache = {}
            if l.get("have_key"):
                k1 = l["have_key"][0]
                latest = list(cache.values())[-1] if cache else {}
                have = len((latest.get("legs") or {}).get(k1) or {}) if isinstance(latest, dict) else 0
            else:
                have = sum(1 for v in cache.values()
                           if isinstance(v, dict) and (v.get("rows") or v.get("legs")))
            if l.get("denom_from") == "comtrade_reporters":
                denom = _comtrade_reporter_total()
            elif l.get("denom_key"):
                dk = l["denom_key"]
                for v in cache.values():
                    if isinstance(v, dict) and v.get(dk):
                        denom = max(denom or 0, v[dk])
        rows.append({"name": l["name"], "merged": l["merged"], "out": l["out"],
                     "have": have, "denom": denom, "coverage": l["coverage"]})
    return rows


def _comtrade_reporter_total() -> int | None:
    c = os.path.join(ROOT, ".cache", "comtrade_reference.json")
    try:
        import json
        with open(c, encoding="utf-8") as f:
            return len(json.load(f).get("reporters") or {})
    except (OSError, ValueError):
        return None


def report() -> str:
    merged = merged_layers()
    L = ["  层清单：共 %d 层，其中 %d 层进 all.graph.json"
         % (len(LAYERS), len(merged)),
         "  %-14s %-6s %-24s %s" % ("层", "进总图", "产物", "覆盖")]
    for r in coverage():
        cov = r["coverage"]
        if r["have"] is not None and r["denom"]:
            cov = "%d/%d —— %s" % (r["have"], r["denom"], cov)
        elif r["have"] is not None:
            cov = "%d 条已取 —— %s" % (r["have"], cov)
        L.append("  %-14s %-6s %-24s %s"
                 % (r["name"], "是" if r["merged"] else "否", r["out"], cov))
    return "\n".join(L)
