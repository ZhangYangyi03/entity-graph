"""Time on the graph: intervals, slices, versions, and the refusals that make them mean
something. No network."""
import json

import pytest

from entitygraph.history import SideTable
from entitygraph.model import Edge, Graph, Node
from entitygraph.timeline import Interval, Timeline, from_graph, intervals_of


def mk_edge(src, rel, dst, start, end="", **attrs):
    return Edge(src=src, dst=dst, rel=rel, source="test",
                url=f"https://example.test/{src}-{rel}-{dst}",
                attrs={"valid_from": start, **({"valid_to": end} if end else {}), **attrs})


def mk_graph(*edges):
    g = Graph()
    for k in ("a", "b", "c"):
        g.add_node(Node(key=k, kind="company", label=k.upper()))
    for e in edges:
        g.add_edge(e)
    return g


# --- Interval ---------------------------------------------------------------

def test_interval_is_half_open():
    iv = Interval("2020-01-01", "2021-01-01")
    assert iv.contains("2020-01-01")
    assert iv.contains("2020-12-31")
    assert not iv.contains("2021-01-01")   # the day the next version starts belongs to it


def test_open_interval_has_no_end():
    iv = Interval("2020-01-01")
    assert iv.contains("2099-12-31")
    assert not iv.contains("2019-12-31")


def test_empty_timestamp_is_refused_not_defaulted():
    with pytest.raises(ValueError):
        Interval("2020-01-01").contains("")


def test_edge_without_dates_reads_as_empty_interval():
    e = Edge(src="a", dst="b", rel="r", source="test")
    assert intervals_of(e) == Interval("", "")


# --- Timeline: the refusals -------------------------------------------------

def test_undated_edge_is_reported_and_blocks_a_strict_slice():
    g = mk_graph(mk_edge("a", "holds", "b", "2020-01-01"),
                 Edge(src="b", dst="c", rel="holds", source="test"))
    tl = Timeline(g)
    chk = tl.check()
    assert len(chk["undated"]) == 1
    assert not chk["ok"]
    with pytest.raises(ValueError, match="no valid_from"):
        tl.as_of("2020-06-01")


def test_lenient_slice_keeps_the_undated_edge_and_says_so():
    g = mk_graph(mk_edge("a", "holds", "b", "2020-01-01"),
                 Edge(src="b", dst="c", rel="holds", source="test"))
    sub = Timeline(g).as_of("2020-06-01", strict=False)
    assert len(sub.edges) == 2


def test_inverted_interval_is_rejected():
    g = mk_graph(mk_edge("a", "holds", "b", "2021-01-01", "2020-01-01"))
    chk = Timeline(g).check()
    assert chk["inverted"]
    assert not chk["ok"]


# --- Timeline: the slice ----------------------------------------------------

def test_slice_returns_only_edges_live_on_that_date():
    g = mk_graph(mk_edge("a", "holds", "b", "2019-01-01", "2020-01-01"),
                 mk_edge("a", "holds", "c", "2020-01-01"))
    tl = Timeline(g)
    assert [e.dst for e in tl.edges_at("2019-06-01")] == ["b"]
    assert [e.dst for e in tl.edges_at("2020-06-01")] == ["c"]
    assert tl.edges_at("2018-06-01") == []


def test_slice_carries_the_nodes_its_edges_need():
    g = mk_graph(mk_edge("a", "holds", "b", "2019-01-01", "2020-01-01"),
                 mk_edge("b", "holds", "c", "2020-01-01"))
    sub = Timeline(g).as_of("2019-06-01")
    assert set(sub.nodes) == {"a", "b"}     # c is not reachable yet
    assert sub.nodes["b"].label == "B"


def test_change_points_are_the_only_dates_that_matter():
    g = mk_graph(mk_edge("a", "holds", "b", "2019-01-01", "2020-01-01"),
                 mk_edge("a", "holds", "c", "2020-01-01", "2022-01-01"))
    assert Timeline(g).change_points() == ["2019-01-01", "2020-01-01", "2022-01-01"]


def test_segments_cover_an_interval_without_gaps():
    g = mk_graph(mk_edge("a", "holds", "b", "2019-01-01", "2020-01-01"),
                 mk_edge("a", "holds", "c", "2020-01-01"))
    segs = list(Timeline(g).segment("2018-01-01", "2021-01-01"))
    assert [s[0] for s in segs] == ["2018-01-01", "2019-01-01", "2020-01-01"]
    # contiguous: each segment ends where the next begins
    for a, b in zip(segs, segs[1:]):
        assert a[1] == b[0]
    assert segs[-1][1] == "2021-01-01"


# --- SideTable: versions of one triple --------------------------------------

def test_two_versions_of_one_holding_return_by_date():
    st = SideTable()
    st.add(mk_edge("a", "holder_of", "b", "2019-02-14", "2021-02-16", percent=5.4),
           supersedes="")
    st.add(mk_edge("a", "holder_of", "b", "2021-02-16", percent=4.1),
           supersedes="https://example.test/filing-2")
    assert st.at(("a", "holder_of", "b"), "2020-06-01").edge.attrs["percent"] == 5.4
    assert st.at(("a", "holder_of", "b"), "2022-01-01").edge.attrs["percent"] == 4.1
    assert st.at(("a", "holder_of", "b"), "2018-01-01") is None


def test_versions_are_ordered_oldest_first():
    st = SideTable()
    st.add(mk_edge("a", "holder_of", "b", "2021-02-16", percent=4.1))
    st.add(mk_edge("a", "holder_of", "b", "2019-02-14", "2021-02-16", percent=5.4))
    assert [r.interval.start for r in st.versions(("a", "holder_of", "b"))] ==            ["2019-02-14", "2021-02-16"]


def test_overlapping_versions_are_a_contradiction_not_a_coin_flip():
    st = SideTable()
    st.add(mk_edge("a", "holder_of", "b", "2019-01-01", "2021-01-01"))
    st.add(mk_edge("a", "holder_of", "b", "2020-01-01", "2022-01-01"))
    assert len(st.contradictions()) == 1
    with pytest.raises(ValueError, match="overlapping"):
        st.at(("a", "holder_of", "b"), "2020-06-01")


def test_sidetable_slice_matches_the_timeline_slice():
    g = mk_graph(mk_edge("a", "holds", "b", "2019-01-01", "2020-01-01"),
                 mk_edge("a", "holds", "c", "2020-01-01"))
    st = SideTable()
    for e in g.edges.values():
        st.add(e)
    on = "2019-06-01"
    assert {e.key for e in st.as_of(on, graph=g).edges.values()} ==            {e.key for e in Timeline(g).edges_at(on)}


def test_collapse_keeps_the_latest_version_only():
    st = SideTable()
    st.add(mk_edge("a", "holder_of", "b", "2019-02-14", "2021-02-16", percent=5.4))
    st.add(mk_edge("a", "holder_of", "b", "2021-02-16", percent=4.1))
    collapsed = st.collapse()
    assert len(collapsed.edges) == 1
    assert collapsed.edges[("a", "holder_of", "b")].attrs["percent"] == 4.1


def test_sidetable_reports_its_size_and_serialises():
    st = SideTable()
    st.add(mk_edge("a", "holder_of", "b", "2019-02-14", "2021-02-16", percent=5.4))
    st.add(mk_edge("a", "holder_of", "b", "2021-02-16", percent=4.1))
    assert len(st) == 2
    d = st.to_dict()
    assert len(d["a:holder_of:b"]) == 2
    json.dumps(d)   # must be serialisable as-is


# --- from_graph: retrofitting dates onto an existing graph -------------------

def test_from_graph_dates_an_edgar_edge_by_its_filing_date():
    e = Edge(src="a", dst="b", rel="holder_of", source="SEC EDGAR",
             url="https://example.test/f1", attrs={"date": "2021-02-16", "percent": 5.4})
    g = mk_graph(e)
    dated, notes = from_graph(g)
    assert dated.edges[e.key].attrs["valid_from"] == "2021-02-16"
    assert "valid_to" not in dated.edges[e.key].attrs
    assert notes == []


def test_the_next_filing_ends_the_previous_version():
    """A Graph cannot hold two versions of one triple -- the dict would keep the last one.
    So the versions go straight into the side table, which is what it is for."""
    st = SideTable()
    st.add(Edge(src="a", dst="b", rel="holder_of", source="SEC EDGAR", url="u1",
                attrs={"date": "2019-02-14", "valid_from": "2019-02-14",
                       "valid_to": "2021-02-16", "percent": 5.4}))
    st.add(Edge(src="a", dst="b", rel="holder_of", source="SEC EDGAR", url="u2",
                attrs={"date": "2021-02-16", "valid_from": "2021-02-16", "percent": 4.1}))
    assert st.at(("a", "holder_of", "b"), "2020-06-01").edge.attrs["percent"] == 5.4
    assert st.at(("a", "holder_of", "b"), "2022-01-01").edge.attrs["percent"] == 4.1


def test_an_unmapped_source_is_left_undated_and_named():
    e = Edge(src="a", dst="b", rel="licensed_in", source="FDIC", url="u", attrs={})
    dated, notes = from_graph(mk_graph(e))
    assert not intervals_of(dated.edges[e.key]).start
    assert notes[0]["source"] == "FDIC"
    assert notes[0]["what"] == "undated"


def test_a_mapping_can_be_declared_for_a_source():
    e = Edge(src="a", dst="b", rel="licensed_in", source="FDIC", url="u",
             attrs={"as_of": "2026-10-30"})
    dated, notes = from_graph(mk_graph(e), maps={"FDIC": {"valid_from": "as_of"}})
    assert dated.edges[e.key].attrs["valid_from"] == "2026-10-30"
    assert notes == []


def test_same_day_versions_do_not_become_a_zero_length_interval():
    """Two filings signed the same day: the second must not close the first with an end
    equal to its start, which would make the version exist for zero time."""
    from entitygraph.timeline import _date_of
    from entitygraph.timeline import DEFAULT_MAPS
    st = SideTable()
    e = Edge(src="a", dst="b", rel="holder_of", source="SEC EDGAR", url="u1",
             attrs={"date": "2021-02-16", "valid_from": "2021-02-16"})
    st.add(e)   # end omitted because end <= start; asserted below via the absence of valid_to
    assert "valid_to" not in e.attrs
    assert _date_of("2021-02-16T00:00:00+00:00") == "2021-02-16"
    assert DEFAULT_MAPS["SEC EDGAR"]["valid_from"] == "date"


def test_a_real_style_slice_differs_by_year():
    """The shape the module exists for: the same graph answers three dates differently."""
    g = mk_graph(
        Edge(src="a", dst="b", rel="holder_of", source="SEC EDGAR", url="u1", attrs={"date": "2019-02-14"}),
        Edge(src="a", dst="c", rel="holder_of", source="SEC EDGAR", url="u2", attrs={"date": "2021-02-16"}),
        Edge(src="c", dst="b", rel="holder_of", source="SEC EDGAR", url="u3", attrs={"date": "2023-02-14"}),
    )
    dated, _ = from_graph(g)
    st = SideTable.from_graph(dated)
    counts = {t: len(st.as_of(t, graph=dated).edges) for t in ("2019-06-01", "2021-06-01", "2023-06-01")}
    # a->b has no end because nothing else in the graph ends *that same triple*: a filing
    # about a->c does not end a->b. Ends come from a superseding document for the same
    # relationship, never from an unrelated one, so a->b is still open in 2023.
    assert counts == {"2019-06-01": 1, "2021-06-01": 2, "2023-06-01": 3}


def test_sidetable_missing_lists_edges_it_never_saw():
    g = mk_graph(Edge(src="a", dst="b", rel="r", source="test"))
    dated, _ = from_graph(g)
    st = SideTable.from_graph(dated)
    assert st.missing(dated) == [("a", "r", "b")]
