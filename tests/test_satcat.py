"""The satellite catalogue join.

The rule this file exists to hold: an edge from a satellite to a company is created only
when the catalogue or a declared mapping says so. Nothing here guesses ownership from a
name similarity, and "TBD" stays unowned instead of being handed to the biggest operator.
"""
import pytest

from entitygraph.satcat import (CatalogueRow, country_node, operator_of, parse_rows,
                                summary, to_graph)

RAW = [
    {"OBJECT_NAME": "STARLINK-1007", "OBJECT_ID": "2019-029F", "OBJECT_TYPE": "PAY",
     "OWNER": "US", "LAUNCH_DATE": "2019-05-24", "DECAY_DATE": "", "APOGEE": 550,
     "PERIGEE": 550, "PERIOD": 95.0},
    {"OBJECT_NAME": "ONEWEB-0001", "OBJECT_ID": "2019-010A", "OBJECT_TYPE": "PAY",
     "OWNER": "UK", "LAUNCH_DATE": "2019-02-27", "DECAY_DATE": "", "APOGEE": 1200,
     "PERIGEE": 1200, "PERIOD": 109.0},
    {"OBJECT_NAME": "SOMETHING UNNAMED", "OBJECT_ID": "2020-001A", "OBJECT_TYPE": "PAY",
     "OWNER": "TBD", "LAUNCH_DATE": "2020-01-01", "DECAY_DATE": "", "APOGEE": 500,
     "PERIGEE": 500, "PERIOD": 94.0},
    {"OBJECT_NAME": "OLD BIRD", "OBJECT_ID": "1990-001A", "OBJECT_TYPE": "PAY",
     "OWNER": "CIS", "LAUNCH_DATE": "1990-01-01", "DECAY_DATE": "2001-01-01",
     "APOGEE": 0, "PERIGEE": 0, "PERIOD": 0},
]


def test_decayed_objects_are_not_on_orbit():
    rows = parse_rows(RAW)
    s = summary(rows)
    assert s["on_orbit"] == 3


def test_an_unknown_owner_gets_no_country_node():
    """TBD must not be quietly attached to a state."""
    assert country_node("TBD") == ""
    assert country_node("") == ""


def test_state_codes_map_to_iso3():
    assert country_node("PRC") == "country:chn"
    assert country_node("CIS") == "country:rus"
    assert country_node("US") == "country:usa"


def test_series_name_identifies_the_operator():
    assert operator_of("STARLINK-1007", "US") == "SpaceX"
    assert operator_of("ONEWEB-0001", "UK") == "Eutelsat OneWeb"


def test_an_operator_code_names_a_company():
    assert operator_of("ANYTHING", "SES") == "SES S.A."


def test_no_operator_is_invented_for_an_unknown_object():
    assert operator_of("SOMETHING UNNAMED", "TBD") == ""


def test_graph_edges_never_reach_a_decayed_object():
    g = to_graph(parse_rows(RAW))
    keys = {n.key for n in g.nodes.values()} if isinstance(g.nodes, dict) else {n.key for n in g.nodes}
    assert "sat:1990-001A" not in keys
    assert "sat:2019-029F" in keys


def test_every_edge_cites_the_catalogue():
    g = to_graph(parse_rows(RAW))
    edges = g.edges.values() if isinstance(g.edges, dict) else g.edges
    for e in edges:
        assert e.source == "CelesTrak satcat"
        assert e.url.startswith("https://celestrak.org/")


def test_launch_date_becomes_valid_from_and_nothing_invents_a_date():
    g = to_graph(parse_rows(RAW))
    edges = [e for e in (g.edges.values() if isinstance(g.edges, dict) else g.edges)
             if e.rel == "operated_by"]
    assert edges
    assert all(e.attrs.get("valid_from") == "2019-05-24" for e in edges if e.src == "sat:2019-029F")
