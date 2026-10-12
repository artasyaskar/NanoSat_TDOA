#!/usr/bin/env python3
"""
process_capture.py - Production-grade ADRA-TDOA solver & ablation CLI for laptop processing.

Usage:
    python process_capture.py --config configs/geometry/site_simulation.json \
                              --captures-dir captures/ \
                              --slot 20261016_120000 \
                              --algo adra
"""

import argparse
import glob
import json
import os
import sys
import numpy as np

# Ensure root in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from src.processing.core import (
    C_LIGHT, load_site_config, load_cu8, extract_band,
    compute_pair_tdoa, solve_position_2d
)
from src.algorithms.adra_tdoa import auto_calibrate_coax_bench, compute_chromatic_group_delay_offset_s


def main():
    p = argparse.ArgumentParser(description="ADRA-TDOA Hyperbolic Location & Calibration Solver")
    p.add_argument("--config", default="configs/geometry/site_simulation.json", help="Path to site configuration JSON")
    p.add_argument("--captures-dir", default="captures", help="Directory containing .cu8 and .json captures")
    p.add_argument("--slot", default=None, help="Specific UTC timestamp slot to process (e.g. 20261016_120000)")
    p.add_argument("--algo", default="adra", choices=["adra", "gcc_phat", "standard"],
                   help="TDOA algorithm mode: adra (novel), gcc_phat, or standard")
    p.add_argument("--bench", action="store_true",
                   help="Lab Closed-Coaxial Bench Zero-Check Mode (true geometric Delta_d = 0 m)")
    p.add_argument("--out", default=None, help="Output JSON results path")
    args = p.parse_args()

    if not os.path.exists(args.config):
        sys.exit(f"[FATAL] Config file not found: {args.config}")

    cfg = load_site_config(args.config)
    fs = cfg.get("sample_rate_sps", cfg.get("sample_rate", 2400000))
    rx_center = cfg.get("receiver_center_hz", cfg.get("rx_center_hz", 433920000))
    bw = cfg.get("occupied_bandwidth_hz", cfg.get("band_hz", 600000))
    nodes = cfg["_node_xy"]
    ref_xy = cfg["_ref_xy"]
    truth_xy = cfg.get("_truth_xy")
    cal_matrix = cfg.get("chromatic_cal_matrix", {})

    # Authoritative baseband frequencies:
    if "ref_signal_hz" in cfg:
        ref_rf = cfg["ref_signal_hz"]
    else:
        ref_rf = cfg["reference_tx"]["rf_center_hz"] + cfg["reference_tx"]["digital_offset_hz"]

    if "target_signal_hz" in cfg:
        tgt_rf = cfg["target_signal_hz"]
    else:
        tgt_rf = cfg["target_tx"]["rf_center_hz"] + cfg["target_tx"]["digital_offset_hz"]

    f_ref_bb = ref_rf - rx_center  # -570 kHz
    f_tgt_bb = tgt_rf - rx_center  # +570 kHz

    print("=" * 72)
    print(f"       NanoSat_TDOA Solution 1 Localization Engine [{args.algo.upper()}]")
    print("=" * 72)
    print(f"  Receiver Center:    {rx_center/1e6:.3f} MHz (Fs = {fs/1e6:.1f} MS/s)")
    print(f"  Reference Baseband: {f_ref_bb/1e3:+.1f} kHz  |  Target Baseband: {f_tgt_bb/1e3:+.1f} kHz")
    print(f"  Filter Bandwidth:   {bw/1e3:.1f} kHz (Guard Band: {abs(f_tgt_bb - f_ref_bb) - bw:.0f} Hz)")

    # Find matching capture files
    node_files = {}
    for node_name in cfg["nodes"]:
        pattern = f"{node_name}_{args.slot}*.cu8" if args.slot else f"{node_name}_*.cu8"
        matches = sorted(glob.glob(os.path.join(args.captures_dir, pattern)))
        if not matches:
            sys.exit(f"[FATAL] No capture file found for {node_name} matching '{pattern}' in {args.captures_dir}")
        node_files[node_name] = matches[-1]

    print("\n  Matched Station Captures:")
    processed_bands = {}
    for node_name, cu8_path in node_files.items():
        json_path = os.path.splitext(cu8_path)[0] + ".json"
        meta = {}
        if os.path.exists(json_path):
            with open(json_path) as f_m:
                meta = json.load(f_m)
        if "cal_delay_offset_ns" in meta:
            cal_ns = float(meta["cal_delay_offset_ns"])
        elif node_name in cal_matrix:
            cal_ns = float(cal_matrix[node_name].get("chromatic_delay_ns", 0.0))
        else:
            cal_ns = 0.0
        print(f"    {node_name:<8}: {os.path.basename(cu8_path)} (Cal offset: {cal_ns:+.2f} ns)")

        raw_iq = load_cu8(cu8_path)
        ref_b = extract_band(raw_iq, fs, f_ref_bb, bw)
        tgt_b = extract_band(raw_iq, fs, f_tgt_bb, bw)
        processed_bands[node_name] = (ref_b, tgt_b, cal_ns)

    pairs = [("node1", "node2"), ("node1", "node3"), ("node2", "node3")]
    pair_dd = {}
    pair_spread = {}
    raw_d_ns_map = {}

    print("\n  Pairwise Differential Time-Difference Calculations:")
    print("  Pair        |  Raw d (ns) | Cal Offset (ns) | Ref Geom (m) | Final DD (m) | Skew (ppm) | Quality")
    print("  " + "-" * 88)

    for base, other in pairs:
        ri, ti, cal_i = processed_bands[base]
        rj, tj, cal_j = processed_bands[other]

        d_sec, spread, used, q0, telem = compute_pair_tdoa(
            ri, ti, rj, tj, fs, seg_ms=10.0, nseg=60, mode=args.algo, return_telemetry=True
        )
        if d_sec is None:
            sys.exit(f"[FATAL] Correlation failed between {base} and {other} (q0={q0:.1f}, valid segments={used})")

        raw_d_ns_map[(base, other)] = d_sec * 1e9
        cal_delay_diff_s = (cal_j - cal_i) * 1e-9
        corrected_d_sec = d_sec - cal_delay_diff_s

        if args.bench:
            delta_r_ref = 0.0
        else:
            d_ref_other = float(np.hypot(*(ref_xy - nodes[other])))
            d_ref_base = float(np.hypot(*(ref_xy - nodes[base])))
            delta_r_ref = d_ref_other - d_ref_base

        final_dd_m = (corrected_d_sec * C_LIGHT) + delta_r_ref
        pair_dd[(base, other)] = final_dd_m
        pair_spread[(base, other)] = spread
        skew_ppm = telem.get("clock_drift_ppm", 0.0)

        print(f"  {base:<5}-{other:<5} | {d_sec*1e9:11.2f} | {cal_delay_diff_s*1e9:15.2f} | "
              f"{delta_r_ref:12.2f} | {final_dd_m:12.2f} | {skew_ppm:+10.3f} | {q0:6.1f} ({used} seg)")

    if args.bench:
        bench_cal = auto_calibrate_coax_bench(raw_d_ns_map)
        print("\n" + "=" * 72)
        print("              CLOSED-COAXIAL BENCH ZERO-CHECK & V4 AUTO-CALIBRATION")
        print("=" * 72)
        print(f"  Station 1 (V3 R820T2) Offset: {bench_cal['node1_cal_ns']:+.3f} ns")
        print(f"  Station 2 (V3 R820T2) Offset: {bench_cal['node2_cal_ns']:+.3f} ns")
        print(f"  Station 3 (V4 R828D)  Offset: {bench_cal['node3_cal_ns']:+.3f} ns")
        print(f"  3-Node Loop Closure Residual: {bench_cal['loop_closure_error_ns']:.4f} ns ({bench_cal['loop_closure_error_m']:.4f} m)")
        print("=" * 72)

    est_xy, raw_cost, _ = solve_position_2d(nodes, pair_dd, mode=args.algo)
    cost = float(np.squeeze(raw_cost))

    print("\n" + "=" * 72)
    print("                         POSITION ESTIMATE")
    print("=" * 72)
    print(f"  Estimated Position:  X = {est_xy[0]:.2f} m,  Y = {est_xy[1]:.2f} m")
    err_m = None
    if truth_xy is not None:
        err_m = float(np.hypot(*(est_xy - truth_xy)))
        print(f"  Ground Truth:        X = {truth_xy[0]:.2f} m,  Y = {truth_xy[1]:.2f} m")
        print(f"  Absolute Fix Error:  {err_m:.3f} meters")
    print(f"  Residual Cost RMS:   {cost:.4f} meters")
    print("=" * 72)

    if args.out:
        out_dict = {
            "algorithm": args.algo,
            "estimated_target_xy": [float(est_xy[0]), float(est_xy[1])],
            "residual_cost_m": cost,
            "pair_dd_m": {f"{k[0]}_{k[1]}": float(v) for k, v in pair_dd.items()},
            "truth_error_m": float(err_m) if err_m is not None else None
        }
        with open(args.out, "w") as f_o:
            json.dump(out_dict, f_o, indent=2)
        print(f"  Results exported to: {args.out}")


if __name__ == "__main__":
    main()
