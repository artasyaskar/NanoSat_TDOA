#!/usr/bin/env python3
"""
pluto_rx_capture.py  -  Solution 2: the ADALM-Pluto used as a RECEIVER node.

The Pluto's USB 2.0 link is too slow to stream 20 MS/s continuously, but it can
record a burst of up to a few million samples into its own memory and then send it.
That is all TDOA needs: one short, gap-free piece per capture time.

    python3 pluto_rx_capture.py --node node3
Saves  ~/tdoa/captures/node3_<UTC>.npy  every --period seconds (aligned to the clock).
"""
import argparse
import datetime as dt
import json
import os
import time

import numpy as np
import adi   # pip3 install pyadi-iio

p = argparse.ArgumentParser()
p.add_argument("--node", required=True)
p.add_argument("--uri", default="ip:192.168.2.1")
p.add_argument("--freq", type=float, default=2437e6, help="Wi-Fi channel 6 centre")
p.add_argument("--rate", type=float, default=20e6)
p.add_argument("--gain", type=float, default=40, help="manual RX gain in dB (0..70)")
p.add_argument("--samples", type=int, default=2 ** 22, help="4M samples = 0.21 s at 20 MS/s")
p.add_argument("--period", type=int, default=30)
p.add_argument("--count", type=int, default=0)
p.add_argument("--dir", default=os.path.expanduser("~/tdoa/captures"))
a = p.parse_args()
os.makedirs(a.dir, exist_ok=True)

sdr = adi.Pluto(a.uri)
sdr.sample_rate = int(a.rate)
sdr.rx_rf_bandwidth = int(a.rate)
sdr.rx_lo = int(a.freq)
sdr.gain_control_mode_chan0 = "manual"      # never automatic gain for TDOA
sdr.rx_hardwaregain_chan0 = a.gain
sdr.rx_buffer_size = a.samples
sdr.rx()                                    # one warm-up capture, thrown away

done = 0
while True:
    slot = (int(time.time()) // a.period + 1) * a.period
    sdr.rx_destroy_buffer()                 # make sure the next capture is fresh
    while time.time() < slot - 0.002:
        time.sleep(0.001)
    t_call = time.time()
    x = sdr.rx().astype(np.complex64) / 2048.0
    name = f"{a.node}_{dt.datetime.fromtimestamp(slot, dt.timezone.utc):%Y%m%d_%H%M%S}"
    np.save(os.path.join(a.dir, name + ".npy"), x)
    with open(os.path.join(a.dir, name + ".json"), "w") as f:
        json.dump({"node": a.node, "slot_utc": slot, "call_time": t_call, "rate": a.rate,
                   "freq": a.freq, "gain": a.gain, "samples": len(x)}, f)
    print(f"[{a.node}] saved {name}.npy ({len(x)} samples)", flush=True)
    done += 1
    if a.count and done >= a.count:
        break
