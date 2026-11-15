"""验证:识别出的事件与预设进出港记录逐条比对,误差不超过两分钟。"""
import csv
from collections import defaultdict


def load_truth(path):
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append({"mmsi": int(r["mmsi"]), "etype": r["etype"],
                         "zone": r["zone"], "ts": float(r["ts"]),
                         "name": r["name"]})
    return rows


def validate(conn, truth_path, tol_s=120.0):
    truth = load_truth(truth_path)
    shared = {r[0] for r in conn.execute("SELECT mmsi FROM ships WHERE shared=1")}
    detected = defaultdict(list)
    for mmsi, etype, ts in conn.execute(
            "SELECT mmsi, etype, ts FROM events ORDER BY ts"):
        detected[(mmsi, etype)].append(ts)

    # 真值中属于共用 MMSI 的记录不参与比对(该 MMSI 已被标记待人工核查)
    skipped = [t for t in truth if t["mmsi"] in shared]
    truth = [t for t in truth if t["mmsi"] not in shared]

    used = defaultdict(set)   # (mmsi,etype) -> 已匹配的 detected 下标
    per_type = defaultdict(lambda: {"n": 0, "matched": 0, "errs": [], "miss": 0})
    misses = []
    for t in sorted(truth, key=lambda r: r["ts"]):
        key = (t["mmsi"], t["etype"])
        st = per_type[t["etype"]]
        st["n"] += 1
        best_i, best_err = None, None
        for i, ts in enumerate(detected.get(key, [])):
            if i in used[key]:
                continue
            err = abs(ts - t["ts"])
            if best_err is None or err < best_err:
                best_i, best_err = i, err
        if best_i is not None and best_err <= 300:  # 5 分钟内才算同一事件
            used[key].add(best_i)
            st["matched"] += 1
            st["errs"].append(best_err)
            if best_err > tol_s:
                misses.append({**t, "err_s": round(best_err, 1),
                               "reason": "误差超限"})
        else:
            st["miss"] += 1
            misses.append({**t, "err_s": None, "reason": "未检出"})

    # 误报:检出事件找不到真值对应
    truth_keys = defaultdict(list)
    for t in truth:
        truth_keys[(t["mmsi"], t["etype"])].append(t["ts"])
    false_pos = 0
    for key, ts_list in detected.items():
        if key[0] in shared:
            continue
        for i, ts in enumerate(ts_list):
            if i in used[key]:
                continue
            if not any(abs(ts - t0) <= 300 for t0 in truth_keys.get(key, [])):
                false_pos += 1

    report = {"per_type": {}, "false_positive": false_pos,
              "skipped_shared_mmsi": len(skipped),
              "shared_flagged": sorted(shared), "problems": misses}
    ok = True
    for etype, st in sorted(per_type.items()):
        errs = st["errs"]
        max_err = max(errs) if errs else 0.0
        mean_err = sum(errs) / len(errs) if errs else 0.0
        report["per_type"][etype] = {
            "truth": st["n"], "matched": st["matched"], "miss": st["miss"],
            "max_err_s": round(max_err, 1), "mean_err_s": round(mean_err, 1),
        }
        if st["miss"] > 0 or max_err > tol_s:
            ok = False
    report["pass"] = ok and false_pos == 0
    return report
