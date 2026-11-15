"""港口事件识别:电子围栏进出(插值到分)、靠泊/离泊、锚泊/起锚。"""
import json

from .geo import ZONES, STOP_ZONE_KEYS, first_crossing_t, point_in_poly, zone_of

STOP_SOG_KN = 0.5          # 低于此航速视为停船
MIN_STOP_MIN = 10          # 停船持续不足此时长不计事件
PORT_POLY = ZONES["port"]["poly"]


def _ship_names(conn):
    out = {}
    for r in conn.execute("SELECT mmsi, names FROM ships"):
        names = json.loads(r["names"] or "[]")
        out[r["mmsi"]] = names[0] if names else ""
    return out


def detect_events(conn):
    """基于清洗后航迹识别事件,写入 events 表。返回事件数。"""
    cur = conn.cursor()
    conn.execute("DELETE FROM events")
    names = _ship_names(conn)
    count = 0

    mmsi_list = [r[0] for r in cur.execute(
        "SELECT DISTINCT mmsi FROM positions_clean")]
    for mmsi in mmsi_list:
        pts = [tuple(r) for r in cur.execute(
            "SELECT ts, lat, lon, sog FROM positions_clean "
            "WHERE mmsi=? ORDER BY ts", (mmsi,))]
        if not pts:
            continue
        name = names.get(mmsi, "")
        ev = []

        # ---- 1) 港区进出:相邻点跨越围栏,线性插值穿越时刻 ----
        prev_inside = point_in_poly(pts[0][1], pts[0][2], PORT_POLY)
        for i in range(1, len(pts)):
            t0, la0, lo0 = pts[i - 1][0], pts[i - 1][1], pts[i - 1][2]
            t1, la1, lo1 = pts[i][0], pts[i][1], pts[i][2]
            inside = point_in_poly(la1, lo1, PORT_POLY)
            if inside != prev_inside:
                t = first_crossing_t((la0, lo0), (la1, lo1), PORT_POLY)
                ts = t0 + (t if t is not None else 0.5) * (t1 - t0)
                ev.append(("enter_port" if inside else "exit_port", "port", ts, None))
            prev_inside = inside

        # ---- 2) 停船段 -> 靠泊/锚泊 ----
        segs = []          # (start_idx, end_idx) 闭区间,均为停船点
        start = None
        for i, p in enumerate(pts):
            if (p[3] or 0) < STOP_SOG_KN:
                if start is None:
                    start = i
            else:
                if start is not None:
                    segs.append((start, i - 1))
                    start = None
        if start is not None:
            segs.append((start, len(pts) - 1))

        for s, e in segs:
            dur_s = pts[e][0] - pts[s][0]
            if dur_s < MIN_STOP_MIN * 60:
                continue
            mid = pts[(s + e) // 2]
            zk = zone_of(mid[1], mid[2], STOP_ZONE_KEYS)
            if zk is None:
                continue
            # 起止时刻:与相邻移动点取中点(误差小于半个报文间隔)
            if s > 0:
                ts_start = (pts[s - 1][0] + pts[s][0]) / 2
            else:
                ts_start = pts[s][0]
            if e < len(pts) - 1:
                ts_end = (pts[e][0] + pts[e + 1][0]) / 2
            else:
                ts_end = pts[e][0]
            kind = "anchor" if zk == "anchor" else "berth"
            dur_min = (ts_end - ts_start) / 60.0
            ev.append((kind + "_start", zk, ts_start, dur_min))
            ev.append((kind + "_end", zk, ts_end, None))

        ev.sort(key=lambda x: x[2])
        cur.executemany(
            "INSERT INTO events(mmsi, ship_name, etype, zone, ts, dur_min) "
            "VALUES(?,?,?,?,?,?)",
            [(mmsi, name, et, zk, ts, dur) for et, zk, ts, dur in ev])
        count += len(ev)
    conn.commit()
    return count
