"""Entity resolution: one company must be one node even when five registries name it
five ways.

The rule here is conservative on purpose. We join on identifiers (LEI, CIK, SIREN, org
number, VAT, FIGI) and never on a name alone. A name match is reported as a candidate
with its score, and the caller decides -- guessing on names is how entity graphs
acquire phantom subsidiaries.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

SUFFIXES = {
    "inc", "incorporated", "corp", "corporation", "co", "company", "ltd", "limited",
    "llc", "l.l.c.", "plc", "ag", "sa", "s.a.", "sas", "gmbh", "bv", "b.v.", "nv",
    "n.v.", "oy", "ab", "as", "a/s", "aps", "srl", "s.r.l.", "spa", "s.p.a.", "kk",
    "holdings", "holding", "group", "the",
}


def normalize_name(name: str) -> str:
    s = unicodedata.normalize("NFKD", name or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    # Drop dots first so "S.A." collapses to "sa" and the suffix list can match it;
    # replacing punctuation with a space instead splits it into "s" and "a", which no
    # suffix set contains. Other punctuation becomes a space.
    s = s.replace(".", "")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    tokens = [t for t in s.split() if t and t not in SUFFIXES]
    return " ".join(tokens)


def name_similarity(a: str, b: str) -> float:
    """Token-set Jaccard on normalised names. Cheap, explainable, no dependency."""
    A, B = set(normalize_name(a).split()), set(normalize_name(b).split())
    if not A or not B:
        return 0.0
    return len(A & B) / len(A | B)


ID_PATTERNS = {
    "lei": re.compile(r"^[A-Z0-9]{18}[0-9]{2}$"),
    "cik": re.compile(r"^\d{1,10}$"),
    "isin": re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$"),
    "figi": re.compile(r"^BBG[0-9A-Z]{9}$"),
    "siren": re.compile(r"^\d{9}$"),
    "orgnr": re.compile(r"^\d{9}$"),
}


@dataclass
class Match:
    key: str
    label: str
    score: float
    on: str            # which identifier or "name" produced it


class Resolver:
    """Identifier-first lookup over a Graph."""

    def __init__(self, graph):
        self.g = graph
        self._index: dict[tuple[str, str], str] = {}
        for n in graph.nodes.values():
            for k, v in n.attrs.items():
                if isinstance(v, str) and v:
                    self._index[(k, v.upper())] = n.key

    def by_id(self, attr: str, value: str) -> str | None:
        return self._index.get((attr, (value or "").upper()))

    def candidates(self, name: str, threshold: float = 0.6) -> list[Match]:
        out = []
        for n in self.g.nodes.values():
            if n.kind != "company":
                continue
            s = name_similarity(name, n.label)
            if s >= threshold:
                out.append(Match(n.key, n.label, round(s, 3), "name"))
        return sorted(out, key=lambda m: -m.score)
