"""Layout pins for the non-US adapters.

These do not hit the network. Each fixture is a short excerpt of a response observed on
2026-10-23, kept only long enough to pin the field names the adapter reads. When an
upstream registry renames a field, one of these should fail rather than the graph quietly
producing a node with a null label.
"""
import json

from entitygraph import registries as R


# ---- France: dirigeants is a flat list, names arrive uppercase, no nom_complet
FR_PAYLOAD = {"results": [{
    "siren": "652014051", "nom_complet": "CARREFOUR",
    "siege": {"siret": "65201405100019", "adresse": "93 AVENUE DE PARIS 91300 MASSY"},
    "nature_juridique": "5599", "activite_principale": "47.11D",
    "date_creation": "1959-01-01", "etat_administratif": "A",
    "dirigeants": [
        {"nom": "BARRAULT", "prenoms": "FREDERIC JEAN-CLAUDE",
         "qualite": "Administrateurs représentant les salariés",
         "type_dirigeant": "personne physique"},
        {"nom": "BOMPARD", "prenoms": "ALEXANDRE",
         "qualite": "Président du conseil d’administration et directeur général",
         "type_dirigeant": "personne physique"},
    ]}]}


def test_france_joins_first_and_last_name(monkeypatch):
    monkeypatch.setattr(R, "get", lambda url: FR_PAYLOAD)
    recs = R.france_search("Carrefour", 1)
    assert recs[0]["id"] == "652014051"
    names = [o["name"] for o in recs[0]["officers"]]
    assert names[0] == "Frederic Jean-claude Barrault"
    assert "Alexandre Bompard" in names


def test_france_marks_officer_kind(monkeypatch):
    monkeypatch.setattr(R, "get", lambda url: FR_PAYLOAD)
    rec = R.france_search("Carrefour", 1)[0]
    assert rec["officers"][0]["kind"] == "person"


# ---- Norway: person.navn is {"etternavn","fornavn"}, roles carry type.beskrivelse
NO_ROLES = {"rollegrupper": [
    {"type": {"kode": "DAGL", "beskrivelse": "Daglig leder"},
     "roller": [{"person": {"navn": {"etternavn": "Opedal", "fornavn": "Anders"},
                            "fodselsdato": "1968-05-04"},
                 "type": {"kode": "DAGL", "beskrivelse": "Daglig leder"}}]},
    {"type": {"kode": "STYR", "beskrivelse": "Styre"},
     "roller": [{"person": {"navn": {"etternavn": "Roth", "fornavn": "Jarle Kjell"}},
                 "type": {"kode": "LEDE", "beskrivelse": "Styrets leder"}}]},
]}


def test_norway_roles_reads_structured_name(monkeypatch):
    monkeypatch.setattr(R, "get", lambda url: NO_ROLES)
    people = R.norway_roles("923609016")
    assert people[0]["name"] == "Anders Opedal"
    assert people[0]["role"] == "Daglig leder"
    assert people[1]["name"] == "Jarle Kjell Roth"
    assert people[1]["role"] == "Styrets leder"


def test_smart_case_keeps_particles_and_initials():
    assert R._smart_case("PETROLEO BRASILEIRO S A PETROBRAS") == "Petroleo Brasileiro S A Petrobras"
    assert R._smart_case("ANGELICA GARCIA COBAS LAUREANO") == "Angelica Garcia Cobas Laureano"
    assert R._smart_case("Alexandre Bompard") == "Alexandre Bompard"


# ---- Denmark: one flat object, no wrapper
DK_PAYLOAD = {"vat": "24256790", "name": "NOVO NORDISK A/S", "address": "Novo Alle",
              "zipcode": "2880", "city": "Bagsværd", "industrydesc": "Fremstilling af farmaceutiske",
              "employees": "44000", "companydesc": "Aktieselskab", "startdate": "1923-11-13"}


def test_denmark_maps_flat_object(monkeypatch):
    monkeypatch.setattr(R, "get", lambda url: DK_PAYLOAD)
    rec = R.denmark_search("Novo")[0]
    assert rec["id"] == "24256790"
    assert rec["country"] == "DK"
    assert rec["address"].startswith("Novo Alle, 2880")
