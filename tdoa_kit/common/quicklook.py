#!/usr/bin/env python3
"""
quicklook.py  -  check ONE recording before trusting it (works for both solutions).

    python3 quicklook.py captures/node1_20261020_101500.cu8                 (RTL-SDR, Solution 1)
    python3 quicklook.py captures/node1_20261020_101500.sc16 --rate 20e6 --center 2437e6
    python3 quicklook.py captures/node3_20261020_101500.npy  --rate 20e6 --center 2437e6

Prints: how much of the recording is clipped (too much gain), the noise floor and
the strongest peaks; saves  <file>_quicklook.png  with the spectrum and the signal level.
"""
import argparse
import os
import sys

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tdoa_lib import load_cu8, load_sc16

p = argparse.ArgumentParser()
p.add_argument("file")
p.add_argument("--rate", type=float, default=2.4e6)
p.add_argument("--center", type=float, default=433.92e6)
a = p.parse_args()

if a.file.endswith(".cu8"):
    raw = np.fromfile(a.file, dtype=np.uint8)
    clip = np.mean((raw == 0) | (raw == 255)) * 100
    x = load_cu8(a.file)
elif a.file.endswith(".sc16"):
    raw = np.fromfile(a.file, dtype=np.int16)
    clip = np.mean(np.abs(raw) >= 2040) * 100
    x = load_sc16(a.file)
else:
    x = np.load(a.file)
    clip = np.mean((np.abs(x.real) >= 0.995) | (np.abs(x.imag) >= 0.995)) * 100
x = x - np.mean(x)
nf = 4096
seg = x[: len(x) // nf * nf].reshape(-1, nf)
P = np.mean(np.abs(np.fft.fftshift(np.fft.fft(seg * np.hanning(nf), axis=1), axes=1)) ** 2, axis=0)
PdB = 10 * np.log10(P + 1e-20)
f = (a.center + np.fft.fftshift(np.fft.fftfreq(nf, 1 / a.rate))) / 1e6
floor = np.median(PdB)
print(f"File: {a.file}")
print(f"  length          : {len(x) / a.rate:.3f} s ({len(x)} samples)")
print(f"  clipped samples : {clip:.3f} %   {'OK' if clip < 0.1 else 'TOO HIGH - reduce gain'}")
print(f"  noise floor     : {floor:.1f} dB (relative)")
print(f"  strongest level : {PdB.max() - floor:.1f} dB above the noise at {f[np.argmax(PdB)]:.3f} MHz")

fig, axs = plt.subplots(2, 1, figsize=(9, 6))
axs[0].plot(f, PdB - floor, lw=0.8)
axs[0].set_xlabel("MHz")
axs[0].set_ylabel("dB above noise")
axs[0].set_title(os.path.basename(a.file) + "  - spectrum")
axs[0].grid(alpha=0.3)
dec = max(1, int(a.rate * 1e-4))
env = np.abs(x[: len(x) // dec * dec]).reshape(-1, dec).max(1)
axs[1].plot(np.arange(len(env)) * dec / a.rate * 1e3, env, lw=0.6)
axs[1].set_xlabel("time (ms)")
axs[1].set_ylabel("level")
axs[1].set_title("signal level over time")
fig.tight_layout()
out = a.file.rsplit(".", 1)[0] + "_quicklook.png"
fig.savefig(out, dpi=120)
print(f"  picture saved   : {out}")
