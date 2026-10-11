#!/usr/bin/env python3
"""
health_check.py - Field hardware and synchronization diagnostic utility for Pi stations.

Run on any Raspberry Pi station to verify readiness before starting capture runs:
    python3 health_check.py
"""

import json
import os
import shutil
import subprocess
import sys


def check_cpu_temp():
    temp_path = "/sys/class/thermal/thermal_zone0/temp"
    if os.path.exists(temp_path):
        try:
            with open(temp_path) as f:
                temp_c = float(f.read().strip()) / 1000.0
                status = "OK" if temp_c < 75.0 else ("WARNING (High)" if temp_c < 82.0 else "CRITICAL (Throttling)")
                return f"{temp_c:.1f} °C", status
        except Exception:
            pass
    return "Unknown", "N/A"


def check_disk_space(path="."):
    total, used, free = shutil.disk_usage(path)
    free_gb = free / (1024 ** 3)
    status = "OK" if free_gb >= 2.0 else "CRITICAL (Low Disk Space)"
    return f"{free_gb:.2f} GB free", status


def check_rtl_sdr():
    try:
        res = subprocess.run(["rtl_test", "-t"], capture_output=True, text=True, timeout=5)
        out = res.stdout + res.stderr
        if "Found 1 device" in out:
            tuner = "Unknown Tuner"
            for line in out.splitlines():
                if "tuner" in line.lower():
                    tuner = line.strip()
                    break
            return f"Detected ({tuner})", "OK"
        elif "No supported devices" in out:
            return "No device detected", "CRITICAL"
        else:
            return "Device check error", "WARNING"
    except FileNotFoundError:
        return "rtl_test binary not found (compile rtl-sdr-blog)", "CRITICAL"
    except Exception as e:
        return f"Check failed: {e}", "WARNING"


def check_chrony():
    try:
        res = subprocess.run(["chronyc", "tracking"], capture_output=True, text=True, timeout=3)
        if res.returncode == 0:
            offset = None
            for line in res.stdout.splitlines():
                if "System time" in line:
                    parts = line.split()
                    offset = float(parts[2])
                    break
            if offset is not None:
                offset_ms = abs(offset * 1000.0)
                status = "OK" if offset_ms < 10.0 else ("WARNING" if offset_ms < 50.0 else "CRITICAL (Desynced)")
                return f"Offset {offset*1000:.2f} ms", status
        return "Not synchronized", "CRITICAL"
    except FileNotFoundError:
        return "chrony not installed", "CRITICAL"
    except Exception as e:
        return f"Check error: {e}", "WARNING"


def check_config(cfg_path=None):
    if cfg_path is None:
        cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "station_config.json")
    if not os.path.exists(cfg_path):
        return f"Missing {cfg_path}", "CRITICAL"
    try:
        with open(cfg_path) as f:
            cfg = json.load(f)
        node_id = cfg.get("station_id", "UNSET")
        tuner = cfg.get("tuner_type", "UNSET")
        return f"Loaded ({node_id}, {tuner})", "OK"
    except Exception as e:
        return f"Invalid JSON: {e}", "CRITICAL"


def main():
    print("=" * 60)
    print("  TDOA Station Diagnostic Health Check (Raspberry Pi)")
    print("=" * 60)

    checks = [
        ("Configuration", check_config()),
        ("RTL-SDR USB Dongle", check_rtl_sdr()),
        ("Chrony Time Sync", check_chrony()),
        ("Storage Capacity", check_disk_space()),
        ("CPU Temperature", check_cpu_temp()),
    ]

    all_ok = True
    for name, (val, stat) in checks:
        indicator = "[ PASS ]" if stat == "OK" else (f"[ {stat} ]")
        print(f"  {name:<22}: {val:<25} {indicator}")
        if stat == "CRITICAL":
            all_ok = False

    print("=" * 60)
    if all_ok:
        print("  STATUS: READY FOR CAPTURE")
    else:
        print("  STATUS: ACTION REQUIRED BEFORE CAPTURE RUNS")
    print("=" * 60)
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
