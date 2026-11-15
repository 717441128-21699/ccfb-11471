"""网页端:报文接入 / 船舶档案 / 航迹回放 / 港口事件 / 统计报表。

运行: py -m ais_port.webapp   (先运行 run_all.py 生成数据)
"""
import json
import os
from datetime import datetime, timedelta, timezone

from flask import Flask, jsonify, render_template, request
from jinja2 import DictLoader

from . import clean as clean_mod
from . import events as events_mod
from . import nmea, pipeline
from . import voyages as voyages_mod
from .db import connect, event_name, fmt_ts, init_db, ship_type_name
from .geo import ZONES
from .stats import daily_counts, stop_duration_by_type

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.environ.get("AIS_DB", os.path.join(DATA_DIR, "ais.db"))
TZ8 = timezone(timedelta(hours=8))

app = Flask(__name__)

TEMPLATES = {
    "base.html": """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{% block title %}云澜港 AIS 监视系统{% endblock %}</title>
<link rel="stylesheet" href="{{ url_for('static', filename='style.css') }}">
</head>
<body>
<nav class="top">
  <span class="brand">云澜港 AIS 监视系统</span>
  <a href="/">总览</a>
  <a href="/ingest">报文接入</a>
  <a href="/ships">船舶档案</a>
  <a href="/replay">航迹回放</a>
  <a href="/events">港口事件</a>
  <a href="/stats">统计报表</a>
</nav>
<main>
{% block content %}{% endblock %}
</main>
</body>
</html>""",

    "index.html": """{% extends "base.html" %}
{% block content %}
<h1>系统总览</h1>
<div class="cards">
  <div class="card"><div class="num">{{ c.ships }}</div><div>档案船舶</div></div>
  <div class="card"><div class="num">{{ c.pos }}</div><div>原始位置报文</div></div>
  <div class="card"><div class="num">{{ c.clean }}</div><div>清洗后航迹点</div></div>
  <div class="card"><div class="num">{{ c.events }}</div><div>港口事件</div></div>
  <div class="card"><div class="num">{{ c.voyages }}</div><div>航次</div></div>
  <div class="card warn"><div class="num">{{ c.shared }}</div><div>共用 MMSI 嫌疑</div></div>
</div>
<h2>处理链路</h2>
<ol class="flow">
  <li>NMEA 校验和验证、多片报文重组、六比特解码(坏包丢弃并计数)</li>
  <li>静态报告(5/24 型)与动态位置(1/2/3/18 型)分库存储</li>
  <li>多船共用 MMSI 识别(船名冲突 + 位置不可能跳变)</li>
  <li>航迹清洗:跳点、漂移点、速度异常点剔除</li>
  <li>电子围栏事件:进港 / 出港 / 靠泊 / 锚泊,时刻精确到分</li>
  <li>连续航迹切分航次,自动判断起止港</li>
</ol>
{% endblock %}""",

    "ingest.html": """{% extends "base.html" %}
{% block content %}
<h1>报文接入</h1>
<section class="panel">
  <h2>接入 NMEA 日志文件</h2>
  {% if files %}
  <select id="fname">{% for f in files %}<option value="{{ f }}">{{ f }}</option>{% endfor %}</select>
  <button id="btnIngest">开始接入</button>
  <span id="ingestBusy"></span>
  {% else %}<p>data/ 目录下没有 .log 文件,请先运行报文生成器。</p>{% endif %}
  <pre id="ingestResult"></pre>
</section>
<section class="panel">
  <h2>单条报文解码</h2>
  <textarea id="nmeaIn" rows="4" placeholder="粘贴 NMEA 报文,可多行,如 !AIVDM,1,1,,A,13HOI:0P00PD;88MD5MTDwwT000,0*5C"></textarea>
  <button id="btnDecode">解码</button>
  <pre id="decodeResult"></pre>
</section>
<section class="panel">
  <h2>接入记录</h2>
  <table>
    <tr><th>时间</th><th>文件</th><th>计数</th></tr>
    {% for r in runs %}
    <tr><td>{{ r.ts_fmt }}</td><td>{{ r.filename }}</td><td class="mono">{{ r.counters }}</td></tr>
    {% endfor %}
  </table>
</section>
<script src="{{ url_for('static', filename='ingest.js') }}"></script>
{% endblock %}""",

    "ships.html": """{% extends "base.html" %}
{% block content %}
<h1>船舶档案</h1>
<table>
  <tr><th>MMSI</th><th>船名</th><th>呼号</th><th>IMO</th><th>船型</th>
      <th>尺度(长×宽 m)</th><th>首次出现</th><th>末次出现</th><th>状态</th></tr>
  {% for s in ships %}
  <tr class="{{ 'bad' if s.shared else '' }}">
    <td class="mono">{{ s.mmsi }}</td>
    <td>{{ s.names }}</td>
    <td>{{ s.callsign or '-' }}</td>
    <td>{{ s.imo or '-' }}</td>
    <td>{{ s.type_name }}</td>
    <td>{{ s.dims }}</td>
    <td>{{ s.first_seen }}</td>
    <td>{{ s.last_seen }}</td>
    <td>{{ s.status }}</td>
  </tr>
  {% endfor %}
</table>
{% endblock %}""",

    "replay.html": """{% extends "base.html" %}
{% block content %}
<h1>航迹回放</h1>
<div class="panel controls">
  日期 <input type="date" id="day" value="2026-10-05">
  <button id="btnLoad">加载</button>
  <button id="btnPlay">▶ 播放</button>
  倍速
  <select id="speed">
    <option value="1">1x</option><option value="10">10x</option>
    <option value="60" selected>60x</option><option value="300">300x</option>
    <option value="900">900x</option>
  </select>
  <span id="clock" class="mono"></span>
  <span id="shipcount"></span>
</div>
<input type="range" id="scrub" min="0" max="1000" value="0">
<canvas id="map" width="1120" height="620"></canvas>
<script src="{{ url_for('static', filename='replay.js') }}"></script>
{% endblock %}""",

    "events.html": """{% extends "base.html" %}
{% block content %}
<h1>港口事件</h1>
<form class="panel controls" method="get">
  类型
  <select name="etype">
    <option value="">全部</option>
    {% for k, v in etypes.items() %}
    <option value="{{ k }}" {{ 'selected' if k == q.etype }}>{{ v }}</option>
    {% endfor %}
  </select>
  MMSI <input name="mmsi" value="{{ q.mmsi }}" size="10">
  日期 <input type="date" name="date" value="{{ q.date }}">
  <button type="submit">查询</button>
</form>
<table>
  <tr><th>时间</th><th>船名</th><th>MMSI</th><th>事件</th><th>区域</th><th>时长</th></tr>
  {% for e in events %}
  <tr>
    <td class="mono">{{ e.ts }}</td><td>{{ e.ship_name }}</td>
    <td class="mono">{{ e.mmsi }}</td><td>{{ e.etype }}</td>
    <td>{{ e.zone }}</td><td>{{ e.dur }}</td>
  </tr>
  {% endfor %}
</table>

<h2>航次列表</h2>
<table>
  <tr><th>MMSI</th><th>航次</th><th>起运港</th><th>目的港</th><th>离港时间</th><th>到港时间</th></tr>
  {% for v in voyages %}
  <tr>
    <td class="mono">{{ v.mmsi }}</td><td>{{ v.voyage_no }}</td>
    <td>{{ v.from_loc }}</td><td>{{ v.to_loc }}</td>
    <td class="mono">{{ v.depart }}</td><td class="mono">{{ v.arrive }}</td>
  </tr>
  {% endfor %}
</table>
{% endblock %}""",

    "stats.html": """{% extends "base.html" %}
{% block content %}
<h1>统计报表</h1>
<section class="panel">
  <h2>每日进出港艘次</h2>
  <canvas id="dailyChart" width="1120" height="260"></canvas>
  <table>
    <tr><th>日期</th><th>进港艘次</th><th>出港艘次</th><th>靠泊艘次</th><th>锚泊艘次</th></tr>
    {% for d in daily %}
    <tr><td class="mono">{{ d.date }}</td><td>{{ d.enter }}</td><td>{{ d.exit }}</td>
        <td>{{ d.berth }}</td><td>{{ d.anchor }}</td></tr>
    {% endfor %}
  </table>
</section>
<section class="panel">
  <h2>分船型平均锚泊 / 靠泊时长</h2>
  <canvas id="typeChart" width="1120" height="240"></canvas>
  <table>
    <tr><th>船型</th><th>锚泊次数</th><th>平均锚泊时长(h)</th>
        <th>靠泊次数</th><th>平均靠泊时长(h)</th></tr>
    {% for t in by_type %}
    <tr><td>{{ t.ship_type }}</td><td>{{ t.anchor_n }}</td>
        <td>{{ t.anchor_avg_h or '-' }}</td>
        <td>{{ t.berth_n }}</td><td>{{ t.berth_avg_h or '-' }}</td></tr>
    {% endfor %}
  </table>
</section>
<script src="{{ url_for('static', filename='stats.js') }}"></script>
{% endblock %}""",
}

app.jinja_loader = DictLoader(TEMPLATES)


def get_conn():
    if not os.path.exists(DB_PATH):
        os.makedirs(DATA_DIR, exist_ok=True)
        init_db(DB_PATH).close()
    return connect(DB_PATH)


# ---------------- 页面 ----------------

@app.route("/")
def index():
    conn = get_conn()
    c = {
        "ships": conn.execute("SELECT COUNT(*) FROM ships").fetchone()[0],
        "pos": conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0],
        "clean": conn.execute("SELECT COUNT(*) FROM positions_clean").fetchone()[0],
        "events": conn.execute("SELECT COUNT(*) FROM events").fetchone()[0],
        "voyages": conn.execute("SELECT COUNT(*) FROM voyages").fetchone()[0],
        "shared": conn.execute("SELECT COUNT(*) FROM ships WHERE shared=1").fetchone()[0],
    }
    conn.close()
    return render_template("index.html", c=c)


@app.route("/ingest")
def ingest_page():
    conn = get_conn()
    runs = [{
        "ts_fmt": fmt_ts(r["ts"], True),
        "filename": r["filename"],
        "counters": r["counters"],
    } for r in conn.execute(
        "SELECT * FROM ingest_runs ORDER BY id DESC LIMIT 20")]
    conn.close()
    files = []
    if os.path.isdir(DATA_DIR):
        files = sorted(f for f in os.listdir(DATA_DIR) if f.endswith(".log"))
    return render_template("ingest.html", files=files, runs=runs)


@app.route("/ships")
def ships_page():
    conn = get_conn()
    ships = []
    for r in conn.execute("SELECT * FROM ships ORDER BY mmsi"):
        names = json.loads(r["names"] or "[]")
        length = (r["dim_bow"] or 0) + (r["dim_stern"] or 0)
        beam = (r["dim_port"] or 0) + (r["dim_starboard"] or 0)
        ships.append({
            "mmsi": r["mmsi"],
            "names": " / ".join(names) if names else "(未上报)",
            "callsign": r["callsign"], "imo": r["imo"],
            "type_name": ship_type_name(r["ship_type"]),
            "dims": ("%d×%d" % (length, beam)) if length else "-",
            "first_seen": fmt_ts(r["first_seen"]),
            "last_seen": fmt_ts(r["last_seen"]),
            "shared": r["shared"],
            "status": ("⚠ 疑似多船共用: " + (r["note"] or "")) if r["shared"] else "正常",
        })
    conn.close()
    return render_template("ships.html", ships=ships)


@app.route("/replay")
def replay_page():
    return render_template("replay.html")


@app.route("/events")
def events_page():
    q = {"etype": request.args.get("etype", ""),
         "mmsi": request.args.get("mmsi", "").strip(),
         "date": request.args.get("date", "")}
    sql = "SELECT * FROM events WHERE 1=1"
    args = []
    if q["etype"]:
        sql += " AND etype=?"
        args.append(q["etype"])
    if q["mmsi"].isdigit():
        sql += " AND mmsi=?"
        args.append(int(q["mmsi"]))
    if q["date"]:
        try:
            d0 = datetime.strptime(q["date"], "%Y-%m-%d").replace(tzinfo=TZ8).timestamp()
            sql += " AND ts>=? AND ts<?"
            args += [d0, d0 + 86400]
        except ValueError:
            pass
    sql += " ORDER BY ts DESC LIMIT 500"
    conn = get_conn()
    events = [{
        "ts": fmt_ts(r["ts"]),
        "ship_name": r["ship_name"] or "(未知)",
        "mmsi": r["mmsi"],
        "etype": event_name(r["etype"]),
        "zone": ZONES.get(r["zone"], {}).get("name", r["zone"]),
        "dur": ("%.0f 分钟" % r["dur_min"]) if r["dur_min"] else "-",
    } for r in conn.execute(sql, args)]
    voyages = [{
        "mmsi": r["mmsi"], "voyage_no": r["voyage_no"],
        "from_loc": r["from_loc"], "to_loc": r["to_loc"],
        "depart": fmt_ts(r["depart_ts"]), "arrive": fmt_ts(r["arrive_ts"]),
    } for r in conn.execute(
        "SELECT * FROM voyages ORDER BY depart_ts DESC LIMIT 200")]
    conn.close()
    from .db import EVENT_NAMES
    return render_template("events.html", events=events, voyages=voyages,
                           etypes=EVENT_NAMES, q=q)


@app.route("/stats")
def stats_page():
    conn = get_conn()
    daily = daily_counts(conn)
    by_type = stop_duration_by_type(conn)
    conn.close()
    return render_template("stats.html", daily=daily, by_type=by_type)


# ---------------- API ----------------

@app.route("/api/ingest", methods=["POST"])
def api_ingest():
    fn = (request.get_json(force=True) or {}).get("filename", "")
    if not fn or ".." in fn or "/" in fn or "\\" in fn:
        return jsonify({"error": "非法文件名"}), 400
    path = os.path.join(DATA_DIR, fn)
    if not os.path.exists(path):
        return jsonify({"error": "文件不存在"}), 404
    counters = pipeline.ingest_file(path, DB_PATH, source_name=fn)
    conn = get_conn()
    flagged = clean_mod.detect_shared_mmsi(conn)
    cstats = clean_mod.clean_tracks(conn)
    n_ev = events_mod.detect_events(conn)
    n_vo = voyages_mod.build_voyages(conn)
    conn.close()
    return jsonify({"counters": counters, "shared_mmsi_flagged": flagged,
                    "clean": cstats, "events": n_ev, "voyages": n_vo})


@app.route("/api/decode")
def api_decode():
    text = request.args.get("sentence", "")
    out = []
    reasm = nmea.Reassembler()
    for line in text.strip().splitlines():
        item = {"raw": line.strip()}
        ts, sent = nmea.parse_log_line(line)
        if sent is None:
            item["error"] = "无法解析为 NMEA 语句"
        elif not nmea.checksum_ok(sent):
            item["error"] = "校验和错误"
        else:
            body = sent[1:sent.rfind("*")]
            fields = body.split(",")
            try:
                got = reasm.feed(fields)
            except (ValueError, IndexError) as e:
                item["error"] = "分片错误: %s" % e
            else:
                if got is None:
                    item["status"] = "分片已缓存,等待后续分片"
                else:
                    payload, fill = got
                    msg = nmea.decode_message(nmea.payload_to_bits(payload, fill))
                    item["decoded"] = msg
        out.append(item)
    return jsonify(out)


@app.route("/api/track")
def api_track():
    date = request.args.get("date", "")
    try:
        d0 = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=TZ8).timestamp()
    except ValueError:
        return jsonify({"error": "日期格式应为 YYYY-MM-DD"}), 400
    d1 = d0 + 86400
    conn = get_conn()
    names = {}
    types = {}
    for r in conn.execute("SELECT mmsi, names, ship_type FROM ships"):
        ns = json.loads(r["names"] or "[]")
        names[r["mmsi"]] = ns[0] if ns else str(r["mmsi"])
        types[r["mmsi"]] = ship_type_name(r["ship_type"])
    rows = conn.execute(
        "SELECT mmsi, ts, lat, lon, sog FROM positions_clean "
        "WHERE ts>=? AND ts<? ORDER BY mmsi, ts", (d0, d1)).fetchall()
    conn.close()
    ships = {}
    for mmsi, ts, lat, lon, sog in rows:
        ships.setdefault(mmsi, []).append(
            [round(ts, 1), round(lat, 5), round(lon, 5), round(sog or 0, 1)])
    return jsonify({
        "t0": d0, "t1": d1,
        "geofences": {k: {"name": v["name"], "poly": v["poly"]}
                      for k, v in ZONES.items()},
        "ships": [{"mmsi": m, "name": names.get(m, str(m)),
                   "type": types.get(m, ""), "pts": pts}
                  for m, pts in ships.items()],
    })


@app.route("/api/stats")
def api_stats():
    conn = get_conn()
    data = {"daily": daily_counts(conn), "by_type": stop_duration_by_type(conn)}
    conn.close()
    return jsonify(data)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
