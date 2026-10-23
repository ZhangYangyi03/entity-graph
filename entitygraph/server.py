"""An HTTP face over the graph: stdlib only, so `python -m entitygraph.server` just runs.

Three endpoints, matching what the analysis actually needs:
  GET /entity?name=...&cik=...   build a graph around a company
  GET /entity/{lei}              the same by identifier
  GET /uk/stats                  how many UK companies / PSC rows the local index holds
  GET /health                    what the process can reach, and what it cached
"""
from __future__ import annotations

import json
import os
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import __version__
from .build import build
from .resolve import Resolver
from . import uk as ukmod


def _json(handler, code: int, payload) -> None:
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode()
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    handler.wfile.write(body)


class Handler(BaseHTTPRequestHandler):
    server_version = f"entitygraph/{__version__}"

    def log_message(self, fmt, *args):  # keep stdout readable
        pass

    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        parts = [p for p in parsed.path.split("/") if p]
        qs = urllib.parse.parse_qs(parsed.query)

        if not parts or parts[0] == "health":
            return _json(self, 200, {
                "ok": True, "version": __version__,
                "cache": os.environ.get("ENTITYGRAPH_CACHE", ".cache"),
                "sources": ["GLEIF", "SEC EDGAR", "OpenSanctions"],
                "registries": ["FR", "NO", "DK", "FI", "CH", "CZ", "BR", "GB", "CN", "TW", "KR"],
                "uk_index": ukmod.stats(os.environ.get("ENTITYGRAPH_UK_DB", "uk.sqlite")),
            })

        if parts[0] == "uk" and len(parts) > 1 and parts[1] == "stats":
            return _json(self, 200, ukmod.stats(os.environ.get("ENTITYGRAPH_UK_DB", "uk.sqlite")))

        if parts[0] == "entity":
            if len(parts) > 1:
                g = build(lei=parts[1])
            else:
                name = (qs.get("name") or [None])[0]
                cik = (qs.get("cik") or [None])[0]
                if not name and not cik:
                    return _json(self, 400, {"error": "give ?name= or ?cik= or /entity/{lei}"})
                cc = qs.get("country")
                g = build(name=name, cik=cik, countries=cc,
                          registry_id=(qs.get("id") or [None])[0],
                          registry_country=(cc[0] if cc else None),
                          officers=(qs.get("officers") or ["1"])[0] not in ("0", "false"))
            payload = g.to_dict()
            payload["stats"] = g.stats()
            payload["notes"] = getattr(g, "_notes", [])
            payload["resolver_candidates"] = [
                m.__dict__ for m in Resolver(g).candidates(
                    (qs.get("name") or [""])[0])] if qs.get("name") else []
            return _json(self, 200, payload)

        return _json(self, 404, {"error": "unknown path",
                                 "paths": ["/entity", "/entity/{lei}", "/uk/stats", "/health"]})


def main() -> None:
    host = os.environ.get("ENTITYGRAPH_HOST", "127.0.0.1")
    port = int(os.environ.get("ENTITYGRAPH_PORT", "8087"))
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"entitygraph {__version__} on http://{host}:{port}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    main()
