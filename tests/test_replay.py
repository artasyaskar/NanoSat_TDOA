"""
Tests for deterministic offline replay engine (src/processing/replay.py).
Ensures repeatable processing, explicit failure reason codes, edge-case rejection,
and compatibility with both JSON configurations and surveyed geometries.
"""
import copy
import json
import os
import shutil
import tempfile
import numpy as np
import pytest

from src.processing import replay, survey


@pytest.fixture
def sim_config_path():
    p = os.path.join(os.path.dirname(__file__), "..", "captures_sim", "config_used.json")
    assert os.path.exists(p), f"Missing simulated config at {p}"
    return os.path.abspath(p)


@pytest.fixture
def sim_captures_dir():
    p = os.path.join(os.path.dirname(__file__), "..", "captures_sim")
    assert os.path.exists(p), f"Missing simulated captures dir at {p}"
    return os.path.abspath(p)


def test_missing_files_yields_no_fix(sim_config_path, tmp_path):
    ctx = replay.build_context(config_path=sim_config_path)
    # Empty directory
    res = replay.replay_slot(
        captures_dir=str(tmp_path),
        slot="nonexistent_slot",
        ctx=ctx,
        max_seconds=0.1,
    )
    assert res["status"] == "NO_FIX"
    assert "MISSING_FILES" in res["reason"]
    assert res["data_source"] == "REPLAY"


def test_truncated_files_yields_capture_too_short(sim_config_path, tmp_path):
    ctx = replay.build_context(config_path=sim_config_path)
    slot = "short_slot"
    # Create 100-byte dummy files
    for n in ["node1", "node2", "node3"]:
        fpath = tmp_path / f"{n}_{slot}.cu8"
        fpath.write_bytes(b"\x80" * 100)

    res = replay.replay_slot(
        captures_dir=str(tmp_path),
        slot=slot,
        ctx=ctx,
        max_seconds=0.1,
    )
    assert res["status"] == "NO_FIX"
    assert "CAPTURE_TOO_SHORT" in res["reason"]


def test_replay_determinism_on_simulated_slot(sim_captures_dir, sim_config_path):
    """Replaying the same capture must yield bit-for-bit identical outputs."""
    ctx = replay.build_context(config_path=sim_config_path)
    slot = "20261020_101500"

    res1 = replay.replay_slot(
        captures_dir=sim_captures_dir,
        slot=slot,
        ctx=ctx,
        max_seconds=0.3,
        nseg=20,
    )
    res2 = replay.replay_slot(
        captures_dir=sim_captures_dir,
        slot=slot,
        ctx=ctx,
        max_seconds=0.3,
        nseg=20,
    )

    assert res1["status"] == "FIX"
    assert res2["status"] == "FIX"
    # Ensure all numerical values match exactly
    assert res1["position_xy"][0] == pytest.approx(res2["position_xy"][0], abs=1e-9)
    assert res1["position_xy"][1] == pytest.approx(res2["position_xy"][1], abs=1e-9)
    assert res1["residual_rms_m"] == pytest.approx(res2["residual_rms_m"], abs=1e-9)

    # Pairs must match
    for p1, p2 in zip(res1["pairs"], res2["pairs"]):
        assert p1["pair"] == p2["pair"]
        assert p1["status"] == p2["status"]
        if p1["status"] == "OK":
            assert p1["range_diff_m"] == pytest.approx(p2["range_diff_m"], abs=1e-9)


def test_random_noise_capture_fails_correlation(sim_config_path, tmp_path):
    """Pure white noise must fail correlation quality checks (NO_GOOD_SEGMENTS)."""
    ctx = replay.build_context(config_path=sim_config_path)
    slot = "noise_slot"
    rng = np.random.default_rng(42)
    # 0.1s at 2.4MSPS is 240,000 samples = 480,000 bytes
    n_bytes = 480_000
    for n in ["node1", "node2", "node3"]:
        noise = rng.integers(120, 136, size=n_bytes, dtype=np.uint8)
        (tmp_path / f"{n}_{slot}.cu8").write_bytes(noise.tobytes())

    res = replay.replay_slot(
        captures_dir=str(tmp_path),
        slot=slot,
        ctx=ctx,
        max_seconds=0.1,
        nseg=10,
    )
    assert res["status"] == "NO_FIX"
    # Should fail due to coarse reference quality or no good segments
    for p in res["pairs"]:
        assert p["status"] == "FAILED"


def test_build_context_from_survey_geometry():
    """Verify that a surveyed geometry dict can be directly loaded into replay context."""
    s_sol = survey.solve_survey(
        d12=8.0, d13=6.0, d23=5.0,
        p1=2.0, p2=6.0, p3=4.5,
        side=+1,
        n_monte_carlo=50,
    )
    assert s_sol["status"] == "PASS"

    ctx = replay.build_context(geometry=s_sol)
    assert "node1" in ctx["node_xy"]
    assert "node2" in ctx["node_xy"]
    assert "node3" in ctx["node_xy"]
    assert ctx["ref_xy"] is not None
    assert np.allclose(ctx["node_xy"]["node1"], [0.0, 0.0])


def test_format_report_outputs():
    dummy_res = {
        "data_source": "REPLAY",
        "slot": "test_slot",
        "algo": "adra",
        "status": "NO_FIX",
        "reason": "TOO_FEW_PAIRS (0 usable, need >= 2)",
        "nodes": {
            "node1": {"cfo_hz": 1200.0, "cfo_ppm": 2.76, "cfo_tone_snr_db": 14.5, "ref_power_db": -25.0, "target_power_db": -30.0, "clipping_pct": 0.0}
        },
        "pairs": [
            {
                "pair": "node1-node2",
                "status": "FAILED",
                "reason": "NO_GOOD_SEGMENTS",
                "quality_scan": {"target_quality_median": 2.5, "target_quality_p10": 2.0, "ref_quality_median": 22.0}
            }
        ]
    }
    rep = replay.format_report(dummy_res)
    assert "[REPLAY] slot=test_slot" in rep
    assert "NO_FIX" in rep
    assert "NO_GOOD_SEGMENTS" in rep
