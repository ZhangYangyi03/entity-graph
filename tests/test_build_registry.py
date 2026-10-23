"""build() must attach registry hits without inventing a merge."""
from entitygraph.build import build
from entitygraph import registries as R


def test_registry_hit_becomes_company_plus_officers(monkeypatch):
    monkeypatch.setattr(R, "lookup_name", lambda name, countries=None: [{
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
    monkeypatch.setattr(R, "lookup_name", lambda name, countries=None: [])
    g = build(name="Nobody Industries", sanction_check=False)
    assert all(n.kind == "company" for n in g.nodes.values())
