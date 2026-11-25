"""Answers that a person would accept: folded identity, stated denominator, no invention."""
from __future__ import annotations

from entitygraph import bridge, query
from entitygraph.model import Edge, Graph, Node

SRC = "test"


def _g():
    g = Graph()
    g.add_node(Node("country:US", "country", "US", {"iso2": "US", "iso3": "USA"}, SRC))
    g.add_node(Node("country:usa", "country", "US", {"owner_code": "US"}, SRC))
    g.add_node(Node("country:FR", "country", "France", {"iso2": "FR", "iso3": "FRA"}, SRC))
    g.add_node(Node("sat:1", "satellite", "ONE", {"owner_code": "US"}, SRC))
    g.add_node(Node("sat:2", "satellite", "TWO", {"owner_code": "US"}, SRC))
    g.add_node(Node("sat:3", "satellite", "THREE", {"owner_code": "SES"}, SRC))
    g.add_node(Node("company:spacex", "company", "SpaceX", {}, SRC))
    g.add_node(Node("cnstock:600000", "company", "浦发银行", {"code": "600000"}, SRC))
    g.add_node(Node("country:CN", "country", "中国", {"iso3": "CHN"}, SRC))
    g.add_edge(Edge("sat:1", "country:usa", "owned_by_state", {}, SRC))
    g.add_edge(Edge("sat:2", "country:usa", "owned_by_state", {}, SRC))
    g.add_edge(Edge("sat:1", "company:spacex", "operated_by", {}, SRC))
    g.add_edge(Edge("country:usa", "country:US", "same_country_as", {}, SRC))
    return g


def test_fold_map_follows_same_country_as():
    g = _g()
    fold = query.fold_map(g)
    assert fold["country:usa"] == "country:US"
    assert fold["country:US"] == "country:US"
    assert fold["country:FR"] == "country:FR"


def test_satellites_by_country_counts_each_fact_once():
    """The regression this repo needed: US must be 2, not 2 + 2 for the two spellings."""
    g = _g()
    rep = query.satellites_by_country(g)
    rows = dict(rep["rows"])
    assert rows["US"] == 2
    assert rep["population"] == 3
    assert rep["classified"] == 2
    assert rep["unclassified"] == 1          # SES is an operator, not a state


def test_every_answer_states_its_denominator():
    for rep in (query.satellites_by_country(_g()), query.satellites_by_operator(_g())):
        assert rep["population"] == rep["classified"] + rep["unclassified"]


def test_resolve_prefers_exact_then_substring():
    g = _g()
    assert query.resolve(g, "country:US") == ["country:US"]
    assert "company:spacex" in query.resolve(g, "SpaceX")
    assert query.resolve(g, "") == []


def test_path_reports_disconnected_rather_than_guessing():
    g = _g()
    g.add_edge(Edge("cnstock:600000", "country:CN", "listed_in_country", {}, SRC))
    assert query.path(g, "中国", "SpaceX")["connected"] is False
    # an endpoint that does not exist is "not found", which is not "not connected"
    assert "error" in query.path(g, "CN", "SpaceX")
    rep = query.path(g, "浦发银行", "中国")
    assert rep["connected"] is True and rep["hops"] == 1


def test_board_members_states_figures_and_missing_ones():
    g = _g()
    g.add_node(Node("board:ths:1", "market_board", "脑机接口", {}, SRC))
    g.add_node(Node("cnstock:300007", "company", "汉威科技 (300007)",
                    {"code": "300007", "total_mcap_yi": 118.21, "pe_ttm": 73.69}, SRC))
    g.add_edge(Edge("cnstock:300007", "board:ths:1", "in_board", {}, SRC))
    rep = query.board_members(g, "脑机接口")
    assert rep["population"] == 1 and rep["with_figures"] == 1
    assert rep["total_mcap_yi"] == 118.2


def test_issuer_evidence_reads_both_edge_directions():
    """discloses (cninfo feed) and announced (巨潮 index) are the same fact, mirrored."""
    g = _g()
    g.add_node(Node("board:ths:1", "market_board", "脑机接口", {}, SRC))
    g.add_node(Node("cnstock:300007", "company", "汉威科技 (300007)",
                    {"code": "300007", "total_mcap_yi": 118.21}, SRC))
    g.add_node(Node("announcement:a1", "disclosure", "某脑机接口项目公告", {"sec_code": "300007"}, SRC))
    g.add_edge(Edge("cnstock:300007", "board:ths:1", "in_board", {}, SRC))
    g.add_edge(Edge("cnstock:300007", "announcement:a1", "discloses", {}, SRC))
    rep = query.issuer_evidence(g, "脑机接口")
    assert rep["with_announcements"] == 1
    assert rep["rows"][0]["announcement_count"] == 1
    # and the mirrored direction counts the same, not double
    g2 = _g()
    g2.add_node(Node("board:ths:1", "market_board", "脑机接口", {}, SRC))
    g2.add_node(Node("cnstock:300007", "company", "汉威科技", {"code": "300007"}, SRC))
    g2.add_node(Node("announcement:a1", "disclosure", "公告", {"sec_code": "300007"}, SRC))
    g2.add_edge(Edge("cnstock:300007", "board:ths:1", "in_board", {}, SRC))
    g2.add_edge(Edge("announcement:a1", "cnstock:300007", "announced", {}, SRC))
    assert query.issuer_evidence(g2, "脑机接口")["rows"][0]["announcement_count"] == 1


def test_issuer_identity_joins_on_the_exchange_code_not_the_namespace():
    """The first run of this bridge missed every disclosure: its keys are not cnstock:."""
    g = _g()
    g.add_node(Node("announcement:cninfo:600000:1.PDF", "disclosure", "公告",
                    {"sec_code": "600000"}, SRC))
    st = bridge.bridge_listing_identities(g)
    assert st["issuer_identity_edges"] == 1
    assert ("announcement:cninfo:600000:1.PDF", "same_issuer_as", "cnstock:600000") in g.edges
