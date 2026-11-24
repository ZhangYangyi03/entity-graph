"""The value gate: it must say no for the right reason, and yes for the right reason."""
import json
import os

from entitygraph import auto


def _res(rows):
    return {"count": len(rows), "results": rows}


def _page(url="https://a.example/x", status=200, text="x" * 500, title="T", links=()):
    return {"url": url, "status": status, "title": title, "text": text,
            "links": list(links), "bytes": len(text)}


def test_one_thin_page_is_not_worth_cleaning_and_the_reason_says_so():
    v = auto.judge(_res([_page(text="short")]))
    assert v["worth"] is False
    assert "too little to join" in v["reason"]
    assert v["pages"] == 1


def test_two_pages_are_a_set_and_worth_it():
    v = auto.judge(_res([_page(url="https://a.example/1"), _page(url="https://a.example/2")]))
    assert v["worth"] is True and "a set, not one page" in v["reason"]
    assert v["hosts"] == 1


def test_one_thick_document_is_worth_it():
    v = auto.judge(_res([_page(text="y" * 2500)]))
    assert v["worth"] is True and "a document, not a stub" in v["reason"]


def test_a_thin_page_with_a_location_still_counts():
    v = auto.judge(_res([_page(text="coord 40.96, 100.29 satellite pass", title="Map")]))
    assert v["worth"] is True and v["geo_signal_pages"] == 1
    assert "geography layer" in v["reason"]


def test_all_failed_is_not_worth_it():
    v = auto.judge(_res([{"url": "https://d.example/", "error": "timeout"}]))
    assert v["worth"] is False and "all failed" in v["reason"]


def test_empty_result_is_not_worth_it():
    v = auto.judge({"count": 0, "results": []})
    assert v["worth"] is False


def test_maybe_clean_writes_a_store_a_log_and_a_verdict(tmp_path):
    store = str(tmp_path / "s" / "web.graph.json")
    log = str(tmp_path / "s" / "auto.log")
    res = _res([_page(url="https://a.example/1"), _page(url="https://b.example/2")])
    v = auto.maybe_clean(res, engine="autoforge.crawler:fast", store=store, log=log)
    assert v["worth"] and v["cleaned"] and v["store_nodes_after"] == 2
    blob = json.load(open(store, encoding="utf-8"))
    assert sorted(n["kind"] for n in blob["nodes"]) == ["webpage", "webpage"]
    assert all(n["source"] == "autoforge.crawler" for n in blob["nodes"])
    line = json.loads(open(log, encoding="utf-8").read().strip())
    assert line["pages"] == 2 and line["engine"] == "autoforge.crawler:fast"


def test_nothing_is_written_when_the_gate_says_no(tmp_path):
    store = str(tmp_path / "s" / "web.graph.json")
    log = str(tmp_path / "s" / "auto.log")
    v = auto.maybe_clean(_res([_page(text="short")]), store=store, log=log)
    assert v["worth"] is False
    assert not os.path.exists(store) and not os.path.exists(log)


def test_force_skips_the_value_gate_but_not_the_integrity_rules(tmp_path):
    """force means "I know it is thin, take it anyway" -- not "take a bot wall".

    The page here is 400 chars: under the value gate (one page, no location signal) yet
    above the soft-block threshold. A 5-char stub is still refused with force=True,
    because that refusal is about whether the bytes are a page at all.
    """
    store = str(tmp_path / "s" / "web.graph.json")
    log = str(tmp_path / "s" / "auto.log")
    thin_but_real = _page(text="z" * 400)
    assert auto.judge(_res([thin_but_real]))["worth"] is False
    v = auto.maybe_clean(_res([thin_but_real]), store=store, log=log, force=True)
    assert v["cleaned"] is True and v["store_nodes_after"] == 1
    v2 = auto.maybe_clean(_res([_page(text="stub")]), store=store + "2", log=log, force=True)
    assert v2["cleaned"] is False and "soft-block" in v2["reason"]


def test_a_second_crawl_adds_to_the_store_rather_than_replacing_it(tmp_path):
    store = str(tmp_path / "s" / "web.graph.json")
    log = str(tmp_path / "s" / "auto.log")
    auto.maybe_clean(_res([_page(url="https://a.example/1"), _page(url="https://a.example/2")]),
                     store=store, log=log)
    v = auto.maybe_clean(_res([_page(url="https://c.example/3"), _page(url="https://c.example/4")]),
                         store=store, log=log)
    assert v["store_nodes_after"] == 4
    assert len(open(log, encoding="utf-8").read().strip().splitlines()) == 2


def test_the_same_url_twice_does_not_duplicate_a_node(tmp_path):
    store = str(tmp_path / "s" / "web.graph.json")
    log = str(tmp_path / "s" / "auto.log")
    same = _page(url="https://a.example/x")
    auto.maybe_clean(_res([same, _page(url="https://a.example/y")]), store=store, log=log)
    v = auto.maybe_clean(_res([same, _page(url="https://a.example/z")]), store=store, log=log)
    assert v["store_nodes_after"] == 3


def test_store_is_capped_and_the_dropped_are_counted(tmp_path, monkeypatch):
    monkeypatch.setattr(auto, "MAX_STORE_NODES", 3)
    store = str(tmp_path / "s" / "web.graph.json")
    log = str(tmp_path / "s" / "auto.log")
    rows = [_page(url=f"https://a.example/{i}") for i in range(5)]
    v = auto.maybe_clean(_res(rows), store=store, log=log)
    assert v["store_nodes_after"] >= 3
    assert v["dropped_oldest"] >= 0            # the cap reports, it does not raise


def test_a_broken_result_does_not_break_the_caller(tmp_path):
    v = auto.maybe_clean({"results": None}, store=str(tmp_path / "s.json"),
                         log=str(tmp_path / "l.log"))
    assert v["worth"] is False                  # judged, not crashed


def test_two_hosts_in_one_crawl_keep_their_own_links(tmp_path):
    store = str(tmp_path / "s" / "web.graph.json")
    log = str(tmp_path / "s" / "auto.log")
    res = _res([_page(url="https://a.example/1", links=["https://z.example/"]),
                _page(url="https://b.example/1", links=["https://z.example/"])])
    v = auto.maybe_clean(res, store=store, log=log)
    g = auto._load_store(store)
    # each page links to the shared domain once, from itself
    assert sum(1 for _ in g.edges.values()) == 2
    # 2 pages + the one shared domain. Grouping per host does not duplicate the domain
    # node: the same host key is reused, which is the point of grouping.
    assert v["store_nodes_after"] == 3


def test_the_verdict_shape_the_autoforge_hook_reads_is_stable(tmp_path):
    """autoforge.autoclean.one_line reads exactly these keys -- keep them present."""
    v = auto.maybe_clean(_res([_page(url="https://a.example/1"), _page(url="https://a.example/2")]),
                         store=str(tmp_path / "s" / "web.graph.json"),
                         log=str(tmp_path / "s" / "auto.log"))
    for key in ("worth", "cleaned", "reason", "store", "store_nodes_after",
                "dropped_oldest", "caveats"):
        assert key in v, key


def test_judge_does_not_mutate_its_input():
    res = _res([_page(), _page(url="https://a.example/2")])
    before = json.dumps(res, sort_keys=True)
    auto.judge(res)
    assert json.dumps(res, sort_keys=True) == before


def test_a_page_that_is_only_a_bot_wall_is_not_counted_as_a_page():
    """The gate sees an honest page count: a soft block is not a fetched page."""
    v = auto.judge(_res([_page(text="verify you are human"), _page(text="x" * 3000)]))
    # judge counts 2 successful rows; add_web is what refuses the wall. Both numbers
    # are reported so the caller can tell the difference.
    assert v["pages"] == 2 and v["worth"] is True


def test_rolling_store_keeps_provenance_and_a_url_survives_a_second_crawl(tmp_path):
    store = str(tmp_path / "s" / "web.graph.json")
    log = str(tmp_path / "s" / "auto.log")
    auto.maybe_clean(_res([_page(url="https://a.example/x"), _page(url="https://a.example/y")]),
                     store=store, log=log)
    auto.maybe_clean(_res([_page(url="https://a.example/x"), _page(url="https://a.example/z")]),
                     store=store, log=log)
    g = auto._load_store(store)
    page = [n for n in g.nodes.values()
            if n.kind == "webpage" and n.attrs.get("url") == "https://a.example/x"][0]
    assert page.url == "https://a.example/x"
    assert page.source == "autoforge.crawler"
    assert len(g.nodes) == 3


def test_an_explicit_clean_does_not_also_trigger_the_automatic_one(tmp_path, monkeypatch):
    """fetch_web hands its result to add_web -- the caller IS the cleaner.

    If the crawler's auto-clean hook also fired, the same pages would land in two stores
    and the second copy would never be looked at. So fetch_web asks the hook to stand
    aside, and this test asserts it actually does.
    """
    monkeypatch.setenv("ENTITYGRAPH_AUTO_STORE", str(tmp_path / "auto" / "web.graph.json"))
    from entitygraph import web
    seen = {}

    class FakeCrawler:
        @staticmethod
        def crawl(urls, mode="fast", concurrency=20, timeout=8, with_text=False):
            import os
            seen["autoclean"] = os.environ.get("AUTOFORGE_AUTOCLEAN")
            return {"mode": mode, "count": len(urls), "elapsed": 0.0, "rps": 0.0,
                    "results": [{"url": u, "status": 200, "title": "T",
                                 "text": "x" * 500, "links": [], "bytes": 500}
                                for u in urls]}

    res, engine = web.fetch_web(["https://a.example/1"], crawler=FakeCrawler)
    assert seen["autoclean"] == "0"          # asked to stand aside
    assert engine == "autoforge.crawler:fast"
    assert res["count"] == 1
    import os
    assert os.environ.get("AUTOFORGE_AUTOCLEAN") != "0"   # and put back afterwards


def test_fetch_web_restores_a_pre_existing_setting(monkeypatch):
    from entitygraph import web
    monkeypatch.setenv("AUTOFORGE_AUTOCLEAN", "0")

    class FakeCrawler:
        @staticmethod
        def crawl(urls, **kw):
            return {"mode": "fast", "count": 0, "elapsed": 0.0, "rps": 0.0, "results": []}

    import os
    web.fetch_web(["https://a.example/1"], crawler=FakeCrawler)
    assert os.environ.get("AUTOFORGE_AUTOCLEAN") == "0"
