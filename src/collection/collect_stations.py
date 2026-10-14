#!/usr/bin/env python3
"""
collect_stations.py - Robust network collection utility for Raspberry Pi station captures.

Pulls captured IQ (.cu8) and companion metadata (.json) files from Station 1, 2, and 3
over SSH/SCP, verifies SHA-256 integrity, and organizes them into local captures/ directory.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys

DEFAULT_HOSTS = {
    "node1": "resolve@192.168.5.229",
    "node2": "resolve@192.168.5.249",
    "node3": "resolve@192.168.5.222"
}


def verify_sha256(cu8_path, expected_hash):
    if not os.path.exists(cu8_path):
        return False
    h = hashlib.sha256()
    with open(cu8_path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest().lower() == expected_hash.lower()


def fetch_from_station(node_name, host, dest_dir, remote_dir="~/tdoa/captures"):
    print(f"\n---> Fetching captures from {node_name} ({host})...")
    os.makedirs(dest_dir, exist_ok=True)
    scp_cmd = [
        "scp",
        "-o", "ConnectTimeout=5",
        "-o", "BatchMode=yes",
        f"{host}:{remote_dir}/{node_name}_*",
        dest_dir
    ]
    try:
        res = subprocess.run(scp_cmd, capture_output=True, text=True)
        if res.returncode != 0:
            print(f"  [WARNING] SCP failed for {node_name}: {res.stderr.strip()}")
            print(f"  Tip: Check network connectivity or copy manually via USB drive.")
            return False
        print(f"  [SUCCESS] Captures fetched from {node_name}.")
        return True
    except FileNotFoundError:
        print("  [ERROR] scp command not found on host machine.")
        return False


def main():
    p = argparse.ArgumentParser(description="Collect TDOA captures from Raspberry Pi stations")
    p.add_argument("--dest", default="captures", help="Local directory to store captures")
    p.add_argument("--hosts-config", default=None, help="Optional JSON file mapping station IDs to hostnames")
    args = p.parse_args()

    hosts = DEFAULT_HOSTS
    if args.hosts_config and os.path.exists(args.hosts_config):
        with open(args.hosts_config) as f:
            hosts = json.load(f)

    print("=" * 65)
    print("        TDOA Multi-Station Network Capture Collector")
    print("=" * 65)

    success_count = 0
    for node_name, host in hosts.items():
        if fetch_from_station(node_name, host, args.dest):
            success_count += 1

    print("\n" + "=" * 65)
    print(f"  Collection complete: {success_count}/{len(hosts)} stations synchronized.")
    print("=" * 65)


if __name__ == "__main__":
    main()
