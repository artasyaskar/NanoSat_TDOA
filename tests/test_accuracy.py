"""
Tests for independent accuracy testing and metrics evaluation (src/processing/accuracy.py).
"""
import os
import tempfile
import pytest

from src.processing import accuracy


def test_generate_lab_test_plan():
    stations = {
        "node1": [0.0, 0.0],
        "node2": [8.0, 0.0],
        "node3": [1.0, 6.0],
    }
    plan = accuracy.generate_lab_test_plan(stations)
    assert len(plan) == 6
    spot_ids = [s["spot_id"] for s in plan]
    assert "SPOT-1-CENTER" in spot_ids
    assert "SPOT-2-CORNER-S1" in spot_ids
    assert "SPOT-3-CORNER-S2" in spot_ids
    assert "SPOT-4-CORNER-S3" in spot_ids
    assert "SPOT-5-BASELINE-MID" in spot_ids
    assert "SPOT-6-EXTERIOR" in spot_ids

    for s in plan:
        assert len(s["planned_xy"]) == 2
        assert len(s["description"]) > 0
        assert len(s["marking_guide"]) > 0


def test_accuracy_session_creation():
    sess = accuracy.AccuracySession("test_session_01", category="static_ota")
    assert sess.session_id == "test_session_01"
    assert sess.category == "static_ota"

    with pytest.raises(ValueError, match="Category must be one of"):
        accuracy.AccuracySession("bad_session", category="invalid_cat")


def test_add_spot_and_record_trials():
    sess = accuracy.AccuracySession("bench_01", category="conducted_calibration")
    sess.add_spot("SPOT-1", "Bench Spot 1", truth_xy=(10.0, 10.0))

    # Add 4 fixes with known offsets
    # Errors: [0.1, 0.0] -> dist 0.1
    #         [-0.1, 0.0] -> dist 0.1
    #         [0.0, 0.2] -> dist 0.2
    #         [0.0, -0.2] -> dist 0.2
    sess.record_trial("SPOT-1", estimated_xy=[10.1, 10.0], status="FIX", latency_s=1.2)
    sess.record_trial("SPOT-1", estimated_xy=[9.9, 10.0], status="FIX", latency_s=1.1)
    sess.record_trial("SPOT-1", estimated_xy=[10.0, 10.2], status="FIX", latency_s=1.3)
    sess.record_trial("SPOT-1", estimated_xy=[10.0, 9.8], status="FIX", latency_s=1.4)

    # Add 1 failure
    sess.record_trial("SPOT-1", status="NO_FIX", reason="TOO_FEW_PAIRS", latency_s=1.5)

    metrics = sess.compute_metrics()
    assert metrics["n_trials"] == 5
    assert metrics["n_fixes"] == 4
    assert metrics["n_failures"] == 1
    assert metrics["failure_rate_pct"] == 20.0

    # Errors are 0.1, 0.1, 0.2, 0.2
    # Mean error = (0.1+0.1+0.2+0.2)/4 = 0.15
    # MSE = (0.01 + 0.01 + 0.04 + 0.04)/4 = 0.10 / 4 = 0.025
    # RMSE = sqrt(0.025) ≈ 0.1581
    assert metrics["rmse_m"] == pytest.approx(0.1581, abs=1e-3)
    assert metrics["median_error_m"] == pytest.approx(0.15, abs=1e-3)
    assert metrics["bias_x_m"] == pytest.approx(0.0, abs=1e-3)
    assert metrics["bias_y_m"] == pytest.approx(0.0, abs=1e-3)
    assert metrics["radial_bias_m"] == pytest.approx(0.0, abs=1e-3)


def test_csv_export(tmp_path):
    sess = accuracy.AccuracySession("csv_test", category="synthetic")
    sess.add_spot("SPOT-A", "Test Spot A", truth_xy=(5.0, 5.0))
    sess.record_trial("SPOT-A", estimated_xy=[5.05, 5.02], status="FIX", latency_s=0.5)

    raw_csv = str(tmp_path / "raw.csv")
    summary_csv = str(tmp_path / "summary.csv")

    sess.export_csv(raw_csv, summary_csv)
    assert os.path.exists(raw_csv)
    assert os.path.exists(summary_csv)

    raw_text = open(raw_csv, "r", encoding="utf-8").read()
    assert "trial_id,session_id,category" in raw_text
    assert "SPOT-A" in raw_text

    sum_text = open(summary_csv, "r", encoding="utf-8").read()
    assert "rmse_m,median_error_m" in sum_text
    assert "SPOT-A" in sum_text
