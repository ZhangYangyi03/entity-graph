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
