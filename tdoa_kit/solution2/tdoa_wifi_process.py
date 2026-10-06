#!/usr/bin/env python3
"""
tdoa_wifi_process.py  -  Solution 2: positions from Wi-Fi packets. Runs on the laptop.

    python3 tdoa_wifi_process.py --config config_wifi.json --dir captures
    python3 tdoa_wifi_process.py --config config_wifi.json --dir captures --slot 20261020_101500

Steps for every capture time:
  1. load one recording per node (bladeRF .sc16 or Pluto .npy)
  2. find every Wi-Fi packet ("burst") and measure how long it lasts
       about 640 us long   -> REFERENCE packet
       about 1800 us long  -> TARGET packet         anything else (neighbours' Wi-Fi) is ignored
  3. remove each radio's frequency error (squaring trick on the reference packets)
  4. line up the recordings roughly by matching the on/off pattern of the packets
  5. for every packet seen by both nodes of a pair, measure the exact lag (sub-sample)
  6. reference packets: fit a straight line  lag(t) = a + b*t
       a = start-time difference, b = the two radios' clock-speed difference (drift)
  7. target packets: subtract that line at the target packet's time -> drift-free TDOA
  8. add the reference geometry, solve for position, draw the map
"""
import argparse
import csv
import glob
import os
import sys

import numpy as np
from scipy import signal

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "common"))
from tdoa_lib import (C, load_config, load_sc16, mix, xcorr_lag,
                      solve_position, plot_solution, xy_to_latlon, model_dd)

p = argparse.ArgumentParser()
p.add_argument("--config", default="config_wifi.json")
p.add_argument("--dir", default="captures")
p.add_argument("--slot", default=None)
a = p.parse_args()

cfg = load_config(a.config)
fs = cfg["sample_rate"]
fc = cfg["rx_center_hz"]
REF_US = cfg.get("ref_packet_us", 640)       # length of a reference packet (microseconds)
TGT_US = cfg.get("tgt_packet_us", 1800)      # length of a target packet
TOL_US = cfg.get("packet_tolerance_us", 80)  # accepted difference
MIN_LEN = 250e-6
nodes = list(cfg["nodes"].keys())
node_xy, ref_xy, truth_xy = cfg["_node_xy"], cfg["_ref_xy"], cfg["_truth_xy"]
lat0, lon0 = cfg["_origin"]


def load_any(path):
    if path.endswith(".npy"):
        return np.load(path).astype(np.complex64)
    return load_sc16(path)


def find_bursts(x):
    """Return list of (start, end, kind) for every packet found in x."""
    pw = np.abs(x) ** 2
    win = int(10e-6 * fs)                                    # 10 microsecond smoothing
    env = signal.fftconvolve(pw, np.ones(win) / win, mode="same")
    floor = np.median(env)
    on = env > 10 * floor                                    # 10 dB above the noise
    edges = np.flatnonzero(np.diff(on.astype(np.int8)))
    if on[0]:
        edges = np.r_[0, edges]
    if on[-1]:
        edges = np.r_[edges, len(on) - 1]
    bursts, all_us = [], []
    for s, e in zip(edges[0::2], edges[1::2]):
        dur = (e - s) / fs
        all_us.append(int(round(dur * 1e5)) * 10)            # every packet, to 10 us
        if dur < MIN_LEN or s < 200 or e > len(x) - 200:    # too short or cut at the edge
            continue
        us = dur * 1e6
        kind = "ref" if abs(us - REF_US) < TOL_US else ("tgt" if abs(us - TGT_US) < TOL_US else None)
        if kind:
            bursts.append((int(s), int(e), kind))
    return bursts, on, all_us


def remove_cfo(x, bursts, nfft=1 << 16, max_cfo=120e3):
    """
    Measure the frequency error on the reference packets and remove it.
    Each packet is squared (removes the +1/-1 data, leaves a tone at 2 x error);
    the spectra of all reference packets are added up and the tone is located.
    """
    acc = np.zeros(nfft)
    for s, e, k in bursts:
        if k == "ref":
            seg = x[s:e].astype(np.complex128) ** 2
            sp = np.abs(np.fft.fftshift(np.fft.fft(seg * np.hanning(len(seg)), nfft))) ** 2
            acc += sp / sp.sum()                     # every packet gets one equal "vote"
    freqs = np.fft.fftshift(np.fft.fftfreq(nfft, 1 / fs))
    acc[np.abs(freqs) > 2 * max_cfo] = 0
    k = int(np.argmax(acc))
    a_, b_, c_ = np.log(acc[k - 1:k + 2] + 1e-30)
    delta = 0.5 * (a_ - c_) / (a_ - 2 * b_ + c_)
    cfo = (freqs[k] + delta * fs / nfft) / 2
    snr = 10 * np.log10(acc[k] / (np.median(acc[acc > 0]) + 1e-30))
    return mix(x, fs, cfo), cfo, snr


def coarse_offset(on_i, on_j, max_s=0.30):
    """Rough lag (samples) of node j vs node i from the packets' on/off pattern."""
    dec = int(5e-6 * fs)
    ei = on_i[: len(on_i) // dec * dec].reshape(-1, dec).mean(1) - 0.5
    ej = on_j[: len(on_j) // dec * dec].reshape(-1, dec).mean(1) - 0.5
    z = signal.correlate(ej, ei, mode="full", method="fft")
    lags = np.arange(-len(ei) + 1, len(ej))
    keep = np.abs(lags) * dec <= max_s * fs
    return int(lags[keep][np.argmax(z[keep])] * dec)


def burst_lag(xi, xj, s, e, centre, half=300, fsearch=(-600, 600, 100)):
    """Exact lag of one packet between two recordings, small frequency search included."""
    seg_i = xi[s - 50:e + 50]
    c = int(round(centre))
    b0, b1 = s - 50 + c - half, e + 50 + c + half
    if b0 < 0 or b1 > len(xj):
        return None, 0
    seg_j = xj[b0:b1]
    best = (None, 0)
    for f in np.arange(*fsearch):
        D, q = xcorr_lag(seg_i, mix(seg_j, fs, f), 0, half, upsample=8)
        if q > best[1]:
            best = (D + c, q)
    return best


def pair_tdoa(Xi, Xj):
    (xi, bi, oi), (xj, bj, oj) = Xi, Xj
    D0 = coarse_offset(oi, oj)
    starts_j = np.array([s for s, _, _ in bj])
    kinds_j = [k for _, _, k in bj]
    meas = {"ref": [], "tgt": []}
    for s, e, k in bi:
        if len(starts_j) == 0:
            break
        m = int(np.argmin(np.abs(starts_j - (s + D0))))
        if abs(starts_j[m] - (s + D0)) > int(30e-6 * fs) + 200 or kinds_j[m] != k:
            continue                                         # not the same packet
        D, q = burst_lag(xi, xj, s, e, starts_j[m] - s)
        if D is not None and q > 6:
            meas[k].append((s / fs, D))
    r, t = np.array(meas["ref"]), np.array(meas["tgt"])
    if len(r) < 4 or len(t) < 2:
        return None, None, (len(r), len(t))
    # straight-line fit to the reference lags, twice, dropping outliers the second time
    coef = np.polyfit(r[:, 0], r[:, 1], 1)
    res = r[:, 1] - np.polyval(coef, r[:, 0])
    good = np.abs(res) < 3 * 1.4826 * np.median(np.abs(res)) + 0.05
    coef = np.polyfit(r[good, 0], r[good, 1], 1)
    d = t[:, 1] - np.polyval(coef, t[:, 0])                 # drift-free target-minus-reference
    med = np.median(d)
    spread = 1.4826 * np.median(np.abs(d - med)) / np.sqrt(len(d))
    return med / fs, spread / fs, (int(good.sum()), len(t), coef[0] / fs * 1e6)


def process_slot(slot):
    files = {}
    for n in nodes:
        f = sorted(glob.glob(os.path.join(a.dir, f"{n}_{slot}.*")))
        f = [x for x in f if x.endswith((".sc16", ".npy"))]
        if f:
            files[n] = f[0]
    if len(files) < 3:
        print(f"[{slot}] only {len(files)} node files - need 3")
        return None
    print(f"\n=== Capture {slot}: nodes {', '.join(files)} ===")
    data = {}
    for n, f in files.items():
        x = load_any(f)
        x = x - np.mean(x)
        bursts, on, all_us = find_bursts(x)
        vals, cnt = np.unique(all_us, return_counts=True)
        top = sorted(zip(cnt, vals), reverse=True)[:4]
        print(f"  {n}: most common packet lengths (us): " + ", ".join(f"{v} x{c}" for c, v in top))
        x, cfo, snr = remove_cfo(x, bursts)
        nr = sum(1 for b in bursts if b[2] == "ref")
        nt = sum(1 for b in bursts if b[2] == "tgt")
        print(f"  {n}: {len(x) / fs * 1000:.0f} ms recorded, {nr} reference + {nt} target packets, "
              f"frequency error {cfo:+.0f} Hz ({cfo / fc * 1e6:+.2f} ppm)")
        data[n] = (x, bursts, on)
    have = list(data)
    pair_dd, pair_w = {}, {}
    for i in range(len(have)):
        for j in range(i + 1, len(have)):
            ni, nj = have[i], have[j]
            d, sd, info = pair_tdoa(data[ni], data[nj])
            if d is None:
                print(f"  pair {ni}-{nj}: FAILED (matched ref/target packets: {info})")
                continue
            ref_geom = (np.hypot(*(ref_xy - node_xy[nj])) - np.hypot(*(ref_xy - node_xy[ni]))) / C
            tdoa = d + ref_geom
            pair_dd[(ni, nj)] = C * tdoa
            sd_m = max(C * sd, 0.3)
            pair_w[(ni, nj)] = 1 / sd_m ** 2
            print(f"  pair {ni}-{nj}: {info[0]} ref + {info[1]} target packets, clock drift "
                  f"{info[2]:+.2f} ppm -> range difference {C * tdoa:8.1f} m (+/-{sd_m:.1f} m)")
    if len(pair_dd) < 2:
        print("  Not enough good pairs.")
        return None
    est, cost, grid = solve_position(node_xy, pair_dd, pair_w)
    lat, lon = xy_to_latlon(est[0], est[1], lat0, lon0)
    print(f"  ESTIMATED TARGET: {lat:.6f}, {lon:.6f}   https://maps.google.com/?q={lat:.6f},{lon:.6f}")
    err = None
    if truth_xy is not None:
        err = float(np.hypot(*(est - truth_xy)))
        print(f"  Error vs. surveyed true target position: {err:.1f} m")
    png = os.path.join(a.dir, f"result_{slot}.png")
    plot_solution(node_xy, ref_xy, est, truth_xy, pair_dd, cost, grid, png,
                  f"Wi-Fi TDOA fix {slot}" + (f"   (error {err:.1f} m)" if err is not None else ""))
    print(f"  Map saved: {png}")
    return slot, lat, lon, err


all_files = glob.glob(os.path.join(a.dir, "*_*.sc16")) + glob.glob(os.path.join(a.dir, "*_*.npy"))
slots = [a.slot] if a.slot else sorted({os.path.basename(f).split("_", 1)[1].rsplit(".", 1)[0] for f in all_files})
results = [r for r in (process_slot(s) for s in slots) if r]
if results:
    with open(os.path.join(a.dir, "results.csv"), "a", newline="") as f:
        csv.writer(f).writerows(results)
    errs = [r[3] for r in results if r[3] is not None]
    if errs:
        print(f"\nSUMMARY over {len(errs)} captures: median error {np.median(errs):.1f} m, worst {max(errs):.1f} m")
