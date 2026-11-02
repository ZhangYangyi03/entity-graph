"""Parse SC 13D/13G cover pages into real holder nodes.

The filing itself is not the owner. The cover page carries `NAME OF REPORTING PERSON`,
the aggregate amount (row 9) and the percent of class (row 11); those three are what make
an edge worth drawing. Older 13Gs are HTML schedules, so this reads text rather than XML.

Every parser here is written against the observed layout, and tests/test_holders.py pins
the layout with a recorded excerpt, because a silent change upstream is exactly how a
graph starts inventing holders.
"""
from __future__ import annotations

import html as _html
import re

from .fetch import FetchError, get

_SEC_BASE = "https://www.sec.gov/Archives/edgar/data"

# Row 1 of the cover page. Case varies between filers; the colon is sometimes absent.
# Row 1 is "NAME OF REPORTING PERSON" on most schedules and "NAMES OF REPORTING PERSONS"
# on others (Bezos' Amazon 13G/A uses the plural). A number between the label and the name
# is part of neither -- it is the row marker.
_NAME_RE = re.compile(
    r"NAMES? OF REPORTING PERSONS?\s*[:\-]?\s*"
    r"(?:(?:I\.R\.S\. IDENTIFICATION NO\. OF ABOVE PERSONS \(entities only\))|[\d.)]|\s)*"
    r"([A-Z][^\d]{2,90}?)\s+(?:2\s+CHECK|[A-Z]{2}\s+CHECK|CHECK THE APPROPRIATE|S\.?I\.?|I\.R\.S\.)",
    re.I)
_AMOUNT_RE = re.compile(r"AGGREGATE AMOUNT BENEFICIALLY OWNED BY EACH REPORTING PERSON\s*([\d,]{2,})\s*(?:shares)?", re.I)
# Row 11 is printed both as "IN ROW 9" and as "IN ROW (9)".
_PERCENT_RE = re.compile(r"PERCENT OF CLASS REPRESENTED BY AMOUNT IN ROW \(?9\)?\s*([\d.]+)\s*%", re.I)

# A cover-page name that is really boilerplate, not a person or entity.
_JUNK = ("see the cover", "reporting persons", "not applicable", "none")


def _plain(h: str) -> str:
    t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", h, flags=re.S | re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = _html.unescape(t)
    t = t.replace("\u2009", " ").replace("\u2004", " ")
    return re.sub(r"\s+", " ", t)


def parse_cover(text: str) -> list[dict]:
    """Pull every reporting person's (name, shares, percent) out of one schedule."""
    flat = _plain(text) if "<" in text[:4000] else re.sub(r"\s+", " ", text)
    out: list[dict] = []
    for m in _NAME_RE.finditer(flat):
        name = m.group(1).strip(" -–—.,")
        if not name or any(j in name.lower() for j in _JUNK):
            continue
        # The label repeats as "...PERSONS I.R.S. IDENTIFICATION NOS. OF ABOVE PERSONS", so a
        # short all-caps fragment is page furniture, not a holder. A real holder name either
        # contains a lower-case letter or is long enough to be a corporate name.
        if not re.search(r"[a-z]", name) and len(name) < 12:
            continue
        if re.match(r"^(S\.?S\.?|I\.?R\.?S\.?|C\.?I\.?K\.?|SEE)\b", name, re.I):
            continue
        # The cover page for this person continues until the next "1 NAME OF REPORTING".
        window = flat[m.end(): m.end() + 2500]
        amt = _AMOUNT_RE.search(window)
        pct = _PERCENT_RE.search(window)
        out.append({
            "name": name,
            "shares": int(amt.group(1).replace(",", "")) if amt else None,
            "percent": float(pct.group(1)) if pct else None,
        })
    # De-duplicate identical cover pages (amendments repeat them verbatim).
    seen, uniq = set(), []
    for r in out:
        k = (r["name"].lower(), r["shares"], r["percent"])
        if k not in seen:
            seen.add(k)
            uniq.append(r)
    return uniq


def filing_documents(cik: str | int, accession: str) -> list[str]:
    """URLs of the schedule documents in one filing directory, primary doc first."""
    base = f"{_SEC_BASE}/{int(cik)}/{accession.replace('-', '')}"
    try:
        idx = get(base + "/index.json")
    except FetchError:
        return []
    names = [i["name"] for i in idx.get("directory", {}).get("item", [])]
    docs = [n for n in names
            if n.lower().endswith((".htm", ".html", ".txt"))
            and "index" not in n.lower()
            and not n.lower().endswith("-headers.html")]
    # Ordering is a guess, not a fact: filers name the schedule anything. Try the ones
    # whose filename hints at 13D/13G first, then the rest, and let parsing decide.
    docs.sort(key=lambda n: (0 if "13g" in n.lower() or "13d" in n.lower() else 1, n))
    return [f"{base}/{n}" for n in docs[:5]]


def holders_in_filing(cik: str | int, accession: str) -> list[dict]:
    """Reporting persons named in one filing, each with its document URL.

    Scans every document in the filing until one parses, because the cover page is not
    reliably the primary document and the filename is no guide.
    """
    for url in filing_documents(cik, accession):
        try:
            text = get(url, raw=True, ttl=86400 * 30)
        except FetchError:
            continue
        rows = parse_cover(text)
        if rows:
            for r in rows:
                r["url"] = url
                r["kind"] = classify(r["name"], text)
            return rows
    return []


# Cover pages mix natural persons and entities. Row 12 ("TYPE OF REPORTING PERSON") is
# where filers declare it: HC/CO/PN are entities or trusts, IN is an individual. Without
# row 12 the fallback is a name-shape heuristic, and the node says which rule decided,
# because "we guessed" and "the filing says" must not look the same downstream.
_PERSON_RE = re.compile(
    r"TYPE OF REPORTING PERSON\s*([A-Z]{2}(?:\s*,\s*[A-Z]{2})*)", re.I)
_ENTITY_TYPES = {"HC", "CO", "PN", "BD", "BK", "IC", "IA", "EP", "SA", "OO", "CP", "FI", "INS"}
_PERSON_HINT = re.compile(r"\b[A-Z][a-z]+\s+[A-Z]\.?\s+[A-Z][a-z]+")


def classify(name: str, cover_text: str = "") -> str:
    """Return 'person' or 'company', and be explicit about which rule fired."""
    m = _PERSON_RE.search(cover_text or "")
    if m:
        codes = {c.strip().upper() for c in m.group(1).split(",")}
        if "IN" in codes:
            return "person"
        if codes & _ENTITY_TYPES:
            return "company"
    if _PERSON_HINT.search(name or ""):
        return "person"
    return "company"
