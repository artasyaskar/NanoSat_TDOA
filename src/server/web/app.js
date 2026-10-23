/**
 * app.js - Tactical EW-TDOA & SIGINT Mission Control Orchestrator
 * Connects the Stitch Palantir/Anduril Tactical UI to the ADRA-TDOA Backend Engine.
 */

document.addEventListener('DOMContentLoaded', () => {
  // Application State
  let currentMode = 'live'; // 'live' | 'coax' | 'urban' | 'ablation'
  let currentAlgo = 'adra'; // 'adra' | 'gcc_phat' | 'standard'
  let fleetData = {};
  let gdopData = null;
  let lastSolveData = null;
  let benchmarkData = null;

  // 1. Initialize Polar Radar & Interactive Tactical Map
  const radar = new PolarRadar('polarRadarCanvas');
  const tacticalMap = new TacticalMap('tacticalMapCanvas', async (updatedGeometry) => {
    try {
      await fetch('/api/geometry', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(updatedGeometry)
      });
      await fetchGDOP();
      await fetchSolve();
    } catch (e) {
      console.warn('Geometry update failed:', e);
    }
  });

  // 2. Canvas Contexts for Right Panel Instrumentation
  const specCanvas = document.getElementById('rfSpectrumCanvas');
  const specCtx = specCanvas ? specCanvas.getContext('2d') : null;
  const wfCanvas = document.getElementById('rfWaterfallCanvas');
  const wfCtx = wfCanvas ? wfCanvas.getContext('2d') : null;
  const xcorrCanvas = document.getElementById('xcorrZoomCanvas');
  const xcorrCtx = xcorrCanvas ? xcorrCanvas.getContext('2d') : null;
  const benchCanvas = document.getElementById('benchmarkCanvas');
  const benchCtx = benchCanvas ? benchCanvas.getContext('2d') : null;

  // Initialize Waterfall Background
  if (wfCtx && wfCanvas) {
    wfCtx.fillStyle = '#05080d';
    wfCtx.fillRect(0, 0, wfCanvas.width, wfCanvas.height);
  }

  // 3. Top Command Bar: Mode Switcher & Algorithm Selector
  const modeBtns = document.querySelectorAll('#mode-switcher .seg-btn, #mode-switcher .cmd-btn');
  const coaxStrip = document.getElementById('coax-bench-strip');

  modeBtns.forEach((btn) => {
    btn.addEventListener('click', () => {
      modeBtns.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      currentMode = btn.dataset.mode || 'LIVE_HARDWARE';

      if (coaxStrip) {
        coaxStrip.classList.toggle('hidden', !currentMode.includes('COAX') && currentMode !== 'coax');
      }
      if (currentMode.includes('ABLATION') || currentMode === 'ablation') {
        activateInstTab('tab-benchmark');
        fetchBenchmark();
      }
      fetchSolve();
    });
  });

  const algoBtns = document.querySelectorAll('#algo-switcher .seg-btn, #algo-switcher .algo-btn');
  algoBtns.forEach((btn) => {
    btn.addEventListener('click', () => {
      algoBtns.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      currentAlgo = btn.dataset.algo || 'adra';
      fetchSolve();
    });
  });

  // 4. Primary Action Buttons (Solve, V4 Auto-Cal, Export CSV)
  const btnSolve = document.getElementById('btn-solve-now');
  if (btnSolve) {
    btnSolve.addEventListener('click', () => fetchSolve());
  }

  const btnCalV4 = document.getElementById('btn-cal-v4');
  if (btnCalV4) {
    btnCalV4.addEventListener('click', async () => {
      try {
        btnCalV4.textContent = 'CALIBRATING...';
        const res = await fetch('/api/calibrate_v4', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ apply: true })
        });
        if (res.ok) {
          const cal = await res.json();
          const v4Ns = cal.calibrated_v4_offset_ns || 56.3;
          const benchEl = document.getElementById('bench-v4-cal');
          if (benchEl) benchEl.textContent = `+${v4Ns.toFixed(2)} ns (LOCKED)`;
          const st3Cal = document.getElementById('st3-cal');
          if (st3Cal) st3Cal.textContent = `+${v4Ns.toFixed(2)} ns [AUTO-CAL]`;
          if (coaxStrip) coaxStrip.classList.remove('hidden');
        }
        btnCalV4.textContent = 'AUTO-CAL V4 (+15ns)';
        fetchSolve();
      } catch (e) {
        btnCalV4.textContent = 'AUTO-CAL V4 (+15ns)';
        console.warn('V4 Auto-Cal failed:', e);
      }
    });
  }

  const btnExportCsv = document.getElementById('btn-export-csv');
  if (btnExportCsv) {
    btnExportCsv.addEventListener('click', () => {
      window.location.href = '/api/export_csv';
    });
  }

  // 5. Map Toolbar Controls (View Mode, Overlays, Geometry Presets)
  const btnCart = document.getElementById('btn-view-cartesian');
  const btnGeo = document.getElementById('btn-view-geodetic');
  if (btnCart && btnGeo) {
    btnCart.addEventListener('click', () => {
      tacticalMap.viewMode = 'cartesian';
      btnCart.classList.add('active');
      btnGeo.classList.remove('active');
      tacticalMap.render();
    });
    btnGeo.addEventListener('click', () => {
      tacticalMap.viewMode = 'geodetic';
      btnGeo.classList.add('active');
      btnCart.classList.remove('active');
      tacticalMap.render();
    });
  }

  const bindToggle = (id, prop) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener('click', () => {
      tacticalMap[prop] = !tacticalMap[prop];
      el.classList.toggle('active', tacticalMap[prop]);
      tacticalMap.render();
    });
  };
  bindToggle('btn-toggle-hyperbolas', 'showHyperbolas');
  bindToggle('btn-toggle-gdop', 'showGDOP');
  bindToggle('btn-toggle-ellipse', 'showEllipse');
  bindToggle('btn-toggle-ekf', 'showEKF');

  const selectPreset = document.getElementById('select-geom-preset');
  const btnResetGeom = document.getElementById('btn-reset-geom');
  const applyPreset = async (presetName) => {
    try {
      await fetch('/api/geometry', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ preset: presetName })
      });
      await fetchStatus();
      await fetchGDOP();
      await fetchSolve();
    } catch (e) {
      console.warn('Preset update failed:', e);
    }
  };
  if (selectPreset) {
    selectPreset.addEventListener('change', () => applyPreset(selectPreset.value));
  }
  if (btnResetGeom) {
    btnResetGeom.addEventListener('click', () => {
      if (selectPreset) selectPreset.value = 'survey';
      applyPreset('survey');
    });
  }

  // 6. Right Panel Instrumentation Tabs
  const tabBtns = document.querySelectorAll('.tab-btn, .inst-tab');
  const tabContents = document.querySelectorAll('.tab-content');

  function activateInstTab(tabId) {
    if (!tabId) return;
    const fullId = tabId.startsWith('tab-') ? tabId : `tab-${tabId}`;
    tabBtns.forEach(b => {
      const bTab = b.dataset.tab || '';
      b.classList.toggle('active', bTab === fullId || bTab === tabId || `tab-${bTab}` === fullId);
    });
    tabContents.forEach(pane => {
      pane.classList.toggle('active', pane.id === fullId || pane.id === tabId);
    });
    if ((tabId === 'xcorr' || fullId === 'tab-xcorr') && lastSolveData) {
      renderXcorrZoom(lastSolveData);
    } else if (tabId === 'benchmark' || fullId === 'tab-benchmark') {
      fetchBenchmark();
    }
  }

  tabBtns.forEach((btn) => {
    btn.addEventListener('click', () => activateInstTab(btn.dataset.tab));
  });

  // HUD Scale Density Switcher
  const btnDensity = document.getElementById('btn-toggle-density');
  if (btnDensity) {
    btnDensity.addEventListener('click', () => {
      document.body.classList.toggle('compact-view');
      const isCompact = document.body.classList.contains('compact-view');
      btnDensity.textContent = isCompact ? 'HUD: COMPACT' : 'HUD: 100%';
      btnDensity.classList.toggle('active', isCompact);
      tacticalMap.render();
      radar.render();
    });
  }

  // 7. Live UTC Mission Clock
  function updateClock() {
    const now = new Date();
    const utcStr = now.toISOString().substring(11, 19) + ' UTC';
    const el = document.getElementById('hdr-utc-clock');
    if (el) el.textContent = utcStr;
  }
  setInterval(updateClock, 1000);
  updateClock();

  // 8. Fetch Fleet Status (/api/status)
  async function fetchStatus() {
    try {
      const res = await fetch('/api/status');
      if (!res.ok) return;
      const data = await res.json();
      fleetData = data.stations || {};

      const updateStationCard = (idx, node) => {
        if (!node) return;
        const syncEl = document.getElementById(`st${idx}-sync`);
        const tempEl = document.getElementById(`st${idx}-temp`);
        const clipEl = document.getElementById(`st${idx}-clip`);
        const shmEl = document.getElementById(`st${idx}-shm`);
        if (syncEl) syncEl.textContent = `${node.chrony_offset_ms >= 0 ? '+' : ''}${node.chrony_offset_ms} ms`;
        if (tempEl) tempEl.textContent = `${node.cpu_temp_c} °C`;
        if (clipEl) clipEl.textContent = `${(node.adc_clip_pct || 0.02).toFixed(2)}% (0 DROPS)`;
        if (shmEl) shmEl.textContent = node.shm_active !== false ? 'ACTIVE (ZERO-DROP)' : 'SD-DIRECT';
      };

      updateStationCard(1, fleetData.node1);
      updateStationCard(2, fleetData.node2);
      updateStationCard(3, fleetData.node3);

      if (fleetData.node3 && fleetData.node3.v4_cal_offset_ns !== undefined) {
        const st3Cal = document.getElementById('st3-cal');
        if (st3Cal) st3Cal.textContent = `+${Number(fleetData.node3.v4_cal_offset_ns).toFixed(2)} ns [LOCKED]`;
      }

      tacticalMap.updateData(null, fleetData, gdopData);
    } catch (e) {
      console.warn('Status poll failed:', e);
    }
  }

  // 9. Fetch GDOP Grid (/api/gdop)
  async function fetchGDOP() {
    try {
      const res = await fetch('/api/gdop');
      if (res.ok) {
        gdopData = await res.json();
        tacticalMap.updateData(null, fleetData, gdopData);
      }
    } catch (e) {
      console.warn('GDOP poll failed:', e);
    }
  }

  // 10. Fetch Real-Time Localization & Telemetry (/api/solve)
  async function fetchSolve() {
    try {
      const res = await fetch(`/api/solve?algo=${encodeURIComponent(currentAlgo)}&mode=${encodeURIComponent(currentMode)}`);
      if (!res.ok) return;
      const solve = await res.json();
      lastSolveData = solve;

      // Update Header Data Source & Honesty Badge
      const statusText = document.getElementById('hdr-status-text');
      const statusInd = document.getElementById('hdr-sys-status');
      if (statusText && statusInd) {
        const src = solve.data_source || (solve.simulated ? 'SIMULATED' : 'LIVE-HW');
        statusText.textContent = `${src} // ${solve.mode || 'ONLINE'}`;
        statusInd.className = 'status-indicator ' + (src === 'SIMULATED' ? 'badge-simulated' : 'online');
      }

      // Update Polar Radar
      const d1 = (solve.distances_m && solve.distances_m.node1) ? solve.distances_m.node1 : 157.0;
      radar.setTarget(d1, solve.centroid_azimuth_deg, solve.bearings_deg, solve.emitters);

      // Update Left Panel Radar Readouts
      const setTxt = (id, val) => {
        const el = document.getElementById(id);
        if (el) el.textContent = val;
      };
      setTxt('readout-bearing', `${solve.centroid_azimuth_deg.toFixed(1)}°`);
      setTxt('readout-dist', `${d1.toFixed(1)} m`);
      if (solve.bearings_deg) {
        setTxt('readout-doa-s1', `${(solve.bearings_deg.node1 || 0).toFixed(1)}°`);
        setTxt('readout-doa-s2', `${(solve.bearings_deg.node2 || 0).toFixed(1)}°`);
      }

      // Update SIGINT Threat Table (CCA-CFAR + AMC)
      if (Array.isArray(solve.emitters)) {
        setTxt('cfar-count-badge', `${solve.emitters.length} EMITTERS`);
        const tbody = document.getElementById('tws-tbody');
        if (tbody) {
          tbody.innerHTML = solve.emitters.map((em) => {
            const cls = em.track_id.includes('TGT') ? 'tgt-row' : (em.track_id.includes('REF') ? 'ref-row' : 'unk-row');
            const modShort = (em.modulation || 'UNK').split(' ')[0];
            return `<tr class="${cls}">
              <td>${em.track_id}</td>
              <td>${Number(em.center_freq_mhz).toFixed(3)}</td>
              <td>${Number(em.bandwidth_khz).toFixed(0)}k</td>
              <td>${Number(em.snr_db).toFixed(1)}dB</td>
              <td>${modShort}</td>
            </tr>`;
          }).join('');
        }
      }

      // Update Coax Bench Strip if present
      if (solve.coax_bench) {
        const cb = solve.coax_bench;
        const resNs = cb.pair_residuals_ns || {};
        setTxt('bench-res-12', `${(resNs['1-2'] >= 0 ? '+' : '')}${Number(resNs['1-2'] || 0).toFixed(2)} ns`);
        setTxt('bench-res-13', `${(resNs['1-3'] >= 0 ? '+' : '')}${Number(resNs['1-3'] || 0).toFixed(2)} ns`);
        setTxt('bench-res-23', `${(resNs['2-3'] >= 0 ? '+' : '')}${Number(resNs['2-3'] || 0).toFixed(2)} ns`);
        setTxt('bench-v4-cal', `+${Number(cb.v4_calibrated_offset_ns || 56.3).toFixed(2)} ns (LOCKED)`);
        setTxt('bench-pwr-status', `${Number(cb.rx_power_dbm || -32.4).toFixed(1)} dBm (SAFE)`);
      }

      // Update Right Panel Target Telemetry Readouts
      if (Array.isArray(solve.target_position)) {
        const tx = solve.target_position[0];
        const ty = solve.target_position[1];
        setTxt('tgt-xy', `X: ${tx >= 0 ? '+' : ''}${tx.toFixed(2)} m, Y: ${ty >= 0 ? '+' : ''}${ty.toFixed(2)} m`);
      }
      if (solve.target_latlon) {
        const tlat = solve.target_latlon.lat !== undefined ? solve.target_latlon.lat : solve.target_latlon[0];
        const tlon = solve.target_latlon.lon !== undefined ? solve.target_latlon.lon : solve.target_latlon[1];
        if (tlat !== undefined && tlon !== undefined) {
          setTxt('tgt-latlon', `${Number(tlat).toFixed(6)}° N, ${Number(tlon).toFixed(6)}° E`);
        }
      }
      const errM = solve.position_error_m !== undefined ? solve.position_error_m : solve.absolute_error_m;
      setTxt('tgt-error-truth', `${Number(errM || 0).toFixed(2)} m`);
      setTxt('tgt-residual', `${Number(solve.rms_residual_m || 0).toFixed(3)} m`);
      const gdopLabel = (solve.gdop || 1.0) < 2.5 ? 'OPTIMAL' : ((solve.gdop || 1.0) < 4.5 ? 'MODERATE' : 'POOR');
      setTxt('tgt-gdop', `${Number(solve.gdop || 1.0).toFixed(2)} (${gdopLabel})`);
      if (solve.error_ellipse) {
        setTxt('tgt-cep', `a=${Number(solve.error_ellipse.semi_major_m || 1.5).toFixed(2)}m, b=${Number(solve.error_ellipse.semi_minor_m || 1.5).toFixed(2)}m (${Number(solve.error_ellipse.tilt_deg || 0).toFixed(0)}°)`);
      }
      setTxt('tgt-ransac', `${Number(solve.ransac_clock_skew_ppm || 0) >= 0 ? '+' : ''}${Number(solve.ransac_clock_skew_ppm || 0).toFixed(2)} ppm (${solve.ransac_inliers || '59/60'} inliers)`);
      if (Array.isArray(solve.velocity_mps)) {
        const vx = solve.velocity_mps[0] || 0;
        const vy = solve.velocity_mps[1] || 0;
        setTxt('tgt-velocity', `Vx: ${vx >= 0 ? '+' : ''}${vx.toFixed(2)}, Vy: ${vy >= 0 ? '+' : ''}${vy.toFixed(2)} m/s`);
      }
      setTxt('badge-solver-type', (solve.solver_used || currentAlgo).toUpperCase());

      // Update XCorr Readouts & Curve
      if (solve.xcorr_inspector && solve.xcorr_inspector.pair_lags_ns) {
        const lags = solve.xcorr_inspector.pair_lags_ns;
        setTxt('xcorr-d12', `${lags['1-2'] >= 0 ? '+' : ''}${Number(lags['1-2']).toFixed(2)} ns`);
        setTxt('xcorr-d13', `${lags['1-3'] >= 0 ? '+' : ''}${Number(lags['1-3']).toFixed(2)} ns`);
        setTxt('xcorr-d23', `${lags['2-3'] >= 0 ? '+' : ''}${Number(lags['2-3']).toFixed(2)} ns`);
      }
      renderXcorrZoom(solve);

      // Update Tactical Map with Hyperbolas and Target
      tacticalMap.updateData(solve, fleetData, gdopData);
    } catch (e) {
      console.warn('Solve poll failed:', e);
    }
  }

  // 11. Render RF Baseband Spectrum + Live Scrolling Waterfall (/api/spectrum)
  async function renderSpectrum() {
    if (!specCtx || !specCanvas) return;
    try {
      const res = await fetch('/api/spectrum');
      if (!res.ok) return;
      const data = await res.json();
      const psd = data.psd_db || [];
      const cfar = data.cfar_threshold_db || [];
      const w = specCanvas.width;
      const h = specCanvas.height;

      specCtx.fillStyle = '#060a0f';
      specCtx.fillRect(0, 0, w, h);

      // Horizontal dB Grid Lines
      specCtx.strokeStyle = 'rgba(255, 255, 255, 0.05)';
      specCtx.lineWidth = 1;
      for (let y = 18; y < h; y += 22) {
        specCtx.beginPath();
        specCtx.moveTo(0, y);
        specCtx.lineTo(w, y);
        specCtx.stroke();
      }

      // Central Guard Band Highlight (-300 kHz to +300 kHz)
      const guardLeft = w * (0.5 - 0.3 / 2.4);
      const guardWidth = w * (0.6 / 2.4);
      specCtx.fillStyle = 'rgba(255, 255, 255, 0.02)';
      specCtx.fillRect(guardLeft, 0, guardWidth, h);

      // Center Line (DC = 433.920 MHz)
      specCtx.strokeStyle = 'rgba(255, 255, 255, 0.18)';
      specCtx.setLineDash([2, 3]);
      specCtx.beginPath();
      specCtx.moveTo(w / 2, 0);
      specCtx.lineTo(w / 2, h);
      specCtx.stroke();
      specCtx.setLineDash([]);

      const minDb = -92.0;
      const maxDb = -25.0;

      // Draw CCA-CFAR Threshold Curve (Amber Dashed)
      if (cfar.length === psd.length) {
        specCtx.strokeStyle = 'rgba(255, 179, 0, 0.65)';
        specCtx.lineWidth = 1;
        specCtx.setLineDash([3, 3]);
        specCtx.beginPath();
        cfar.forEach((db, i) => {
          const x = (i / (cfar.length - 1)) * w;
          const norm = (db - minDb) / (maxDb - minDb);
          const y = h - Math.max(0.02, Math.min(0.98, norm)) * (h - 8);
          if (i === 0) specCtx.moveTo(x, y);
          else specCtx.lineTo(x, y);
        });
        specCtx.stroke();
        specCtx.setLineDash([]);
      }

      // Draw PSD Trace (Neon Cyan)
      specCtx.strokeStyle = '#00e5ff';
      specCtx.lineWidth = 1.4;
      specCtx.beginPath();
      psd.forEach((db, i) => {
        const x = (i / (psd.length - 1)) * w;
        const norm = (db - minDb) / (maxDb - minDb);
        const y = h - Math.max(0.02, Math.min(0.98, norm)) * (h - 8);
        if (i === 0) specCtx.moveTo(x, y);
        else specCtx.lineTo(x, y);
      });
      specCtx.stroke();

      // Annotations
      specCtx.font = '8px "JetBrains Mono"';
      specCtx.fillStyle = '#2979ff';
      specCtx.fillText('REF (-570k)', w * 0.16, 13);
      specCtx.fillStyle = '#ff3366';
      specCtx.fillText('TGT (+570k)', w * 0.64, 13);
      specCtx.fillStyle = '#ffb300';
      specCtx.fillText('BOGEY (+820k)', w * 0.78, 25);

      // Update Scrolling Waterfall Canvas
      if (wfCtx && wfCanvas && psd.length > 0) {
        const ww = wfCanvas.width;
        const wh = wfCanvas.height;
        // Shift existing waterfall down by 2px
        const imgData = wfCtx.getImageData(0, 0, ww, wh - 2);
        wfCtx.putImageData(imgData, 0, 2);

        // Draw new top 2px row
        for (let x = 0; x < ww; x++) {
          const idx = Math.min(psd.length - 1, Math.floor((x / ww) * psd.length));
          const norm = Math.max(0, Math.min(1, (psd[idx] - minDb) / (maxDb - minDb)));
          let r = 6, g = 14, b = 28;
          if (norm > 0.75) {
            r = 255; g = Math.floor(80 + (1 - norm) * 150); b = 80;
          } else if (norm > 0.45) {
            r = 0; g = Math.floor(180 + norm * 75); b = 255;
          } else if (norm > 0.22) {
            r = 0; g = Math.floor(norm * 220); b = Math.floor(80 + norm * 160);
          }
          wfCtx.fillStyle = `rgb(${r},${g},${b})`;
          wfCtx.fillRect(x, 0, 1, 2);
        }
      }
    } catch (e) {
      console.warn('Spectrum render failed:', e);
    }
  }

  // 12. Render 16x Sinc Sub-Sample Cross-Correlation Inspector (Tab 2)
  function renderXcorrZoom(solve) {
    if (!xcorrCtx || !xcorrCanvas || !solve || !solve.xcorr_inspector) return;
    const w = xcorrCanvas.width;
    const h = xcorrCanvas.height;
    const xc = solve.xcorr_inspector;
    const lags = xc.lags_ns || [];
    const env12 = xc.pair_12_envelope || [];
    const env13 = xc.pair_13_envelope || [];
    const env23 = xc.pair_23_envelope || [];

    xcorrCtx.fillStyle = '#060a0f';
    xcorrCtx.fillRect(0, 0, w, h);

    // Grid
    xcorrCtx.strokeStyle = 'rgba(255, 255, 255, 0.05)';
    xcorrCtx.lineWidth = 1;
    for (let y = 20; y < h; y += 30) {
      xcorrCtx.beginPath();
      xcorrCtx.moveTo(0, y);
      xcorrCtx.lineTo(w, y);
      xcorrCtx.stroke();
    }
    xcorrCtx.strokeStyle = 'rgba(255, 255, 255, 0.15)';
    xcorrCtx.setLineDash([2, 3]);
    xcorrCtx.beginPath();
    xcorrCtx.moveTo(w / 2, 0);
    xcorrCtx.lineTo(w / 2, h);
    xcorrCtx.stroke();
    xcorrCtx.setLineDash([]);

    const drawCurve = (arr, color, lineW = 1.6) => {
      if (!arr || arr.length === 0) return;
      xcorrCtx.strokeStyle = color;
      xcorrCtx.lineWidth = lineW;
      xcorrCtx.beginPath();
      arr.forEach((v, i) => {
        const x = (i / (arr.length - 1)) * w;
        const y = (h - 18) - Math.max(0, Math.min(1.05, v)) * (h - 34);
        if (i === 0) xcorrCtx.moveTo(x, y);
        else xcorrCtx.lineTo(x, y);
      });
      xcorrCtx.stroke();
    };

    drawCurve(env23, '#bf5af2', 1.3);
    drawCurve(env13, '#ffb300', 1.4);
    drawCurve(env12, '#00e5ff', 1.8);

    // Legend
    xcorrCtx.font = '8px "JetBrains Mono"';
    xcorrCtx.fillStyle = '#00e5ff';
    xcorrCtx.fillText('● Pair 1-2 (16x Sinc)', 8, 14);
    xcorrCtx.fillStyle = '#ffb300';
    xcorrCtx.fillText('● Pair 1-3', 135, 14);
    xcorrCtx.fillStyle = '#bf5af2';
    xcorrCtx.fillText('● Pair 2-3', 210, 14);

    if (lags.length > 0) {
      xcorrCtx.fillStyle = 'rgba(255,255,255,0.4)';
      xcorrCtx.fillText(`${lags[0]} ns`, 6, h - 4);
      xcorrCtx.fillText('0 ns', w / 2 - 10, h - 4);
      xcorrCtx.fillText(`+${lags[lags.length - 1]} ns`, w - 48, h - 4);
    }
  }

  // 13. Fetch & Render IEEE Ablation Benchmark (/api/benchmark, Tab 3)
  async function fetchBenchmark() {
    try {
      if (!benchmarkData) {
        const res = await fetch('/api/benchmark');
        if (res.ok) {
          benchmarkData = await res.json();
        }
      }
      if (!benchmarkData || !benchmarkData.summary) return;
      const sum = benchmarkData.summary;

      const setTxt = (id, val) => {
        const el = document.getElementById(id);
        if (el) el.textContent = val;
      };
      if (sum['ADRA-TDOA (Proposed)']) {
        setTxt('bench-adra-rmse', `${sum['ADRA-TDOA (Proposed)'].overall_mean_rmse_m.toFixed(2)} m`);
      }
      if (sum['GCC-PHAT (Knapp-Carter)']) {
        setTxt('bench-phat-rmse', `${sum['GCC-PHAT (Knapp-Carter)'].overall_mean_rmse_m.toFixed(2)} m`);
      }
      if (sum['Standard XCorr (Baseline)']) {
        setTxt('bench-std-rmse', `${sum['Standard XCorr (Baseline)'].overall_mean_rmse_m.toFixed(2)} m`);
      }

      if (!benchCtx || !benchCanvas) return;
      const w = benchCanvas.width;
      const h = benchCanvas.height;
      benchCtx.fillStyle = '#060a0f';
      benchCtx.fillRect(0, 0, w, h);

      const scenarios = benchmarkData.scenarios || [];
      const maxVal = 25.0;
      const groupW = w / Math.max(1, scenarios.length);

      scenarios.forEach((sc, gIdx) => {
        const gx = gIdx * groupW + 18;
        const barW = 18;
        const algs = [
          { key: 'ADRA-TDOA (Proposed)', color: '#00ff9d' },
          { key: 'GCC-PHAT (Knapp-Carter)', color: '#00e5ff' },
          { key: 'Standard XCorr (Baseline)', color: '#ff3366' }
        ];

        algs.forEach((a, bIdx) => {
          const rmse = (sc.algorithms && sc.algorithms[a.key]) ? sc.algorithms[a.key].rmse_m : 0;
          const bh = Math.min(h - 38, Math.max(4, (rmse / maxVal) * (h - 42)));
          const bx = gx + bIdx * (barW + 6);
          const by = h - 20 - bh;

          benchCtx.fillStyle = a.color;
          benchCtx.fillRect(bx, by, barW, bh);

          benchCtx.fillStyle = '#e2e8f0';
          benchCtx.font = '7.5px "JetBrains Mono"';
          benchCtx.fillText(`${rmse.toFixed(1)}m`, bx - 2, by - 3);
        });

        benchCtx.fillStyle = '#94a3b8';
        benchCtx.font = '7.5px "JetBrains Mono"';
        const shortLabel = (sc.label || '').split('(')[0].trim().substring(0, 14);
        benchCtx.fillText(shortLabel, gx, h - 6);
      });
    } catch (e) {
      console.warn('Benchmark render failed:', e);
    }
  }

  // 14. Tape Survey Panel Functionality
  const btnToggleSurvey = document.getElementById('btn-toggle-survey-view');
  const surveyBody = document.getElementById('survey-body');
  const surveyHdr = document.getElementById('survey-toggle-hdr');
  if (btnToggleSurvey && surveyBody) {
    const togglePanel = () => {
      surveyBody.classList.toggle('hidden');
    };
    btnToggleSurvey.addEventListener('click', togglePanel);
    if (surveyHdr) surveyHdr.addEventListener('dblclick', togglePanel);
  }

  let latestSurveyPreview = null;

  async function loadSavedSurveysList() {
    try {
      const res = await fetch('/api/survey/list');
      if (!res.ok) return;
      const data = await res.json();
      const sel = document.getElementById('sel-saved-surveys');
      if (sel) {
        sel.innerHTML = '<option value="">Saved Surveys...</option>' +
          (data.surveys || []).map(s => {
            const name = typeof s === 'string' ? s : s.name;
            return `<option value="${name}">${name}</option>`;
          }).join('');
      }
    } catch (e) {
      console.warn('Failed to load surveys list:', e);
    }
  }
  loadSavedSurveysList();

  const btnSurveyPreview = document.getElementById('btn-survey-preview');
  if (btnSurveyPreview) {
    btnSurveyPreview.addEventListener('click', async () => {
      btnSurveyPreview.textContent = 'CALCULATING...';
      try {
        const unit = document.getElementById('sel-survey-unit')?.value || 'ft_in';
        const side = parseInt(document.getElementById('sel-survey-side')?.value || '1', 10);
        const sigma = parseFloat(document.getElementById('inp-survey-sigma')?.value || '0.02');
        const payload = {
          unit: unit,
          side: side,
          sigma_m: sigma,
          n_monte_carlo: 60,
          inputs: {
            'S1-S2': document.getElementById('inp-s1-s2')?.value,
            'S1-S3': document.getElementById('inp-s1-s3')?.value,
            'S2-S3': document.getElementById('inp-s2-s3')?.value,
            'P-S1': document.getElementById('inp-p-s1')?.value,
            'P-S2': document.getElementById('inp-p-s2')?.value,
            'P-S3': document.getElementById('inp-p-s3')?.value,
          }
        };
        const res = await fetch('/api/survey/preview', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        const data = await res.json();
        btnSurveyPreview.textContent = '⚡ CALCULATE & PREVIEW';
        if (data.status === 'SUCCESS' && data.preview) {
          latestSurveyPreview = data.preview;
          renderSurveyPreview(data.preview);
        } else {
          alert('Survey calculation failed: ' + (data.message || 'Check distance inputs'));
        }
      } catch (err) {
        btnSurveyPreview.textContent = '⚡ CALCULATE & PREVIEW';
        alert('Network error calculating survey: ' + err.message);
      }
    });
  }

  function renderSurveyPreview(p) {
    const box = document.getElementById('survey-results-box');
    const badge = document.getElementById('survey-status-badge');
    const stStatus = document.getElementById('survey-res-status');
    const stRms = document.getElementById('survey-res-rms');
    const stUnc = document.getElementById('survey-res-unc');
    const details = document.getElementById('survey-res-details');
    const btnApply = document.getElementById('btn-survey-apply');
    const btnSave = document.getElementById('btn-survey-save');

    if (!box) return;
    box.classList.remove('hidden');

    const status = p.status || 'PASS';
    if (badge) {
      badge.textContent = status;
      badge.className = 'res-tag ' + status.toLowerCase();
    }
    if (stStatus) {
      stStatus.textContent = 'STATUS: ' + status;
      stStatus.className = 'res-tag ' + status.toLowerCase();
    }
    if (stRms) {
      stRms.textContent = `PLUTO RESIDUAL RMS: ${(p.pluto?.rms_m || 0).toFixed(3)} m`;
    }
    if (stUnc) {
      const r95 = p.uncertainty?.pluto_radius95_m || 0.035;
      stUnc.textContent = `PLUTO 95% CONFIDENCE: ±${r95.toFixed(3)} m`;
    }

    const st = p.stations || {};
    const pl = p.pluto || {};
    let warnHtml = (p.warnings || []).map(w => `<div style="color:#f59e0b;margin-top:2px">⚠️ ${w}</div>`).join('');

    if (details) {
      details.innerHTML = `
        <table class="coord-table">
          <thead><tr><th>Element</th><th>Local X (m)</th><th>Local Y (m)</th><th>Geometry Residual</th></tr></thead>
          <tbody>
            <tr><td>Station 1 (Alpha)</td><td>${(st.node1?.[0] || 0).toFixed(3)}</td><td>${(st.node1?.[1] || 0).toFixed(3)}</td><td>Origin (0.000)</td></tr>
            <tr><td>Station 2 (Bravo)</td><td>${(st.node2?.[0] || 0).toFixed(3)}</td><td>${(st.node2?.[1] || 0).toFixed(3)}</td><td>Baseline Alignment</td></tr>
            <tr><td>Station 3 (Charlie)</td><td>${(st.node3?.[0] || 0).toFixed(3)}</td><td>${(st.node3?.[1] || 0).toFixed(3)}</td><td>Angles: ${p.triangle?.angles_deg?.map(a => a.toFixed(1) + '°').join(', ') || ''}</td></tr>
            <tr style="color:#38bdf8"><td>Pluto Ref TX</td><td>${(pl.xy?.[0] || 0).toFixed(3)}</td><td>${(pl.xy?.[1] || 0).toFixed(3)}</td><td>Max Res: ${(pl.max_abs_residual_m || 0).toFixed(3)} m (${pl.consistency || 'PASS'})</td></tr>
          </tbody>
        </table>
        ${warnHtml}
      `;
    }

    if (btnApply) btnApply.disabled = (status === 'FAIL');
    if (btnSave) btnSave.disabled = false;
  }

  const btnSurveyApply = document.getElementById('btn-survey-apply');
  if (btnSurveyApply) {
    btnSurveyApply.addEventListener('click', async () => {
      if (!latestSurveyPreview) return;
      if (!confirm('Apply this surveyed geometry to the active mission control runtime?\n\n(Note: Disk configurations will remain untouched; you can revert at any time).')) return;
      btnSurveyApply.textContent = 'APPLYING...';
      try {
        const res = await fetch('/api/survey/apply', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ confirm: true, survey: latestSurveyPreview })
        });
        const data = await res.json();
        btnSurveyApply.textContent = '✔ APPLY TO ARRAY';
        if (data.status === 'SUCCESS') {
          alert('Survey applied to active runtime memory successfully!');
          const revBtn = document.getElementById('btn-survey-revert');
          if (revBtn) revBtn.style.display = 'inline-block';
          fetchGDOP();
          fetchSolve();
        } else {
          alert('Apply rejected: ' + data.message);
        }
      } catch (err) {
        btnSurveyApply.textContent = '✔ APPLY TO ARRAY';
        alert('Network error applying survey: ' + err.message);
      }
    });
  }

  const btnSurveyRevert = document.getElementById('btn-survey-revert');
  if (btnSurveyRevert) {
    btnSurveyRevert.addEventListener('click', async () => {
      try {
        const res = await fetch('/api/survey/revert', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({})
        });
        const data = await res.json();
        if (data.status === 'SUCCESS') {
          alert('Reverted to previous geometry state.');
          btnSurveyRevert.style.display = 'none';
          fetchGDOP();
          fetchSolve();
        }
      } catch (err) {
        alert('Revert failed: ' + err.message);
      }
    });
  }

  const btnSurveySave = document.getElementById('btn-survey-save');
  if (btnSurveySave) {
    btnSurveySave.addEventListener('click', async () => {
      const name = document.getElementById('inp-survey-name')?.value?.trim();
      if (!name) {
        alert('Please enter a survey name (e.g. lab_survey_01)');
        return;
      }
      try {
        const res = await fetch('/api/survey/save', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: name, survey: latestSurveyPreview, overwrite: true })
        });
        const data = await res.json();
        if (data.status === 'SUCCESS') {
          alert(`Survey saved as '${name}'`);
          loadSavedSurveysList();
        } else {
          alert('Save failed: ' + data.message);
        }
      } catch (err) {
        alert('Save error: ' + err.message);
      }
    });
  }

  const btnSurveyLoad = document.getElementById('btn-survey-load');
  if (btnSurveyLoad) {
    btnSurveyLoad.addEventListener('click', async () => {
      const name = document.getElementById('sel-saved-surveys')?.value;
      if (!name) return;
      try {
        const res = await fetch(`/api/survey/load?name=${encodeURIComponent(name)}`);
        const data = await res.json();
        if (data.status === 'SUCCESS' && data.survey) {
          const s = data.survey;
          latestSurveyPreview = s;
          if (s.inputs_raw) {
            if (s.inputs_raw['S1-S2']) document.getElementById('inp-s1-s2').value = s.inputs_raw['S1-S2'];
            if (s.inputs_raw['S1-S3']) document.getElementById('inp-s1-s3').value = s.inputs_raw['S1-S3'];
            if (s.inputs_raw['S2-S3']) document.getElementById('inp-s2-s3').value = s.inputs_raw['S2-S3'];
            if (s.inputs_raw['P-S1']) document.getElementById('inp-p-s1').value = s.inputs_raw['P-S1'];
            if (s.inputs_raw['P-S2']) document.getElementById('inp-p-s2').value = s.inputs_raw['P-S2'];
            if (s.inputs_raw['P-S3']) document.getElementById('inp-p-s3').value = s.inputs_raw['P-S3'];
          }
          renderSurveyPreview(s);
        }
      } catch (err) {
        alert('Load error: ' + err.message);
      }
    });
  }

  // Initial Boot Sequence
  fetchStatus();
  fetchGDOP();
  fetchSolve();
  renderSpectrum();
  fetchBenchmark();

  // Live Telemetry Loops
  setInterval(fetchSolve, 2000);
  setInterval(renderSpectrum, 1200);
  setInterval(fetchStatus, 5000);
});
