"""
survey.py - Tape-measure survey solver for the lab geometry.

Turns hand-measured distances into local Cartesian coordinates and tells the operator
HOW MUCH to trust them. Nothing here touches hardware or any existing configuration.

Frame convention: S1 = (0, 0), S2 on the +X axis, S3 on the selected Y side.

Inputs (all lengths -> metres):
    S1-S2, S1-S3, S2-S3          station-to-station distances (build the receiver layout)
    P-S1,  P-S2,  P-S3           Pluto-to-station distances (establish reference TX position)

Target ground truth is a SEPARATE job: use locate_from_distances() with the target
antenna's own tape distances (ideally to all three stations, so there is a redundancy check).
"""
import json
import math
import os
import re
import time

import numpy as np
from scipy.optimize import least_squares

FT_M = 0.3048
IN_M = 0.0254

# Operator tape-reading uncertainty (1-sigma, metres). This is an ASSUMPTION, shown to the user.
DEFAULT_SIGMA_M = 0.02

STATION_KEYS = ("node1", "node2", "node3")
SURVEY_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class SurveyError(ValueError):
    """Raised for physically impossible or unparsable survey input."""


# ----------------------------------------------------------------------------
# Unit parsing
# ----------------------------------------------------------------------------
_NUM = r"(\d+(?:\.\d+)?|\.\d+)"
_FT_IN_RE = re.compile(
    rf"^\s*{_NUM}\s*(?:ft|feet|foot|')\s*(?:{_NUM}\s*(?:in|inch|inches|\")?)?\s*$", re.I)
_DASH_FT_IN_RE = re.compile(rf"^\s*{_NUM}\s*-\s*{_NUM}\s*$")
_VAL_UNIT_RE = re.compile(rf"^\s*{_NUM}\s*(m|meter|meters|metre|metres|cm|mm|in|inch|inches|ft|feet)?\s*$", re.I)


def parse_length(value, default_unit="m"):
    """
    Parse a length into metres. Accepts numbers (interpreted in default_unit) or strings:
    '8.026', '8.026 m', '802.6 cm', '26ft 4in', "26' 4\"", '26-4' (feet-inches), '316 in'.
    Raises SurveyError for empty, negative, zero, NaN or unparsable input.
    """
    if isinstance(value, bool) or value is None:
        raise SurveyError(f"length missing or invalid: {value!r}")
    if isinstance(value, (int, float, np.floating, np.integer)):
        x, unit = float(value), default_unit
        metres = _to_metres(x, unit)
    else:
        s = str(value).strip()
        if not s:
            raise SurveyError("length is empty")
        m = _FT_IN_RE.match(s)
        if m:
            metres = float(m.group(1)) * FT_M + (float(m.group(2)) * IN_M if m.group(2) else 0.0)
        else:
            m = _DASH_FT_IN_RE.match(s)
            if m:
                metres = float(m.group(1)) * FT_M + float(m.group(2)) * IN_M
            else:
                m = _VAL_UNIT_RE.match(s)
                if not m:
                    raise SurveyError(f"cannot parse length {value!r}")
                unit = (m.group(2) or default_unit).lower()
                metres = _to_metres(float(m.group(1)), unit)
    if not math.isfinite(metres) or metres <= 0.0:
        raise SurveyError(f"length must be a positive finite number, got {value!r}")
    return metres


def _to_metres(x, unit):
    u = unit.lower()
    if u in ("m", "meter", "meters", "metre", "metres"):
        return x
    if u == "cm":
        return x / 100.0
    if u == "mm":
        return x / 1000.0
    if u in ("in", "inch", "inches"):
        return x * IN_M
    if u in ("ft", "feet"):
        return x * FT_M
    raise SurveyError(f"unknown unit {unit!r}")


# ----------------------------------------------------------------------------
# Geometry primitives
# ----------------------------------------------------------------------------
def _circle_intersections(a, ra, b, rb):
    """Two intersection points of circles (a, ra) and (b, rb); None if they do not meet."""
    a = np.asarray(a, float)
    b = np.asarray(b, float)
    d = float(np.hypot(*(b - a)))
    if d < 1e-9:
        return None
    u = (b - a) / d
    n = np.array([-u[1], u[0]])
    x = (ra * ra - rb * rb + d * d) / (2.0 * d)
    h2 = ra * ra - x * x
    if h2 < 0.0:
        return None
    h = math.sqrt(h2)
    base = a + x * u
    return base + h * n, base - h * n


def _angle_deg(p, q, r):
    """Interior angle at q of triangle p-q-r."""
    v1, v2 = np.asarray(p) - np.asarray(q), np.asarray(r) - np.asarray(q)
    c = float(np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2) + 1e-18))
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def _dist_to_line(p, a, b):
    a, b, p = np.asarray(a, float), np.asarray(b, float), np.asarray(p, float)
    ab = b - a
    return float(abs(ab[0] * (p[1] - a[1]) - ab[1] * (p[0] - a[0])) / (np.hypot(*ab) + 1e-18))


def _consistency(max_abs_resid, sigma):
    """PASS <= 3 sigma, WARN <= 10 sigma, FAIL otherwise (sigma = assumed tape 1-sigma)."""
    if max_abs_resid <= 3.0 * sigma:
        return "PASS"
    if max_abs_resid <= 10.0 * sigma:
        return "WARN"
    return "FAIL"


# ----------------------------------------------------------------------------
# Station triangle
# ----------------------------------------------------------------------------
def build_station_triangle(d12, d13, d23, side=+1):
    """
    S1=(0,0), S2=(d12,0), S3 on side*Y. Validates triangle inequality and degeneracy.
    side=+1 puts S3 at +Y, side=-1 at -Y (the mirror solution; physically indistinguishable
    from distances alone, so the operator must choose to match the real room).
    """
    if side not in (+1, -1):
        raise SurveyError("side must be +1 or -1")
    for name, v in (("S1-S2", d12), ("S1-S3", d13), ("S2-S3", d23)):
        if not (math.isfinite(v) and v > 0):
            raise SurveyError(f"{name} must be positive and finite")
    # strict triangle inequality
    for name, lhs, rhs in (("S1-S2 + S1-S3 > S2-S3", d12 + d13, d23),
                           ("S1-S2 + S2-S3 > S1-S3", d12 + d23, d13),
                           ("S1-S3 + S2-S3 > S1-S2", d13 + d23, d12)):
        if lhs <= rhs:
            raise SurveyError(f"impossible triangle: violates {name} ({lhs:.4f} <= {rhs:.4f})")
    x3 = (d12 * d12 + d13 * d13 - d23 * d23) / (2.0 * d12)
    y2 = d13 * d13 - x3 * x3
    if y2 <= 0.0:
        raise SurveyError("degenerate triangle (stations collinear)")
    y3 = side * math.sqrt(y2)
    s1, s2, s3 = np.array([0.0, 0.0]), np.array([d12, 0.0]), np.array([x3, y3])
    angles = [_angle_deg(s2, s1, s3), _angle_deg(s1, s2, s3), _angle_deg(s1, s3, s2)]
    warnings = []
    min_angle = min(angles)
    if min_angle < 5.0:
        raise SurveyError(f"near-degenerate triangle: smallest angle {min_angle:.1f} deg < 5 deg")
    if min_angle < 20.0:
        warnings.append(f"poorly conditioned triangle: smallest angle {min_angle:.1f} deg (GDOP will be high)")
    return {"node1": s1, "node2": s2, "node3": s3}, {"angles_deg": angles, "warnings": warnings}


# ----------------------------------------------------------------------------
# Locate a point from distances to the stations (Pluto position OR target ground truth)
# ----------------------------------------------------------------------------
def locate_from_distances(stations, dists, sigma_m=DEFAULT_SIGMA_M, prefer_side=None):
    """
    stations: {"node1": (x, y), ...}; dists: {"node1": metres, ...} (2 or 3 entries).
    Returns dict with xy, per-station residuals (metres), rms, consistency, ambiguity info.

    2 distances  -> two mirror solutions. Ambiguous unless prefer_side (+1 left of the line
                    from the first to the second named station, -1 right) is given.
    3 distances  -> least squares over all stations; residuals expose tape mistakes.
    """
    names = [k for k in STATION_KEYS if k in dists and k in stations]
    if len(names) < 2:
        raise SurveyError("need distances to at least two stations")
    pts = {k: np.asarray(stations[k], float) for k in names}
    r = {k: float(dists[k]) for k in names}
    for k in names:
        if not (math.isfinite(r[k]) and r[k] > 0):
            raise SurveyError(f"distance to {k} must be positive and finite")

    warnings = []
    # starting candidates from every pair of circles
    starts = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            ints = _circle_intersections(pts[names[i]], r[names[i]], pts[names[j]], r[names[j]])
            if ints is not None:
                starts.extend(ints)
    if not starts:
        raise SurveyError("distance circles do not intersect: measurements are inconsistent with the station layout")

    def resid(p):
        return np.array([np.hypot(*(p - pts[k])) - r[k] for k in names])

    sols = []
    for s in starts:
        res = least_squares(resid, s, method="lm" if len(names) >= 2 else "trf")
        p = res.x
        c = float(np.sum(res.fun ** 2))
        if not any(np.hypot(*(p - q)) < 0.02 for q, _ in sols):
            sols.append((p, c))
    sols.sort(key=lambda t: t[1])

    # Two-distance case: exact intersection, so mirror solutions have ~zero cost each.
    ambiguous = False
    candidates = [{"xy": [float(p[0]), float(p[1])], "rms_m": math.sqrt(c / len(names))} for p, c in sols]
    best_p, best_c = sols[0]
    if len(names) == 2:
        a, b = pts[names[0]], pts[names[1]]
        left = lambda p: float((b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])) > 0
        if len(sols) >= 2 and np.hypot(*(sols[0][0] - sols[1][0])) > 0.05:
            if prefer_side in (+1, -1):
                chosen = [p for p, _ in sols if (left(p) == (prefer_side == +1))]
                best_p = chosen[0] if chosen else sols[0][0]
            else:
                ambiguous = True
                warnings.append("only 2 distances: two mirror solutions exist; give a third distance or choose a side")
    elif len(sols) >= 2:
        rms0 = math.sqrt(sols[0][1] / len(names))
        rms1 = math.sqrt(sols[1][1] / len(names))
        if np.hypot(*(sols[0][0] - sols[1][0])) > 0.05 and rms1 <= rms0 + 1.5 * sigma_m:
            ambiguous = True
            warnings.append("two distinct solutions fit the 3 distances equally well (point near a station line?)")

    resids = resid(best_p)
    max_abs = float(np.max(np.abs(resids)))
    out = {
        "xy": [float(best_p[0]), float(best_p[1])],
        "residuals_m": {k: float(v) for k, v in zip(names, resids)},
        "rms_m": float(np.sqrt(np.mean(resids ** 2))),
        "max_abs_residual_m": max_abs,
        "n_distances": len(names),
        "ambiguous": bool(ambiguous),
        "candidates": candidates,
        "consistency": _consistency(max_abs, sigma_m) if len(names) >= 3 else "UNCHECKED (only 2 distances, no redundancy)",
        "warnings": warnings,
    }
    # reference/target sitting on a station-pair line makes TDOA geometry degenerate
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            dl = _dist_to_line(best_p, pts[names[i]], pts[names[j]])
            if dl < 0.3:
                out["warnings"].append(
                    f"point is within {dl:.2f} m of the line {names[i]}-{names[j]} (poor geometry for TDOA)")
    return out


# ----------------------------------------------------------------------------
# Full survey
# ----------------------------------------------------------------------------
def solve_survey(d12, d13, d23, p1, p2, p3, side=+1, sigma_m=DEFAULT_SIGMA_M,
                 n_monte_carlo=200, seed=12345):
    """
    Pure-metre inputs. Returns a dict with stations, pluto, validation, uncertainty.
    Raises SurveyError for impossible input. Deterministic for fixed seed.
    """
    if not (math.isfinite(sigma_m) and sigma_m > 0):
        raise SurveyError("sigma_m must be positive")
    stations, tri = build_station_triangle(d12, d13, d23, side=side)
    pl = locate_from_distances(stations, {"node1": p1, "node2": p2, "node3": p3}, sigma_m=sigma_m)

    warnings = list(tri["warnings"]) + list(pl["warnings"])
    status = pl["consistency"]
    if pl["ambiguous"]:
        status = "FAIL" if status == "FAIL" else "WARN"

    # Monte-Carlo propagation of tape uncertainty (seeded -> reproducible)
    rng = np.random.default_rng(seed)
    s3_s, pl_s = [], []
    base = np.array([d12, d13, d23, p1, p2, p3], float)
    for _ in range(int(n_monte_carlo)):
        d = base + rng.normal(0.0, sigma_m, 6)
        if np.any(d <= 0):
            continue
        try:
            st, _ = build_station_triangle(d[0], d[1], d[2], side=side)
            q = locate_from_distances(st, {"node1": d[3], "node2": d[4], "node3": d[5]}, sigma_m=sigma_m)
        except SurveyError:
            continue
        s3_s.append(st["node3"])
        pl_s.append(q["xy"])
    unc = {"sigma_tape_m": sigma_m, "assumption": "independent Gaussian tape-reading error per distance",
           "n_samples": len(pl_s), "seed": seed}
    if len(pl_s) >= 20:
        s3_a, pl_a = np.array(s3_s), np.array(pl_s)
        unc["node3_std_m"] = [float(np.std(s3_a[:, 0])), float(np.std(s3_a[:, 1]))]
        unc["pluto_std_m"] = [float(np.std(pl_a[:, 0])), float(np.std(pl_a[:, 1]))]
        unc["pluto_radius95_m"] = float(np.percentile(np.hypot(*(pl_a - pl_a.mean(axis=0)).T), 95))
    else:
        warnings.append("uncertainty propagation failed: too few valid Monte-Carlo samples")

    return {
        "status": status,
        "stations": {k: [float(v[0]), float(v[1])] for k, v in stations.items()},
        "pluto": {"xy": pl["xy"], "residuals_m": pl["residuals_m"], "rms_m": pl["rms_m"],
                  "max_abs_residual_m": pl["max_abs_residual_m"], "consistency": pl["consistency"],
                  "ambiguous": pl["ambiguous"]},
        "triangle": {"angles_deg": tri["angles_deg"], "side": int(side),
                     "mirror_note": ("S3 placed at +Y" if side == +1 else "S3 placed at -Y (mirror)")
                                    + "; distances alone cannot tell the mirror image apart - choose to match the room"},
        "uncertainty": unc,
        "inputs_m": {"S1-S2": d12, "S1-S3": d13, "S2-S3": d23, "P-S1": p1, "P-S2": p2, "P-S3": p3},
        "warnings": warnings,
        "provenance": "TAPE_SURVEY (operator measurements, not independently verified)",
    }


def solve_survey_from_inputs(raw, side=+1, sigma_m=DEFAULT_SIGMA_M, default_unit="m", **kw):
    """raw: dict with keys S1-S2, S1-S3, S2-S3, P-S1, P-S2, P-S3 (numbers or strings with units)."""
    keys = ("S1-S2", "S1-S3", "S2-S3", "P-S1", "P-S2", "P-S3")
    missing = [k for k in keys if k not in raw]
    if missing:
        raise SurveyError(f"missing measurements: {', '.join(missing)}")
    vals = [parse_length(raw[k], default_unit) for k in keys]
    res = solve_survey(*vals, side=side, sigma_m=sigma_m, **kw)
    res["inputs_raw"] = {k: raw[k] for k in keys}
    return res


# ----------------------------------------------------------------------------
# Persistence (separate from the working configuration; never overwrites silently)
# ----------------------------------------------------------------------------
_DEFAULT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def survey_dir(root=None):
    if root is None:
        root = _DEFAULT_ROOT
    return os.path.join(root, "configs", "geometry", "surveys")


def save_survey(name, survey, root=None, overwrite=False):
    # Support both save_survey(root, name, survey, overwrite) and save_survey(name, survey, root=None, overwrite=False)
    if isinstance(survey, str) and not isinstance(name, dict):
        # Called as save_survey(root, name, survey, overwrite)
        root, name, survey = name, survey, root
    if root is None:
        root = _DEFAULT_ROOT
    if not SURVEY_NAME_RE.match(name or ""):
        raise SurveyError("survey name must match [A-Za-z0-9_-]{1,64}")
    d = survey_dir(root)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{name}.json")
    if os.path.exists(path) and not overwrite:
        raise SurveyError(f"survey '{name}' already exists; pass overwrite=true to replace it")
    doc = dict(survey)
    doc["schema_version"] = 1
    doc["name"] = name
    doc["saved_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)
    return path


def load_survey(name, root=None):
    # Support load_survey(root, name) and load_survey(name, root=None)
    if root is not None and isinstance(name, str) and os.path.isdir(name):
        root, name = name, root
    if root is None:
        root = _DEFAULT_ROOT
    if not SURVEY_NAME_RE.match(name or ""):
        raise SurveyError("invalid survey name")
    path = os.path.join(survey_dir(root), f"{name}.json")
    if not os.path.exists(path):
        raise SurveyError(f"survey '{name}' not found")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def list_surveys(root=None):
    if root is None:
        root = _DEFAULT_ROOT
    d = survey_dir(root)
    if not os.path.isdir(d):
        return []
    return sorted(os.path.splitext(f)[0] for f in os.listdir(d) if f.endswith(".json"))
