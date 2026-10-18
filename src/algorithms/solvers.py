"""
solvers.py - Research-Grade Multi-Algorithm Localization Engine for TDOA.

Implements state-of-the-art hyperbolic positioning algorithms:
  1. Fang's Exact Analytic Algorithm (Fang, IEEE Trans. AES, 1990):
     Exact closed-form quadratic solution for 3-station arrays (eliminates matrix singularity).
  2. Chan-Ho Algorithm (Chan & Ho, IEEE Trans. SP, 1994):
     Closed-form two-step weighted least-squares solver for overdetermined arrays (M >= 4).
  3. Taylor-Series Iterative WLS (Foy, IEEE Trans. AES, 1976):
     Gauss-Newton with robust Huber loss for multipath outlier rejection.
  4. Cramer-Rao Lower Bound (CRLB) covariance & CEP-95 Error Ellipse generator.
"""

import numpy as np
from scipy.optimize import least_squares
from scipy.stats import chi2

C_LIGHT = 299_792_458.0


# ----------------------------------------------------------------------------
# 1. Theoretical Cramer-Rao Lower Bound (CRLB)
# ----------------------------------------------------------------------------
def compute_crlb(node_xy, target_xy, sigma_tdoa_s=5e-9):
    """
    Computes theoretical 2D Cramer-Rao Lower Bound (CRLB) covariance matrix:
      J = H^T * Q^{-1} * H
      Cov(p) >= J^{-1}
    where H is the hyperbolic Jacobian and Q is the TDOA noise covariance matrix.
    """
    nodes = list(node_xy.values())
    m = len(nodes)
    if m < 3:
        raise ValueError("At least 3 receiver stations required for 2D CRLB.")

    p = np.array(target_xy, dtype=float)
    ranges = np.array([np.hypot(p[0] - n[0], p[1] - n[1]) for n in nodes])

    H = np.zeros((m - 1, 2))
    u0 = (p - nodes[0]) / ranges[0]
    for i in range(1, m):
        ui = (p - nodes[i]) / ranges[i]
        H[i - 1, :] = (ui - u0) / C_LIGHT

    var = sigma_tdoa_s ** 2
    Q = np.full((m - 1, m - 1), var) + np.eye(m - 1) * var

    try:
        Q_inv = np.linalg.inv(Q)
        FIM = H.T @ Q_inv @ H
        cov = np.linalg.inv(FIM)
    except np.linalg.LinAlgError:
        cov = np.full((2, 2), np.nan)

    return cov


def get_error_ellipse(cov, p_val=0.95):
    """Calculate semi-major, semi-minor axes and tilt angle of confidence ellipse."""
    if np.any(np.isnan(cov)):
        return 0.0, 0.0, 0.0
    eigvals, eigvecs = np.linalg.eigh(cov)
    eigvals = np.maximum(eigvals, 1e-12)
    order = eigvals.argsort()[::-1]
    eigvals, eigvecs = eigvals[order], eigvecs[:, order]

    k = np.sqrt(chi2.ppf(p_val, df=2))
    a = k * np.sqrt(eigvals[0])
    b = k * np.sqrt(eigvals[1])
    angle_deg = np.degrees(np.arctan2(eigvecs[1, 0], eigvecs[0, 0]))
    return float(a), float(b), float(angle_deg)


# ----------------------------------------------------------------------------
# 2. Fang's Exact Analytic Closed-Form Solver (M = 3 Stations)
# ----------------------------------------------------------------------------
def solve_fang(node_xy, pair_dd):
    """
    Fang (1990) exact closed-form analytic solver for 3-station 2D multilateration.
    Solves the intersection of two hyperbolas via rotation into local canonical frame.
    Zero iterative search; exact to machine precision under noiseless conditions.
    """
    names = list(node_xy.keys())
    s1 = np.array(node_xy[names[0]], dtype=float)
    s2 = np.array(node_xy[names[1]], dtype=float)
    s3 = np.array(node_xy[names[2]], dtype=float)

    # Get range differences relative to station 1:
    # d21 = dist(target, s2) - dist(target, s1)
    # d31 = dist(target, s3) - dist(target, s1)
    def get_dd(na, nb):
        if (na, nb) in pair_dd:
            return pair_dd[(na, nb)]
        elif (nb, na) in pair_dd:
            return -pair_dd[(nb, na)]
        for other in names:
            if other != na and other != nb:
                has_first = (na, other) in pair_dd or (other, na) in pair_dd
                has_second = (other, nb) in pair_dd or (nb, other) in pair_dd
                if has_first and has_second:
                    return get_dd(na, other) + get_dd(other, nb)
        raise KeyError(f"Pair ({na}, {nb}) not found in range differences")

    d21 = get_dd(names[0], names[1])
    d31 = get_dd(names[0], names[2])

    # Rotate coordinate system so S1 is at (0, 0) and S2 is on the positive X-axis
    dx21 = s2[0] - s1[0]
    dy21 = s2[1] - s1[1]
    x2 = np.hypot(dx21, dy21)
    if x2 < 1e-6:
        return None, 1e6

    alpha = np.arctan2(dy21, dx21)
    cos_a, sin_a = np.cos(alpha), np.sin(alpha)
    R = np.array([[cos_a, sin_a], [-sin_a, cos_a]])

    # Local coordinates of S3
    s3_local = R @ (s3 - s1)
    x3, y3 = s3_local[0], s3_local[1]
    if abs(y3) < 1e-6:
        # Collinear sensors
        return None, 1e6

    # Hyperbola 1 (Station 2): x = g * r1 + h
    g = -d21 / x2
    h = (x2 ** 2 - d21 ** 2) / (2.0 * x2)

    # Hyperbola 2 (Station 3): y = e * r1 + f
    e = -(d31 + x3 * g) / y3
    f = (x3 ** 2 + y3 ** 2 - d31 ** 2 - 2.0 * x3 * h) / (2.0 * y3)

    # Quadratic equation: A * r1^2 + B * r1 + C = 0
    A = g ** 2 + e ** 2 - 1.0
    B = 2.0 * (g * h + e * f)
    C = h ** 2 + f ** 2

    # Solve quadratic
    discriminant = B ** 2 - 4.0 * A * C
    if discriminant < 0:
        # Complex roots: fallback to Taylor-series
        return None, 1e6

    sqrt_disc = np.sqrt(discriminant)
    candidate_r1 = []
    if abs(A) < 1e-9:
        if abs(B) > 1e-9:
            candidate_r1.append(-C / B)
    else:
        candidate_r1.append((-B + sqrt_disc) / (2.0 * A))
        candidate_r1.append((-B - sqrt_disc) / (2.0 * A))

    best_pos = None
    best_res = 1e9

    for r1 in candidate_r1:
        if r1 <= 0:
            continue
        x_loc = g * r1 + h
        y_loc = e * r1 + f
        pos_global = s1 + R.T @ np.array([x_loc, y_loc])

        # Evaluate residual
        d21_calc = np.hypot(*(pos_global - s2)) - np.hypot(*(pos_global - s1))
        d31_calc = np.hypot(*(pos_global - s3)) - np.hypot(*(pos_global - s1))
        res = (d21_calc - d21) ** 2 + (d31_calc - d31) ** 2

        if res < best_res:
            best_res = res
            best_pos = pos_global

    if best_pos is None:
        return None, 1e6

    return best_pos, float(np.sqrt(best_res / 2.0))


# ----------------------------------------------------------------------------
# 3. Chan-Ho Closed-Form Analytical Algorithm (M >= 4 Stations)
# ----------------------------------------------------------------------------
def solve_chan_ho(node_xy, pair_dd, sigma_m=1.0):
    """
    Chan & Ho (1994) two-step WLS solver for overdetermined arrays (M >= 4).
    If M == 3, automatically delegates to Fang's exact closed-form solver.
    """
    if len(node_xy) == 3:
        return solve_fang(node_xy, pair_dd)

    station_names = list(node_xy.keys())
    base_name = station_names[0]
    s0 = np.array(node_xy[base_name], dtype=float)
    other_names = station_names[1:]
    M = len(other_names) + 1

    r_i0 = []
    si_list = []
    for name in other_names:
        key = (base_name, name)
        rev_key = (name, base_name)
        if key in pair_dd:
            dd = pair_dd[key]
        elif rev_key in pair_dd:
            dd = -pair_dd[rev_key]
        else:
            raise KeyError(f"Missing range difference for pair {base_name}-{name}")
        r_i0.append(dd)
        si_list.append(np.array(node_xy[name], dtype=float))

    r_i0 = np.array(r_i0, dtype=float)
    S = np.array(si_list, dtype=float) - s0
    K = np.sum(S ** 2, axis=1)

    G = np.column_stack((-S, -r_i0))
    h = 0.5 * (r_i0 ** 2 - K)

    W = np.eye(M - 1) / (sigma_m ** 2)
    try:
        theta = np.linalg.pinv(G.T @ W @ G) @ G.T @ W @ h
    except np.linalg.LinAlgError:
        return None, 1e6

    r0_est = abs(theta[2])
    B = np.diag(np.maximum(r0_est + r_i0, 0.1))
    Cov_psi = B @ (np.full((M - 1, M - 1), 0.5) + np.eye(M - 1) * 0.5) @ B * (sigma_m ** 2)
    W_refined = np.linalg.pinv(Cov_psi)

    theta = np.linalg.pinv(G.T @ W_refined @ G) @ G.T @ W_refined @ h
    pos_est = theta[:2] + s0

    res = 0.0
    for name, dd in zip(other_names, r_i0):
        d_calc = np.hypot(*(pos_est - node_xy[name])) - np.hypot(*(pos_est - node_xy[base_name]))
        res += (d_calc - dd) ** 2
    rms_res = float(np.sqrt(res / len(r_i0)))

    return pos_est, rms_res


# ----------------------------------------------------------------------------
# 4. Taylor-Series Iterative Weighted Least Squares (Foy)
# ----------------------------------------------------------------------------
def solve_taylor_series(node_xy, pair_dd, p_init=None, max_iter=20, tol_m=1e-3, huber_delta=2.0):
    """
    Taylor-series (Gauss-Newton) iterative solver with robust Huber loss weighting.
    Provides fast asymptotic quadratic convergence to the local maximum likelihood solution.
    """
    pairs = list(pair_dd.keys())
    centroid = np.mean(np.array(list(node_xy.values())), axis=0)
    p = centroid.copy() if p_init is None else np.array(p_init, dtype=float)

    for iteration in range(max_iter):
        residuals = []
        H = []

        for base, other in pairs:
            si = np.array(node_xy[base], dtype=float)
            sj = np.array(node_xy[other], dtype=float)
            di = np.hypot(p[0] - si[0], p[1] - si[1])
            dj = np.hypot(p[0] - sj[0], p[1] - sj[1])
            di = max(di, 0.01)
            dj = max(dj, 0.01)

            modeled_dd = dj - di
            meas_dd = pair_dd[(base, other)]
            res = meas_dd - modeled_dd
            residuals.append(res)

            grad_x = (p[0] - sj[0]) / dj - (p[0] - si[0]) / di
            grad_y = (p[1] - sj[1]) / dj - (p[1] - si[1]) / di
            H.append([grad_x, grad_y])

        residuals = np.array(residuals)
        H = np.array(H)

        abs_res = np.abs(residuals)
        weights = np.where(abs_res <= huber_delta, 1.0, huber_delta / (abs_res + 1e-12))
        W = np.diag(weights)

        try:
            delta = np.linalg.pinv(H.T @ W @ H) @ H.T @ W @ residuals
        except np.linalg.LinAlgError:
            break

        p += delta
        if np.linalg.norm(delta) < tol_m:
            break

    rms_res = float(np.sqrt(np.mean(residuals ** 2)))
    return p, rms_res


# ----------------------------------------------------------------------------
# 5. ADRA-TDOA 3-Stage Hybrid Solver (Coarse Grid + Analytic + Huber-IRLS + LM)
# ----------------------------------------------------------------------------
def solve_adra_3stage(node_xy, pair_dd, pair_w=None, tracker=None, timestamp=None,
                      search_radius=1200.0, grid_step=15.0, sigma_tdoa_s=2.5e-9):
    """
    ADRA-TDOA 3-Stage Positioning & Tracking Engine (Innovation 5):
      Stage A: Coarse Global Grid + Fang (3-node) / Chan-Ho (4+ node) Closed-Form Initialization.
      Stage B: Iteratively Reweighted Least Squares (IRLS) with Huber Loss +
               Trust-Region Levenberg-Marquardt Non-Linear Polish.
      Stage C: Constant-Velocity Extended Kalman Filter (CV-EKF) update +
               Analytical CRLB Covariance & 95% CEP Error Ellipse.
    """
    pairs = list(pair_dd.keys())
    w_arr = np.ones(len(pairs), dtype=float) if pair_w is None else np.array([pair_w[p] for p in pairs], dtype=float)
    w_arr = w_arr / (np.max(w_arr) + 1e-12)

    # Adapt search radius and grid step for compact arrays (e.g. 10m lab testbench)
    nodes_arr = np.array(list(node_xy.values()))
    span = float(np.max(np.ptp(nodes_arr, axis=0))) if len(nodes_arr) > 1 else 100.0
    if span < 100.0:
        search_radius = max(span * 3.0, 30.0)
        grid_step = max(search_radius / 300.0, 0.1)

    centroid = np.mean(nodes_arr, axis=0)
    gx = np.arange(centroid[0] - search_radius, centroid[0] + search_radius, grid_step)
    gy = np.arange(centroid[1] - search_radius, centroid[1] + search_radius, grid_step)
    X, Y = np.meshgrid(gx, gy)
    cost = np.zeros_like(X)
    for wk, (i, j) in zip(w_arr, pairs):
        xi, xj = node_xy[i], node_xy[j]
        m = np.hypot(X - xj[0], Y - xj[1]) - np.hypot(X - xi[0], Y - xi[1])
        cost += wk * (m - pair_dd[(i, j)]) ** 2
    r_idx, c_idx = np.unravel_index(np.argmin(cost), cost.shape)
    p_grid = np.array([gx[c_idx], gy[r_idx]], dtype=float)

    # Stage A.2: Fang / Chan-Ho Analytic Closed-Form Initialization
    try:
        p_analytic, res_analytic = solve_chan_ho(node_xy, pair_dd)
    except Exception:
        p_analytic, res_analytic = None, 1e6
    res_grid = float(np.sqrt(np.mean([
        (np.hypot(*(p_grid - node_xy[j])) - np.hypot(*(p_grid - node_xy[i])) - pair_dd[(i, j)]) ** 2
        for (i, j) in pairs
    ])))

    if p_analytic is not None and res_analytic <= res_grid:
        p_init = p_analytic
    else:
        p_init = p_grid

    # Stage B.1: Huber-IRLS Iterative Refinement
    p_irls, res_irls = solve_taylor_series(node_xy, pair_dd, p_init=p_init, max_iter=25, tol_m=1e-4, huber_delta=1.5)

    # Stage B.2: Bounded Levenberg-Marquardt / Trust-Region Polish with Huber Loss
    def lm_residuals(p):
        return [
            np.sqrt(wk) * ((np.hypot(*(p - node_xy[j])) - np.hypot(*(p - node_xy[i]))) - pair_dd[(i, j)])
            for wk, (i, j) in zip(w_arr, pairs)
        ]

    sol = least_squares(lm_residuals, p_irls, loss="huber", f_scale=1.5, max_nfev=100)
    p_stage_b = sol.x

    # Pick minimum-residual candidate between Analytic, Huber-IRLS, and LM Polish
    candidates = [("ADRA-3Stage (Huber-IRLS+LM)", p_stage_b), ("ADRA-IRLS", p_irls)]
    if p_analytic is not None:
        candidates.append(("ADRA-Analytic (Fang/Chan-Ho)", p_analytic))

    best_name, best_pos, best_res = candidates[0][0], candidates[0][1], 1e9
    for cname, cpos in candidates:
        r_val = float(np.sqrt(np.mean([
            ((np.hypot(*(cpos - node_xy[j])) - np.hypot(*(cpos - node_xy[i]))) - pair_dd[(i, j)]) ** 2
            for (i, j) in pairs
        ])))
        if r_val < best_res - 1e-6:
            best_res = r_val
            best_pos = cpos
            best_name = cname

    # Stage C: CV-EKF Kinematic Update (if tracker instance supplied)
    ekf_state = None
    if tracker is not None:
        ekf_state = tracker.update_with_tdoa(node_xy, pair_dd, timestamp=timestamp)

    cov = compute_crlb(node_xy, best_pos, sigma_tdoa_s=sigma_tdoa_s)
    a_ax, b_ax, ang = get_error_ellipse(cov)

    return {
        "position": [float(best_pos[0]), float(best_pos[1])],
        "rms_residual_m": float(best_res),
        "solver_used": best_name,
        "crlb_cov": cov.tolist() if not np.any(np.isnan(cov)) else None,
        "error_ellipse": {
            "semi_major_m": round(a_ax, 3),
            "semi_minor_m": round(b_ax, 3),
            "tilt_deg": round(ang, 2)
        },
        "ekf_state": ekf_state
    }


# ----------------------------------------------------------------------------
# 6. Multi-Solver Fusion & Best-Fit Arbiter
# ----------------------------------------------------------------------------
def solve_multilateration_ensemble(node_xy, pair_dd, pair_w=None, tracker=None, timestamp=None):
    """
    Runs both Analytic Closed-Form (Fang/Chan) and ADRA 3-Stage Huber-IRLS + LM with global seed.
    Selects the solution with minimum geometric residual variance.
    """
    pos_analytic, res_analytic = solve_chan_ho(node_xy, pair_dd)
    p_init = pos_analytic if pos_analytic is not None else np.mean(np.array(list(node_xy.values())), axis=0)
    pos_taylor, res_taylor = solve_taylor_series(node_xy, pair_dd, p_init=p_init)

    if pos_analytic is not None and res_analytic <= res_taylor + 1e-4:
        best_pos = pos_analytic
        best_res = res_analytic
        solver_name = "ADRA-Fang-Exact" if len(node_xy) == 3 else "ADRA-Chan-Ho"
    else:
        best_pos = pos_taylor
        best_res = res_taylor
        solver_name = "ADRA-Huber-IRLS"

    ekf_state = None
    if tracker is not None:
        ekf_state = tracker.update_with_tdoa(node_xy, pair_dd, timestamp=timestamp)

    cov = compute_crlb(node_xy, best_pos)
    a, b, ang = get_error_ellipse(cov)

    return {
        "position": [float(best_pos[0]), float(best_pos[1])],
        "rms_residual_m": float(best_res),
        "solver_used": solver_name,
        "crlb_cov": cov.tolist() if not np.any(np.isnan(cov)) else None,
        "error_ellipse": {
            "semi_major_m": round(a, 3),
            "semi_minor_m": round(b, 3),
            "tilt_deg": round(ang, 2)
        },
        "ekf_state": ekf_state
    }

