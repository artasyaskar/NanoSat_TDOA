"""
test_pipeline_baseline.py - Automated verification of the baseline TDOA pipeline on ideal synthetic data.
"""

import os
import sys
import numpy as np

# Add src and tdoa_kit to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "tdoa_kit", "common")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "tdoa_kit", "solution1")))

from src.processing.core import (
    C_LIGHT, extract_band, estimate_cfo_bpsk, mix,
    compute_pair_tdoa, solve_position_2d, model_dd
)
from make_waveforms import make_pn_waveform, FS_TX, REF_DIGITAL_OFFSET, TGT_DIGITAL_OFFSET


def periodic_resample(x, n_new):
    X = np.fft.fft(x)
    n = len(x)
    Y = np.zeros(n_new, dtype=complex)
    h = n // 2
    Y[:h] = X[:h]
    Y[-h:] = X[-h:]
    return np.fft.ifft(Y) * (n_new / n)


def run_baseline_test():
    print("=== Running TEST-SYNTH-BASELINE: Ideal Synthetic Validation ===")
    fs = 2_400_000
    fc = 433_920_000
    lo_ref = 433_750_000
    lo_tgt = 434_090_000
    f_ref_bb = (lo_ref + REF_DIGITAL_OFFSET) - fc  # -570 kHz
    f_tgt_bb = (lo_tgt + TGT_DIGITAL_OFFSET) - fc  # +570 kHz
    bw = 600_000

    # Geometry (meters)
    nodes = {
        "node1": np.array([0.0, 0.0]),
        "node2": np.array([270.0, 30.0]),
        "node3": np.array([110.0, 240.0])
    }
    ref_xy = np.array([-160.0, 280.0])
    truth_tgt = np.array([125.0, 95.0])

    # Generate test waveforms
    ref_tx = make_pn_waveform(seed=11, digital_offset=REF_DIGITAL_OFFSET)
    tgt_tx = make_pn_waveform(seed=22, digital_offset=TGT_DIGITAL_OFFSET)
    Q = int(len(ref_tx) * fs / FS_TX)
    S_ref = np.fft.fft(periodic_resample(ref_tx, Q))
    S_tgt = np.fft.fft(periodic_resample(tgt_tx, Q))
    f_axis = np.fft.fftfreq(Q, 1.0 / fs)
    N = int(2.0 * fs)

    # Receiver start jitters (simulate 10-30 ms asynchronous startup)
    t0_map = {"node1": 0.012, "node2": 0.035, "node3": 0.021}

    def simulate_rx(node_name):
        nxy = nodes[node_name]
        t0 = t0_map[node_name]

        # Delay for Ref and Target
        d_ref = float(np.hypot(*(ref_xy - nxy)))
        d_tgt = float(np.hypot(*(truth_tgt - nxy)))
        tau_ref = t0 - d_ref / C_LIGHT
        tau_tgt = t0 - d_tgt / C_LIGHT

        spec_ref = S_ref * np.exp(2j * np.pi * f_axis * tau_ref)
        spec_tgt = S_tgt * np.exp(2j * np.pi * f_axis * tau_tgt)

        y_ref = np.resize(np.fft.ifft(spec_ref), N)
        y_tgt = np.resize(np.fft.ifft(spec_tgt), N)

        # Mix by transmitter LO offset relative to receiver center (LO_REF - FC = -170 kHz, LO_TGT - FC = +170 kHz)
        # Note: ref_tx already has -400 kHz digital offset, so -400 kHz + (-170 kHz) = -570 kHz in receiver baseband!
        n = np.arange(N)
        shift_ref = (lo_ref - fc)
        shift_tgt = (lo_tgt - fc)
        s_ref = y_ref * np.exp(2j * np.pi * shift_ref * n / fs)
        s_tgt = y_tgt * np.exp(2j * np.pi * shift_tgt * n / fs)
        return (s_ref + s_tgt).astype(np.complex64)

    # Simulate captures for all nodes
    rx_data = {n: simulate_rx(n) for n in nodes}

    # Process each node
    processed = {}
    for n, x in rx_data.items():
        # Band extraction
        ref_band = extract_band(x, fs, f_ref_bb, bw)
        tgt_band = extract_band(x, fs, f_tgt_bb, bw)
        processed[n] = (ref_band, tgt_band)

    # Cross-correlate pairs and compute TDOAs
    pairs = [("node1", "node2"), ("node1", "node3"), ("node2", "node3")]
    pair_dd = {}

    for base, other in pairs:
        ri, ti = processed[base]
        rj, tj = processed[other]

        d_sec, spread, used, q0 = compute_pair_tdoa(ri, ti, rj, tj, fs, seg_ms=10.0, nseg=60)
        assert d_sec is not None, f"Cross correlation failed for pair {base}-{other}"

        # Add back surveyed reference geometric delay
        ref_geom = (np.hypot(*(ref_xy - nodes[other])) - np.hypot(*(ref_xy - nodes[base]))) / C_LIGHT
        tdoa = d_sec + ref_geom
        pair_dd[(base, other)] = C_LIGHT * tdoa

        # Theoretical truth range difference
        true_dd = np.hypot(*(truth_tgt - nodes[other])) - np.hypot(*(truth_tgt - nodes[base]))
        err_m = abs(pair_dd[(base, other)] - true_dd)
        print(f"  Pair {base}-{other}: Measured DD = {pair_dd[(base, other)]:.2f} m, Truth DD = {true_dd:.2f} m (Error = {err_m:.3f} m)")
        assert err_m < 0.20, f"Range difference error too large: {err_m} m"

    # Solve position
    est_xy, cost, grid = solve_position_2d(nodes, pair_dd)
    pos_error_m = float(np.hypot(*(est_xy - truth_tgt)))
    print(f"  Estimated Target: ({est_xy[0]:.2f}, {est_xy[1]:.2f}) m")
    print(f"  True Target:      ({truth_tgt[0]:.2f}, {truth_tgt[1]:.2f}) m")
    print(f"  Position Error:   {pos_error_m:.3f} m")

    assert pos_error_m < 0.50, f"Position error exceeds 0.5m benchmark: {pos_error_m} m"
    print("VERDICT: PASS\n")
    return pos_error_m


if __name__ == "__main__":
    run_baseline_test()
