from entitygraph.model import Graph, Node
from entitygraph.resolve import Resolver, name_similarity, normalize_name


def test_suffixes_are_dropped():
    assert normalize_name("Berkshire Hathaway Inc.") == "berkshire hathaway"
    assert normalize_name("Apple Inc.") == normalize_name("APPLE")


def test_accent_and_punctuation_folding():
    assert normalize_name("Société Générale S.A.") == "societe generale"


def test_similarity_is_symmetric_and_bounded():
    a, b = "Berkshire Hathaway", "Berkshire Hathaway Energy"
    assert name_similarity(a, b) == name_similarity(b, a)
    assert 0 < name_similarity(a, b) < 1
    assert name_similarity("", "x") == 0.0


def test_resolver_prefers_identifier_over_name():
    g = Graph()
    g.add_node(Node(key="lei:A", kind="company", label="Acme Inc", attrs={"lei": "A" * 20},
                    source="s"))
    g.add_node(Node(key="lei:B", kind="company", label="Acme Holdings", attrs={"lei": "B" * 20},
                    source="s"))
    r = Resolver(g)
    assert r.by_id("lei", "A" * 20) == "lei:A"
    cands = r.candidates("Acme")
    assert {c.key for c in cands} == {"lei:A", "lei:B"}
