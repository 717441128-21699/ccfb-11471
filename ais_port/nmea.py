"""NMEA 0183 / AIS 解码:校验和、分片重组、六比特解码、报文字段解析。

日志行格式: `<unix_ts> <sentence>`(岸基接收机打的时间戳)。
也支持裸语句(无时间戳),用于网页端单条解码。
"""


# ---------- 校验和 ----------

def checksum_ok(sentence):
    s = sentence.strip()
    if not (s.startswith("!") or s.startswith("$")):
        return False
    star = s.rfind("*")
    if star < 0 or star + 3 > len(s):
        return False
    cs = 0
    for ch in s[1:star]:
        cs ^= ord(ch)
    try:
        return cs == int(s[star + 1:star + 3], 16)
    except ValueError:
        return False


def add_checksum(body):
    cs = 0
    for ch in body:
        cs ^= ord(ch)
    return "!%s*%02X" % (body, cs)


# ---------- 六比特编解码 ----------

def _armor_val(c):
    v = ord(c) - 48
    if v > 40:
        v -= 8
    return v


def _armor_char(v):
    return chr(v + 48) if v < 40 else chr(v + 56)


def payload_to_bits(payload, fill=0):
    bits = []
    for c in payload:
        v = _armor_val(c)
        if not 0 <= v <= 63:
            raise ValueError("非法载荷字符 %r" % c)
        for i in range(5, -1, -1):
            bits.append((v >> i) & 1)
    if fill:
        bits = bits[:-fill]
    return bits


def bits_to_payload(bits):
    """比特列表 -> 载荷字符串,返回 (payload, fill_bits)。"""
    fill = (-len(bits)) % 6
    bits = list(bits) + [0] * fill
    out = []
    for i in range(0, len(bits), 6):
        v = 0
        for b in bits[i:i + 6]:
            v = (v << 1) | b
        out.append(_armor_char(v))
    return "".join(out), fill


def bits_to_uint(bits, start, length):
    v = 0
    for i in range(start, start + length):
        v = (v << 1) | bits[i]
    return v


def bits_to_int(bits, start, length):
    v = bits_to_uint(bits, start, length)
    if bits[start]:
        v -= 1 << length
    return v


def bits_to_text(bits, start, length):
    """AIS 六比特文本:'@'=0..'_'=31, 空格..?=32..63。"""
    chars = []
    for i in range(start, start + length, 6):
        v = bits_to_uint(bits, i, 6)
        chars.append(chr(v + 64) if v < 32 else chr(v))
    return "".join(chars).rstrip("@").strip()


class BitWriter:
    """编码器(报文生成器用),与上面的解码器互逆。"""

    def __init__(self):
        self.bits = []

    def uint(self, v, n):
        v = int(v) & ((1 << n) - 1)
        for i in range(n - 1, -1, -1):
            self.bits.append((v >> i) & 1)

    def sint(self, v, n):
        if v < 0:
            v += 1 << n
        self.uint(v, n)

    def text(self, s, n):
        s = (s or "").upper()
        vals = []
        for c in s:
            o = ord(c)
            if 64 <= o <= 95:
                vals.append(o - 64)
            elif 32 <= o <= 63:
                vals.append(o)
            else:
                vals.append(0)
        for i in range(n // 6):
            self.uint(vals[i] if i < len(vals) else 0, 6)

    def payload(self):
        return bits_to_payload(self.bits)


# ---------- AIS 报文解析 ----------

def decode_message(bits):
    """比特列表 -> dict;不支持的类型返回 {'type','mmsi'} 骨架,过短返回 None。"""
    if len(bits) < 38:
        return None
    mtype = bits_to_uint(bits, 0, 6)
    mmsi = bits_to_uint(bits, 8, 30)
    d = {"type": mtype, "mmsi": mmsi}
    try:
        if mtype in (1, 2, 3):
            d.update(
                nav_status=bits_to_uint(bits, 38, 4),
                rot=bits_to_int(bits, 42, 8),
                sog=bits_to_uint(bits, 50, 10) / 10.0,
                lon=bits_to_int(bits, 61, 28) / 600000.0,
                lat=bits_to_int(bits, 89, 27) / 600000.0,
                cog=bits_to_uint(bits, 116, 12) / 10.0,
                heading=bits_to_uint(bits, 128, 9),
                second=bits_to_uint(bits, 137, 6),
            )
        elif mtype == 5:
            d.update(
                imo=bits_to_uint(bits, 40, 30),
                callsign=bits_to_text(bits, 70, 42),
                name=bits_to_text(bits, 112, 120),
                ship_type=bits_to_uint(bits, 232, 8),
                dim_bow=bits_to_uint(bits, 240, 9),
                dim_stern=bits_to_uint(bits, 249, 9),
                dim_port=bits_to_uint(bits, 258, 6),
                dim_starboard=bits_to_uint(bits, 264, 6),
                draught=bits_to_uint(bits, 294, 8) / 10.0,
                destination=bits_to_text(bits, 302, 120),
            )
        elif mtype == 18:
            d.update(
                sog=bits_to_uint(bits, 46, 10) / 10.0,
                lon=bits_to_int(bits, 57, 28) / 600000.0,
                lat=bits_to_int(bits, 85, 27) / 600000.0,
                cog=bits_to_uint(bits, 112, 12) / 10.0,
                heading=bits_to_uint(bits, 124, 9),
                second=bits_to_uint(bits, 133, 6),
            )
        elif mtype == 24:
            part = bits_to_uint(bits, 38, 2)
            d["part"] = part
            if part == 0:
                d["name"] = bits_to_text(bits, 40, 120)
            else:
                d.update(
                    ship_type=bits_to_uint(bits, 40, 8),
                    callsign=bits_to_text(bits, 90, 42),
                    dim_bow=bits_to_uint(bits, 132, 9),
                    dim_stern=bits_to_uint(bits, 141, 9),
                    dim_port=bits_to_uint(bits, 150, 6),
                    dim_starboard=bits_to_uint(bits, 156, 6),
                )
    except IndexError:
        return None
    return d


# ---------- 分片重组 ----------

class Reassembler:
    """按 (信道, 序号) 重组多片 AIVDM 报文。"""

    def __init__(self):
        self.pending = {}  # key -> {"total":n, "parts":{num:payload}, "fill":last_fill}

    def feed(self, fields):
        """fields 为语句体逗号拆分结果。返回完整 payload(bits 已可解)或 None。"""
        total = int(fields[1])
        num = int(fields[2])
        seq = fields[3] or "0"
        channel = fields[4]
        payload = fields[5]
        fill = int(fields[6]) if fields[6] else 0
        if total == 1:
            return payload, fill
        key = (channel, seq)
        slot = self.pending.setdefault(key, {"total": total, "parts": {}, "fill": 0})
        slot["parts"][num] = payload
        if num == total:
            slot["fill"] = fill
        if len(slot["parts"]) == total:
            joined = "".join(slot["parts"][i] for i in range(1, total + 1))
            f = slot["fill"]
            del self.pending[key]
            return joined, f
        return None

    def expire_count(self):
        n = len(self.pending)
        self.pending.clear()
        return n


def parse_log_line(line):
    """返回 (ts 或 None, sentence 或 None)。无法解析返回 (None, None)。"""
    line = line.strip()
    if not line:
        return None, None
    ts, sent = None, line
    if " " in line:
        head, _, rest = line.partition(" ")
        try:
            ts = float(head)
            sent = rest.strip()
        except ValueError:
            sent = line
    if sent.startswith("!") or sent.startswith("$"):
        return ts, sent
    return ts, None
