// 航迹回放:多船同屏、倍速播放、进度拖拽
const cv = document.getElementById('map');
const ctx = cv.getContext('2d');
const BOUNDS = { lat0: 29.928, lat1: 30.032, lon0: 121.928, lon1: 122.092 };
let S = null;            // {t0,t1,geofences,ships}
let simT = 0, playing = false, speed = 60, lastFrame = 0;
const TRAIL_S = 1800;    // 尾迹长度(模拟秒)

function proj(lat, lon) {
  return [(lon - BOUNDS.lon0) / (BOUNDS.lon1 - BOUNDS.lon0) * cv.width,
          (BOUNDS.lat1 - lat) / (BOUNDS.lat1 - BOUNDS.lat0) * cv.height];
}
function fmtClock(ts) {
  return new Date(ts * 1000 + 8 * 3600 * 1000).toISOString()
    .replace('T', ' ').slice(0, 19) + ' (UTC+8)';
}
function shipColor(i) { return `hsl(${(i * 47) % 360}, 75%, 62%)`; }

function bisect(pts, t) {
  let lo = 0, hi = pts.length;
  while (lo < hi) { const mid = (lo + hi) >> 1; (pts[mid][0] <= t) ? lo = mid + 1 : hi = mid; }
  return lo - 1;
}

function draw() {
  ctx.clearRect(0, 0, cv.width, cv.height);
  if (!S) return;
  // 电子围栏
  for (const key of Object.keys(S.geofences)) {
    const z = S.geofences[key];
    ctx.beginPath();
    z.poly.forEach((p, i) => {
      const [x, y] = proj(p[0], p[1]);
      i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    });
    ctx.closePath();
    ctx.strokeStyle = key === 'port' ? '#3d6ea8' : (key === 'anchor' ? '#3fa06a' : '#b08a3d');
    ctx.lineWidth = key === 'port' ? 2 : 1.2;
    ctx.stroke();
    const cx = z.poly.reduce((s, p) => s + p[0], 0) / z.poly.length;
    const cy = z.poly.reduce((s, p) => s + p[1], 0) / z.poly.length;
    const [lx, ly] = proj(cx, cy);
    ctx.fillStyle = '#7f93ad'; ctx.font = '12px sans-serif'; ctx.textAlign = 'center';
    ctx.fillText(z.name, lx, ly);
  }
  // 船舶
  let visible = 0;
  S.ships.forEach((sh, i) => {
    const idx = bisect(sh.pts, simT);
    if (idx < 0) return;
    visible++;
    const col = shipColor(i);
    // 尾迹
    ctx.beginPath();
    let started = false;
    for (let j = idx; j >= 0 && sh.pts[j][0] >= simT - TRAIL_S; j--) {
      const [x, y] = proj(sh.pts[j][1], sh.pts[j][2]);
      started ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
      started = true;
    }
    ctx.strokeStyle = col; ctx.globalAlpha = 0.45; ctx.lineWidth = 1.2; ctx.stroke();
    ctx.globalAlpha = 1;
    // 当前位置
    const p = sh.pts[idx];
    const [x, y] = proj(p[1], p[2]);
    ctx.beginPath(); ctx.arc(x, y, 4, 0, 7); ctx.fillStyle = col; ctx.fill();
    ctx.fillStyle = '#dce8f8'; ctx.font = '11px sans-serif'; ctx.textAlign = 'left';
    ctx.fillText(`${sh.name} ${p[3].toFixed(1)}kn`, x + 7, y - 5);
  });
  document.getElementById('clock').textContent = fmtClock(simT);
  document.getElementById('shipcount').textContent = `在屏船舶 ${visible} 艘`;
  document.getElementById('scrub').value = S ? (1000 * (simT - S.t0) / (S.t1 - S.t0)) : 0;
}

function loop(now) {
  if (playing && S) {
    simT += (now - lastFrame) / 1000 * speed;
    if (simT >= S.t1) { simT = S.t1; playing = false;
      document.getElementById('btnPlay').textContent = '▶ 播放'; }
    draw();
  }
  lastFrame = now;
  requestAnimationFrame(loop);
}

async function loadDay() {
  const date = document.getElementById('day').value;
  const r = await fetch('/api/track?date=' + date);
  S = await r.json();
  if (S.error) { alert(S.error); return; }
  simT = S.t0; playing = false;
  document.getElementById('btnPlay').textContent = '▶ 播放';
  draw();
}

document.getElementById('btnLoad').onclick = loadDay;
document.getElementById('btnPlay').onclick = () => {
  if (!S) return;
  if (simT >= S.t1) simT = S.t0;
  playing = !playing;
  document.getElementById('btnPlay').textContent = playing ? '⏸ 暂停' : '▶ 播放';
};
document.getElementById('speed').onchange = e => { speed = +e.target.value; };
document.getElementById('scrub').oninput = e => {
  if (!S) return;
  simT = S.t0 + (S.t1 - S.t0) * e.target.value / 1000;
  draw();
};
loadDay();
requestAnimationFrame(loop);
