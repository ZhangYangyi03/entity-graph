# entity-graph

A company entity graph assembled only from public sources that need no paid API key, where
every node and every edge carries the URL it came from.

The point is not the drawing. It is that a claim like "Berkshire Hathaway holds 5.4% of
Apple" resolves to a filing, on a dated accession number, that you can open. An edge with
no source is refused by the model itself (`Graph.add_edge` raises), so the graph cannot
quietly accumulate the thing that makes entity graphs useless: assertions nobody can check.

## What it actually produces

Measured on 2026-10-23, `apple.graph.json` in this repository:

    $ python -m entitygraph.cli build --cik 320193 --format summary
    nodes 59  edges 58
    kinds: company=58, person=1
    rels:  holder_of=58

One edge, in full:

    src   holder:warren-e-buffett
    rel   holder_of
    dst   cik:320193
    attrs {form: SC 13G/A, date: 2021-02-16, accession: 0001193125-21-044816,
           shares: 907559761, percent: 5.4}
    source SEC EDGAR
    url   https://www.sec.gov/Archives/edgar/data/320193/000119312521044816/...

That is Buffett's stake parsed from the cover page of the filing itself, not from a
summary table.

## Sources, and why these

    source            needs a key?   what it contributes
    GLEIF             no            legal entity, direct parent, ultimate parent
    SEC EDGAR         no            subsidiaries (10-K Ex. 21), holders (SC 13D/13G cover
                                    pages), insider filings (Form 4)
    OpenSanctions     no (bulk)     sanctions matches
    OpenCorporates    YES, paid     deliberately absent -- see below

OpenCorporates was tried and dropped. Its free API tier no longer exists: a registered
account still shows "Get an API account", which leads to a pricing page whose cheapest
plan is GBP 2,250/year for 500 calls a month. The account is real; the free token is not.
Claiming otherwise would have meant special-casing a source that returns 401.

## Install and run

    pip install -e ".[dev]"
    python -m entitygraph.cli build --cik 320193 --format summary
    python -m entitygraph.cli build --name "Berkshire Hathaway" --format json --out bh.json
    python -m entitygraph.server          # {host}:8087/entity?name=...  /entity/{lei}

    pytest -q                             # 15 tests, no network

## Design choices that are load-bearing

Identifier joins, never names. `Resolver.by_id` matches on LEI / CIK / ISIN / FIGI.
`Resolver.candidates(name)` returns scored guesses and labels them `on="name"` -- a name
guess never silently becomes a merge. Name matching is token-set Jaccard after
normalisation, which is why `Société Générale S.A.` normalises to `societe generale`
(there is a test for exactly that, because the first version split `S.A.` into `s` and `a`).

Gaps are reported, not hidden. `build()` collects a `notes` list of every source that did
not answer, and the CLI prints it. A partial graph that says what is missing is worth more
than a complete-looking one that swallowed an exception.

The cache is on by default and on disk. `fetch.get(url, ttl=...)` stores under `.cache/`;
`ttl=0` bypasses. SEC 403s on a User-Agent without a contact address, so `DEFAULT_UA`
carries one -- and the measured shape matters: `entity-graph/0.1 addr` returns 200 while
`entity-graph/0.1 (url; addr)` returns 403.

The SEC cover-page parser is pinned by a recorded excerpt in `tests/test_holders.py`. A
silent upstream layout change is exactly how a graph starts inventing holders.

## What is missing

    subsidiary edges   only when the filer included Exhibit 21. Apple does not, so Apple's
                       graph has holder edges and no subsidiaries. The gap is reported.
    person identity    a person is keyed by normalised name, so two different people with
                       one name collapse. No person-level identifier is used from a free
                       source yet.
    non-US registries  France (recherche-entreprises), Norway (brreg roller), UK (494 MB
                       bulk snapshot), US state registries: endpoints verified, not yet
                       wired into `build()`.
    sanctions          wired but only ever adds a `sanctioned` edge on an OpenSanctions
                       hit; no fuzzy-matching discipline yet.

## Licence

MIT. Data belongs to the registries cited in each node's `source` and `url`.
