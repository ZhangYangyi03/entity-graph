"""crawl -> clean in one call: the joins that used to be manual."""
import json

from entitygraph import pipeline
from entitygraph.model import Graph, Node


def _fake_crawler(rows):
    class C:
        @staticmethod
        def crawl(urls, mode="fast", concurrency=20, timeout=8, with_text=False):
            return {"mode": mode, "count": len(urls), "elapsed": 0.01, "rps": 99.0,
                    "results": rows}
    return C


def test_crawl_into_graph_ingests_pages_and_reports_engine():
    # Text must be longer than the crawler's soft-block threshold, or a real page
    # gets refused as a bot wall -- the heuristic is exercised, not worked around.
    body = "hello world " * 40
    rows = [{"url": "https://a.example/x", "status": 200, "title": "X",
             "text": body, "links": ["https://b.example/"], "bytes": len(body)}]
    g, rep = pipeline.crawl_into_graph(["https://a.example/x"], crawler=_fake_crawler(rows))
    assert rep["engine"] == "autoforge.crawler:fast"
    assert rep["pages"] == 1 and rep["edges"] == 1
    kinds = sorted(n.kind for n in g.nodes.values())
    assert kinds == ["webpage", "website"]
    page = [n for n in g.nodes.values() if n.kind == "webpage"][0]
    assert page.attrs["text"].startswith("hello world")
    assert page.url == "https://a.example/x"          # provenance survives the join


def test_a_failed_fetch_is_a_caveat_not_a_missing_row():
    rows = [{"url": "https://dead.example/", "error": "timeout"}]
    g, rep = pipeline.crawl_into_graph(["https://dead.example/"], crawler=_fake_crawler(rows))
    assert rep["pages"] == 0 and len(rep["caveats"]) == 1
    assert rep["caveats"][0]["why"] == "fetch error"
    assert len(g.nodes) == 0


def test_bot_check_page_is_refused_rather_than_laundered_into_data():
    rows = [{"url": "https://wall.example/", "status": 200, "title": "Just a moment",
             "text": "verify you are human", "links": [], "bytes": 9}]
    g, rep = pipeline.crawl_into_graph(["https://wall.example/"], crawler=_fake_crawler(rows))
    assert rep["pages"] == 0
    assert rep["caveats"][0]["why"] == "blocked"


def test_empty_url_list_does_nothing_and_does_not_raise():
    g, rep = pipeline.crawl_into_graph([], crawler=_fake_crawler([]))
    assert rep["engine"] == "none" and rep["pages"] == 0 and len(g.nodes) == 0


def test_locator_report_parsing_keeps_only_well_formed_granules():
    text = ("5 tile(s) for location (40.96, 100.29) (model coordinate)\n"
            "  0.7978  lat  40.1943  lon   99.8112  "
            "S2A_MSIL2A_20220120T041101_N9999_R047_T47TNE_20221125T123732\n"
            "  0.5000  lat  41.0000  lon  102.0000  S2A_MSIL2A_BADSUFFIX\n"
            "  nearest first: the first row is the best match\n")
    hits = pipeline.parse_locator_report(text)
    assert len(hits) == 2
    assert hits[0]["granule_wellformed"] is True
    assert hits[1]["granule_wellformed"] is False
    assert hits[0]["lat"] == 40.1943


def test_geotag_writes_the_scene_and_says_what_it_is_not():
    g = Graph()
    g.add_node(Node("sat:1", "satellite", "S", {"sub_lat": 1.0, "sub_lon": 2.0}))
    g.add_node(Node("b:1", "bank", "B", {}))

    def locator(lat, lon):
        return [{"score": 0.73, "lat": lat, "lon": lon,
                 "product_id": "S2A_MSIL2A_20210605T003711_N9999_R059_T55MES_20230408T120140",
                 "granule_wellformed": True}]
    locator.model = "location->image (model-free)"
    locator.endpoint = "https://example.invalid"
    st = pipeline.geotag_locations(g, locator)
    assert st["geotagged"] == 1 and st["no_coordinate"] == 1
    a = g.nodes["sat:1"].attrs
    assert a["geo_scene"].startswith("S2A_MSIL2A_")
    assert "NOT a land-cover label" in a["geo_scene_method"]
    assert a["geo_scene_endpoint"] == "https://example.invalid"
    assert "geo_scene" not in g.nodes["b:1"].attrs


def test_a_dead_locator_is_counted_not_substituted():
    g = Graph()
    g.add_node(Node("b:1", "bank", "B", {"lat": 1.0, "lon": 2.0}))

    def bad(lat, lon):
        raise RuntimeError("service down")
    bad.model = bad.endpoint = ""
    st = pipeline.geotag_locations(g, bad)
    assert st["failed"] == 1 and st["geotagged"] == 0
    assert "service down" in g.nodes["b:1"].attrs["geo_scene_error"]
    assert "geo_scene" not in g.nodes["b:1"].attrs


def test_no_locator_means_no_attribute_and_a_zeroed_report():
    g = Graph()
    g.add_node(Node("b:1", "bank", "B", {"lat": 1.0, "lon": 2.0}))
    st = pipeline.geotag_locations(g, None)
    assert st["locator"] == "none" and st["geotagged"] == 0
    assert "geo_scene" not in g.nodes["b:1"].attrs


def test_geotag_limit_stops_and_reports_the_skipped():
    g = Graph()
    for i in range(4):
        g.add_node(Node(f"b:{i}", "bank", f"B{i}", {"lat": 1.0, "lon": 2.0}))
    calls = []

    def locator(lat, lon):
        calls.append((lat, lon))
        return [{"score": 1.0, "lat": lat, "lon": lon,
                 "product_id": "S2A_MSIL2A_20210605T003711_N9999_R059_T55MES_20230408T120140",
                 "granule_wellformed": True}]
    locator.model = locator.endpoint = ""
    st = pipeline.geotag_locations(g, locator, limit=2)
    assert len(calls) == 2 and st["geotagged"] == 2 and st["skipped_by_limit"] == 2


def test_run_clean_wires_the_whole_thing_and_writes_without_a_graph(tmp_path, monkeypatch):
    body = "some real page text " * 40
    rows = [{"url": "https://a.example/x", "status": 200, "title": "X", "text": body,
             "links": [], "bytes": len(body)}]
    out = tmp_path / "clean" / "g.json"
    ref = tmp_path / "clean" / "refused.json"
    rep = pipeline.run_clean(
        graphs=[], urls=["https://a.example/x"], tle="",
        geocode=False, geotag=False, crawler=_fake_crawler(rows),
        out=str(out), refused=str(ref))
    assert rep["nodes"] == 1 and rep["edges"] == 0
    assert rep["crawl"]["pages"] == 1
    blob = json.loads(out.read_text(encoding="utf-8"))
    assert blob["nodes"][0]["kind"] == "webpage"
    assert json.load(open(ref, encoding="utf-8"))["refusals"] == []
    assert rep["bytes_written"] > 0
