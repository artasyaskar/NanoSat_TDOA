#!/usr/bin/env python3
"""
bladerf_driver.py - Cross-Platform bladeRF Target Transmitter Driver.

Supports both Linux and Windows, automatically formatting CLI commands for
either bladeRF 2.0 micro (tx1) or older bladeRF x40/x115 (tx).
"""

import argparse
import os
import shutil
import signal
import subprocess
import sys

proc = None


def handle_shutdown(signum, frame):
    global proc
    print(f"\n[BLADERF] Received termination signal {signum}, stopping transmission...", flush=True)
    if proc:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
    sys.exit(0)


signal.signal(signal.SIGINT, handle_shutdown)
signal.signal(signal.SIGTERM, handle_shutdown)


def main():
    global proc
    p = argparse.ArgumentParser(description="Cross-Platform bladeRF Target Transmitter Driver")
    p.add_argument("--gain", type=int, default=0, help="TX gain in dB (Default: 0)")
    p.add_argument("--lo", type=int, default=434090000, help="Transmitter LO frequency in Hz (Default: 434.090 MHz)")
    p.add_argument("--rate", type=int, default=2000000, help="Sample rate in sps (Default: 2 MS/s)")
    p.add_argument("--bw", type=int, default=1500000, help="RF bandwidth in Hz (Default: 1.5 MHz)")
    p.add_argument("--file", default="target_bladerf_sc16q11.bin", help="Path to SC16 binary waveform")
    p.add_argument("--legacy", action="store_true", help="Force legacy bladeRF x40/x115 syntax")
    a = p.parse_args()

    if not os.path.exists(a.file):
        sys.exit(f"[BLADERF] [FATAL] Waveform file not found: {a.file}. Run make_waveforms.py first.")

    cli_bin = shutil.which("bladeRF-cli")
    if not cli_bin:
        sys.exit("[BLADERF] [FATAL] bladeRF-cli executable not found in PATH.")

    file_abs = os.path.abspath(a.file).replace("\\", "/")

    if a.legacy:
        # bladeRF x40 / x115 legacy command syntax
        commands = (
            f"set frequency tx {a.lo}; "
            f"set samplerate tx {a.rate}; "
            f"set bandwidth tx {a.bw}; "
            f"set txvga1 -35; set txvga2 {a.gain}; "
            f"tx config file={file_abs} format=bin repeat=0 delay=0; "
            f"tx start; "
            f"tx wait"
        )
    else:
        # bladeRF 2.0 micro command syntax
        commands = (
            f"set frequency tx1 {a.lo}; "
            f"set samplerate tx1 {a.rate}; "
            f"set bandwidth tx1 {a.bw}; "
            f"set gain tx1 {a.gain}; "
            f"tx config file={file_abs} format=bin repeat=0 delay=0; "
            f"tx start; "
            f"tx wait"
        )

    cmd = [cli_bin, "-e", commands]
    rf_center = (a.lo + 400e3) / 1e6
    print(f"[BLADERF] Starting transmission: LO={a.lo/1e6:.3f} MHz, Signal Center={rf_center:.3f} MHz, Gain={a.gain} dB", flush=True)

    proc = subprocess.Popen(cmd)
    proc.wait()


if __name__ == "__main__":
    main()
