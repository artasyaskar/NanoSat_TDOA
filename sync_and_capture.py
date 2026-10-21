#!/usr/bin/env python3
import subprocess
import threading
import time
import os
import sys

HOSTS = {
    "node1": "resolve@192.168.5.229",
    "node2": "resolve@192.168.5.249",
    "node3": "resolve@192.168.5.222"
}

def get_remote_time(host):
    res = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", host, "python3 -c \"import time; print(time.time())\""],
                         capture_output=True, text=True)
    if res.returncode != 0:
        print(f"Error on {host}: {res.stderr}")
    return float(res.stdout.strip())

print("=== 1. Checking Pi Stations Time Sync ===", flush=True)
t_hosts = {}
for n, h in HOSTS.items():
    t_hosts[n] = get_remote_time(h)
    print(f"  {n} ({h}): current epoch = {t_hosts[n]:.3f}", flush=True)

master_now = t_hosts["node2"]
t_target_master = master_now + 3.0
print(f"  Master target physical epoch: {t_target_master:.3f}", flush=True)

sleep_for = {}
for n, h in HOSTS.items():
    offset_from_master = t_hosts[n] - master_now
    s_time = (t_target_master + offset_from_master) - t_hosts[n]
    sleep_for[n] = max(0.2, s_time)
    print(f"  {n}: will sleep {sleep_for[n]:.2f}s before recording", flush=True)

slot_name = "lab_live"
remote_out = {n: f"/dev/shm/{n}_{slot_name}.cu8" for n in HOSTS}

def record_worker(n, h, s_dur):
    # -n 9600000 = 4.0 seconds at 2.4 MS/s, -g 42 for strong SNR
    py_cmd = f"python3 -c 'import time, os; time.sleep({s_dur:.3f}); os.system(\"rtl_sdr -f 433920000 -s 2400000 -g 42 -n 9600000 {remote_out[n]} >/dev/null 2>&1\")'"
    print(f"  [{n}] Recording 4.0s stream at -g 42...", flush=True)
    subprocess.run(["ssh", "-o", "BatchMode=yes", h, py_cmd], capture_output=True, text=True, timeout=15)
    print(f"  [{n}] Capture complete!", flush=True)

threads = []
for n, h in HOSTS.items():
    t = threading.Thread(target=record_worker, args=(n, h, sleep_for[n]))
    threads.append(t)
    t.start()

for t in threads:
    t.join()

print("\n=== 2. Downloading recordings to laptop ===", flush=True)
local_dir = os.path.join(r"d:\NanoSat_TDOA", "captures")
os.makedirs(local_dir, exist_ok=True)
for n, h in HOSTS.items():
    local_file = os.path.join(local_dir, f"{n}_{slot_name}.cu8")
    cmd = ["scp", "-o", "BatchMode=yes", f"{h}:{remote_out[n]}", local_file]
    subprocess.run(cmd, check=True)
    sz = os.path.getsize(local_file)
    print(f"  {n}: downloaded {sz} bytes -> {local_file}", flush=True)

print("\n=== 3. Running TDOA Localization Pipeline with Measured Ground Truth ===", flush=True)
config_path = os.path.join(r"d:\NanoSat_TDOA", "tdoa_kit", "solution1", "config_lab.json")
tdoa_script = os.path.join(r"d:\NanoSat_TDOA", "tdoa_kit", "solution1", "tdoa_process.py")
res = subprocess.run([sys.executable, tdoa_script, "--config", config_path, "--dir", local_dir, "--slot", slot_name],
                     capture_output=True, text=True)
print(res.stdout, flush=True)
if res.stderr:
    print("STDERR:", res.stderr, flush=True)
