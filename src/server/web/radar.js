/**
 * radar.js - Canvas 2D Polar Radar Scope & SIGINT Intercept Renderer
 * High-performance animated radar sweep with phosphorescent trails,
 * range rings, azimuth ticks, multi-target TWS blips (TGT-ALPHA, REF-PLUTO, UNK-BOGEY-01),
 * and per-station DOA bearing fans.
 */

class PolarRadar {
  constructor(canvasId) {
    this.canvas = document.getElementById(canvasId);
    this.ctx = this.canvas.getContext('2d');
    this.width = this.canvas.width;
    this.height = this.canvas.height;
    this.cx = this.width / 2;
    this.cy = this.height / 2;
    this.maxRadius = Math.min(this.width, this.height) / 2 - 14;

    this.maxRangeMeters = 400.0;
    this.sweepAngle = 0; // radians
    this.sweepSpeed = 0.028; // radians per frame

    this.target = {
      active: true,
      distance_m: 157.0,
      azimuth_deg: 35.4,
      trailIntensity: 0.5
    };

    this.bogey = {
      active: true,
      distance_m: 245.0,
      azimuth_deg: 118.5,
      trailIntensity: 0.4,
      label: 'BOGEY-01'
    };

    this.refNode = {
      active: true,
      distance_m: 322.0,
      azimuth_deg: 330.3,
      trailIntensity: 0.4
    };

    this.stationBearings = {};
    this.initAnimation();
  }

  setTarget(distance_m, azimuth_deg, bearings = null, emitters = null) {
    this.target.distance_m = Math.min(this.maxRangeMeters * 0.98, Math.max(5.0, distance_m || 157.0));
    this.target.azimuth_deg = azimuth_deg || 0.0;
    this.target.active = true;

    if (bearings) {
      this.stationBearings = bearings;
    }

    if (Array.isArray(emitters)) {
      const unk = emitters.find(e => e.track_id && e.track_id.includes('UNK'));
      if (unk) {
        this.bogey.active = true;
        this.bogey.label = unk.track_id.replace('UNK-', '');
      }
    }
  }

  initAnimation() {
    const loop = () => {
      this.render();
      requestAnimationFrame(loop);
    };
    requestAnimationFrame(loop);
  }

  render() {
    const ctx = this.ctx;
    ctx.clearRect(0, 0, this.width, this.height);

    // 1. Obsidian Phosphor Radar Background
    const bgGrad = ctx.createRadialGradient(this.cx, this.cy, 4, this.cx, this.cy, this.maxRadius);
    bgGrad.addColorStop(0, 'rgba(0, 26, 20, 0.72)');
    bgGrad.addColorStop(0.75, 'rgba(4, 12, 16, 0.90)');
    bgGrad.addColorStop(1, 'rgba(3, 6, 10, 0.98)');
    ctx.fillStyle = bgGrad;
    ctx.beginPath();
    ctx.arc(this.cx, this.cy, this.maxRadius, 0, Math.PI * 2);
    ctx.fill();

    // 2. Concentric Range Rings (100m, 200m, 300m, 400m)
    const rings = [100, 200, 300, 400];
    ctx.strokeStyle = 'rgba(0, 255, 157, 0.18)';
    ctx.lineWidth = 1;
    ctx.setLineDash([2, 4]);

    rings.forEach((r_m) => {
      const r_px = (r_m / this.maxRangeMeters) * this.maxRadius;
      ctx.beginPath();
      ctx.arc(this.cx, this.cy, r_px, 0, Math.PI * 2);
      ctx.stroke();

      ctx.fillStyle = 'rgba(0, 255, 157, 0.42)';
      ctx.font = '8px "JetBrains Mono"';
      ctx.textAlign = 'left';
      ctx.fillText(`${r_m}m`, this.cx + 3, this.cy - r_px + 9);
    });
    ctx.setLineDash([]);

    // 3. Radial Azimuth Lines (every 30 degrees)
    for (let deg = 0; deg < 360; deg += 30) {
      const rad = (deg - 90) * (Math.PI / 180);
      const x2 = this.cx + Math.cos(rad) * this.maxRadius;
      const y2 = this.cy + Math.sin(rad) * this.maxRadius;

      ctx.strokeStyle = deg % 90 === 0 ? 'rgba(0, 229, 255, 0.32)' : 'rgba(0, 255, 157, 0.10)';
      ctx.lineWidth = deg % 90 === 0 ? 1.2 : 1;
      ctx.beginPath();
      ctx.moveTo(this.cx, this.cy);
      ctx.lineTo(x2, y2);
      ctx.stroke();

      const lblR = this.maxRadius + 11;
      const lx = this.cx + Math.cos(rad) * lblR;
      const ly = this.cy + Math.sin(rad) * lblR;
      ctx.fillStyle = deg % 90 === 0 ? '#00e5ff' : 'rgba(255, 255, 255, 0.45)';
      ctx.font = deg % 90 === 0 ? 'bold 9.5px "Outfit"' : '8px "JetBrains Mono"';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';

      let labelText = `${deg}°`;
      if (deg === 0) labelText = 'N';
      if (deg === 90) labelText = 'E';
      if (deg === 180) labelText = 'S';
      if (deg === 270) labelText = 'W';
      ctx.fillText(labelText, lx, ly);
    }

    // 4. Rotating Phosphor Sweep Line with Gradient Sector
    this.sweepAngle = (this.sweepAngle + this.sweepSpeed) % (Math.PI * 2);
    const sweepSectorAngle = Math.PI / 3.8;
    const sweepGrad = ctx.createRadialGradient(this.cx, this.cy, 0, this.cx, this.cy, this.maxRadius);
    sweepGrad.addColorStop(0, 'rgba(0, 255, 157, 0.32)');
    sweepGrad.addColorStop(1, 'rgba(0, 255, 157, 0.0)');

    ctx.save();
    ctx.beginPath();
    ctx.moveTo(this.cx, this.cy);
    ctx.arc(this.cx, this.cy, this.maxRadius, this.sweepAngle - sweepSectorAngle, this.sweepAngle);
    ctx.closePath();
    ctx.fillStyle = sweepGrad;
    ctx.fill();

    const leadX = this.cx + Math.cos(this.sweepAngle) * this.maxRadius;
    const leadY = this.cy + Math.sin(this.sweepAngle) * this.maxRadius;
    ctx.strokeStyle = '#00ff9d';
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(this.cx, this.cy);
    ctx.lineTo(leadX, leadY);
    ctx.stroke();
    ctx.restore();

    // 5. Reference Pluto Blip (-570 kHz)
    if (this.refNode.active) {
      const refRad = (this.refNode.azimuth_deg - 90) * (Math.PI / 180);
      const refDistPx = Math.min(this.maxRadius * 0.9, (this.refNode.distance_m / this.maxRangeMeters) * this.maxRadius);
      const rx = this.cx + Math.cos(refRad) * refDistPx;
      const ry = this.cy + Math.sin(refRad) * refDistPx;

      ctx.fillStyle = '#2979ff';
      ctx.beginPath();
      ctx.arc(rx, ry, 3.5, 0, Math.PI * 2);
      ctx.fill();

      ctx.fillStyle = 'rgba(41, 121, 255, 0.85)';
      ctx.font = '7.5px "JetBrains Mono"';
      ctx.textAlign = 'left';
      ctx.fillText('REF', rx + 6, ry + 2);
    }

    // 6. Unknown Bogey Intercept Blip (CCA-CFAR)
    if (this.bogey.active) {
      const bRad = (this.bogey.azimuth_deg - 90) * (Math.PI / 180);
      const bDistPx = (this.bogey.distance_m / this.maxRangeMeters) * this.maxRadius;
      const bx = this.cx + Math.cos(bRad) * bDistPx;
      const by = this.cy + Math.sin(bRad) * bDistPx;

      let bDiff = (this.sweepAngle - bRad) % (Math.PI * 2);
      if (bDiff < 0) bDiff += Math.PI * 2;
      if (bDiff < 0.12) this.bogey.trailIntensity = 1.0;
      else this.bogey.trailIntensity = Math.max(0.2, this.bogey.trailIntensity - 0.008);

      ctx.strokeStyle = `rgba(255, 179, 0, ${0.35 + this.bogey.trailIntensity * 0.5})`;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(bx, by - 5);
      ctx.lineTo(bx + 5, by + 4);
      ctx.lineTo(bx - 5, by + 4);
      ctx.closePath();
      ctx.stroke();

      ctx.fillStyle = '#ffb300';
      ctx.font = '7.5px "JetBrains Mono"';
      ctx.textAlign = 'left';
      ctx.fillText(this.bogey.label, bx + 7, by + 2);
    }

    // 7. Primary Target Blip (TGT-ALPHA) & Bearing Vector
    if (this.target.active) {
      const tgtRad = (this.target.azimuth_deg - 90) * (Math.PI / 180);
      const tgtDistPx = (this.target.distance_m / this.maxRangeMeters) * this.maxRadius;
      const tgtX = this.cx + Math.cos(tgtRad) * tgtDistPx;
      const tgtY = this.cy + Math.sin(tgtRad) * tgtDistPx;

      let diff = (this.sweepAngle - tgtRad) % (Math.PI * 2);
      if (diff < 0) diff += Math.PI * 2;
      if (diff < 0.12) {
        this.target.trailIntensity = 1.0;
      } else {
        this.target.trailIntensity = Math.max(0.2, this.target.trailIntensity - 0.008);
      }

      // Bearing Cursor Vector from Centroid to Target
      ctx.strokeStyle = `rgba(0, 229, 255, ${0.45 + this.target.trailIntensity * 0.45})`;
      ctx.lineWidth = 1.4;
      ctx.setLineDash([4, 3]);
      ctx.beginPath();
      ctx.moveTo(this.cx, this.cy);
      ctx.lineTo(tgtX, tgtY);
      ctx.stroke();
      ctx.setLineDash([]);

      // Glowing Target Blip
      ctx.shadowColor = '#ff3366';
      ctx.shadowBlur = 12 * this.target.trailIntensity;
      ctx.fillStyle = `rgba(255, 51, 102, ${0.45 + this.target.trailIntensity * 0.55})`;
      ctx.beginPath();
      ctx.arc(tgtX, tgtY, 4.5 + this.target.trailIntensity * 2.5, 0, Math.PI * 2);
      ctx.fill();
      ctx.shadowBlur = 0;

      // Target Crosshair Mark
      ctx.strokeStyle = '#ffffff';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(tgtX - 6, tgtY);
      ctx.lineTo(tgtX + 6, tgtY);
      ctx.moveTo(tgtX, tgtY - 6);
      ctx.lineTo(tgtX, tgtY + 6);
      ctx.stroke();

      // Blip Text Annotation
      ctx.fillStyle = '#ffb300';
      ctx.font = 'bold 8.5px "JetBrains Mono"';
      ctx.textAlign = 'left';
      ctx.fillText(`TGT [${this.target.azimuth_deg.toFixed(1)}°]`, tgtX + 8, tgtY - 6);
    }

    // 8. Outer Metallic Bezel Ring
    ctx.strokeStyle = 'rgba(0, 229, 255, 0.42)';
    ctx.lineWidth = 1.8;
    ctx.beginPath();
    ctx.arc(this.cx, this.cy, this.maxRadius, 0, Math.PI * 2);
    ctx.stroke();
  }
}
