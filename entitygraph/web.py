"""General-web adapter: turn arbitrary fetched pages into cited graph nodes.

entity-graph used to ingest only structured JSON/API endpoints, which is why its
data surface stayed narrow (54 declared domains, 18 actually cached). This module
is the generic entry point: any http(s) page becomes a `webpage` node, and the
sites it links out to become `website` nodes joined by `links_to` edges.

Every node and edge carries the URL that was really fetched and the timestamp,
per this repository's provenance rule. A URL that fails is NOT silently dropped:
it goes into caveats with its reason, so a reader can tell "not covered" from
"not fetched". A 2xx response that is actually a bot-check placeholder is also
recorded as a failure rather than being laundered into data.

The crawler is optional. If `autoforge.crawler` is importable (the engine behind
the native crawl_fast / crawl_stealth / crawl_render tools) its engines are used;
otherwise a small stdlib fetcher covers the same contract so a plain install of
entitygraph still works. `add_web()` accepts an already-fetched result dict, so
tests and offline re-imports never need the network.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import html as _html
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from typing import Any, Callable

from .model import Edge, Graph, Node

NOW = lambda: _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")

BLOCK_MARKERS = (
    "robot check", "captcha", "are you human", "just a moment",
    "access denied", "unusual traffic", "verify you are human",
    "please enable javascript", "attention required",
)


def normalize_url(url: str) -> str:
    """Drop the fragment and trailing index noise so a page has one stable key."""
    u = (url or "").strip()
    try:
        p = urllib.parse.urlsplit(u)
        if p.scheme not in ("http", "https") or not p.netloc:
            return u
        path = re.sub(r"/(?:index|default)\.(?:html?|php|aspx?)$", "/", p.path, flags=re.I)
        return urllib.parse.urlunsplit((p.scheme.lower(), p.netloc.lower(), path, p.query, ""))
    except ValueError:
        return u


def web_key(url: str) -> str:
    return "web:" + hashlib.sha1(normalize_url(url).encode("utf-8")).hexdigest()[:16]


def host_key(host: str) -> str:
    """A site key from a bare hostname or a full URL."""
    h = (host or "").strip().lower()
    if "://" in h:
        h = host_of(h)
    return "site:" + re.sub(r"^www\.", "", h)


def host_of(url: str) -> str:
    try:
        return re.sub(r"^www\.", "", (urllib.parse.urlsplit(url).hostname or "").lower())
    except ValueError:
        return ""


def is_blocked(row: dict) -> str:
    """Return a reason string when a 2xx response is really a bot-check shell."""
    title = " ".join((row.get("title") or "").split()).lower()
    text = " ".join((row.get("text") or "").split())
    status = row.get("status")
    for m in BLOCK_MARKERS:
        if m in title or m in text[:400].lower():
            return "bot-check page: %s" % m
    if status in (200, 202) and len(text) < 120 and (row.get("bytes") or 0) < 6000:
        return "soft block: 2xx but only %d chars of text / %s bytes" % (len(text), row.get("bytes"))
    return ""


def _abs_links(page_url: str, hrefs: list[str]) -> list[str]:
    out, seen = [], set()
    for h in hrefs or []:
        if not isinstance(h, str) or not h.strip():
            continue
        try:
            a = urllib.parse.urljoin(page_url, h.strip())
            p = urllib.parse.urlsplit(a)
            if p.scheme not in ("http", "https") or not p.hostname:
                continue
            a = urllib.parse.urlunsplit((p.scheme, p.netloc, p.path, p.query, ""))
            if a not in seen:
                seen.add(a); out.append(a)
        except ValueError:
            continue
    return out


class _Parser(HTMLParser):
    """Tiny stdlib fallback extractor; only used when autoforge.crawler is absent."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title: list[str] = []
        self.links: list[str] = []
        self.text: list[str] = []
        self._in_title = False
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self._skip += 1
        if tag == "title":
            self._in_title = True
        if tag == "a":
            for k, v in attrs:
                if k == "href" and v:
                    self.links.append(v)

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript") and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._skip:
            return
        if self._in_title:
            self.title.append(data)
        else:
            s = " ".join(data.split())
            if s:
                self.text.append(s)


def _fallback_one(url: str, timeout: float) -> dict:
    import urllib.request
    from .fetch import _ssl_context, DEFAULT_UA

    req = urllib.request.Request(url, headers={"User-Agent": DEFAULT_UA,
                                               "Accept": "text/html,*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as r:
            raw = r.read()
            status = getattr(r, "status", 200)
    except Exception as e:
        return {"url": url, "error": "%s: %s" % (type(e).__name__, str(e)[:140])}
    s = raw.decode("utf-8", "replace")
    p = _Parser()
    try:
        p.feed(s)
    except Exception:
        pass
    return {"url": url, "status": status, "bytes": len(raw),
            "title": " ".join(p.title).strip() or None,
            "links": p.links[:50], "text": " ".join(p.text)[:5000]}


def fetch_web(urls: list[str], *, mode: str = "fast", concurrency: int = 12,
              timeout: float = 10.0, with_text: bool = True,
              crawler: Any = None) -> tuple[dict, str]:
    """Fetch URLs, preferring the native autoforge crawler.

    Returns (result_dict, engine_name). Never raises for a per-URL failure; the
    failure is a row in results, matching the crawler's own contract.
    """
    if isinstance(urls, str):
        urls = [urls]
    urls = [str(u).strip() for u in urls if str(u).strip()]
    if mode not in ("fast", "stealth", "render"):
        raise ValueError("mode must be one of fast|stealth|render")
    if not urls:
        return {"mode": mode, "count": 0, "elapsed": 0.0, "rps": 0.0,
                "results": []}, "none"

    if crawler is None:
        try:
            from autoforge import crawler as crawler  # type: ignore
        except Exception:
            crawler = None
    if crawler is not None and hasattr(crawler, "crawl"):
        res = crawler.crawl(urls, mode=mode, concurrency=concurrency,
                            timeout=timeout, with_text=with_text)
        return res, "autoforge.crawler:" + mode

    import time
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=max(1, min(int(concurrency), 32))) as ex:
        rows = list(ex.map(lambda u: _fallback_one(u, timeout), urls))
    el = time.perf_counter() - t0
    return {"mode": "stdlib", "count": len(urls), "elapsed": round(el, 3),
            "rps": round(len(urls) / el, 1) if el else 0.0,
            "results": rows}, "entitygraph.web:stdlib"


def add_web(graph: Graph, result: dict, *, engine: str = "",
            max_links: int = 24, text_chars: int = 3000,
            source: str = "web") -> dict:
    """Ingest one crawl() result into `graph`. Returns a coverage report.

    Only successfully fetched, non-blocked pages become nodes. Everything else
    is listed in `caveats` with the reason, never quietly omitted.
    """
    caveats: list[dict] = []
    by_host: dict[str, int] = {}
    pages = edges = hosts = 0

    for row in result.get("results", []):
        url = row.get("url") or ""
        if row.get("error"):
            caveats.append({"url": url, "why": "fetch error", "detail": str(row["error"])[:200]})
            continue
        status = row.get("status")
        if not isinstance(status, int) or not 200 <= status < 300:
            caveats.append({"url": url, "why": "http status", "detail": str(status)})
            continue
        blocked = is_blocked(row)
        if blocked:
            caveats.append({"url": url, "why": "blocked", "detail": blocked})
            continue

        h = host_of(url)
        title = " ".join((row.get("title") or "").split())[:200]
        text = " ".join((row.get("text") or "").split())
        markdown = (row.get("markdown") or "")[:text_chars]
        page_key = web_key(url)
        graph.add_node(Node(
            key=page_key, kind="webpage", label=title or url,
            attrs={"url": normalize_url(url), "host": h, "status": status,
                   "bytes": row.get("bytes"), "title": title,
                   "text": text[:text_chars], "markdown": markdown,
                   "link_count": len(row.get("links") or []),
                   "fetched_via": engine or result.get("mode", "")},
            source=source, url=url, fetched_at=NOW()))
        pages += 1
        by_host[h] = by_host.get(h, 0) + 1

        linked_hosts: dict[str, str] = {}
        for a in _abs_links(url, row.get("links") or []):
            lh = host_of(a)
            if lh and lh not in linked_hosts:
                linked_hosts[lh] = a
        for lh, sample in list(linked_hosts.items())[:max_links]:
            hk = host_key(lh)
            graph.add_node(Node(key=hk, kind="website", label=lh,
                                attrs={"host": lh, "sample_url": sample},
                                source=source, url=url, fetched_at=NOW()))
            hosts += 1
            graph.add_edge(Edge(src=page_key, dst=hk, rel="links_to",
                                attrs={"sample_url": sample, "via": engine or result.get("mode", "")},
                                source=source, url=url, fetched_at=NOW()))
            edges += 1

    return {"pages": pages, "hosts": hosts, "edges": edges,
            "by_host": by_host, "caveats": caveats,
            "requested": result.get("count", len(result.get("results", [])))}


def build_web(urls: list[str], *, graph: Graph | None = None, mode: str = "fast",
              concurrency: int = 12, timeout: float = 10.0, with_text: bool = True,
              max_links: int = 24, text_chars: int = 3000,
              crawler: Any = None, source: str = "web") -> tuple[Graph, dict]:
    """Fetch and ingest in one call. Returns (graph, meta) with the caveats."""
    g = graph or Graph()
    result, engine = fetch_web(urls, mode=mode, concurrency=concurrency,
                               timeout=timeout, with_text=with_text, crawler=crawler)
    rep = add_web(g, result, engine=engine, max_links=max_links,
                  text_chars=text_chars, source=source)
    meta = {"engine": engine, "mode": result.get("mode"), "count": result.get("count"),
            "elapsed": result.get("elapsed"), "rps": result.get("rps"),
            "fetched_at": NOW(), **rep}
    return g, meta


def to_dict(graph: Graph, meta: dict) -> dict:
    """Graph JSON with the coverage report kept next to the nodes and edges."""
    d = graph.to_dict()
    d["meta"] = {k: v for k, v in meta.items() if k != "caveats"}
    d["caveats"] = meta.get("caveats", [])
    return d
