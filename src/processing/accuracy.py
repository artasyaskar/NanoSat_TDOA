"""
accuracy.py - Independent accuracy testing, ground-truth surveying, and metric evaluation.

Provides rigorous test harness for:
  - CONDUCTED_CALIBRATION (cable / bench tests)
  - SYNTHETIC             (known simulation tests)
  - STATIC_OTA            (fixed floor positions with surveyed ground truth)
  - MOVING_OTA            (dynamic walking / corner trails)

Evaluates bias, RMSE, median error, 95th-percentile error, failure rate, and latency.
Exports raw trials and metric summaries to CSV.
Invented or assumed accuracy values are strictly prohibited.
"""
import csv
import math
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from src.processing import survey

TEST_CATEGORIES = ("conducted_calibration", "synthetic", "static_ota", "moving_ota")


def generate_lab_test_plan(stations: Dict[str, Any], clearance_m: float = 0.8) -> List[Dict[str, Any]]:
    """
    Generate at least 6 standard static target positions covering the array:
      1. Center (Centroid)
      2. Corner near Station 1
      3. Corner near Station 2
      4. Corner near Station 3
      5. Baseline Midpoint (between S1 and S2)
      6. Outside Triangle (exterior reference)
    """
    s1 = np.asarray(stations["node1"], float)
    s2 = np.asarray(stations["node2"], float)
    s3 = np.asarray(stations["node3"], float)

    centroid = (s1 + s2 + s3) / 3.0

    # Inward offset from vertices toward centroid
    def _inward(vertex):
        v = centroid - vertex
        dist = np.linalg.norm(v)
        if dist < 1e-6:
            return vertex
        step = min(clearance_m, dist * 0.4)
        return vertex + (v / dist) * step

    spot_s1 = _inward(s1)
    spot_s2 = _inward(s2)
    spot_s3 = _inward(s3)

    # Baseline midpoint (S1-S2) nudged slightly toward center
    mid12 = (s1 + s2) / 2.0
    dir_to_s3 = s3 - mid12
    norm_s3 = np.linalg.norm(dir_to_s3)
    spot_mid = mid12 + (dir_to_s3 / norm_s3) * min(0.5, norm_s3 * 0.2) if norm_s3 > 1e-6 else mid12

    # Exterior spot: behind S1-S2 baseline away from S3
    spot_outside = mid12 - (dir_to_s3 / norm_s3) * 1.2 if norm_s3 > 1e-6 else (mid12 - np.array([0.0, 1.2]))

    plan = [
        {
            "spot_id": "SPOT-1-CENTER",
            "name": "Array Center (Centroid)",
            "planned_xy": [round(float(centroid[0]), 3), round(float(centroid[1]), 3)],
            "zone": "CENTER",
            "description": "Geometric centroid of the 3 stations. Low GDOP benchmark.",
            "marking_guide": "Mark floor at intersection of medians; measure tape distances to S1, S2, S3.",
        },
        {
            "spot_id": "SPOT-2-CORNER-S1",
            "name": "Corner Near S1 (Alpha)",
            "planned_xy": [round(float(spot_s1[0]), 3), round(float(spot_s1[1]), 3)],
            "zone": "CORNER_S1",
            "description": "Near Station 1 with 0.8m inward offset.",
            "marking_guide": "Offset 0.8m from S1 antenna tripod along line toward centroid.",
        },
        {
            "spot_id": "SPOT-3-CORNER-S2",
            "name": "Corner Near S2 (Bravo)",
            "planned_xy": [round(float(spot_s2[0]), 3), round(float(spot_s2[1]), 3)],
            "zone": "CORNER_S2",
            "description": "Near Station 2 with 0.8m inward offset.",
            "marking_guide": "Offset 0.8m from S2 antenna tripod along line toward centroid.",
        },
        {
            "spot_id": "SPOT-4-CORNER-S3",
            "name": "Corner Near S3 (Charlie)",
            "planned_xy": [round(float(spot_s3[0]), 3), round(float(spot_s3[1]), 3)],
            "zone": "CORNER_S3",
            "description": "Near Station 3 with 0.8m inward offset.",
            "marking_guide": "Offset 0.8m from S3 antenna tripod along line toward centroid.",
        },
        {
            "spot_id": "SPOT-5-BASELINE-MID",
            "name": "Baseline Midpoint (S1-S2)",
            "planned_xy": [round(float(spot_mid[0]), 3), round(float(spot_mid[1]), 3)],
            "zone": "BASELINE_NEAR",
            "description": "Near the primary baseline S1-S2, inside triangle boundary.",
            "marking_guide": "Midway between S1 and S2, nudged 0.5m toward S3.",
        },
        {
            "spot_id": "SPOT-6-EXTERIOR",
            "name": "Outside Triangle (Exterior)",
            "planned_xy": [round(float(spot_outside[0]), 3), round(float(spot_outside[1]), 3)],
            "zone": "EXTERIOR",
            "description": "Outside receiver perimeter to evaluate GDOP degradation and boundary performance.",
            "marking_guide": "1.2m behind S1-S2 baseline, facing away from S3.",
        },
    ]
    return plan


class AccuracySession:
    def __init__(self, session_id: str, category: str = "static_ota", operator: str = "operator"):
        cat = category.lower()
        if cat not in TEST_CATEGORIES:
            raise ValueError(f"Category must be one of {TEST_CATEGORIES}, got {category}")
        self.session_id = str(session_id)
        self.category = cat
        self.operator = str(operator)
        self.created_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        self.spots: Dict[str, Dict[str, Any]] = {}
        self.trials: List[Dict[str, Any]] = []

    def add_spot(self, spot_id: str, name: str, truth_xy: Optional[Tuple[float, float]] = None,
                 tape_distances: Optional[Dict[str, float]] = None,
                 stations: Optional[Dict[str, Any]] = None,
                 description: str = "") -> Dict[str, Any]:
        """
        Add a test spot with ground truth.
        truth_xy can be given directly or solved from tape_distances to the stations.
        """
        sid = str(spot_id).strip()
        survey_res = None
        if truth_xy is None and tape_distances is not None and stations is not None:
            # Solve ground truth independently from tape distances
            st_dict = {k: np.asarray(v, float) for k, v in stations.items()}
            survey_res = survey.locate_from_distances(st_dict, tape_distances)
            truth_xy = (survey_res["xy"][0], survey_res["xy"][1])

        if truth_xy is None:
            raise ValueError("Must provide truth_xy or (tape_distances and stations)")

        spot_record = {
            "spot_id": sid,
            "name": name,
            "truth_xy": [float(truth_xy[0]), float(truth_xy[1])],
            "description": description,
            "survey_info": survey_res,
        }
        self.spots[sid] = spot_record
        return spot_record

    def record_trial(self, spot_id: str, estimated_xy: Optional[List[float]] = None,
                     status: str = "FIX", reason: Optional[str] = None,
                     latency_s: float = 0.0, residual_m: Optional[float] = None,
                     raw_metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Record an individual trial fix."""
        if spot_id not in self.spots:
            raise KeyError(f"Unknown spot_id: {spot_id}. Must add_spot first.")

        truth_xy = self.spots[spot_id]["truth_xy"]
        trial_id = len(self.trials) + 1

        error_m = None
        err_x = None
        err_y = None
        is_fix = (status.upper() == "FIX" and estimated_xy is not None)

        if is_fix:
            ex = float(estimated_xy[0])
            ey = float(estimated_xy[1])
            err_x = ex - truth_xy[0]
            err_y = ey - truth_xy[1]
            error_m = math.hypot(err_x, err_y)
            est_out = [ex, ey]
        else:
            est_out = None

        trial = {
            "trial_id": trial_id,
            "spot_id": spot_id,
            "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "status": "FIX" if is_fix else status.upper(),
            "reason": reason,
            "truth_xy": truth_xy,
            "estimated_xy": est_out,
            "error_m": round(error_m, 4) if error_m is not None else None,
            "error_x_m": round(err_x, 4) if err_x is not None else None,
            "error_y_m": round(err_y, 4) if err_y is not None else None,
            "latency_s": round(float(latency_s), 4),
            "residual_m": round(float(residual_m), 4) if residual_m is not None else None,
            "metadata": raw_metadata or {},
        }
        self.trials.append(trial)
        return trial

    def compute_metrics(self, spot_id: Optional[str] = None) -> Dict[str, Any]:
        """Compute statistical accuracy metrics."""
        trials = self.trials
        if spot_id is not None:
            trials = [t for t in trials if t["spot_id"] == spot_id]

        total = len(trials)
        if total == 0:
            return {
                "n_trials": 0, "n_fixes": 0, "n_failures": 0, "failure_rate_pct": 0.0,
                "rmse_m": None, "median_error_m": None, "p95_error_m": None,
                "bias_x_m": None, "bias_y_m": None, "radial_bias_m": None,
                "latency_mean_s": None,
            }

        fixes = [t for t in trials if t["status"] == "FIX" and t["error_m"] is not None]
        n_fixes = len(fixes)
        n_failures = total - n_fixes
        failure_rate = (n_failures / total) * 100.0

        latencies = [t["latency_s"] for t in trials if t.get("latency_s") is not None]
        latency_mean = float(np.mean(latencies)) if latencies else None

        if n_fixes == 0:
            return {
                "n_trials": total, "n_fixes": 0, "n_failures": n_failures,
                "failure_rate_pct": round(failure_rate, 2),
                "rmse_m": None, "median_error_m": None, "p95_error_m": None,
                "bias_x_m": None, "bias_y_m": None, "radial_bias_m": None,
                "latency_mean_s": round(latency_mean, 3) if latency_mean is not None else None,
            }

        errors = np.array([t["error_m"] for t in fixes], float)
        err_xs = np.array([t["error_x_m"] for t in fixes], float)
        err_ys = np.array([t["error_y_m"] for t in fixes], float)

        rmse = float(np.sqrt(np.mean(errors ** 2)))
        med_err = float(np.median(errors))
        p95_err = float(np.percentile(errors, 95))
        bias_x = float(np.mean(err_xs))
        bias_y = float(np.mean(err_ys))
        radial_bias = float(np.hypot(bias_x, bias_y))

        return {
            "n_trials": total,
            "n_fixes": n_fixes,
            "n_failures": n_failures,
            "failure_rate_pct": round(failure_rate, 2),
            "rmse_m": round(rmse, 4),
            "median_error_m": round(med_err, 4),
            "p95_error_m": round(p95_err, 4),
            "min_error_m": round(float(np.min(errors)), 4),
            "max_error_m": round(float(np.max(errors)), 4),
            "bias_x_m": round(bias_x, 4),
            "bias_y_m": round(bias_y, 4),
            "radial_bias_m": round(radial_bias, 4),
            "latency_mean_s": round(latency_mean, 3) if latency_mean is not None else None,
        }

    def export_csv(self, raw_path: str, summary_path: Optional[str] = None):
        """Export raw trial observations and optional summary to CSV."""
        os.makedirs(os.path.dirname(os.path.abspath(raw_path)), exist_ok=True)
        with open(raw_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "trial_id", "session_id", "category", "spot_id", "timestamp_utc",
                "status", "reason", "truth_x_m", "truth_y_m", "est_x_m", "est_y_m",
                "error_m", "error_x_m", "error_y_m", "latency_s", "residual_m"
            ])
            for t in self.trials:
                truth = t["truth_xy"]
                est = t["estimated_xy"] or ["", ""]
                writer.writerow([
                    t["trial_id"], self.session_id, self.category, t["spot_id"], t["timestamp_utc"],
                    t["status"], t["reason"] or "", truth[0], truth[1], est[0], est[1],
                    t["error_m"] if t["error_m"] is not None else "",
                    t["error_x_m"] if t["error_x_m"] is not None else "",
                    t["error_y_m"] if t["error_y_m"] is not None else "",
                    t["latency_s"], t["residual_m"] if t["residual_m"] is not None else "",
                ])

        if summary_path is not None:
            os.makedirs(os.path.dirname(os.path.abspath(summary_path)), exist_ok=True)
            with open(summary_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "session_id", "category", "spot_id", "n_trials", "n_fixes", "n_failures",
                    "failure_rate_pct", "rmse_m", "median_error_m", "p95_error_m",
                    "bias_x_m", "bias_y_m", "radial_bias_m", "latency_mean_s"
                ])
                # Overall
                overall = self.compute_metrics()
                writer.writerow([
                    self.session_id, self.category, "ALL", overall["n_trials"], overall["n_fixes"],
                    overall["n_failures"], overall["failure_rate_pct"], overall["rmse_m"],
                    overall["median_error_m"], overall["p95_error_m"], overall["bias_x_m"],
                    overall["bias_y_m"], overall["radial_bias_m"], overall["latency_mean_s"]
                ])
                # Per spot
                for sid in self.spots:
                    sm = self.compute_metrics(spot_id=sid)
                    writer.writerow([
                        self.session_id, self.category, sid, sm["n_trials"], sm["n_fixes"],
                        sm["n_failures"], sm["failure_rate_pct"], sm["rmse_m"],
                        sm["median_error_m"], sm["p95_error_m"], sm["bias_x_m"],
                        sm["bias_y_m"], sm["radial_bias_m"], sm["latency_mean_s"]
                    ])
