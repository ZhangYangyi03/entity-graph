"""UK Companies House: bulk snapshots turned into a local SQLite index.

The bulk feed is the only keyless path to UK company data, and it is 494 MB of CSV for
basic company records plus ~2.5 GB across 33 shards for persons with significant control.
Neither belongs on a request path, so this module *ingests once* and answers from SQLite.
Query-time `lookup` then costs a indexed SELECT, not a 78 MB download.

Nothing here invents a UK company number: rows are keyed by the number Companies House
issued.
"""
from __future__ import annotations

import csv
import io
import os
import re
import sqlite3
import urllib.request
import zipfile

from .fetch import DEFAULT_UA
import json

BASE = "https://download.companieshouse.gov.uk"
INDEX_PAGE = f"{BASE}/en_output.html"
PSC_PAGE = f"{BASE}/en_pscdata.html"

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    company_number TEXT PRIMARY KEY,
    name           TEXT,
    status         TEXT,
    company_type   TEXT,
    incorporation  TEXT,
    reg_address    TEXT,
    sic            TEXT
);
CREATE TABLE IF NOT EXISTS psc (
    company_number TEXT,
    name           TEXT,
    kind           TEXT,
    natures        TEXT,
    country        TEXT,
    dob_month      TEXT,
    dob_year       TEXT
);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""

INDEXES = """
CREATE INDEX IF NOT EXISTS idx_companies_name ON companies(name);
CREATE INDEX IF NOT EXISTS idx_psc_company    ON psc(company_number);
"""
# A LIKE '%x%' scan over 5.7M rows took ~3s. A leading-anchored LIKE can use an index,
# so the fast path is a prefix index and a query that always anchors: exact match first,
# then prefix, then (only if nothing was found) the slow contains-scan.
PREFIX_INDEX = "CREATE INDEX IF NOT EXISTS idx_companies_name_prefix ON companies(name COLLATE NOCASE);"


def _page_links(page: str, pattern: str) -> list[str]:
    req = urllib.request.Request(page, headers={"User-Agent": DEFAULT_UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        html = r.read().decode("utf-8", "ignore")
    return re.findall(pattern, html)


def latest_basic_url() -> str | None:
    links = _page_links(INDEX_PAGE, r'BasicCompanyDataAsOneFile-[0-9-]+\.zip')
    return BASE + "/" + sorted(links)[-1] if links else None


def latest_psc_urls() -> list[str]:
    links = _page_links(PSC_PAGE, r'psc-snapshot-[0-9-]+_\d+of\d+\.zip')
    def shard_no(fn: str) -> int:
        m = re.search(r"_(\d+)of\d+[.]zip$", fn)
        return int(m.group(1)) if m else 0
    return [BASE + "/" + x for x in sorted(set(links), key=shard_no)]


def open_db(path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    return con


def _csv_rows_from_zip(blob: bytes):
    """Basic company snapshot: one CSV inside the zip."""
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        for member in z.namelist():
            if not member.lower().endswith(".csv"):
                continue
            with z.open(member) as fh:
                text = io.TextIOWrapper(fh, encoding="latin-1", errors="replace", newline="")
                yield from csv.DictReader(text)


def _jsonl_rows_from_zip(blob: bytes):
    """PSC snapshot: not CSV. Observed 2026-10-23: a .txt holding one JSON object per line,
    shaped {"company_number": ..., "data": {"name": ..., "kind": ..., "natures_of_control":
    [...]}}."""
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        for member in z.namelist():
            if not member.lower().endswith((".txt", ".json", ".jsonl")):
                continue
            with z.open(member) as fh:
                for line in io.TextIOWrapper(fh, encoding="utf-8", errors="replace"):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        continue


def ingest_basic(db_path: str, url: str | None = None, keep_zip: str | None = None) -> int:
    """Download the basic company snapshot and load it. ~494 MB in, one table out."""
    url = url or latest_basic_url()
    if not url:
        raise RuntimeError("no BasicCompanyData zip advertised on the index page")
    con = open_db(db_path)
    zip_path = _download(url, keep_zip or os.path.join(os.path.dirname(os.path.abspath(db_path)) or ".",
                                                       "basic-company-data.zip"))
    # Bulk load: drop the lookup index while inserting and rebuild it after. Inserting into
    # an indexed table row by row is what made this take longer than the download.
    con.execute("DROP INDEX IF EXISTS idx_companies_name")
    con.execute("PRAGMA journal_mode=MEMORY")
    con.execute("PRAGMA synchronous=OFF")
    n = 0
    batch = []
    for row in _csv_rows_from_file(zip_path):
        num = (row.get("CompanyNumber") or row.get(" CompanyNumber") or "").strip()
        if not num:
            continue
        batch.append((
            num,
            (row.get("CompanyName") or "").strip(),
            (row.get("CompanyStatus") or "").strip(),
            (row.get("CompanyCategory") or "").strip(),
            (row.get("IncorporationDate") or "").strip(),
            ", ".join(x.strip() for x in (
                row.get("RegAddress.AddressLine1") or "",
                row.get("RegAddress.PostTown") or "",
                row.get("RegAddress.PostCode") or "") if x and x.strip()),
            (row.get("SICCode.SicText_1") or "").strip(),
        ))
        n += 1
        if len(batch) >= 20000:
            con.executemany("INSERT OR REPLACE INTO companies VALUES (?,?,?,?,?,?,?)", batch)
            batch.clear()
    if batch:
        con.executemany("INSERT OR REPLACE INTO companies VALUES (?,?,?,?,?,?,?)", batch)
    con.execute("INSERT OR REPLACE INTO meta VALUES ('basic_url', ?)", (url,))
    con.commit()
    con.executescript(INDEXES)
    con.commit()
    return n


def ingest_psc_shard(db_path: str, url: str, keep_zip: str | None = None) -> int:
    """Load one PSC shard so officers can be resolved to real people."""
    con = open_db(db_path)
    zip_path = _download(url, keep_zip or os.path.join(
        os.path.dirname(os.path.abspath(db_path)) or ".", os.path.basename(url)))
    n = 0
    batch = []
    for obj in _jsonl_rows_from_file(zip_path):
        num = str(obj.get("company_number") or "").strip()
        d = obj.get("data") or {}
        name = (d.get("name") or "").strip()
        if not num or not name:
            continue
        natures = d.get("natures_of_control") or []
        dob = d.get("date_of_birth") or {}
        batch.append((
            num, name, (d.get("kind") or "").strip(),
            ", ".join(natures) if isinstance(natures, list) else str(natures),
            (d.get("country_of_residence") or "").strip(),
            str(dob.get("month") or ""), str(dob.get("year") or ""),
        ))
        n += 1
        if len(batch) >= 20000:
            con.executemany("INSERT INTO psc VALUES (?,?,?,?,?,?,?)", batch)
            batch.clear()
    if batch:
        con.executemany("INSERT INTO psc VALUES (?,?,?,?,?,?,?)", batch)
    con.execute("INSERT OR REPLACE INTO meta VALUES ('psc_url', ?)", (url,))
    con.commit()
    return n


def _download(url: str, dest: str, expected: int | None = None) -> str:
    """Stream to disk, verify the size, resume if a previous attempt died part-way.

    Observed here: a 494 MB snapshot arrived as 104 MB, and ZipFile only said
    'not a zip file' -- the truncation looked exactly like a corrupt upstream. The
    Content-Length from this host is authoritative, so a short file is re-requested
    rather than parsed.
    """
    os.makedirs(os.path.dirname(os.path.abspath(dest)) or ".", exist_ok=True)
    if expected is None:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": DEFAULT_UA})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                expected = int(r.headers.get("Content-Length") or 0) or None
        except Exception:
            expected = None
    for attempt in range(4):
        if os.path.exists(dest) and expected and os.path.getsize(dest) == expected:
            return dest
        have = os.path.getsize(dest) if os.path.exists(dest) else 0
        headers = {"User-Agent": DEFAULT_UA}
        if have:
            headers["Range"] = f"bytes={have}-"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=1800) as r:
            mode = "ab" if (have and r.status == 206) else "wb"
            if mode == "wb":
                have = 0
            with open(dest, mode) as fh:
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    fh.write(chunk)
        got = os.path.getsize(dest)
        if not expected or got == expected:
            return dest
    raise RuntimeError(f"short download: {os.path.getsize(dest)} of {expected} bytes from {url}")


def _csv_rows_from_file(path: str):
    with zipfile.ZipFile(path) as z:
        for member in z.namelist():
            if not member.lower().endswith(".csv"):
                continue
            with z.open(member) as fh:
                text = io.TextIOWrapper(fh, encoding="latin-1", errors="replace", newline="")
                yield from csv.DictReader(text)


def _jsonl_rows_from_file(path: str):
    with zipfile.ZipFile(path) as z:
        for member in z.namelist():
            if not member.lower().endswith((".txt", ".json", ".jsonl")):
                continue
            with z.open(member) as fh:
                for line in io.TextIOWrapper(fh, encoding="utf-8", errors="replace"):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        continue


def lookup(db_path: str, *, number: str | None = None, name: str | None = None,
           limit: int = 3, contains: bool = False) -> list[dict]:
    """Answer from the index. Returns the same record shape as the other registries.

    Name lookup is tiered on purpose: exact, then prefix (both index-backed), and only if
    neither hits does it fall back to a contains-scan -- which is correct but takes seconds
    on 5.7M rows, so it is the last resort rather than the default.
    """
    if not os.path.exists(db_path):
        return []
    con = open_db(db_path)
    rows = []
    if number:
        rows = con.execute("SELECT * FROM companies WHERE company_number=?",
                           (number.strip().upper(),)).fetchall()
        cols = [c[0] for c in con.execute("SELECT * FROM companies LIMIT 0").description]
    elif name:
        cols = [c[0] for c in con.execute("SELECT * FROM companies LIMIT 0").description]
        q = name.strip()
        rows = con.execute(
            "SELECT * FROM companies WHERE name = ? COLLATE NOCASE LIMIT ?", (q, limit)).fetchall()
        if not rows:
            rows = con.execute(
                "SELECT * FROM companies WHERE name LIKE ? COLLATE NOCASE LIMIT ?",
                (q + "%", limit)).fetchall()
        if not rows and contains:
            rows = con.execute(
                "SELECT * FROM companies WHERE name LIKE ? COLLATE NOCASE LIMIT ?",
                ("%" + q + "%", limit)).fetchall()
    else:
        return []

    out = []
    for row in rows:
        d = dict(zip(cols, row))
        psc = con.execute("SELECT name, kind, natures FROM psc WHERE company_number=?",
                          (d["company_number"],)).fetchall()
        out.append({
            "id": d["company_number"], "name": d["name"], "country": "GB",
            "address": d["reg_address"], "kind": "company",
            "officers": [{"name": p[0], "role": f"PSC ({p[2]})" if p[2] else "PSC"} for p in psc],
            "attributes": {"status": d["status"], "company_type": d["company_type"],
                           "incorporated": d["incorporation"], "sic": d["sic"]},
            "url": "https://find-and-update.company-information.service.gov.uk/company/" + d["company_number"],
        })
    return out


def ingest_basic_chunked(db_path: str, zip_path: str, skip: int = 0, limit: int = 250000) -> dict:
    """Load `limit` rows starting at `skip`, so a call can be bounded and resumed.

    Why this exists: the snapshot decompresses to 2.8 GB of CSV. Decoding it end to end
    outruns the sandbox's per-call budget, and a process started to outlive the sandbox was
    reaped with it, leaving a 334 MB database with zero rows. Chunks make progress
    recorded rather than hoped for -- `skip` is the resume point, and the caller reads it
    back from meta instead of counting again.
    """
    con = open_db(db_path)
    con.execute("DROP INDEX IF EXISTS idx_companies_name")
    con.execute("PRAGMA journal_mode=MEMORY")
    con.execute("PRAGMA synchronous=OFF")
    n = 0
    batch = []
    for i, row in enumerate(_csv_rows_from_file(zip_path)):
        if i < skip:
            continue
        if n >= limit:
            break
        num = (row.get("CompanyNumber") or row.get(" CompanyNumber") or "").strip()
        if not num:
            n += 1
            continue
        batch.append((
            num,
            (row.get("CompanyName") or "").strip(),
            (row.get("CompanyStatus") or "").strip(),
            (row.get("CompanyCategory") or "").strip(),
            (row.get("IncorporationDate") or "").strip(),
            ", ".join(x.strip() for x in (
                row.get("RegAddress.AddressLine1") or "",
                row.get("RegAddress.PostTown") or "",
                row.get("RegAddress.PostCode") or "") if x and x.strip()),
            (row.get("SICCode.SicText_1") or "").strip(),
        ))
        n += 1
        if len(batch) >= 20000:
            con.executemany("INSERT OR REPLACE INTO companies VALUES (?,?,?,?,?,?,?)", batch)
            batch.clear()
    if batch:
        con.executemany("INSERT OR REPLACE INTO companies VALUES (?,?,?,?,?,?,?)", batch)
    con.commit()
    pos = skip + n
    con.execute("INSERT OR REPLACE INTO meta VALUES ('basic_skip', ?)", (str(pos),))
    con.commit()
    return {"read": n, "skip": pos, "companies": con.execute(
        "SELECT COUNT(*) FROM companies").fetchone()[0]}


def build_indexes(db_path: str) -> None:
    con = open_db(db_path)
    con.executescript(INDEXES)
    con.commit()


def csv_member(zip_path: str) -> str:
    with zipfile.ZipFile(zip_path) as z:
        for member in z.namelist():
            if member.lower().endswith(".csv"):
                return member
    raise RuntimeError(f"no CSV inside {zip_path}")


def ingest_basic_bytes(db_path: str, zip_path: str, limit_rows: int = 3000000,
                       byte_budget: int = 200 * 1024 * 1024) -> dict:
    """Load from the stored byte offset to EOF-or-budget, then remember the offset.

    Resuming by row index would mean re-decompressing from the start on every call -- the
    cost that made the whole-file attempt time out. The header at byte 0 is always read
    first, because it is the only source of column order; the prefix before `start` is
    then skipped and counted, so `consumed` is where this call actually stopped.
    """
    con = open_db(db_path)
    # WAL + NORMAL, not MEMORY/OFF: the bulk load gets interrupted on this host (a killed
    # call leaves a half-written page and then EVERY later query says 'database disk image
    # is malformed'). WAL keeps the last committed transaction intact no matter where the
    # process dies, at the cost of a slower flush -- a trade worth taking for a load that
    # is expected to be interrupted.
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("DROP INDEX IF EXISTS idx_companies_name")

    member = csv_member(zip_path)
    row = con.execute("SELECT v FROM meta WHERE k='basic_offset'").fetchone()
    start = int(row[0]) if row else 0

    with zipfile.ZipFile(zip_path) as z:
        info = z.getinfo(member)
        with z.open(member) as raw:
            header_line = raw.readline()
            header = [h.strip() for h in next(csv.reader(
                [header_line.decode("latin-1", "replace")]))]
            idx = {n: header.index(n) for n in (
                "CompanyNumber", "CompanyName", "CompanyStatus", "CompanyCategory",
                "IncorporationDate", "RegAddress.AddressLine1", "RegAddress.PostTown",
                "RegAddress.PostCode", "SICCode.SicText_1")}
            consumed = len(header_line)
            want = start - consumed
            while want > 0:
                chunk = raw.read(min(want, 1 << 20))
                if not chunk:
                    break
                consumed += len(chunk)
                want -= len(chunk)

            rows = 0
            batch = []
            while rows < limit_rows and consumed < info.file_size - 1:
                line = raw.readline()
                if not line:
                    break
                consumed += len(line)
                rows += 1
                if consumed - start > byte_budget and rows > 1000:
                    break
                f = next(csv.reader([line.decode("latin-1", "replace")]))
                if len(f) <= idx["SICCode.SicText_1"]:
                    continue
                batch.append((
                    f[idx["CompanyNumber"]].strip(), f[idx["CompanyName"]].strip(),
                    f[idx["CompanyStatus"]].strip(), f[idx["CompanyCategory"]].strip(),
                    f[idx["IncorporationDate"]].strip(),
                    ", ".join(x.strip() for x in (f[idx["RegAddress.AddressLine1"]],
                                                  f[idx["RegAddress.PostTown"]],
                                                  f[idx["RegAddress.PostCode"]]) if x.strip()),
                    f[idx["SICCode.SicText_1"]].strip()))
                if len(batch) >= 20000:
                    con.executemany("INSERT OR REPLACE INTO companies VALUES (?,?,?,?,?,?,?)", batch)
                    batch.clear()
                    # Commit the rows AND the resume point together, so a kill can only
                    # cost the current batch, never the whole run.
                    con.execute("INSERT OR REPLACE INTO meta VALUES ('basic_offset', ?)",
                                (str(consumed),))
                    con.commit()
            if batch:
                con.executemany("INSERT OR REPLACE INTO companies VALUES (?,?,?,?,?,?,?)", batch)
            con.execute("INSERT OR REPLACE INTO meta VALUES ('basic_offset', ?)", (str(consumed),))
            con.execute("INSERT OR REPLACE INTO meta VALUES ('basic_total', ?)", (str(info.file_size),))
            con.commit()
            done = consumed >= info.file_size - 1
    return {"rows": rows, "offset": consumed, "total_bytes": info.file_size, "done": done,
            "companies": con.execute("SELECT COUNT(*) FROM companies").fetchone()[0]}




def stats(db_path: str) -> dict:
    if not os.path.exists(db_path):
        return {"exists": False}
    con = open_db(db_path)
    return {"exists": True,
            "companies": con.execute("SELECT COUNT(*) FROM companies").fetchone()[0],
            "psc": con.execute("SELECT COUNT(*) FROM psc").fetchone()[0],
            "meta": dict(con.execute("SELECT k, v FROM meta").fetchall())}
