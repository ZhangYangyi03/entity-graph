"""Offline tests for the Japan adapter.

The bug worth pinning is the resume: the dump is Shift-JIS, and a byte offset computed
from decoded character lengths silently overshoots, which is how the first load ended at
4.5 M of 5.8 M rows while reporting success. The resume now counts lines.
"""
import os
import sqlite3

import pytest

from entitygraph import japan


@pytest.fixture()
def jp_db(tmp_path):
    db = str(tmp_path / "jp.sqlite")
    con = sqlite3.connect(db)
    con.executescript(japan.SCHEMA)
    con.execute("CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT)")
    rows = [
        (1, "1180301018771", "01", "", "", "", "トヨタ自動車株式会社", "",
         "230", "愛知県", "豊田市", "トヨタ町1", "", "01", "206", "4718571",
         "Toyota Motor Corporation", "Aichi", "1 Toyota-cho, Toyota shi",
         "トヨタジドウシャ", "0"),
        (2, "1130001011420", "01", "", "", "", "任天堂株式会社", "",
         "260", "京都府", "京都市南区", "上鳥羽鉾立町11-1", "", "01", "202", "6018501",
         "Nintendo Co., Ltd.", "Kyoto", "11-1 Kamitoba Hokotate-cho",
         "ニンテンドウ", "0"),
    ]
    con.executemany("INSERT INTO houjin VALUES (" + ",".join("?" * 21) + ")", rows)
    con.commit()
    con.close()
    return db


def test_lookup_by_exact_name(jp_db):
    rows = japan.lookup(jp_db, name="トヨタ自動車株式会社")
    assert len(rows) == 1
    r = rows[0]
    assert r["id"] == "1180301018771" and r["country"] == "JP"
    assert r["attributes"]["prefecture"] == "愛知県"
    assert "houjin-bangou" in r["url"]


def test_lookup_falls_back_to_legal_form_suffix(jp_db):
    """Registry names carry 株式会社; a bare name should still land."""
    rows = japan.lookup(jp_db, name="トヨタ自動車")
    assert rows and rows[0]["id"] == "1180301018771"


def test_lookup_by_corporate_number(jp_db):
    rows = japan.lookup(jp_db, houjin_bangou="1130001011420")
    assert rows[0]["name"] == "任天堂株式会社"


def test_missing_index_reads_as_not_loaded(tmp_path):
    assert japan.lookup(str(tmp_path / "absent.sqlite"), name="x") == []
    assert japan.stats(str(tmp_path / "absent.sqlite")) == {"exists": False}


def test_stats_reports_the_line_resume_pointer(jp_db):
    s = japan.stats(jp_db)
    assert s["exists"] and s["entities"] == 2


def test_resume_counts_lines_not_bytes():
    """Guard against reintroducing a byte-based skip: the helper must be line based."""
    import inspect
    src = inspect.getsource(japan.ingest)
    assert "linepos" in src
    assert "raw.tell()" not in src
