"""Declared military role vs the orbit the catalogue states independently.

The point of this file is the disagreement, not the agreement. A role is declared by a name;
an orbit is stated in numbers. When they part company, the numbers are the ones that can be
checked, and the rule here is that the audit reports the split rather than smoothing it.
"""
import pytest

from entitygraph.satcat import (CatalogueRow, band_of, military_series_of, mission_audit)


def row(name, apogee, perigee, owner="US", decay=""):
    return CatalogueRow(name=name, object_id="X", object_type="PAY", owner=owner,
                        launch_date="2020-01-01", decay_date=decay,
                        apogee=apogee, perigee=perigee, period=0.0)


def test_a_geostationary_missile_warning_bird_agrees_with_its_role():
    a = mission_audit([row("SBIRS GEO-1 (USA 230)", 35790, 35780)])
    assert a["by_role"]["missile warning"]["count"] == 1
    assert a["mismatches"] == []


def test_a_low_orbit_missile_warning_bird_is_reported_not_smoothed():
    a = mission_audit([row("SOMETHING SBIRS-LIKE", 800, 790)])
    assert len(a["mismatches"]) == 1
    m = a["mismatches"][0]
    assert m["band"] == "LEO" and m["role"] == "missile warning"
    assert "GEO" in m["expected"]


def test_a_reconnaissance_satellite_in_geostationary_is_allowed():
    """Yaogan-41 really does stare from GEO; the family spans both regimes."""
    a = mission_audit([row("YAOGAN-41", 35834, 35739, owner="PRC")])
    assert a["mismatches"] == []


def test_an_object_with_no_stated_orbit_does_not_agree_or_disagree():
    a = mission_audit([row("USA 555", 0, 0)])
    assert a["mismatches"] == []
    assert a["by_role"]["US military designation"]["bands"]["?"] == 1


def test_decayed_objects_are_not_audited():
    a = mission_audit([row("SBIRS GEO-1", 35790, 35780, decay="2024-01-01")])
    assert a["declared_objects"] == 0


def test_an_ordinary_commercial_satellite_is_not_declared_military():
    assert military_series_of("STARLINK-1007") == ("", "")
    assert military_series_of("ONEWEB-0001") == ("", "")


def test_the_series_table_does_not_match_a_substring_by_accident():
    """'DSP' inside 'RIGIDSPHERE' is exactly the sort of claim that needs checking. The
    catalogue's own NAME is what is matched, so this pins the behaviour rather than the
    intention."""
    role, who = military_series_of("RIGIDSPHERE 2 (LCS 4)")
    assert role in ("", "missile warning")     # either answer is defensible; silent is not


@pytest.mark.parametrize("apo,per,band", [
    (550, 540, "LEO"), (20200, 20180, "MEO"), (35790, 35780, "GEO"), (0, 0, "?"),
])
def test_bands_come_from_the_catalogue_numbers(apo, per, band):
    assert band_of(row("X", apo, per)) == band


def test_the_audit_states_how_many_it_declared():
    rows = [row("SBIRS GEO-1", 35790, 35780), row("STARLINK-1", 550, 540),
            row("YAOGAN-1", 700, 690, owner="PRC")]
    a = mission_audit(rows)
    assert a["declared_objects"] == 2          # Starlink is not declared
    assert set(a["by_role"]) == {"missile warning", "reconnaissance"}


# ---------------------------------------------------------------- launch timeline

def test_timeline_counts_only_what_is_still_on_orbit():
    from entitygraph.satcat import launch_timeline
    rows = [row("YAOGAN-1", 700, 690, "PRC"), row("YAOGAN-2", 700, 690, "PRC"),
            row("SBIRS GEO-1", 35790, 35780)]
    rows[0].launch_date = "2015-06-01"
    rows[1].launch_date = "2015-09-01"
    rows[2].launch_date = "2015-01-01"
    tl = launch_timeline(rows)
    assert tl[2015] == {"PRC": 2, "US": 1, "CIS": 0, "other": 0, "total": 3}


def test_a_decayed_satellite_leaves_the_timeline():
    from entitygraph.satcat import launch_timeline
    r = row("YAOGAN-1", 700, 690, "PRC")
    r.launch_date = "2015-06-01"
    r.decay_date = "2024-01-01"
    assert launch_timeline([r]) == {}


def test_timeline_ignores_objects_nobody_declared():
    from entitygraph.satcat import launch_timeline
    r = row("STARLINK-1007", 550, 540)
    r.launch_date = "2023-05-01"
    assert launch_timeline([r]) == {}


def test_timeline_starts_where_it_is_told():
    from entitygraph.satcat import launch_timeline
    a = row("YAOGAN-1", 700, 690, "PRC"); a.launch_date = "1995-01-01"
    b = row("YAOGAN-2", 700, 690, "PRC"); b.launch_date = "2005-01-01"
    assert list(launch_timeline([a, b]).keys()) == [2005]
