"""Audit helper: compare config_lab.json / app.py geometry with the tape table in the report."""
import os, sys
import numpy as np
ROOT = r"d:\NanoSat_TDOA"
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "tdoa_kit", "common"))
from tdoa_lib import load_config

cfg = load_config(os.path.join(ROOT, "tdoa_kit", "solution1", "config_lab.json"))
n = cfg["_node_xy"]; ref = cfg["_ref_xy"]; tr = cfg["_truth_xy"]
d = lambda a, b: float(np.hypot(*(np.asarray(a) - np.asarray(b))))
print("config_lab.json local XY (m):")
for k, v in n.items(): print(f"  {k}: ({v[0]:+.3f}, {v[1]:+.3f})")
print(f"  ref  : ({ref[0]:+.3f}, {ref[1]:+.3f})  truth: ({tr[0]:+.3f}, {tr[1]:+.3f})")
tape = {"S1-S2": 8.026, "S1-S3": 6.198, "S2-S3": 4.978, "P-S1": 2.184, "P-S2": 5.969, "T-S1": 2.489, "T-S2": 5.715}
got = {"S1-S2": d(n["node1"], n["node2"]), "S1-S3": d(n["node1"], n["node3"]), "S2-S3": d(n["node2"], n["node3"]),
       "P-S1": d(ref, n["node1"]), "P-S2": d(ref, n["node2"]), "T-S1": d(tr, n["node1"]), "T-S2": d(tr, n["node2"])}
print("\ndistance            tape(report)   config_lab   diff(cm)")
for k in tape: print(f"  {k:6s}            {tape[k]:8.3f}     {got[k]:8.3f}   {100*(got[k]-tape[k]):+7.1f}")
print(f"\nNOT in tape table (no redundancy check possible): P-S3 = {d(ref, n['node3']):.3f} m (config), T-S3 = {d(tr, n['node3']):.3f} m (config)")

# Expected true range differences from the config ground truth
print("\nGround-truth range differences from config_lab (target):")
for a, b in (("node1", "node2"), ("node1", "node3"), ("node2", "node3")):
    dd = d(tr, n[b]) - d(tr, n[a]); rg = d(ref, n[b]) - d(ref, n[a])
    print(f"  {a}-{b}: target dd {dd:+.3f} m ({dd/299792458*1e9:+.2f} ns)   reference dd {rg:+.3f} m ({rg/299792458*1e9:+.2f} ns)  baseline {d(n[a], n[b]):.3f} m")
print("  Max physically possible |dd| for 8.026/6.198/4.978 m baselines = 26.8 / 20.7 / 16.6 ns")
# sample period
print(f"\nOne RTL sample at 2.4 MS/s = {1e9/2.4e6:.1f} ns = {299792458/2.4e6:.1f} m; baseline S2-S3 is only {d(n['node2'], n['node3']):.2f} m = {d(n['node2'], n['node3'])/(299792458/2.4e6):.3f} sample")
