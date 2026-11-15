"""SQLite 存储:库表结构与通用辅助。"""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

TZ8 = timezone(timedelta(hours=8))

SCHEMA = """
CREATE TABLE IF NOT EXISTS positions (
    mmsi INTEGER, ts REAL, lat REAL, lon REAL,
    sog REAL, cog REAL, heading INTEGER, nav_status INTEGER
);
CREATE INDEX IF NOT EXISTS idx_pos_mmsi_ts ON positions(mmsi, ts);
CREATE INDEX IF NOT EXISTS idx_pos_ts ON positions(ts);

CREATE TABLE IF NOT EXISTS positions_clean (
    mmsi INTEGER, ts REAL, lat REAL, lon REAL,
    sog REAL, cog REAL, heading INTEGER, nav_status INTEGER
);
CREATE INDEX IF NOT EXISTS idx_pclean_mmsi_ts ON positions_clean(mmsi, ts);
CREATE INDEX IF NOT EXISTS idx_pclean_ts ON positions_clean(ts);

CREATE TABLE IF NOT EXISTS static_reports (
    mmsi INTEGER, ts REAL, name TEXT, callsign TEXT, imo INTEGER,
    ship_type INTEGER, dim_bow INTEGER, dim_stern INTEGER,
    dim_port INTEGER, dim_starboard INTEGER, destination TEXT
);
CREATE INDEX IF NOT EXISTS idx_static_mmsi ON static_reports(mmsi);

CREATE TABLE IF NOT EXISTS ships (
    mmsi INTEGER PRIMARY KEY,
    names TEXT,            -- JSON 数组,出现过的所有船名
    callsign TEXT, imo INTEGER, ship_type INTEGER,
    dim_bow INTEGER, dim_stern INTEGER, dim_port INTEGER, dim_starboard INTEGER,
    first_seen REAL, last_seen REAL,
    shared INTEGER DEFAULT 0,   -- 1 = 多船共用 MMSI 嫌疑
    note TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mmsi INTEGER, ship_name TEXT, etype TEXT, zone TEXT,
    ts REAL, dur_min REAL
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_mmsi ON events(mmsi);

CREATE TABLE IF NOT EXISTS voyages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mmsi INTEGER, voyage_no INTEGER,
    from_loc TEXT, to_loc TEXT, depart_ts REAL, arrive_ts REAL
);

CREATE TABLE IF NOT EXISTS ingest_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL, filename TEXT, counters TEXT
);

CREATE TABLE IF NOT EXISTS ground_truth (
    ship_id TEXT, mmsi INTEGER, name TEXT, etype TEXT, zone TEXT, ts REAL
);
"""

EVENT_NAMES = {
    "enter_port": "进港",
    "exit_port": "出港",
    "berth_start": "靠泊",
    "berth_end": "离泊",
    "anchor_start": "锚泊",
    "anchor_end": "起锚",
}

SHIP_TYPE_NAMES = {
    30: "渔船", 52: "拖轮", 60: "客船", 70: "散货船", 71: "集装箱船",
    72: "杂货船", 80: "油轮", 81: "化学品船",
}

NAV_STATUS = {
    0: "在航", 1: "锚泊", 2: "失控", 3: "操纵受限", 4: "吃水受限",
    5: "系泊", 6: "搁浅", 7: "捕捞作业", 8: "帆船航行",
}


def connect(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(path):
    conn = connect(path)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def reset_business_tables(conn):
    """重跑链路时清空派生数据。"""
    for t in ("positions", "positions_clean", "static_reports", "ships",
              "events", "voyages", "ingest_runs", "ground_truth"):
        conn.execute("DELETE FROM %s" % t)
    conn.commit()


def fmt_ts(ts, with_sec=False):
    if ts is None:
        return "-"
    dt = datetime.fromtimestamp(ts, tz=TZ8)
    return dt.strftime("%Y-%m-%d %H:%M:%S" if with_sec else "%Y-%m-%d %H:%M")


def day_str(ts):
    return datetime.fromtimestamp(ts, tz=TZ8).strftime("%Y-%m-%d")


def ship_type_name(code):
    if code is None:
        return "未知"
    return SHIP_TYPE_NAMES.get(code, "类型%d" % code)


def event_name(etype):
    return EVENT_NAMES.get(etype, etype)


def dumps(obj):
    return json.dumps(obj, ensure_ascii=False)
