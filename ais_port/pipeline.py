"""报文接入:读日志文件 -> 校验 -> 重组 -> 解码 -> 入库,全程计数。"""
import json
import time
from collections import Counter

from . import nmea
from .db import connect


def _update_ship(cur, mmsi, ts, msg):
    row = cur.execute("SELECT names, first_seen, last_seen FROM ships WHERE mmsi=?",
                      (mmsi,)).fetchone()
    name = msg.get("name")
    if row is None:
        names = [name] if name else []
        cur.execute(
            "INSERT INTO ships(mmsi, names, first_seen, last_seen) VALUES(?,?,?,?)",
            (mmsi, json.dumps(names, ensure_ascii=False), ts, ts))
    else:
        names = json.loads(row["names"] or "[]")
        if name and name not in names:
            names.append(name)
        cur.execute(
            "UPDATE ships SET names=?, first_seen=MIN(first_seen,?), "
            "last_seen=MAX(last_seen,?) WHERE mmsi=?",
            (json.dumps(names, ensure_ascii=False), ts, ts, mmsi))
    # 静态字段补充
    cur.execute(
        """UPDATE ships SET
             callsign = COALESCE(?, callsign), imo = COALESCE(?, imo),
             ship_type = COALESCE(?, ship_type),
             dim_bow = COALESCE(?, dim_bow), dim_stern = COALESCE(?, dim_stern),
             dim_port = COALESCE(?, dim_port),
             dim_starboard = COALESCE(?, dim_starboard)
           WHERE mmsi=?""",
        (msg.get("callsign"), msg.get("imo"), msg.get("ship_type"),
         msg.get("dim_bow"), msg.get("dim_stern"),
         msg.get("dim_port"), msg.get("dim_starboard"), mmsi))


def ingest_file(path, db_path, source_name=None, commit_batch=5000):
    """接入一个 NMEA 日志文件,返回计数器 dict。"""
    conn = connect(db_path)
    cur = conn.cursor()
    reasm = nmea.Reassembler()
    c = Counter()
    pos_batch, st_batch = [], []

    def flush():
        if pos_batch:
            cur.executemany(
                "INSERT INTO positions VALUES(?,?,?,?,?,?,?,?)", pos_batch)
            pos_batch.clear()
        if st_batch:
            cur.executemany(
                "INSERT INTO static_reports VALUES(?,?,?,?,?,?,?,?,?,?,?)", st_batch)
            st_batch.clear()
        conn.commit()

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            c["total_lines"] += 1
            ts, sent = nmea.parse_log_line(line)
            if sent is None:
                c["garbage_lines"] += 1
                continue
            if not nmea.checksum_ok(sent):
                c["bad_checksum"] += 1
                continue
            body = sent[1:sent.rfind("*")]
            fields = body.split(",")
            if len(fields) < 7 or not fields[0].endswith("VDM"):
                c["non_vdm"] += 1
                continue
            c["valid_sentences"] += 1
            try:
                got = reasm.feed(fields)
            except (ValueError, IndexError):
                c["fragment_error"] += 1
                continue
            if got is None:
                c["fragments_buffered"] += 1
                continue
            payload, fill = got
            c["messages_assembled"] += 1
            try:
                bits = nmea.payload_to_bits(payload, fill)
            except ValueError:
                c["payload_error"] += 1
                continue
            msg = nmea.decode_message(bits)
            if msg is None:
                c["decode_error"] += 1
                continue
            c["type_%d" % msg["type"]] += 1
            ts = ts if ts is not None else time.time()
            if msg["type"] in (1, 2, 3, 18):
                lat, lon = msg.get("lat"), msg.get("lon")
                if lat is None or abs(lat) > 90 or abs(lon) > 180:
                    c["invalid_pos"] += 1
                    continue
                pos_batch.append((msg["mmsi"], ts, lat, lon, msg.get("sog"),
                                  msg.get("cog"), msg.get("heading"),
                                  msg.get("nav_status")))
                _update_ship(cur, msg["mmsi"], ts, msg)
            elif msg["type"] in (5, 24):
                st_batch.append((
                    msg["mmsi"], ts, msg.get("name"), msg.get("callsign"),
                    msg.get("imo"), msg.get("ship_type"), msg.get("dim_bow"),
                    msg.get("dim_stern"), msg.get("dim_port"),
                    msg.get("dim_starboard"), msg.get("destination")))
                _update_ship(cur, msg["mmsi"], ts, msg)
            if len(pos_batch) + len(st_batch) >= commit_batch:
                flush()
    c["fragments_dropped"] = reasm.expire_count()
    flush()
    cur.execute("INSERT INTO ingest_runs(ts, filename, counters) VALUES(?,?,?)",
                (time.time(), source_name or str(path),
                 json.dumps(dict(c), ensure_ascii=False)))
    conn.commit()
    conn.close()
    return dict(c)
