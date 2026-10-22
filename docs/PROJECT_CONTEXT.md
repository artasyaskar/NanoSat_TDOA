# NanoSat_TDOA: Durable Project Context & System Memory
**Document Version:** 1.0  
**Date of Audit & Reconstruction:** 2026-10-19  
**Status:** Software-Only Audit & Verification Complete (Phase A Verified)  
**Authority Reference:** `TDOA_Build_Guide.docx` (Supervisor: Sir Ahsan)  
**Operating Mode:** Software-Only / Conducted-Only Safe Mode (No radiated transmissions permitted)

---

## 1. Project Identity and Core Objective

* **Project Name:** `NanoSat_TDOA`
* **Supervisor:** Sir Ahsan
* **Target Objective:** Implement **Solution 1** from Sir Ahsan's `TDOA_Build_Guide.docx`.
* **System Concept:** Ground-based passive Time Difference of Arrival (TDOA) radio localization demonstrator operating at UHF ($433\text{ MHz}$).
* **Primary Scope:**
  * Initial deployment of **three geographically distributed receiver stations** arranged in a triangle (baselines $\sim 150\text{--}300\text{ m}$), with optional future expansion to a 4th station.
  * Receiver hardware per station: One Raspberry Pi (Pi 4 or Pi 5) + one RTL-SDR USB dongle + dedicated vertical dipole antenna and length-matched coaxial cable.
    * Station 1: Raspberry Pi + RTL-SDR Blog V3 (R820T2 tuner).
    * Station 2: Raspberry Pi + RTL-SDR Blog V3 (R820T2 tuner).
    * Station 3: Raspberry Pi + RTL-SDR Blog V4 (R828D tuner).
  * Transmitter hardware:
    * **ADALM-Pluto:** Reference transmitter placed at a surveyed, known position $\vec{R}_{\text{ref}}$.
    * **bladeRF:** Target transmitter whose position $\vec{R}_{\text{tgt}}$ is to be estimated.
  * Development & Processing: Laptop connected via local network (Wi-Fi/hotspot or Ethernet) collects recordings from Pis, executes correlation, applies reference correction, computes multilateration fixes, and displays maps/metrics.
* **Separation of Concerns:**
  * This project is strictly **Solution 1 TDOA**. It is not to be confused with earlier MUSIC Direction-of-Arrival (DOA) work or Solution 2 (Wi-Fi 2.4 GHz).
  * Real RF transmissions are prohibited until formal university/supervisor authorization and PTA/FAB regulatory compliance are documented.

---

## 2. Authoritative Solution 1 Architecture & Frequency Plan

### 2.1 Resolution of Frequency Plan Nomenclature
A critical finding from this audit reconciles the apparent contradiction between "$\pm 400\text{ kHz}$ digital shifts" and "$\pm 570\text{ kHz}$ receiver baseband offsets":

1. **Transmitter Reference Frame (LO + Digital Shift):**
   * **Pluto Reference TX:** Hardware LO set to $433.750\text{ MHz}$. Software applies a digital frequency shift of $-400\text{ kHz}$.
     $$f_{\text{RF, ref}} = 433.750\text{ MHz} - 0.400\text{ MHz} = \mathbf{433.350\text{ MHz}}$$
     *Carrier Leakage:* Pluto LO leakage spike stays at $433.750\text{ MHz}$ (outside signal bandwidth).
   * **bladeRF Target TX:** Hardware LO set to $434.090\text{ MHz}$. Software applies a digital frequency shift of $+400\text{ kHz}$.
     $$f_{\text{RF, tgt}} = 434.090\text{ MHz} + 0.400\text{ MHz} = \mathbf{434.490\text{ MHz}}$$
     *Carrier Leakage:* bladeRF LO leakage spike stays at $434.090\text{ MHz}$ (outside signal bandwidth).

2. **Receiver Reference Frame (RTL-SDR tuned to $433.920\text{ MHz}$ at $2.4\text{ MS/s}$):**
   * Instantaneous complex passband spans $432.720\text{ MHz}$ to $435.120\text{ MHz}$ ($[-1200\text{ kHz}, +1200\text{ kHz}]$).
   * **Reference Baseband Offset:** $433.350\text{ MHz} - 433.920\text{ MHz} = \mathbf{-570\text{ kHz}}$.
   * **Target Baseband Offset:** $434.490\text{ MHz} - 433.920\text{ MHz} = \mathbf{+570\text{ kHz}}$.
   * Both signals occupy $540\text{ kHz}$ bandwidth (RRC $\alpha = 0.35$, chip rate $400\text{ kchip/s}$):
     * Reference band spans $[-840\text{ kHz}, -300\text{ kHz}]$.
     * Target band spans $[+300\text{ kHz}, +840\text{ kHz}]$.
   * **Central Guard Band ($[-300\text{ kHz}, +300\text{ kHz}]$, $600\text{ kHz}$ wide):**
     * Contains Pluto LO leakage at $-170\text{ kHz}$ ($433.750 - 433.920\text{ MHz}$).
     * Contains RTL-SDR zero-IF DC spike at $0\text{ Hz}$.
     * Contains bladeRF LO leakage at $+170\text{ kHz}$ ($434.090 - 433.920\text{ MHz}$).
     * All three unwanted spikes fall into this empty gap and are filtered out by digital Kaiser FIR bandpass filtering.

```
                             RECEIVER SPECTRUM (2.4 MS/s)
                   Receiver Center Frequency = 433.920 MHz (0 Hz Baseband)
  |<---------------------------------- 2.400 MHz ---------------------------------->|
  |                                                                                 |
  |  [ Reference Band ]        ( Central Guard Band )         [ Target Band ]       |
  |   BW = 540 kHz                 Width = 600 kHz             BW = 540 kHz         |
  |<- - - - - - - - - ->|                               |< - - - - - - - - ->|      |
--+---------------------+-------+-------+-------+-------+--------------------+------+--
  |                     |       |       |       |       |                    |      |
-840                  -300    -170      0     +170    +300                 +840    kHz
                      Pluto    RTL    bladeRF
                     Leakage   DC     Leakage
```

---

## 3. Mathematical Principles & Reference Correction

### 3.1 Timing Model & Clock Skew
Let $t_{0, i}$ be the asynchronous sampling start epoch of receiver $i$, and $\epsilon_i$ be its relative fractional clock skew (in ppm).
Physical sample time at sample index $m$:
$$t_i[m] = t_{0, i} + m \cdot \frac{1 + \epsilon_i}{F_s}$$

When cross-correlating a $10\text{ ms}$ segment between stations $i$ and $j$:
$$D_{\text{ref}, ij} = F_s \left[ (t_{0, j} - t_{0, i}) + \frac{\|\vec{R}_{\text{ref}} - \vec{S}_j\| - \|\vec{R}_{\text{ref}} - \vec{S}_i\|}{c} \right]$$
$$D_{\text{tgt}, ij} = F_s \left[ (t_{0, j} - t_{0, i}) + \frac{\|\vec{R}_{\text{tgt}} - \vec{S}_j\| - \|\vec{R}_{\text{tgt}} - \vec{S}_i\|}{c} \right]$$

### 3.2 Target Differential Range Equation
Subtracting reference lag from target lag cancels $(t_{0, j} - t_{0, i})$:
$$\Delta D_{ij} = D_{\text{tgt}, ij} - D_{\text{ref}, ij}$$
$$\text{TDOA}_{ij} = \frac{\Delta D_{ij}}{F_s} + \frac{\|\vec{R}_{\text{ref}} - \vec{S}_j\| - \|\vec{R}_{\text{ref}} - \vec{S}_i\|}{c}$$
$$\Delta R_{\text{tgt}, ij} = c \cdot \text{TDOA}_{ij} = \frac{c \cdot \Delta D_{ij}}{F_s} + \Delta R_{\text{ref}, ij}$$

> [!IMPORTANT]
> Reference subtraction alone does NOT equal target TDOA. It removes receiver clock start jitter, but requires adding back the surveyed geometric range difference $\Delta R_{\text{ref}, ij}$. Any error in reference survey coordinates maps directly into target TDOA.

---

## 4. Verified Repository Directory Structure & Component Roles

```
d:/NanoSat_TDOA/
├── .agents/                      [Agent instructions and gravitas reliability skill]
├── captures_sim/                 [Simulated raw captures generated by simulate_captures.py]
├── configs/
│   ├── geometry/
│   │   └── site_simulation.json  [Surveyed coordinates for simulation site]
│   └── stations/
│       ├── station1_node1.json   [Node 1 hardware config: Pi 5 + RTL-SDR Blog V3]
│       ├── station2_node2.json   [Node 2 hardware config: Pi 5 + RTL-SDR Blog V3]
│       └── station3_node3.json   [Node 3 hardware config: Pi 5 + RTL-SDR Blog V4, cal_delay field]
├── deploy/
│   └── pi/
│       ├── health_check.py       [Hardware diagnostics: temp, disk, chrony, RTL-SDR USB]
│       ├── setup_pi.sh           [Raspberry Pi automated OS provisioning script]
│       ├── station_daemon.py     [Autonomous UTC slot-aligned capture daemon]
│       └── tdoa-recorder.service [systemd unit file for auto-start on boot]
├── docs/
│   ├── PROJECT_CONTEXT.md        [This document: master durable project context]
│   └── research/
│       └── paper_draft.md        [IEEE Transactions draft on asymmetric dual-offset TDOA]
├── src/
│   ├── algorithms/
│   │   ├── gdop.py               [Spatial GDOP tensor and 2D contour engine]
│   │   ├── solvers.py            [Fang closed-form 3-station, Chan-Ho 4+, Taylor WLS, CRLB]
│   │   └── tracker.py            [4D kinematic EKF for moving targets]
│   ├── collection/
│   │   └── collect_stations.py   [SCP harvesting utility with SHA-256 verification]
│   ├── processing/
│   │   ├── core.py               [Core DSP: Kaiser FIR, CFO derotation, sinc xcorr, 2D solver]
│   │   └── process_capture.py    [Modular CLI solver for laptop capture processing]
│   ├── server/                   [Mission Control Dashboard]
│   │   ├── app.py                [Zero-dependency HTTP server with REST API]
│   │   └── web/                  [Frontend: Polar Radar, 2D Tactical Map, GDOP Heatmap]
│   └── transmitters/
│       ├── bladerf_driver.py     [Cross-platform headless bladeRF transmitter driver]
│       └── pluto_driver.py       [Headless non-blocking ADALM-Pluto transmitter driver]
├── tdoa_kit/                     [Original Supervisor Software Kit - Untouched Baseline]
│   ├── common/
│   │   ├── quicklook.py          [Single-file RF spectrum and clipping inspector]
│   │   └── tdoa_lib.py           [Original DSP & grid-search library]
│   ├── solution1/                [RTL-SDR 433 MHz demonstrator scripts]
│   │   ├── bladerf_tx_target.sh  [Original bladeRF bash script]
│   │   ├── collect_captures.sh   [Original SCP collection script]
│   │   ├── config.json           [Original solution 1 config template]
│   │   ├── make_waveforms.py     [Baseband PN signal synthesis script]
│   │   ├── pi_setup.sh           [Original Pi setup script]
│   │   ├── pluto_tx_reference.py [Original Pluto transmission script with blocking input]
│   │   ├── rtl_recorder.py       [Original Pi capture script]
│   │   ├── simulate_captures.py  [Original synthetic capture generator]
│   │   ├── tdoa_process.py       [Original end-to-end processing script]
│   │   └── tx_host_setup.sh      [Original transmitter host setup script]
│   └── solution2/                [Wi-Fi 2.4 GHz alternative implementation - Reference only]
├── tests/
│   ├── algorithms/
│   │   └── test_advanced_algorithms.py [Tests for Fang, Chan-Ho, Taylor, EKF, GDOP]
│   ├── synthetic/
│   │   ├── generate_test_captures.py   [Generates raw uint8 .cu8 and .json test captures]
│   │   ├── test_clock_skew.py          [Verifies 5 ppm relative clock drift rejection]
│   │   ├── test_fractional_delay.py    [Maps S-curve sub-sample interpolation bias]
│   │   ├── test_pipeline_baseline.py   [Ideal synthetic validation test]
│   │   └── test_reference_geometry.py  [Evaluates reference survey error sensitivity]
│   ├── test_captures/                  [Pre-generated test capture dataset with .cu8 and .json]
│   └── run_all_tests.py                [Master test runner for all automated verification]
├── .gitignore
├── config_sim.json               [Simulation configuration matching simulate_captures.py]
├── implementation_plan.md        [Formal technical audit and staged implementation plan]
├── skills-lock.json
└── TDOA_Build_Guide.docx         [Primary project build guide by Sir Ahsan]
```

---

## 5. In-Session Verification Evidence & Test Execution

All 5 core automated tests in `tests/run_all_tests.py` were executed and passed with zero errors:

| Test Name | Module | Benchmark Criterion | Observed Result | Status |
| :--- | :--- | :--- | :--- | :---: |
| **Baseline Pipeline Accuracy** | `test_pipeline_baseline.py` | Position error $< 0.50\text{ m}$ | **$0.052\text{ m}$** fix error ($14.09\text{s}$) | **PASS** |
| **Clock Skew & Reference Rejection** | `test_clock_skew.py` | Position error $< 6.00\text{ m}$ under $5\text{ ppm}$ relative skew | **$0.014\text{ m}$** fix error, spread $0.35\text{ ns}$ ($18.20\text{s}$) | **PASS** |
| **Sub-Sample S-Curve Interpolation** | `test_fractional_delay.py` | Peak S-curve bias $< 20.0\text{ ns}$, RMS $< 8.0\text{ ns}$ | Peak **$0.38\text{ ns}$** ($0.11\text{ m}$), RMS **$0.25\text{ ns}$** ($57.13\text{s}$) | **PASS** |
| **Reference Survey Sensitivity** | `test_reference_geometry.py` | Error amplification $\le 2.50\times$ | **$0.50\times$** error ratio ($1.43\text{s}$) | **PASS** |
| **Advanced Multilateration & EKF** | `test_advanced_algorithms.py` | Fang error $< 0.5\text{ m}$, EKF tracking $< 1.5\text{ m}$ | Fang **$0.082\text{ m}$**, EKF **$0.212\text{ m}$**, GDOP **$0.94$** ($0.07\text{s}$) | **PASS** |

**Total Suite Duration:** $90.92\text{ seconds}$. Master verdict: `ALL TESTS PASSED: PHASE A SOFTWARE VERIFICATION COMPLETE.`

### Additional Pipeline Execution Evidence:
1. `src/processing/process_capture.py` executed on `tests/test_captures/` with a simulated $15\text{ ns}$ hardware calibration offset on Station 3 (V4 dongle). It completed with exit code 0, achieving an absolute fix error of $4.57\text{ m}$ on raw quantized uint8 data with $0.012\text{ m}$ residual RMS.
2. `tdoa_kit/solution1/tdoa_process.py` executed on `captures_sim/` with exit code 0, achieving an absolute fix error of **$0.1\text{ m}$** vs surveyed ground truth.

---

## 6. Staged Approval Gates & Implementation Status

```
[ Phase A: Software Simulation & Driver Hardening ] ---> STATUS: COMPLETE & VERIFIED
                     |
[ Phase B: Conducted Hardware Bench Testing ]      ---> STATUS: HOLD (Awaiting Attenuator Rig & Sign-Off)
                     |
[ Phase C: Short-Baseline Open Field Trials ]       ---> STATUS: STRICT HOLD (PTA/FAB Regulatory Clearance Required)
```

### Approved & Permitted Activities (Phase A - Active):
* Mathematical modeling, simulation, test development, signal processing analysis.
* Codebase hardening (headless scripts, signal handling, modular architecture).
* Documentation, planning, and design.

### Prohibited Activities (Until Explicit Supervisor & Lab Sign-Off):
* Direct connection of SDR transmitters to RTL-SDR receivers without verified $\ge 30\text{ dB}$ attenuator.
* Over-the-air RF radiation at $433\text{ MHz}$ without PTA/FAB regulatory compliance and written departmental clearance.
* Modifications to physical Raspberry Pi hardware in the lab during audit sessions.

---

## 7. Technical Risks and Unverified Assumptions

| Risk / Assumption | Severity | Status | Mitigation / Protocol |
| :--- | :---: | :---: | :--- |
| **RTL-SDR V3 vs V4 Group Delay Asymmetry** | Medium | **Unverified Hypothesis** | V4 uses R828D with preselection filters. Any differential phase curvature across the $800\text{ kHz}$ band gap survives reference subtraction. Requires conducted calibration `TEST-V4-BIAS` ($N \ge 30$ slots) to determine stability. |
| **RTL-SDR Sample Clock Drift vs Temperature** | Low–Med | **Partially Verified** | TCXO crystals on V3/V4 drift $\pm 1\text{ ppm}$. $10\text{ ms}$ segmented correlation successfully rejects up to $5\text{ ppm}$ relative skew in simulation. Outdoor direct sunlight could cause thermal gradient. |
| **GNSS Survey Coordinate Accuracy** | High | **Unverified in Field** | Consumer smartphone GPS exhibits $3\text{--}8\text{ m}$ error. Reference survey errors propagate into target fixes. Multi-constellation temporal averaging ($\ge 15\text{ min}$) or RTK is required. |
| **Multipath & Building Reflections** | High | **Simulated Only** | Urban building reflections blur cross-correlation peaks. Open-field testing with line-of-sight is essential for initial validation. |
| **Pakistan 433 MHz Regulatory Status** | Critical | **Pending Approval** | 430–440 MHz is not an automatic unlicensed SRD band in Pakistan. Over-the-air radiation without institutional approval is illegal. |

---

## 8. Hardware Inventory Assumptions

* **Confirmed via Architecture:**
  * 3 Raspberry Pi stations + 3 RTL-SDR dongles (Station 1: V3, Station 2: V3, Station 3: V4).
  * 1 ADALM-Pluto reference transmitter (operates via USB or Ethernet `192.168.2.1`).
  * 1 bladeRF target transmitter (2.0 micro or x40/x115).
  * 1 development laptop (Python 3.10+, NumPy, SciPy, Matplotlib).
* **Unconfirmed (Physical Inspection Required):**
  * Availability and rating of RF combiner, 4-way splitter, and $30\text{ dB}$ / $2\text{ W}$ coaxial attenuator for bench testing.
  * Length matching of receiver coaxial extension cables ($\Delta L < 5\text{ mm}$ recommended).
  * Battery power bank capacity ($5\text{V } 3\text{A}$, $\ge 20{,}000\text{ mAh}$) for field endurance.

---

## 9. Phase Progression & Status
* **Phase A (Software & DSP Architecture):** COMPLETE & VERIFIED.
* **Phase B (Conducted & Over-the-Air Lab Hardware Validation):** **IN PROGRESS / REPRODUCTION PENDING (2026-10-22 Audit)**.
  * *Audit Result:* Offline deterministic replay of `captures/node*_lab_live.cu8` returned `NO_FIX` (Status: `TOO_FEW_PAIRS`).
  * *Blockers Identified:* Weak target correlation on Node 1 (quality 2.6–2.9 vs 8.0 threshold), unverified +56.3 ns V4 group delay calibration pushing Pair 2-3 into baseline violation, and incomplete 3-station reference survey.

---

## 10. Laboratory Hardware Validation Status & Audit Findings (2026-10-22)

An over-the-air multi-node hardware test was conducted in the laboratory:
* **Reference Transmitter:** ADALM-Pluto SDR transmitting cyclic PN waveform @ $433.350\text{ MHz}$ ($2.4\text{ MSPS}$, $0\text{ dB}$ attenuation, LO $433.750\text{ MHz} - 400\text{ kHz}$ digital shift).
* **Target Transmitter:** Nuand bladeRF 2.0 micro transmitting continuous PN sequence @ $434.490\text{ MHz}$ ($2.0\text{ MSPS}$, $42\text{ dB}$ overall gain, LO $434.090\text{ MHz} + 400\text{ kHz}$ digital shift).
* **Receiver Fleet:** 3 Raspberry Pi SDR nodes (Node 1 @ `192.168.5.229` [V3], Node 2 @ `192.168.5.249` [V3], Node 3 @ `192.168.5.222` [V4]).

All three receiver stations recorded synchronized 4.0-second I/Q streams ($19.2\text{ MB}$ per station in `/dev/shm` zero-drop RAM-disk).

### 10.1 Inchi-Tape Surveyed Ground Truth
The following measurements were recorded via measuring tape in the lab:

| Geometric Element | Physical Measurement (Tape) | Metric Length | Local Cartesian Coordinate $(x, y)$ | Notes |
| :--- | :--- | :--- | :--- | :--- |
| **Station 1 (Alpha)** | Origin Reference | $0.00\text{ m}$ | $(-4.30\text{ m}, -1.28\text{ m})$ | S1-S2 baseline: $8.026\text{ m}$ |
| **Station 2 (Bravo)** | $26\text{ ft } 4\text{ in}$ from S1 | $8.026\text{ m}$ | $(+3.73\text{ m}, -1.28\text{ m})$ | Defines primary X-axis |
| **Station 3 (Charlie)** | $20\text{ ft } 4\text{ in}$ from S1, $16\text{ ft } 4\text{ in}$ from S2 | $6.198\text{ m} / 4.978\text{ m}$ | $(+0.56\text{ m}, +2.57\text{ m})$ | S3 coordinates solved from S1/S2 |
| **Pluto Reference TX** | $7\text{ ft } 2\text{ in}$ from S1, $19\text{ ft } 7\text{ in}$ from S2 | $2.184\text{ m} / 5.969\text{ m}$ | $(-2.20\text{ m}, -0.65\text{ m})$ | **P-S3 distance unmeasured** |
| **bladeRF Target TX** | $8\text{ ft } 2\text{ in}$ from S1, $18\text{ ft } 9\text{ in}$ from S2 | $2.489\text{ m} / 5.715\text{ m}$ | **$(-1.93\text{ m}, -0.51\text{ m})$** | **T-S3 distance unmeasured** |

*Array Centroid Geodetic Origin:* $33.000810^\circ\text{N}, 73.001360^\circ\text{E}$.

### 10.2 Audit Analysis of Experimental Replay
When the saved `lab_live` raw files are processed through the deterministic offline pipeline:
1. **Node 1 Cross-Correlation Failure:** Pairs 1-2 and 1-3 fail segment quality checks (`target_quality` is $2.6\text{--}2.9$, below the rejection threshold of $8.0$). Reference quality is high ($\approx 26.5$), indicating the bladeRF target transmission was suppressed or disrupted at Node 1.
2. **Pair 2-3 Baseline Anomaly:** Pair 2-3 yields a range difference of $-5.46\text{ m}$, exceeding the physical $4.98\text{ m}$ baseline. This was traced to applying an unverified $+56.30\text{ ns}$ ($16.88\text{ m}$) group-delay calibration on Station 3 (V4).
3. **Reported vs. Replayed Discrepancy:** The earlier reported TDOA deltas ($0.4\text{ cm}$, $6.0\text{ cm}$, $0.9\text{ cm}$) match the exact geometric ground truth distances, but are not reproduced by cross-correlation of the saved I/Q data. They represent analytical expectations rather than demonstrated over-the-air localization.
4. **Current Status:** Live hardware experiments are paused until bench conducted calibration and SNR adjustments are completed in the lab. Full audit details are documented in [AUDIT_2026-10-22_live_pipeline.md](file:///d:/NanoSat_TDOA/docs/AUDIT_2026-10-22_live_pipeline.md).

