"""
test_advanced_algorithms.py - Verification suite for advanced research-grade algorithms.
"""

import os
import sys
import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src")))

from src.algorithms.solvers import (
    solve_chan_ho, solve_taylor_series, solve_multilateration_ensemble,
    solve_adra_3stage, compute_crlb, get_error_ellipse
)
from src.algorithms.tracker import TargetEKFTracker
from src.algorithms.gdop import calculate_gdop_point, generate_gdop_grid
from src.algorithms.adra_tdoa import auto_calibrate_coax_bench, compute_chromatic_group_delay_offset_s
from src.algorithms.sigint_amc import detect_and_classify_emitters
from src.processing.benchmark_ablation import run_ablation_benchmark


def run_advanced_algorithms_test():
    print("=== Running TEST-ADV-ALGORITHMS: Multi-Solver, ADRA-TDOA, EKF, GDOP & SIGINT Suite ===")

    # Setup standard 3-station geometry
    nodes = {
        "node1": np.array([0.0, 0.0]),
        "node2": np.array([270.0, 30.0]),
        "node3": np.array([110.0, 240.0])
    }
    truth_tgt = np.array([125.0, 95.0])

    # Range differences
    pairs = [("node1", "node2"), ("node1", "node3"), ("node2", "node3")]
    pair_dd = {}
    for i, j in pairs:
        dd = float(np.hypot(*(truth_tgt - nodes[j])) - np.hypot(*(truth_tgt - nodes[i])))
        # Add tiny realistic measurement noise (5 cm)
        pair_dd[(i, j)] = dd + 0.05 * np.random.randn()

    # 1. Test Chan-Ho Solver
    p_chan, res_chan = solve_chan_ho(nodes, pair_dd)
    err_chan = float(np.hypot(*(p_chan - truth_tgt)))
    print(f"  [1] Chan-Ho Closed-Form Fix:  ({p_chan[0]:.2f}, {p_chan[1]:.2f}) m (Error = {err_chan:.3f} m, Residual = {res_chan:.4f} m)")
    assert err_chan < 0.50, f"Chan-Ho error {err_chan:.3f} m exceeds 0.5m"

    # 2. Test Taylor-Series Solver
    p_taylor, res_taylor = solve_taylor_series(nodes, pair_dd)
    err_taylor = float(np.hypot(*(p_taylor - truth_tgt)))
    print(f"  [2] Taylor-Series Iterative:   ({p_taylor[0]:.2f}, {p_taylor[1]:.2f}) m (Error = {err_taylor:.3f} m, Residual = {res_taylor:.4f} m)")
    assert err_taylor < 0.50, f"Taylor-Series error {err_taylor:.3f} m exceeds 0.5m"

    # 3. Test ADRA 3-Stage Hybrid Solver & CRLB Error Ellipse
    ensemble = solve_adra_3stage(nodes, pair_dd)
    print(f"  [3] ADRA-TDOA 3-Stage Solver:  Selected: {ensemble['solver_used']}, Position: {ensemble['position']}")
    ellipse = ensemble["error_ellipse"]
    print(f"      CRLB 95% Confidence Ellipse: Major={ellipse['semi_major_m']}m, Minor={ellipse['semi_minor_m']}m, Tilt={ellipse['tilt_deg']}°")
    assert ellipse["semi_major_m"] > 0.0 and ellipse["semi_minor_m"] > 0.0

    # 4. Test EKF Tracker over Moving UAV Flight Path
    print("  [4] Simulating Moving Target (UAV at 10 m/s heading northeast)...")
    tracker = TargetEKFTracker(q_accel=0.2, sigma_meas_m=0.3)
    p_start = np.array([50.0, 50.0])
    v_true = np.array([7.07, 7.07])  # 10 m/s at 45 deg
    tracker.initialize(p_start + np.random.randn(2)*2.0, v_init=(0.0, 0.0), t0=0.0)

    time_steps = [0.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0]
    ekf_errors = []
    for t in time_steps[1:]:
        p_true_t = p_start + v_true * t
        meas_dd_t = {}
        for i, j in pairs:
            true_dd = float(np.hypot(*(p_true_t - nodes[j])) - np.hypot(*(p_true_t - nodes[i])))
            meas_dd_t[(i, j)] = true_dd + 0.15 * np.random.randn()

        state = tracker.update_with_tdoa(nodes, meas_dd_t, timestamp=t)
        err_pos = float(np.hypot(state["x"] - p_true_t[0], state["y"] - p_true_t[1]))
        ekf_errors.append(err_pos)
        print(f"      t={t:4.1f}s | True: ({p_true_t[0]:5.1f}, {p_true_t[1]:5.1f}) | EKF: ({state['x']:5.1f}, {state['y']:5.1f}) | Speed: {state['speed_mps']:4.1f} m/s | Error: {err_pos:4.2f}m")

    mean_ekf_err = np.mean(ekf_errors[-3:])  # Steady state error
    print(f"      Steady-State EKF Tracking Error: {mean_ekf_err:.3f} m")
    assert mean_ekf_err < 1.5, f"EKF tracking error {mean_ekf_err} exceeds 1.5m"

    # 5. Test Spatial GDOP Engine
    gdop_target = calculate_gdop_point(nodes, truth_tgt)
    print(f"  [5] Target Geometric Dilution (GDOP): {gdop_target:.2f} (Target is inside baseline array)")
    assert 0.5 <= gdop_target <= 3.5, f"Unexpected GDOP {gdop_target}"

    gdop_grid = generate_gdop_grid(nodes, grid_res=25)
    print(f"      Generated GDOP Grid: {len(gdop_grid['x_coords'])}x{len(gdop_grid['y_coords'])} cells (Min GDOP: {gdop_grid['min_gdop']:.2f}, Mean: {gdop_grid['mean_gdop']:.2f})")

    # 6. Test Closed-Coaxial Bench V4 Auto-Calibration
    bench_res = auto_calibrate_coax_bench({
        ("node1", "node2"): 0.08,
        ("node1", "node3"): 15.04,
        ("node2", "node3"): 14.95
    })
    print(f"  [6] Lab Coax Bench V4 Auto-Calibration: S3 Offset = {bench_res['node3_cal_ns']} ns, Closure = {bench_res['loop_closure_error_ns']} ns ({bench_res['status']})")
    assert abs(bench_res["node3_cal_ns"] - 15.0) < 0.2 and bench_res["loop_closure_error_ns"] < 0.2

    # 7. Test SIGINT CA-CFAR Unknown Emitter Detector & AMC Classifier
    freqs = np.linspace(-1200, 1200, 256)
    psd = np.full(256, -78.0)
    psd[np.abs(freqs - (-570.0)) <= 240.0] = -42.0
    psd[np.abs(freqs - (+570.0)) <= 240.0] = -45.0
    psd[np.abs(freqs - (+920.0)) <= 35.0] = -52.0  # Unknown FSK bogey
    sigint_out = detect_and_classify_emitters(freqs, psd)
    print(f"  [7] SIGINT CA-CFAR + AMC: Detected {len(sigint_out['intercepts'])} emitters: {[x['track_id'] for x in sigint_out['intercepts']]}")
    assert len(sigint_out["intercepts"]) >= 3

    # 8. Test IEEE Ablation Benchmark
    bench_ablation = run_ablation_benchmark(num_trials=20)
    adra_row = [r for r in bench_ablation["algorithms"] if r["algorithm_id"] == "adra"][0]
    std_row = [r for r in bench_ablation["algorithms"] if r["algorithm_id"] == "standard"][0]
    print(f"  [8] IEEE Ablation Benchmark: ADRA Urban MP RMSE = {adra_row['urban_multipath_rmse_m']} m vs Standard = {std_row['urban_multipath_rmse_m']} m (+{adra_row['improvement_vs_baseline_pct']}% gain)")
    assert adra_row["urban_multipath_rmse_m"] < std_row["urban_multipath_rmse_m"]

    print("VERDICT: PASS\n")
    return True


if __name__ == "__main__":
    run_advanced_algorithms_test()

