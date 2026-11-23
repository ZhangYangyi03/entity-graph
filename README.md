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

## SGP4, and the 4.483 km that was not there

The first version of `orbit.py` propagated with Kepler plus secular J2, no drag. On the
2026-11-02 catalogue it reported the closest approach in the sky:

    4.483 km   STARLINK-35792 <-> STARLINK-35841   2026-11-02T23:55:57Z

SGP4 puts the same pair 1,937 km apart at that instant. One of the two is wrong and there is
no guesswork about which. Their mean motions differ by 0.0130 rev/day, so they drift apart in
phase at 4.7 degrees per day and would take 77 days to line up -- while the drag term on the
first one (ndot 0.0199 rev/day^2, a hundred times the catalogue median; both objects are low
and decaying) changes its mean motion by 0.0307 rev/day over the 37 hours of extrapolation,
which is 2.4 times the gap between them. A propagator that holds the mean motion constant
therefore invents a meeting that the drag term takes away. That is the whole mechanism.

So SGP4 is the default now. It is vendored in `entitygraph/_sgp4` -- MIT, from the `sgp4`
package, which wraps Vallado's official C++ -- because `entity-graph` has kept its dependency
count at zero and this does not have to break that. Nothing in it is edited except the import
paths. It earns the default by reproducing the published verification vectors rather than by
reputation:

    $ python -m pytest tests/test_sgp4_ver.py -q
    4 passed

    700 lines of tcppver.out compared field by field at 2e-7 km
    33 verification cases, including the 7 that are supposed to fail
    worst deviation across all 667 propagated samples: 1.153e-07 km

Rebuilding the TLE from CelesTrak's GP JSON took three tries and each failure is a test now:
the designator is compressed (`1998-067A` -> `98067A`, 8 columns), catalogue numbers above
99999 use alpha-5 (`100408` -> `A0408`), and BSTAR's mantissa is `0.NNNNN` so its exponent is
one higher than the scientific one (`0.00011739` -> ` 11739-3`). All 11,823 objects in the
sweep rebuild and re-parse; the number round-trips through the parser.

### What the two propagators disagree by

    $ python -m entitygraph.cli orbit compare --elements gp_stations.json

    elapsed   median km   p90 km   max km
      0.00d      11.75    11.75    12.30
      1.00d      60.08   318.20  1197.23
      7.00d     540.01  4019.84 12818.24

The disagreement is already 11.75 km at the elements' own epoch, before any extrapolation.
That is what "no short-period terms" means in kilometres: rotating the J2 position about the
orbit normal by 0.1 degrees -- 1.55 seconds of travel -- collapses the gap to 0.75 km, so 11
of those kilometres are along-track phase and 0.75 km is radial. The closed form is fine for
"which shell is this in" and not fine for "how close did these two get", which is why it is
no longer the one answering the second question.

### Refinement, not sampling

Sampling reports the nearest sample, which is always farther than the real minimum. Measured
on the surviving real events, 120 s sampling overstated the minimum by 0.008 to 3.592 km --
the same size as the distances being reported. `screen()` now collapses consecutive in-
threshold samples of one pair into one event and runs a golden-section search to the actual
minimum, and prints both numbers so the step's error is visible rather than assumed small.

    $ python -m entitygraph.cli orbit close --elements gp_starlink.json ...
    objects 11823  candidates 53281  window +/-30min  step 120s  threshold 25km  propagator sgp4
    close approaches: 8  (each sampled minimum refined to the real minimum)
        4.168 km  (step said 4.168, +0.000)  STARLINK-2319 <-> STARLINK-33745
        4.171 km  (step said 4.179, +0.008)  STARLINK-4663 <-> STARLINK-34446
        7.353 km  (step said 7.950, +0.597)  STARLINK-5462 <-> STARLINK-5467
       10.978 km  (step said 14.570, +3.592) STARLINK-2386 <-> STARLINK-3153

A full sweep of 11,823 objects is 36 seconds.

## 脑机接口 6+1 与生物制造产业图谱

`industry.graph.json` 是《生物制造、脑机接口领域产业图谱研究项目实施方案》要的那张图：
以脑机接口「6+1」七条技术路线为骨架，把实测数据源的证据按关键词挂到路线上，再标
产业链环节（上游材料器件 / 中游系统装备 / 下游临床应用）与四链（产业链、创新链、人才链、
资金链）。

    $ python -m entitygraph.cli industry --out industry.graph.json
    industry: nodes 82  edges 85
    kinds: bci_direction=7  bio_domain=5  chain_stage=3  dataset=29  company=7 ...
    rels:  work_on=20  relevant_to=27  counts_toward=8  covers_stage=3 ...

三条命令各管一段：

    python -m entitygraph.cli bci     --limit 4      # 数据集/试验/论文/器械
    python -m entitygraph.cli biomed  --symbol EGFR --drug imatinib   # 靶点→药→标签→不良事件
    python -m entitygraph.cli industry                # 把上面两段按 6+1 与四链标注

七条路线（`entitygraph/directions.py`，每条带实施方案要求的技术性能参数）：
神经调控、神经修复、微针电极、生物人工脑盖骨双向接口、脑深区磁声成像、脑控装备、+1 类器官/片上脑机接口。

数据源，全部免 key，2026-11-23 在本机实测：

    source              访问方式   贡献
    OpenNeuro           GraphQL    BIDS 数据集（EEG/MEG/iEEG/fMRI）
    DANDI               REST       NWB dandisets，细胞电生理
    NeuroVault          REST       1.8 万+ 统计图谱
    PhysioNet           HTML       经典 EEG 语料（eegmmidb 等），无 API，解析页面
    BNCI Horizon 2020   HTML       BCI 专用目录 34 套，带受试者/信号数
    ClinicalTrials.gov  REST v2    在研器械与干预试验
    PubMed E-utilities  REST       论文
    openFDA 510(k)      REST       已上市 EEG/BCI 器械与申请人
    Open Targets        GraphQL    靶点—疾病关联（打分）
    ChEMBL / UniProt / PubChem / RxNorm / DailyMed / Europe PMC / bioRxiv / openFDA 药品
                                  靶点→化合物→药物→标签→不良事件

明确试过并放弃的两个，写在这里免得下次当 bug 重试：

    NIH RePORTER (api.reporter.nih.gov)  自签证书链，本机信任库与 certifi 都拒绝。
    RCSB PDB (search.rcsb.org)           同上，"unable to get local issuer certificate"。
    两者都是真数据，但要用就得关掉证书校验——这个仓库不做这个交易。

图谱的规矩照旧，一处没松：**每一条边都带它来自哪个 URL**。证据节点里的
`bci6plus1` 标签还额外带 `tagging` 说明——一个关键词都没命中的记录会被标成
"no 6+1 keyword matched; left untagged rather than guessed"，而不是硬塞进某条路线。

### 这张图现在缺什么（缺就是缺，不装作有）

    企业节点     6+1 的企业（脑虎科技、阶梯医疗、博睿康、华大智造、金斯瑞、凯莱英）
                 已从实施方案里落成节点并注明出处；但要按工商/融资维度铺开，需要企查查
                 或 GLEIF 付费面，本机没有 key。
    专利         FTO 与专利许可调研要智慧芽/Incopat 或付费 API，没有免 key 全库。
    资金链       NIH RePORTER 本可用（项目、经费、机构），倒在证书上（见上）。

## 商业价值层：主题的市值口径与披露证据

技术图谱本身不卖钱，能卖钱的是「这个主题在资本市场上有多少家、值多少钱、谁真披露过」。
`entitygraph/market.py` 就是这一层，全部免 key、本机实测（2026-11-23）：

    source                  访问方式        贡献
    同花顺 概念板块           HTML (gb18030)  板块目录 361 个 + 成分股全名单（翻页到底）
    腾讯行情 qt.gtimg.cn      REST           总市值/流通市值/PE(TTM)/PB/换手率，60 只一批
    巨潮资讯 cninfo          REST            公告全文检索（标题+代码+PDF 路径），自动翻页

    $ python -m entitygraph.cli industry       # 建图，市场层默认开启
    $ python -m entitygraph.cli brief          # 生成《产业图谱价值快照.md》

最近的实测结果（数字是跑出来的，不是估的）：

    板块            成分股   有报价   合计总市值(亿)   板块内中位数(亿)
    脑机接口            50      50        6925.6            60.7
    阿尔茨海默概念       36      35        4565.4            95.0

    去重上市公司 84 家，合计 11373.6 亿元，中位数 71.2 亿元，
    前 10 家 5125.1 亿元 = 45.1%（集中度）
    巨潮标题命中「脑机/脑机接口」的披露 82 条，涉 17 家上市公司

快照里按市值排出的前几名（同花顺脑机接口板块）：亿纬锂能 969.4 亿、科大讯飞 825.4 亿、
世纪华通 771.7 亿；阿尔茨海默概念侧：上海医药 599.3 亿、复星医药 542.1 亿。

规矩照旧，市场层尤其不能松：板块成分是**市场归类**，不是技术判定。一家公司在脑机接口
板块里，不等于它有脑机产品，所以这类边一律 `basis="板块归属，非技术判定"`，在报告里单独
计数，不和技术证据的边混在一起。巨潮只索引标题/正文，实测命中量很小，披露条数是下限
不是上限，图谱里写的是原样条数。

明确放弃的三个，写下来免得下次当 bug 重试：

    东方财富 push2        本会话后期对本机直接抛 RemoteDisconnected，加了浏览器 UA、
                          ut 参数、82./17. 分片都不行，于是不作为依赖。
    交易所公告接口         巨潮 sse_stock.json 404、szse irm 405，不通。
    新浪行情 hq.sinajs    403，无 referer 白名单。

真正的缺口（不是懒，是没有免 key 的路径）：

    专利族/FTO/许可     需智慧芽或 Incopat 付费库
    非上市公司估值      脑虎科技、阶梯医疗、博睿康等没有公开市值，只能作为技术主体出现
    一级市场融资轮次    需要 IT桔子/企名片一类数据，无免 key 接口

## 可转让成果清单（6+1 每条路线手上有什么牌）

`entitygraph/deliverables.py` 把图谱按七条路线切开，回答成果转化会上真正会被问的那句：
「这条路线，我们现在能拿出去谈的东西有几样、分别是什么。」

    $ python -m entitygraph.cli industry      # 先建图
    $ python -m entitygraph.cli deliverables  # → 可转让成果清单.md + 成果清单.json

计分口径写在报告第一段，因为它必须能被反驳：

    证据类型            权重   在成果转化里代表什么
    器械注册/上市许可      4   合规锚点，可作许可与合资标的
    在研临床试验          3   在研项目，可按适应症/中心早期授权
    企业技术主体          2   产业化载体，直接对接的承接方
    科研承担单位          2   技术出让方，职务成果的所有权一侧
    论文/方法学           1   可授权 know-how，专利布局基础
    数据集                1   数据资产，算法类成果的载体
    上市公司披露          1   资本市场证据，已有主体押注
    上市公司(板块)         1   潜在买方（板块归类，非技术判定）

**score 是证据密度，不是估值。** 一条路线得分 = Σ(条数 × 权重)，衡量「手上已经有几张牌」，
不衡量牌值多少钱。市场市值单独成列，标「资本关注度，不计入得分」——一条边 6925 亿市值
不会把技术得分抬高一分，这条有测试守着。

最近一次实测跑的排名：

    1  脑控装备              28  42.4%   博睿康已持证 + 15 个 BCI 数据集 + 在研试验
    2  微针电极              19  28.8%   器械注册 + 企业主体 + 数据集
    3  生物人工脑盖骨双向接口     6   9.1%   同济医院颅骨改性 + 企业
    4  神经调控               4   6.1%
    5  脑深区磁声成像           4   6.1%
    6  神经修复               3   4.5%
    7  +1 类器官/片上脑机接口     2   3.0%   海河实验室片上脑机接口

跑这轮时逮到一个真 bug，值得单独记：巨潮一条公告标题是「针织袜机**电脑控**制系统」，
被 `脑控` 关键词命中，把一家袜机厂塞进了脑控装备。修法不是删短词，而是给短 CJK 关键词
加前字黑名单（`directions.CJK_FALSE_FRIENDS{"脑控": ("电",)}`），并在清单里给弱命中
打上「弱命中：关键词」标记。这类误命中不修，图谱越扩越脏。

清单末尾照旧列出它回答不了的三件事：FTO（无专利族数据）、非上市公司估值、数据集穷举度。

## 德语 / 法语（以及中文、日文标签）

之前所有源都是英语的。这一层补上德语和法语——德国是 DRKS/GND/DFG 那一套，法国是
HAL/SIRENE/Bpifrance 那一套，两边都用母语发布。只用英语扫，等于把这两个市场删掉。

    $ python -m entitygraph.cli multilingual --rows 6

同一个概念只有一个节点，靠 Wikidata 的多语言标签挂在上面：

    en  brain–computer interface
    de  Brain-Computer-Interface
    fr  interface neuronale directe
    zh  脑机接口
    ja  ブレイン・マシン・インタフェース

实测（2026-11-23，本机）：

    source                        访问    结果
    Wikidata 多语言标签            REST    Q897410，一实体五语言
    HAL（法国开放档案）            REST    主题命中 1123 篇；lang_s:fr 过滤生效
    GND（德国规范档 / lobid）      REST    150 条，含企业（如 g.tec，Utrecht，成立 2015）
    recherche-entreprises（法国）  REST    SIRENE 企业库，按自由词查
    Europe PMC LANG 过滤           REST    ger 3 / fre 1 / chi 174 篇

「只在英语里找过」这件事现在是个可以量化的数字：语言切片节点把每种语言的命中数
写进图里，`language_report()` 直接打出来。

明确不通的四个，写下来免得下次当 bug 重试：

    DNB SRU (services.dnb.de)      403，需要账号
    CTIS  (euclinicaltrials.eu)    403，需要 API key
    OpenAIRE (api.openaire.eu)     本机 TLS 握手超时
    CORDIS 检索 API                500

DRKS（德国临床试验注册）记为**已知缺口**，不是填出来的行：它的检索是 POST 表单，
GET 只返回空壳。我给它写了检测和一条 gap 说明，但没有编造任何一条试验记录——
这个仓库的规矩是宁可空缺，不编数据。

计分与作图规矩同前：每条边带 lang 与 basis，德语边的 basis 写「GND 检索命中」，
不会因为语言不同就被算成技术证据。

### 再加日文与韩文

    source            访问          实测命中（本机 2026-11-23）
    J-STAGE（日本）    Atom API      brain-computer 14673 条；"brain-computer interface" 2853 条
    CiNii（日本）      OpenSearch    4287 条（按已加引号的短语查为 4315 条，同一库两种写法）
    KoreaMed / KCI     网页          **不通**：KoreaMed 检索页无结构化结果，KCI 需 POST 且返回难以解析

`python -m entitygraph.cli multilingual` 现在一次跑完：英/德/法/中/日 五种语言标签，
德法日三个区域的数据源，语言切片节点把每种语言的档案命中数写成图里的数字（不是只留取样）。

```
  ger  语言文献命中    3 条（本图取样 3 条）
  fre  语言文献命中    1 条
  chi  语言文献命中  174 条
  ja   J-STAGE（日本）    语言文献命中 2853 条（本图取样 5 条）
  ja   CiNii（日本）      语言文献命中 4315 条（本图取样 5 条）
```

韩国留空：KoreaMed 和 KCI 两个入口都测了，都拿不到可结构化解析的记录，于是写成
一条 gap 说明——`publication_ko` 节点一条都没有，测试守着这一点。宁可空着，不编。

## 瑞典 / 丹麦 / 芬兰 / 斯洛文尼亚

    $ python -m entitygraph.cli nordic --rows 4

四国各有一个可以复核的数字，这是这一层的全部意义——「我们查过瑞典」不是证据，
一个能一条查询重跑出来的计数才是：

    国家        来源                          实测命中
    瑞典        Europe PMC AFF:"Sweden"        68 篇 BCI 论文
                Libris XL（国家联合目录）       24107 条记录
    丹麦        Europe PMC AFF:"Denmark"       134 篇
                cvrapi.dk（CVR 工商登记）       Neuro Recovery ApS，CVR 44435616，8200 Aarhus N，NACE 869900
    芬兰        Europe PMC AFF:"Finland"       60 篇
    斯洛文尼亚  Europe PMC AFF:"Slovenia"      19 篇

顺带覆盖挪威（不在四国里，但接口通了，就没理由装看不见）：
api.nb.no 国家书目 18479 条，挂在 country:NO 上，**不**并进丹麦的计数里——
把挪威国家库算成丹麦的国家目录，那个丹麦数字就成假的了，测试守着这一点。

实测不通的七个，写进代码里的 DEAD_ENDPOINTS，免得下次重跑：

    DiVA（瑞典高校档案）        TLS 握手超时，两次
    Finna（芬兰国家库）         403，要 API key
    DTU Pure（丹麦理工）        TLS EOF
    SICRIS（斯洛文尼亚科研登记）  502 Bad Gateway
    OpenScience.si / dLib.si    页面能开，但拿不到可结构化记录
    Swepub                      404，旧 API 已下线
    data.norge.no               404

丹麦那家企业带一条 caveat：它是按关键词 neuro 命中的登记记录，没有核验它是否真做
脑机接口。关键词命中不等于业务相关，这条不删。

Europe PMC 在本机 TLS 会间歇性 EOF，所以它单独给了 5 次重试 —— 这是实测行为，不是保险。

## 全世界 249 个国家，一个不落

    $ python -m entitygraph.cli world

骨架是 ISO-3166 官方 249 个两位代码（不是「两百个左右」），清单本身带快照
`tests/data/iso3166_snapshot.json`，断网时也照样出全图，快照里写着抓取日期。

    国家节点        249   一个不少，包括 Antarctica、Vatican、Nauru、Tuvalu、Tokelau
    论文切片        249   每国一条，0 也写 0
    企业切片        498   每国两条（关键词 brain / neuro）
    区域/次区域/收入  6 / 17 / 4   World Bank 与 ISO 给的，是骨架不是证据
    国家间论文合作   91 条  Europe PMC 双国机构名同现，带篇数

跑出来的分布（2026-11-23 实测）

    有 BCI 论文的国家   122
    论文数为 0 的国家   127   —— 全部列在报告里，不留空行
    有 GLEIF 命中企业的国家  34（110 家实体）
    取数失败            0

论文数前五：中国 3767、德国 950、美国 803、意大利 612、加拿大 483。

国家之间的关系，三种，分清楚哪种是骨架哪种是证据

    关系              来源                    性质
    belongs_to_region       World Bank       骨架：地理归属
    belongs_to_subregion    ISO-3166         骨架
    shares_income_band      World Bank       骨架：收入层
    co_author_country       Europe PMC       证据：两国机构名同现于同一批论文

合作前五：德国–意大利 98、中国–美国 97、中国–日本 82、中国–德国 78、德国–美国 75。

三条实测出来的坑，都写进代码和报告，不当成 bug 反复踩

    OpenAlex        api.openalex.org 每次都 429（连带 mailto 的 polite pool 也是），本机不可用
    Europe PMC      AFF 用两位代码会掉六分之五：「SE」11 篇对「Sweden」68 篇，「CI」2 篇对
                    「Ivory Coast」0 篇。所以一律按国名查，**所有计数都是下限，不是总数**
    ISO 上游漏项    Antarctica 与 Taiwan 两行没有 region；我没有替它猜一个大洲，标成「未分类」，
                    测试断言只有这两行是未分类

一条规矩：论文数为 0 的国家不连边。空集合不是关系。

## 从"猜"到"字段"：机构层与跨国试验

前三项里最关键的一处：在此之前，一个国家有多少脑机接口论文，是靠**在国家名出现在机构
字符串里**数出来的——所以那些数全是下限。这一层换成记录自己声明的字段。

    $ python -m entitygraph.cli institutions

ClinicalTrials.gov v2（国别、申办方都是字段，不用猜）

    试验          275 条，两页翻完
    涉及国家      33 个，节点用 country:<ISO2> 落地
    跨国试验      3 条，国家间边 trial_spans_country
                  CH–US（NCT03379532）、IE–GB（NCT03827187）、IL–US（NCT04050696）
    试验前五      美国 94、中国 30、新加坡 22、法国 18、瑞士 9

ROR：机构名 -> 一个注册 ID、一个国家、一个类型

    申办机构      156 个
    结构化匹配    90 个（例如 University of Michigan -> ror.org/00jmfr291 / United States）
    显式不匹配    66 个，保留原名、标 unmatched，**不猜归属**

匹配规则只有一条，而且是踩过坑之后收紧的

    规则：去掉机构名里的连接词后，词集合必须完全相同。

    为什么不用相似度：先写了 Jaccard≥0.7，实测放过了
    「National University Hospital, Singapore」≈「National University of Singapore」——
    一家医院和一所大学，相似度 0.75，会默默把 21 家试验记到错误的机构上。
    也拦得住「BrainQ Technologies」→「ARIA Technologies」（法国另一家公司）、
    「Axoft, Inc.」→「Instituto Nacional de Cancerología」。

    为什么不能更严：ClinicalTrials.gov 写「University of Aarhus」，ROR 写「Aarhus University」，
    同一所学校，词集合相同只是词序和 of 不同——纯字符串相等会漏掉它。

年度曲线（宽口径，说清楚是哪一种计数）

    2010: 416   2015: 936   2020: 1639   2023: 2241   2025: 3219
    口径：("brain-computer interface" OR "brain computer interface" OR "BCI"
           OR "brain-machine interface")，逐年算，含缩写与复数
    精确短语 2025 年是 1724，宽口径是 3219 —— 两个数都记在 concept:BCI 节点上，
                差距摆出来，不挑一个好看的说

OpenAlex 依然不可用：api.openalex.org 每次 429（带不带 polite-pool mailto 都一样，
2026-11-23 又测了一次）。它本可以用 institutions.country_code 直接消掉下限，但没有就是没有；
拿"碰巧成功的那一次"当真数比留个缺口更糟。

## 五张图并成一张：现在能问出单个图问不出的问题

    $ python -m entitygraph.cli merge

    合并后 1913 节点 / 2627 边（world + institutions + nordic + multilingual + industry）

并集能成立，是因为五张图从一开始就约定：国家永远是 `country:<ISO2>`，概念永远是
`concept:BCI`。合并是按 key 求并集，不是模糊对齐。

补上的那条线，原来根本画不出来

    上市公司 -> 国家  85 条 listed_in_country
    这个仓的行业图知道一只 A 股是中国的公司，却没有边，因为当时没有国家节点可指。
    现在有了，边就按它**上市的市场**画（"market": "A股" 是记录里的字），
    边上注明「按上市市场，不是经营地」——不把上市地偷换成经营地。

于是第一次能问：哪些国家是三条独立测量同时指向的？

    德国 论文 950   试验  5  企业命中 37
    美国 论文 803   试验 94  企业命中 12
    意大利 612 / 6 / 22      加拿大 483 / 9 / 1      法国 406 / 18 / 20
    印度 375 / 1 / 60        西班牙 351 / 3 / 17     英国 343 / 7 / 35
    澳大利亚 340 / 3 / 2     奥地利 315 / 3 / 5      瑞士 253 / 9 / 4      比利时 165 / 2 / 12

    有论文又有试验的国家 29 个；只有论文没有试验的 93 个；三源同时齐全 16 个。

这三列是三种不同的测量方法（论文按机构国名=下限、试验按地点字段=总数、企业按 GLEIF
关键词命中），并列而不是相加——它们的证据强度不一样，加起来就是一个假精度。
## 商业 / 政治 / 金融三层，全部落在同两个主题上

要求是"国家之间不仅学术联系，商业、政治、金融也要整合"，同时"针对脑机接口和生物医药那俩
主题"。所以三层都不是泛泛的国家关系，每一层都缩到主题口径上：

    层     关系                     口径与判据
    商业   bci_biopharma_exports_to  HS 9018 医疗器械 / 9021 假体与神经刺激 / 9027 诊断仪 / 3004 药品
            bci_biopharma_imports_from  UN Comtrade，按报告国-伙伴国双边额（USD）
    金融   market_comoves_with       单国 MSCI 指数 ETF 的一年日收益皮尔逊相关（同为 iShares）
            moves_with_bci_biopharma_sector 该国股指与 XBI/IBB/BBH/FBT/IHI/IXJ 生物医药板块的相关
    政治   has_diplomatic_relations_with  Wikidata P530 有向建交
            votes_with               联大点名投票的理想点距离，一致性 = 1 - |Δ|/2，阈值 0.75

边上的 attrs 一律带值、年份、依据（basis），因为"相关"和"资本流动"是两件事，混在一张图上
就是把结论悄悄换成证据。官方双边金融流量（BIS/IMF CDIS/OECD FDI）在本机三个源全部不可达
（406/500/406），所以金融层用股指同步性代替，并在 gaps 里明说它不是资本流——这条限制写进
README 而不是藏在代码里。

同时修掉三个会静默出错的真 bug，每个都是"看起来有数据，其实是空的"：

1. Comtrade 报告国码。参考清单里 841 是 "USA and Puerto Rico (...1980)"，entryExpiredDate
   1980-12-31；842 才是美国。原来的字典后写覆盖先写，取到 841，于是美国对 2024 年的查询返回
   零行——全世界的贸易都在，只有美国那一整块没了。现在过期条目不覆盖生效条目。
2. 500 行截断。公共预览接口单次最多回 500 行，一国四个 HS 码一起查正好卡在 500，出口和进口
   被截掉一半而 status 仍是 200。改成按 flowCode 分开取（同一对国家 202 + 130 行），并把
   "返回恰好 500 行"当成截断而不是数据。
3. 联大阈值。一致性 > 0.5 会留下 11252 条边，占全部国家对 61%——"多数国家多数时候意见相同"
   把其它每一层都淹掉了。改到 0.75，边变成"投票站在一起"，丢掉的 10796 对写在 gaps 里。

复现：

    python -m entitygraph.cli geo --out all.graph.json          # 只吃缓存，秒级，不烧额度
    python -m entitygraph.cli geo --out all.graph.json --fetch-trade   # 联网补取
    python tools/fetch_theme_trade.py                            # 后台慢慢补（公共额度限流）
    python -m pytest -q                                          # 235 项，含三层口径的判据测试

已知残缺：Comtrade 公共预览对前两个报告国就开始 403 Quota Exceeded，所以主题贸易目前只有
美国（91.5 亿美元出口）和中国（18.4 亿）两个报告国的双边明细，共 665 条边；其余国家在
gaps 里点名，不留假覆盖。后台脚本会按限额节奏补齐，缓存写盘所以断点续取。


