"""Tests for src/processing/survey.py. All data here is SYNTHETIC geometry, not measurements."""
import os
import sys
import tempfile

import numpy as np
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from src.processing import survey as sv  # noqa: E402


def _dists(stations, p):
    return {k: float(np.hypot(*(np.array(p) - np.array(v)))) for k, v in stations.items()}


# --- unit parsing -----------------------------------------------------------
@pytest.mark.parametrize("text,expected", [
    ("8.026", 8.026), ("8.026 m", 8.026), ("802.6 cm", 8.026), ("26ft 4in", 26 * 0.3048 + 4 * 0.0254),
    ("26' 4\"", 26 * 0.3048 + 4 * 0.0254), ("26-4", 26 * 0.3048 + 4 * 0.0254), ("316 in", 316 * 0.0254),
    ("7ft", 7 * 0.3048), (2.5, 2.5),
])
def test_parse_length_ok(text, expected):
    assert sv.parse_length(text) == pytest.approx(expected, abs=1e-9)


def test_parse_length_default_unit_cm():
    assert sv.parse_length(250, default_unit="cm") == pytest.approx(2.5)


@pytest.mark.parametrize("bad", ["", "abc", "-3", "0", None, True, "3 parsecs", float("nan"), float("inf"), -1.0])
def test_parse_length_rejects(bad):
    with pytest.raises(sv.SurveyError):
        sv.parse_length(bad)


# --- station triangle -------------------------------------------------------
def test_triangle_3_4_5():
    st, info = sv.build_station_triangle(3.0, 4.0, 5.0)
    # d12=3, d13=4, d23=5 is a right triangle with the right angle at S1
    assert st["node1"].tolist() == [0.0, 0.0]
    assert st["node2"].tolist() == [3.0, 0.0]
    assert st["node3"][0] == pytest.approx(0.0, abs=1e-9)
    assert st["node3"][1] == pytest.approx(4.0, abs=1e-9)


def test_triangle_mirror_flips_y_only():
    a, _ = sv.build_station_triangle(8.0, 6.0, 5.0, side=+1)
    b, _ = sv.build_station_triangle(8.0, 6.0, 5.0, side=-1)
    assert a["node3"][0] == pytest.approx(b["node3"][0])
    assert a["node3"][1] == pytest.approx(-b["node3"][1])
    assert a["node3"][1] > 0


def test_triangle_distances_reproduced():
    st, _ = sv.build_station_triangle(8.026, 6.198, 4.978)
    assert np.hypot(*(st["node3"] - st["node1"])) == pytest.approx(6.198, abs=1e-9)
    assert np.hypot(*(st["node3"] - st["node2"])) == pytest.approx(4.978, abs=1e-9)


@pytest.mark.parametrize("d", [(1.0, 1.0, 3.0), (10.0, 2.0, 3.0), (3.0, 10.0, 2.0)])
def test_impossible_triangle_rejected(d):
    with pytest.raises(sv.SurveyError):
        sv.build_station_triangle(*d)


def test_degenerate_triangle_rejected():
    with pytest.raises(sv.SurveyError):
        sv.build_station_triangle(10.0, 5.0, 5.0)   # collinear (equality)
    with pytest.raises(sv.SurveyError):
        sv.build_station_triangle(10.0, 5.0, 5.01)  # nearly flat, smallest angle ~2.6 deg < 5 deg


def test_poorly_conditioned_triangle_warns():
    _, info = sv.build_station_triangle(10.0, 9.0, 2.0)
    assert any("poorly conditioned" in w for w in info["warnings"])


def test_bad_side_rejected():
    with pytest.raises(sv.SurveyError):
        sv.build_station_triangle(8.0, 6.0, 5.0, side=0)


# --- locating a point -------------------------------------------------------
STATIONS = {"node1": (0.0, 0.0), "node2": (8.026, 0.0), "node3": (2.1, 5.8)}


def test_locate_exact_three_distances():
    truth = (2.3, 1.7)
    out = sv.locate_from_distances(STATIONS, _dists(STATIONS, truth))
    assert out["xy"] == pytest.approx(list(truth), abs=1e-6)
    assert out["consistency"] == "PASS"
    assert not out["ambiguous"]


def test_locate_tape_error_detected_as_fail():
    d = _dists(STATIONS, (2.3, 1.7))
    d["node3"] += 0.5     # 50 cm tape slip
    out = sv.locate_from_distances(STATIONS, d, sigma_m=0.02)
    assert out["consistency"] in ("WARN", "FAIL")
    assert out["max_abs_residual_m"] > 0.05


def test_locate_two_distances_flags_mirror_ambiguity_and_side_choice():
    truth = (2.3, 1.7)  # above the S1->S2 line (left of direction +X)
    d = _dists(STATIONS, truth)
    d2 = {"node1": d["node1"], "node2": d["node2"]}
    amb = sv.locate_from_distances(STATIONS, d2)
    assert amb["ambiguous"] is True
    assert len(amb["candidates"]) == 2
    up = sv.locate_from_distances(STATIONS, d2, prefer_side=+1)
    down = sv.locate_from_distances(STATIONS, d2, prefer_side=-1)
    assert up["xy"] == pytest.approx([2.3, 1.7], abs=1e-6)
    assert down["xy"] == pytest.approx([2.3, -1.7], abs=1e-6)
    assert not up["ambiguous"]


def test_locate_disjoint_circles_rejected():
    with pytest.raises(sv.SurveyError):
        sv.locate_from_distances(STATIONS, {"node1": 0.5, "node2": 0.5})


def test_locate_needs_two_distances():
    with pytest.raises(sv.SurveyError):
        sv.locate_from_distances(STATIONS, {"node1": 3.0})


def test_locate_warns_when_on_station_line():
    d = _dists(STATIONS, (4.0, 0.0))
    out = sv.locate_from_distances(STATIONS, d)
    assert any("line node1-node2" in w for w in out["warnings"])


# --- full survey ------------------------------------------------------------
def _consistent_inputs():
    st, _ = sv.build_station_triangle(8.026, 6.198, 4.978)
    pluto = np.array([2.0, 1.2])
    d = _dists(st, pluto)
    return (8.026, 6.198, 4.978, d["node1"], d["node2"], d["node3"]), pluto


def test_full_survey_recovers_pluto():
    vals, pluto = _consistent_inputs()
    res = sv.solve_survey(*vals)
    assert res["status"] == "PASS"
    assert res["pluto"]["xy"] == pytest.approx(list(pluto), abs=1e-6)
    assert res["stations"]["node1"] == [0.0, 0.0]
    assert res["stations"]["node2"] == [8.026, 0.0]
    assert res["stations"]["node3"][1] > 0
    assert "pluto_radius95_m" in res["uncertainty"]
    assert res["provenance"].startswith("TAPE_SURVEY")


def test_full_survey_reports_inconsistent_pluto():
    vals, _ = _consistent_inputs()
    bad = list(vals)
    bad[5] += 1.0   # 1 m error in P-S3
    res = sv.solve_survey(*bad)
    assert res["status"] in ("WARN", "FAIL")
    assert res["pluto"]["max_abs_residual_m"] > 0.1


def test_full_survey_deterministic():
    vals, _ = _consistent_inputs()
    a = sv.solve_survey(*vals)
    b = sv.solve_survey(*vals)
    assert a == b


def test_full_survey_uncertainty_scales_with_sigma():
    vals, _ = _consistent_inputs()
    small = sv.solve_survey(*vals, sigma_m=0.005)["uncertainty"]["pluto_radius95_m"]
    large = sv.solve_survey(*vals, sigma_m=0.05)["uncertainty"]["pluto_radius95_m"]
    assert large > 3.0 * small


def test_survey_from_inputs_mixed_units():
    raw = {"S1-S2": "26ft 4in", "S1-S3": "20ft 4in", "S2-S3": "16ft 4in",
           "P-S1": "7ft 2in", "P-S2": "19ft 7in", "P-S3": "14ft"}
    res = sv.solve_survey_from_inputs(raw)
    assert res["inputs_m"]["S1-S2"] == pytest.approx(8.0264, abs=1e-3)
    assert res["inputs_raw"]["S1-S2"] == "26ft 4in"


def test_survey_from_inputs_missing_key():
    with pytest.raises(sv.SurveyError):
        sv.solve_survey_from_inputs({"S1-S2": 8})


# --- persistence ------------------------------------------------------------
def test_save_load_never_overwrites_silently():
    vals, _ = _consistent_inputs()
    res = sv.solve_survey(*vals)
    with tempfile.TemporaryDirectory() as root:
        p = sv.save_survey(root, "lab_a", res)
        assert os.path.exists(p)
        assert sv.list_surveys(root) == ["lab_a"]
        with pytest.raises(sv.SurveyError):
            sv.save_survey(root, "lab_a", res)
        sv.save_survey(root, "lab_a", res, overwrite=True)
        loaded = sv.load_survey(root, "lab_a")
        assert loaded["stations"] == res["stations"]
        assert loaded["name"] == "lab_a"


@pytest.mark.parametrize("bad", ["", "../evil", "a/b", "a b", "x" * 65])
def test_save_rejects_bad_names(bad):
    with tempfile.TemporaryDirectory() as root:
        with pytest.raises(sv.SurveyError):
            sv.save_survey(root, bad, {})
        with pytest.raises(sv.SurveyError):
            sv.load_survey(root, bad)


def test_load_missing():
    with tempfile.TemporaryDirectory() as root:
        with pytest.raises(sv.SurveyError):
            sv.load_survey(root, "nope")
