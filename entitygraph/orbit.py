"""Where a satellite is, and how close two of them get.

Two propagators, and the difference between them is the point of this module.

The default is SGP4, via the vendored copy in `_sgp4` -- the same algorithm operators use,
reproducing Vallado's published verification vectors to 2e-7 km (tests/test_sgp4_ver.py
checks all 700 lines of tcppver.out). It needs the TLE, not just the GP mean elements,
because its drag model reads BSTAR from line 1.

Kept alongside it is the first version: Kepler plus secular J2, closed-form, using only the
GP elements. It is still here because it is checkable by hand and because the difference
between the two is a number rather than an opinion -- `cli orbit compare` prints how far
apart they drift on the same objects. That number is the reason the default changed.

What neither makes this: a conjunction assessment. Real operators propagate the full
catalogue including debris and refine each candidate's closest approach. This is a
screening pass at a stated sampling step -- it says "these two were within N km at this
sample", and prints the step, so the reader can tell what was swept and what was not.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

MU = 398600.4418          # km^3/s^2
R_EARTH = 6378.137        # km
J2 = 1.08262668e-3
OMEGA_EARTH = 7.2921150e-5  # rad/s


@dataclass
class Element:
    """One mean element set (what CelesTrak GP hands over)."""
    name: str
    object_id: str
    epoch: str            # ISO
    epoch_ts: float       # seconds since unix epoch, for the prop math
    mean_motion: float    # rev/day
    eccentricity: float
    inclination: float    # deg
    raan: float           # deg
    argp: float           # deg
    mean_anomaly: float   # deg
    norad_id: int = 0
    line1: str = ""       # the TLE itself: SGP4's drag model needs BSTAR from line 1
    line2: str = ""
    bstar: float = 0.0
    ndot: float = 0.0     # rev/day^2, as printed in the TLE -- the drag term it states

    @property
    def semi_major_axis(self) -> float:
        n = self.mean_motion * 2 * math.pi / 86400.0
        return (MU / (n * n)) ** (1.0 / 3.0)

    @property
    def altitude(self) -> float:
        return self.semi_major_axis - R_EARTH

    @property
    def period_minutes(self) -> float:
        return 1440.0 / self.mean_motion


def parse_epoch(iso: str) -> float:
    """CelesTrak writes 2026-11-01T18:20:00.163392 (no timezone, meaning UTC)."""
    from datetime import datetime, timezone
    s = iso.strip().replace("Z", "")
    if "." in s:
        head, frac = s.split(".", 1)
        s = head + "." + (frac + "000000")[:6]
    dt = datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
    return dt.timestamp()


def from_gp(rec: dict) -> Element:
    return Element(
        name=rec.get("OBJECT_NAME", "").strip(),
        object_id=rec.get("OBJECT_ID", "").strip(),
        epoch=rec.get("EPOCH", ""),
        epoch_ts=parse_epoch(rec.get("EPOCH", "")),
        mean_motion=float(rec["MEAN_MOTION"]),
        eccentricity=float(rec["ECCENTRICITY"]),
        inclination=float(rec["INCLINATION"]),
        raan=float(rec["RA_OF_ASC_NODE"]),
        argp=float(rec["ARG_OF_PERICENTER"]),
        mean_anomaly=float(rec["MEAN_ANOMALY"]),
        norad_id=int(rec.get("NORAD_CAT_ID") or 0),
        bstar=float(rec.get("BSTAR") or 0.0),
        ndot=float(rec.get("MEAN_MOTION_DOT") or 0.0),
    )


def _kepler(M: float, e: float, iters: int = 8) -> float:
    """Solve M = E - e sin E. Eight Newton steps is far more than a mean element needs."""
    E = M
    for _ in range(iters):
        f = E - e * math.sin(E) - M
        fp = 1.0 - e * math.cos(E)
        if fp == 0:
            break
        E -= f / fp
    return E


def eci(el: Element, t: float) -> tuple[float, float, float]:
    """Position in a pseudo-inertial frame at unix time t (seconds).

    Secular J2 drift in RAAN, argument of perigee and mean anomaly. Not SGP4: no drag, and
    no short-period terms -- and the second one bites immediately rather than eventually.

    Measured against SGP4 on 20 real element sets at their own epoch (cli orbit compare):
    the two positions differ by about 11.7 km, and 11 of those kilometres are along-track
    phase, equivalent to a 1.55 s timing offset; 0.75 km is radial. The whole disagreement
    is there at t=0, before any propagation, which is what "no short-period terms" means in
    kilometres. Over a day it grows to 60 km median at 1 day and 540 km at 7.

    So: fine for "which shell is this in", not fine for "how close did these two get". The
    default propagator is SGP4 for exactly that reason.
    """
    a = el.semi_major_axis
    e = el.eccentricity
    i = math.radians(el.inclination)
    n0 = el.mean_motion * 2 * math.pi / 86400.0           # rad/s
    p = a * (1 - e * e)
    cos_i = math.cos(i)
    factor = 1.5 * J2 * (R_EARTH / p) ** 2 * n0
    raan_dot = -factor * cos_i
    argp_dot = factor * (2.0 - 2.5 * math.sin(i) ** 2)
    m_dot = n0 * (1.0 + 1.5 * J2 * (R_EARTH / p) ** 2 * math.sqrt(1 - e * e) * (1 - 1.5 * math.sin(i) ** 2))

    dt = t - el.epoch_ts
    raan = math.radians(el.raan) + raan_dot * dt
    argp = math.radians(el.argp) + argp_dot * dt
    M = math.radians(el.mean_anomaly) + m_dot * dt
    M = M % (2 * math.pi)

    E = _kepler(M, e)
    xv = a * (math.cos(E) - e)
    yv = a * math.sqrt(1 - e * e) * math.sin(E)

    cw, sw = math.cos(argp), math.sin(argp)
    co, so = math.cos(raan), math.sin(raan)
    ci, si = cos_i, math.sin(i)
    x = (co * cw - so * sw * ci) * xv + (-co * sw - so * cw * ci) * yv
    y = (so * cw + co * sw * ci) * xv + (-so * sw + co * cw * ci) * yv
    z = (sw * si) * xv + (cw * si) * yv
    return x, y, z


def _gp_to_tle(el: Element) -> tuple[str, str]:
    """Put the two TLE lines back together from a CelesTrak GP record.

    CelesTrak's JSON is the TLE already parsed into fields, so the lines can be rebuilt
    without asking the network for a text copy. The trick is that it is a *fixed-column*
    format, and one field CelesTrak hands over does not fit where it goes: OBJECT_ID is the
    international designator with the hyphens added for display ("1998-067A"), while a TLE
    wants it compressed ("98067A"). Writing the display form shifts every following column
    by two and the parser rejects the line -- which is how this was found.

    Checksums are written as 0. SGP4 does not verify them, and a real checksum here would be
    a claim this function is in no position to make.
    """
    from datetime import datetime, timezone

    # The designator goes in as 2-digit-year + launch number + piece: "98067A".
    # CelesTrak's OBJECT_ID is the display form, "1998-067A", which does not fit a TLE.
    # The piece is up to three characters ("074AH"), and the field is 8 wide, so a long
    # piece simply takes the space the padding would have used.
    if "-" in el.object_id:
        year, rest = el.object_id.split("-", 1)      # "2019" + "074AL" -- one hyphen, not two
    else:
        year, rest = "", el.object_id
    object_id = (year[2:] + rest)[:8].ljust(8)

    # --- line 2: fixed columns, one decimal place per field, exactly as printed
    ecc = f"{el.eccentricity:.7f}"[2:9]                      # drop the "0."
    mean_motion = f"{el.mean_motion:11.8f}"
    rev = f"{int(round(el.epoch_ts / 86400.0)) % 100000:5d}"
    from ._sgp4.alpha5 import to_alpha5     # a 6-digit catalogue number needs letters
    satnum = to_alpha5(el.norad_id)

    line2 = (f"2 {satnum} {el.inclination:8.4f} {el.raan:8.4f} "
             f"{ecc} {el.argp:8.4f} {el.mean_anomaly:8.4f} {mean_motion}{rev}")

    # --- line 1: epoch as YYDDD.DDDDDDDD, then the drag terms in their exponent form
    dt = datetime.fromtimestamp(el.epoch_ts, timezone.utc)
    jan1 = datetime(dt.year, 1, 1, tzinfo=timezone.utc).timestamp()
    day_of_year = (el.epoch_ts - jan1) / 86400.0 + 1.0
    epoch = f"{dt.year % 100:02d}{day_of_year:012.8f}"

    mantissa = f"{abs(el.ndot):.8f}"                      # "0.00006056"
    ndot = ("-" if el.ndot < 0 else " ") + mantissa[1:]   # " .00006056", integer digit dropped

    def _exp_form(value: float) -> str:
        """TLE writes 2.8098e-5 as ' 28098-4'.

        The mantissa is 0.NNNNN, not N.NNNNN, so the exponent is one higher than the
        scientific one and the digits are the ones after the point. Writing ' 11739-4' for
        0.00011739 is what this got wrong first: the value is right, the encoding of it was
        not, and a drag term off by a factor of ten is a drag term.
        """
        if value == 0:
            return " 00000-0"
        exponent = int(f"{abs(value):.4e}".split("e")[1])
        digits = f"{abs(value):.4e}".split("e")[0].replace(".", "")[:5]
        sign = "-" if value < 0 else " "
        return f"{sign}{digits}{exponent + 1:+d}"

    line1 = (f"1 {satnum}U {object_id} {epoch} "
             f"{ndot} {_exp_form(0.0)} {_exp_form(el.bstar)} 0    0")
    return line1, line2


def _satrec(el: Element):
    """The compiled SGP4 state for one element set, built once and cached on the object."""
    cached = getattr(el, "_satrec", None)
    if cached is not None:
        return cached
    from ._sgp4.model import Satrec
    line1, line2 = (el.line1, el.line2) if el.line1 else _gp_to_tle(el)
    satrec = Satrec.twoline2rv(line1, line2)
    el._satrec = satrec                      # noqa: SLF001 - a per-object cache
    return satrec


def sgp4_eci(el: Element, t: float):
    """Position in TEME at unix time t, in km. Returns (position, error)."""
    satrec = _satrec(el)
    tsince = (t - el.epoch_ts) / 60.0
    error, position, _velocity = satrec.sgp4_tsince(tsince)
    return position, error


def distance(el_a: Element, el_b: Element, t: float,
             propagator: str = "sgp4") -> float:
    """Separation in km at unix time t.

    `propagator` selects sgp4 (default) or j2 (the hand-written closed form). A pair whose
    SGP4 state has errored -- a decayed object, an impossible eccentricity -- returns
    infinity rather than a NaN, so it drops out of a screening pass instead of poisoning
    the comparison.
    """
    if propagator == "sgp4":
        pa, ea = sgp4_eci(el_a, t)
        pb, eb = sgp4_eci(el_b, t)
        if ea or eb:
            return math.inf
        ax, ay, az = pa
        bx, by, bz = pb
    else:
        ax, ay, az = eci(el_a, t)
        bx, by, bz = eci(el_b, t)
    return math.sqrt((ax - bx) ** 2 + (ay - by) ** 2 + (az - bz) ** 2)


@dataclass
class CloseApproach:
    a: str
    b: str
    t: float                 # unix seconds of the closest sample
    km: float                # distance at the refined closest approach
    a_id: str = ""
    b_id: str = ""
    sampled_km: float = 0.0  # what the sampling step reported before refinement

    @property
    def iso(self) -> str:
        from datetime import datetime, timezone
        return datetime.fromtimestamp(self.t, timezone.utc).isoformat(timespec="seconds")


def refine(a: Element, b: Element, t: float, propagator: str = "sgp4",
           half_span_s: float = 120.0, tolerance_s: float = 1e-3) -> tuple[float, float]:
    """Narrow a sampled minimum down to the pair's actual closest approach.

    Sampling reports the smallest distance at a sample, which is always larger than the real
    minimum between samples. Golden-section search over the bracket finds the minimum of the
    distance function, not of the sample set. Measured on the 2026-11-02 sweep: sampling at
    120 s overstates the true minimum by 0.01 to 0.73 km, which is the same size as the
    distances being reported -- hence this function.

    Assumes the function is unimodal inside the bracket. That holds while the relative
    velocity stays roughly constant over the two-minute bracket, which at 7.5 km/s means the
    objects have not yet turned around inside it.
    """
    golden = (math.sqrt(5.0) - 1.0) / 2.0
    lo, hi = t - half_span_s, t + half_span_s
    c = hi - golden * (hi - lo)
    d = lo + golden * (hi - lo)
    fc = distance(a, b, c, propagator)
    fd = distance(a, b, d, propagator)
    while hi - lo > tolerance_s:
        if fc < fd:
            hi, d, fd = d, c, fc
            c = hi - golden * (hi - lo)
            fc = distance(a, b, c, propagator)
        else:
            lo, c, fc = c, d, fd
            d = lo + golden * (hi - lo)
            fd = distance(a, b, d, propagator)
    t_best = (lo + hi) / 2.0
    return distance(a, b, t_best, propagator), t_best


def screen(pairs, t0: float, hours: float, step_s: float = 60.0,
           threshold_km: float = 1.0, propagator: str = "sgp4",
           refine_winners: bool = True):
    """Walk a time window, report every pair that comes within threshold_km.

    A pair that stays inside the threshold for several consecutive samples is one event, not
    five: the samples are collapsed to the nearest one and then refined. Reporting each
    sample separately was the first behaviour here, and on a real Starlink pair it turned one
    approach into eight rows.
    """
    n_steps = max(1, int(int(hours * 3600) // int(step_s)))
    closest: dict[tuple[str, str], tuple[float, float]] = {}
    for k in range(n_steps + 1):
        t = t0 + k * step_s
        for a, b in pairs:
            # Cheap reject first: objects in shells this far apart cannot close to a
            # kilometre inside a window this short.
            if abs(a.semi_major_axis - b.semi_major_axis) > 200:
                continue
            dkm = distance(a, b, t, propagator)
            if dkm <= threshold_km:
                key = (a.name, b.name)
                if key not in closest or dkm < closest[key][0]:
                    closest[key] = (dkm, t)

    hits: list[CloseApproach] = []
    by_name_a = {a.name: a for a, _ in pairs}
    by_name_b = {b.name: b for _, b in pairs}
    for (name_a, name_b), (sampled_km, sampled_t) in closest.items():
        a = by_name_a.get(name_a) or by_name_b.get(name_a)
        b = by_name_a.get(name_b) or by_name_b.get(name_b)
        if refine_winners and a is not None and b is not None:
            km, t = refine(a, b, sampled_t, propagator)
        else:
            km, t = sampled_km, sampled_t
        hits.append(CloseApproach(a=name_a, b=name_b, t=t, km=km,
                                  a_id=a.object_id if a else "",
                                  b_id=b.object_id if b else "",
                                  sampled_km=sampled_km))
    hits.sort(key=lambda h: h.km)
    return hits


def is_duplicate_geometry(a: Element, b: Element) -> bool:
    """True ONLY when every published element is bit-identical.

    Kept deliberately strict. An earlier version loosened the tolerances to 0.15 km / 0.01
    deg to make a suspicious cluster of STARLINK pairs disappear; measured on the 2026-11-02
    sweep, those pairs differ by 3 to 8 km of semi-major axis with epochs hours apart, which
    is exactly the signature of two satellites drifting in the same shell -- a real approach,
    not a catalogue artefact. Widening the tolerance would have deleted the finding. The
    docked-module case this function exists for is bit-exact, so bit-exact is the test.
    """
    tol_km, tol_deg = 1e-6, 1e-6
    if abs(a.semi_major_axis - b.semi_major_axis) > tol_km:
        return False
    if abs(a.eccentricity - b.eccentricity) > 1e-9:
        return False
    if abs(a.inclination - b.inclination) > tol_deg:
        return False
    for x, y in ((a.raan, b.raan), (a.argp, b.argp), (a.mean_anomaly, b.mean_anomaly)):
        d = abs((x - y) % 360.0)
        if min(d, 360.0 - d) > tol_deg:
            return False
    return True


def _plane_key(e: Element) -> tuple[int, int]:
    """Coarse orbital plane: inclination and RAAN rounded to whole degrees.

    Coarse on purpose. Bucketing at 0.5 degrees (the first attempt) split the shells into
    hundreds of sub-buckets of one or two objects each and returned 914 pairs out of 11,823
    objects -- a sieve so fine it let the real answers through the holes. Whole degrees keep
    the shells intact; the mean-anomaly neighbourhood is what does the work.
    """
    return (round(e.inclination), round(e.raan))


def candidate_pairs(elements, margin_km: float = 30.0, steps_apart: int = 8):
    """Pairs that can actually meet, without building the full product.

    Two satellites on different orbital planes pass through each other's plane only at the
    nodes, so "within a kilometre" requires the same plane (inclination and RAAN) and, along
    that plane, nearby positions. Bucketing on the plane and sorting by mean anomaly
    collapses 11,823 objects from 69 million pairs to a few hundred thousand.

    `steps_apart` widens the neighbourhood along the track. It is a screening parameter, not
    a physical one, and it is printed with any result so a reader can tell what was swept.
    """
    out = []
    buckets: dict[tuple[int, int], list[Element]] = {}
    for e in elements:
        buckets.setdefault(_plane_key(e), []).append(e)
    for els in buckets.values():
        els.sort(key=lambda e: e.mean_anomaly)
        n = len(els)
        for i in range(n):
            for j in range(i + 1, min(i + 1 + steps_apart, n)):
                a, b = els[i], els[j]
                if abs(a.semi_major_axis - b.semi_major_axis) > margin_km:
                    continue
                if a.object_id and a.object_id == b.object_id:
                    continue
                out.append((a, b))
    return out
