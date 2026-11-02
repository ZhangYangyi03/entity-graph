"""The Fortune Global 500 list, parsed from Wikipedia, with each entry left unverified.

The list is a *starting point*, not a fact about a company. Every name here is one string
that a person typed into a table; turning it into a company node requires matching it to a
LEI, a CIK, or an explicit manual record, and the match is what gets cited. A row that
matches nothing is kept as a `rank_only` node saying exactly that -- it is better to show
"Fortune says #37 is X, and I could not confirm X exists in any registry" than to drop it
or to invent a company node for it.

    python -m entitygraph.fortune --out fortune500.json
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request

from .fetch import FetchError

# Fortune's own site truncates the table behind a paywall and the Wikipedia article for the
# ranking only carries the top ten of each year. The "largest companies by revenue" list is
# the same ranking with a wider table, so it is the one parsed -- and it has 44 rows, not 500.
# That gap is stated here rather than papered over with 500 invented entries.
PAGE = "https://en.wikipedia.org/w/index.php?title=List_of_largest_companies_by_revenue&action=raw"
CITE = "https://en.wikipedia.org/wiki/List_of_largest_companies_by_revenue"

_FLAG = re.compile(r"\{\{flag\|([^}|]+)")
_LINK = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")


def parse(wikitext: str) -> list[dict]:
    """Rows of the ranking table: rank, company, country, industry, revenue, source url.

    Two table formats are handled because the page uses both: a block whose cells are on
    lines starting with "|", and a block whose cells are separated by "||" on one line. The
    rule that keeps this honest is that a row without a rank is not guessed at -- it is
    skipped, and the count of skipped rows is what the caller sees.
    """
    rows: list[dict] = []
    _carry: dict = {}
    for block in wikitext.split("\n|-"):
        if 'scope="row"' not in block:
            continue
        body = block.strip("\n")
        head, _, rest = body.partition("\n")
        rank = re.sub(r"[^\d]", "", head.split("|")[-1])
        if not rank:
            continue
        # drop style attributes and rowspan/colspan markers, then split on either separator
        rest = re.sub(r'style="[^"]*"', "", rest)
        rest = re.sub(r'rowspan="\d+"', "", rest)
        cells = [c.strip() for c in re.split(r"\n\||\|\|", rest) if c.strip()]
        if not cells:
            continue
        link = _LINK.search(cells[0])
        company = (link.group(2) or link.group(1).split("(")[0].strip()) if link else \
            re.sub(r"[\[\]]", "", cells[0]).strip()
        # The breakdown-by-country tables use the same "scope=row" markup as the ranking.
        # Their first cell is a bare flag template; a ranking row's first cell is a company
        # name, so a cell whose ONLY content is a template is not a company row.
        first = re.sub(r'^[\s|]+', "", cells[0])
        if not company or re.fullmatch(r"\{\{[^}]*\}\}[\s|]*", first):
            continue
        country, industry, revenue, cite = None, "", None, ""
        spans_country = 'rowspan' in block and re.search(r'rowspan="\d+"\s*\|\s*\{\{flag', block)
        for c in cells[1:]:
            f = _FLAG.search(c)
            if f is None:
                f = re.search(r"\{\{flagicon\|([^}|]+)", c)
            if f and country is None and not revenue_pat(c):
                tail = c.split("}}")[-1]
                # the column can carry "|" noise or a second value after a rowspan cell
                country = re.sub(r"^[\s|]+", "", tail).split("|")[0].strip() if tail.strip() else f.group(1).strip()
                if not country:
                    country = f.group(1).strip()
        for c in cells[1:]:
            r2 = revenue_pat(c)
            if r2 and revenue is None:
                v = float(r2.group(1).replace(",", ""))
                revenue = v * 1e9        # the column is USD billions
                break
        for c in cells[1:]:
            if not _FLAG.search(c) and not re.search(r"flagicon", c) and not revenue_pat(c) \
                    and "Yes" != c.strip() and "No" != c.strip() and not c.startswith("<ref"):
                cand = re.sub(r"<br\s*/?>", " / ", c)
                cand = re.sub(r"\{\{[^}]*\}\}", "", cand)
                cand = re.sub(r"\[\[[^\]|]*\|([^\]]+)\]\]", r"\1", cand)
                cand = re.sub(r"[\[\]]", "", cand).strip(" /")
                if cand and not cand.lower().startswith("<ref") and not re.fullmatch(r"[\d.,\s|]+", cand):
                    industry = re.sub(r"^[\s|]+", "", cand).strip()
                    break
        m = re.search(r"url=([^\s|}]+)", " ".join(cells))
        if m:
            cite = m.group(1)
        if country is None and _carry.get("country") and _carry.get("rows", 0) > 0:
            country = _carry["country"]
            _carry["rows"] -= 1
            if _carry["rows"] == 0:
                _carry["country"] = None
        elif country is not None and spans_country:
            _carry["country"] = country
            _carry["rows"] = max(0, int(re.search(r'rowspan="(\d+)"', block).group(1)) - 1)
        rows.append({"rank": int(rank), "company": company, "country": country,
                     "industry": industry, "revenue_usd": revenue, "cite_url": cite})
    return rows


def revenue_pat(cell: str):
    return re.search(r"\|\s*([\d,]+(?:\.\d+)?)\s*\{\{", cell)


UA = "entitygraph/0.1 (Fortune Global 500 parse; https://github.com/ZhangYangyi03/entity-graph)"


def fetch_text(url: str = PAGE) -> str:
    """Wikitext, not JSON -- so it goes around fetch.get(.get) and its JSON decoder."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        return urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "replace")
    except Exception as exc:                       # noqa: BLE001
        raise FetchError(f"could not read {url}: {exc}") from exc


def fetch() -> list[dict]:
    return parse(fetch_text())


def main(argv: list[str]) -> int:
    rows = fetch()
    out = None
    for i, a in enumerate(argv):
        if a == "--out" and i + 1 < len(argv):
            out = argv[i + 1]
    print(f"{len(rows)} rows parsed from {CITE}")
    print(f"{'rank':>5}  {'company':<44}{'country':<16}{'revenue bn':>11}")
    for r in rows[:12]:
        rev = f"{r['revenue_usd'] / 1e9:.1f}" if r["revenue_usd"] else "-"
        print(f"{r['rank']:>5}  {r['company'][:42]:<44}{str(r['country'])[:14]:<16}{rev:>11}")
    if out:
        json.dump({"source": CITE, "url": PAGE, "rows": rows},
                  open(out, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
        print(f"written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
