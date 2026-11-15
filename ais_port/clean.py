"""航迹清洗:去重、合理性过滤、跳点/漂移点剔除、MMSI 共用识别。"""
import json
from collections import defaultdict

from .geo import REGION_BBOX, haversine_m

MAX_SPEED_KN = 45.0        # 商船合理航速上限
GAP_RESET_S = 2 * 3600     # 航迹中断超过此时长则重新起锚点
KN_PER_M_S = 1.943844


def _speed_kn(p1, p2):
    dt = p2[1] - p1[1]
    if dt <= 0:
        return float("inf")
    d = haversine_m(p1[2], p1[3], p2[2], p2[3])
    return d / dt * KN_PER_M_S


def detect_shared_mmsi(conn):
    """多船共用 MMSI 识别:静态船名冲突 + 位置矛盾报文对持续出现。

    位置判据:120 秒时间窗内位移超过 5 公里的报文对达到 50 对以上。
    孤立错位点(噪声)一个月只产生十来对,而两船共用会持续交替出现。
    """
    cur = conn.cursor()
    flagged = 0
    ship_rows = conn.execute("SELECT mmsi, names FROM ships").fetchall()
    for row in ship_rows:
        mmsi = row["mmsi"]
        names = json.loads(row["names"] or "[]")
        reasons = []
        if len([n for n in names if n]) >= 2:
            reasons.append("同一MMSI上报多个船名: " + "/".join(names))
        far_pairs = 0
        prev = None
        for p in cur.execute(
                "SELECT ts, lat, lon FROM positions WHERE mmsi=? ORDER BY ts",
                (mmsi,)):
            if prev is not None:
                dt = p[0] - prev[0]
                if 0 < dt < 120 and haversine_m(prev[1], prev[2],
                                                p[1], p[2]) > 5000:
                    far_pairs += 1
            prev = p
        if far_pairs >= 50:
            reasons.append("120秒内位移超5公里的矛盾报文达%d对" % far_pairs)
        if reasons:
            cur.execute("UPDATE ships SET shared=1, note=? WHERE mmsi=?",
                        ("; ".join(reasons), mmsi))
            flagged += 1
    conn.commit()
    return flagged


def clean_tracks(conn):
    """清洗 positions -> positions_clean。返回统计。"""
    cur = conn.cursor()
    conn.execute("DELETE FROM positions_clean")
    stats = defaultdict(int)
    lat0, lon0, lat1, lon1 = REGION_BBOX
    mmsi_list = [r[0] for r in cur.execute(
        "SELECT DISTINCT mmsi FROM positions")]
    shared = {r[0] for r in cur.execute("SELECT mmsi FROM ships WHERE shared=1")}

    for mmsi in mmsi_list:
        if mmsi in shared:
            stats["skipped_shared_mmsi"] += 1
            continue
        pts = [tuple(r) for r in cur.execute(
            "SELECT mmsi, ts, lat, lon, sog, cog, heading, nav_status "
            "FROM positions WHERE mmsi=? ORDER BY ts", (mmsi,))]
        stats["raw_points"] += len(pts)
        # 第一遍:字段合理性与区域过滤
        kept = []
        for p in pts:
            lat, lon, sog = p[2], p[3], p[4]
            if not (lat0 <= lat <= lat1 and lon0 <= lon <= lon1):
                stats["drop_out_of_region"] += 1
                continue
            if sog is not None and sog > MAX_SPEED_KN:
                stats["drop_sog_field"] += 1
                continue
            if kept and p[1] == kept[-1][1]:
                stats["drop_duplicate_ts"] += 1
                continue
            kept.append(p)
        # 第二遍:跳点/漂移点剔除(锚点法,前看 3 个点判别连续坏点)
        out = []
        i = 0
        n = len(kept)
        while i < n:
            p = kept[i]
            if not out or p[1] - out[-1][1] > GAP_RESET_S:
                out.append(p)          # 首点或长时间中断,直接重起
                i += 1
                continue
            a = out[-1]
            if _speed_kn(a, p) <= MAX_SPEED_KN:
                out.append(p)
                i += 1
                continue
            # p 可疑:前看 3 个点,若某点能与锚点衔接,丢弃其间所有坏点
            reconciled = False
            for j in range(i + 1, min(i + 4, n)):
                if kept[j][1] - a[1] > GAP_RESET_S:
                    break
                if _speed_kn(a, kept[j]) <= MAX_SPEED_KN:
                    stats["drop_jump_point"] += j - i
                    i = j              # 衔接点下一轮会被接受
                    reconciled = True
                    break
            if not reconciled:
                out.append(p)          # 无法判别,保留以免卡死
                i += 1
        stats["clean_points"] += len(out)
        cur.executemany("INSERT INTO positions_clean VALUES(?,?,?,?,?,?,?,?)", out)
    conn.commit()
    return dict(stats)
