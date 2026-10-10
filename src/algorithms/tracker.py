"""
tracker.py - Extended Kalman Filter (EKF) Kinematic Target Tracking Filter.

Tracks moving targets (NanoSat orbital passes, tactical UAVs, ground transmitters)
across sequential TDOA capture epochs. Estimates full 4D state vector:
  x = [px, py, vx, vy]^T
with continuous covariance propagation, velocity heading, and predictive trajectory smoothing.
"""

import numpy as np

C_LIGHT = 299_792_458.0


class TargetEKFTracker:
    def __init__(self, q_accel=0.5, sigma_meas_m=1.0):
        """
        q_accel: continuous acceleration spectral density (process noise intensity, m^2/s^3)
        sigma_meas_m: standard deviation of range difference measurements (meters)
        """
        self.q = q_accel
        self.r_var = sigma_meas_m ** 2
        self.x = None  # State: [px, py, vx, vy]
        self.P = None  # Covariance matrix 4x4
        self.last_timestamp = None
        self.track_history = []

    def initialize(self, p_init, v_init=(0.0, 0.0), p_cov=100.0, v_cov=25.0, t0=0.0):
        """Initialize filter state and covariance."""
        self.x = np.array([p_init[0], p_init[1], v_init[0], v_init[1]], dtype=float)
        self.P = np.diag([p_cov, p_cov, v_cov, v_cov])
        self.last_timestamp = t0
        self.track_history = [{
            "timestamp": t0,
            "x": float(self.x[0]),
            "y": float(self.x[1]),
            "vx": float(self.x[2]),
            "vy": float(self.x[3]),
            "speed_mps": float(np.hypot(self.x[2], self.x[3])),
            "heading_deg": float(np.degrees(np.arctan2(self.x[3], self.x[2]))) % 360.0,
            "pos_std_m": float(np.sqrt(self.P[0, 0] + self.P[1, 1]))
        }]

    def predict(self, dt):
        """Kinematic state prediction over time interval dt (seconds)."""
        if self.x is None:
            return

        # State transition matrix F
        F = np.array([
            [1.0, 0.0,  dt, 0.0],
            [0.0, 1.0, 0.0,  dt],
            [0.0, 0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0, 1.0]
        ])

        # Discrete process noise covariance Q (Piecewise Constant White Acceleration)
        dt2 = dt * dt
        dt3 = dt2 * dt / 2.0
        dt4 = dt2 * dt2 / 4.0
        Q = self.q * np.array([
            [dt4, 0.0, dt3, 0.0],
            [0.0, dt4, 0.0, dt3],
            [dt3, 0.0, dt2, 0.0],
            [0.0, dt3, 0.0, dt2]
        ])

        self.x = F @ self.x
        self.P = F @ self.P @ F.T + Q

    def update_with_tdoa(self, node_xy, pair_dd, timestamp=None):
        """
        Extended Kalman Filter measurement update directly using raw TDOA range differences.
        Measurement model: z_k = h(x) + v
        where h_ij(x) = hypot(x - sj) - hypot(x - si)
        """
        if timestamp is not None and self.last_timestamp is not None:
            dt = max(0.001, timestamp - self.last_timestamp)
            self.predict(dt)
            self.last_timestamp = timestamp

        pairs = list(pair_dd.keys())
        m = len(pairs)
        z = np.array([pair_dd[p] for p in pairs], dtype=float)

        # Predicted measurements h(x) and Jacobian H
        px, py = self.x[0], self.x[1]
        h_x = np.zeros(m)
        H = np.zeros((m, 4))

        for idx, (base, other) in enumerate(pairs):
            si = np.array(node_xy[base], dtype=float)
            sj = np.array(node_xy[other], dtype=float)
            di = np.hypot(px - si[0], py - si[1])
            dj = np.hypot(px - sj[0], py - sj[1])
            di = max(di, 0.01)
            dj = max(dj, 0.01)

            h_x[idx] = dj - di
            # Partial derivatives with respect to [px, py, vx, vy]
            grad_x = (px - sj[0]) / dj - (px - si[0]) / di
            grad_y = (py - sj[1]) / dj - (py - si[1]) / di
            H[idx, 0] = grad_x
            H[idx, 1] = grad_y
            H[idx, 2] = 0.0
            H[idx, 3] = 0.0

        # Measurement noise covariance R
        R = np.eye(m) * self.r_var

        # Innovation (residual)
        y = z - h_x
        S = H @ self.P @ H.T + R
        K = self.P @ H.T @ np.linalg.pinv(S)

        # State and covariance update
        self.x = self.x + K @ y
        I = np.eye(4)
        self.P = (I - K @ H) @ self.P @ (I - K @ H).T + K @ R @ K.T  # Joseph form for numerical stability

        speed = float(np.hypot(self.x[2], self.x[3]))
        heading = float(np.degrees(np.arctan2(self.x[3], self.x[2]))) % 360.0
        pos_std = float(np.sqrt(max(0.0, self.P[0, 0] + self.P[1, 1])))

        state_record = {
            "timestamp": timestamp,
            "x": float(self.x[0]),
            "y": float(self.x[1]),
            "vx": float(self.x[2]),
            "vy": float(self.x[3]),
            "speed_mps": speed,
            "speed_kmh": speed * 3.6,
            "heading_deg": heading,
            "pos_std_m": pos_std
        }
        self.track_history.append(state_record)
        return state_record
