# 云澜港 AIS 处理链路

岸基 AIS 原始 NMEA 报文 → 解码清洗 → 电子围栏事件 → 航次/统计 → 网页展示。

## 运行

```powershell
py run_all.py              # 端到端:生成模拟报文→接入→清洗→事件→航次→比对真值
py -m ais_port.webapp      # 网页: http://127.0.0.1:5000
```

## 模块

| 文件 | 职责 |
|---|---|
| `ais_port/nmea.py` | 校验和、多片重组、六比特编解码、1/2/3/5/18/24 型报文解析 |
| `ais_port/pipeline.py` | 日志接入:坏包丢弃计数、动静数据分库存储 |
| `ais_port/clean.py` | 多船共用 MMSI 识别(船名冲突+位置矛盾);跳点/漂移/超速点清洗 |
| `ais_port/events.py` | 电子围栏事件:进港/出港(线段插值)、靠泊/锚泊(停船段),精确到分 |
| `ais_port/voyages.py` | 连续航迹切航次,自动判断起止港 |
| `ais_port/stats.py` | 每日进出港艘次、分船型平均锚泊/靠泊时长 |
| `ais_port/generator.py` | 报文生成器:一个月港口活动,注入坏校验和/丢片/错位/共用 MMSI 等噪声 |
| `ais_port/validate.py` | 识别事件与预设记录逐条比对(容差 2 分钟) |
| `ais_port/webapp.py` | Flask 网页:报文接入/船舶档案/航迹回放/港口事件/统计报表 |

## 数据

`data/nmea_month.log` 模拟报文(约 15.7 万条),`data/ground_truth.csv` 预设真值,
`data/ais.db` SQLite 库。日志行格式:`<unix时间戳> <NMEA语句>`。
