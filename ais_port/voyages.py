"""航次切分:按进出港事件把连续航迹切成航次,自动判断起止港。"""
from .geo import ZONES

SEA = "外海"
PORT_NAME = ZONES["port"]["name"]


def build_voyages(conn):
    cur = conn.cursor()
    conn.execute("DELETE FROM voyages")
    total = 0
    mmsi_list = [r[0] for r in cur.execute(
        "SELECT DISTINCT mmsi FROM positions_clean")]
    for mmsi in mmsi_list:
        evs = [tuple(r) for r in cur.execute(
            "SELECT etype, ts FROM events WHERE mmsi=? AND etype IN "
            "('enter_port','exit_port') ORDER BY ts", (mmsi,))]
        span = cur.execute(
            "SELECT MIN(ts), MAX(ts) FROM positions_clean WHERE mmsi=?",
            (mmsi,)).fetchone()
        if not span or span[0] is None:
            continue
        track_start, track_end = span
        voyages = []
        last_depart = None       # 上一次出港时刻
        last_loc = SEA
        for etype, ts in evs:
            if etype == "enter_port":
                if last_loc == SEA:
                    voyages.append((last_loc, PORT_NAME,
                                    last_depart or track_start, ts))
                last_loc = PORT_NAME
            else:
                if last_loc == PORT_NAME:
                    voyages.append((PORT_NAME, SEA, ts, None))  # 出港航段开口
                last_depart = ts
                last_loc = SEA
        # 收尾:船最后在港内则闭合在港,最后在海上则补出港航段
        if last_loc == SEA and last_depart is not None:
            pass  # 出港开口航段已记录
        for i, (frm, to, dep, arr) in enumerate(voyages, 1):
            cur.execute(
                "INSERT INTO voyages(mmsi, voyage_no, from_loc, to_loc, "
                "depart_ts, arrive_ts) VALUES(?,?,?,?,?,?)",
                (mmsi, i, frm, to, dep, arr if arr is not None else track_end))
            total += 1
    conn.commit()
    return total
