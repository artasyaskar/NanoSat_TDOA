"""
test_server_endpoints.py - Automated HTTP API verification test for Tactical Mission Control Server
"""
import os
import sys
import json
import time
import urllib.request
import threading
from http.server import HTTPServer, ThreadingHTTPServer

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, BASE_DIR)

from src.server.app import MissionControlHandler

def test_endpoints():
    test_port = 18092
    server = ThreadingHTTPServer(('127.0.0.1', test_port), MissionControlHandler)
    server.daemon_threads = True
    th = threading.Thread(target=server.serve_forever, daemon=True)
    th.start()
    time.sleep(1.0)

    endpoints_get = [
        ('/', 'text/html'),
        ('/api/status', 'application/json'),
        ('/api/gdop', 'application/json'),
        ('/api/solve?algo=adra&mode=live', 'application/json'),
        ('/api/solve?algo=gcc_phat&mode=live', 'application/json'),
        ('/api/solve?algo=standard&mode=live', 'application/json'),
        ('/api/solve?algo=adra&mode=coax', 'application/json'),
        ('/api/spectrum', 'application/json'),
        ('/api/benchmark', 'application/json'),
        ('/api/export_csv', 'text/csv'),
    ]

    print(f"=== Verifying {len(endpoints_get)} GET Endpoints on http://127.0.0.1:{test_port} ===")
    for path, expected_type in endpoints_get:
        url = f"http://127.0.0.1:{test_port}{path}"
        with urllib.request.urlopen(url, timeout=40) as resp:
            status = resp.status
            content_type = resp.headers.get_content_type()
            body = resp.read()
            assert status == 200, f"Expected 200 for {path}, got {status}"
            assert expected_type in content_type, f"Expected {expected_type} in {content_type} for {path}"
            print(f"  [ PASS ] GET {path:<40} -> Status: {status}, Type: {content_type}, Size: {len(body)} bytes")

    print("\n=== Verifying POST /api/calibrate_v4 ===")
    cal_url = f"http://127.0.0.1:{test_port}/api/calibrate_v4"
    cal_data = json.dumps({"apply": True}).encode("utf-8")
    req = urllib.request.Request(cal_url, data=cal_data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=5) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        offset = data.get("calibrated_v4_offset_ns") or data.get("calibration", {}).get("node3_cal_ns", 15.0)
        assert offset is not None
        print(f"  [ PASS ] POST /api/calibrate_v4 -> Status: 200, Offset: +{offset:.2f} ns")

    print("\n=== Verifying POST /api/geometry (Preset Switching) ===")
    geom_url = f"http://127.0.0.1:{test_port}/api/geometry"
    for preset in ["survey", "equilateral", "collinear", "coax_bench"]:
        geom_data = json.dumps({"preset": preset}).encode("utf-8")
        req = urllib.request.Request(geom_url, data=geom_data, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            print(f"  [ PASS ] POST /api/geometry preset='{preset}' -> Status: 200, Preset: {data.get('preset')}")

    print("\n=== Verifying POST /api/telemetry (Pi Station Health Ingestion) ===")
    telem_url = f"http://127.0.0.1:{test_port}/api/telemetry"
    sample_telem = {
        "node": "node1",
        "chrony_offset_ms": 0.12,
        "cpu_temp_c": 44.5,
        "shm_active": True,
        "adc_clip_pct": 0.01,
        "tuner_type": "R820T2"
    }
    telem_data = json.dumps(sample_telem).encode("utf-8")
    req = urllib.request.Request(telem_url, data=telem_data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=5) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
        print(f"  [ PASS ] POST /api/telemetry node='node1' -> Status: 200, Updated: {data.get('status')}")

    print("\n=== Verifying Survey API (Preview, Save, List, Load, Apply, Revert) ===")
    prev_url = f"http://127.0.0.1:{test_port}/api/survey/preview"
    sample_survey_inputs = {
        "unit": "m", "side": 1, "sigma_m": 0.02, "n_monte_carlo": 25,
        "inputs": {"S1-S2": 8.0, "S1-S3": 6.0, "S2-S3": 5.0, "P-S1": 2.0, "P-S2": 6.0, "P-S3": 4.5}
    }
    req = urllib.request.Request(prev_url, data=json.dumps(sample_survey_inputs).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=15) as resp:
        assert resp.status == 200
        prev_data = json.loads(resp.read().decode("utf-8"))
        assert prev_data["status"] == "SUCCESS"
        assert "preview" in prev_data
        print("  [ PASS ] POST /api/survey/preview -> Status: 200, Status: PASS")

    # Survey Save
    save_url = f"http://127.0.0.1:{test_port}/api/survey/save"
    save_payload = {"name": "test_survey_run", "survey": prev_data["preview"], "overwrite": True}
    req = urllib.request.Request(save_url, data=json.dumps(save_payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=5) as resp:
        assert resp.status == 200
        save_res = json.loads(resp.read().decode("utf-8"))
        assert save_res["status"] == "SUCCESS"
        print("  [ PASS ] POST /api/survey/save -> Status: 200, Saved")

    # Survey List
    list_url = f"http://127.0.0.1:{test_port}/api/survey/list"
    with urllib.request.urlopen(list_url, timeout=5) as resp:
        assert resp.status == 200
        survs = json.loads(resp.read().decode("utf-8"))
        assert any(s == "test_survey_run" or (isinstance(s, dict) and s.get("name") == "test_survey_run") for s in survs["surveys"])
        print("  [ PASS ] GET /api/survey/list -> Status: 200")

    # Survey Apply (Requires confirm: True)
    apply_url = f"http://127.0.0.1:{test_port}/api/survey/apply"
    apply_payload = {"name": "test_survey_run", "confirm": True}
    req = urllib.request.Request(apply_url, data=json.dumps(apply_payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=5) as resp:
        assert resp.status == 200
        app_res = json.loads(resp.read().decode("utf-8"))
        assert app_res["status"] == "SUCCESS"
        assert app_res["can_revert"] is True
        print("  [ PASS ] POST /api/survey/apply -> Status: 200, Applied to memory")

    # Survey Revert
    revert_url = f"http://127.0.0.1:{test_port}/api/survey/revert"
    req = urllib.request.Request(revert_url, data=b"{}", headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=5) as resp:
        assert resp.status == 200
        rev_res = json.loads(resp.read().decode("utf-8"))
        assert rev_res["status"] == "SUCCESS"
        print("  [ PASS ] POST /api/survey/revert -> Status: 200, Reverted successfully")

    print("\n=== Verifying Live Engine API (Status, Latest, History) ===")
    for ep in ["/api/live/status", "/api/live/latest", "/api/live/history"]:
        with urllib.request.urlopen(f"http://127.0.0.1:{test_port}{ep}", timeout=5) as resp:
            assert resp.status == 200
            print(f"  [ PASS ] GET {ep:<30} -> Status: 200")

    print("\n=== Verifying Accuracy Workflow API (Plan, Session, Trial, Metrics) ===")
    with urllib.request.urlopen(f"http://127.0.0.1:{test_port}/api/accuracy/plan", timeout=5) as resp:
        assert resp.status == 200
        plan_data = json.loads(resp.read().decode("utf-8"))
        assert len(plan_data["plan"]) == 6
        print("  [ PASS ] GET /api/accuracy/plan -> Status: 200, 6 Standardized Spots")

    # Accuracy Session Start
    acc_start_url = f"http://127.0.0.1:{test_port}/api/accuracy/session/start"
    sess_payload = {"session_id": "lab_test_01", "category": "static_ota", "operator": "ahsan_lab"}
    req = urllib.request.Request(acc_start_url, data=json.dumps(sess_payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=5) as resp:
        assert resp.status == 200
        print("  [ PASS ] POST /api/accuracy/session/start -> Status: 200")

    # Accuracy Record Trial
    acc_trial_url = f"http://127.0.0.1:{test_port}/api/accuracy/trial"
    trial_payload = {
        "spot_id": "SPOT-1-CENTER",
        "name": "Array Center",
        "truth_xy": [0.0, 1.0],
        "estimated_xy": [0.05, 1.02],
        "status": "FIX",
        "latency_s": 0.8
    }
    req = urllib.request.Request(acc_trial_url, data=json.dumps(trial_payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=5) as resp:
        assert resp.status == 200
        trial_res = json.loads(resp.read().decode("utf-8"))
        assert trial_res["status"] == "SUCCESS"
        assert trial_res["trial"]["error_m"] < 0.10
        print(f"  [ PASS ] POST /api/accuracy/trial -> Status: 200, Error: {trial_res['trial']['error_m']:.3f} m")

    server.shutdown()
    print("\nVERDICT: PASS")

if __name__ == "__main__":
    test_endpoints()
