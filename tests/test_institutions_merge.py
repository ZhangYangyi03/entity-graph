"""Offline tests for the institution layer and the merge.

No network here on purpose: these assert the rules that decide what a number MEANS
(which institution name is a match, which country a listed company belongs to, whether an
edge may point at a node that does not exist), and those rules must be checkable in a second
so a wrong rule is caught before it is applied to 249 countries.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from entitygraph.institutions import match_rule, _norm, COUNTRY_ISO2, institution_report
from entitygraph.merge_world import merge, merge_report, load_graph
from entitygraph.model import Edge, Graph, Node


# ---------------------------------------------------------------- ROR matching
def test_identical_name_matches():
    rule, score = match_rule("Karolinska Institutet", "Karolinska Institutet")
    assert score == 1.0 and "完全相同" in rule


def test_connector_and_word_order_still_matches():
    # measured real case: ClinicalTrials.gov writes "University of Aarhus", ROR says
    # "Aarhus University". Same institution, and refusing it would be a false negative.
    assert match_rule("University of Aarhus", "Aarhus University") is not None


def test_hospital_is_not_the_university():
    # measured real case. A Jaccard>=0.7 rule accepted this at 0.75; it must not.
    assert match_rule("National University Hospital, Singapore",
                      "National University of Singapore") is None


def test_different_company_is_not_a_match():
    assert match_rule("BrainQ Technologies", "ARIA Technologies") is None


def test_unrelated_entity_is_not_a_match():
    assert match_rule("Axoft, Inc.", "Instituto Nacional de Cancerología") is None


def test_legal_suffix_is_normalised_away():
    assert _norm("Axoft, Inc.") == _norm("Axoft")


def test_missing_input_returns_none():
    assert match_rule("", "Aarhus University") is None
    assert match_rule("Aarhus University", "") is None


# ---------------------------------------------------------------- country mapping
def test_trial_country_names_have_iso2():
    for name in ("United States", "China", "Taiwan", "Korea, Republic of"):
        assert name in COUNTRY_ISO2


def test_unmapped_country_is_absent_rather_than_guessed():
    # a country the table does not know must not be silently mapped to something else
    assert COUNTRY_ISO2.get("Atlantis") is None


# ---------------------------------------------------------------- merge
def _write(path, nodes, edges):
    g = Graph()
    for k, kind, attrs in nodes:
        g.add_node(Node(key=k, kind=kind, label=k, attrs=attrs, source="test"))
    for s, r, d, attrs in edges:
        g.add_edge(Edge(src=s, dst=d, rel=r, attrs=attrs, source="test"))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(g.to_dict(), f, ensure_ascii=False)
    return path


def test_merge_unions_by_key_and_keeps_both_sources(tmp_path):
    a = _write(tmp_path / "a.json",
               [("country:SE", "country", {"iso2": "SE"}),
                ("publication_slice:SE", "publication_slice", {"iso2": "SE", "hit_count": 68})],
               [("country:SE", "has_publication_slice", "publication_slice:SE", {})])
    b = _write(tmp_path / "b.json",
               [("country:SE", "country", {"iso2": "SE", "region": "Europe"}),
                ("trial:X", "trial", {}), ("country:SE", "country", {"iso2": "SE"})],
               [("trial:X", "trial_located_in", "country:SE", {})])
    g = merge([a, b])
    assert len([n for n in g.nodes.values() if n.key == "country:SE"]) == 1
    se = g.nodes["country:SE"]
    assert se.attrs.get("region") == "Europe"          # richer attribute set kept
    assert "test" in se.source                          # provenance not lost
    assert se.attrs.get("papers") == 68                 # from the publication slice
    assert se.attrs.get("trials") == 1                  # from the trial edge


def test_merge_refuses_to_invent_a_missing_input(tmp_path):
    real = _write(tmp_path / "real.json", [("country:SE", "country", {})], [])
    g = merge([real, str(tmp_path / "nope.json")])
    assert any("未找到" in (n.get("error") or "") for n in g._notes)
    assert "country:SE" in g.nodes


def test_listed_company_gets_its_country_from_its_market(tmp_path):
    p = _write(tmp_path / "ind.json",
               [("country:CN", "country", {"iso2": "CN"}),
                ("cnstock:300007", "listed_company", {"market": "A股"})], [])
    g = merge([p])
    assert ("cnstock:300007", "listed_in_country", "country:CN") in g.edges
    e = g.edges[("cnstock:300007", "listed_in_country", "country:CN")]
    assert "不是经营地" in e.attrs.get("basis", "")


def test_company_without_market_gets_no_country_edge(tmp_path):
    p = _write(tmp_path / "ind.json",
               [("country:CN", "country", {"iso2": "CN"}),
                ("cnstock:9", "listed_company", {})], [])
    g = merge([p])
    assert not [e for e in g.edges.values() if e.rel == "listed_in_country"]


# ---------------------------------------------------------------- the real artefact
ART = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "all.graph.json")


@pytest.mark.skipif(not os.path.exists(ART), reason="all.graph.json not built yet")
def test_merged_artifact_has_no_dangling_edges():
    g = load_graph(ART)
    dangling = [(e.src, e.dst) for e in g.edges.values()
                if e.src not in g.nodes or e.dst not in g.nodes]
    assert dangling == []


@pytest.mark.skipif(not os.path.exists(ART), reason="all.graph.json not built yet")
def test_merged_artifact_covers_the_world_and_the_three_sources():
    g = load_graph(ART)
    countries = [n for n in g.nodes.values() if n.kind == "country"]
    assert len(countries) >= 249
    triple = [n for n in countries
              if (n.attrs or {}).get("papers") and (n.attrs or {}).get("trials")
              and (n.attrs or {}).get("companies")]
    assert len(triple) >= 10
