"""
test_fractional_delay.py - Automated verification of sub-sample interpolation accuracy.

Sweeps fractional delays from 0.0 to 1.0 sample (0 to 416.7 ns, or 0 to 124.9 m)
to map the interpolator's S-curve bias. Verifies that 8x sinc upsampling + parabolic
refinement bounds peak interpolation bias within the CRLB budget (< 20 ns / 6.0 m).
"""

import os
import sys
import numpy as np

# Add paths
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "tdoa_kit", "solution1")))

from src.processing.core import (
    C_LIGHT, extract_band, compute_pair_tdoa
)
from make_waveforms import make_pn_waveform, FS_TX


def periodic_resample(x, n_new):
    X = np.fft.fft(x)
    n = len(x)
    Y = np.zeros(n_new, dtype=complex)
    h = n // 2
    Y[:h] = X[:h]
    Y[-h:] = X[-h:]
    return np.fft.ifft(Y) * (n_new / n)


def run_fractional_delay_test():
    print("=== Running TEST-SYNTH-FRAC: Sub-Sample S-Curve Bias Verification ===")
    fs = 2_400_000
    Ts = 1.0 / fs  # 416.67 ns
    bw = 600_000
    f_ref_bb = -570_000
    f_tgt_bb = +570_000

    # Baseband test signal
    tgt_tx = make_pn_waveform(seed=42, digital_offset=0.0)
    Q = int(len(tgt_tx) * fs / FS_TX)
    s_base = periodic_resample(tgt_tx, Q)
    S_base = np.fft.fft(s_base)
    f_axis = np.fft.fftfreq(Q, Ts)

    # Reference signal
    ref_tx = make_pn_waveform(seed=11, digital_offset=0.0)
    s_ref = periodic_resample(ref_tx, Q)
    S_ref = np.fft.fft(s_ref)

    N = int(1.5 * fs)  # 1.5 s capture satisfies coarse search window
    frac_steps = np.linspace(0.0, 1.0, 11)  # 0.0, 0.1, ..., 1.0
    errors_ns = []

    print(f"  Sampling Period Ts: {Ts*1e9:.2f} ns ({Ts*C_LIGHT:.2f} m)")
    print("  Fractional Step | True Delay (ns) | Measured (ns) | Error (ns) | Error (m)")
    print("  " + "-" * 65)

    for frac in frac_steps:
        delay_s = frac * Ts
        delay_ns = delay_s * 1e9

        # Receiver 1: at t = 0
        r1_ref = np.resize(np.fft.ifft(S_ref), N) * np.exp(2j * np.pi * f_ref_bb * np.arange(N) / fs)
        r1_tgt = np.resize(np.fft.ifft(S_base), N) * np.exp(2j * np.pi * f_tgt_bb * np.arange(N) / fs)
        rx1 = (r1_ref + r1_tgt).astype(np.complex64)

        # Receiver 2: delayed by delay_s for target, ref delay = 0
        spec_tgt_del = S_base * np.exp(-2j * np.pi * f_axis * delay_s)
        r2_ref = np.resize(np.fft.ifft(S_ref), N) * np.exp(2j * np.pi * f_ref_bb * np.arange(N) / fs)
        r2_tgt = np.resize(np.fft.ifft(spec_tgt_del), N) * np.exp(2j * np.pi * f_tgt_bb * np.arange(N) / fs)
        rx2 = (r2_ref + r2_tgt).astype(np.complex64)

        # Extract bands
        r1_b_ref = extract_band(rx1, fs, f_ref_bb, bw)
        r1_b_tgt = extract_band(rx1, fs, f_tgt_bb, bw)
        r2_b_ref = extract_band(rx2, fs, f_ref_bb, bw)
        r2_b_tgt = extract_band(rx2, fs, f_tgt_bb, bw)

        d_sec, spread, used, q0 = compute_pair_tdoa(r1_b_ref, r1_b_tgt, r2_b_ref, r2_b_tgt, fs, seg_ms=10.0, nseg=30)
        assert d_sec is not None, f"Cross-correlation failed for frac {frac}"

        # In compute_pair_tdoa, delay is measured between node 1 and node 2:
        # d_sec measures (tgt2 - tgt1) - (ref2 - ref1) = delay_s - 0 = delay_s
        meas_ns = d_sec * 1e9
        err_ns = meas_ns - delay_ns
        err_m = err_ns * 1e-9 * C_LIGHT
        errors_ns.append(err_ns)

        print(f"      {frac:4.1f}        |    {delay_ns:8.2f}     |   {meas_ns:8.2f}    |  {err_ns:7.2f}   |  {err_m:6.2f}")

    max_err_ns = max(abs(e) for e in errors_ns)
    rms_err_ns = np.sqrt(np.mean(np.array(errors_ns)**2))
    print("  " + "-" * 65)
    print(f"  Max Absolute S-Curve Bias: {max_err_ns:.2f} ns ({max_err_ns*1e-9*C_LIGHT:.2f} m)")
    print(f"  RMS Interpolation Error:   {rms_err_ns:.2f} ns ({rms_err_ns*1e-9*C_LIGHT:.2f} m)")

    # Assert S-curve bias stays well within budget (< 20 ns)
    assert max_err_ns < 20.0, f"Peak S-curve bias {max_err_ns:.2f} ns exceeds 20 ns budget"
    assert rms_err_ns < 8.0, f"RMS interpolation error {rms_err_ns:.2f} ns exceeds 8 ns budget"
    print("VERDICT: PASS\n")
    return max_err_ns


if __name__ == "__main__":
    run_fractional_delay_test()
