#!/usr/bin/env python3
"""
pluto_driver.py - Headless, Non-Blocking ADALM-Pluto Reference Transmitter Driver.

Eliminates the interactive input() crash from the original script, allowing execution
as a systemd service, background process, or via automated orchestration.
"""

import argparse
import os
import signal
import sys
import time
import numpy as np

running = True


def handle_shutdown(signum, frame):
    global running
    print(f"\n[PLUTO] Received termination signal {signum}, stopping transmission...", flush=True)
    running = False


signal.signal(signal.SIGINT, handle_shutdown)
signal.signal(signal.SIGTERM, handle_shutdown)


def main():
    p = argparse.ArgumentParser(description="Headless ADALM-Pluto Reference Transmitter Driver")
    p.add_argument("--uri", default="ip:192.168.2.1", help="Pluto connection URI (e.g. ip:192.168.2.1 or usb:...)")
    p.add_argument("--lo", type=float, default=433.750e6, help="Transmitter LO frequency in Hz (Default: 433.750 MHz)")
    p.add_argument("--gain", type=float, default=-40.0, help="TX hardware gain in dB (-89 to 0)")
    p.add_argument("--file", default="ref_pluto.npy", help="Path to baseband waveform .npy file")
    a = p.parse_args()

    if not os.path.exists(a.file):
        sys.exit(f"[PLUTO] [FATAL] Waveform file not found: {a.file}. Run make_waveforms.py first.")

    try:
        import adi
    except ImportError:
        sys.exit("[PLUTO] [FATAL] pyadi-iio is not installed. Install via: pip3 install pyadi-iio")

    print(f"[PLUTO] Connecting to device at {a.uri}...", flush=True)
    try:
        sdr = adi.Pluto(a.uri)
    except Exception as e:
        sys.exit(f"[PLUTO] [FATAL] Failed to connect to PlutoSDR at {a.uri}: {e}")

    iq = np.load(a.file)
    sdr.sample_rate = 2_000_000
    sdr.tx_rf_bandwidth = 1_500_000
    sdr.tx_lo = int(a.lo)
    sdr.tx_hardwaregain_chan0 = float(a.gain)
    sdr.tx_cyclic_buffer = True

    # Scale to full 16-bit range for pyadi-iio
    tx_data = (iq * (2 ** 14)).astype(np.complex64)
    sdr.tx(tx_data)

    rf_center = (a.lo - 400e3) / 1e6
    print(f"[PLUTO] Transmitting: LO={a.lo/1e6:.3f} MHz, Signal Center={rf_center:.3f} MHz, Gain={a.gain} dB", flush=True)
    print("[PLUTO] Running continuously in headless mode. Send SIGINT (Ctrl+C) or SIGTERM to stop.", flush=True)

    while running:
        time.sleep(0.5)

    try:
        sdr.tx_destroy_buffer()
    except Exception:
        pass
    print("[PLUTO] Transmission cleanly halted.", flush=True)


if __name__ == "__main__":
    main()
