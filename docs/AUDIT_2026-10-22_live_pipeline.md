# NanoSat ADRA-TDOA: Live Pipeline Audit & Physical Reproducibility Report
**Date:** 2026-10-22  
**Auditor:** Antigravity (Gravitas Reliability Runtime)  
**Operating Mode:** Software-Only / Offline Replay / Zero Live Transmission  
**Repository State:** Offline Replay Verified, Target Correlation Blocker Diagnosed

---

## 1. Executive Summary & Verdict

An exhaustive offline audit of the existing signal processing pipeline and saved laboratory raw I/Q captures (`captures/node{1,2,3}_lab_live.cu8`, 19.2 MB each, 4.0 s @ 2.4 MSPS) was conducted without hardware access.

### Gravitas Verdict
* **Live Hardware Performance Claim:** **UNVERIFIED / FAILS REPRODUCTION**.
* **Prior "0.4 cm Delta / 4.2 cm Accuracy" Status:** **RETRACTED**. The numbers reported in Section 10 of `PROJECT_CONTEXT.md` (+10.76 ns, +5.09 ns, -5.90 ns) match the theoretical geometric ground-truth distances to within sub-centimeter tolerances, but **cannot be produced** from the actual raw I/Q recordings saved in `captures/`.
* **Actual Offline Replay Result on `lab_live`:** **`NO_FIX` (Status: `TOO_FEW_PAIRS`)**.
  - Station 1–Station 2 Pair: **FAILED** (`NO_GOOD_SEGMENTS`, target quality median 2.92 vs threshold 8.0).
  - Station 1–Station 3 Pair: **FAILED** (`NO_GOOD_SEGMENTS`, target quality median 2.60 vs threshold 8.0).
  - Station 2–Station 3 Pair: **PROCESSED** (`target quality` 14.87, 43 segments), but yielded $\Delta R = -5.46\text{ m}$, which **exceeds the physical baseline** of $4.984\text{ m}$.

---

## 2. Quantitative Reproducibility Evidence

The deterministic replay engine (`src/processing/replay.py`) was executed against the exact raw captures:

```
[REPLAY] slot=lab_live algo=adra -> NO_FIX reason=TOO_FEW_PAIRS (1 usable, need >= 2)
  node1: cfo +29686 Hz (+68.4 ppm) tone-SNR 13.9 dB ref -27.5 dB tgt -33.2 dB clip 0.000%
  node2: cfo +29725 Hz (+68.5 ppm) tone-SNR 11.3 dB ref -34.2 dB tgt -40.7 dB clip 0.000%
  node3: cfo +27354 Hz (+63.0 ppm) tone-SNR 14.3 dB ref -25.8 dB tgt -39.5 dB clip 0.000%
  pair node1-node2: FAILED NO_GOOD_SEGMENTS | tgt-quality med 2.9 (p10 2.1), ref med 26.3
  pair node1-node3: FAILED NO_GOOD_SEGMENTS | tgt-quality med 2.6 (p10 2.1), ref med 26.6
  pair node2-node3: OK range diff -5.46 m (baseline 4.98 m, +/-3.39 m, 43 seg) | tgt-quality med 14.9, ref med 27.7
log: docs/audit/replay_lab_live_cal56.json
```

### Raw I/Q Cryptographic Provenance
* `node1_lab_live.cu8`: `19,200,000` bytes, SHA-256: `cc32da3ddfcb1f4ea592626997a82b8a11dd0384dd859c7df0280e2c3eda15c7`
* `node2_lab_live.cu8`: `19,200,000` bytes, SHA-256: `e2102551cd14c1b1f1990a79abed48025374c441e1ca2e3e8c4277b7aef7cad5`
* `node3_lab_live.cu8`: `19,200,000` bytes, SHA-256: `3ee85221d1a4476b426a8676eb14b81a6b59d98a45d2aa705d2327e9e566f5ce`

---

## 3. Deep-Dive Root Cause Analysis

### 3.1 Node 1 Target Correlation Failure
* **Symptom:** In pairs involving Node 1 (`node1-node2` and `node1-node3`), reference correlation is healthy (`ref_quality` $\approx 26.3\text{--}26.6$), but target correlation is completely washed out (`target_quality` $\approx 2.6\text{--}2.9$, threshold is $8.0$).
* **Mechanism:** 
  1. The reference transmitter (Pluto @ 433.350 MHz) was received with tone SNR of $13.9\text{ dB}$ on Node 1.
  2. The target transmitter (bladeRF @ 434.490 MHz) is $5.7\text{ dB}$ lower in power ($-33.2\text{ dB}$ vs $-27.5\text{ dB}$).
  3. While Node 2 and Node 3 successfully cross-correlated the target band with median quality $14.87$, cross-correlating Node 1 against either Node 2 or Node 3 showed zero prominent correlation peak.
  4. Probable physical causes in the lab: Node 1 antenna orientation/obstruction, severe multipath cancellation at Node 1's position at $434.490\text{ MHz}$, or frequency offset / phase noise on the bladeRF transmitter channel.

### 3.2 Physical Baseline Violation on Pair 2-3 ($\Delta R = -5.46\text{ m}$ on a $4.98\text{ m}$ Baseline)
* **Symptom:** Pair 2-3 produced an estimated range difference of $-5.46\text{ m}$. Under the triangle inequality, the range difference between two receivers can **never exceed the inter-station baseline**:
  $$|\Delta R_{23}| \le \|\vec{S}_2 - \vec{S}_3\| = 4.984\text{ m}$$
  $$-5.46\text{ m} < -4.984\text{ m} \implies \text{Physical Impossibility}$$
* **Breakdown of Pair 2-3 Timing:**
  - Raw uncalibrated differential delay: $d_{\text{raw}} = +43.84\text{ ns}$
  - Hardcoded Station 3 V4 calibration offset: $c_{\text{offset}} = +56.30\text{ ns}$
  - Corrected time difference: $d_{\text{corr}} = d_{\text{raw}} - c_{\text{offset}} = 43.84 - 56.30 = -12.46\text{ ns}$
  - Geometric reference delay: $\Delta \tau_{\text{ref}} = -5.75\text{ ns}$
  - Target TDOA: $\text{TDOA} = d_{\text{corr}} - \Delta \tau_{\text{ref}} = -12.46 - (-5.75) = -18.22\text{ ns}$
  - Range difference: $\Delta R = c \cdot \text{TDOA} = -18.218\text{ ns} \times 0.299792\text{ m/ns} = \mathbf{-5.462\text{ m}}$
* **Conclusion on Calibration:**
  The calibration offset of $+56.30\text{ ns}$ ($16.88\text{ m}$) is larger than the entire propagation time across the $4.98\text{ m}$ baseline ($16.63\text{ ns}$). It was introduced without a recorded conducted bench calibration log and shifted the solution beyond the physical boundary. This calibration must be re-measured on the bench.

### 3.3 Geometry Incompleteness (Unmeasured Baselines)
* In the physical tape measurements:
  - Station 1–Station 2, Station 1–Station 3, and Station 2–Station 3 were measured.
  - Pluto reference was measured only to S1 ($2.184\text{ m}$) and S2 ($5.969\text{ m}$). **P-S3 was never measured.**
  - Target was measured only to S1 ($2.489\text{ m}$) and S2 ($5.715\text{ m}$). **T-S3 was never measured.**
* Because only two distances were measured for Pluto and Target, there is an unresolved mirror ambiguity across the S1-S2 baseline, and zero redundancy to check for tape measurement errors.

---

## 4. End-to-End Data Flow Trace

```
[RTL-SDR .cu8 raw bytes] (2.4 MSPS, interleaved uint8, /dev/shm)
          │
          ▼
[tdoa_lib.preprocess_node]
   ├── DC offset subtraction: (iq - 127.5) / 128.0
   ├── Power estimation: 10 * log10(mean(|x|^2))
   ├── CFO search: 65536-point FFT on reference tone -> parabolic peak interpolation
   └── Kaiser FIR Bandpass Filtering:
          ├── Reference band: [-840 kHz, -300 kHz]
          └── Target band:    [+300 kHz, +840 kHz]
          │
          ▼
[Coarse Reference Cross-Correlation] (Decimated 4x, full-slot FFT convolution)
   └── Detects bulk clock start offset (up to ±100,000 samples)
          │
          ▼
[Segmented Fine Cross-Correlation] (N_seg = 60 segments of 10 ms = 24,000 samples)
   ├── Sliding FFT correlation on reference & target
   ├── Parabolic/sinc peak refinement (sub-sample precision)
   └── Peak quality evaluation: (peak - mean) / stddev
          │
          ▼
[Differential Delay & Outlier Rejection]
   ├── Segment-by-segment: ΔD[k] = D_tgt[k] - D_ref[k]
   ├── Linear clock drift compensation (CFO residual slope)
   └── Huber M-estimator on inlier distribution
          │
          ▼
[Calibration & Geometric Reference Correction]
   ├── Δτ_corr = Δτ_meas - chromatic_cal[node_j] + chromatic_cal[node_i]
   └── TDOA_ij = Δτ_corr - (||R_ref - S_j|| - ||R_ref - S_i||) / c
          │
          ▼
[Consistency Checks & Rejection Gates]
   ├── Baseline check: |c * TDOA_ij| <= ||S_i - S_j|| + margin
   └── Loop closure check: TDOA_12 + TDOA_23 + TDOA_31 ≈ 0 (< 2.5 m)
          │
          ▼
[Multilateration Solver] (ADRA / Levenberg-Marquardt / Closed-Form)
   └── Computes (x, y) target coordinates & solver residual
          │
          ▼
[Mission Control Server & Dashboard]
   └── REST API (/api/live/latest, /api/survey, /api/history) -> Web UI Canvas
```

---

## 5. Audit of Stale, Hardcoded, and Synthetic Elements

| Component | Location | Issue | Status & Remedy |
| :--- | :--- | :--- | :--- |
| `/api/solve` | `src/server/app.py` | Generates synthetic truth + Gaussian noise ($0.05\text{ m}$) under the label `LIVE_HARDWARE`. | **FIXED**: Explicit `data_source: "SIMULATED"` and `simulated: true` tags added. |
| Dashboard Header | `src/server/static/index.html` | Displayed "LIVE SURVEILLANCE" regardless of engine state. | **FIXED**: Updated to reflect `SIMULATED`, `REPLAY`, `LIVE-HW`, `STALE`, `NO FIX`. |
| Station Telemetry | `src/server/app.py` | Fake temperatures ($42.1^\circ\text{C}$) and SNR ($28\text{ dB}$) in `station_fleet`. | Replaced with real capture metadata or marked `N/A`. |
| Spectrum API | `src/server/app.py` | Synthesized random Gaussian noise. | Clearly labeled as synthetic monitor. |
| V4 Calibration | `config_lab.json` | $56.30\text{ ns}$ offset without laboratory conducted log. | Marked `UNVERIFIED_BENCH_CAL`. Requires conducted recalibration. |
| Target Ground Truth | `config_lab.json` | Tied to 2 tape distances; T-S3 missing. | Separate ground truth survey panel required. |

---

## 6. Protocols for Lab Validation (Checklist for Return to Lab)

1. **Verify Raw RF Signal Levels & Prevent Saturation:**
   - Run a short 1-second test capture.
   - Verify clipping is 0.000% across all 3 RTL-SDRs.
   - Adjust RTL-SDR gain (or bladeRF TX power) so target SNR is $> 18\text{ dB}$ on all 3 nodes.
2. **Conducted V4 Group Delay Calibration:**
   - Connect Pluto via 4-way resistive splitter with matched coaxial cables to Node 1 (V3), Node 2 (V3), and Node 3 (V4).
   - Execute `TEST-V4-BIAS` to measure the true hardware group delay offset between V3 and V4.
3. **Complete 6-Distance Tape Survey:**
   - Measure S1-S2, S1-S3, S2-S3.
   - Measure Pluto: P-S1, P-S2, **and P-S3**.
   - Input into the new Tape Survey panel to verify zero triangle residual.
4. **Independent Target Survey:**
   - Floor-mark at least 6 static positions.
   - Measure distances from all 3 stations for each position to obtain ground truth.
   - Run offline replay on static captures before attempting moving trials.
