"""Non-US company registries, one interface, every one of them keyless.

Each adapter turns a national registry into the same small record:

    {"id", "name", "country", "address", "kind", "officers": [...], "attributes": {...}}

so `build()` does not grow a branch per country. Every adapter returns its own `url` so the
node keeps its citation -- a French company must not lose its INSEE link because it ended up
in the same graph as a Norwegian one.

Written against responses actually observed on 2026-10-23. Where a field name was guessed
rather than seen, the adapter does not read it.
"""
from __future__ import annotations

import json
import re

from .fetch import FetchError, get, q

RECORD_KEYS = ("id", "name", "country", "address", "kind", "officers", "attributes", "url")


def _rec(**kw) -> dict:
    r = {k: kw.get(k) for k in RECORD_KEYS}
    r["officers"] = kw.get("officers") or []
    r["attributes"] = kw.get("attributes") or {}
    return r


def _addr(*parts) -> str:
    return ", ".join(str(p) for p in parts if p)


# ---------------------------------------------------------------- France (INSEE / RNE)

FR_URL = "https://recherche-entreprises.api.gouv.fr/search"


def france_search(name: str, limit: int = 3) -> list[dict]:
    """recherche-entreprises: includes `dirigeants` (officers) on every hit."""
    data = get(f"{FR_URL}?q={q(name)}&per_page={limit}")
    out = []
    for hit in data.get("results", [])[:limit]:
        siege = hit.get("siege") or {}
        officers = []
        for d in (hit.get("dirigeants") or [])[:15]:
            # Observed shape: {"nom": "BARRAULT", "prenoms": "FREDERIC JEAN-CLAUDE",
            # "qualite": "...", "type_dirigeant": "personne physique"}. There is no
            # nom_complet in this payload, and both parts arrive uppercase.
            first = (d.get("prenoms") or "").strip()
            last = (d.get("nom") or "").strip()
            who = " ".join(x for x in (_smart_case(first), _smart_case(last)) if x)
            if who:
                officers.append({"name": who, "role": d.get("qualite"),
                                 "kind": "person" if (d.get("type_dirigeant") or "").startswith("personne") else "company"})
        out.append(_rec(
            id=hit.get("siren"), name=hit.get("nom_complet") or hit.get("nom_raison_sociale"),
            country="FR", address=siege.get("adresse"),
            kind="company",
            officers=officers,
            attributes={"siren": hit.get("siren"), "siret": siege.get("siret"),
                        "legal_form": hit.get("nature_juridique"),
                        "naf": hit.get("activite_principale"),
                        "employees": hit.get("tranche_effectif_salarie"),
                        "created": hit.get("date_creation"),
                        "state": hit.get("etat_administratif")},
            url=f"https://annuaire-entreprises.data.gouv.fr/entreprise/{hit.get('siren')}"))
    return out


def france_by_siren(siren: str) -> list[dict]:
    return france_search(siren, limit=1)


def france_bodacc(siren: str, limit: int = 5) -> list[dict]:
    """Commercial-court announcements: insolvency, sale, changes of control."""
    url = ("https://bodacc-datadila.opendatasoft.com/api/explore/v2.1/catalog/datasets/"
           f"annonces-commerciales/records?where=registre%3D%22{siren}%22&limit={limit}")
    data = get(url)
    out = []
    for r in data.get("results", []):
        out.append({"date": r.get("dateparution"), "nature": r.get("familleavis_lib") or r.get("nature"),
                    "tribunal": r.get("tribunal"), "url": url})
    return out


# ---------------------------------------------------------------- Norway (Brønnøysund)

NO_SEARCH = "https://data.brreg.no/enhetsregisteret/api/enheter"


def norway_search(name: str, limit: int = 3) -> list[dict]:
    data = get(f"{NO_SEARCH}?navn={q(name)}&size={limit}")
    out = []
    for e in (data.get("_embedded") or {}).get("enheter", [])[:limit]:
        a = e.get("forretningsadresse") or {}
        out.append(_rec(
            id=e.get("organisasjonsnummer"), name=e.get("navn"), country="NO",
            address=_addr(*(a.get("adresse") or []), a.get("postnummer"), a.get("poststed")),
            kind="company",
            attributes={"orgnr": e.get("organisasjonsnummer"),
                        "legal_form": e.get("organisasjonsform", {}).get("kode"),
                        "employees": e.get("antallAnsatte"),
                        "registered": e.get("registreringsdatoEnhetsregisteret"),
                        "bankrupt": e.get("konkurs"),
                        "group": e.get("erIKonsern")},
            url=f"https://data.brreg.no/enhetsregisteret/api/enheter/{e.get('organisasjonsnummer')}"))
    return out


def norway_roles(orgnr: str) -> list[dict]:
    """The actual roster: directors, signatories and the general manager."""
    data = get(f"{NO_SEARCH}/{orgnr}/roller")
    people = []
    for grp in data.get("rollegrupper", []):
        gtype = (grp.get("type") or {}).get("beskrivelse") or (grp.get("type") or {}).get("kode")
        for p in grp.get("roller", []):
            person = p.get("person") or {}
            ent = p.get("enhet") or {}
            navn = person.get("navn")
            # Observed shape: navn is an object {"etternavn": "Opedal", "fornavn": "Anders"},
            # not a string, so a truthiness test on it would keep a dict as the label.
            if isinstance(navn, dict):
                who = " ".join(x for x in (navn.get("fornavn"), navn.get("etternavn")) if x)
            elif isinstance(navn, list):
                # Brreg returns a list of name variants for some entities; the current
                # one is the only item, the rest are former names.
                who = next((x.get("navn") for x in navn if isinstance(x, dict) and x.get("navn")), "") \
                      if navn and isinstance(navn[0], dict) else " ".join(map(str, navn))
            else:
                who = navn or (ent.get("navn") if isinstance(ent.get("navn"), str) else "")
            role = (p.get("type") or {}).get("beskrivelse") or gtype
            if who:
                people.append({"name": _smart_case(who), "role": role,
                               "birth": person.get("fodselsdato"),
                               "orgnr": ent.get("organisasjonsnummer") or
                                       (ent.get("_links", {}).get("self", {}) or {}).get("href", "").split("/")[-1]})
    return people


def _smart_case(name: str) -> str:
    """Registries store names in caps. Restore readable case; keep particles lowercase
    and keep 1-2 letter tokens alone (initials)."""
    if not name or name != name.upper():
        return name
    parts = []
    for w in name.split():
        lw = w.lower()
        if lw in ("de", "van", "der", "la", "le", "di", "da", "dos", "e", "y"):
            parts.append(lw)
        elif len(w) <= 2:
            parts.append(w)
        else:
            parts.append(w.capitalize())
    return " ".join(parts)


def _titlecase_no(name: str) -> str:
    """Brreg stores names uppercase; restore readable casing without touching initials."""
    if not name or name != name.upper():
        return name
    return " ".join(w.capitalize() if len(w) > 2 else w for w in name.split())


# ---------------------------------------------------------------- Denmark (CVR)

DK_URL = "https://cvrapi.dk/api"


def denmark_search(name: str) -> list[dict]:
    data = get(f"{DK_URL}?search={q(name)}&country=dk")
    if not data or data.get("error"):
        return []
    return [_rec(id=data.get("vat"), name=data.get("name"), country="DK",
                 address=_addr(data.get("address"), data.get("zipcode"), data.get("city")),
                 kind="company",
                 attributes={"cvr": data.get("vat"), "industry": data.get("industrydesc"),
                             "employees": data.get("employees"), "company_type": data.get("companydesc"),
                             "started": data.get("startdate"), "bankrupt": data.get("creditbankrupt")},
                 url=f"https://datacvr.virk.dk/enhed/virksomhed/{data.get('vat')}")]


# ---------------------------------------------------------------- Finland (PRH)

FI_URL = "https://avoindata.prh.fi/opendata-ytj-api/v3/companies"


def finland_search(name: str, limit: int = 2) -> list[dict]:
    data = get(f"{FI_URL}?name={q(name)}&maxResults={limit}")
    out = []
    for c in data.get("companies", [])[:limit]:
        bid = (c.get("businessId") or {}).get("value")
        names = c.get("names") or [{}]
        addr = (c.get("addresses") or [{}])[0]
        out.append(_rec(id=bid, name=names[0].get("name"), country="FI",
                        address=_addr(addr.get("street"), addr.get("postCode"), addr.get("city")),
                        kind="company",
                        attributes={"business_id": bid, "legal_form": c.get("legalForm"),
                                    "registered": (c.get("registrationDate") or "")[:10],
                                    "industry": c.get("mainBusinessLine", {}).get("descriptions")},
                        url=f"https://tietopalvelu.ytj.fi/yritys/{bid}"))
    return out


# ---------------------------------------------------------------- Czechia (ARES)

CZ_URL = "https://ares.gov.cz/ekonomicke-subjekty-v-be/rest/ekonomicke-subjekty"


def czech_by_ico(ico: str) -> list[dict]:
    d = get(f"{CZ_URL}/{ico}")
    sidlo = d.get("sidlo") or {}
    return [_rec(id=d.get("ico"), name=d.get("obchodniJmeno"), country="CZ",
                 address=sidlo.get("textovaAdresa") or _addr(sidlo.get("nazevUlice"), sidlo.get("nazevObce")),
                 kind="company",
                 attributes={"ico": d.get("ico"), "dic": d.get("dic"),
                             "legal_form": d.get("pravniForma"), "created": (d.get("datumVzniku") or "")[:10]},
                 url=f"https://ares.gov.cz/ekonomicke-subjekty?ico={ico}")]


# ---------------------------------------------------------------- Switzerland (Zefix)

CH_URL = "https://www.zefix.ch/ZefixREST/api/v1/firm/search.json"


def switzerland_search(name: str, limit: int = 2) -> list[dict]:
    body = json.dumps({"name": name, "maxEntries": limit}).encode()
    data = get(CH_URL, raw=False) if False else _post_json(CH_URL, body)
    out = []
    for f in data.get("list", [])[:limit]:
        out.append(_rec(id=f.get("uid"), name=f.get("name"), country="CH",
                        address=_addr(f.get("address", {}).get("street"),
                                      f.get("address", {}).get("swissZipCode"),
                                      f.get("address", {}).get("town")),
                        kind="company",
                        attributes={"uid": f.get("uid"), "status": f.get("status"),
                                    "legal_form": (f.get("legalForm") or {}).get("name"),
                                    "canton": f.get("canton")},
                        url=f"https://www.zefix.ch/en/search/entity/list?name={q(name or '')}"))
    return out


def _post_json(url: str, body: bytes) -> dict:
    """POST helper. Raises FetchError, not HTTPError, so callers have one exception to
    catch: a 404 from one country must never abort a multi-country lookup."""
    import urllib.error
    import urllib.request

    from .fetch import DEFAULT_UA
    req = urllib.request.Request(url, data=body,
                                 headers={"User-Agent": DEFAULT_UA, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8", "ignore"))
    except (urllib.error.HTTPError, urllib.error.URLError) as e:
        raise FetchError(f"POST {url}: {e}") from e


# ---------------------------------------------------------------- Brazil (ReceitaWS)

BR_URL = "https://receitaws.com.br/v1/cnpj"


def brazil_by_cnpj(cnpj: str) -> list[dict]:
    digits = re.sub(r"\D", "", cnpj)
    d = get(f"{BR_URL}/{digits}")
    if d.get("status") == "ERROR":
        return []
    partners = [{"name": p.get("nome"), "role": p.get("qual")}
                for p in (d.get("qsa") or []) if p.get("nome")]
    return [_rec(id=digits, name=d.get("nome"), country="BR",
                 address=_addr(d.get("logradouro"), d.get("numero"), d.get("municipio"), d.get("uf")),
                 kind="company", officers=partners,
                 attributes={"cnpj": digits, "trade_name": d.get("fantasia"),
                             "status": d.get("situacao"), "opened": d.get("abertura"),
                             "activity": (d.get("atividade_principal") or [{}])[0].get("text")},
                 url=f"https://receitaws.com.br/v1/cnpj/{digits}")]


# ---------------------------------------------------------------- dispatch

BY_COUNTRY = {
    "FR": ("SIREN (9 digits)", france_by_siren),
    "NO": ("organisasjonsnummer (9 digits)", None),   # filled by norway_search on name
    "CZ": ("IČO (8 digits)", czech_by_ico),
    "BR": ("CNPJ (14 digits)", brazil_by_cnpj),
}

SEARCHERS = {
    "FR": france_search, "NO": norway_search, "DK": denmark_search,
    "FI": finland_search, "CH": switzerland_search,
}

OFFICER_LOADERS = {
    "FR": lambda rec: [(o["name"], o.get("role")) for o in rec["officers"]],
    "NO": lambda rec: [(p["name"], p.get("role")) for p in norway_roles(rec["id"])],
    "BR": lambda rec: [(o["name"], o.get("role")) for o in rec["officers"]],
}


def lookup_name(name: str, countries: list[str] | None = None) -> list[dict]:
    """Try each national registry by name; return every hit that came back."""
    hits: list[dict] = []
    for cc, fn in SEARCHERS.items():
        if countries and cc not in countries:
            continue
        try:
            got = fn(name)
        except (FetchError, ValueError, KeyError, OSError):
            continue
        hits.extend(got)
    return hits


def lookup_id(value: str, country: str) -> list[dict]:
    fn = BY_COUNTRY.get(country, (None, None))[1]
    if fn is None:
        return []
    try:
        return fn(value)
    except (FetchError, ValueError, OSError):
        return []
