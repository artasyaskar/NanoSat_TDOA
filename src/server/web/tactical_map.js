/**
 * tactical_map.js - Dual-Mode Tactical Multilateration & What-If Geometry Designer
 * Supports:
 *   - 2D Cartesian Tactical Grid vs. WGS84 Geodetic Graticule Projection
 *   - Interactive Drag-and-Drop of Receiver Nodes (S1, S2, S3), Reference TX, and Target
 *   - TDOA Hyperbolic Isochrones, 95% CEP Error Ellipse, GDOP Heatmap, and CV-EKF Trajectory Trail
 */

class TacticalMap {
  constructor(canvasId, onGeometryChange = null) {
    this.canvas = document.getElementById(canvasId);
    this.ctx = this.canvas.getContext('2d');
    this.width = this.canvas.width;
    this.height = this.canvas.height;
    this.onGeometryChange = onGeometryChange;

    // View Transformation: world meters to canvas pixels
    this.scale = 0.80;
    this.originX = this.width / 2 - 35;
    this.originY = this.height / 2 + 55;

    // Geodetic Origin (Islamabad / NUST Campus Survey Datum)
    this.lat0 = 33.000000;
    this.lon0 = 73.000000;

    // Layer Visibility & Mode Toggles
    this.viewMode = 'cartesian'; // 'cartesian' | 'geodetic'
    this.showHyperbolas = true;
    this.showGDOP = false;
    this.showEllipse = true;
    this.showEKF = true;

    // Data State (Default: Lab 10-Meter Equilateral Active Hardware Preset)
    this.stations = {
      node1: { name: 'Alpha (S1)', x: -4.30, y: -1.28, tuner: 'R820T2' },
      node2: { name: 'Bravo (S2)', x: 3.73, y: -1.28, tuner: 'R820T2' },
      node3: { name: 'Charlie (S3)', x: 0.56, y: 2.57, tuner: 'R828D' }
    };
    this.reference = { x: -2.20, y: -0.65, name: 'Pluto Ref' };
    this.target = { x: -1.93, y: -0.51 };
    this.truthTarget = { x: -1.93, y: -0.51 };
    this.velocity = [0.0, 0.0];
    this.ellipse = { semi_major_m: 1.27, semi_minor_m: 0.58, tilt_deg: -157.0 };
    this.hyperbolas = {};
    this.ekfTrail = [];
    this.gdopGrid = null;

    // Drag-and-Drop State
    this.draggingItem = null; // { type: 'station'|'ref'|'target', key: 'node1' }
    this.hoverItem = null;

    this.initInteractions();
    this.render();
  }

  worldToCanvas(x, y) {
    return {
      cx: this.originX + x * this.scale,
      cy: this.originY - y * this.scale
    };
  }

  canvasToWorld(cx, cy) {
    return {
      x: (cx - this.originX) / this.scale,
      y: (this.originY - cy) / this.scale
    };
  }

  xyToLatLon(x, y) {
    const lat = this.lat0 + y / 111320.0;
    const lon = this.lon0 + x / (111320.0 * Math.cos((this.lat0 * Math.PI) / 180.0));
    return { lat, lon };
  }

  initInteractions() {
    const getCanvasCoords = (e) => {
      const rect = this.canvas.getBoundingClientRect();
      const scaleX = this.canvas.width / rect.width;
      const scaleY = this.canvas.height / rect.height;
      return {
        cx: (e.clientX - rect.left) * scaleX,
        cy: (e.clientY - rect.top) * scaleY
      };
    };

    const findHitObject = (cx, cy) => {
      const hitRadius = 14;
      for (const [sid, st] of Object.entries(this.stations)) {
        const p = this.worldToCanvas(st.x, st.y);
        if (Math.hypot(cx - p.cx, cy - p.cy) <= hitRadius) {
          return { type: 'station', key: sid };
        }
      }
      const rp = this.worldToCanvas(this.reference.x, this.reference.y);
      if (Math.hypot(cx - rp.cx, cy - rp.cy) <= hitRadius) {
        return { type: 'ref', key: 'reference' };
      }
      const tp = this.worldToCanvas(this.truthTarget.x, this.truthTarget.y);
      if (Math.hypot(cx - tp.cx, cy - tp.cy) <= hitRadius) {
        return { type: 'target', key: 'target' };
      }
      return null;
    };

    this.canvas.addEventListener('mousedown', (e) => {
      const { cx, cy } = getCanvasCoords(e);
      const hit = findHitObject(cx, cy);
      if (hit) {
        this.draggingItem = hit;
        this.canvas.style.cursor = 'grabbing';
      }
    });

    this.canvas.addEventListener('mousemove', (e) => {
      const { cx, cy } = getCanvasCoords(e);
      const w = this.canvasToWorld(cx, cy);
      const ll = this.xyToLatLon(w.x, w.y);

      const cursorEl = document.getElementById('map-cursor-readout');
      if (cursorEl) {
        if (this.viewMode === 'geodetic') {
          cursorEl.textContent = `LAT: ${ll.lat.toFixed(6)}° N | LON: ${ll.lon.toFixed(6)}° E  [X: ${w.x >= 0 ? '+' : ''}${w.x.toFixed(1)}m, Y: ${w.y >= 0 ? '+' : ''}${w.y.toFixed(1)}m]`;
        } else {
          cursorEl.textContent = `CURSOR X: ${w.x >= 0 ? '+' : ''}${w.x.toFixed(1)}m | Y: ${w.y >= 0 ? '+' : ''}${w.y.toFixed(1)}m  [${ll.lat.toFixed(5)}°N, ${ll.lon.toFixed(5)}°E]`;
        }
      }

      if (this.draggingItem) {
        const rx = Math.round(w.x * 2) / 2;
        const ry = Math.round(w.y * 2) / 2;
        if (this.draggingItem.type === 'station') {
          const st = this.stations[this.draggingItem.key];
          if (st) {
            st.x = rx;
            st.y = ry;
          }
        } else if (this.draggingItem.type === 'ref') {
          this.reference.x = rx;
          this.reference.y = ry;
        } else if (this.draggingItem.type === 'target') {
          this.truthTarget.x = rx;
          this.truthTarget.y = ry;
          this.target.x = rx;
          this.target.y = ry;
        }
        this.render();
      } else {
        const hit = findHitObject(cx, cy);
        this.hoverItem = hit;
        this.canvas.style.cursor = hit ? 'grab' : 'crosshair';
      }
    });

    const endDrag = () => {
      if (this.draggingItem) {
        this.draggingItem = null;
        this.canvas.style.cursor = 'crosshair';
        if (typeof this.onGeometryChange === 'function') {
          const stPayload = {};
          for (const [sid, st] of Object.entries(this.stations)) {
            stPayload[sid] = { x: st.x, y: st.y };
          }
          this.onGeometryChange({
            stations: stPayload,
            reference: { x: this.reference.x, y: this.reference.y },
            target: { x: this.truthTarget.x, y: this.truthTarget.y }
          });
        }
      }
    };

    this.canvas.addEventListener('mouseup', endDrag);
    this.canvas.addEventListener('mouseleave', endDrag);
  }

  updateData(solveData, fleetData, gdopData) {
    if (fleetData && Object.keys(fleetData).length > 0 && !this.draggingItem) {
      this.stations = fleetData;
    } else if (solveData && solveData.stations && Object.keys(solveData.stations).length > 0 && !this.draggingItem) {
      this.stations = solveData.stations;
    }
    if (solveData && !this.draggingItem) {
      this.target.x = solveData.target_position[0];
      this.target.y = solveData.target_position[1];
      if (solveData.truth_position) {
        this.truthTarget.x = solveData.truth_position[0];
        this.truthTarget.y = solveData.truth_position[1];
      }
      if (solveData.reference_position) {
        this.reference.x = solveData.reference_position[0];
        this.reference.y = solveData.reference_position[1];
      }
      this.velocity = solveData.velocity_mps || [0.0, 0.0];
      this.ellipse = solveData.error_ellipse;
      this.hyperbolas = solveData.hyperbolas || {};
      this.ekfTrail = solveData.ekf_trail || [];
    }
    if (gdopData) {
      this.gdopGrid = gdopData;
    }
    this.render();
  }

  render() {
    const ctx = this.ctx;
    ctx.clearRect(0, 0, this.width, this.height);

    // Auto-scale detection (Lab 10m array vs Outdoor 500m array)
    let maxSpan = 0;
    for (const st of Object.values(this.stations)) {
      if (st && typeof st.x === 'number' && typeof st.y === 'number') {
        maxSpan = Math.max(maxSpan, Math.hypot(st.x, st.y));
      }
    }
    const isLabScale = maxSpan > 0.1 && maxSpan < 35;
    this.scale = isLabScale ? 18.0 : 0.80;
    this.originX = this.width / 2;
    this.originY = this.height / 2;

    // 1. Tactical Background (Cartesian vs Geodetic Terrain Dark Tone)
    if (this.viewMode === 'geodetic') {
      const grad = ctx.createRadialGradient(this.width / 2, this.height / 2, 30, this.width / 2, this.height / 2, this.width * 0.7);
      grad.addColorStop(0, '#0a1622');
      grad.addColorStop(1, '#050a10');
      ctx.fillStyle = grad;
      ctx.fillRect(0, 0, this.width, this.height);
    } else {
      ctx.fillStyle = '#060a0f';
      ctx.fillRect(0, 0, this.width, this.height);
    }

    // 2. Grid Lines (Cartesian Meters vs Geodetic Lat/Lon Graticules)
    const gridStep = isLabScale ? 2.0 : 50.0;
    const minGX = isLabScale ? -16 : -400;
    const maxGX = isLabScale ? 16 : 600;
    const minGY = isLabScale ? -10 : -300;
    const maxGY = isLabScale ? 10 : 500;
    ctx.strokeStyle = this.viewMode === 'geodetic' ? 'rgba(0, 229, 255, 0.07)' : 'rgba(255, 255, 255, 0.04)';
    ctx.lineWidth = 1;

    for (let gx = minGX; gx <= maxGX; gx += gridStep) {
      const p1 = this.worldToCanvas(gx, minGY);
      ctx.beginPath();
      ctx.moveTo(p1.cx, 0);
      ctx.lineTo(p1.cx, this.height);
      ctx.stroke();

      const labelCond = isLabScale ? (gx % 4 === 0) : (gx % 100 === 0);
      if (labelCond) {
        ctx.fillStyle = this.viewMode === 'geodetic' ? 'rgba(0, 229, 255, 0.38)' : 'rgba(255, 255, 255, 0.22)';
        ctx.font = '8px "JetBrains Mono"';
        if (this.viewMode === 'geodetic') {
          const ll = this.xyToLatLon(gx, 0);
          ctx.fillText(`${ll.lon.toFixed(5)}°E`, p1.cx + 2, this.height - 5);
        } else {
          ctx.fillText(`${gx}m`, p1.cx + 2, this.height - 5);
        }
      }
    }

    for (let gy = minGY; gy <= maxGY; gy += gridStep) {
      const p = this.worldToCanvas(0, gy);
      ctx.beginPath();
      ctx.moveTo(0, p.cy);
      ctx.lineTo(this.width, p.cy);
      ctx.stroke();

      const labelCond = isLabScale ? (gy % 4 === 0) : (gy % 100 === 0);
      if (labelCond) {
        ctx.fillStyle = this.viewMode === 'geodetic' ? 'rgba(0, 229, 255, 0.38)' : 'rgba(255, 255, 255, 0.22)';
        ctx.font = '8px "JetBrains Mono"';
        if (this.viewMode === 'geodetic') {
          const ll = this.xyToLatLon(0, gy);
          ctx.fillText(`${ll.lat.toFixed(5)}°N`, 4, p.cy - 3);
        } else {
          ctx.fillText(`${gy}m`, 4, p.cy - 3);
        }
      }
    }

    // 3. Axis Center Lines (0, 0 Datum)
    const o = this.worldToCanvas(0, 0);
    ctx.strokeStyle = 'rgba(0, 229, 255, 0.22)';
    ctx.lineWidth = 1.2;
    ctx.beginPath();
    ctx.moveTo(o.cx, 0);
    ctx.lineTo(o.cx, this.height);
    ctx.moveTo(0, o.cy);
    ctx.lineTo(this.width, o.cy);
    ctx.stroke();

    // 4. GDOP Heatmap Overlay (if enabled)
    if (this.showGDOP && this.gdopGrid) {
      this.renderGDOP(ctx);
    }

    // 5. Draw Baseline Triangle Between Receiver Stations
    const stNames = Object.keys(this.stations);
    if (stNames.length >= 3) {
      const p1 = this.worldToCanvas(this.stations[stNames[0]].x, this.stations[stNames[0]].y);
      const p2 = this.worldToCanvas(this.stations[stNames[1]].x, this.stations[stNames[1]].y);
      const p3 = this.worldToCanvas(this.stations[stNames[2]].x, this.stations[stNames[2]].y);

      ctx.fillStyle = 'rgba(0, 255, 157, 0.035)';
      ctx.strokeStyle = 'rgba(0, 255, 157, 0.28)';
      ctx.lineWidth = 1.2;
      ctx.setLineDash([4, 4]);
      ctx.beginPath();
      ctx.moveTo(p1.cx, p1.cy);
      ctx.lineTo(p2.cx, p2.cy);
      ctx.lineTo(p3.cx, p3.cy);
      ctx.closePath();
      ctx.fill();
      ctx.stroke();
      ctx.setLineDash([]);
    }

    // 6. Draw TDOA Hyperbolic Isochrone Curves
    if (this.showHyperbolas && this.hyperbolas) {
      const colors = ['#00e5ff', '#ffb300', '#bf5af2'];
      let cIdx = 0;
      for (const [pairKey, pts] of Object.entries(this.hyperbolas)) {
        if (!pts || pts.length < 2) continue;
        ctx.strokeStyle = colors[cIdx % colors.length];
        ctx.shadowColor = colors[cIdx % colors.length];
        ctx.shadowBlur = 8;
        ctx.lineWidth = 2.0;
        ctx.beginPath();
        pts.forEach((pt, idx) => {
          const cp = this.worldToCanvas(pt[0], pt[1]);
          if (idx === 0) ctx.moveTo(cp.cx, cp.cy);
          else ctx.lineTo(cp.cx, cp.cy);
        });
        ctx.stroke();
        ctx.shadowBlur = 0;
        cIdx++;
      }
    }

    // 7. Draw CV-EKF Trajectory Trail (Innovation 5)
    if (this.showEKF && Array.isArray(this.ekfTrail) && this.ekfTrail.length > 1) {
      ctx.strokeStyle = 'rgba(0, 255, 157, 0.55)';
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      this.ekfTrail.forEach((pt, idx) => {
        const cp = this.worldToCanvas(pt[0], pt[1]);
        if (idx === 0) ctx.moveTo(cp.cx, cp.cy);
        else ctx.lineTo(cp.cx, cp.cy);
      });
      ctx.stroke();

      this.ekfTrail.forEach((pt, idx) => {
        const cp = this.worldToCanvas(pt[0], pt[1]);
        const alpha = 0.2 + 0.6 * (idx / this.ekfTrail.length);
        ctx.fillStyle = `rgba(0, 255, 157, ${alpha.toFixed(2)})`;
        ctx.beginPath();
        ctx.arc(cp.cx, cp.cy, 2.2, 0, Math.PI * 2);
        ctx.fill();
      });
    }

    // 8. Draw 95% Confidence Error Ellipse (CRLB)
    if (this.showEllipse && this.ellipse) {
      const tcp = this.worldToCanvas(this.target.x, this.target.y);
      const a_px = Math.max(9, this.ellipse.semi_major_m * this.scale);
      const b_px = Math.max(9, this.ellipse.semi_minor_m * this.scale);
      const ang_rad = (this.ellipse.tilt_deg * Math.PI) / 180;

      ctx.save();
      ctx.translate(tcp.cx, tcp.cy);
      ctx.rotate(-ang_rad);
      ctx.strokeStyle = 'rgba(255, 179, 0, 0.88)';
      ctx.lineWidth = 1.5;
      ctx.setLineDash([3, 3]);
      ctx.fillStyle = 'rgba(255, 179, 0, 0.09)';
      ctx.beginPath();
      ctx.ellipse(0, 0, a_px, b_px, 0, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
      ctx.restore();
    }

    // 9. Draw Station Nodes (S1, S2, S3)
    for (const [sid, st] of Object.entries(this.stations)) {
      const sp = this.worldToCanvas(st.x, st.y);
      const isV4 = st.tuner && st.tuner.includes('R828D');
      const nodeColor = isV4 ? '#bf5af2' : '#00ff9d';

      ctx.fillStyle = nodeColor;
      ctx.shadowColor = nodeColor;
      ctx.shadowBlur = 10;
      ctx.beginPath();
      ctx.arc(sp.cx, sp.cy, 5.5, 0, Math.PI * 2);
      ctx.fill();
      ctx.shadowBlur = 0;

      ctx.strokeStyle = '#ffffff';
      ctx.lineWidth = 1.2;
      ctx.strokeRect(sp.cx - 7, sp.cy - 7, 14, 14);

      ctx.fillStyle = '#ffffff';
      ctx.font = 'bold 9px "JetBrains Mono"';
      const label = (st.name || sid).toUpperCase();
      ctx.fillText(label, sp.cx + 10, sp.cy - 1);

      ctx.fillStyle = 'rgba(255, 255, 255, 0.55)';
      ctx.font = '7.5px "JetBrains Mono"';
      ctx.fillText(`(${st.x.toFixed(0)}, ${st.y.toFixed(0)}m)`, sp.cx + 10, sp.cy + 9);
    }

    // 10. Draw Reference Transmitter (Pluto -570 kHz)
    const rp = this.worldToCanvas(this.reference.x, this.reference.y);
    ctx.fillStyle = '#2979ff';
    ctx.shadowColor = '#2979ff';
    ctx.shadowBlur = 9;
    ctx.beginPath();
    ctx.arc(rp.cx, rp.cy, 6, 0, Math.PI * 2);
    ctx.fill();
    ctx.shadowBlur = 0;

    ctx.strokeStyle = '#2979ff';
    ctx.lineWidth = 1.2;
    ctx.beginPath();
    ctx.arc(rp.cx, rp.cy, 10, 0, Math.PI * 2);
    ctx.stroke();

    ctx.fillStyle = '#60a5fa';
    ctx.font = 'bold 9px "JetBrains Mono"';
    ctx.fillText('REF [PLUTO -570k]', rp.cx + 13, rp.cy + 3);

    // 11. Draw Localized Target Point (bladeRF +570 kHz)
    const tp = this.worldToCanvas(this.target.x, this.target.y);
    ctx.fillStyle = '#ff3366';
    ctx.shadowColor = '#ff3366';
    ctx.shadowBlur = 14;
    ctx.beginPath();
    ctx.arc(tp.cx, tp.cy, 6, 0, Math.PI * 2);
    ctx.fill();
    ctx.shadowBlur = 0;

    ctx.strokeStyle = '#ffffff';
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(tp.cx - 10, tp.cy);
    ctx.lineTo(tp.cx + 10, tp.cy);
    ctx.moveTo(tp.cx, tp.cy - 10);
    ctx.lineTo(tp.cx, tp.cy + 10);
    ctx.stroke();

    ctx.fillStyle = '#ff3366';
    ctx.font = 'bold 9.5px "JetBrains Mono"';
    if (this.viewMode === 'geodetic') {
      const tll = this.xyToLatLon(this.target.x, this.target.y);
      ctx.fillText(`TGT-ALPHA [${tll.lat.toFixed(5)}°N, ${tll.lon.toFixed(5)}°E]`, tp.cx + 12, tp.cy - 6);
    } else {
      ctx.fillText(`TGT-ALPHA (${this.target.x >= 0 ? '+' : ''}${this.target.x.toFixed(1)}, ${this.target.y >= 0 ? '+' : ''}${this.target.y.toFixed(1)}m)`, tp.cx + 12, tp.cy - 6);
    }
  }

  renderGDOP(ctx) {
    const xs = this.gdopGrid.x_coords;
    const ys = this.gdopGrid.y_coords;
    const mat = this.gdopGrid.gdop_matrix;
    if (!xs || !ys || !mat) return;

    const cellW = Math.abs(xs[1] - xs[0]) * this.scale;
    const cellH = Math.abs(ys[1] - ys[0]) * this.scale;

    for (let i = 0; i < ys.length; i++) {
      for (let j = 0; j < xs.length; j++) {
        const val = mat[i][j];
        const cp = this.worldToCanvas(xs[j], ys[i]);
        let r = 0, g = 255, b = 157, alpha = 0.08;
        if (val > 4.0) {
          r = 255; g = 51; b = 102; alpha = 0.14;
        } else if (val > 2.2) {
          r = 255; g = 179; b = 0; alpha = 0.11;
        }
        ctx.fillStyle = `rgba(${r}, ${g}, ${b}, ${alpha})`;
        ctx.fillRect(cp.cx - cellW / 2, cp.cy - cellH / 2, cellW + 1, cellH + 1);
      }
    }
  }
}
