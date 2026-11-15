"""报文生成器:模拟云澜港一个月的船舶活动,输出 NMEA 日志与预设真值。

噪声注入:校验和错误、分片丢失、位置跳点(船自报错位)、重复报文、
垃圾行、多船共用一个 MMSI。
"""
import csv
import json
import math
import random
from datetime import datetime, timedelta, timezone

from .geo import ZONES, haversine_m, first_crossing_t, point_in_poly
from .nmea import BitWriter, add_checksum, bits_to_payload

TZ8 = timezone(timedelta(hours=8))
BASE_TS = datetime(2026, 10, 1, 0, 0, tzinfo=TZ8).timestamp()
DAYS = 30
DECEL_M = 200.0
KN2MS = 0.514444
MS2KN = 1.943844

PORT_POLY = ZONES["port"]["poly"]
ANCHOR_POLY = ZONES["anchor"]["poly"]
BERTH_CENTERS = {  # 各泊位中心点
    "berth1": (29.99175, 121.99450),
    "berth2": (29.99175, 122.00150),
    "berth3": (29.99175, 122.00850),
}

SHIP_TYPES = [  # (AIS类型码, 中文名, 船长范围, 船宽范围)
    (70, "散货船", (120, 200), (18, 32)),
    (71, "集装箱船", (140, 260), (22, 40)),
    (72, "杂货船", (80, 140), (13, 22)),
    (80, "油轮", (150, 250), (24, 44)),
    (81, "化学品船", (100, 180), (16, 28)),
    (60, "客船", (60, 120), (12, 20)),
]
NAME_PREFIX = {70: "YUANHANG", 71: "JIZHOU", 72: "HAIFENG",
               80: "YOUCHEN", 81: "HUAXIN", 60: "KELUN", 30: "YUCHUAN"}


def bearing_deg(lat1, lon1, lat2, lon2):
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(math.radians(lat2))
    x = (math.cos(math.radians(lat1)) * math.sin(math.radians(lat2))
         - math.sin(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.cos(dl))
    return (math.degrees(math.atan2(y, x)) + 360) % 360


class Leg:
    """匀速直线航段,可选末端减速/起始加速(200 m 匀变速区)。"""

    def __init__(self, p0, p1, v_kn, decel_end=False, accel_start=False):
        self.p0, self.p1 = p0, p1
        self.v = v_kn * KN2MS
        self.L = haversine_m(p0[0], p0[1], p1[0], p1[1])
        self.cog = bearing_deg(p0[0], p0[1], p1[0], p1[1])
        self.s_acc = DECEL_M if accel_start else 0.0
        self.s_dec = DECEL_M if decel_end else 0.0
        self.a = self.v ** 2 / (2 * DECEL_M)
        self.t_acc = 2 * self.s_acc / self.v
        self.t_cru = (self.L - self.s_acc - self.s_dec) / self.v
        self.t_dec = 2 * self.s_dec / self.v
        self.dur = self.t_acc + self.t_cru + self.t_dec

    def at(self, tau):
        """返回 (lat, lon, speed_kn)。"""
        if tau < self.t_acc:
            s = 0.5 * self.a * tau ** 2
            spd = self.a * tau
        elif tau < self.t_acc + self.t_cru:
            s = self.s_acc + self.v * (tau - self.t_acc)
            spd = self.v
        else:
            u = min(tau - self.t_acc - self.t_cru, self.t_dec)
            s = self.L - self.s_dec + self.v * u - 0.5 * self.a * u ** 2
            spd = max(0.0, self.v - self.a * u)
        f = min(1.0, s / self.L)
        lat = self.p0[0] + (self.p1[0] - self.p0[0]) * f
        lon = self.p0[1] + (self.p1[1] - self.p0[1]) * f
        return lat, lon, spd * MS2KN

    def time_at_distance(self, s):
        """沿航段航行 s 米所需时间(用于真值穿越时刻)。"""
        if s <= self.s_acc:
            return math.sqrt(2 * s / self.a)
        if s <= self.L - self.s_dec:
            return self.t_acc + (s - self.s_acc) / self.v
        u = s - (self.L - self.s_dec)
        # s = v*u - 0.5*a*u^2 求 u(取小根)
        disc = self.v ** 2 - 2 * self.a * u
        return self.t_acc + self.t_cru + (self.v - math.sqrt(max(0.0, disc))) / self.a


# ---------------- AIS 报文编码 ----------------

def encode_pos(mmsi, nav, sog, lat, lon, cog, sec, mtype=1):
    w = BitWriter()
    w.uint(mtype, 6)
    w.uint(0, 2)
    w.uint(mmsi, 30)
    if mtype == 18:
        w.uint(0, 8)
    else:
        w.uint(nav, 4)
        w.sint(0, 8)
    w.uint(round(sog * 10), 10)
    w.uint(0, 1)
    w.sint(round(lon * 600000), 28)
    w.sint(round(lat * 600000), 27)
    w.uint(round(cog * 10) % 3600, 12)
    w.uint(int(cog) % 360, 9)
    w.uint(sec, 6)
    if mtype == 18:
        w.uint(0, 2)
        for _ in range(7):
            w.uint(0, 1)
        w.uint(0, 20)
    else:
        w.uint(0, 2)
        w.uint(0, 3)
        w.uint(0, 1)
        w.uint(0, 19)
    return w.payload()


def encode_type5(ship):
    w = BitWriter()
    w.uint(5, 6)
    w.uint(0, 2)
    w.uint(ship["mmsi"], 30)
    w.uint(0, 2)
    w.uint(ship["imo"], 30)
    w.text(ship["callsign"], 42)
    w.text(ship["name"], 120)
    w.uint(ship["ship_type"], 8)
    w.uint(ship["dim_bow"], 9)
    w.uint(ship["dim_stern"], 9)
    w.uint(ship["dim_port"], 6)
    w.uint(ship["dim_stbd"], 6)
    w.uint(1, 4)
    w.uint(10, 4)
    w.uint(15, 5)
    w.uint(8, 5)
    w.uint(30, 6)
    w.uint(round(ship["draught"] * 10), 8)
    w.text("YUNLAN", 120)
    w.uint(0, 1)
    w.uint(0, 1)
    return w.payload()


def encode_type24(ship, part):
    w = BitWriter()
    w.uint(24, 6)
    w.uint(0, 2)
    w.uint(ship["mmsi"], 30)
    w.uint(part, 2)
    if part == 0:
        w.text(ship["name"], 120)
    else:
        w.uint(ship["ship_type"], 8)
        w.uint(0, 18)
        w.uint(0, 4)
        w.uint(0, 20)
        w.text(ship["callsign"], 42)
        w.uint(ship["dim_bow"], 9)
        w.uint(ship["dim_stern"], 9)
        w.uint(ship["dim_port"], 6)
        w.uint(ship["dim_stbd"], 6)
        w.uint(0, 6)
    return w.payload()


def nmea_sentence(payload, fill, total=1, num=1, seq="", channel="A"):
    if total == 1:
        body = "AIVDM,1,1,,%s,%s,%d" % (channel, payload, fill)
    else:
        body = "AIVDM,%d,%d,%s,%s,%s,%d" % (total, num, seq, channel, payload, fill)
    return add_checksum(body)


# ---------------- 生成器主体 ----------------

class Generator:
    def __init__(self, seed=20261001):
        self.rng = random.Random(seed)
        self.lines = []       # (ts, sentence)
        self.truth = []       # dict 行
        self.ships = self._make_fleet()

    def _make_fleet(self):
        ships = []
        mmsi_seq = 412000100
        for i in range(35):
            stype, _, lrange, brange = SHIP_TYPES[i % len(SHIP_TYPES)]
            length = self.rng.uniform(*lrange)
            beam = self.rng.uniform(*brange)
            bow = round(length * self.rng.uniform(0.75, 0.9))
            ships.append({
                "id": "SHIP%02d" % (i + 1),
                "mmsi": mmsi_seq + i,
                "name": "%s %02d" % (NAME_PREFIX[stype], i + 1),
                "callsign": "B%c%c%c%c" % tuple(self.rng.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ") for _ in range(4)),
                "imo": 9000000 + i * 17,
                "ship_type": stype,
                "dim_bow": bow, "dim_stern": round(length - bow),
                "dim_port": round(beam / 2), "dim_stbd": round(beam) - round(beam / 2),
                "draught": round(self.rng.uniform(4, 12), 1),
                "cls": "A",
            })
        # 渔船(B 类)
        for i in range(5):
            length = self.rng.uniform(20, 35)
            ships.append({
                "id": "FISH%02d" % (i + 1),
                "mmsi": 412000500 + i,
                "name": "%s %02d" % (NAME_PREFIX[30], i + 1),
                "callsign": "B%c%c%c%c" % tuple(self.rng.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ") for _ in range(4)),
                "imo": 0, "ship_type": 30,
                "dim_bow": round(length * 0.8), "dim_stern": round(length * 0.2),
                "dim_port": 3, "dim_stbd": 3, "draught": 2.0, "cls": "B",
            })
        # 多船共用一个 MMSI:7 号与 23 号船都发 412000777
        ships[6]["mmsi"] = 412000777
        ships[22]["mmsi"] = 412000777
        return ships

    # ---- 噪声开关 ----
    def _maybe_corrupt(self, sentence):
        if self.rng.random() < 0.01:
            i = self.rng.randrange(7, len(sentence) - 3)
            c = sentence[i]
            sentence = sentence[:i] + ("A" if c != "A" else "B") + sentence[i + 1:]
        return sentence

    def _emit(self, ts, sentence):
        self.lines.append((ts, self._maybe_corrupt(sentence)))

    def _emit_pos(self, ship, ts, lat, lon, sog, cog, nav):
        # 0.3% 概率船自报错位(单点跳到 ±0.3 度外)
        if self.rng.random() < 0.003:
            lat += self.rng.uniform(-0.3, 0.3)
            lon += self.rng.uniform(-0.3, 0.3)
        lat += self.rng.gauss(0, 0.00003)
        lon += self.rng.gauss(0, 0.00003)
        sec = int(ts) % 60
        mtype = 18 if ship["cls"] == "B" else 1
        payload, fill = encode_pos(ship["mmsi"], nav, max(0.0, sog), lat, lon,
                                   cog % 360, sec, mtype)
        self._emit(ts, nmea_sentence(payload, fill))
        if self.rng.random() < 0.002:  # 0.2% 重复报文
            self._emit(ts, nmea_sentence(payload, fill))

    def _emit_static(self, ship, ts):
        if ship["cls"] == "A":
            payload, fill = encode_type5(ship)
            seq = str(self.rng.randrange(10))
            cut = 60
            self._emit(ts, nmea_sentence(payload[:cut], 0, total=2, num=1, seq=seq))
            if self.rng.random() >= 0.02:  # 2% 概率第二片丢失
                self._emit(ts + 1, nmea_sentence(payload[cut:], fill,
                                                 total=2, num=2, seq=seq))
        else:
            p, f = encode_type24(ship, 0)
            self._emit(ts, nmea_sentence(p, f))
            p, f = encode_type24(ship, 1)
            self._emit(ts + 1, nmea_sentence(p, f))

    def _run_leg(self, ship, leg, t0, nav=0):
        tau = 0.0
        while tau < leg.dur - 1e-6:
            lat, lon, spd = leg.at(tau)
            self._emit_pos(ship, t0 + tau, lat, lon, spd, leg.cog, nav)
            tau += 10.0 if spd >= 2.0 else 15.0
        return t0 + leg.dur

    def _run_stop(self, ship, t0, t1, lat, lon, nav, jitter_m):
        t = t0
        while t < t1:
            la = lat + self.rng.gauss(0, jitter_m / 111320.0)
            lo = lon + self.rng.gauss(0, jitter_m / (111320.0 * 0.866))
            self._emit_pos(ship, t, la, lo, abs(self.rng.gauss(0.1, 0.08)),
                           self.rng.uniform(0, 360), nav)
            t += 60.0

    def _truth(self, ship, etype, zone, ts):
        self.truth.append({"ship_id": ship["id"], "mmsi": ship["mmsi"],
                           "name": ship["name"], "etype": etype,
                           "zone": zone, "ts": round(ts, 1)})

    def _port_call(self, ship, t_start):
        rng = self.rng
        berth = rng.choice(list(BERTH_CENTERS))
        do_anchor = rng.random() < 0.65
        # 航路点
        spawn = (29.938 + rng.uniform(0, 0.008), 121.955 + rng.uniform(0, 0.09))
        approach = (29.970 + rng.uniform(-0.002, 0.002), 122.000 + rng.uniform(-0.003, 0.003))
        anchor_pt = (30.012 + rng.uniform(-0.002, 0.002), 122.000 + rng.uniform(-0.002, 0.002))
        berth_pt = (BERTH_CENTERS[berth][0] + rng.uniform(-0.0002, 0.0002),
                    BERTH_CENTERS[berth][1] + rng.uniform(-0.0004, 0.0004))
        t = t_start
        # 段1: 外海 -> 引航点
        t = self._run_leg(ship, Leg(spawn, approach, 11.0), t)
        # 段2: 引航点 -> 锚地或泊位(记录进港穿越)
        dest = anchor_pt if do_anchor else berth_pt
        leg2 = Leg(approach, dest, 6.0, decel_end=True)
        self._truth_crossing(ship, leg2, t, "enter_port")
        t = self._run_leg(ship, leg2, t)
        if do_anchor:
            anchor_end = t + rng.uniform(1, 8) * 3600
            self._truth(ship, "anchor_start", "anchor", t)
            self._run_stop(ship, t, anchor_end, *anchor_pt, nav=1, jitter_m=18)
            self._truth(ship, "anchor_end", "anchor", anchor_end)
            t = anchor_end
            leg3 = Leg(anchor_pt, berth_pt, 5.0, accel_start=True, decel_end=True)
            t = self._run_leg(ship, leg3, t)
        # 靠泊
        berth_end = t + rng.uniform(6, 20) * 3600
        self._truth(ship, "berth_start", berth, t)
        self._run_stop(ship, t, berth_end, *berth_pt, nav=5, jitter_m=4)
        self._truth(ship, "berth_end", berth, berth_end)
        t = berth_end
        # 段5: 泊位 -> 引航点(记录出港穿越)
        approach2 = (29.970 + rng.uniform(-0.002, 0.002), 122.000 + rng.uniform(-0.003, 0.003))
        leg5 = Leg(berth_pt, approach2, 6.0, accel_start=True)
        self._truth_crossing(ship, leg5, t, "exit_port")
        t = self._run_leg(ship, leg5, t)
        # 段6: 引航点 -> 外海
        despawn = (29.938 + rng.uniform(0, 0.008), 121.955 + rng.uniform(0, 0.09))
        t = self._run_leg(ship, Leg(approach2, despawn, 11.0), t)
        return t

    def _truth_crossing(self, ship, leg, t0, etype):
        """计算航段穿越港区边界的精确时刻,记入真值。"""
        in0 = point_in_poly(leg.p0[0], leg.p0[1], PORT_POLY)
        in1 = point_in_poly(leg.p1[0], leg.p1[1], PORT_POLY)
        if in0 == in1:
            return
        f = first_crossing_t(leg.p0, leg.p1, PORT_POLY)
        if f is None:
            return
        ts = t0 + leg.time_at_distance(f * leg.L)
        self._truth(ship, etype, "port", ts)

    def run(self):
        rng = self.rng
        day_s = 86400.0
        self._overlap_starts = None
        for idx, ship in enumerate(self.ships):
            if ship["cls"] == "B":
                # 渔船:港区外横穿,不进港
                t = BASE_TS + rng.uniform(0, 2) * day_s
                for _ in range(8):
                    p0 = (29.948 + rng.uniform(0, 0.008), 121.90 + rng.uniform(0, 0.02))
                    p1 = (29.952 + rng.uniform(0, 0.008), 122.08 + rng.uniform(0, 0.02))
                    leg = Leg(p0, p1, 8.0)
                    self._run_leg(ship, leg, t)
                    t += rng.uniform(3, 5) * day_s
                    if t > BASE_TS + DAYS * day_s:
                        break
                continue
            # 货/客船:每月 2-3 次挂靠
            n_calls = rng.choice([2, 2, 3])
            t = BASE_TS + rng.uniform(0, 3) * day_s
            call_starts = []
            for _ in range(n_calls):
                if t > BASE_TS + (DAYS - 2) * day_s:
                    break
                call_starts.append(t)
                t += rng.uniform(6, 12) * day_s
            if idx == 6:
                self._overlap_starts = call_starts
            # 22 号船与 6 号船共用 MMSI,船期强制重叠以产生位置矛盾
            if idx == 22 and self._overlap_starts:
                call_starts = [s + rng.uniform(0, 1800)
                               for s in self._overlap_starts]
            for cs in call_starts:
                t_end = self._port_call(ship, cs)
                # 静态报文:整个挂靠期间每 6 分钟
                ts = cs
                while ts < t_end:
                    self._emit_static(ship, ts)
                    ts += 360.0
        # 垃圾行
        for _ in range(50):
            ts = BASE_TS + rng.uniform(0, DAYS * day_s)
            self.lines.append((ts, "".join(rng.choice("abcdef0123456789!@#$")
                                           for _ in range(rng.randrange(5, 40)))))
        self.lines.sort(key=lambda x: x[0])
        return self

    def write(self, log_path, truth_path):
        with open(log_path, "w", encoding="utf-8") as f:
            for ts, sent in self.lines:
                f.write("%.0f %s\n" % (ts, sent))
        with open(truth_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["ship_id", "mmsi", "name",
                                              "etype", "zone", "ts"])
            w.writeheader()
            for row in sorted(self.truth, key=lambda r: r["ts"]):
                w.writerow(row)
        return {"sentences": len(self.lines), "truth_events": len(self.truth),
                "ships": len(self.ships)}


def generate(log_path, truth_path, seed=20261001):
    return Generator(seed).run().write(log_path, truth_path)
