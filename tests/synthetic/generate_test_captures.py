"""
generate_test_captures.py - Generate realistic raw uint8 .cu8 and .json files for end-to-end verification.
"""

import hashlib
import json
import os
import sys
import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "tdoa_kit", "solution1")))

from src.processing.core import C_LIGHT
from make_waveforms import make_pn_waveform, FS_TX


def periodic_resample(x, n_new):
    X = np.fft.fft(x)
    n = len(x)
    Y = np.zeros(n_new, dtype=complex)
    h = n // 2
    Y[:h] = X[:h]
    Y[-h:] = X[-h:]
    return np.fft.ifft(Y) * (n_new / n)


def generate_captures(output_dir="tests/test_captures", slot_str="20261016_120000"):
    os.makedirs(output_dir, exist_ok=True)
    fs = 2_400_000
    N = int(2.0 * fs)

    from src.processing.core import load_site_config
    cfg = load_site_config("configs/geometry/site_simulation.json")
    nodes = cfg["_node_xy"]
    ref_xy = cfg["_ref_xy"]
    truth_tgt = cfg["_truth_xy"]

    f_ref_bb = -570_000
    f_tgt_bb = +570_000

    ref_tx = make_pn_waveform(seed=11, digital_offset=0.0)
    tgt_tx = make_pn_waveform(seed=22, digital_offset=0.0)
    Q = int(len(ref_tx) * fs / FS_TX)
    S_ref = np.fft.fft(periodic_resample(ref_tx, Q))
    S_tgt = np.fft.fft(periodic_resample(tgt_tx, Q))
    f_axis = np.fft.fftfreq(Q, 1.0 / fs)

    t0_map = {"node1": 0.012, "node2": 0.035, "node3": 0.021}
    # For node3 (V4 dongle), let's simulate a 15.0 ns filter calibration offset
    cal_offset_map = {"node1": 0.0, "node2": 0.0, "node3": 15.0}

    print(f"Generating realistic .cu8 captures in {output_dir}...")
    for node_name, nxy in nodes.items():
        t0 = t0_map[node_name]
        cal_ns = cal_offset_map[node_name]
        d_ref = float(np.hypot(*(ref_xy - nxy)))
        d_tgt = float(np.hypot(*(truth_tgt - nxy)))

        # Hardware filter delay adds to both signals at receiver 3
        tau_ref = t0 - (d_ref / C_LIGHT) - (cal_ns * 1e-9)
        tau_tgt = t0 - (d_tgt / C_LIGHT) - (cal_ns * 1e-9)

        spec_ref = S_ref * np.exp(2j * np.pi * f_axis * tau_ref)
        spec_tgt = S_tgt * np.exp(2j * np.pi * f_axis * tau_tgt)
        y_ref = np.resize(np.fft.ifft(spec_ref), N)
        y_tgt = np.resize(np.fft.ifft(spec_tgt), N)

        n = np.arange(N)
        rx = y_ref * np.exp(2j * np.pi * f_ref_bb * n / fs) + y_tgt * np.exp(2j * np.pi * f_tgt_bb * n / fs)

        # Convert complex64 to RTL-SDR raw uint8 format: (raw - 127.4)/128.0
        # raw = int8_val * 128.0 + 127.4
        i_u8 = np.clip(np.round(rx.real * 128.0 + 127.4), 0, 255).astype(np.uint8)
        q_u8 = np.clip(np.round(rx.imag * 128.0 + 127.4), 0, 255).astype(np.uint8)

        cu8_data = np.empty(2 * N, dtype=np.uint8)
        cu8_data[0::2] = i_u8
        cu8_data[1::2] = q_u8

        cu8_path = os.path.join(output_dir, f"{node_name}_{slot_str}.cu8")
        json_path = os.path.join(output_dir, f"{node_name}_{slot_str}.json")

        with open(cu8_path, "wb") as f_cu8:
            f_cu8.write(cu8_data.tobytes())

        hasher = hashlib.sha256()
        hasher.update(cu8_data.tobytes())

        meta = {
            "station_id": node_name,
            "hardware_model": "RTL-SDR Blog V4" if node_name == "node3" else "RTL-SDR Blog V3",
            "tuner_type": "R828D" if node_name == "node3" else "R820T2",
            "slot_utc_str": f"{slot_str} UTC",
            "center_freq_hz": 433920000,
            "sample_rate_sps": 2400000,
            "cal_delay_offset_ns": cal_ns,
            "bytes_recorded": len(cu8_data),
            "sha256": hasher.hexdigest()
        }
        with open(json_path, "w") as f_j:
            json.dump(meta, f_j, indent=2)

        print(f"  Created: {cu8_path} ({len(cu8_data)} bytes, cal_ns={cal_ns})")


if __name__ == "__main__":
    generate_captures()
