"""SGP4 as the propagator: the parts where it disagrees with the first version.

The first version of this module propagated with Kepler plus secular J2, ignoring drag. It
found a "closest approach of 4.483 km" between two Starlinks on the 2026-11-02 catalogue.
SGP4 puts the same pair 1,937 km apart at that instant. This file holds the checks that make
that difference a test rather than a story.
"""
import math

import pytest

from entitygraph import orbit
from entitygraph.orbit import Element, _gp_to_tle, distance, from_gp, sgp4_eci


def element(**kwargs):
    base = dict(name="TEST", object_id="2026-001A", epoch="2026-11-01T00:00:00",
                epoch_ts=1793491200.0, mean_motion=15.5, eccentricity=1e-4,
                inclination=53.0, raan=100.0, argp=90.0, mean_anomaly=0.0,
                norad_id=99999)
    base.update(kwargs)
    return Element(**base)


# ------------------------------------------------------------------ the TLE rebuild

def test_rebuilt_lines_parse_and_keep_the_catalogue_number():
    el = element(norad_id=25544, object_id="1998-067A", mean_motion=15.49140542,
                 eccentricity=0.0007086, inclination=51.6332, raan=340.4448,
                 argp=335.8887, mean_anomaly=24.1768, bstar=0.00011739,
                 ndot=0.00006056)
    line1, line2 = _gp_to_tle(el)
    from entitygraph._sgp4.model import Satrec
    satrec = Satrec.twoline2rv(line1, line2)
    assert satrec.satnum == 25544


def test_a_six_digit_catalogue_number_uses_alpha5():
    """TLE has five columns for the number, so Starlink's 100408 has to be written 'A0408'."""
    el = element(norad_id=100408, object_id="2026-192E")
    line1, _line2 = _gp_to_tle(el)
    assert line1[2:7] == "A0408"
    from entitygraph._sgp4.model import Satrec
    from entitygraph._sgp4.alpha5 import to_alpha5
    satrec = Satrec.twoline2rv(line1, _gp_to_tle(el)[1])
    assert satrec.satnum == 100408
    assert to_alpha5(100408) == "A0408"


def test_the_designator_is_compressed_not_displayed():
    """'1998-067A' is how CelesTrak displays it; a TLE wants '98067A' in 8 columns."""
    el = element(object_id="1998-067A")
    line1, _ = _gp_to_tle(el)
    assert line1[9:17] == "98067A  "


def test_a_long_piece_does_not_overflow_the_field():
    el = element(object_id="2019-074AL")
    line1, _ = _gp_to_tle(el)
    assert line1[9:17] == "19074AL "


def test_bstar_is_written_in_the_tle_exponent_convention():
    """0.00011739 goes in as ' 11739-3': mantissa is 0.NNNNN, so the exponent is one up."""
    el = element(bstar=0.00011739)
    line1, _ = _gp_to_tle(el)
    assert line1[53:61] == " 11739-3"


# ------------------------------------------------- the disagreement with the J2 version

def test_at_epoch_the_two_propagators_are_kilometres_apart():
    """Not a regression: this is the size of the short-period terms the closed form omits."""
    el = element(mean_motion=15.5, eccentricity=0.0007, inclination=51.6)
    position_sgp4, error = sgp4_eci(el, el.epoch_ts)
    assert error == 0
    position_j2 = orbit.eci(el, el.epoch_ts)
    gap = math.dist(position_sgp4, position_j2)
    assert 5.0 < gap < 20.0, gap


def test_distance_returns_infinity_when_sgp4_reports_an_error():
    """A decayed object drops out of a screening pass instead of poisoning it with NaN."""
    a = element(name="A")
    b = element(name="B")
    # 33334 in SGP4-VER.TLE: a mean motion so small the eccentricity leaves [0,1] at once.
    line1 = "1 33334U 78066F   06174.85818871  .00000620  00000-0  10000-3 0  6809"
    line2 = "2 33334  68.4714 236.1303 5602877 123.7484 302.5767  0.00001000 67521"
    broken = element(name="BROKEN", line1=line1, line2=line2)
    assert distance(broken, b, broken.epoch_ts) == math.inf


# ------------------------------------------------------------ the false 4.483 km pair

STARLINK_PAIR = {
    # STARLINK-35792 and STARLINK-35841, as the 2026-11-02 catalogue printed them. Both are
    # low and decaying: ndot 0.0199 rev/day^2 and bstar 0.0652, a hundred times the median.
    "a": dict(name="STARLINK-35792", object_id="2025-298T", norad_id=67092,
              epoch="2026-11-01T12:00:00.998784", epoch_ts=1793534400.998784,
              mean_motion=15.28285453, eccentricity=0.0003344, inclination=70.0054,
              raan=136.4862, argp=262.2917, mean_anomaly=28.0429,
              ndot=0.01994694, bstar=0.065205),
    "b": dict(name="STARLINK-35841", object_id="2025-298M", norad_id=67086,
              epoch="2026-11-01T20:11:10.430016", epoch_ts=1793563870.430016,
              mean_motion=15.29585296, eccentricity=0.0002901, inclination=70.0029,
              raan=135.5852, argp=274.6402, mean_anomaly=85.4442,
              ndot=0.00007054, bstar=0.0024958),
}


def test_the_pair_that_looked_like_four_kilometres_is_not_close_under_sgp4():
    a = Element(**STARLINK_PAIR["a"])
    b = Element(**STARLINK_PAIR["b"])
    t0 = max(a.epoch_ts, b.epoch_ts)
    hits = orbit.screen([(a, b)], t0 - 1800, hours=1.0, step_s=120.0,
                        threshold_km=25.0, propagator="sgp4")
    assert hits == [], [h.km for h in hits]


def test_the_j2_version_still_reports_it_and_that_is_the_point():
    """The old propagator's answer, kept so the difference stays visible and testable."""
    a = Element(**STARLINK_PAIR["a"])
    b = Element(**STARLINK_PAIR["b"])
    t = 1793663757.0          # 2026-11-02T23:55:57Z, the instant the old sweep reported
    assert distance(a, b, t, propagator="j2") < 5.0
    assert math.dist(sgp4_eci(a, t)[0], sgp4_eci(b, t)[0]) > 1000.0


def test_their_mean_motions_do_not_meet_inside_a_two_day_window():
    """Independent of the propagator: the phase difference is 4.7 deg/day apart, and the
    drag term changes the mean motion by more than twice the gap between them."""
    a = Element(**STARLINK_PAIR["a"])
    b = Element(**STARLINK_PAIR["b"])
    drift_per_day = abs(b.mean_motion - a.mean_motion) * 360.0
    assert drift_per_day < 10.0
    assert abs(a.ndot) * 1.54 > 2.0 * abs(b.mean_motion - a.mean_motion)


# ------------------------------------------------------------------- refining a minimum

def test_refine_finds_a_lower_minimum_than_the_sampling_step():
    """120 s sampling overstates the minimum; golden-section search does not."""
    a = Element(**STARLINK_PAIR["a"])
    b = Element(**STARLINK_PAIR["b"])
    t0 = max(a.epoch_ts, b.epoch_ts)
    coarse = min(
        ((distance(a, b, t0 + k * 120.0, "j2"), t0 + k * 120.0) for k in range(31)),
    )
    km, _t = orbit.refine(a, b, coarse[1], propagator="j2", half_span_s=120.0)
    assert km <= coarse[0] + 1e-9


def test_screen_collapses_consecutive_samples_into_one_event():
    """One approach inside the threshold for several samples is one row, not five."""
    a = Element(**STARLINK_PAIR["a"])
    b = Element(**STARLINK_PAIR["b"])
    t0 = max(a.epoch_ts, b.epoch_ts)
    hits = orbit.screen([(a, b)], t0 - 1800, hours=1.0, step_s=60.0,
                        threshold_km=2000.0, propagator="j2")
    assert len(hits) == 1
    assert hits[0].sampled_km >= hits[0].km


def test_screen_reports_the_step_it_used():
    """A "no events" answer means "none at this sampling", so the sampling is on the result."""
    a = Element(**STARLINK_PAIR["a"])
    b = Element(**STARLINK_PAIR["b"])
    t0 = max(a.epoch_ts, b.epoch_ts)
    hits = orbit.screen([(a, b)], t0, hours=0.1, step_s=30.0, threshold_km=1.0)
    assert hits == []
