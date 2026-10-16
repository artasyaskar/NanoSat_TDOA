"""
tdoa_lib.py  -  Shared functions for the low-cost TDOA demo kit.

Used by Solution 1 (RTL-SDR, PN signals) and Solution 2 (bladeRF/Pluto, Wi-Fi).
Every function is written to be read by a beginner: each one does ONE job.

Units used everywhere:
  - time in seconds, frequency in Hz, distance in metres
  - positions are local x (east) / y (north) in metres around an origin
"""
import json
import math
import numpy as np
from scipy import signal
from scipy.optimize import least_squares

C = 299_792_458.0  # speed of light (m/s)


# ----------------------------------------------------------------------------
# 1. Geography: convert GPS latitude/longitude to flat x/y metres and back.
#    Over a few kilometres the earth is flat enough for this simple formula.
# ----------------------------------------------------------------------------
def latlon_to_xy(lat, lon, lat0, lon0):
    """Return (x_east_m, y_north_m) of a point relative to origin (lat0, lon0)."""
    k_lat = 111_132.92 - 559.82 * math.cos(2 * math.radians(lat0))
    k_lon = 111_412.84 * math.cos(math.radians(lat0))
    return (lon - lon0) * k_lon, (lat - lat0) * k_lat


def xy_to_latlon(x, y, lat0, lon0):
    k_lat = 111_132.92 - 559.82 * math.cos(2 * math.radians(lat0))
    k_lon = 111_412.84 * math.cos(math.radians(lat0))
    return lat0 + y / k_lat, lon0 + x / k_lon


import os
import sys

_ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT_DIR not in sys.path:
    sys.path.insert(0, _ROOT_DIR)

from src.algorithms.adra_tdoa import (
    lorentzian_taylor_peak, adra_xcorr_lag, adra_segment_lag,
    compute_pair_tdoa_adra, compute_chromatic_group_delay_offset_s,
    DEFAULT_CHROMATIC_CAL_MATRIX
)
from src.algorithms.solvers import solve_adra_3stage


def load_config(path):
    """Read config.json and convert every surveyed point to local x/y metres."""
    with open(path) as f:
        cfg = json.load(f)
    pts = dict(cfg["nodes"])
    pts["_reference"] = cfg["reference"]
    if "truth_target" in cfg:
        pts["_truth"] = cfg["truth_target"]
    lat0 = np.mean([p["lat"] for p in cfg["nodes"].values()])
    lon0 = np.mean([p["lon"] for p in cfg["nodes"].values()])
    cfg["_origin"] = (lat0, lon0)
    xy = {name: np.array(latlon_to_xy(p["lat"], p["lon"], lat0, lon0)) for name, p in pts.items()}
    cfg["_node_xy"] = {n: xy[n] for n in cfg["nodes"]}
    cfg["_ref_xy"] = xy["_reference"]
    cfg["_truth_xy"] = xy.get("_truth")
    cfg["_cal_matrix"] = cfg.get("chromatic_cal_matrix", DEFAULT_CHROMATIC_CAL_MATRIX)
    return cfg


# ----------------------------------------------------------------------------
# 2. Reading recordings from disk.
# ----------------------------------------------------------------------------
def load_cu8(path, max_samples=None):
    """RTL-SDR files (rtl_sdr output): unsigned 8-bit I,Q,I,Q... -> complex."""
    raw = np.fromfile(path, dtype=np.uint8, count=-1 if max_samples is None else 2 * max_samples)
    raw = raw[: len(raw) // 2 * 2].astype(np.float32)
    raw = (raw - 127.4) / 128.0
    return (raw[0::2] + 1j * raw[1::2]).astype(np.complex64)


def load_sc16(path, scale=2048.0):
    """bladeRF files: signed 16-bit I,Q (SC16 Q11, full scale = 2048) -> complex."""
    raw = np.fromfile(path, dtype=np.int16).astype(np.float32) / scale
    raw = raw[: len(raw) // 2 * 2]
    return (raw[0::2] + 1j * raw[1::2]).astype(np.complex64)


# ----------------------------------------------------------------------------
# 3. Basic signal processing.
# ----------------------------------------------------------------------------
def mix(x, fs, f_shift, n0=0):
    """Move a signal down in frequency by f_shift Hz (so f_shift ends up at 0 Hz)."""
    n = np.arange(n0, n0 + len(x), dtype=np.float64)
    return (x * np.exp(-2j * np.pi * f_shift * n / fs)).astype(np.complex64)


def lowpass(x, fs, bandwidth, numtaps=255):
    """Keep only -bandwidth/2 ... +bandwidth/2 around 0 Hz (zero-delay FIR)."""
    taps = signal.firwin(numtaps, bandwidth / 2, fs=fs, window=("kaiser", 8.0))
    y = signal.fftconvolve(x, taps, mode="same")
    return y.astype(np.complex64)


def extract_band(x, fs, f_center, bandwidth):
    """Cut out one signal from a wider recording and bring it to 0 Hz."""
    return lowpass(mix(x, fs, f_center), fs, bandwidth)


def estimate_cfo_bpsk(x_bb, fs, max_cfo, nfft=1 << 20):
    """
    Frequency-offset estimate for a BPSK-type signal (our PN test signals, and
    802.11b Wi-Fi). Squaring a BPSK signal removes the +1/-1 data and leaves a
    pure tone at 2 x (frequency offset). We find that tone with an FFT.
    """
    y = x_bb[: min(len(x_bb), nfft)].astype(np.complex128) ** 2
    y = y * np.hanning(len(y))
    spec = np.abs(np.fft.fftshift(np.fft.fft(y, nfft)))
    freqs = np.fft.fftshift(np.fft.fftfreq(nfft, 1 / fs))
    keep = np.abs(freqs) <= 2 * max_cfo
    k = np.argmax(spec * keep)
    # parabolic refinement of the tone position
    if 0 < k < nfft - 1:
        a, b, c = spec[k - 1], spec[k], spec[k + 1]
        delta = 0.5 * (a - c) / (a - 2 * b + c) if (a - 2 * b + c) != 0 else 0.0
    else:
        delta = 0.0
    f2 = freqs[k] + delta * fs / nfft
    snr_db = 10 * np.log10(spec[k] / (np.median(spec[keep]) + 1e-12))
    return f2 / 2.0, snr_db


def parabolic_peak(mag, k):
    """Sub-sample peak position using hybrid 5-point Taylor & Log-Parabolic refiner."""
    if k <= 0 or k >= len(mag) - 1:
        return float(k)
    kf, _ = lorentzian_taylor_peak(mag, k, early_gate=False)
    return float(kf)


def xcorr_lag(a, b, lag_center, lag_halfwidth, upsample=8, mode="adra", fs=2_400_000):
    """
    Find D (in samples, fractional) such that  b[n] ~= a[n - D].
    Supports mode="adra" (Coherence-Weighted GCC + CLEAN Early-Gate + Taylor-Lorentzian),
    mode="gcc_phat", and mode="standard".
    """
    lag, quality, _ = adra_xcorr_lag(
        a, b, lag_center, lag_halfwidth, upsample=upsample, mode=mode, fs=fs
    )
    return float(lag), float(quality)


def segment_lag(x_i, x_j, start, n, lag_center, lag_halfwidth, upsample=8, mode="adra", fs=2_400_000):
    """Cut matching pieces out of two recordings and measure their lag D."""
    lag, q, _ = adra_segment_lag(
        x_i, x_j, start, n, lag_center, lag_halfwidth, upsample=upsample, mode=mode, fs=fs
    )
    return lag, q


# ----------------------------------------------------------------------------
# 4. Position solver.
#    Input: for each node pair (i, j) the measured range difference
#           dd_ij = |P - node_j| - |P - node_i|   (metres)
#    Output: the point P that best agrees with all of them.
# ----------------------------------------------------------------------------
def model_dd(p, xi, xj):
    return np.hypot(*(p - xj)) - np.hypot(*(p - xi))


def solve_position(node_xy, pair_dd, pair_w=None, search_radius=1500.0, grid_step=5.0, mode="adra"):
    """
    Three-stage ADRA-TDOA solver (or 2-stage baseline when mode="standard"):
      (1) Coarse global grid + Fang/Chan-Ho closed-form initialization
      (2) Huber-IRLS outlier-resistant iterative refinement
      (3) Bounded Levenberg-Marquardt polish.
    Returns (best_xy, cost_map, (gx, gy)).
    """
    pairs = list(pair_dd.keys())
    w = np.ones(len(pairs)) if pair_w is None else np.array([pair_w[p] for p in pairs])
    nodes_arr = np.array(list(node_xy.values()))
    span = float(np.max(np.ptp(nodes_arr, axis=0))) if len(nodes_arr) > 1 else 100.0
    if span < 100.0:
        search_radius = max(span * 3.0, 30.0)
        grid_step = max(search_radius / 300.0, 0.1)

    centre = np.mean(nodes_arr, axis=0)
    gx = np.arange(centre[0] - search_radius, centre[0] + search_radius, grid_step)
    gy = np.arange(centre[1] - search_radius, centre[1] + search_radius, grid_step)
    X, Y = np.meshgrid(gx, gy)
    cost = np.zeros_like(X)
    for wk, (i, j) in zip(w, pairs):
        xi, xj = node_xy[i], node_xy[j]
        model = np.hypot(X - xj[0], Y - xj[1]) - np.hypot(X - xi[0], Y - xi[1])
        cost += wk * (model - pair_dd[(i, j)]) ** 2
    r, c = np.unravel_index(np.argmin(cost), cost.shape)
    p0 = np.array([gx[c], gy[r]])

    if mode == "adra":
        res3 = solve_adra_3stage(node_xy, pair_dd, pair_w=pair_w, search_radius=search_radius, grid_step=grid_step)
        p0 = np.array(res3["position"], dtype=float)

    def resid(p):
        return [np.sqrt(wk) * (model_dd(p, node_xy[i], node_xy[j]) - pair_dd[(i, j)])
                for wk, (i, j) in zip(w, pairs)]

    lo_b = [gx[0], gy[0]]
    hi_b = [gx[-1], gy[-1]]
    p0 = np.clip(p0, lo_b, hi_b)
    sol = least_squares(resid, p0, bounds=(lo_b, hi_b), loss="huber" if mode == "adra" else "linear")
    return sol.x, cost, (gx, gy)



def plot_solution(node_xy, ref_xy, est_xy, truth_xy, pair_dd, cost, grid, out_png, title=""):
    """Draw the map: nodes, reference, hyperbolas, estimate and (if known) truth."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    gx, gy = grid
    X, Y = np.meshgrid(gx, gy)
    fig, ax = plt.subplots(figsize=(7.5, 7.0), dpi=150)
    ax.imshow(np.log10(cost + 1.0), origin="lower", extent=[gx[0], gx[-1], gy[0], gy[-1]],
              cmap="Blues_r", alpha=0.55)
    colors = ["#d62728", "#2ca02c", "#9467bd", "#ff7f0e", "#8c564b", "#e377c2"]
    for k, (i, j) in enumerate(pair_dd):
        xi, xj = node_xy[i], node_xy[j]
        model = np.hypot(X - xj[0], Y - xj[1]) - np.hypot(X - xi[0], Y - xi[1])
        ax.contour(X, Y, model - pair_dd[(i, j)], levels=[0], colors=[colors[k % 6]], linewidths=1.6)
        ax.plot([], [], color=colors[k % 6], label=f"hyperbola {i}-{j}")
    for n, p in node_xy.items():
        ax.plot(*p, "k^", ms=11)
        ax.annotate(n, p, xytext=(6, 6), textcoords="offset points", fontsize=10, weight="bold")
    ax.plot(*ref_xy, "*", color="#1f77b4", ms=16, mec="k", label="reference transmitter")
    if truth_xy is not None:
        ax.plot(*truth_xy, "o", mfc="none", mec="k", ms=16, mew=2, label="true target")
    ax.plot(*est_xy, "x", color="#d62728", ms=14, mew=3, label="estimated target")
    allp = np.array(list(node_xy.values()) + [ref_xy, est_xy] + ([truth_xy] if truth_xy is not None else []))
    pad = max(float(np.max(np.ptp(allp, axis=0))) * 0.35, 3.0)
    ax.set_xlim(allp[:, 0].min() - pad, allp[:, 0].max() + pad)
    ax.set_ylim(allp[:, 1].min() - pad, allp[:, 1].max() + pad)
    ax.set_aspect("equal")
    ax.set_xlabel("East (m)")
    ax.set_ylabel("North (m)")
    ax.grid(alpha=0.3)
    ax.legend(loc="lower right", fontsize=8, framealpha=0.9)
    ax.set_title(title, fontsize=11)
    fig.tight_layout()
    fig.savefig(out_png)
    plt.close(fig)
