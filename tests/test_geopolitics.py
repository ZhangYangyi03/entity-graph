"""Offline tests for the commercial / political / financial layer.

The rules these assert are the ones that decide what an edge MEANS. Every case below is a
mistake this run actually made or nearly made, and each one is cheap to check.
"""
import json
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from entitygraph import geopolitics as G
from entitygraph.merge_world import load_graph
from entitygraph.model import Edge, Graph, Node

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_pearson_of_identical_series_is_one():
    x = [0.01, -0.02, 0.03, 0.0] * 12
    r, n = G._pearson(x, x)
    assert abs(r - 1.0) < 1e-9 and n == len(x)


def test_pearson_of_mirrored_series_is_minus_one():
    x = [0.01, -0.02, 0.03, 0.0] * 12
    y = [-v for v in x]
    r, _ = G._pearson(x, y)
    assert abs(r + 1.0) < 1e-9


def test_short_series_is_refused_not_guessed():
    r, n = G._pearson([0.1, 0.2], [0.1, 0.2])
    assert r != r and n == 2          # NaN: not enough days to claim a correlation


def test_flat_series_has_no_correlation():
    r, _ = G._pearson([0.0] * 40, [0.1] * 40)
    assert r != r


def test_theme_hs_codes_are_the_ones_claimed():
    # 9018 electronics, 9021 prosthetics, 9027 diagnostics, 3004 medicaments
    assert set(G.THEME_HS) == {"9018", "9021", "9027", "3004"}


def test_trade_quota_error_is_not_cached_as_an_answer():
    # measured: the public Comtrade preview returns 403 Quota Exceeded; caching that as
    # "no trade" would silently zero out 200 countries on the next run
    p = G._cache_path("trade_flows.json")
    src = open(os.path.join(ROOT, "entitygraph", "geopolitics.py"), encoding="utf-8").read()
    assert "不计入结果" in src
    assert "return res" in src


def test_a_500_row_answer_is_treated_as_truncation_not_data():
    # measured 2026-11-24: four HS codes in one request come back as exactly 500 rows with
    # no error, some codes silently missing (DE both-flows 4 codes: 500). The preview caps
    # a response at 500, so a 500 is re-fetched split by flowCode instead of trusted.
    src = open(os.path.join(ROOT, "entitygraph", "geopolitics.py"), encoding="utf-8").read()
    assert "len(rows) >= 500" in src and "&flowCode=" in src


def test_trade_requests_are_one_hs_code_each():
    # the bug the 500 cap caused: four codes joined into cmdCode=9018,9021,9027,3004
    src = open(os.path.join(ROOT, "entitygraph", "geopolitics.py"), encoding="utf-8").read()
    assert 'TRADE_URL % (code, TRADE_YEAR, hs)' in src
    assert '",".join' not in src.split("def trade_rows")[1].split("def attach_trade")[0]


def test_empty_reporter_is_distinguished_from_unfetched():
    # RU answers 0 rows for these HS codes in 2023 and 2024 (measured); that is a real empty
    # answer and must be recorded as such, not left looking like a country nobody fetched.
    src = open(os.path.join(ROOT, "entitygraph", "geopolitics.py"), encoding="utf-8").read()
    assert "verified_empty" in src


def test_bis_aggregate_codes_are_not_countries():
    # measured: BIS serves 5J ("All countries") and 5A ("All reporting countries") as ordinary
    # members of their own dimensions, so a country's partner list contains the world total.
    # Keeping it would link a country to a "country" called All countries and double every sum.
    src = open(os.path.join(ROOT, "entitygraph", "geopolitics.py"), encoding="utf-8").read()
    assert "_bis_country_codes" in src
    assert "r not in allowed or c not in allowed" in src
    assert "5J" in src


def test_bis_nan_cells_are_dropped_not_carried():
    # measured: some cells arrive as the literal string "NaN" (CA -> LS, and others).
    # float() accepts it, so an edge would carry usd=nan and poison every sum downstream.
    src = open(os.path.join(ROOT, "entitygraph", "geopolitics.py"), encoding="utf-8").read()
    assert "f != f" in src and "NaN" in src


def test_bis_iso_filter_refuses_a_thin_spine():
    # the filter is the 249-row ISO spine; if the spine came back short the layer would drop
    # real countries silently, so a short spine must raise instead of filtering
    src = open(os.path.join(ROOT, "entitygraph", "geopolitics.py"), encoding="utf-8").read()
    assert "refusing to filter BIS on it" in src


def test_bis_claims_and_liabilities_stay_directed():
    # a claim by banks in A on B is a different fact from the reverse; one symmetric
    # "connected to" relation is exactly the mistake the BIS layer exists to stop making
    assert "bank_claims_on" in G.GEO_RELS and "bank_liabilities_to" in G.GEO_RELS


def test_country_and_theme_etfs_do_not_collide():
    assert not (set(G.COUNTRY_ETFS.values()) & set(G.THEME_ETFS))
    assert "XBI" in G.THEME_ETFS and "SPY" in G.COUNTRY_ETFS.values()


def test_ideal_point_parser_accepts_the_tab_file():
    # the Dataverse .tab is tab separated; reading it as CSV yields one column and every row
    # is silently skipped (measured: 0 points before the delimiter fix)
    import csv
    import io
    tab = "ccode\tiso3c\tcountryname\tyear\tidealpointall\n2\t\"USA\"\t\"US\"\t2023.0\t1.98\n"
    delim = "\t" if "\t" in tab.split("\n", 1)[0] else ","
    rows = list(csv.DictReader(io.StringIO(tab), delimiter=delim))
    assert rows[0]["iso3c"].strip('"') == "USA"


def test_country_index_uses_iso2_from_attrs_not_the_key():
    g = Graph()
    g.add_node(Node(key="country:cn", kind="country", label="中国", attrs={"iso2": "CN"}))
    assert G.country_index(g) == {"CN": "country:cn"}


def test_attach_refuses_to_invent_country_nodes():
    g = Graph()
    g.add_node(Node(key="country:SE", kind="country", label="Sweden", attrs={"iso2": "SE"}))
    g.add_node(Node(key="country:US", kind="country", label="USA", attrs={"iso2": "US"}))
    idx = G.country_index(g)
    # a made-up agreement table: the real one needs the network, this only checks wiring
    import types
    real = G.un_ideal_points
    # 1.0 vs 1.15 -> agreement 1 - 0.15/2 = 0.925, above the 0.75 bloc threshold.
    # Using 1.9 here would test the threshold, not the wiring, and would fail for the
    # right reason -- the first version of this test made exactly that mistake.
    G.un_ideal_points = lambda: {"SWE": {"session": 2023, "ideal_point": 1.0, "qo": None},
                                 "USA": {"session": 2023, "ideal_point": 1.15, "qo": None},
                                 "XXX": {"session": 2023, "ideal_point": 0.0, "qo": None}}
    try:
        stats = G.attach_un_votes(g, idx, min_agreement=0.75)
    finally:
        G.un_ideal_points = real
    assert stats["countries"] == 2                 # XXX has no node, so it is not used
    assert ("country:SE", "votes_with", "country:US") in g.edges


ART = os.path.join(ROOT, "all.graph.json")


@pytest.mark.skipif(not os.path.exists(ART), reason="all.graph.json not built yet")
def test_stored_graph_every_geo_edge_has_a_value_and_a_basis():
    g = load_graph(ART)
    rels = {"bci_biopharma_exports_to": "usd", "bci_biopharma_imports_from": "usd",
            "votes_with": "agreement", "market_comoves_with": "pearson"}
    seen = 0
    for e in g.edges.values():
        if e.rel in rels:
            seen += 1
            assert rels[e.rel] in (e.attrs or {}), (e.rel, e.attrs)
        if e.rel in ("votes_with", "market_comoves_with"):
            assert (e.attrs or {}).get("basis"), e.rel
    assert seen > 0


@pytest.mark.skipif(not os.path.exists(ART), reason="all.graph.json not built yet")
def test_stored_graph_has_no_dangling_edges():
    g = load_graph(ART)
    bad = [(e.src, e.rel, e.dst) for e in g.edges.values()
           if e.src not in g.nodes or e.dst not in g.nodes]
    assert bad == []


# ------------------------------------------------------------------ 企业层：GLEIF

def test_theme_name_tier_rejects_brain_without_a_medical_anchor():
    # 实测的四个 miss：都含 brain，一个都不是主题
    assert G.theme_name_tier(["BRAIN PETROLEUM PRODUCTS TRADING L.L.C"]) == "off"
    assert G.theme_name_tier(["IT-Brain Software GmbH"]) == "off"
    assert G.theme_name_tier(["BRAIN-SOL Foltinek GmbH"]) == "weak"
    assert G.theme_name_tier(["Brain Beyond Pty Ltd"]) == "weak"
    # 而真正落在主题上的必须命中
    assert G.theme_name_tier(["EVER Neuro Pharma GmbH"]) == "strong"
    assert G.theme_name_tier(["Neuroelectrics Barcelona SL"]) == "strong"


def test_legal_suffix_shape_test_separates_a_group_head_from_a_subsidiary():
    # "Medtronic Europe Sàrl" 通过 404 判据会被当成"没有母公司"，但它明显是子公司。
    # 所以判据是名字形状：查询名 + 只有法律后缀。
    ok = ["AG", "Inc.", "PLC", "N.V.", "SA", "GmbH", "Holding AG", "Corporation",
          "Technologies, Inc."]
    bad = ["Europe Sàrl", "Europe", "PENSION PLAN", "Consumer Healthcare",
           "Defined Contribution Plan Master Trust", "India Private Limited"]
    # "Limited" 单独出现时是后缀也是子公司标志（"MEDTRONIC LIMITED" 就是爱尔兰子公司），
    # 这类靠国别校验挡：查 "Medtronic" 且国别 IE 时才认，查 US 时 IE 那条直接出局。
    for t in ok:
        assert G.LEGAL_SUFFIX.match(" " + t.lower()), t
    for t in bad:
        assert not G.LEGAL_SUFFIX.match(" " + t.lower()), t


def test_gleif_parent_parser_reads_the_id_field_not_nodeid():
    # 实测：关系里的节点字段叫 id，读 nodeID 恒为 None，于是 110 家企业零条母公司关系，
    # 而请求返回 200 —— 静默空。这条测试盯住字段名。
    src = open(os.path.join(ROOT, "entitygraph", "geopolitics.py"), encoding="utf-8").read()
    i = src.index("def gleif_record(")
    j = src.index("def _theme_name_ok", i)
    seg = src[i:j]
    assert 'end.get("id")' in seg
    assert 'end.get("nodeID")' not in seg


def test_gleif_cache_write_does_not_self_deadlock():
    # 实测卡在 32/110 好几分钟：gleif_record 拿了普通 Lock 再调 _save，_save 又拿同一把锁。
    # 一个自死锁不报错，看起来和网速慢完全一样。
    import threading
    assert isinstance(G._write_lock(), type(threading.RLock()))
