#!/usr/bin/env python3
"""
upload_all_stations.py - One-Click Deployment Script for All 3 Ground Stations.

Deploys updated software, configs, and setup scripts to:
  - Station 1 (Alpha):   192.168.5.248 (RTL-SDR V3 R820T2)
  - Station 2 (Bravo):   192.168.5.249 (RTL-SDR V3 R820T2)
  - Station 3 (Charlie): 192.168.5.250 (RTL-SDR Blog V4 R828D)
"""

import os
import sys
import paramiko

STATIONS = [
    {
        "id": "node1",
        "name": "Station 1 (Alpha)",
        "ip": "192.168.5.248",
        "config": r"d:\NanoSat_TDOA\configs\stations\station1_node1.json",
    },
    {
        "id": "node2",
        "name": "Station 2 (Bravo)",
        "ip": "192.168.5.249",
        "config": r"d:\NanoSat_TDOA\configs\stations\station2_node2.json",
    },
    {
        "id": "node3",
        "name": "Station 3 (Charlie)",
        "ip": "192.168.5.250",
        "config": r"d:\NanoSat_TDOA\configs\stations\station3_node3.json",
    },
]

POSSIBLE_USERS = [
    ("resolve", "resolve"),
    ("pi", "raspberry"),
    ("pi", "resolve"),
    ("admin", "admin"),
]

FILES_TO_COPY = [
    (r"d:\NanoSat_TDOA\tdoa_kit\solution1\rtl_recorder.py", "rtl_recorder.py"),
    (r"d:\NanoSat_TDOA\deploy\pi\station_daemon.py", "station_daemon.py"),
    (r"d:\NanoSat_TDOA\deploy\pi\health_check.py", "health_check.py"),
    (r"d:\NanoSat_TDOA\deploy\pi\setup_pi.sh", "setup_pi.sh"),
    (r"d:\NanoSat_TDOA\tdoa_kit\solution1\pi_setup.sh", "pi_setup.sh"),
]

PUBKEY_PATH = r"C:\Users\DELL\.ssh\id_ed25519.pub"


def try_connect(ip):
    """Attempt SSH connection via key first, then fallback to credential list."""
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    # Try key-based login first for known users
    for user, _ in POSSIBLE_USERS:
        try:
            ssh.connect(ip, username=user, timeout=3, look_for_keys=True)
            return ssh, user
        except Exception:
            pass

    # Try password login
    for user, pwd in POSSIBLE_USERS:
        try:
            ssh.connect(ip, username=user, password=pwd, timeout=3, look_for_keys=False)
            return ssh, user
        except Exception:
            pass

    return None, None


def deploy_station(st):
    ip = st["ip"]
    name = st["name"]
    print(f"\n=======================================================")
    print(f"Connecting to {name} at {ip}...")
    ssh, user = try_connect(ip)
    if ssh is None:
        print(f"[FAIL] Could not connect to {ip}. Ensure Pi is powered on and connected to the network.")
        return False

    print(f"[OK] Connected to {name} as '{user}'.")

    # Install public key if available
    if os.path.exists(PUBKEY_PATH):
        with open(PUBKEY_PATH, "r", encoding="utf-8") as f:
            pubkey = f.read().strip()
        cmd_key = (
            f"mkdir -p ~/.ssh && "
            f"grep -qxF '{pubkey}' ~/.ssh/authorized_keys 2>/dev/null || echo '{pubkey}' >> ~/.ssh/authorized_keys && "
            f"chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys"
        )
        ssh.exec_command(cmd_key)

    sftp = ssh.open_sftp()
    remote_base = f"/home/{user}/tdoa"
    for rdir in [remote_base, f"{remote_base}/captures"]:
        try:
            sftp.mkdir(rdir)
        except IOError:
            pass

    # Upload common files
    for local_p, remote_name in FILES_TO_COPY:
        if os.path.exists(local_p):
            target = f"{remote_base}/{remote_name}"
            sftp.put(local_p, target)
            print(f"  Uploaded: {remote_name}")

    # Upload station specific config as station_config.json
    if os.path.exists(st["config"]):
        sftp.put(st["config"], f"{remote_base}/station_config.json")
        print(f"  Uploaded: station_config.json ({os.path.basename(st['config'])})")

    # Make executable
    ssh.exec_command(f"chmod +x {remote_base}/*.py {remote_base}/*.sh")
    print(f"[SUCCESS] {name} deployed cleanly!")
    ssh.close()
    return True


def main():
    print("=" * 60)
    print("   NanoSat TDOA - Ground Station Automatic Deployment")
    print("=" * 60)
    results = {}
    for st in STATIONS:
        results[st["id"]] = deploy_station(st)

    print("\n" + "=" * 60)
    print("               DEPLOYMENT SUMMARY")
    print("=" * 60)
    for st in STATIONS:
        status = "DEPLOYED [OK]" if results[st["id"]] else "OFFLINE [FAIL]"
        print(f"  {st['name']:<25} ({st['ip']}): {status}")
    print("=" * 60)


if __name__ == "__main__":
    main()
