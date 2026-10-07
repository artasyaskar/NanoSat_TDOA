#!/usr/bin/env python3
"""
simulate_captures.py  -  Solution 1: create FAKE recordings that look exactly like the
real RTL-SDR files, so you can test the whole software chain at your desk first.

    python3 simulate_captures.py                 # line-of-sight site
    python3 simulate_captures.py --urban         # adds building echoes (multipath)
    python3 tdoa_process.py --config config_sim.json --dir captures_sim

The fake recordings include everything that goes wrong in real life:
  * each Pi starts recording at a slightly different time (up to tens of ms)
  * each dongle and each transmitter has its own frequency error (ppm)
  * transmitter carrier-leakage spikes, DC offset, receiver noise, 8-bit rounding
"""
import argparse
import datetime
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "common"))
from tdoa_lib import C, xy_to_latlon
from make_waveforms import make_pn_waveform, FS_TX, REF_DIGITAL_OFFSET, TGT_DIGITAL_OFFSET

p = argparse.ArgumentParser()
p.add_argument("--urban", action="store_true", help="add multipath echoes")
p.add_argument("--slots", type=int, default=3)
p.add_argument("--snr_db", type=float, default=12.0, help="signal-to-noise at 200 m, per sample")
p.add_argument("--out", default="captures_sim")
p.add_argument("--seed", type=int, default=1)
p.add_argument("--four", action="store_true", help="use 4 receiver nodes")
a = p.parse_args()
rng = np.random.default_rng(a.seed)

FS = 2_400_000
DUR = 2.0
FC = 433_920_000
LO_REF, LO_TGT = 433_750_000, 434_090_000          # transmitter centre frequencies
LAT0, LON0 = 33.000000, 73.000000                  # example origin - replace in real config

# Site layout in metres (east, north). Triangle sides ~230-280 m.
nodes = {"node1": (0.0, 0.0), "node2": (270.0, 30.0), "node3": (110.0, 240.0)}
if a.four:
    nodes["node4"] = (300.0, 250.0)
ref_xy = (-160.0, 280.0)                            # reference transmitter (surveyed)
tgt_xy = (125.0, 95.0)                              # true target (unknown to the software)


def periodic_resample(x, n_new):
    """Exact resampling of a periodic signal by FFT zero-padding."""
    X = np.fft.fft(x)
    n = len(x)
    Y = np.zeros(n_new, dtype=complex)
    h = n // 2
    Y[:h] = X[:h]
    Y[-h:] = X[-h:]
    return np.fft.ifft(Y) * (n_new / n)


ref_tx = make_pn_waveform(seed=11, digital_offset=REF_DIGITAL_OFFSET)
tgt_tx = make_pn_waveform(seed=22, digital_offset=TGT_DIGITAL_OFFSET)
Q = int(len(ref_tx) * FS / FS_TX)
S_ref = np.fft.fft(periodic_resample(ref_tx, Q))
S_tgt = np.fft.fft(periodic_resample(tgt_tx, Q))
f_axis = np.fft.fftfreq(Q, 1 / FS)
N = int(DUR * FS)

err_ref_tx = 9e-6 * LO_REF        # Pluto 9 ppm off
err_tgt_tx = -2e-6 * LO_TGT       # bladeRF -2 ppm off
rx_ppm = {n: rng.uniform(-1.5, 1.5) for n in nodes}     # TCXO dongles, +/-1.5 ppm


def receive(S, tx_lo, tx_err, src_xy, node_xy, t0, rx_err, amp_ref):
    d = np.hypot(src_xy[0] - node_xy[0], src_xy[1] - node_xy[1])
    amp = amp_ref * 200.0 / d
    a_shift = t0 - d / C                                   # sample n <-> emitted time t0 + n/FS - d/C
    paths = [(0.0, 1.0)]
    if a.urban:                                            # two building echoes
        paths += [(rng.uniform(50e-9, 250e-9), 0.5 * np.exp(2j * np.pi * rng.random())),
                  (rng.uniform(200e-9, 600e-9), 0.25 * np.exp(2j * np.pi * rng.random()))]
    spec = sum(g * S * np.exp(2j * np.pi * f_axis * (a_shift - extra)) for extra, g in paths)
    y = np.resize(np.fft.ifft(spec), N)
    f_off = (tx_lo + tx_err) - (FC + rx_err)              # where it lands in the recording
    n = np.arange(N)
    tone = 0.02 * np.exp(2j * np.pi * f_off * (t0 + n / FS))      # carrier leakage
    return amp * (y * np.exp(2j * np.pi * f_off * (t0 + n / FS)) + tone)


os.makedirs(a.out, exist_ok=True)
sig_rms = np.sqrt(np.mean(np.abs(ref_tx) ** 2))
noise_sigma = sig_rms / (10 ** (a.snr_db / 20))
for k in range(a.slots):
    slot_time = 30.0 * k     # seconds since session start (kept small: float precision!)
    slot = (datetime.datetime(2026, 10, 20, 10, 15, 0) + datetime.timedelta(seconds=30 * k)).strftime("%Y%m%d_%H%M%S")
    for name, nxy in nodes.items():
        t0 = slot_time + rng.uniform(-0.010, 0.040)       # this Pi starts a bit early/late
        rx_err = rx_ppm[name] * 1e-6 * FC
        y = receive(S_ref, LO_REF, err_ref_tx, ref_xy, nxy, t0, rx_err, 1.0)
        y += receive(S_tgt, LO_TGT, err_tgt_tx, tgt_xy, nxy, t0, rx_err, 1.0)
        y += noise_sigma * (rng.standard_normal(N) + 1j * rng.standard_normal(N)) / np.sqrt(2)
        y += 0.01 + 0.008j                                 # DC offset
        scale = 30.0 / np.sqrt(np.mean(np.abs(y) ** 2))    # gain setting -> ~30 counts rms
        iq = np.empty(2 * N, dtype=np.float32)
        iq[0::2], iq[1::2] = y.real * scale + 127.4, y.imag * scale + 127.4
        np.clip(np.round(iq), 0, 255).astype(np.uint8).tofile(os.path.join(a.out, f"{name}_{slot}.cu8"))
    print("wrote slot", slot)


def ll(xy):
    lat, lon = xy_to_latlon(xy[0], xy[1], LAT0, LON0)
    return {"lat": round(lat, 7), "lon": round(lon, 7)}


# The solver re-centres on the node average, so we store real lat/lon like a real survey.
cfg = {
    "sample_rate": FS, "rx_center_hz": FC,
    "ref_signal_hz": LO_REF + REF_DIGITAL_OFFSET, "target_signal_hz": LO_TGT + TGT_DIGITAL_OFFSET,
    "band_hz": 600_000, "cfo_margin_hz": 25_000,
    "nodes": {n: ll(xy) for n, xy in nodes.items()},
    "reference": ll(ref_xy), "truth_target": ll(tgt_xy),
}
for path in ("config_sim.json", os.path.join(a.out, "config_used.json")):
    with open(path, "w") as f:
        json.dump(cfg, f, indent=2)
print("wrote config_sim.json (copy kept in the capture folder)")
