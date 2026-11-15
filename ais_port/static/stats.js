// 统计报表图表:每日进出港艘次 + 分船型平均锚泊时长
function barChart(canvasId, labels, series) {
  const cv = document.getElementById(canvasId);
  if (!cv) return;
  const ctx = cv.getContext('2d');
  const W = cv.width, H = cv.height, padL = 40, padB = 46, padT = 14;
  const maxV = Math.max(1, ...series.flatMap(s => s.values));
  const plotW = W - padL - 10, plotH = H - padT - padB;
  ctx.clearRect(0, 0, W, H);
  ctx.strokeStyle = '#35507a'; ctx.fillStyle = '#9fb4d0'; ctx.font = '11px sans-serif';
  ctx.beginPath(); ctx.moveTo(padL, padT); ctx.lineTo(padL, H - padB); ctx.lineTo(W, H - padB); ctx.stroke();
  const n = labels.length;
  const groupW = plotW / n;
  const barW = Math.min(18, groupW / (series.length + 1));
  labels.forEach((lb, i) => {
    series.forEach((s, k) => {
      const v = s.values[i] || 0;
      const h = v / maxV * plotH;
      const x = padL + i * groupW + k * barW + 2;
      ctx.fillStyle = s.color;
      ctx.fillRect(x, H - padB - h, barW - 2, h);
    });
    if (n <= 31) {
      ctx.fillStyle = '#9fb4d0'; ctx.textAlign = 'center';
      ctx.fillText(lb, padL + i * groupW + groupW / 2, H - padB + 14);
    }
  });
  // 图例
  series.forEach((s, k) => {
    ctx.fillStyle = s.color;
    ctx.fillRect(padL + 10 + k * 130, 2, 10, 10);
    ctx.fillStyle = '#cde'; ctx.textAlign = 'left';
    ctx.fillText(s.name, padL + 24 + k * 130, 11);
  });
}

fetch('/api/stats').then(r => r.json()).then(d => {
  barChart('dailyChart', d.daily.map(x => x.date.slice(5)), [
    { name: '进港艘次', color: '#4caf7d', values: d.daily.map(x => x.enter) },
    { name: '出港艘次', color: '#e8a04c', values: d.daily.map(x => x.exit) },
  ]);
  barChart('typeChart', d.by_type.map(x => x.ship_type), [
    { name: '平均锚泊时长(h)', color: '#5ec1ff', values: d.by_type.map(x => x.anchor_avg_h || 0) },
    { name: '平均靠泊时长(h)', color: '#c98ae0', values: d.by_type.map(x => x.berth_avg_h || 0) },
  ]);
});
