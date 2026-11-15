"""统计报表:每日进出港艘次、分船型平均锚泊/靠泊时长。"""
from collections import defaultdict

from .db import day_str, ship_type_name


def daily_counts(conn):
    """[{date, enter, exit, berth, anchor}] 按日期升序。"""
    days = defaultdict(lambda: {"enter": 0, "exit": 0, "berth": 0, "anchor": 0})
    for etype, ts in conn.execute(
            "SELECT etype, ts FROM events WHERE etype IN "
            "('enter_port','exit_port','berth_start','anchor_start')"):
        d = days[day_str(ts)]
        if etype == "enter_port":
            d["enter"] += 1
        elif etype == "exit_port":
            d["exit"] += 1
        elif etype == "berth_start":
            d["berth"] += 1
        else:
            d["anchor"] += 1
    return [{"date": k, **v} for k, v in sorted(days.items())]


def stop_duration_by_type(conn):
    """分船型平均锚泊/靠泊时长(小时)。"""
    agg = defaultdict(lambda: {"anchor_sum": 0.0, "anchor_n": 0,
                               "berth_sum": 0.0, "berth_n": 0})
    rows = conn.execute(
        """SELECT e.etype, e.dur_min, s.ship_type
           FROM events e JOIN ships s ON s.mmsi = e.mmsi
           WHERE e.etype IN ('anchor_start','berth_start')
             AND e.dur_min IS NOT NULL""").fetchall()
    for etype, dur, stype in rows:
        a = agg[ship_type_name(stype)]
        if etype == "anchor_start":
            a["anchor_sum"] += dur
            a["anchor_n"] += 1
        else:
            a["berth_sum"] += dur
            a["berth_n"] += 1
    out = []
    for tname, a in sorted(agg.items()):
        out.append({
            "ship_type": tname,
            "anchor_n": a["anchor_n"],
            "anchor_avg_h": round(a["anchor_sum"] / a["anchor_n"] / 60, 2)
            if a["anchor_n"] else None,
            "berth_n": a["berth_n"],
            "berth_avg_h": round(a["berth_sum"] / a["berth_n"] / 60, 2)
            if a["berth_n"] else None,
        })
    return out
