"""build() must attach registry hits without inventing a merge."""
import sys

from entitygraph.build import build

buildmod = sys.modules['entitygraph.build']
from entitygraph import registries as R


def _offline(monkeypatch, registry_hit=None):
    """Every live source stubbed. Without this the test goes to GLEIF/EDGAR over the real
    network and takes 78 seconds (measured) -- a unit test for edge assembly should not
    depend on whether the machine has a route to the internet."""
    monkeypatch.setattr(buildmod, "get", lambda *a, **kw: {"data": []})
    monkeypatch.setattr(buildmod.sources, "sanctions_for", lambda *a, **kw: [], raising=False)
    monkeypatch.setattr(buildmod.asia, "lookup", lambda *a, **kw: [], raising=False)
    monkeypatch.setattr(buildmod.korea, "lookup", lambda *a, **kw: [], raising=False)
    monkeypatch.setattr(buildmod.japan, "lookup", lambda *a, **kw: [], raising=False)
    monkeypatch.setattr(buildmod.china, "lookup", lambda *a, **kw: [], raising=False)
    monkeypatch.setattr(buildmod.ukmod, "lookup", lambda *a, **kw: [], raising=False)
    if registry_hit is not None:
        monkeypatch.setattr(R, "lookup_name", lambda name, countries=None: registry_hit)
def test_registry_hit_becomes_company_plus_officers(monkeypatch):
    _offline(monkeypatch, registry_hit=[{
        "id": "652014051", "name": "CARREFOUR", "country": "FR",
        "address": "93 AVENUE DE PARIS 91300 MASSY", "kind": "company",
        "officers": [{"name": "Alexandre Bompard", "role": "Président"}],
        "attributes": {"siren": "652014051"}, "url": "https://example.invalid/fr/652014051",
    }])
    g = build(name="Carrefour", sanction_check=False, lei=None)
    kinds = {n.kind for n in g.nodes.values()}
    assert "company" in kinds and "person" in kinds
    rels = {e.rel for e in g.edges.values()}
    assert "officer_of" in rels
    edge = next(e for e in g.edges.values() if e.rel == "officer_of")
    assert edge.source == "FR registry"
    assert edge.url == "https://example.invalid/fr/652014051"


def test_no_registry_hit_leaves_sanctions_alone(monkeypatch):
    _offline(monkeypatch, registry_hit=[])
    g = build(name="Nobody Industries", sanction_check=False)
    assert all(n.kind == "company" for n in g.nodes.values())
