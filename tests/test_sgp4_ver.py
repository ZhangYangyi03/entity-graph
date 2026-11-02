"""Does the vendored SGP4 reproduce the published verification vectors?

The reference is `tcppver.out`, the output of Vallado's own testcpp.cpp run over the 33
cases in SGP4-VER.TLE. It is 700 lines long and the comparison is field by field, the way
the reference implementation's own test suite does it: the first seven numbers to 2e-7,
the rest as text.

Without this file the vendored code is an assertion that it works. With it, it is a
measured claim.
"""
import math
import os

import pytest

from entitygraph._sgp4.earth_gravity import wgs72
from entitygraph._sgp4.ext import invjday, rv2coe
from entitygraph._sgp4.io import twoline2rv
from entitygraph._sgp4.model import Satrec

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "entitygraph", "_sgp4", "data")

TOLERANCE = 2e-7
RAD = 180.0 / math.pi

# The cases whose propagation is expected to fail, in file order. From the reference
# implementation's own suite: a negative mean eccentricity twice, a decayed satellite,
# a negative semilatus rectum, an eccentricity outside [0,1], and an underground one.
EXPECTED_ERRORS = [1, 1, 6, 6, 4, 3, 6]


def _lines():
    path = os.path.join(DATA, "SGP4-VER.TLE")
    with open(path, encoding="ascii") as handle:
        return handle.read().splitlines()


def _cases():
    """(line1, line2, tstart, tend, tstep) for each of the 33 verification cases."""
    lines = _lines()
    out = []
    index = 0
    while index < len(lines):
        line1 = lines[index]
        if not line1.startswith("1 "):
            index += 1
            continue
        line2 = lines[index + 1]
        tstart, tend, tstep = (float(field) for field in line2[69:].split())
        out.append((line1, line2, tstart, tend, tstep))
        index += 2
    return out


def _reference():
    path = os.path.join(DATA, "tcppver.out")
    with open(path, encoding="ascii") as handle:
        return handle.read().replace("\r", "").splitlines(True)


def _run():
    """Reproduce testcpp.cpp's 700 lines, and the errors it expects along the way."""
    errors = []
    lines = []
    for line1, line2, tstart, tend, tstep in _cases():
        satrec = Satrec.twoline2rv(line1, line2)
        lines.append("%ld xx\n" % (satrec.satnum,))
        block, error = _one_satellite(satrec, tstart, tend, tstep)
        if error is not None:
            errors.append(error)
        lines.extend(block)
    return lines, errors


def _one_satellite(satrec, tstart, tend, tstep):
    """One block of output. Returns (lines, first error that stopped it).

    The reference behaviour this mirrors: an error does not just stop the sample being
    printed, it abandons the whole satellite. Writing `break` where the original says
    `return` gets a 71-line block where the reference has 70, which is how this was found.
    """
    out = []

    error, position, velocity = satrec.sgp4_tsince(0.0)
    if any(math.isnan(value) for value in position):
        out.append("(Use previous data line)\n")
        return out, error
    out.append(_short_line(0.0, position, velocity))

    tsince = tstart
    while tsince <= tend:
        if tsince == tstart == 0.0:
            tsince += tstep
            continue
        error, position, velocity = satrec.sgp4_tsince(tsince)
        if error != 0:
            return out, error
        out.append(_long_line(satrec, tsince, position, velocity))
        tsince += tstep

    if tsince - tend < tstep - 1e-6:
        error, position, velocity = satrec.sgp4_tsince(tend)
        if error != 0:
            return out, error
        out.append(_long_line(satrec, tend, position, velocity))

    return out, None


def _short_line(tsince, position, velocity):
    return " %16.8f %16.8f %16.8f %16.8f %12.9f %12.9f %12.9f\n" % (
        tsince, position[0], position[1], position[2],
        velocity[0], velocity[1], velocity[2])


def _long_line(satrec, tsince, position, velocity):
    short = _short_line(tsince, position, velocity).strip("\n")
    jd = satrec.jdsatepoch + satrec.jdsatepochF + tsince / 1440.0
    year, month, day, hour, minute, second = invjday(jd)
    (p, a, ecc, incl, node, argp, nu, m, arglat, truelon, lonper) = rv2coe(
        position, velocity, wgs72.mu)
    return short + (
        " %14.6f %8.6f %10.5f %10.5f %10.5f %10.5f %10.5f"
        " %5i%3i%3i %2i:%2i:%9.6f\n"
    ) % (a, ecc, incl * RAD, node * RAD, argp * RAD, nu * RAD, m * RAD,
         year, month, day, hour, minute, second)


def test_the_reference_file_is_the_size_it_claims():
    assert len(_reference()) == 700
    assert len(_cases()) == 33


def test_every_line_of_tcppver_matches():
    reference = _reference()
    produced, _ = _run()

    assert len(produced) == len(reference) == 700

    previous = None
    for lineno, (expected, actual) in enumerate(zip(reference, produced), start=1):
        if actual == "(Use previous data line)\n":
            actual = "       0.00000000" + previous[17:107]

        if "xx" in actual:
            assert actual == expected, "line %d" % lineno
        else:
            expected_fields = expected.split()
            actual_fields = actual.split()
            assert len(actual_fields) == len(expected_fields), "line %d" % lineno
            for a, e in zip(actual_fields[:7], expected_fields[:7]):
                assert -TOLERANCE < float(a) - float(e) < TOLERANCE, (
                    "line %d: %s vs %s" % (lineno, a, e))
            assert actual_fields[7:] == expected_fields[7:], "line %d" % lineno

        if "xx" not in actual:
            previous = actual


def test_the_cases_that_must_fail_do_fail():
    lines, errors = _run()
    assert len(lines) == 700
    assert errors == EXPECTED_ERRORS


def test_a_clean_case_is_good_to_the_last_digit():
    """One case checked against the printed digits rather than a tolerance."""
    case = _cases()[0]
    satrec = Satrec.twoline2rv(case[0], case[1])
    error, position, velocity = satrec.sgp4_tsince(0.0)
    assert error == 0
    assert abs(position[0] - 7022.46529266) < 1e-8
    assert abs(position[1] - (-1400.08296755)) < 1e-8
    assert abs(position[2] - 0.03995155) < 1e-8
