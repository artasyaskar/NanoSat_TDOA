"""
core.py - Core Signal Processing, Cross-Correlation, and Solver Library for NanoSat_TDOA.

Modular implementation of the TDOA pipeline supporting:
  1. CFO estimation & derotation via non-linear BPSK squaring.
  2. FIR extraction of reference (-570 kHz) and target (+570 kHz) bands.
  3. Segmented cross-correlation with 8x sinc upsampling and parabolic peak refinement.
  4. Dual-transmitter delay cancellation with surveyed reference geometry correction.
  5. 2D grid search and non-linear least squares hyperbolic solver.
"""

import json
import math
import numpy as np
from scipy import signal
from scipy.optimize import least_squares

C_LIGHT = 299_792_458.0  # Speed of light in vacuum (m/s)


# ----------------------------------------------------------------------------
# 1. Geographic Coordinate Projections (WGS84 Local Tangent Plane)
# ----------------------------------------------------------------------------
def latlon_to_xy(lat, lon, lat0, lon0):
    """Convert (latitude, longitude) to local meters (x_east, y_north) relative to (lat0, lon0)."""
    k_lat = 111_132.92 - 559.82 * math.cos(2.0 * math.radians(lat0))
    k_lon = 111_412.84 * math.cos(math.radians(lat0))
    x = (lon - lon0) * k_lon
    y = (lat - lat0) * k_lat
    return x, y


def xy_to_latlon(x, y, lat0, lon0):
    """Convert local meters (x_east, y_north) back to (latitude, longitude)."""
    k_lat = 111_132.92 - 559.82 * math.cos(2.0 * math.radians(lat0))
    k_lon = 111_412.84 * math.cos(math.radians(lat0))
    lat = lat0 + y / k_lat
    lon = lon0 + x / k_lon
    return lat, lon


def load_site_config(path):
    """Load JSON site geometry and convert surveyed station coordinates to local coordinates."""
    with open(path) as f:
        cfg = json.load(f)
    pts = dict(cfg["nodes"])
    pts["_reference"] = cfg["reference"]
    if "truth_target" in cfg:
        pts["_truth"] = cfg["truth_target"]

    lat0 = float(np.mean([p["lat"] for p in cfg["nodes"].values()]))
    lon0 = float(np.mean([p["lon"] for p in cfg["nodes"].values()]))
    cfg["_origin"] = (lat0, lon0)

    xy = {name: np.array(latlon_to_xy(p["lat"], p["lon"], lat0, lon0)) for name, p in pts.items()}
    cfg["_node_xy"] = {n: xy[n] for n in cfg["nodes"]}
    cfg["_ref_xy"] = xy["_reference"]
    cfg["_truth_xy"] = xy.get("_truth")
    return cfg


# ----------------------------------------------------------------------------
# 2. Raw Sample I/O
# ----------------------------------------------------------------------------
def load_cu8(path, max_samples=None):
    """Load RTL-SDR raw unsigned 8-bit IQ bytes and convert to normalized complex64."""
    count = -1 if max_samples is None else 2 * max_samples
    raw = np.fromfile(path, dtype=np.uint8, count=count)
    raw = raw[: len(raw) // 2 * 2].astype(np.float32)
    raw = (raw - 127.4) / 128.0
    return (raw[0::2] + 1j * raw[1::2]).astype(np.complex64)


# ----------------------------------------------------------------------------
# 3. Baseband Signal Processing & Filtering
# ----------------------------------------------------------------------------
def mix(x, fs, f_shift, n0=0):
    """Shift frequency down by f_shift Hz (complex digital downconverter)."""
    n = np.arange(n0, n0 + len(x), dtype=np.float64)
    phasor = np.exp(-2j * np.pi * f_shift * n / fs)
    return (x * phasor).astype(np.complex64)


def lowpass_fir(x, fs, cutoff_hz, numtaps=255):
    """Kaiser-windowed zero-delay lowpass FIR filter."""
    taps = signal.firwin(numtaps, cutoff_hz, fs=fs, window=("kaiser", 8.0))
    return signal.fftconvolve(x, taps, mode="same").astype(np.complex64)


def extract_band(x, fs, f_center, bandwidth, numtaps=255):
    """Downconvert selected band to 0 Hz and apply lowpass filter."""
    return lowpass_fir(mix(x, fs, f_center), fs, bandwidth / 2.0, numtaps=numtaps)


def estimate_cfo_bpsk(x_bb, fs, max_cfo, nfft=1 << 20):
    """
    Estimate Carrier Frequency Offset (CFO) on BPSK signal via squaring operation.
    Squaring eliminates +/- 1 phase modulation, producing an unmodulated tone at 2 * CFO.
    """
    y = x_bb[: min(len(x_bb), nfft)].astype(np.complex128) ** 2
    y = y * np.hanning(len(y))
    spec = np.abs(np.fft.fftshift(np.fft.fft(y, nfft)))
    freqs = np.fft.fftshift(np.fft.fftfreq(nfft, 1.0 / fs))
    keep = np.abs(freqs) <= 2.0 * max_cfo

    k = int(np.argmax(spec * keep))
    # Sub-bin parabolic refinement
    if 0 < k < nfft - 1:
        a, b, c = spec[k - 1], spec[k], spec[k + 1]
        den = a - 2 * b + c
        delta = 0.5 * (a - c) / den if den != 0 else 0.0
    else:
        delta = 0.0

    tone_freq = freqs[k] + delta * fs / nfft
    cfo = tone_freq / 2.0
    snr_db = 10.0 * np.log10(spec[k] / (np.median(spec[keep]) + 1e-12))
    return float(cfo), float(snr_db)


from src.algorithms.adra_tdoa import (
    lorentzian_taylor_peak, adra_xcorr_lag, adra_segment_lag,
    compute_pair_tdoa_adra, compute_chromatic_group_delay_offset_s,
    auto_calibrate_coax_bench, DEFAULT_CHROMATIC_CAL_MATRIX
)
from src.algorithms.solvers import solve_adra_3stage


# ----------------------------------------------------------------------------
# 4. Cross-Correlation and Sub-Sample Delay Estimation (ADRA-TDOA + Baseline)
# ----------------------------------------------------------------------------
def parabolic_peak(mag, k):
    """3-point parabolic peak interpolation (with hybrid log-parabolic/Taylor upgrade)."""
    if k <= 0 or k >= len(mag) - 1:
        return float(k)
    kf, _ = lorentzian_taylor_peak(mag, k, early_gate=False)
    return float(kf)


def xcorr_lag(a, b, lag_center, lag_halfwidth, upsample=8, mode="adra", fs=2_400_000):
    """
    Estimate fractional sample lag D such that b[n] ~= a[n - D].
    Supports mode="adra" (Coherence-Weighted GCC + CLEAN + 5-pt Lorentzian-Taylor),
    mode="gcc_phat", and mode="standard".
    """
    lag, quality, _ = adra_xcorr_lag(
        a, b, lag_center, lag_halfwidth, upsample=upsample, mode=mode, fs=fs
    )
    return float(lag), float(quality)


def segment_lag(x_i, x_j, start, n, lag_center, lag_halfwidth, upsample=8, mode="adra", fs=2_400_000):
    """Extract segment of length n from x_i, correlate against search window in x_j."""
    lag, q, _ = adra_segment_lag(
        x_i, x_j, start, n, lag_center, lag_halfwidth, upsample=upsample, mode=mode, fs=fs
    )
    return lag, q


def compute_pair_tdoa(ri, ti, rj, tj, fs, seg_ms=10.0, nseg=60, min_quality=8.0,
                      mode="adra", return_telemetry=False):
    """
    Compute differential lag d = D_tgt - D_ref across nseg segments.
    Uses ADRA-TDOA (RANSAC-Kalman clock-drift trajectory + Coherence-Weighted GCC +
    Early-Gate CLEAN + Huber M-estimator) by default, or "gcc_phat" / "standard".
    Returns (d_seconds, spread_seconds, valid_segments, coarse_quality)
    or (..., telemetry_dict) when return_telemetry=True.
    """
    d_sec, spread_sec, used, q0, telemetry = compute_pair_tdoa_adra(
        ri, ti, rj, tj, fs, seg_ms=seg_ms, nseg=nseg, min_quality=min_quality, mode=mode
    )
    if return_telemetry:
        return d_sec, spread_sec, used, q0, telemetry
    return d_sec, spread_sec, used, q0


# ----------------------------------------------------------------------------
# 5. Hyperbolic Position Solver (ADRA 3-Stage + 2D Grid Optimization)
# ----------------------------------------------------------------------------
def model_dd(p, xi, xj):
    """Model range difference: distance to node j minus distance to node i."""
    return float(np.hypot(*(p - xj)) - np.hypot(*(p - xi)))


def solve_position_2d(node_xy, pair_dd, pair_w=None, search_radius=1500.0, grid_step=5.0, mode="adra"):
    """
    Hybrid 2D hyperbolic position solver:
      Stage 1: Exhaustive global 2D grid search over search_radius + Fang/Chan-Ho closed-form seed.
      Stage 2: Huber-IRLS + Bounded non-linear least squares polish using Levenberg-Marquardt / TRF.
    """
    pairs = list(pair_dd.keys())
    w = np.ones(len(pairs)) if pair_w is None else np.array([pair_w[p] for p in pairs])
    centroid = np.mean(np.array(list(node_xy.values())), axis=0)

    gx = np.arange(centroid[0] - search_radius, centroid[0] + search_radius, grid_step)
    gy = np.arange(centroid[1] - search_radius, centroid[1] + search_radius, grid_step)
    X, Y = np.meshgrid(gx, gy)
    cost = np.zeros_like(X)

    for wk, (i, j) in zip(w, pairs):
        xi, xj = node_xy[i], node_xy[j]
        m = np.hypot(X - xj[0], Y - xj[1]) - np.hypot(X - xi[0], Y - xi[1])
        cost += wk * (m - pair_dd[(i, j)]) ** 2

    r, c = np.unravel_index(np.argmin(cost), cost.shape)
    p0 = np.array([gx[c], gy[r]])

    if mode == "adra":
        res_3stage = solve_adra_3stage(node_xy, pair_dd, pair_w=pair_w, search_radius=search_radius, grid_step=15.0)
        p0 = np.array(res_3stage["position"], dtype=float)

    def residuals(p):
        return [np.sqrt(wk) * (model_dd(p, node_xy[i], node_xy[j]) - pair_dd[(i, j)])
                for wk, (i, j) in zip(w, pairs)]

    lo_b = [gx[0], gy[0]]
    hi_b = [gx[-1], gy[-1]]
    p0_clipped = np.clip(p0, lo_b, hi_b)
    sol = least_squares(residuals, p0_clipped, bounds=(lo_b, hi_b))
    rms_cost = float(np.sqrt(np.mean(sol.fun ** 2)))
    return sol.x, rms_cost, (gx, gy, cost)

