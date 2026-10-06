#!/usr/bin/env python3
"""
bladerf_rx_capture.py  -  Solution 2: a bladeRF used as a RECEIVER node.

Streams continuously (USB 3.0) and, on every capture slot, saves a gap-free piece
of 0.4 s (8M samples). Longer than the Pluto's 0.2 s piece so the two always overlap.

    python3 bladerf_rx_capture.py --node node1
Saves  ~/tdoa/captures/node1_<UTC>.sc16   (signed 16-bit I/Q, full scale 2048)

Needs the libbladeRF Python bindings (see the guide, section "Installing software").
"""
import argparse
import datetime as dt
import json
import os
import shutil
import time

from bladerf import _bladerf

p = argparse.ArgumentParser()
p.add_argument("--node", required=True)
p.add_argument("--freq", type=float, default=2437e6)
p.add_argument("--rate", type=float, default=20e6)
p.add_argument("--gain", type=int, default=40)
p.add_argument("--samples", type=int, default=8_000_000)
p.add_argument("--period", type=int, default=30)
p.add_argument("--count", type=int, default=0)
p.add_argument("--dir", default=os.path.expanduser("~/tdoa/captures"))
a = p.parse_args()
os.makedirs(a.dir, exist_ok=True)

sdr = _bladerf.BladeRF()
ch = sdr.Channel(_bladerf.CHANNEL_RX(0))
ch.frequency = int(a.freq)
ch.sample_rate = int(a.rate)
ch.bandwidth = int(a.rate * 0.9)
ch.gain_mode = _bladerf.GainMode.Manual
ch.gain = a.gain
sdr.sync_config(layout=_bladerf.ChannelLayout.RX_X1, fmt=_bladerf.Format.SC16_Q11,
                num_buffers=32, buffer_size=16384, num_transfers=16, stream_timeout=3500)
ch.enable = True

CHUNK = 65536                                  # samples per read (3.3 ms)
chunk = bytearray(CHUNK * 4)
capture = bytearray(a.samples * 4)             # kept in RAM: SD cards are too slow
done = 0
try:
    while True:
        slot = (int(time.time()) // a.period + 1) * a.period
        while time.time() < slot:              # keep the stream flowing, discard samples
            sdr.sync_rx(chunk, CHUNK)
        view, got = memoryview(capture), 0
        while got < a.samples:
            n = min(CHUNK, a.samples - got)
            sdr.sync_rx(view[got * 4:(got + n) * 4], n)
            got += n
        name = f"{a.node}_{dt.datetime.fromtimestamp(slot, dt.timezone.utc):%Y%m%d_%H%M%S}"
        tmp = os.path.join("/dev/shm", name + ".sc16")
        with open(tmp, "wb") as f:
            f.write(capture)
        shutil.move(tmp, os.path.join(a.dir, name + ".sc16"))
        with open(os.path.join(a.dir, name + ".json"), "w") as f:
            json.dump({"node": a.node, "slot_utc": slot, "rate": a.rate, "freq": a.freq,
                       "gain": a.gain, "samples": a.samples}, f)
        print(f"[{a.node}] saved {name}.sc16", flush=True)
        done += 1
        if a.count and done >= a.count:
            break
except KeyboardInterrupt:
    pass
finally:
    ch.enable = False
    sdr.close()
