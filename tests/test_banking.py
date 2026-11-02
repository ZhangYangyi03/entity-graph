"""Offline tests for the currency-and-banking adapter.

Two things are worth pinning and both are easy to get wrong:

1. The country key. SIX writes registry-style names -- "UNITED STATES OF AMERICA (THE)",
   "NETHERLANDS (THE)" -- while FDIC says "United States". If the two spellings do not
   collapse to one key, 4,217 banks end up licensed-in a country node that does not exist.
   Measured on 2026-10-30: exactly that, 4,219 dangling edges.

2. The failure modes. A source that 404s must become a note, not an exception and not an
   empty result that reads like "there are no products". The MIR endpoint is the live case:
   only one of its product keys answers, and the other 11 must not be invented.
"""
import io

import pytest

from entitygraph import banking


# ------------------------------------------------------------------ country keys

@pytest.mark.parametrize("name,expected", [
    ("UNITED STATES OF AMERICA (THE)", "country:united-states"),
    ("United States", "country:united-states"),
    ("NETHERLANDS (THE)", "country:netherlands"),
    ("Netherlands", "country:netherlands"),
    ("KOREA (THE REPUBLIC OF)", "country:korea-south"),
    ("CONGO (THE DEMOCRATIC REPUBLIC OF THE)", "country:congo-democratic-republic"),
    ("UNITED KINGDOM OF GREAT BRITAIN AND NORTHERN IRELAND (THE)",
     "country:united-kingdom"),
    ("FALKLAND ISLANDS (THE) [MALVINAS]", "country:falkland-islands"),
    ("Euro area", "country:euro-area"),
    ("  GERMANY  ", "country:germany"),
])
def test_country_key_collapses_registry_spellings(name, expected):
    assert banking.country_key(name) == expected


def test_two_spellings_of_one_country_produce_one_key():
    """The invariant that keeps the graph joined, stated as one assertion."""
    assert banking.country_key("UNITED STATES OF AMERICA (THE)") == \
           banking.country_key("United States")


# ------------------------------------------------------------------ RBA parsing

RBA_F5 = (
    "F5  INDICATOR LENDING RATES\n"
    "Title,Lending rates; Small business; Variable; Term,"
    "Lending rates; Housing loans; Banks; Variable; Standard; Owner-occupier,"
    "Lending rates; Empty; Series\n"
    "01-Jan-2026,9.10,6.20,\n"
    "01-Feb-2026,9.05,6.15,\n"
    "01-Mar-2026,8.95,6.05,\n"
)


def test_rba_parser_drops_a_series_with_no_observations():
    series = banking._parse_rba_series(RBA_F5)
    assert len(series) == 2, list(series)
    assert "Lending rates; Empty; Series" not in series


def test_rba_parser_keeps_every_observation_in_order():
    series = banking._parse_rba_series(RBA_F5)
    term = series["Lending rates; Small business; Variable; Term"]
    assert term == [("01-Jan-2026", "9.10"), ("01-Feb-2026", "9.05"),
                    ("01-Mar-2026", "8.95")]


def test_rba_label_becomes_queryable_facets():
    facets = banking._rba_label(
        "Lending rates; Housing loans; Banks; Variable; Standard; Owner-occupier")
    assert facets["category"] == "loan"
    assert facets["segment"] == "household mortgage"
    assert facets["business"] == "new business"
    facets = banking._rba_label("Lending rates; Small business; Overdraft")
    assert facets["segment"] == "business"


def test_rba_products_builds_one_node_per_series_and_an_edge_with_a_rate(monkeypatch):
    monkeypatch.setattr(banking, "get", lambda url, **kw: RBA_F5)
    nodes, edges = banking.rba_products()
    # Both table URLs serve the same fixture, and rba_products de-duplicates on the series
    # slug, so two distinct series survive -- not four. The de-dup is the point: F5 and F6
    # overlap on several housing series and one product must be one node.
    assert len(nodes) == 2
    assert all(n.kind == "bank_product" for n in nodes)
    assert all(e.rel == "offered_in" for e in edges)
    assert all(e.dst == "country:australia" for e in edges)
    assert all(isinstance(e.attrs["rate"], float) for e in edges)
    assert {e.attrs["rate"] for e in edges} == {8.95, 6.05}


# ------------------------------------------------------------------ failure modes

def test_a_failing_source_becomes_a_note_not_an_exception(monkeypatch):
    """The build must survive one dead source and say which one died."""
    def boom(*a, **kw):
        raise banking.FetchError("404 https://example.invalid")

    monkeypatch.setattr(banking, "rba_products", boom)
    # The other live feeds must be stubbed too: with only RBA stubbed, a machine with a
    # real network goes to Canada/World Bank and back, and an offline machine hangs until
    # the OS timeout. Neither is this test's subject -- it is about the note.
    monkeypatch.setattr(banking, "boc_prime_rate", lambda *a, **kw: [])
    monkeypatch.setattr(banking, "wb_rates", lambda *a, **kw: ({}, []))
    monkeypatch.setattr(banking, "ecb_mir_products", lambda *a, **kw: ([], []))
    g = banking.build_currency_graph(rates=False, banks=False)
    note = [n for n in g._notes if n["source"] == "RBA F5/F6"]
    assert note and note[0]["what"] == "fetch"
    assert "404" in note[0]["error"]


def test_integrity_note_reports_zero_dangling_edges(monkeypatch):
    """Every edge must have both endpoints. This is the check that caught the country
    spelling bug, so it is pinned rather than trusted."""
    monkeypatch.setattr(banking, "rba_products", lambda *a, **kw: [])
    monkeypatch.setattr(banking, "boc_prime_rate", lambda *a, **kw: [])
    monkeypatch.setattr(banking, "wb_rates", lambda *a, **kw: ({}, []))
    monkeypatch.setattr(banking, "ecb_mir_products", lambda *a, **kw: ([], []))
    g = banking.build_currency_graph(rates=False, banks=False)
    integrity = [n for n in g._notes if n["source"] == "integrity"]
    assert integrity and integrity[0]["what"] == "ok", integrity


def test_mir_does_not_invent_product_keys(monkeypatch):
    """Only one MIR product key answers; the module must report one product, not twelve."""
    monkeypatch.setattr(banking, "_csv_rows", lambda url, **kw: [
        {"KEY": "M.DE.B.A2C.A.R.A.2250.EUR.N", "OBS_VALUE": "3.5",
         "TIME_PERIOD": "2026-08", "TITLE": "house purchase loans"}])
    nodes, edges = banking.ecb_mir_products()
    assert len(nodes) == 1
    assert nodes[0].key == "product:mir:2250"
    assert nodes[0].attrs["series_item"] == "2250"
    assert all("rate" in e.attrs for e in edges)


# ------------------------------------------------------------------ the graph itself

def test_a_built_graph_serialises_and_reads_back():
    g = banking.build_currency_graph(rates=False, banks=False, products=False)
    import json
    blob = json.loads(g.to_json())
    assert blob["nodes"] and isinstance(blob["edges"], list)
    assert len({n["key"] for n in blob["nodes"]}) == len(blob["nodes"])
