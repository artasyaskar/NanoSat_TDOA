"""
gdop.py - Geometric Dilution of Precision (GDOP) Surface & Spatial Heatmap Engine.

Calculates the spatial GDOP tensor across a surveillance grid:
  GDOP(x, y) = sqrt(Trace((H^T * H)^{-1}))
Provides critical mission intelligence on array coverage, geometric blind spots,
and optimal sensor placement.
"""

import numpy as np


def calculate_gdop_point(node_xy, target_xy):
    """
    Computes GDOP at a single (x, y) coordinate.
    Returns float value; values > 20.0 indicate geometric singularity / blind spots.
    """
    nodes = list(node_xy.values())
    m = len(nodes)
    if m < 3:
        return np.nan

    p = np.array(target_xy, dtype=float)
    ranges = [np.hypot(p[0] - n[0], p[1] - n[1]) for n in nodes]
    if any(r < 1e-3 for r in ranges):
        return np.nan  # Collocated with a sensor

    # Range differences relative to node 0
    u0 = (p - nodes[0]) / ranges[0]
    H = np.zeros((m - 1, 2))
    for i in range(1, m):
        ui = (p - nodes[i]) / ranges[i]
        H[i - 1, :] = ui - u0

    try:
        FIM = H.T @ H
        cov = np.linalg.inv(FIM)
        gdop = float(np.sqrt(np.trace(cov)))
        return min(gdop, 50.0)
    except np.linalg.LinAlgError:
        return 50.0


def generate_gdop_grid(node_xy, bounds=None, grid_res=50):
    """
    Generate a 2D spatial GDOP matrix across surveillance bounds.
    bounds: (xmin, xmax, ymin, ymax) in meters. If None, derived from station array.
    """
    nodes = np.array(list(node_xy.values()))
    if bounds is None:
        margin = 300.0
        xmin, xmax = np.min(nodes[:, 0]) - margin, np.max(nodes[:, 0]) + margin
        ymin, ymax = np.min(nodes[:, 1]) - margin, np.max(nodes[:, 1]) + margin
    else:
        xmin, xmax, ymin, ymax = bounds

    xs = np.linspace(xmin, xmax, grid_res)
    ys = np.linspace(ymin, ymax, grid_res)
    grid = np.zeros((grid_res, grid_res))

    for r, y in enumerate(ys):
        for c, x in enumerate(xs):
            grid[r, c] = calculate_gdop_point(node_xy, (x, y))

    return {
        "x_coords": xs.tolist(),
        "y_coords": ys.tolist(),
        "gdop_matrix": np.nan_to_num(grid, nan=50.0).tolist(),
        "bounds": [float(xmin), float(xmax), float(ymin), float(ymax)],
        "min_gdop": float(np.nanmin(grid)),
        "mean_gdop": float(np.nanmean(grid[grid < 45.0]))
    }
