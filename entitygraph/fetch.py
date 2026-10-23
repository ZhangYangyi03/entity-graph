"""HTTP with a cache, a timeout, and a UA that says who is asking.

SEC requires a User-Agent carrying a contact address; several of the other registries
throttle anonymous clients. One place sets that, so no caller can forget.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

# SEC rejects (403) any User-Agent without a contact address, and does so on the very
# first request, so the default must carry one. Override with ENTITYGRAPH_UA, or set
# ENTITYGRAPH_CONTACT to put a different address in.
_CONTACT = os.environ.get("ENTITYGRAPH_CONTACT", "2936745@qq.com")
# Measured on 2026-10-23: SEC returns 403 for "entity-graph/0.1 (...; addr)" and for a
# browser UA, and 200 for "entity-graph/0.1 addr". It is the *shape* it rejects, not the
# presence of an address, so the default is the shape that works.
DEFAULT_UA = os.environ.get("ENTITYGRAPH_UA", f"entity-graph/0.1 {_CONTACT}")
CACHE_DIR = os.environ.get(
    "ENTITYGRAPH_CACHE",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".cache"),
)


class FetchError(RuntimeError):
    pass


def _cache_path(url: str) -> str:
    h = hashlib.sha256(url.encode()).hexdigest()[:32]
    return os.path.join(CACHE_DIR, h + ".json")


def get(url: str, *, ttl: float = 86400.0, timeout: float = 30.0, headers: dict | None = None,
        raw: bool = False, retries: int = 2) -> str | dict:
    """GET with an on-disk cache.

    ttl seconds of freshness; a cache hit inside the window returns without touching the
    network. `raw=True` returns text (for HTML/CSV endpoints), otherwise JSON is parsed.
    """
    cp = _cache_path(url)
    if ttl > 0 and os.path.exists(cp) and time.time() - os.path.getmtime(cp) < ttl:
        with open(cp, encoding="utf-8") as f:
            blob = json.load(f)
        return blob["text"] if raw else blob["json"]

    h = {"User-Agent": DEFAULT_UA, "Accept": "*/*"}
    if headers:
        h.update(headers)

    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                text = r.read().decode("utf-8", "ignore")
            break
        except urllib.error.HTTPError as e:
            if e.code in (403, 404) or attempt == retries:
                raise FetchError(f"{e.code} {url}") from e
            last = e
        except Exception as e:  # timeouts, TLS resets
            if attempt == retries:
                raise FetchError(f"{type(e).__name__}: {e} <- {url}") from e
            last = e
        time.sleep(1.5 * (attempt + 1))
    else:  # pragma: no cover
        raise FetchError(str(last))

    parsed = None
    if not raw:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as e:
            raise FetchError(f"not JSON from {url}: {text[:120]!r}") from e

    if ttl > 0:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(cp, "w", encoding="utf-8") as f:
            json.dump({"url": url, "at": time.time(), "text": text, "json": parsed}, f,
                      ensure_ascii=False)
    return text if raw else parsed


def q(s: str) -> str:
    return urllib.parse.quote(s, safe="")
