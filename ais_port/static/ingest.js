// 报文接入页:触发接入、单条解码
const btnIngest = document.getElementById('btnIngest');
if (btnIngest) {
  btnIngest.onclick = async () => {
    const fname = document.getElementById('fname').value;
    const out = document.getElementById('ingestResult');
    document.getElementById('ingestBusy').textContent = '接入中,请稍候…';
    out.textContent = '';
    try {
      const r = await fetch('/api/ingest', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filename: fname }),
      });
      const d = await r.json();
      out.textContent = JSON.stringify(d, null, 2);
    } catch (e) { out.textContent = '接入失败: ' + e; }
    document.getElementById('ingestBusy').textContent = '';
  };
}
document.getElementById('btnDecode').onclick = async () => {
  const text = document.getElementById('nmeaIn').value;
  const out = document.getElementById('decodeResult');
  const r = await fetch('/api/decode?sentence=' + encodeURIComponent(text));
  const d = await r.json();
  out.textContent = d.map(item => {
    let s = item.raw + '\n';
    if (item.error) s += '  ✗ ' + item.error;
    else if (item.status) s += '  … ' + item.status;
    else s += '  ✓ ' + JSON.stringify(item.decoded, null, 2);
    return s;
  }).join('\n\n');
};
