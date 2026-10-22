"""
live_state.py - Live tracking engine and state coordinator.

Defines state machine and history buffer for mission control:
  - SIMULATED : synthetic / math-model generated
  - REPLAY    : offline replay of saved raw IQ recordings
  - LIVE_HW   : live SDR hardware capture (strictly guarded; disabled without hardware sign-off)
  - STALE     : fix exists but age exceeds stale_timeout_s
  - NO_FIX    : capture or solver rejected current measurement

Ensures:
  1. No silent fallbacks to simulation when in live or replay modes.
  2. Fix age is tracked and presented with every fix.
  3. Never re-labels an old fix as a fresh measurement.
"""
from collections import deque
import threading
import time
from typing import Any, Dict, List, Optional


class LiveStateError(Exception):
    """Raised for invalid state transitions or unauthorized hardware access."""


class LiveEngine:
    def __init__(self, stale_timeout_s: float = 15.0, history_len: int = 100):
        self.stale_timeout_s = float(stale_timeout_s)
        self.history_len = int(history_len)
        self._lock = threading.Lock()

        # Engine state
        self.is_running = False
        self.source = "SIMULATED"  # "SIMULATED", "REPLAY", "LIVE_HW"
        self.start_time: Optional[float] = None
        self.stop_time: Optional[float] = None

        # Fix tracking
        self.latest_record: Optional[Dict[str, Any]] = None
        self.last_fix_time: Optional[float] = None
        self.history: deque = deque(maxlen=self.history_len)

        # Statistics
        self.total_cycles = 0
        self.total_fixes = 0
        self.total_failures = 0
        self.total_rejected_pairs = 0

        # Safety gate for live hardware
        self.hardware_enabled = False

    def start(self, source: str = "SIMULATED", allow_live_hw: bool = False,
              config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Start the live engine in the specified mode."""
        with self._lock:
            src_upper = source.upper()
            if src_upper == "LIVE_HW":
                if not (allow_live_hw and self.hardware_enabled):
                    raise LiveStateError(
                        "LIVE_HW mode is disabled. Over-the-air RF hardware capture requires "
                        "explicit physical authorization and verification."
                    )

            if src_upper not in ("SIMULATED", "REPLAY", "LIVE_HW"):
                raise LiveStateError(f"Invalid engine source: {source}")

            self.source = src_upper
            self.is_running = True
            self.start_time = time.time()
            return {
                "running": True,
                "source": self.source,
                "started_at": self.start_time,
                "stale_timeout_s": self.stale_timeout_s,
            }

    def stop(self) -> Dict[str, Any]:
        """Stop the live engine."""
        with self._lock:
            self.is_running = False
            self.stop_time = time.time()
            return {
                "running": False,
                "source": self.source,
                "stopped_at": self.stop_time,
                "total_cycles": self.total_cycles,
                "total_fixes": self.total_fixes,
                "total_failures": self.total_failures,
            }

    def submit_fix(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """
        Record a solver or replay cycle.
        record should contain:
          - data_source: "SIMULATED", "REPLAY", or "LIVE_HW"
          - status: "FIX" or "NO_FIX"
          - reason: optional failure reason
          - position_xy: [x, y] if FIX
          - residual_rms_m: float
          - pairs: list of pair details
        """
        now = time.time()
        with self._lock:
            self.total_cycles += 1
            status = record.get("status", "NO_FIX")

            entry = dict(record)
            entry["timestamp"] = now
            entry["cycle_id"] = self.total_cycles

            # Check rejected pairs
            pairs = record.get("pairs", [])
            rejected = sum(1 for p in pairs if p.get("status") in ("FAILED", "REJECTED"))
            entry["rejected_pairs"] = rejected
            self.total_rejected_pairs += rejected

            if status == "FIX":
                self.total_fixes += 1
                self.last_fix_time = now
                entry["is_stale"] = False
            else:
                self.total_failures += 1
                # If current measurement is NO_FIX, do not update last_fix_time
                entry["is_stale"] = True

            self.latest_record = entry
            self.history.append(entry)
            return entry

    def get_latest(self) -> Dict[str, Any]:
        """Return the latest record with real-time fix age and freshness status."""
        now = time.time()
        with self._lock:
            if self.latest_record is None:
                return {
                    "source": self.source,
                    "status": "NO_MEASUREMENT",
                    "reason": "Engine has not processed any measurements yet.",
                    "is_running": self.is_running,
                    "position_xy": None,
                    "fix_age_s": None,
                    "is_stale": True,
                }

            rec = dict(self.latest_record)
            if self.last_fix_time is not None:
                fix_age = now - self.last_fix_time
                rec["fix_age_s"] = round(fix_age, 2)
                if fix_age > self.stale_timeout_s:
                    rec["is_stale"] = True
                    if rec["status"] == "FIX":
                        rec["state_display"] = "STALE"
                    else:
                        rec["state_display"] = "NO_FIX"
                else:
                    rec["is_stale"] = False
                    rec["state_display"] = rec["status"]
            else:
                rec["fix_age_s"] = None
                rec["is_stale"] = True
                rec["state_display"] = "NO_FIX"

            rec["is_running"] = self.is_running
            return rec

    def get_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Return recent tracking history."""
        with self._lock:
            items = list(self.history)[-limit:]
            now = time.time()
            res = []
            for item in items:
                it = dict(item)
                it["age_s"] = round(now - it.get("timestamp", now), 2)
                res.append(it)
            return res

    def get_status(self) -> Dict[str, Any]:
        """Return engine operational status."""
        now = time.time()
        with self._lock:
            uptime = (now - self.start_time) if (self.is_running and self.start_time) else 0.0
            return {
                "is_running": self.is_running,
                "source": self.source,
                "uptime_s": round(uptime, 1),
                "total_cycles": self.total_cycles,
                "total_fixes": self.total_fixes,
                "total_failures": self.total_failures,
                "total_rejected_pairs": self.total_rejected_pairs,
                "hardware_enabled": self.hardware_enabled,
                "stale_timeout_s": self.stale_timeout_s,
            }
