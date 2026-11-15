"""电子围栏与地理计算:多边形围栏、点内判定、线段穿越、球面距离。"""
import math

# ---- 云澜港 电子围栏定义 (lat, lon) ----
ZONES = {
    "port": {
        "name": "云澜港港区",
        "poly": [(29.990, 121.990), (29.990, 122.020),
                 (30.020, 122.020), (30.020, 121.990)],
    },
    "anchor": {
        "name": "云澜锚地",
        "poly": [(30.008, 121.994), (30.008, 122.006),
                 (30.016, 122.006), (30.016, 121.994)],
    },
    "berth1": {
        "name": "1号泊位",
        "poly": [(29.9910, 121.9920), (29.9910, 121.9970),
                 (29.9925, 121.9970), (29.9925, 121.9920)],
    },
    "berth2": {
        "name": "2号泊位",
        "poly": [(29.9910, 121.9990), (29.9910, 122.0040),
                 (29.9925, 122.0040), (29.9925, 121.9990)],
    },
    "berth3": {
        "name": "3号泊位",
        "poly": [(29.9910, 122.0060), (29.9910, 122.0110),
                 (29.9925, 122.0110), (29.9925, 122.0060)],
    },
}
BERTH_KEYS = ["berth1", "berth2", "berth3"]
STOP_ZONE_KEYS = ["anchor"] + BERTH_KEYS

# 区域合理性范围(清洗用,港口周边约 ±0.5 度)
REGION_BBOX = (29.4, 121.4, 30.6, 122.6)  # lat_min, lon_min, lat_max, lon_max


def haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(min(1.0, math.sqrt(a)))


def point_in_poly(lat, lon, poly):
    """射线法,poly 为 [(lat, lon), ...]"""
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        yi, xi = poly[i]
        yj, xj = poly[j]
        if (xi > lon) != (xj > lon):
            y_cross = (yj - yi) * (lon - xi) / (xj - xi) + yi
            if lat < y_cross:
                inside = not inside
        j = i
    return False if inside is False else True


def _seg_t(a, b, c, d):
    """线段 ab 与 cd 相交时返回 ab 上的参数 t∈[0,1],否则 None。"""
    ax, ay = a
    bx, by = b
    cx, cy = c
    dx, dy = d
    rx, ry = bx - ax, by - ay
    sx, sy = dx - cx, dy - cy
    den = rx * sy - ry * sx
    if abs(den) < 1e-18:
        return None
    t = ((cx - ax) * sy - (cy - ay) * sx) / den
    u = ((cx - ax) * ry - (cy - ay) * rx) / den
    if -1e-12 <= t <= 1 + 1e-12 and -1e-12 <= u <= 1 + 1e-12:
        return max(0.0, min(1.0, t))
    return None


def first_crossing_t(p1, p2, poly):
    """p1->p2 线段首次穿越多边形边界的参数 t(用于插值穿越时刻)。"""
    best = None
    n = len(poly)
    for i in range(n):
        t = _seg_t(p1, p2, poly[i], poly[(i + 1) % n])
        if t is not None and (best is None or t < best):
            best = t
    return best


def zone_of(lat, lon, keys=None):
    """返回点所在的围栏 key,不在任何围栏内返回 None。"""
    for key in (keys or ZONES.keys()):
        if point_in_poly(lat, lon, ZONES[key]["poly"]):
            return key
    return None
