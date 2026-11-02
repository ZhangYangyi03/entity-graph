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


## Currencies, products and banks

The company graph answers *who owns whom*. This half answers the other banking question:
*which currency, which product, at what rate, in which country, from which bank*. Same rule
as everywhere else in this repository -- every node and edge carries the endpoint it came
from.

Measured on 2026-10-30, `banking.graph.json` (10.4 MB):

    $ python -m entitygraph.cli banking --format summary
    nodes 8192  edges 8295
    kinds: bank=4217, bank_holding=3318, country=422, currency=178,
           bank_product=56, feature=1
    rels:  licensed_in=4217, subsidiary_of=3536, legal_tender_in=277,
           has_metric=160, offered_in=76, rate_against=29

    source                 needs a key?  what it contributes
    SIX (ISO 4217)         no            178 official currencies: alpha code, numeric
                                         code, minor units, and the countries using them
    ECB eurofxref          no            29 currencies against the euro, 64 days of fixings
    ECB Data API (MIR)     no            euro-area bank rate on one product (house-purchase
                                         loans, new business), 21 country series
    Reserve Bank of Australia F5/F6  no  54 Australian lending products: small-business
                                         variable/overdraft, housing loans by lender type,
                                         rate type and borrower
    Bank of Canada Valet   no            prime business rate, daily
    World Bank (WDI)       no            lending and deposit rates for 160 countries
    FDIC BankFind          no            4,217 active US banks with assets, deposits, net
                                         income, charter, regulator, and holding company

Two questions the graph can answer, both from the file, offline:

    $ python -m entitygraph.cli query banks --top 5
    $ python -m entitygraph.cli query rates --country euro --top 5

The holding-company edge is a real join, not a name guess: FDIC publishes RSSDHCR, a
regulatory RSSD id, so `bank:fdic:10004` joins `bank_holding:rssd:1249691` on an
identifier. 2,264 of the 4,217 banks also carry an LEI, which is the same join key the
company graph uses.

### What this cost, and the two bugs worth naming

The ECB's MIR endpoint looks like it carries a dozen products. It does not: 11 of the 12
series keys (2110 through 2260) return 404 on the same dimension pattern, and only 2250
answers. Rather than invent siblings, the module reports the one product it can cite and
the breadth comes from RBA's F5/F6 tables instead. There is a test that pins this
(`test_mir_does_not_invent_product_keys`).

The country key is the other one. SIX writes registry-style names -- "UNITED STATES OF
AMERICA (THE)", "NETHERLANDS (THE)", "CONGO (THE DEMOCRATIC REPUBLIC OF THE)" -- while FDIC
says "United States". The first build produced 4,219 edges pointing at country nodes that
did not exist: every bank unlicensed-in, every MIR rate offered nowhere. The fix normalises
the decorations before the alias lookup, and the build now ends with an integrity note that
counts dangling edges, so this cannot come back silently:

    note integrity  ok  every edge has both endpoints

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

    pytest -q                             # 60 tests, no network

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

## Asia-Pacific: China, Taiwan, and what Japan/Korea actually require

| source | what it gives | status |
|---|---|---|
| Shanghai Stock Exchange | 1,604 listed companies: name, A-share code, board, listing date | live, keyless |
| Shenzhen Stock Exchange | 2,906 listed companies: name, code, industry, listing date | live, keyless |
| Taiwan MOEA open data | full registration by 統一編號: status, capital, paid-in, address | live, keyless |
| Qichacha Open Platform | 工商信息 + 股东 + 对外投资 + 法定代表人（企业/事业单位/国企） | live, needs `QCC_APP_KEY` + `QCC_SECRET_KEY` |

The Chinese exchanges publish rosters, not ownership. A Chinese listed company therefore
gets a node and a citation and no invented shareholders. Shenzhen pages at a fixed 20 rows,
so the full roster costs ~146 requests and is cached for 24 hours; Shanghai returns 25 rows
and only honours the page window when `beginPage` and `endPage` are both set to `pageNo` —
without them it replays pages 1..N and a paging loop silently accumulates duplicates.

Mainland-China *ownership* (shareholders, legal representative, outbound investments) comes
from the **Qichacha Open Platform** (`openapi.qcc.com`), the paid, licensed API — not a
scrape of the consumer site, which is deliberately blocked (see below). It answers by
company name or by 统一社会信用代码 and returns the legal person, org type, capital,
shareholders (with their percentages) and investments, so a state-owned enterprise or a
事业单位 resolves to a real ownership edge instead of a bare name. The AppKey and SecretKey
are read from `QCC_APP_KEY` and `QCC_SECRET_KEY`, never committed; without them the source
is skipped with a note, exactly like the Korean OpenDART source.

    $ QCC_APP_KEY=<key> QCC_SECRET_KEY=<secret> python -m entitygraph.cli build --name 天津协医 --country CN


Korea is in the project too, and it is the only source that needs a key. OpenDART issues
one free against an email; it is read from `OPENDART_API_KEY`, never committed. Measured on
삼성전자 (corp_code 00126380): profile with ticker 005930 and both CEOs, 2,782 filings in
2026, and 43 major-shareholder rows (지분공시), each citing its receipt number. Without the
key the source is skipped with a note rather than returning an empty list, because an empty
list and a missing credential look identical downstream and only one of them is honest.

Japan is now in too, as an ingest rather than a request path. The NTA publishes a monthly
full dump fetched by POSTing a form with a CSRF token; unpacked it is a 1 GB Shift-JIS CSV
of 5,837,980 legal entities, loaded into a 1.6 GB SQLite. Exact and suffix-tolerant name
lookup answer in 0-2 ms. It carries corporate number, name, kana, registered address and
prefecture, and no directors or shareholders at all -- a Japanese node from this source
must never grow officer edges.

EDINET remains out of reach: its API answers 401 without a reviewed subscription key, and
gBizINFO errors on the search endpoint. Mainland China's gsxt.gov.cn and
creditchina.gov.cn present self-signed certificates plus a JS cookie challenge, and the
consumer aggregator sites (tianyancha 418) block automated clients. These are recorded as
blocked rather than quietly dropped — the licensed Open Platform API above (Qichacha) is the
sanctioned route to the same underlying registry data.

## Non-US registries, and the UK index

Added 2026-10-23. Every adapter below was written against a response actually observed on
that date, not against documentation -- the field names here are the ones the services send.

    country  registry                        key?   officers?
    FR       recherche-entreprises (INSEE)   no     yes -- dirigeants, name + quality
    NO       Brønnøysund (Brreg) + roller    no     yes -- CEO, board, signatories
    DK       CVR via cvrapi                  no     no  -- the free endpoint returns owners: null
    FI       PRH avoindata v3                no     no
    CH       Zefix (POST search)             no     no
    CZ       ARES (lookup by IČO)            no     no
    BR       ReceitaWS (lookup by CNPJ)      no     yes -- qsa partners
    GB       Companies House bulk snapshot   no     yes -- persons with significant control

Lookup by name or by national id:

    $ python -m entitygraph.cli build --name Carrefour --country FR --format summary
    nodes 20  edges 14      # 14 officer_of edges from the French registry

    $ python -m entitygraph.cli build --registry-id 33000167000101 --country BR --format summary

### Why the UK is an ingest, not a request

The Companies House bulk feed decompresses to 2.8 GB of CSV for company records and ~2.5 GB
across 33 shards for PSC. That cannot sit behind a request, so it becomes a local SQLite
index and lookups cost an indexed SELECT:

    $ python -m entitygraph.ingest --db uk.sqlite --basic --psc-shards 33

Measured here: 5,704,703 companies and 499,971 PSC rows (shard 1 of 33) in a 720 MB index.
Name lookup is tiered -- exact, then prefix (both index-backed, 2-25 ms), then a
contains-scan only if neither hit.

Two things this cost, both fixed rather than worked around:

- A truncated download reported itself as `not a zip file`; the file is now checked against
  Content-Length and resumed with a Range request.
- Interrupting the load left `database disk image is malformed` on every later query. The
  load now runs in WAL with per-batch commits, so a kill costs one batch and the index
  stays valid -- `PRAGMA integrity_check` returns `ok` after each interruption.

## Time: what the graph looked like then

A graph with one copy of each relationship answers "who owns whom today". It cannot answer
"who owned whom in 2020", because the four years of filings that came after have overwritten
it. `entitygraph.history` and `entitygraph.timeline` add the missing half of the provenance
rule: every edge must say *when* it was true and what ended it, exactly as it already has to
say where it came from.

Measured on `apple.graph.json` (58 SC 13D/G edges, four signature dates 2019-02-14 →
2024-02-14):

    $ python -m entitygraph.cli history --graph apple.graph.json --between 2018-01-01 2025-01-01
    2018-01-01 .. 2019-02-14   edges    0  nodes    0
    2019-02-14 .. 2021-02-16   edges    5  nodes    6
    2021-02-16 .. 2023-02-14   edges   49  nodes   50
    2023-02-14 .. 2024-02-14   edges   57  nodes   58
    2024-02-14 .. 2025-01-01   edges   58  nodes   59

Four dates, four different graphs, every one of them reproducible from the filings the
edges already cite. `--as-of 2019-06-01` returns the five holders that were on the cover
pages in 2019 and nothing that arrived later.

Three rules make it honest rather than decorative:

    half-open intervals     [valid_from, valid_to). The day a superseding filing is signed
                            belongs to the new version, so two versions never both hold on
                            the same date.
    no invented dates       an edge whose source has no date mapping is left undated and
                            listed by `--check`. A made-up date is worse than a missing one:
                            it makes a slice look authoritative when it is not.
    overlap is an error     two versions of one triple live on the same date, the side table
                            raises. The archive contradicting itself is not something to
                            average away.

Why a side table. `Graph` keys edges by `(src, rel, dst)`, so loading four years of filings
into one throws away every version but the last. `SideTable` keeps them all and a slice pulls
the right one back in. `SideTable.contradictions()` names any triple whose versions overlap,
and `Timeline.segment()` walks the history change-point by change-point, so no step size has
to be guessed.

---

## Orbit: where the catalogue says things are, and how close they get

CelesTrak publishes mean element sets for everything in orbit. This module turns them into
positions and into a screening pass, and it turns the satellite catalogue into graph edges.

Two halves, and the second is the point.

The geometry is Kepler plus secular J2, written out in `entitygraph/orbit.py` with no
dependency, because a closed-form formula can be checked against the source and a numerical
integrator can only be trusted on reputation:

    orbit              altitude / period    textbook
    geostationary      35786 km / 1436 min  35786 km / 1436 min
    GPS                20183 km /  718 min  20200 km /  718 min
    ISS                  419 km / 92.95 min   420 km / 92.9 min

What it is not: SGP4. No drag, no short-period terms, no debris catalogue. It is a screening
pass, and any result says so with the sampling it used.

Run against 11,823 objects from five CelesTrak groups (starlink, gps-ops, geo, science,
stations), window ±30 minutes around the freshest element epoch, sampled every 120 s,
reporting everything within 25 km:

    4.483 km  2026-11-02T23:55:57Z  STARLINK-35792  <-> STARLINK-35841
    7.325 km  2026-11-02T22:55:57Z  STARLINK-4663   <-> STARLINK-34446
    8.204 km  2026-11-02T23:03:57Z  STARLINK-3781   <-> STARLINK-32808
    8.705 km  2026-11-02T23:55:57Z  STARLINK-5462   <-> STARLINK-5467

    16 pairs within 25 km. 1 km: none. 10 km: three.

Every one of them is a Starlink. That is not a filter: 11,156 of the 11,823 objects are
Starlink, and a constellation of eleven thousand satellites in a handful of shells will
dominate any measurement of who is near whom.

Three things the first attempt got wrong, kept here because each one is the sort of error
that produces a confident wrong number:

    the sieve that was too fine   Bucketing candidate pairs on (inclination, RAAN, argp) at
                                  0.5 degrees returned 914 pairs out of 11,823 objects and
                                  found zero approaches. Planes are not that precise. Whole
                                  degrees keeps the shells intact and the count at 53,281.
    the duplicate that was not    Dock modules are published with bit-identical elements and
                                  propagate to distance zero forever; the ISS and its two
                                  modules produced 17 phantom "0.000 km" events. They are now
                                  excluded by an exact comparison, and by nothing looser.
    the finding that looked like  The obvious next move was to widen that duplicate test to
    the artefact                  0.15 km and make the suspicious Starlink pairs disappear.
                                  Measured first: those pairs differ by 3 to 8 km of
                                  semi-major axis with epochs hours apart. That is two
                                  satellites drifting in one shell -- a real approach. The
                                  tolerance stayed at bit-exact and the finding stayed.

Then the graph. `satcat` carries OWNER, LAUNCH_DATE, OBJECT_TYPE, apogee and perigee for
17,376 objects, which is enough to hang satellites off the entity graph:

    on orbit 17376   types: PAY=17372 R/B=4
    top owners     US=13101  PRC=1578  UK=700  CIS=378  JPN=149  FR=78  IND=78
    top operators  SpaceX=11184  Eutelsat OneWeb=651  Shanghai Spacecom=257  China SatNet=252

    python -m entitygraph.cli orbit catalog --elements ... --satcat satcat_active.json

An edge from a satellite to a company exists only where the catalogue's OWNER code names an
operator, or where a declared series mapping does. `TBD` -- 152 objects whose owner is not
established -- gets no edge at all. Manufacturer and launch provider are not in this source,
so no edge is invented for them; the launch date the catalogue does state becomes
`valid_from`, which is what lets these satellites enter the timeline.

---

## Military and dual-use satellites: what the orbit says about the label

The catalogue names things. Those names carry claims -- a satellite called SBIRS is a missile
warning satellite, one called AEHF is a protected communications satellite. The catalogue
also states an orbit, independently of the name. Two claims, one check:

    python -m entitygraph.cli orbit military --satcat satcat_active.json

    980 declared military / dual-use objects   (19 with no orbit stated)

    role                     count   bands
    US military designation    523   LEO=390 GEO=67 MEO=48 ?=18
    reconnaissance             167   LEO=164 GEO=1 MEO=2
    navigation                  86   MEO=62 GEO=23 LEO=1
    experimental                62   LEO=56 GEO=4 MEO=2
    remote sensing              60   LEO=57 GEO=3
    signals intelligence        28   GEO=22 LEO=4 MEO=2
    protected comms             10   GEO=10
    wideband comms              10   GEO=10
    missile warning              8   GEO=8
    mobile comms                 5   GEO=5

    orbit disagrees with declared role: 0

A zero that means something, because three disagreements were found and fixed first:

    RIGIDSPHERE 2 (LCS 4)   matched "DSP" as a substring and was audited as a missile-warning
                            satellite in low orbit. Matching a three-letter key anywhere in a
                            name is a claim this project should not make; the entry was
                            narrowed to "DSP 20" .. "DSP 23".
    SAPPHIRE                declared missile warning, flies at 780 km. Canada's Sapphire is
                            a space-surveillance sensor for the US Space Surveillance
                            Network, not a warning bird. The declaration was wrong, the
                            orbit was right.
    YAOGAN-41               declared reconnaissance, sits at 35,800 km. Checked instead of
                            assumed: Yaogan-41 really is a geostationary stare, and the
                            family spans LEO imagers and GEO watches both. The role's
                            compatible bands were wrong, not the satellite.

19 objects have no orbit in the catalogue at all -- apogee and perigee zero. They are
counted as "?" and excluded from the comparison, because an object with no stated orbit can
neither agree nor disagree, and calling its band "LEO" would be inventing the number.

What this comparison cannot do is separate a military satellite from a civilian one. It
reads the published name. A satellite with no descriptive name -- "USA 555" -- is a US
military designation and nothing more; where it flies is stated, what it does is not.

---

### The same graph through the timeline

`satcat.graph.json` (17 MB checked in) is that catalogue as an entity graph: 17,419 nodes and
29,488 edges, every edge dated with the launch date the catalogue states. Because the edges
carry dates, the time machine works on satellites without any new code:

    $ python -m entitygraph.cli history --graph satcat.graph.json --as-of 2021-06-01
    nodes 2233  edges 3071
    kinds: company=11 country=28 satellite=2194
    rels:  operated_by=1018 owned_by_state=2053

    $ python -m entitygraph.cli history --graph satcat.graph.json --as-of 2025-06-01
    nodes 10740  edges 18606

Finding that took a bug fix in the history loader: it looked up the source's default date
attribute and ignored the `valid_from` an edge already carried, so all 29,488 satellite edges
were reported as undated and every slice came back empty. An edge that already says when it
starts does not need a rule to tell it.

### The curve, and the caveat that makes it worth reading

    year   new   PRC    US   CIS  other   cumulative (PRC / US)
    2015    19    11     7     0      1     175  (61 / 89)
    2018    46    34    10     2      0     256  (111 / 116)
    2021    44    37     6     0      1     367  (189 / 142)
    2023    51    41     6     3      1     491  (272 / 175)
    2024   146    28   117     1      0     637  (300 / 292)
    2026   188    31   153     4      0     966  (371 / 543)

2026 is a partial year and is undercounted; the file's freshest element epoch is
2026-11-01, so it covers ten months.

Read as a census of what is up there now, arranged by launch date, this is one of the
clearest things in the repository:

    China's cumulative declared military/dual-use count overtakes the United States' in
    2019 (131 vs 120) and stays ahead until 2024 -- driven by Yaogan (167 objects) and other
    named series.

    The United States went from 6 declared launches in 2021 to 117 in 2024. That is a change
    in the cataloguing, not a claim about the sky: the objects are "USA 350" through
    "USA 725", sequential designations with no descriptive name, which is what the US
    publishes when it does not want to say what a satellite is. China names its satellites,
    the US numbers them, and a count of names therefore counts two different things.

Both statements are true and neither is the whole story, which is why both are printed. The
sweep that produces them is declared-name only, and the last section of this README says
where that stops.

### The gap to military satellite AI

This project does not do any of the following, and no amount of data volume would make it:

    sensor fusion       turning raw imagery, SIGINT or radar returns into tracks and
                        identifications. The catalogue has no pixels in it.
    orbital-edge AI     orbit determination and conjunction prediction on high-fidelity
                        propagation with the full debris catalogue. What is here is a
                        screening pass at 120 s resolution, and it says so.
    tactical inference  what a constellation is FOR. "YAOGAN-41 sits in GEO" is a checkable
                        statement; "YAOGAN-41 is watching the Pacific Fleet" is not, and is
                        not derivable from these sources.

What it does instead is the part that can be audited: who owns what, when it went up, where
it is, and how close things get -- with every edge carrying the URL it came from and every
refusal stated. That is an entity-graph project about space, not a military AI, and the
distinction is the point.
