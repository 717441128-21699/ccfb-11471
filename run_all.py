"""端到端:报文生成 -> 接入解码 -> MMSI 共用识别 -> 航迹清洗
        -> 事件识别 -> 航次切分 -> 与预设记录比对(<=2 分钟)

用法: py run_all.py
"""
import json
import os
import time

from ais_port import clean, events, generator, pipeline, validate, voyages
from ais_port.db import init_db, reset_business_tables

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
LOG = os.path.join(DATA, "nmea_month.log")
TRUTH = os.path.join(DATA, "ground_truth.csv")
DB = os.path.join(DATA, "ais.db")


def main():
    os.makedirs(DATA, exist_ok=True)
    t0 = time.time()

    print("[1/6] 生成一个月模拟报文 ...")
    info = generator.generate(LOG, TRUTH)
    print("      报文 %d 条, 真值事件 %d 个, 船舶 %d 艘"
          % (info["sentences"], info["truth_events"], info["ships"]))

    print("[2/6] 接入解码(校验和/分片重组/六比特) ...")
    if not os.path.exists(DB):
        init_db(DB).close()
    else:
        conn = init_db(DB)
        reset_business_tables(conn)
        conn.close()
    counters = pipeline.ingest_file(LOG, DB, source_name=os.path.basename(LOG))
    keys = ["total_lines", "valid_sentences", "bad_checksum", "garbage_lines",
            "messages_assembled", "fragments_dropped", "invalid_pos"]
    print("      " + ", ".join("%s=%s" % (k, counters.get(k, 0)) for k in keys))

    print("[3/6] 多船共用 MMSI 识别 + 航迹清洗 ...")
    conn = init_db(DB)
    flagged = clean.detect_shared_mmsi(conn)
    cstats = clean.clean_tracks(conn)
    print("      共用 MMSI 嫌疑 %d 个; 原始点 %d -> 清洗后 %d "
          "(跳点 %d, 越界 %d, 重复 %d)"
          % (flagged, cstats.get("raw_points", 0), cstats.get("clean_points", 0),
             cstats.get("drop_jump_point", 0), cstats.get("drop_out_of_region", 0),
             cstats.get("drop_duplicate_ts", 0)))

    print("[4/6] 电子围栏事件识别 ...")
    n_ev = events.detect_events(conn)
    print("      事件 %d 个" % n_ev)

    print("[5/6] 航次切分 ...")
    n_vo = voyages.build_voyages(conn)
    print("      航次 %d 个" % n_vo)

    print("[6/6] 与预设进出港记录逐条比对(容差 120 秒) ...")
    report = validate.validate(conn, TRUTH)
    for etype, st in report["per_type"].items():
        print("      %-12s 真值 %3d  匹配 %3d  漏检 %d  最大误差 %5.1fs  平均 %4.1fs"
              % (etype, st["truth"], st["matched"], st["miss"],
                 st["max_err_s"], st["mean_err_s"]))
    print("      误报 %d 个; 共用 MMSI 跳过真值 %d 条(已标记: %s)"
          % (report["false_positive"], report["skipped_shared_mmsi"],
             ",".join(str(m) for m in report["shared_flagged"])))
    if report["problems"]:
        print("      问题明细:")
        for p in report["problems"][:10]:
            print("       -", json.dumps(p, ensure_ascii=False))
    conn.close()

    print("\n结果: %s   (总耗时 %.1f 秒)"
          % ("通过 ✔ 全部事件误差不超过两分钟" if report["pass"] else "未通过 ✘",
             time.time() - t0))
    print("启动网页: py -m ais_port.webapp  然后访问 http://127.0.0.1:5000")
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
