#!/usr/bin/env python3
"""
pluto_tx_reference.py  -  Solution 1: make the ADALM-Pluto transmit the REFERENCE signal.

Run on the Pi (or laptop) that the Pluto is plugged into, at the SURVEYED reference point.
    python3 pluto_tx_reference.py --gain -40
Keep it running for the whole session. Press Enter to stop.

--gain is the Pluto transmit attenuation in dB (0 = maximum power, -89 = minimum).
Start low (-40) and only raise it if the far nodes cannot see the signal.
"""
import argparse
import numpy as np
import adi   # pip3 install pyadi-iio

p = argparse.ArgumentParser()
p.add_argument("--uri", default="ip:192.168.2.1", help="Pluto address (or usb:...)")
p.add_argument("--lo", type=float, default=433.750e6, help="transmitter centre frequency (Hz)")
p.add_argument("--gain", type=float, default=-40.0, help="TX gain in dB, -89..0")
p.add_argument("--file", default="ref_pluto.npy")
a = p.parse_args()

iq = np.load(a.file)
sdr = adi.Pluto(a.uri)
sdr.sample_rate = 2_000_000          # must match make_waveforms.py
sdr.tx_rf_bandwidth = 1_500_000
sdr.tx_lo = int(a.lo)
sdr.tx_hardwaregain_chan0 = a.gain
sdr.tx_cyclic_buffer = True          # repeat the buffer forever, without gaps
sdr.tx((iq * 2 ** 14).astype(np.complex64))   # pyadi-iio expects +/-2^14 full scale
print(f"Reference transmitting: LO {a.lo/1e6:.3f} MHz, signal centre {(a.lo-400e3)/1e6:.3f} MHz, gain {a.gain} dB")
input("Press Enter to stop transmitting... ")
sdr.tx_destroy_buffer()
print("Stopped.")
