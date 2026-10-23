"""Japan: 国税庁 法人番号 (National Tax Agency corporate number register).

No key, but not a live API either: the NTA publishes a monthly full dump that is fetched
by POSTing a form with a CSRF token, then a ~1 GB Shift-JIS CSV inside a zip. So Japan is
an ingest, like the UK, not a request path.

Measured 2026-10-23: 5,837,980 legal entities, exact-name lookup 0-2 ms against the index.

What it gives: corporate number, name (kanji and kana), registered address, prefecture,
organisation type, and an English translation for parts of the file. What it does not
give: directors, shareholders, or ownership. A Japanese company node that carries only
this source must never grow officer edges.
"""
from __future__ import annotations

import csv
import http.cookiejar
import io
import os
import re
import sqlite3
import ssl
import time
import urllib.parse
import urllib.request
import zipfile

PAGE = "https://www.houjin-bangou.nta.go.jp/download/zenken/index.html"
TOKEN_FIELD = ("jp.go.nta.houjin_bangou.framework.web.common."
               "CNSFWTokenProcessor.request.token")
# The zip selector for the nationwide Unicode CSV. The page lists one selector per
# format and per prefecture (168 of them in total); 28110 is the nationwide CSV.
NATIONWIDE_CSV = "28110"
UA = {"User-Agent": "Mozilla/5.0 entity-graph/0.1"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS houjin (
  seq INTEGER, houjin_bangou TEXT PRIMARY KEY, kind TEXT, closed_kind TEXT,
  closed_date TEXT, update_date TEXT, name TEXT, name_image TEXT,
  pref_code TEXT, pref TEXT, city TEXT, street TEXT, image TEXT,
  corporate_kind TEXT, corporate_code TEXT, postcode TEXT,
  name_en TEXT, pref_en TEXT, street_en TEXT, kana TEXT, is_foreign TEXT
);
CREATE INDEX IF NOT EXISTS idx_jp_name ON houjin(name);
CREATE INDEX IF NOT EXISTS idx_jp_pref ON houjin(pref, city);
"""


class IngestError(RuntimeError):
    pass


def _opener():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx),
                                     urllib.request.HTTPCookieProcessor(cj))
    op.addheaders = list(UA.items())
    return op


def download(dest: str, file_no: str = NATIONWIDE_CSV) -> str:
    """Fetch the nationwide dump. The form needs the session's CSRF token, so the page is
    read first; the token is tied to the cookie jar, not reusable across processes."""
    op = _opener()
    page = op.open(PAGE, timeout=60).read().decode("utf-8", "ignore")
    m = re.search(re.escape(TOKEN_FIELD) + r'" value="([^"]+)"', page)
    if not m:
        raise IngestError("CSRF token not found on the download page; page layout changed")
    data = urllib.parse.urlencode({TOKEN_FIELD: m.group(1), "event": "download",
                                   "selDlFileNo": file_no}).encode()
    req = urllib.request.Request(PAGE, data=data, headers={
        "Content-Type": "application/x-www-form-urlencoded", "Referer": PAGE})
    r = op.open(req, timeout=180)
    if r.read(2) != b"PK":
        raise IngestError("response is not a zip; the form was rejected or redirected")
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    with open(dest, "wb") as out:
        out.write(b"PK")
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            out.write(chunk)
    return dest


AVG_LINE_BYTES = 174  # 1,018,000,000 bytes over 5,837,980 lines


def _bytes_since(_t0: float, lines_done: int) -> int:
    return lines_done * AVG_LINE_BYTES


def _csv_member(zip_path: str) -> str:
    with zipfile.ZipFile(zip_path) as z:
        names = [n for n in z.namelist() if n.endswith(".csv")]
    if not names:
        raise IngestError("no .csv inside the zip")
    return names[0]


def ingest(zip_path: str, db_path: str, byte_budget: int = 400 * 1024 * 1024) -> dict:
    """Load the dump, resuming by *line number*. Byte offsets drift: the file is Shift-JIS
    and len(str) != len(encoded), so a byte-based resume silently skips records -- which
    is exactly how the first run stopped at 4.5 M of 5.8 M rows and reported success."""
    con = sqlite3.connect(db_path, timeout=120)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.executescript(SCHEMA)
    con.execute("CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT)")
    row = con.execute("SELECT v FROM meta WHERE k='linepos'").fetchone()
    lineno = int(row[0]) if row else 0
    before = con.execute("SELECT count(*) FROM houjin").fetchone()[0]

    member = _csv_member(zip_path)
    t0 = time.time()
    n, added, batch = 0, 0, []
    with zipfile.ZipFile(zip_path).open(member) as f:
        raw = io.TextIOWrapper(f, encoding="cp932", errors="replace", newline="")
        while n < lineno:
            if not raw.readline():
                break
            n += 1
        while True:
            line = raw.readline()
            if not line:
                break
            n += 1
            if len(line) < 20:
                continue
            try:
                r = next(csv.reader([line]))
            except Exception:
                continue
            if len(r) < 30:
                continue
            batch.append((int(r[0]), r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9],
                          r[10], r[11], r[12], r[13], r[14], r[15], r[23], r[24], r[25],
                          r[27], r[29]))
            if len(batch) >= 50000:
                mid = con.execute("SELECT count(*) FROM houjin").fetchone()[0]
                con.executemany("INSERT OR REPLACE INTO houjin VALUES ("
                                + ",".join("?" * 21) + ")", batch)
                batch.clear()
                added += con.execute("SELECT count(*) FROM houjin").fetchone()[0] - mid
                con.execute("INSERT OR REPLACE INTO meta VALUES ('linepos', ?)", (str(n),))
                con.commit()
                if _bytes_since(t0, n) > byte_budget:
                    break
        if batch:
            con.executemany("INSERT OR REPLACE INTO houjin VALUES ("
                            + ",".join("?" * 21) + ")", batch)
            con.execute("INSERT OR REPLACE INTO meta VALUES ('linepos', ?)", (str(n),))
            con.commit()
    total = con.execute("SELECT count(*) FROM houjin").fetchone()[0]
    return {"lines_scanned": n, "rows_added": added, "rows_before": before,
            "rows_total": total, "seconds": round(time.time() - t0, 1)}


def _record(row) -> dict:
    no, name, pref, city, street, kana, kind = row
    return {"id": no, "name": (name or "").strip(), "country": "JP",
            "address": " ".join(x for x in (pref, city, street) if x) or None,
            "kind": "company", "officers": [],
            "attributes": {"kana": kana, "prefecture": pref, "corporate_kind": kind},
            "url": f"https://www.houjin-bangou.nta.go.jp/henkorireki-johoto.html?selHouzinNo={no}"}


def lookup(db_path: str, *, name: str | None = None, houjin_bangou: str | None = None,
           limit: int = 3) -> list[dict]:
    if not os.path.exists(db_path):
        return []
    con = sqlite3.connect(db_path, timeout=60)
    try:
        if houjin_bangou:
            rows = con.execute("SELECT houjin_bangou,name,pref,city,street,kana,corporate_kind "
                               "FROM houjin WHERE houjin_bangou=?", (houjin_bangou,)).fetchall()
        elif name:
            key = name.strip()
            rows = con.execute("SELECT houjin_bangou,name,pref,city,street,kana,corporate_kind "
                               "FROM houjin WHERE name=? LIMIT ?", (key, limit)).fetchall()
            if not rows:
                # Registry names carry the legal form; a bare name rarely matches exactly.
                for suffix in ("株式会社", "（株）", "有限会社", "(株)"):
                    rows = con.execute(
                        "SELECT houjin_bangou,name,pref,city,street,kana,corporate_kind "
                        "FROM houjin WHERE name=? LIMIT ?",
                        (key + suffix, limit)).fetchall()
                    if rows:
                        break
        else:
            return []
    finally:
        con.close()
    return [_record(r) for r in rows]


def stats(db_path: str) -> dict:
    if not os.path.exists(db_path):
        return {"exists": False}
    con = sqlite3.connect(db_path, timeout=60)
    try:
        n = con.execute("SELECT count(*) FROM houjin").fetchone()[0]
        pos = con.execute("SELECT v FROM meta WHERE k='linepos'").fetchone()
        return {"exists": True, "entities": n,
                "bytes": os.path.getsize(db_path), "linepos": pos[0] if pos else None}
    finally:
        con.close()
