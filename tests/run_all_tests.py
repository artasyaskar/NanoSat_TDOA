"""
run_all_tests.py - In-process master automated test runner for Phase A verification.
"""

import os
import sys
import time

# Ensure workspace root and test folders in sys.path
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, "tests", "synthetic"))

sys.path.insert(0, os.path.join(BASE_DIR, "tests", "algorithms"))

from test_pipeline_baseline import run_baseline_test
from test_clock_skew import run_clock_skew_test
from test_fractional_delay import run_fractional_delay_test
from test_reference_geometry import run_ref_geometry_test
from test_advanced_algorithms import run_advanced_algorithms_test


def main():
    print("=" * 70, flush=True)
    print("      NanoSat_TDOA Phase A Software-Only Automated Test Suite", flush=True)
    print("=" * 70, flush=True)

    suite = [
        ("Baseline Pipeline Accuracy", run_baseline_test),
        ("Clock Skew & Reference Rejection", run_clock_skew_test),
        ("Sub-Sample S-Curve Interpolation", run_fractional_delay_test),
        ("Reference Survey Geometry Sensitivity", run_ref_geometry_test),
        ("Advanced Multilateration, EKF & GDOP", run_advanced_algorithms_test),
    ]

    results = []
    all_passed = True
    t_start = time.time()

    for name, test_fn in suite:
        print(f"\n---> Running: {name}", flush=True)
        t0 = time.time()
        try:
            test_fn()
            elapsed = time.time() - t0
            results.append((name, True, f"{elapsed:.2f}s"))
            print(f"  [ PASS ] {name} completed in {elapsed:.2f}s", flush=True)
        except Exception as e:
            elapsed = time.time() - t0
            results.append((name, False, f"FAILED: {e}"))
            print(f"  [ FAIL ] {name}: {e}", flush=True)
            all_passed = False

    t_total = time.time() - t_start
    print("\n" + "=" * 70, flush=True)
    print("                         SUMMARY OF RESULTS", flush=True)
    print("=" * 70, flush=True)
    for name, passed, detail in results:
        status = "[ PASS ]" if passed else "[ FAIL ]"
        print(f"  {name:<42} {status}  ({detail})", flush=True)
    print("=" * 70, flush=True)
    print(f"  Total Test Suite Duration: {t_total:.2f}s", flush=True)

    if all_passed:
        print("  ALL TESTS PASSED: PHASE A SOFTWARE VERIFICATION COMPLETE.", flush=True)
        return 0
    else:
        print("  SOME TESTS FAILED: INVESTIGATION REQUIRED.", flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
