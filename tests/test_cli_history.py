"""The history subcommand, end to end, on a graph file shaped like the ones this
repository ships. No network."""
import json
import os
import subprocess
import sys

import pytest

from entitygraph.cli import main

HERE = os.path.dirname(os.path.abspath(__file__))
APPLE = os.path.join(os.path.dirname(HERE), "apple.graph.json")


def run(capsys, *argv):
    rc = main(list(argv))
    return rc, capsys.readouterr().out


@pytest.mark.skipif(not os.path.exists(APPLE), reason="apple.graph.json not present")
def test_check_reports_the_graph_is_fully_dated(capsys):
    rc, out = run(capsys, "history", "--graph", APPLE, "--check")
    assert rc == 0
    assert "undated 0" in out


@pytest.mark.skipif(not os.path.exists(APPLE), reason="apple.graph.json not present")
def test_slices_grow_over_the_years(capsys):
    """The shape the feature exists for: four dates, four different graphs."""
    counts = {}
    for d in ("2019-06-01", "2021-06-01", "2023-06-01", "2024-03-01"):
        rc, out = run(capsys, "history", "--graph", APPLE, "--as-of", d)
        assert rc == 0
        counts[d] = int(out.split("edges")[1].split()[0])
    assert counts["2019-06-01"] < counts["2021-06-01"] < counts["2023-06-01"] < counts["2024-03-01"]


@pytest.mark.skipif(not os.path.exists(APPLE), reason="apple.graph.json not present")
def test_segments_are_contiguous_and_cover_the_range(capsys):
    rc, out = run(capsys, "history", "--graph", APPLE, "--between", "2018-01-01", "2025-01-01")
    assert rc == 0
    rows = [l for l in out.strip().splitlines() if ".." in l]
    assert rows[0].startswith("2018-01-01")
    assert rows[-1].split("..")[1].split()[0] == "2025-01-01"
    for a, b in zip(rows, rows[1:]):
        assert a.split("..")[1].split()[0] == b.split("..")[0].strip()


@pytest.mark.skipif(not os.path.exists(APPLE), reason="apple.graph.json not present")
def test_first_segment_is_empty_before_any_filing(capsys):
    rc, out = run(capsys, "history", "--graph", APPLE, "--as-of", "2010-01-01")
    assert rc == 0
    assert "edges 0" in out
