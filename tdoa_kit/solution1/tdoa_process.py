#!/usr/bin/env python3
"""
tdoa_process.py  -  Solution 1: turn the recordings into a position. Runs on the laptop.

    python3 tdoa_process.py --config config.json --dir captures            (all captures)
    python3 tdoa_process.py --config config.json --dir captures --slot 20261020_101500

For each capture time ("slot") it:
  1. loads one recording per node                              (node1_<slot>.cu8 ...)
  2. measures and removes each dongle's small frequency error   (from the reference signal)
  3. cuts out the reference band and the target band
  4. for each node pair, cross-correlates short pieces (10 ms) of both bands
     -> lag of the reference  D_ref   and lag of the target  D_tgt
     The unknown recording start-time difference is inside BOTH lags and
     cancels when we subtract them:   d = D_tgt - D_ref
  5. adds back the reference's known geometry -> target TDOA -> range difference
  6. finds the point that matches all range differences (hyperbola crossing)
  7. prints the answer, a Google-Maps link and the error vs. truth (if given),
     saves a map picture  result_<slot>.png  and appends to results.csv
"""
import argparse
import csv
import glob
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "common"))
from tdoa_lib import (C, load_config, load_cu8, extract_band, mix, estimate_cfo_bpsk,
                      segment_lag, solve_position, plot_solution, xy_to_latlon, model_dd,
                      compute_pair_tdoa_adra, compute_chromatic_group_delay_offset_s)

p = argparse.ArgumentParser()
p.add_argument("--config", default="config.json")
p.add_argument("--dir", default="captures")
p.add_argument("--slot", default=None, help="process only this capture time, e.g. 20261020_101500")
p.add_argument("--seg_ms", type=float, default=10.0, help="length of each correlation piece")
p.add_argument("--nseg", type=int, default=60, help="how many pieces per recording")
p.add_argument("--max_residual", type=float, default=15.0,
               help="with 4+ nodes: warn when pairs disagree by more than this (m)")
p.add_argument("--min_quality", type=float, default=8.0, help="reject weak correlation peaks")
p.add_argument("--algo", default="adra", choices=["adra", "gcc_phat", "standard"],
               help="TDOA algorithm mode: adra (novel), gcc_phat, or standard")
p.add_argument("--bench", action="store_true",
               help="Lab Coax Bench Zero-Check Mode (true geometric Delta_d = 0 m, auto-calibrates V4 offset)")
p.add_argument("--batch", action="store_true",
               help="Perform robust multi-capture median aggregation across all slots")
p.add_argument("--slots", default=None, help="comma-separated list of capture times")
p.add_argument("--strict_closure", action="store_true", default=True,
               help="automatically drop slots failing Kirchhoff loop-closure (|closure| > 2.5 m or <3 pairs)")
a = p.parse_args()

cfg = load_config(a.config)
fs = cfg["sample_rate"]
fc = cfg["rx_center_hz"]
f_ref = cfg["ref_signal_hz"] - fc            # reference position inside the recording (Hz)
f_tgt = cfg["target_signal_hz"] - fc         # target position inside the recording (Hz)
bw = cfg["band_hz"]
margin = cfg.get("cfo_margin_hz", 25_000)
nodes = list(cfg["nodes"].keys())
node_xy, ref_xy, truth_xy = cfg["_node_xy"], cfg["_ref_xy"], cfg["_truth_xy"]
cal_matrix = cfg.get("_cal_matrix", {})
lat0, lon0 = cfg["_origin"]


def prepare(path):
    """Load one recording, remove its frequency error, return (ref_band, target_band)."""
    x = load_cu8(path)
    x = x - np.mean(x)                                   # remove the DC spike
    rough = extract_band(x, fs, f_ref, bw + 2 * margin)  # reference band, extra wide
    cfo, cfo_snr = estimate_cfo_bpsk(rough, fs, max_cfo=margin)
    x = mix(x, fs, cfo)                                  # remove frequency error
    ref = extract_band(x, fs, f_ref, bw)
    tgt = extract_band(x, fs, f_tgt, bw + 2 * margin)    # target's own TX error is unknown
    p_ref = 10 * np.log10(np.mean(np.abs(ref) ** 2) + 1e-20)
    p_tgt = 10 * np.log10(np.mean(np.abs(tgt) ** 2) + 1e-20)
    return ref, tgt, cfo, cfo_snr, p_ref, p_tgt


def pair_tdoa(ri, ti, rj, tj, mode=None):
    """Measured target differential delay for the pair (i, j), in seconds, and its spread."""
    algo_mode = a.algo if mode is None else mode
    d_sec, spread_sec, used, q0, telemetry = compute_pair_tdoa_adra(
        ri, ti, rj, tj, fs, seg_ms=a.seg_ms, nseg=a.nseg, min_quality=a.min_quality, mode=algo_mode
    )
    return d_sec, spread_sec, used, q0


def residual_rms(p, pair_dd):
    """How well a position agrees with the measurements (metres, small = good)."""
    r = [model_dd(p, node_xy[i], node_xy[j]) - v for (i, j), v in pair_dd.items()]
    return float(np.sqrt(np.mean(np.square(r))))


def process_slot(slot):
    files = {n: os.path.join(a.dir, f"{n}_{slot}.cu8") for n in nodes}
    have = [n for n in nodes if os.path.exists(files[n])]
    if len(have) < 3:
        print(f"[{slot}] only {len(have)} node files found - need at least 3, skipping")
        return None
    print(f"\n=== Capture {slot}: nodes {', '.join(have)} [Algorithm: {a.algo.upper()}] ===")
    data = {}
    for n in have:
        ref, tgt, cfo, cfo_snr, p_ref, p_tgt = prepare(files[n])
        data[n] = (ref, tgt)
        print(f"  {n}: frequency error {cfo:+8.0f} Hz ({cfo / fc * 1e6:+.2f} ppm)   "
              f"ref tone SNR {cfo_snr:4.1f} dB   ref power {p_ref:6.1f} dB   target power {p_tgt:6.1f} dB")
    pair_dd, pair_w = {}, {}
    pairs = [(have[i], have[j]) for i in range(len(have)) for j in range(i + 1, len(have))]
    for base, n in pairs:                                # every pair of nodes
        ri, ti = data[base]
        rj, tj = data[n]
        d, sd, used, q0 = pair_tdoa(ri, ti, rj, tj)
        if d is None:
            print(f"  pair {base}-{n}: FAILED (coarse quality {q0:.1f}, good pieces {used})")
            continue
        # Apply Chromatic Group-Delay Calibration Matrix if configured in config.json
        if "chromatic_cal_matrix" in cfg:
            cal_diff_s = compute_chromatic_group_delay_offset_s(base, n, cal_matrix)
            d = d - cal_diff_s
        if a.bench:
            ref_geom = 0.0
        else:
            ref_geom = (np.hypot(*(ref_xy - node_xy[n])) - np.hypot(*(ref_xy - node_xy[base]))) / C
        tdoa = d + ref_geom                                    # target TDOA (s), node n minus base
        baseline = float(np.hypot(*(node_xy[n] - node_xy[base])))
        if abs(C * tdoa) > baseline + 1.0:                     # strict triangle inequality (+1 m margin)
            print(f"  pair {base}-{n}: REJECTED - |range diff| {abs(C * tdoa):.2f} m exceeds "
                  f"baseline {baseline:.2f} m (physically impossible)")
            continue
        pair_dd[(base, n)] = C * tdoa
        sd_m = max(C * sd, 0.5)
        pair_w[(base, n)] = 1.0 / sd_m ** 2
        print(f"  pair {base}-{n}: target TDOA {tdoa * 1e9:9.2f} ns  -> range difference "
              f"{C * tdoa:8.2f} m  (+/-{sd_m:.2f} m, {used} pieces)")
    if len(pair_dd) < 2:
        print("  Not enough good pairs for a 2-D fix.")
        return None
    # Kirchhoff Loop Closure Validation (for 3-node systems)
    if len(node_xy) == 3 and len(pair_dd) < 3:
        print(f"  [STRICT REJECTION] Only {len(pair_dd)}/3 pairs passed baseline gate -> dropping corrupted slot.")
        return None

    if ("node1", "node2") in pair_dd and ("node1", "node3") in pair_dd and ("node2", "node3") in pair_dd:
        closure = pair_dd[("node1", "node2")] + pair_dd[("node2", "node3")] - pair_dd[("node1", "node3")]
        passed = abs(closure) <= 2.5
        status_str = f"[PASS - HIGH CONFIDENCE ({abs(closure):.2f} m)]" if passed else f"[REJECTED - MULTIPATH ECHO ({abs(closure):.2f} m > 2.5 m)]"
        print(f"  Kirchhoff Loop Closure: {closure:+.2f} m (ideal = 0.00 m)  {status_str}")
        if not passed and a.strict_closure:
            print(f"  [STRICT REJECTION] Slot dropped due to Kirchhoff loop closure inconsistency.")
            return None

    est, cost, grid = solve_position(node_xy, pair_dd, pair_w, mode=a.algo)
    rms = residual_rms(est, pair_dd)
    print(f"  Fit residual (all nodes): {rms:.2f} m")
    if len(node_xy) >= 4 and rms > a.max_residual:
        print("  NOTE: the node pairs disagree by more than "
              f"{a.max_residual:.0f} m - reflections at one site? Check each node's line of sight to the target area.")
    lat, lon = xy_to_latlon(est[0], est[1], lat0, lon0)
    print(f"  ESTIMATED TARGET: {lat:.6f}, {lon:.6f}   https://maps.google.com/?q={lat:.6f},{lon:.6f}")
    err = None
    if truth_xy is not None:
        err = float(np.hypot(*(est - truth_xy)))
        print(f"  Error vs. surveyed true target position: {err:.2f} m")
    title = f"TDOA fix {slot} [{a.algo.upper()}]" + (f"   (error {err:.2f} m)" if err is not None else "")
    png = os.path.join(a.dir, f"result_{slot}.png")
    plot_solution(node_xy, ref_xy, est, truth_xy, pair_dd, cost, grid, png, title)
    print(f"  Map saved: {png}")
    return slot, lat, lon, err, pair_dd, pair_w


if a.slots:
    slots = [s.strip() for s in a.slots.split(",") if s.strip()]
elif a.slot:
    slots = [a.slot]
else:
    slots = sorted({os.path.basename(f).split("_", 1)[1][:-4]
                    for f in glob.glob(os.path.join(a.dir, "*_*.cu8"))})
results = [r for r in (process_slot(s) for s in slots) if r]
if results:
    out = os.path.join(a.dir, "results.csv")
    new = not os.path.exists(out)
    with open(out, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["slot", "lat", "lon", "error_m"])
        w.writerows([[r[0], r[1], r[2], r[3]] for r in results])
    errs = [r[3] for r in results if r[3] is not None]
    if errs:
        print(f"\nSUMMARY over {len(errs)} captures: median error {np.median(errs):.2f} m, "
              f"worst {np.max(errs):.2f} m")
    print(f"Results appended to {out}")

    # Robust Multi-Capture Batch Median Aggregation
    valid_slots = [r for r in results if r is not None and len(r[4]) >= 2]
    if a.batch or (len(valid_slots) >= 2 and a.slot is None):
        print("\n" + "=" * 68)
        print(f"=== MULTI-CAPTURE BATCH MEDIAN AGGREGATION ({len(valid_slots)} Valid Slots) ===")
        print("=" * 68)
        all_pairs = sorted({p for r in valid_slots for p in r[4].keys()})
        batch_dd, batch_w = {}, {}
        for pair in all_pairs:
            vals = np.array([r[4][pair] for r in valid_slots if pair in r[4]], dtype=float)
            weights = np.array([r[5][pair] for r in valid_slots if pair in r[5]], dtype=float)
            med_val = float(np.median(vals))
            mad = float(np.median(np.abs(vals - med_val)))
            batch_dd[pair] = med_val
            batch_w[pair] = float(np.mean(weights))
            print(f"  Batch Pair {pair[0]}-{pair[1]}: median range diff = {med_val:+7.2f} m  "
                  f"(MAD ±{mad:.2f} m over {len(vals)} slots)")

        if ("node1", "node2") in batch_dd and ("node1", "node3") in batch_dd and ("node2", "node3") in batch_dd:
            b_closure = batch_dd[("node1", "node2")] + batch_dd[("node2", "node3")] - batch_dd[("node1", "node3")]
            print(f"  Batch Kirchhoff Loop Closure: {b_closure:+.2f} m (ideal = 0.00 m)"
                  + (" [EXCELLENT]" if abs(b_closure) <= 1.5 else " [MARGINAL]"))

        est_b, cost_b, grid_b = solve_position(node_xy, batch_dd, batch_w, mode=a.algo)
        rms_b = residual_rms(est_b, batch_dd)
        lat_b, lon_b = xy_to_latlon(est_b[0], est_b[1], lat0, lon0)
        print(f"  Batch Fit Residual: {rms_b:.2f} m")
        print(f"  BATCH ESTIMATED TARGET: {lat_b:.6f}, {lon_b:.6f}   https://maps.google.com/?q={lat_b:.6f},{lon_b:.6f}")
        err_b = None
        if truth_xy is not None:
            err_b = float(np.hypot(*(est_b - truth_xy)))
            print(f"  BATCH ERROR vs Surveyed True Target: {err_b:.2f} m")
        png_b = os.path.join(a.dir, "result_batch_median.png")
        title_b = f"TDOA Batch Fix ({len(valid_slots)} slots) [{a.algo.upper()}]" + (f"   (error {err_b:.2f} m)" if truth_xy is not None else "")
        plot_solution(node_xy, ref_xy, est_b, truth_xy, batch_dd, cost_b, grid_b, png_b, title_b)
        print(f"  Batch Map saved: {png_b}")


