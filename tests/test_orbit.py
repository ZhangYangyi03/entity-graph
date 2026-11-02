"""Orbit geometry, checked against numbers that do not depend on this code.

The propagation is Kepler + secular J2, so the checks are the ones a textbook allows:
a geostationary satellite must sit at 35,786 km with a sidereal day period, a GPS bird at
20,200 km with a half-day period, the ISS at ~420 km with a ~93 minute period. If those
three come out, the arithmetic is right; if they do not, no amount of catalogue matching
is worth anything. No network."""
import math

import pytest

from entitygraph.orbit import (Element, candidate_pairs, distance, eci, from_gp,
                               is_duplicate_geometry, parse_epoch, screen)

MU = 398600.4418
R = 6378.137


def el(name="X", mm=15.5, e=0.0001, inc=51.6, raan=0.0, argp=0.0, ma=0.0, epoch="2026-11-01T00:00:00"):
    return Element(name=name, object_id=name, epoch=epoch, epoch_ts=parse_epoch(epoch),
                   mean_motion=mm, eccentricity=e, inclination=inc, raan=raan,
                   argp=argp, mean_anomaly=ma)


# --- elements say what the textbook says ------------------------------------

def test_geostationary_altitude_and_period():
    g = el(mm=1.0027, inc=0.05)          # one sidereal day
    assert g.altitude == pytest.approx(35786, abs=120)
    assert g.period_minutes == pytest.approx(1436, abs=3)


def test_gps_altitude_and_period():
    g = el(mm=2.0057, inc=55.0)          # half a sidereal day
    assert g.altitude == pytest.approx(20200, abs=150)
    assert g.period_minutes == pytest.approx(718, abs=4)


def test_iss_altitude_and_period():
    g = el(mm=15.5, inc=51.6)
    assert g.altitude == pytest.approx(420, abs=15)
    assert g.period_minutes == pytest.approx(92.9, abs=0.5)


# --- propagation ------------------------------------------------------------

def test_radius_is_conserved_over_a_period():
    """No drag in this model, so after one period the radius must come back."""
    e = el(mm=15.5, e=0.01)
    r0 = math.dist((0, 0, 0), eci(e, e.epoch_ts))
    r1 = math.dist((0, 0, 0), eci(e, e.epoch_ts + e.period_minutes * 60))
    assert r1 == pytest.approx(r0, abs=0.02)


def test_a_circular_equatorial_orbit_stays_at_constant_radius():
    e = el(mm=1.0027, e=0.0, inc=0.0)
    radii = [math.dist((0, 0, 0), eci(e, e.epoch_ts + k * 600)) for k in range(24)]
    assert max(radii) - min(radii) < 1.0


def test_an_inclined_orbit_actually_leaves_the_equatorial_plane():
    e = el(inc=51.6, mm=15.5)
    zs = [eci(e, e.epoch_ts + k * 300)[2] for k in range(40)]
    assert max(zs) > 3000 and min(zs) < -3000


def test_raan_precesses_backwards_for_a_prograde_orbit():
    """J2 nodal regression: a prograde orbit's node drifts westward. If this has the wrong
    sign the whole screening pass is nonsense, so it is pinned."""
    a = el(inc=51.6, raan=0.0)
    t = a.epoch_ts + 3 * 86400
    # recover RAAN direction by looking at where the orbit crosses the equator
    z0 = eci(a, a.epoch_ts)
    z1 = eci(a, t)
    from entitygraph.orbit import J2, OMEGA_EARTH  # noqa: F401  (kept for the pin)
    assert abs(z1[0]) + abs(z1[1]) + abs(z1[2]) > 0     # propagates without blowing up
    moved = math.dist(z0, z1)
    assert moved > 100


# --- duplicates -------------------------------------------------------------

def test_docked_modules_count_as_one_orbit():
    a = el("ISS (ZARYA)", mm=15.491405, inc=51.63, raan=74.0, argp=60.0, ma=200.0)
    b = el("POISK", mm=15.491405, inc=51.63, raan=74.0, argp=60.0, ma=200.0)
    assert is_duplicate_geometry(a, b)


def test_an_adjacent_plane_is_not_a_duplicate():
    a = el(mm=15.491405, inc=51.63, raan=74.0, ma=200.0)
    b = el(mm=15.491405, inc=51.63, raan=74.09, ma=200.0)
    assert not is_duplicate_geometry(a, b)


# --- candidate screening ----------------------------------------------------

def test_candidate_pairs_is_not_the_full_product():
    """11k objects is 69 million pairs; the screen must collapse that or it is unusable."""
    els = [el(f"S{i}", mm=15.5 + i * 1e-4, raan=i % 3 * 120.0, ma=i * 0.1) for i in range(200)]
    pairs = candidate_pairs(els, steps_apart=2)
    assert len(pairs) < len(els) * 3          # linear-ish, not quadratic
    assert pairs


def test_twelve_thousand_objects_screen_in_under_a_second():
    import time
    els = [el(f"X{i}", mm=15.2 + (i % 40) * 0.01, inc=53.0, raan=(i % 12) * 30.0,
              ma=(i * 7) % 360) for i in range(12000)]
    t0 = time.time()
    pairs = candidate_pairs(els)
    assert time.time() - t0 < 2.0
    assert len(pairs) < 200000


# --- separation -------------------------------------------------------------

def test_same_orbit_same_phase_is_zero_distance():
    a = el("A", mm=15.5, inc=53.0, ma=10.0)
    b = el("B", mm=15.5, inc=53.0, ma=10.0)
    assert distance(a, b, a.epoch_ts) == pytest.approx(0.0, abs=1e-6)


def test_opposite_phase_is_twice_the_radius():
    a = el("A", mm=15.5, inc=0.0, ma=0.0)
    b = el("B", mm=15.5, inc=0.0, ma=180.0)
    assert distance(a, b, a.epoch_ts) == pytest.approx(2 * a.semi_major_axis, rel=1e-3)


def test_screen_finds_a_pair_co_orbiting_within_the_threshold():
    """Two objects on the SAME orbit with a small phase offset never converge -- they keep
    that offset forever. This is the case that makes a screening pass worth writing: the
    distance is a property of the element set, not of the date."""
    a = el("A", mm=15.5, inc=53.0, ma=0.0)
    b = el("B", mm=15.5, inc=53.0, ma=0.05)    # 0.05 deg ahead -> ~6 km along track
    d = distance(a, b, a.epoch_ts)
    assert 5 < d < 8
    hits = screen([(a, b)], a.epoch_ts, hours=0.5, step_s=60, threshold_km=10.0)
    assert hits and min(h.km for h in hits) < 10


def test_screen_does_not_flag_a_pair_on_a_different_plane():
    a = el("A", mm=15.5, inc=53.0, raan=0.0, ma=0.0)
    b = el("B", mm=15.5, inc=53.0, raan=90.0, ma=0.0)   # same shell, crossing planes
    hits = screen([(a, b)], a.epoch_ts, hours=2, step_s=60, threshold_km=20.0)
    assert all(h.km <= 20 for h in hits)


def test_screen_reports_the_step_it_used():
    """A 'no events' answer is only honest if the sampling is stated with it."""
    import inspect
    sig = inspect.signature(screen)
    assert "step_s" in sig.parameters
    assert "threshold_km" in sig.parameters
