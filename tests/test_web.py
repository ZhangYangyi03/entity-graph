"""Tests for the general-web adapter. Offline: no network is used.

The crawler is injected as a stub so the provenance rules are checked against
a known result, including the failure cases (error / non-2xx / bot shell).
"""
from __future__ import annotations

import json

from entitygraph.model import Graph
from entitygraph import web


class FakeCrawler:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def crawl(self, urls, mode="fast", concurrency=1, timeout=1, with_text=False):
        self.calls.append({"urls": list(urls), "mode": mode, "with_text": with_text})
        return {"mode": mode, "count": len(urls), "elapsed": 0.5, "rps": 2.0,
                "results": self.rows}


def _rows():
    return [
        {"url": "https://news.example.org/", "status": 200, "bytes": 20000,
         "title": "  Example   News  ",
         "text": "A story about open data and another about satellites. " * 8,
         "markdown": "# Example News",
         "links": ["https://arxiv.org/abs/1", "/local", "mailto:a@b.c",
                   "https://arxiv.org/abs/2", "https://data.gov/dataset/9"]},
        {"url": "https://blocked.example.org/", "status": 200, "bytes": 40000,
         "title": "Just a moment", "text": "Checking your browser", "links": []},
        {"url": "https://forbidden.example.org/", "status": 403, "bytes": 100,
         "title": "Forbidden", "text": "", "links": []},
        {"url": "https://down.example.org/", "error": "SSLCertVerificationError"},
    ]


def test_every_node_and_edge_carries_a_real_url_and_source():
    g = Graph()
    meta = web.add_web(g, {"mode": "fast", "count": 4, "results": _rows()},
                       engine="autoforge.crawler:fast")
    assert meta["pages"] == 1 and meta["requested"] == 4
    assert all(n.url and n.source and n.fetched_at for n in g.nodes.values())
    assert all(e.url and e.source and e.fetched_at for e in g.edges.values())
    assert all(n.attrs.get("url") for n in g.nodes.values() if n.kind == "webpage")


def test_links_become_cited_website_nodes_deduplicated_by_host():
    g = Graph()
    meta = web.add_web(g, {"mode": "fast", "count": 1, "results": _rows()[:1]})
    sites = [n for n in g.nodes.values() if n.kind == "website"]
    labels = sorted(n.label for n in sites)
    assert "arxiv.org" in labels and "data.gov" in labels
    assert meta["hosts"] == 3, "two arxiv links must collapse into one website node"
    assert "news.example.org" in labels   # relative /local resolves back to the origin
    assert meta["edges"] == 3
    for e in g.edges.values():
        assert e.rel == "links_to" and e.url == "https://news.example.org/"


def test_failed_and_blocked_urls_are_visible_not_silently_dropped():
    g = Graph()
    meta = web.add_web(g, {"mode": "fast", "count": 4, "results": _rows()})
    reasons = {c["why"] for c in meta["caveats"]}
    assert reasons == {"bot-check page", "http status", "fetch error"} or (
        "blocked" in reasons and "http status" in reasons and "fetch error" in reasons)
    assert len(meta["caveats"]) == 3
    assert not any("blocked.example.org" in n.attrs.get("url", "") for n in g.nodes.values())


def test_soft_block_is_not_laundered_into_data():
    g = Graph()
    row = {"url": "https://amazon.example/", "status": 202, "bytes": 0,
           "title": "", "text": "", "links": []}
    meta = web.add_web(g, {"mode": "fast", "count": 1, "results": [row]})
    assert meta["pages"] == 0
    assert meta["caveats"][0]["why"] == "blocked"


def test_build_web_passes_mode_and_uses_native_crawler():
    fake = FakeCrawler(_rows()[:1])
    g, meta = web.build_web(["https://news.example.org/"], mode="stealth",
                            with_text=True, crawler=fake)
    assert fake.calls[0]["mode"] == "stealth" and fake.calls[0]["with_text"] is True
    assert meta["engine"] == "autoforge.crawler:stealth"
    assert meta["pages"] == 1 and meta["elapsed"] == 0.5
    assert web.to_dict(g, meta)["caveats"] == []


def test_stdlib_fallback_works_without_autoforge(monkeypatch):
    monkeypatch.setattr(web, "_fallback_one",
                        lambda u, timeout: {"url": u, "status": 200, "bytes": 9000,
                                            "title": "Offline", "links": ["https://x.org/a"],
                                            "text": "A complete sentence of real content. " * 4})
    # force the import of autoforge.crawler to fail
    import builtins
    real = builtins.__import__

    def blocked(name, *a, **kw):
        if name.startswith("autoforge"):
            raise ImportError("no autoforge here")
        return real(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", blocked)
    g, meta = web.build_web(["https://offline.example/"], mode="fast")
    assert meta["engine"] == "entitygraph.web:stdlib"
    assert meta["pages"] == 1


def test_url_normalisation_makes_one_stable_page_key():
    a = web.web_key("https://EXAMPLE.org/a/index.html?x=1#frag")
    b = web.web_key("https://example.org/a/?x=1")
    assert a == b
    assert web.host_key("https://www.Arxiv.org/x") == "site:arxiv.org"


def test_edge_without_source_is_still_refused():
    # the repository's central invariant must survive the new adapter
    from entitygraph.model import Edge
    g = Graph()
    try:
        g.add_edge(Edge(src="a", dst="b", rel="links_to"))
    except ValueError:
        pass
    else:
        raise AssertionError("an uncited edge was accepted")


def test_cli_web_writes_a_cited_graph(tmp_path, monkeypatch):
    from entitygraph import cli
    fake = FakeCrawler(_rows()[:1])
    monkeypatch.setattr(web, "fetch_web",
                        lambda *a, **kw: ({"mode": "fast", "count": 1,
                                          "elapsed": 0.1, "rps": 10.0,
                                          "results": _rows()[:1]},
                                         "autoforge.crawler:fast"))
    out = tmp_path / "web.graph.json"
    rc = cli.main(["web", "https://news.example.org/", "--out", str(out)])
    assert rc == 0 and out.exists()
    d = json.loads(out.read_text(encoding="utf-8"))
    assert d["meta"]["engine"] == "autoforge.crawler:fast"
    assert d["nodes"] and d["edges"]
    assert all(e["url"] and e["source"] for e in d["edges"])
