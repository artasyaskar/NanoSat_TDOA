#!/usr/bin/env python3
"""
simulate_wifi.py  -  Solution 2: fake recordings of the two ESP32 beacons (plus a
neighbour's Wi-Fi traffic) as seen by 2 bladeRFs and 1 Pluto. Test the software first:

    python3 simulate_wifi.py [--urban]
    python3 tdoa_wifi_process.py --config config_wifi_sim.json --dir captures_wifi_sim
"""
import argparse
import datetime
import json
import os
import sys

import numpy as np
from scipy import signal

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "common"))
from tdoa_lib import C, xy_to_latlon

p = argparse.ArgumentParser()
p.add_argument("--urban", action="store_true")
p.add_argument("--slots", type=int, default=3)
p.add_argument("--out", default="captures_wifi_sim")
p.add_argument("--seed", type=int, default=3)
a = p.parse_args()
rng = np.random.default_rng(a.seed)

FS, FC = 20e6, 2437e6
LAT0, LON0 = 33.0, 73.0
nodes = {"node1": (0.0, 0.0), "node2": (270.0, 30.0), "node3": (110.0, 240.0)}
kind_of = {"node1": "bladerf", "node2": "bladerf", "node3": "pluto"}
dur_of = {"bladerf": 0.40, "pluto": 0.21}
ppm_of = {"node1": 0.7, "node2": -1.1, "node3": 12.0}       # Pluto crystal is the worst
ref_xy, tgt_xy, other_xy = (-160.0, 280.0), (125.0, 95.0), (400.0, -150.0)
tx_ppm = {"ref": 6.0, "tgt": -9.0, "other": 3.0}
BARKER = np.array([1, -1, 1, 1, -1, 1, 1, 1, -1, -1, -1], float)


def dsss_packet(duration):
    """802.11b-style 1 Mbit/s packet: DBPSK bits spread by the 11-chip Barker code."""
    bits = rng.integers(0, 2, int(duration * 1e6))
    sym = np.cumprod(np.where(bits == 1, -1.0, 1.0))         # differential BPSK
    chips = np.kron(sym, BARKER)                              # 11 Mchip/s
    x22 = np.repeat(chips, 2)                                 # 22 MS/s
    return signal.resample_poly(x22, 10, 11).astype(complex)  # 20 MS/s, ~ +/-9 MHz wide


def frac_delay(x, frac, pad=64):
    y = np.r_[np.zeros(pad), x, np.zeros(pad)]
    f = np.fft.fftfreq(len(y))
    return np.fft.ifft(np.fft.fft(y) * np.exp(-2j * np.pi * f * frac))


def schedule(t_start, t_end):
    ev = []
    t = t_start
    while t < t_end:                                          # reference every ~10 ms
        ev.append(("ref", t + rng.uniform(0, 0.4e-3), 0.64e-3))
        ev.append(("tgt", t + 4.5e-3 + rng.uniform(0, 0.4e-3), 1.80e-3))
        if rng.random() < 0.5:                                # neighbour network traffic
            ev.append(("other", t + 7.5e-3 + rng.uniform(0, 0.3e-3), rng.uniform(0.15e-3, 2.2e-3)))
        t += 10e-3
    return ev


src_xy = {"ref": ref_xy, "tgt": tgt_xy, "other": other_xy}
os.makedirs(a.out, exist_ok=True)
for k in range(a.slots):
    slot_t = 30.0 * k
    slot = (datetime.datetime(2026, 10, 20, 10, 15) + datetime.timedelta(seconds=30 * k)).strftime("%Y%m%d_%H%M%S")
    events = schedule(slot_t - 0.1, slot_t + 0.6)
    waves = [(src, t, dsss_packet(d)) for src, t, d in events]
    for name, nxy in nodes.items():
        kind = kind_of[name]
        t0 = slot_t + (rng.uniform(0.0, 0.08) if kind == "pluto" else rng.uniform(-0.01, 0.02))
        fs_i = FS * (1 + ppm_of[name] * 1e-6)                 # this radio's real sample rate
        N = int(dur_of[kind] * FS)
        y = np.zeros(N, complex)
        for src, t_emit, w in waves:
            sx = src_xy[src]
            d = np.hypot(sx[0] - nxy[0], sx[1] - nxy[1])
            amp = 200.0 / d
            idx = (t_emit + d / C - t0) * fs_i
            i0 = int(np.floor(idx))
            if i0 < 100 or i0 + len(w) + 200 > N:
                continue
            paths = [(0.0, 1.0)]
            if a.urban:
                paths += [(rng.uniform(30e-9, 150e-9), 0.5 * np.exp(2j * np.pi * rng.random())),
                          (rng.uniform(150e-9, 400e-9), 0.25 * np.exp(2j * np.pi * rng.random()))]
            seg = sum(g * frac_delay(w, idx - i0 + ex * FS) for ex, g in paths)
            f_off = FC * (tx_ppm[src] - ppm_of[name]) * 1e-6
            tt = (i0 - 64 + np.arange(len(seg))) / fs_i + t0
            seg = amp * seg * np.exp(2j * np.pi * f_off * tt + 2j * np.pi * rng.random())
            y[i0 - 64:i0 - 64 + len(seg)] += seg
        y += 0.06 * (rng.standard_normal(N) + 1j * rng.standard_normal(N))
        y += 0.02                                             # DC offset
        if kind == "pluto":
            q = (np.clip(np.round(y.real * 300), -2047, 2047) + 1j * np.clip(np.round(y.imag * 300), -2047, 2047)) / 2048
            np.save(os.path.join(a.out, f"{name}_{slot}.npy"), q.astype(np.complex64))
        else:
            iq = np.empty(2 * N, np.int16)
            iq[0::2] = np.clip(np.round(y.real * 300), -2047, 2047)
            iq[1::2] = np.clip(np.round(y.imag * 300), -2047, 2047)
            iq.tofile(os.path.join(a.out, f"{name}_{slot}.sc16"))
    print("wrote slot", slot)


def ll(xy):
    lat, lon = xy_to_latlon(xy[0], xy[1], LAT0, LON0)
    return {"lat": round(lat, 7), "lon": round(lon, 7)}


cfg = {"sample_rate": FS, "rx_center_hz": FC, "ref_packet_us": 640, "tgt_packet_us": 1800, "packet_tolerance_us": 80,
       "nodes": {n: ll(xy) for n, xy in nodes.items()}, "reference": ll(ref_xy), "truth_target": ll(tgt_xy)}
for path in ("config_wifi_sim.json", os.path.join(a.out, "config_used.json")):
    with open(path, "w") as f:
        json.dump(cfg, f, indent=2)
print("wrote config_wifi_sim.json")
