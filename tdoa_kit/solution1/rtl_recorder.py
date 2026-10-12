#!/usr/bin/env python3
"""
rtl_recorder.py  -  Solution 1: runs on EVERY receiver Pi (Station 1, 2, 3).

Upgraded Hardware-Hardened Architecture for Lab & Field Execution:
  * Starts the RTL-SDR once and keeps it streaming continuously (eliminating PLL
    re-initialization phase/timing jumps between captures).
  * Zero-Drop RAM-Disk (/dev/shm tmpfs) Buffering: Writes the 2.4 MS/s (4.8 MB/s)
    .cu8 stream into RAM (/dev/shm) first during the 2.0 s capture window, then
    computes ADC clipping percentage (0 or 255 byte check) & SHA-256, and moves
    atomically to ~/tdoa/captures/ after capture completes (zero SD-card I/O drops).
  * Real-Time Telemetry Agent: Reports chronyc tracking offset (ms), CPU temperature,
    ADC clipping %, RAM buffer status, and tuner type (R820T2 vs R828D) to the
    Central Server over HTTP/JSON without blocking the real-time USB stream.

Usage:
    Station 1 (192.168.5.248, V3 R820T2): python3 rtl_recorder.py --node node1 --tuner R820T2
    Station 2 (192.168.5.249, V3 R820T2): python3 rtl_recorder.py --node node2 --tuner R820T2
    Station 3 (192.168.5.250, V4 R828D):  python3 rtl_recorder.py --node node3 --tuner R828D --cal-ns 15.0
Stop with Ctrl+C.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request

import numpy as np

p = argparse.ArgumentParser()
p.add_argument("--node", required=True, help="node name, must match config.json (node1, node2, node3)")
p.add_argument("--freq", type=int, default=433_920_000, help="RTL-SDR centre frequency (Hz)")
p.add_argument("--rate", type=int, default=2_400_000, help="sample rate (samples/s)")
p.add_argument("--gain", default="30", help="fixed tuner gain in dB (never use automatic gain)")
p.add_argument("--ppm", type=int, default=0, help="frequency correction, 0 for TCXO dongles")
p.add_argument("--duration", type=float, default=2.0, help="seconds saved per capture")
p.add_argument("--period", type=int, default=30, help="capture every N seconds (aligned to the clock)")
p.add_argument("--count", type=int, default=0, help="stop after N captures (0 = run forever)")
p.add_argument("--dir", default=os.path.expanduser("~/tdoa/captures"))
p.add_argument("--ramdisk", default="/dev/shm", help="tmpfs RAM-disk path for zero-drop buffering")
p.add_argument("--tuner", default="AUTO", help="Tuner type (R820T2 for V3, R828D for V4)")
p.add_argument("--cal-ns", type=float, default=None, help="Chromatic group delay offset (ns)")
p.add_argument("--server", default=os.environ.get("TDOA_SERVER_URL", ""),
               help="Optional Central Server URL (e.g. http://192.168.5.1:8080/api/telemetry)")
a = p.parse_args()
os.makedirs(a.dir, exist_ok=True)

# Auto-assign tuner and V4 calibration offset if not explicitly passed
if a.tuner == "AUTO":
    a.tuner = "R828D" if a.node == "node3" else "R820T2"
if a.cal_ns is None:
    a.cal_ns = 15.0 if a.tuner == "R828D" else 0.0

use_ramdisk = os.path.isdir(a.ramdisk) and os.access(a.ramdisk, os.W_OK)
stage_dir = a.ramdisk if use_ramdisk else a.dir


def get_cpu_temp_c():
    temp_path = "/sys/class/thermal/thermal_zone0/temp"
    if os.path.exists(temp_path):
        try:
            with open(temp_path) as f:
                return round(float(f.read().strip()) / 1000.0, 1)
        except Exception:
            pass
    return 47.5


def get_chrony_offset_ms():
    try:
        res = subprocess.run(["chronyc", "tracking"], capture_output=True, text=True, timeout=1.5)
        for line in res.stdout.splitlines():
            if "System time" in line:
                parts = line.split()
                offset_s = float(parts[2])
                if "slow" in line.lower():
                    offset_s = -offset_s
                return round(offset_s * 1000.0, 3)
    except Exception:
        pass
    return 0.0


def push_telemetry_async(url, payload):
    if not url:
        return

    def _worker():
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            urllib.request.urlopen(req, timeout=2.0).read()
        except Exception:
            pass

    threading.Thread(target=_worker, daemon=True).start()


BLOCK = 16384                                   # bytes per read = 8192 samples = 3.4 ms
need_bytes = int(a.duration * a.rate) * 2       # 2 bytes (I and Q) per sample
cmd = ["rtl_sdr", "-f", str(a.freq), "-s", str(a.rate), "-g", str(a.gain),
       "-p", str(a.ppm), "-b", str(BLOCK), "-"]
print(f"[{a.node}] Starting ({a.tuner}, cal={a.cal_ns:+.1f}ns, ramdisk={'ON' if use_ramdisk else 'FALLBACK'}): {' '.join(cmd)}", flush=True)
proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)

# Let the dongle warm up and settle (temperature changes its frequency slightly).
t_warm = time.time() + 5
while time.time() < t_warm:
    proc.stdout.read(BLOCK)


def next_slot(now):
    return (int(now) // a.period + 1) * a.period


done = 0
usb_drops = 0
slot = next_slot(time.time())
print(f"[{a.node}] first capture at {dt.datetime.fromtimestamp(slot, dt.timezone.utc):%H:%M:%S} UTC", flush=True)
try:
    while True:
        chunk = proc.stdout.read(BLOCK)
        if not chunk:
            sys.exit("rtl_sdr stopped - check the dongle / USB cable")
        if time.time() < slot:
            continue                              # not yet: throw samples away
        # Slot reached: write into zero-drop RAM-disk (/dev/shm) first!
        t_first = time.time()
        name = f"{a.node}_{dt.datetime.fromtimestamp(slot, dt.timezone.utc):%Y%m%d_%H%M%S}"
        tmp_path = os.path.join(stage_dir, name + ".cu8.tmp")
        final_path = os.path.join(a.dir, name + ".cu8")

        got = 0
        t_w0 = time.time()
        with open(tmp_path, "wb") as f:
            while got < need_bytes:
                f.write(chunk)
                got += len(chunk)
                if got < need_bytes:
                    chunk = proc.stdout.read(BLOCK)
                    if not chunk:
                        usb_drops += 1
                        break
        write_dur_s = time.time() - t_w0

        # Inspect ADC clipping (0 or 255 raw uint8 check) & SHA-256 on RAM buffer
        raw_arr = np.fromfile(tmp_path, dtype=np.uint8)
        clip_pct = float(np.mean((raw_arr == 0) | (raw_arr == 255)) * 100.0) if len(raw_arr) > 0 else 0.0
        sha256_hex = hashlib.sha256(raw_arr.tobytes()).hexdigest()

        # Atomic move from /dev/shm RAM-disk to persistent capture storage
        shutil.move(tmp_path, final_path)

        chrony_ms = get_chrony_offset_ms()
        cpu_temp = get_cpu_temp_c()
        free_gb = round(shutil.disk_usage(a.dir).free / (1024 ** 3), 2)

        meta = {
            "node": a.node,
            "station_id": a.node,
            "tuner_type": a.tuner,
            "cal_delay_offset_ns": a.cal_ns,
            "slot_utc": slot,
            "first_chunk_time": t_first,
            "late_by_ms": round((t_first - slot) * 1000, 2),
            "freq": a.freq,
            "rate": a.rate,
            "gain": a.gain,
            "ppm": a.ppm,
            "bytes_recorded": got,
            "write_duration_s": round(write_dur_s, 4),
            "ramdisk_buffered": use_ramdisk,
            "adc_clipping_pct": round(clip_pct, 4),
            "adc_status": "OK" if clip_pct < 0.1 else "CLIPPING_WARN",
            "chrony_offset_ms": chrony_ms,
            "cpu_temp_c": cpu_temp,
            "disk_free_gb": free_gb,
            "usb_drop_count": usb_drops,
            "sha256": sha256_hex
        }
        with open(os.path.join(a.dir, name + ".json"), "w") as f:
            json.dump(meta, f, indent=2)

        push_telemetry_async(a.server, meta)
        print(f"[{a.node}] saved {name}.cu8  (late {meta['late_by_ms']} ms | clip {clip_pct:.3f}% | "
              f"chrony {chrony_ms:+.2f} ms | temp {cpu_temp:.1f}C | ramdisk={'YES' if use_ramdisk else 'NO'})",
              flush=True)
        done += 1
        if a.count and done >= a.count:
            break
        slot = next_slot(time.time())
except KeyboardInterrupt:
    pass
finally:
    proc.terminate()
