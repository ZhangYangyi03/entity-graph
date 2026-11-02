"""Where a satellite is, and how close two of them get.

Pure standard library, on purpose: the propagation here is Kepler + J2 secular, which is a
closed-form formula, not a numerical integrator. Writing it out makes it checkable against
the source the elements came from; hiding it behind a library would make the numbers
trustworthy by reputation instead of by arithmetic.

What this is NOT: a conjunction assessment. Real operators use high-fidelity propagation and
the full catalogue including debris. This is a screening pass -- it says "these two were
within a kilometre of each other on this date, using one element set each", and it says so
with the number it used, so the reader can judge it.
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

    Secular J2 drift in RAAN, argument of perigee and mean anomaly -- the terms that
    matter over the hours to days an element set stays useful. Not SGP4: no drag, no
    short-period terms. That is stated here rather than buried, because the difference is
    the difference between "a screening pass" and "a conjunction assessment".
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


def distance(el_a: Element, el_b: Element, t: float) -> float:
    ax, ay, az = eci(el_a, t)
    bx, by, bz = eci(el_b, t)
    return math.sqrt((ax - bx) ** 2 + (ay - by) ** 2 + (az - bz) ** 2)


@dataclass
class CloseApproach:
    a: str
    b: str
    t: float                 # unix seconds of the closest sample
    km: float                # distance at that sample
    a_id: str = ""
    b_id: str = ""

    @property
    def iso(self) -> str:
        from datetime import datetime, timezone
        return datetime.fromtimestamp(self.t, timezone.utc).isoformat(timespec="seconds")


def screen(pairs, t0: float, hours: float, step_s: float = 60.0,
           threshold_km: float = 1.0):
    """Walk a time window, report every pair that comes within threshold_km.

    Nearest-sample, not a refined closest approach: the true minimum between two samples is
    lower than anything reported here, so a "no events" answer means "no event at this
    sampling", and the step is printed with the result.
    """
    n_steps = max(1, int(hours * 3600 / step_s))
    hits: list[CloseApproach] = []
    for k in range(n_steps + 1):
        t = t0 + k * step_s
        for a, b in pairs:
            # Cheap reject first: different orbit shells with a wide margin cannot close to
            # a kilometre, and this is what makes 11k objects tractable.
            if abs(a.semi_major_axis - b.semi_major_axis) > 200:
                continue
            dkm = distance(a, b, t)
            if dkm <= threshold_km:
                hits.append(CloseApproach(a=a.name, b=b.name, t=t, km=dkm,
                                          a_id=a.object_id, b_id=b.object_id))
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
