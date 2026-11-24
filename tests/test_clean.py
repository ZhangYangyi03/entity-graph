"""The cleaning rules, tested on what they must refuse as much as what they attach."""
import json
import sys

import pytest

from entitygraph import clean
from entitygraph.model import Edge, Graph, Node


def _g(tmp_path, name, nodes, edges=()):
    p = tmp_path / name
    p.write_text(json.dumps({"nodes": list(nodes), "edges": list(edges)}), encoding="utf-8")
    return str(p)


def test_merge_is_union_on_key_not_on_name(tmp_path):
    a = _g(tmp_path, "a.graph.json", [
        {"key": "lei:1", "kind": "company", "label": "Acme Inc", "attrs": {},
         "source": "s1", "url": "u", "fetched_at": ""}])
    b = _g(tmp_path, "b.graph.json", [
        {"key": "lei:2", "kind": "company", "label": "Acme Incorporated", "attrs": {},
         "source": "s2", "url": "u", "fetched_at": ""}])
    g, refused, per = clean.merge_graphs([a, b])
    assert len(g.nodes) == 2                    # two keys stay two nodes
    assert len(refused) == 1                    # and the collision is reported
    assert refused[0]["keys"] == ["lei:1", "lei:2"]
    assert per["a.graph.json"]["nodes"] == 1


def test_edge_without_a_citation_is_dropped_not_repaired(tmp_path):
    a = _g(tmp_path, "a.graph.json",
           [{"key": "k:1", "kind": "x", "label": "x", "attrs": {}, "source": "s",
             "url": "", "fetched_at": ""}],
           [{"src": "k:1", "dst": "k:2", "rel": "r", "attrs": {}, "source": "", "url": ""}])
    g, _, _ = clean.merge_graphs([a])
    assert len(g.edges) == 0


def test_symbol_only_labels_are_removed_with_their_edges():
    g = Graph()
    g.add_node(Node("a", "concept", "**"))
    g.add_node(Node("b", "concept", "real"))
    g.add_edge(Edge("a", "b", "r", source="s"))
    dead = clean.strip_symbol_labels(g)
    assert dead == ["a"]
    assert "b" in g.nodes and len(g.edges) == 0


def test_gmst_matches_the_known_j2000_value_and_advances():
    import datetime
    import math
    t0 = datetime.datetime(2000, 1, 1, 12, 0, 0, tzinfo=datetime.timezone.utc).timestamp()
    # The formula's constant is 280.46061837 deg at JD 2451545.0 (2000-01-01 12:00 UT).
    # GMST is 0 at Greenwich *midnight*, not at J2000 -- asserting 0 here was the bug.
    assert abs(math.degrees(clean._gmst_rad(t0)) - 280.46061837) < 1e-3
    assert 14.9 < math.degrees(clean._gmst_rad(t0 + 3600) - clean._gmst_rad(t0)) < 15.1
    assert abs(clean._gmst_rad(t0) - clean._gmst_rad(t0 + 86164.0905)) < 1e-4


def test_geodetic_latitude_is_not_the_geocentric_one():
    # A point on the equator: both agree, and altitude lands on the earth's radius.
    for t in (0.0, 1.7e9):
        lat, lon, alt = clean.eci_to_geodetic((7000.0, 0.0, 0.0), t)
        assert abs(lat) < 1e-6
        assert abs(alt - (7000.0 - clean.R_EARTH_KM)) < 1.0
    # 45 deg geocentric -> geodetic must come out slightly larger, never equal.
    import math
    lat, _, _ = clean.eci_to_geodetic((5000.0, 0.0, 5000.0), 0.0)
    assert lat > 45.0


def test_tle_records_reads_pairs_and_skips_headers():
    text = "HEADER JUNK\nISS (ZARYA)  \n1 25544U 98067A   26327.46150417  .00006075  00000+0  11604-3 0  9990\n2 25544  51.6319 231.8873 0007454  57.2278 302.9428 15.49562644596442\n"
    recs = list(clean.tle_records(text))
    assert len(recs) == 1
    name, l1, l2 = recs[0]
    assert name == "ISS (ZARYA)" and l1.startswith("1 25544") and l2.startswith("2 25544")
    gp = clean._gp_from_tle(name, l1, l2)
    assert gp["NORAD_CAT_ID"] == "25544"
    assert abs(gp["INCLINATION"] - 51.6319) < 1e-4
    assert gp["MEAN_MOTION"] > 15.0


def test_subpoint_attaches_and_records_its_method():
    g = Graph()
    g.add_node(Node("sat:25544", "satellite", "ISS (ZARYA)", {"object_id": "1998-067A"}))
    tle = ("ISS (ZARYA)  \n1 25544U 98067A   26327.46150417  .00006075  00000+0  11604-3 0  9990\n"
           "2 25544  51.6319 231.8873 0007454  57.2278 302.9428 15.49562644596442\n")
    st = clean.attach_subpoints(g, tle, when=1.7e9)
    assert st["matched_nodes"] == 1 and st["propagation_errors"] == 0
    a = g.nodes["sat:25544"].attrs
    assert -90 <= a["sub_lat"] <= 90 and -180 <= a["sub_lon"] <= 180
    assert 300 < a["sub_alt_km"] < 500          # a LEO station, not the moon
    assert "sgp4" in a["sub_method"]


def test_a_catalogue_name_with_no_tle_is_counted_not_invented():
    g = Graph()
    g.add_node(Node("sat:99999", "satellite", "SOMETHING ELSE"))
    st = clean.attach_subpoints(g, "OTHER\n1 25544U 98067A   26327.46150417  .00006075  00000+0  11604-3 0  9990\n2 25544  51.6319 231.8873 0007454  57.2278 302.9428 15.49562644596442\n")
    assert st["matched_nodes"] == 0 and st["tle_unmatched"] == 1
    assert "sub_lat" not in g.nodes["sat:99999"].attrs


def test_ambiguous_city_is_refused_and_says_why():
    g = Graph()
    g.add_node(Node("b:1", "bank", "One", {"city": "Salem", "stname": "Illinois"}))
    g.add_node(Node("b:2", "bank", "Two", {"city": "Nowhere"}))
    gz = {"salem": [{"lat": 38.6, "lon": -88.9, "country": "illinois", "pop": 1},
                    {"lat": 44.9, "lon": -123.0, "country": "oregon", "pop": 2}]}
    st = clean.geocode_cities(g, gz)
    assert st["by_region"] == 1                 # Illinois picks the Illinois Salem
    assert g.nodes["b:1"].attrs["lat"] == 38.6
    assert st["not_in_gazetteer"] == 1
    assert "sub_lat" not in g.nodes["b:2"].attrs


def test_two_places_one_region_still_refuses():
    g = Graph()
    g.add_node(Node("b:1", "bank", "One", {"city": "Salem", "stname": ""}))
    gz = {"salem": [{"lat": 1.0, "lon": 1.0, "country": "", "pop": 1},
                    {"lat": 2.0, "lon": 2.0, "country": "", "pop": 2}]}
    st = clean.geocode_cities(g, gz)
    assert st["refused_ambiguous"] == 1
    assert "lat" not in g.nodes["b:1"].attrs
    assert "refused" in g.nodes["b:1"].attrs["geo_match"]


def test_dump_graph_round_trips_through_json(tmp_path):
    g = Graph()
    g.add_node(Node("a", "x", "A", {"v": 1}, source="s", url="u"))
    g.add_node(Node("b", "x", "B"))
    g.add_edge(Edge("a", "b", "r", {"w": 2}, source="s", url="u"))
    out = str(tmp_path / "sub" / "g.json")      # the directory does not exist yet
    size = clean.dump_graph(g, out)
    blob = json.load(open(out, encoding="utf-8"))
    assert size > 0
    assert [n["key"] for n in blob["nodes"]] == ["a", "b"]
    assert blob["edges"][0]["src"] == "a" and blob["edges"][0]["attrs"]["w"] == 2
