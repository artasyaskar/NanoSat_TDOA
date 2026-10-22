"""
Tests for live tracking state machine and safety gate (src/server/live_state.py).
"""
import time
import pytest
from src.server.live_state import LiveEngine, LiveStateError


def test_default_engine_state():
    engine = LiveEngine()
    st = engine.get_status()
    assert st["is_running"] is False
    assert st["source"] == "SIMULATED"
    assert st["hardware_enabled"] is False


def test_hardware_safety_gate():
    engine = LiveEngine()
    # Live HW must fail if hardware_enabled is False
    with pytest.raises(LiveStateError, match="LIVE_HW mode is disabled"):
        engine.start(source="LIVE_HW", allow_live_hw=True)

    with pytest.raises(LiveStateError, match="LIVE_HW mode is disabled"):
        engine.start(source="LIVE_HW", allow_live_hw=False)


def test_valid_start_and_stop():
    engine = LiveEngine()
    res = engine.start(source="REPLAY")
    assert res["running"] is True
    assert res["source"] == "REPLAY"

    st = engine.get_status()
    assert st["is_running"] is True
    assert st["source"] == "REPLAY"

    res_stop = engine.stop()
    assert res_stop["running"] is False


def test_submit_and_latest_fix():
    engine = LiveEngine(stale_timeout_s=10.0)
    engine.start(source="REPLAY")

    record = {
        "data_source": "REPLAY",
        "status": "FIX",
        "position_xy": [1.5, 2.5],
        "residual_rms_m": 0.05,
        "pairs": [
            {"pair": "node1-node2", "status": "OK"},
            {"pair": "node1-node3", "status": "OK"},
        ]
    }
    engine.submit_fix(record)

    latest = engine.get_latest()
    assert latest["status"] == "FIX"
    assert latest["position_xy"] == [1.5, 2.5]
    assert latest["is_stale"] is False
    assert latest["state_display"] == "FIX"
    assert latest["fix_age_s"] is not None
    assert latest["fix_age_s"] < 1.0


def test_stale_detection():
    # Very short stale threshold
    engine = LiveEngine(stale_timeout_s=0.05)
    engine.start(source="SIMULATED")

    record = {
        "data_source": "SIMULATED",
        "status": "FIX",
        "position_xy": [0.0, 0.0],
        "residual_rms_m": 0.01,
        "pairs": []
    }
    engine.submit_fix(record)
    time.sleep(0.08)

    latest = engine.get_latest()
    assert latest["is_stale"] is True
    assert latest["state_display"] == "STALE"


def test_no_fix_rejection_tracking():
    engine = LiveEngine()
    engine.start(source="REPLAY")

    fail_record = {
        "data_source": "REPLAY",
        "status": "NO_FIX",
        "reason": "TOO_FEW_PAIRS",
        "pairs": [
            {"pair": "node1-node2", "status": "FAILED", "reason": "NO_GOOD_SEGMENTS"},
            {"pair": "node1-node3", "status": "FAILED", "reason": "NO_GOOD_SEGMENTS"},
        ]
    }
    engine.submit_fix(fail_record)

    latest = engine.get_latest()
    assert latest["status"] == "NO_FIX"
    assert latest["reason"] == "TOO_FEW_PAIRS"
    assert latest["rejected_pairs"] == 2
    assert latest["is_stale"] is True

    st = engine.get_status()
    assert st["total_failures"] == 1
    assert st["total_rejected_pairs"] == 2


def test_history_circular_buffer():
    engine = LiveEngine(history_len=5)
    for i in range(10):
        engine.submit_fix({
            "status": "FIX",
            "position_xy": [float(i), float(i)],
            "residual_rms_m": 0.01,
        })

    hist = engine.get_history(limit=10)
    assert len(hist) == 5
    # Should contain items 5 through 9
    assert hist[0]["position_xy"] == [5.0, 5.0]
    assert hist[-1]["position_xy"] == [9.0, 9.0]
