"""SGP4, vendored.

Copied from the `sgp4` package (MIT, Copyright (c) 2012-2016 Brandon Rhodes), which wraps
the official C++ from "Revisiting Spacetrack Report #3" (AIAA 2006-6753). Vendored for one
reason: the pure-Python path lets this repository propagate without adding a dependency, and
entity-graph has kept its dependency count at zero on purpose.

The code is NOT edited beyond rewriting `from sgp4.x import` into `from .x import` so it
resolves as a subpackage. The verification harness in tests/test_sgp4_ver.py checks it
against the published vectors in tcppver.out at 1e-6 km before anything depends on it.
"""
