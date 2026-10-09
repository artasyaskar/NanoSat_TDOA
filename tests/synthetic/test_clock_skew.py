"""
test_clock_skew.py - Automated verification of segmented reference cancellation under clock skew.

Simulates the worst-case realistic RTL-SDR TCXO clock drift scenario:
  Node 1:  0.0 ppm
  Node 2: +2.0 ppm
  Node 3: -3.0 ppm
Relative pairwise skew: up to 5.0 ppm (Pair 2-3).
Without reference cancellation, 5.0 ppm over 2.0 s yields 10.0 us (2998 m) of drift.
With 10 ms segmented cross-correlation and reference subtraction, the drift is
canceled, yielding an accurate fix within benchmark tolerance (< 6.0 m).
"""

import os
import sys
import numpy as np

# Add src and tdoa_kit to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "tdoa_kit", "solution1")))

from src.processing.core import (
    C_LIGHT, extract_band, compute_pair_tdoa, solve_position_2d
)
from make_waveforms import make_pn_waveform, FS_TX


def run_clock_skew_test():
    print("=== Running TEST-SYNTH-SKEW: Clock Skew Rejection Verification ===")
    fs = 2_400_000
    bw = 600_000
    f_ref_bb = -570_000
    f_tgt_bb = +570_000

    # Geometry (meters)
    nodes = {
        "node1": np.array([0.0, 0.0]),
        "node2": np.array([270.0, 30.0]),
        "node3": np.array([110.0, 240.0])
    }
    ref_xy = np.array([-160.0, 280.0])
    truth_tgt = np.array([125.0, 95.0])

    # Baseband waveforms (zero digital offset, we mix explicitly to -570 kHz and +570 kHz)
    ref_tx = make_pn_waveform(seed=11, digital_offset=0.0)
    tgt_tx = make_pn_waveform(seed=22, digital_offset=0.0)

    # 4x oversampling of periodic baseband template for sub-nanosecond continuous interpolation
    Q = int(len(ref_tx) * fs / FS_TX)
    # FFT resample to fs
    X_ref = np.fft.fft(ref_tx)
    X_tgt = np.fft.fft(tgt_tx)
    Y_ref = np.zeros(Q, dtype=complex)
    Y_tgt = np.zeros(Q, dtype=complex)
    h_tx = len(ref_tx) // 2
    Y_ref[:h_tx] = X_ref[:h_tx]
    Y_ref[-h_tx:] = X_ref[-h_tx:]
    Y_tgt[:h_tx] = X_tgt[:h_tx]
    Y_tgt[-h_tx:] = X_tgt[-h_tx:]
    y_ref_fs = np.fft.ifft(Y_ref) * (Q / len(ref_tx))
    y_tgt_fs = np.fft.ifft(Y_tgt) * (Q / len(tgt_tx))

    # 4x zero-padding in frequency domain
    Q_over = 4 * Q
    fs_over = 4 * fs
    Y4_ref = np.zeros(Q_over, dtype=complex)
    Y4_tgt = np.zeros(Q_over, dtype=complex)
    h_q = Q // 2
    Y4_ref[:h_q] = Y_ref[:h_q]
    Y4_ref[-h_q:] = Y_ref[-h_q:]
    Y4_tgt[:h_q] = Y_tgt[:h_q]
    Y4_tgt[-h_q:] = Y_tgt[-h_q:]
    template_ref = np.fft.ifft(Y4_ref) * 4.0
    template_tgt = np.fft.ifft(Y4_tgt) * 4.0

    T_period = Q / fs
    N = int(2.0 * fs)

    # Clock skews in ppm and async start times
    clock_skews = {
        "node1": 0.0e-6,
        "node2": +2.0e-6,
        "node3": -3.0e-6
    }
    t0_map = {
        "node1": 0.012,
        "node2": 0.035,
        "node3": 0.021
    }

    print("  Simulated Station Clocks:")
    print(f"    Node 1: skew = {clock_skews['node1']*1e6:+.1f} ppm, start = {t0_map['node1']*1e3:.1f} ms")
    print(f"    Node 2: skew = {clock_skews['node2']*1e6:+.1f} ppm, start = {t0_map['node2']*1e3:.1f} ms")
    print(f"    Node 3: skew = {clock_skews['node3']*1e6:+.1f} ppm, start = {t0_map['node3']*1e3:.1f} ms")
    print(f"    Max relative skew (Pair 2-3): {abs(clock_skews['node2'] - clock_skews['node3'])*1e6:.1f} ppm")

    def simulate_rx_with_skew(node_name):
        nxy = nodes[node_name]
        t0 = t0_map[node_name]
        skew = clock_skews[node_name]

        d_ref = float(np.hypot(*(ref_xy - nxy)))
        d_tgt = float(np.hypot(*(truth_tgt - nxy)))
        tau_ref = d_ref / C_LIGHT
        tau_tgt = d_tgt / C_LIGHT

        m_indices = np.arange(N)
        # Physical time at receiver ADC sample m
        t_sample = t0 + m_indices * (1.0 + skew) / fs

        # Physical emission times
        t_emit_ref = (t_sample - tau_ref) % T_period
        t_emit_tgt = (t_sample - tau_tgt) % T_period

        # Interpolate continuous waveform on 4x oversampled periodic grid
        idx_float_ref = t_emit_ref * fs_over
        idx_float_tgt = t_emit_tgt * fs_over

        idx0_ref = np.floor(idx_float_ref).astype(int) % Q_over
        idx1_ref = (idx0_ref + 1) % Q_over
        frac_ref = (idx_float_ref - np.floor(idx_float_ref)).astype(np.complex64)
        sig_ref = template_ref[idx0_ref] * (1.0 - frac_ref) + template_ref[idx1_ref] * frac_ref

        idx0_tgt = np.floor(idx_float_tgt).astype(int) % Q_over
        idx1_tgt = (idx0_tgt + 1) % Q_over
        frac_tgt = (idx_float_tgt - np.floor(idx_float_tgt)).astype(np.complex64)
        sig_tgt = template_tgt[idx0_tgt] * (1.0 - frac_tgt) + template_tgt[idx1_tgt] * frac_tgt

        # Mix to baseband offsets (-570 kHz and +570 kHz)
        # Note: ADC mixes with local receiver LO which also has skew, but at 433 MHz,
        # 5 ppm is 2.1 kHz, which is easily handled by baseband Kaiser filter and cross-correlation
        mix_ref = np.exp(2j * np.pi * f_ref_bb * m_indices / fs)
        mix_tgt = np.exp(2j * np.pi * f_tgt_bb * m_indices / fs)

        rx = sig_ref * mix_ref + sig_tgt * mix_tgt
        # Add slight receiver thermal noise (SNR ~25 dB)
        noise = (np.random.normal(0, 0.05, N) + 1j * np.random.normal(0, 0.05, N)).astype(np.complex64)
        return (rx + noise).astype(np.complex64)

    rx_data = {n: simulate_rx_with_skew(n) for n in nodes}

    # Band extraction
    processed = {}
    for n, x in rx_data.items():
        ref_band = extract_band(x, fs, f_ref_bb, bw)
        tgt_band = extract_band(x, fs, f_tgt_bb, bw)
        processed[n] = (ref_band, tgt_band)

    # Cross-correlate pairs with 10 ms segmented processing
    pairs = [("node1", "node2"), ("node1", "node3"), ("node2", "node3")]
    pair_dd = {}

    for base, other in pairs:
        ri, ti = processed[base]
        rj, tj = processed[other]

        # 10 ms segments, 60 segments (covers 0.6 seconds of capture)
        d_sec, spread, used, q0 = compute_pair_tdoa(ri, ti, rj, tj, fs, seg_ms=10.0, nseg=60)
        assert d_sec is not None, f"Cross correlation failed for pair {base}-{other}"

        ref_geom = (np.hypot(*(ref_xy - nodes[other])) - np.hypot(*(ref_xy - nodes[base]))) / C_LIGHT
        tdoa = d_sec + ref_geom
        pair_dd[(base, other)] = C_LIGHT * tdoa

        true_dd = np.hypot(*(truth_tgt - nodes[other])) - np.hypot(*(truth_tgt - nodes[base]))
        err_m = abs(pair_dd[(base, other)] - true_dd)
        print(f"  Pair {base}-{other}: Measured DD = {pair_dd[(base, other)]:.2f} m, Truth DD = {true_dd:.2f} m, Error = {err_m:.3f} m (Spread = {spread*1e9:.2f} ns)")
        assert err_m < 2.0, f"Range difference error {err_m:.3f} m exceeds threshold under 5 ppm skew"

    # Solve position
    est_xy, cost, grid = solve_position_2d(nodes, pair_dd)
    pos_error_m = float(np.hypot(*(est_xy - truth_tgt)))
    print(f"  Estimated Target: ({est_xy[0]:.2f}, {est_xy[1]:.2f}) m")
    print(f"  True Target:      ({truth_tgt[0]:.2f}, {truth_tgt[1]:.2f}) m")
    print(f"  Position Error:   {pos_error_m:.3f} m")

    assert pos_error_m < 6.0, f"Position error {pos_error_m:.3f} m exceeds 6.0 m benchmark under clock skew"
    print("VERDICT: PASS\n")
    return pos_error_m


if __name__ == "__main__":
    run_clock_skew_test()
