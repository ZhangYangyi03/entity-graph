"""The bridges, and the refusals that keep them honest."""
from __future__ import annotations

from entitygraph import bridge
from entitygraph.model import Edge, Graph, Node

SRC = "test"


def _g():
    g = Graph()
    g.add_node(Node("country:US", "country", "US", {"iso2": "US", "iso3": "USA"}, SRC))
    g.add_node(Node("country:usa", "country", "US", {"owner_code": "US"}, SRC))
    g.add_node(Node("country-iso3:USA", "country", "United States", {"iso3": "USA"}, SRC))
    g.add_node(Node("country:FR", "country", "France", {"iso2": "FR", "iso3": "FRA"}, SRC))
    g.add_node(Node("sat:1", "satellite", "ONE", {"owner_code": "US"}, SRC))
    g.add_node(Node("sat:2", "satellite", "TWO", {"owner_code": "SES"}, SRC))
    g.add_node(Node("cnstock:600000", "company", "浦发银行", {"code": "600000"}, SRC))
    g.add_edge(Edge("sat:1", "country:usa", "owned_by_state", {}, SRC))
    return g


def test_islands_counts_what_it_says():
    g = _g()
    m = bridge.islands(g)
    assert m["nodes"] == 7
    assert m["isolated"] == 5          # 7 nodes, one edge joins two of them
    assert m["largest"] == 2 and m["largest_share"] == round(100 * 2 / 7, 1)


def test_canonical_country_prefers_the_node_carrying_alpha3():
    g = _g()
    canon = bridge.canonical_countries(g)
    assert canon["USA"] == "country:US"
    assert canon["FRA"] == "country:FR"


def test_iso3_read_of_the_satcat_spelling():
    g = _g()
    assert bridge.iso3_of(g.nodes["country:usa"]) == "USA"
    assert bridge.iso3_of(g.nodes["country-iso3:USA"]) == "USA"
    # a bank-style node with no alpha-3 and no registry row states nothing
    assert bridge.iso3_of(Node("country:iceland", "country", "ICELAND", {}, SRC)) == ""


def test_country_identity_joins_on_alpha3_only():
    g = _g()
    st = bridge.bridge_country_identity(g)
    assert st["identity_edges"] == 2
    assert ("country:usa", "same_country_as", "country:US") in g.edges
    assert ("country-iso3:USA", "same_country_as", "country:US") in g.edges


def test_satellite_gets_its_state_but_an_operator_code_is_refused():
    g = _g()
    st = bridge.bridge_satellites_to_countries(g)
    assert st["linked"] == 1
    assert ("sat:1", "owned_by_state", "country:US") in g.edges
    # SES is an operator, not a state: no edge, and the refusal is counted not hidden
    assert st["unresolvable_satellites"] == 1
    assert st["unresolvable_codes"]["SES"] == 1
    assert not [e for e in g.edges.values() if e.src == "sat:2"]


def test_no_edge_without_a_citation():
    g = _g()
    bridge.connect(g)
    for e in g.edges.values():
        assert e.source, e.key


def test_listings_attach_to_a_country_that_exists():
    g = _g()
    assert bridge.bridge_listed_companies(g)["linked"] == 0      # country:CN absent
    g.add_node(Node("country:CN", "country", "中国", {"iso3": "CHN"}, SRC))
    assert bridge.bridge_listed_companies(g)["linked"] == 1
    assert ("cnstock:600000", "listed_in_country", "country:CN") in g.edges


def test_connect_moves_the_largest_component_and_says_both_numbers():
    g = _g()
    rep = bridge.connect(g)
    assert rep["before"]["largest_share"] < rep["after"]["largest_share"]
    assert rep["edges_added"] == rep["edges_after"] - rep["edges_before"]
    assert rep["after"]["components"] < rep["before"]["components"]


def test_connect_is_idempotent():
    g = _g()
    first = bridge.connect(g)
    second = bridge.connect(g)
    assert second["edges_added"] == 0
    assert second["after"]["components"] == first["after"]["components"]
