#!/usr/bin/env python3
"""
make_waveforms.py  -  Solution 1: create the two test signals (run ONCE on any computer).

Creates:
  ref_pluto.npy                 reference signal for the ADALM-Pluto  (complex64)
  target_bladerf_sc16q11.bin    target signal for the bladeRF          (int16 I/Q)
  target.npy                    same target signal as complex64 (used by the simulator)

Signal design (why):
  * Pseudo-random +1/-1 "chips" at 400 kchip/s -> a noise-like signal whose
    correlation has ONE sharp peak. Sharp peak = precise timing.
  * Smoothed (raised-cosine spectrum, alpha 0.35) so it occupies only
    ~540 kHz and two of them fit inside one RTL-SDR recording (2.4 MHz).
  * 2^18 chips long, repeated forever: one repeat lasts 0.655 s. Our capture
    start times only need to agree to better than +/-0.3 s.
  * Digitally shifted 400 kHz away from the transmitter's centre frequency,
    so the transmitter's small unwanted "carrier leakage" spike falls in an
    empty gap and is filtered out later.
"""
import numpy as np

FS_TX = 2_000_000        # transmitter sample rate (samples/s)
CHIP_RATE = 400_000      # chips per second
N_CHIPS = 2 ** 18        # chips per repeat (period = 0.655 s)
ALPHA = 0.35             # spectrum roll-off (occupied bandwidth = 1.35 x chip rate = 540 kHz)
REF_DIGITAL_OFFSET = -400_000   # Pluto LO 433.750 MHz  -> signal at 433.350 MHz
TGT_DIGITAL_OFFSET = +400_000   # bladeRF LO 434.090 MHz -> signal at 434.490 MHz


def make_pn_waveform(seed, fs=FS_TX, chip_rate=CHIP_RATE, n_chips=N_CHIPS,
                     digital_offset=0.0, alpha=ALPHA):
    sps = int(round(fs / chip_rate))                     # samples per chip (5)
    rng = np.random.default_rng(seed)
    chips = rng.choice([-1.0, 1.0], size=n_chips)        # the pseudo-random code
    x = np.repeat(chips, sps).astype(np.complex128)      # rectangular chips
    # Smooth the chips with a raised-cosine spectrum (done with an FFT, so the
    # waveform stays perfectly periodic and loops without a glitch).
    X = np.fft.fft(x)
    f = np.abs(np.fft.fftfreq(len(x), 1 / fs))
    f1, f2 = (1 - alpha) * chip_rate / 2, (1 + alpha) * chip_rate / 2
    H = np.where(f <= f1, 1.0, np.where(f >= f2, 0.0, 0.5 * (1 + np.cos(np.pi * (f - f1) / (f2 - f1)))))
    x = np.fft.ifft(X * H)
    # Digital frequency shift (whole number of cycles per repeat -> seamless loop)
    n = np.arange(len(x))
    x = x * np.exp(2j * np.pi * digital_offset * n / fs)
    x = 0.8 * x / np.max(np.abs(x))                      # leave headroom, no clipping
    return x.astype(np.complex64)


if __name__ == "__main__":
    ref = make_pn_waveform(seed=11, digital_offset=REF_DIGITAL_OFFSET)
    tgt = make_pn_waveform(seed=22, digital_offset=TGT_DIGITAL_OFFSET)
    np.save("ref_pluto.npy", ref)
    np.save("target.npy", tgt)
    # bladeRF wants interleaved signed 16-bit I,Q with full scale 2048 ("SC16 Q11")
    iq = np.empty(2 * len(tgt), dtype=np.int16)
    iq[0::2] = np.round(tgt.real * 2047).astype(np.int16)
    iq[1::2] = np.round(tgt.imag * 2047).astype(np.int16)
    iq.tofile("target_bladerf_sc16q11.bin")
    print(f"Wrote ref_pluto.npy, target.npy, target_bladerf_sc16q11.bin  "
          f"({len(ref)} samples each = {len(ref) / FS_TX:.3f} s per repeat)")
