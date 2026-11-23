"""The 6+1 taxonomy and the scrapers that feed the industry map, with no network.

conftest.py refuses every socket, so anything reaching the wire here fails loudly.
"""
from __future__ import annotations

import json

import pytest

from entitygraph import bci, biomed, directions as D, industry


# ------------------------------------------------------------------ taxonomy
def test_six_plus_one_is_seven_directions():
    assert len(D.DIRECTIONS) == 7
    assert "organoid" in D.DIRECTIONS
    # every direction must carry the 技术性能参数 the 实施方案 asks to compare on
    for d in D.DIRECTIONS.values():
        assert d["params"].strip()
        assert d["keywords"]


def test_a_paper_title_lands_on_the_direction_its_paradigm_names():
    hit = D.classify("Four class motor imagery decoding from EEG")
    assert [h["direction"] for h in hit] == ["brain_control"]


def test_one_generic_keyword_is_labelled_weak_not_ok():
    # "electroencephalograph" alone is an EEG machine, not evidence of 微针电极 work
    hit = D.classify("P460, DG32 ELECTROENCEPHALOGRAPH (K896666)")
    assert hit and hit[0]["confidence"] == "weak"


def test_organoid_text_hits_both_the_direction_and_the_bio_domain():
    text = "Brain organoid intelligence on a multielectrode array"
    assert [h["direction"] for h in D.classify(text)] == ["organoid"]
    assert [b["domain"] for b in D.classify_bio(text)] == ["organoid_manufacturing"]


def test_text_with_no_keyword_gets_no_tag_rather_than_a_guess():
    assert D.classify("Auditory oddball during hypnosis") == []


def test_chain_stage_is_none_when_the_text_does_not_say():
    assert D.chain_stage("Auditory oddball during hypnosis") is None
    assert D.chain_stage("implantable electrode with encapsulation")["stage"] == "upstream"


def test_every_named_actor_cites_the_document_sentence_it_rests_on():
    for a in D.ACTORS:
        assert a["fact"].strip(), a["name"]
        assert a["chain"] in D.FOUR_CHAINS


# ------------------------------------------------------------------ scrapers
BNCI_PAGE = """
<div class="card-title">1. Four class motor imagery (001-2014)</div></h3>
<p>This four class motor imagery data set was released as data set 2a.</p>
<a href="/database/data-sets/001-2014/A01T.mat">A01T</a>
<div class="card-title">2. Two class motor imagery (002-2014)</div></h3>
<p>Participants 14 Signals 15 EEG</p>
<a href="/database/data-sets/002-2014/S01T.mat">S01T</a>
"""


def test_bnci_cards_give_id_title_and_participant_counts(monkeypatch):
    monkeypatch.setattr(bci, "get", lambda *a, **k: BNCI_PAGE)
    rows = bci.bnci()
    assert [r["id"] for r in rows] == ["001-2014", "002-2014"]
    assert rows[1]["participants"] == 14 and rows[1]["signals"] == 15
    assert rows[0]["participants"] is None


PHYSIO_SEARCH = """
<a href="/content/eegmmidb/1.0.0/">EEG Motor Movement/Imagery Dataset v1.0.0</a>
"""
PHYSIO_PROJECT = """<title>EEG Motor Movement/Imagery Dataset v1.0.0</title>
<meta name="description" content="EEG recordings obtained from 109 volunteers.">
Published Sept. 9, 2009"""


def test_physionet_reads_the_project_page_for_the_untruncated_title(monkeypatch):
    def fake(url, **kw):
        return PHYSIO_PROJECT if "content/eegmmidb" in url else PHYSIO_SEARCH
    monkeypatch.setattr(bci, "get", fake)
    rows = bci.physionet("eeg")
    assert rows[0]["slug"] == "eegmmidb"
    assert rows[0]["title"].startswith("EEG Motor Movement")
    assert rows[0]["published"] == "Sept. 9, 2009"


def test_openneuro_error_payload_is_an_error_not_an_empty_list(monkeypatch):
    monkeypatch.setattr(bci, "post", lambda *a, **k: {"errors": [{"message": "bad field"}]})
    with pytest.raises(bci.FetchError):
        bci.openneuro(["eeg"])


def test_ctgov_maps_only_the_fields_the_graph_needs():
    payload = {"studies": [{"protocolSection": {
        "identificationModule": {"nctId": "NCT1", "briefTitle": "T"},
        "statusModule": {"overallStatus": "RECRUITING"},
        "designModule": {"phases": ["PHASE2"], "enrollmentInfo": {"count": 12}},
        "conditionsModule": {"conditions": ["Stroke"]},
        "sponsorCollaboratorsModule": {"leadSponsor": {"name": "X"}}}}]}
    import entitygraph.bci as m
    orig = m.get
    m.get = lambda *a, **k: payload
    try:
        rows = m.ctgov("stroke")
    finally:
        m.get = orig
    assert rows == [{"nct": "NCT1", "title": "T", "status": "RECRUITING", "phase": "PHASE2",
                     "enrollment": 12, "conditions": ["Stroke"], "sponsor": "X",
                     "url": "https://clinicaltrials.gov/study/NCT1"}]


# ------------------------------------------------------------------ the map
def _offline(monkeypatch):
    monkeypatch.setattr(bci, "openneuro", lambda *a, **k: [])
    monkeypatch.setattr(bci, "bnci", lambda *a, **k: [])
    monkeypatch.setattr(bci, "ctgov", lambda *a, **k: [])
    monkeypatch.setattr(bci, "pubmed", lambda *a, **k: [])
    monkeypatch.setattr(bci, "openfda_device", lambda *a, **k: [])
    monkeypatch.setattr(industry, "BM", type("M", (), {"build_biomed_graph":
        staticmethod(lambda *a, **k: _empty())}))


def _empty():
    from entitygraph.model import Graph
    return Graph()


def test_industry_map_has_the_seven_directions_even_with_every_source_down(monkeypatch):
    _offline(monkeypatch)
    g = industry.build_industry_map(with_bio=False)
    kinds = g.stats()["by_kind"]
    assert kinds["bci_direction"] == 7
    assert kinds["chain_stage"] == 3
    assert kinds["four_chain"] == 1


def test_industry_map_refuses_to_invent_evidence(monkeypatch):
    _offline(monkeypatch)
    g = industry.build_industry_map(with_bio=False)
    assert g.stats()["by_kind"].get("dataset", 0) == 0
    assert g.stats()["by_kind"].get("trial", 0) == 0


def test_a_fetch_failure_becomes_a_note_not_an_exception(monkeypatch):
    def boom(*a, **k):
        raise bci.FetchError("503")
    monkeypatch.setattr(bci, "openneuro", boom)
    monkeypatch.setattr(bci, "bnci", boom)
    monkeypatch.setattr(bci, "ctgov", boom)
    monkeypatch.setattr(bci, "pubmed", boom)
    monkeypatch.setattr(bci, "openfda_device", boom)
    g = industry.build_industry_map(with_bio=False)
    sources = {n["source"] for n in g._notes}
    assert {"OpenNeuro", "BNCI Horizon 2020", "ClinicalTrials.gov", "PubMed"} <= sources


def test_every_actor_edge_carries_the_basis_it_rests_on(monkeypatch):
    _offline(monkeypatch)
    g = industry.build_industry_map(with_bio=False)
    works = [e for e in g.edges.values() if e.rel == "works_on"]
    assert works
    assert all(e.attrs.get("basis") for e in works)
    assert all(e.source == "实施方案（项目文档）" for e in works)


def test_build_biomed_refuses_without_a_target(monkeypatch):
    monkeypatch.setattr(biomed, "opentargets_target", lambda *a, **k: None)
    monkeypatch.setattr(biomed, "uniprot", lambda *a, **k: None)
    monkeypatch.setattr(biomed, "chembl_targets", lambda *a, **k: [])
    monkeypatch.setattr(biomed, "europepmc", lambda *a, **k: [])
    g = biomed.build_biomed_graph("NOSUCHGENE")
    assert g.stats()["nodes"] == 1  # the gene node itself, from the operator's query
    assert g.nodes[list(g.nodes)[0]].source == "operator query"
