"""
replay.py - Deterministic, hardware-free replay of saved IQ captures.

Runs the SAME DSP/solver functions as tdoa_kit/solution1/tdoa_process.py (tdoa_lib +
src.algorithms.adra_tdoa) but returns a structured, fully-logged record instead of printing,
and NEVER hides a failure: every pair carries a status and a machine-readable reason.

Nothing here talks to hardware, the network, or a simulator. Results are tagged
data_source="REPLAY". The result dict contains no wall-clock values, so identical inputs
give byte-identical output (verified by tests/test_replay.py).

CLI:
    python -m src.processing.replay --dir captures --slot lab_live \
        --config tdoa_kit/solution1/config_lab.json --max-seconds 1.0 --out replay_log.json
"""
import argparse
import hashlib
import json
import math
import os
import sys
import time

import numpy as np

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
for _p in (_ROOT, os.path.join(_ROOT, "tdoa_kit", "common")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import tdoa_lib  # noqa: E402  (same module the CLI pipeline uses)
from tdoa_lib import (C, load_config, load_cu8, extract_band, mix, estimate_cfo_bpsk,  # noqa: E402
                      solve_position, model_dd, compute_pair_tdoa_adra,
                      compute_chromatic_group_delay_offset_s, xy_to_latlon)
from src.algorithms.adra_tdoa import adra_segment_lag  # noqa: E402

DATA_SOURCE = "REPLAY"
NODE_ORDER = ("node1", "node2", "node3")


# ----------------------------------------------------------------------------
# Geometry / configuration
# ----------------------------------------------------------------------------
def build_context(config_path=None, geometry=None):
    """
    Either load a pipeline config.json (lat/lon surveyed) or accept an explicit local-XY
    override: geometry = {"node_xy": {...}, "ref_xy": [x, y], "truth_xy": [x, y] | None,
                          "cal_matrix": {...} | None,
                          "sample_rate":..., "rx_center_hz":..., "ref_signal_hz":..., "target_signal_hz":...}
    """
    if config_path is None and geometry is None:
        raise ValueError("need config_path or geometry")
    if config_path is not None:
        cfg = load_config(config_path)
        ctx = {
            "node_xy": {k: np.asarray(v, float) for k, v in cfg["_node_xy"].items()},
            "ref_xy": np.asarray(cfg["_ref_xy"], float),
            "truth_xy": None if cfg["_truth_xy"] is None else np.asarray(cfg["_truth_xy"], float),
            "cal_matrix": cfg.get("_cal_matrix", {}) if "chromatic_cal_matrix" in cfg else {},
            "fs": int(cfg["sample_rate"]), "fc": float(cfg["rx_center_hz"]),
            "f_ref_hz": float(cfg["ref_signal_hz"]), "f_tgt_hz": float(cfg["target_signal_hz"]),
            "bw": float(cfg["band_hz"]), "margin": float(cfg.get("cfo_margin_hz", 25_000)),
            "origin": tuple(cfg["_origin"]),
            "source": f"config:{os.path.basename(config_path)}",
        }
    else:
        g = geometry
        node_xy = g.get("node_xy")
        if node_xy is None and "stations" in g:
            node_xy = g["stations"]
        ref_xy = g.get("ref_xy")
        if ref_xy is None and "pluto" in g:
            ref_xy = g["pluto"]["xy"] if isinstance(g["pluto"], dict) else g["pluto"]
        ctx = {
            "node_xy": {k: np.asarray(v, float) for k, v in node_xy.items()},
            "ref_xy": np.asarray(ref_xy, float),
            "truth_xy": None if g.get("truth_xy") is None else np.asarray(g["truth_xy"], float),
            "cal_matrix": g.get("cal_matrix") or {},
            "fs": int(g.get("sample_rate", 2_400_000)), "fc": float(g.get("rx_center_hz", 433_920_000)),
            "f_ref_hz": float(g.get("ref_signal_hz", 433_350_000)),
            "f_tgt_hz": float(g.get("target_signal_hz", 434_490_000)),
            "bw": float(g.get("band_hz", 600_000)), "margin": float(g.get("cfo_margin_hz", 50_000)),
            "origin": tuple(g["origin"]) if g.get("origin") else None,
            "source": "geometry_override",
        }
    ctx["f_ref"] = ctx["f_ref_hz"] - ctx["fc"]
    ctx["f_tgt"] = ctx["f_tgt_hz"] - ctx["fc"]
    return ctx


def _sha256(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


# ----------------------------------------------------------------------------
# Capture preparation (mirrors tdoa_process.prepare)
# ----------------------------------------------------------------------------
def prepare_capture(path, ctx, max_seconds=None):
    fs = ctx["fs"]
    max_samples = None if max_seconds is None else int(max_seconds * fs)
    raw = np.fromfile(path, dtype=np.uint8, count=-1 if max_samples is None else 2 * max_samples)
    clip_pct = float(100.0 * np.mean((raw == 0) | (raw == 255))) if raw.size else 0.0
    x = load_cu8(path, max_samples=max_samples)
    x = x - np.mean(x)
    rough = extract_band(x, fs, ctx["f_ref"], ctx["bw"] + 2 * ctx["margin"])
    cfo, cfo_snr = estimate_cfo_bpsk(rough, fs, max_cfo=ctx["margin"])
    x = mix(x, fs, cfo)
    ref = extract_band(x, fs, ctx["f_ref"], ctx["bw"])
    tgt = extract_band(x, fs, ctx["f_tgt"], ctx["bw"] + 2 * ctx["margin"])
    return {
        "ref": ref, "tgt": tgt,
        "info": {
            "n_samples": int(len(x)),
            "cfo_hz": float(cfo), "cfo_ppm": float(cfo / ctx["fc"] * 1e6), "cfo_tone_snr_db": float(cfo_snr),
            "ref_power_db": float(10 * np.log10(np.mean(np.abs(ref) ** 2) + 1e-20)),
            "target_power_db": float(10 * np.log10(np.mean(np.abs(tgt) ** 2) + 1e-20)),
            "clipping_pct": clip_pct,
        },
    }


# ----------------------------------------------------------------------------
# Per-pair processing with full logging
# ----------------------------------------------------------------------------
def _quality_scan(ri, ti, rj, tj, fs, seg_ms, d0, n_scan, mode):
    """Quality of reference and target correlation across n_scan evenly spaced segments."""
    n = int(seg_ms * 1e-3 * fs)
    lo = max(0, -int(d0)) + 1000
    hi = min(len(ri), len(rj) - int(d0)) - n - 1000
    if hi <= lo:
        return None
    starts = np.linspace(lo, hi, n_scan).astype(int)
    qr, qt, lag_now = [], [], d0
    for s in starts:
        dr, q_r, _ = adra_segment_lag(ri, rj, s, n, lag_now, 64, upsample=8, mode=mode, fs=fs)
        if dr is None:
            qr.append(0.0)
            qt.append(0.0)
            continue
        lag_now = dr
        _, q_t, _ = adra_segment_lag(ti, tj, s, n, dr, 48, upsample=8, mode=mode, fs=fs)
        qr.append(float(q_r))
        qt.append(float(q_t if q_t is not None else 0.0))
    qr, qt = np.array(qr), np.array(qt)
    return {"n_scan": int(n_scan),
            "ref_quality_median": float(np.median(qr)), "ref_quality_min": float(np.min(qr)),
            "target_quality_median": float(np.median(qt)), "target_quality_p10": float(np.percentile(qt, 10)),
            "target_quality_max": float(np.max(qt))}


def process_pair(base, n, data, ctx, algo="adra", seg_ms=10.0, nseg=60, min_quality=8.0,
                 apply_cal=True, bench=False, scan_segments=12):
    """Returns a pair record. Never raises for bad data; failures are recorded."""
    ri, ti = data[base]["ref"], data[base]["tgt"]
    rj, tj = data[n]["ref"], data[n]["tgt"]
    fs = ctx["fs"]
    rec = {"pair": f"{base}-{n}", "status": "FAILED", "reason": None}
    baseline = float(np.hypot(*(ctx["node_xy"][n] - ctx["node_xy"][base])))
    rec["baseline_m"] = baseline
    rec["min_quality_threshold"] = float(min_quality)

    d, sd, used, q0, tel = compute_pair_tdoa_adra(
        ri, ti, rj, tj, fs, seg_ms=seg_ms, nseg=nseg, min_quality=min_quality, mode=algo)
    rec["coarse_ref_quality"] = float(q0)
    rec["good_segments"] = int(used)

    # Diagnostics: where does the correlation break down? (reference vs target quality)
    try:
        n_s = int(seg_ms * 1e-3 * fs)
        search_coarse = min(int(0.20 * fs), len(ri) // 4)
        s0 = len(ri) // 2 - n_s // 2
        d0, q_c, _ = adra_segment_lag(ri, rj, s0, n_s * 4, 0, search_coarse, upsample=1, mode="standard", fs=fs)
        if d0 is not None:
            rec["coarse_ref_lag_samples"] = float(d0)
            rec["quality_scan"] = _quality_scan(ri, ti, rj, tj, fs, seg_ms, d0, scan_segments, algo)
    except Exception as e:  # diagnostics must never mask the real result
        rec["quality_scan_error"] = f"{type(e).__name__}: {e}"

    if d is None:
        if q0 < min_quality:
            rec["reason"] = "REFERENCE_COARSE_QUALITY_LOW"
        elif used == 0:
            rec["reason"] = "NO_GOOD_SEGMENTS (reference/target quality below threshold in every piece)"
        else:
            rec["reason"] = f"TOO_FEW_GOOD_SEGMENTS ({used} < 5)"
        return rec

    d_raw = float(d)
    cal_s = 0.0
    if apply_cal and ctx["cal_matrix"]:
        cal_s = float(compute_chromatic_group_delay_offset_s(base, n, ctx["cal_matrix"]))
    d_cal = d_raw - cal_s
    if bench:
        ref_geom = 0.0
    else:
        ref_geom = float((np.hypot(*(ctx["ref_xy"] - ctx["node_xy"][n]))
                          - np.hypot(*(ctx["ref_xy"] - ctx["node_xy"][base]))) / C)
    tdoa = d_cal + ref_geom
    rec.update({
        "d_raw_ns": d_raw * 1e9, "cal_offset_ns": cal_s * 1e9, "d_corrected_ns": d_cal * 1e9,
        "ref_geom_ns": ref_geom * 1e9, "tdoa_ns": tdoa * 1e9, "range_diff_m": float(C * tdoa),
        "spread_m": float(C * sd), "drift_ppm": float(tel.get("clock_drift_ppm", 0.0)),
        "inlier_ratio": float(tel.get("inlier_ratio", 0.0)), "mean_msc": float(tel.get("mean_msc", 0.0)),
    })
    if abs(C * tdoa) > baseline + 1.0:
        rec["status"] = "REJECTED"
        rec["reason"] = (f"BASELINE_VIOLATION (|range diff| {abs(C * tdoa):.2f} m exceeds "
                         f"baseline {baseline:.2f} m + 1 m margin; physically impossible)")
        return rec
    rec["status"] = "OK"
    return rec


# ----------------------------------------------------------------------------
# Slot replay
# ----------------------------------------------------------------------------
def replay_slot(captures_dir, slot, ctx=None, config_path=None, geometry=None, algo="adra", seg_ms=10.0,
                nseg=60, min_quality=8.0, max_seconds=None, apply_cal=True, bench=False,
                closure_limit_m=2.5, strict_closure=True, scan_segments=12):
    if ctx is None:
        ctx = build_context(config_path=config_path, geometry=geometry)
    nodes = list(ctx["node_xy"].keys())
    out = {
        "data_source": DATA_SOURCE, "slot": slot, "algo": algo, "status": "NO_FIX", "reason": None,
        "params": {"seg_ms": seg_ms, "nseg": nseg, "min_quality": min_quality, "max_seconds": max_seconds,
                   "apply_cal": apply_cal, "bench": bench, "closure_limit_m": closure_limit_m,
                   "strict_closure": strict_closure, "scan_segments": scan_segments},
        "geometry": {"source": ctx["source"],
                     "node_xy": {k: [float(v[0]), float(v[1])] for k, v in ctx["node_xy"].items()},
                     "ref_xy": [float(ctx["ref_xy"][0]), float(ctx["ref_xy"][1])],
                     "truth_xy": None if ctx["truth_xy"] is None else [float(ctx["truth_xy"][0]), float(ctx["truth_xy"][1])],
                     "cal_matrix_ns": {k: float(v.get("chromatic_delay_ns", 0.0)) for k, v in ctx["cal_matrix"].items()}},
        "files": {}, "nodes": {}, "pairs": [],
    }

    files = {n: os.path.join(captures_dir, f"{n}_{slot}.cu8") for n in nodes}
    missing = [n for n in nodes if not os.path.exists(files[n])]
    for n in nodes:
        if n not in missing:
            out["files"][n] = {"name": os.path.basename(files[n]), "bytes": os.path.getsize(files[n]),
                               "sha256": _sha256(files[n])}
    if missing:
        out["reason"] = f"MISSING_FILES: {', '.join(missing)}"
        return out
    empty = [n for n in nodes if out["files"][n]["bytes"] < 2 * int(0.2 * ctx["fs"])]
    if empty:
        out["reason"] = f"CAPTURE_TOO_SHORT: {', '.join(empty)} (< 0.2 s of samples)"
        return out

    data = {}
    for n in nodes:
        data[n] = prepare_capture(files[n], ctx, max_seconds=max_seconds)
        out["nodes"][n] = data[n]["info"]

    pair_dd, pair_w = {}, {}
    pairs = [(nodes[i], nodes[j]) for i in range(len(nodes)) for j in range(i + 1, len(nodes))]
    for base, n in pairs:
        rec = process_pair(base, n, data, ctx, algo=algo, seg_ms=seg_ms, nseg=nseg,
                           min_quality=min_quality, apply_cal=apply_cal, bench=bench,
                           scan_segments=scan_segments)
        out["pairs"].append(rec)
        if rec["status"] == "OK":
            pair_dd[(base, n)] = rec["range_diff_m"]
            pair_w[(base, n)] = 1.0 / max(rec["spread_m"], 0.5) ** 2

    if len(pair_dd) < 2:
        out["reason"] = f"TOO_FEW_PAIRS ({len(pair_dd)} usable, need >= 2)"
        return out
    if len(nodes) == 3 and len(pair_dd) < 3 and strict_closure:
        out["reason"] = f"STRICT_REJECTION: only {len(pair_dd)}/3 pairs usable"
        return out

    k12, k13, k23 = ("node1", "node2"), ("node1", "node3"), ("node2", "node3")
    if k12 in pair_dd and k13 in pair_dd and k23 in pair_dd:
        closure = pair_dd[k12] + pair_dd[k23] - pair_dd[k13]
        out["loop_closure_m"] = float(closure)
        out["loop_closure_status"] = "PASS" if abs(closure) <= closure_limit_m else "FAIL"
        if abs(closure) > closure_limit_m and strict_closure:
            out["reason"] = f"LOOP_CLOSURE_FAIL ({abs(closure):.2f} m > {closure_limit_m} m)"
            return out

    est, _, _ = solve_position(ctx["node_xy"], pair_dd, pair_w, mode=algo)
    resid = {f"{i}-{j}": float(model_dd(est, ctx["node_xy"][i], ctx["node_xy"][j]) - v)
             for (i, j), v in pair_dd.items()}
    out["pair_residuals_m"] = resid
    out["residual_rms_m"] = float(np.sqrt(np.mean(np.square(list(resid.values())))))
    out["position_xy"] = [float(est[0]), float(est[1])]
    if ctx["origin"] is not None:
        lat, lon = xy_to_latlon(est[0], est[1], ctx["origin"][0], ctx["origin"][1])
        out["position_latlon"] = [float(lat), float(lon)]
    if ctx["truth_xy"] is not None:
        out["error_vs_truth_m"] = float(np.hypot(*(est - ctx["truth_xy"])))
    out["status"] = "FIX"
    out["reason"] = None
    return out


def write_log(result, path):
    """Write the result with a generation timestamp (kept OUT of the deterministic result)."""
    doc = {"generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "result": result}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)
    return path


def format_report(result):
    """Human-readable summary of a replay record."""
    lines = [f"[{result['data_source']}] slot={result['slot']} algo={result['algo']} -> {result['status']}"
             + (f"  reason={result['reason']}" if result.get("reason") else "")]
    for n, info in result.get("nodes", {}).items():
        lines.append(f"  {n}: cfo {info['cfo_hz']:+.0f} Hz ({info['cfo_ppm']:+.1f} ppm) tone-SNR {info['cfo_tone_snr_db']:.1f} dB "
                     f"ref {info['ref_power_db']:.1f} dB tgt {info['target_power_db']:.1f} dB clip {info['clipping_pct']:.3f}%")
    for p in result.get("pairs", []):
        qs = p.get("quality_scan") or {}
        qtxt = (f" | tgt-quality med {qs['target_quality_median']:.1f} (p10 {qs['target_quality_p10']:.1f}), "
                f"ref med {qs['ref_quality_median']:.1f}") if qs else ""
        if p["status"] == "OK":
            lines.append(f"  pair {p['pair']}: OK range diff {p['range_diff_m']:+.2f} m (baseline {p['baseline_m']:.2f} m, "
                         f"+/-{p['spread_m']:.2f} m, {p['good_segments']} seg){qtxt}")
        else:
            lines.append(f"  pair {p['pair']}: {p['status']} {p['reason']}{qtxt}")
    if "loop_closure_m" in result:
        lines.append(f"  loop closure {result['loop_closure_m']:+.2f} m [{result['loop_closure_status']}]")
    if result["status"] == "FIX":
        lines.append(f"  FIX xy=({result['position_xy'][0]:+.3f}, {result['position_xy'][1]:+.3f}) m  "
                     f"residual rms {result['residual_rms_m']:.3f} m"
                     + (f"  error vs truth {result['error_vs_truth_m']:.3f} m" if "error_vs_truth_m" in result else ""))
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser(description="Deterministic offline replay of saved IQ captures (no hardware).")
    p.add_argument("--dir", default="captures")
    p.add_argument("--slot", required=True)
    p.add_argument("--config", required=True)
    p.add_argument("--algo", default="adra", choices=["adra", "gcc_phat", "standard"])
    p.add_argument("--nseg", type=int, default=60)
    p.add_argument("--max-seconds", type=float, default=None)
    p.add_argument("--min-quality", type=float, default=8.0)
    p.add_argument("--no-cal", action="store_true", help="skip chromatic calibration offsets")
    p.add_argument("--out", default=None, help="write JSON log to this path")
    a = p.parse_args()
    res = replay_slot(a.dir, a.slot, config_path=a.config, algo=a.algo, nseg=a.nseg,
                      min_quality=a.min_quality, max_seconds=a.max_seconds, apply_cal=not a.no_cal)
    print(format_report(res))
    if a.out:
        print("log:", write_log(res, a.out))
    return 0 if res["status"] == "FIX" else 2


if __name__ == "__main__":
    sys.exit(main())
