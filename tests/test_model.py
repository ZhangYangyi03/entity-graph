import pytest
from entitygraph.model import Edge, Graph, Node


def test_edge_without_source_is_refused():
    g = Graph()
    with pytest.raises(ValueError):
        g.add_edge(Edge(src="a", dst="b", rel="subsidiary_of"))


def test_same_node_key_merges_and_records_both_sources():
    g = Graph()
    g.add_node(Node(key="lei:X", kind="company", label="Acme", attrs={"country": "US"},
                    source="GLEIF"))
    g.add_node(Node(key="lei:X", kind="company", label="ACME", attrs={"cik": "1"},
                    source="SEC EDGAR"))
    n = g.nodes["lei:X"]
    assert n.attrs["country"] == "US"
    assert n.attrs["cik"] == "1"
    assert n.attrs["_sources"] == ["GLEIF", "SEC EDGAR"]


def test_merge_does_not_overwrite_with_none():
    g = Graph()
    g.add_node(Node(key="k", kind="company", label="A", attrs={"city": "Oslo"}, source="s1"))
    g.add_node(Node(key="k", kind="company", label="", attrs={"city": None}, source="s2"))
    assert g.nodes["k"].attrs["city"] == "Oslo"


def test_neighbours_both_directions():
    g = Graph()
    for k in ("p", "c"):
        g.add_node(Node(key=k, kind="company", label=k, source="s"))
    g.add_edge(Edge(src="c", dst="p", rel="subsidiary_of", source="s"))
    assert len(g.neighbours("p", "subsidiary_of", "in")) == 1
    assert len(g.neighbours("c", "subsidiary_of", "out")) == 1
    assert len(g.neighbours("p", "subsidiary_of", "out")) == 0


def test_stats_and_json_roundtrip():
    g = Graph()
    g.add_node(Node(key="a", kind="company", label="A", source="s"))
    g.add_node(Node(key="b", kind="person", label="B", source="s"))
    g.add_edge(Edge(src="a", dst="b", rel="director_of", source="s"))
    st = g.stats()
    assert st["nodes"] == 2 and st["edges"] == 1
    assert st["by_kind"] == {"company": 1, "person": 1}
    import json
    blob = json.loads(g.to_json())
    assert len(blob["nodes"]) == 2 and len(blob["edges"]) == 1
