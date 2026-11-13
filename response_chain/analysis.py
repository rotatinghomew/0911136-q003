"""整理各站原始湿润区间与明确缺测。"""
from collections import defaultdict
from datetime import datetime


def station_intervals(tables):
    stations = {row["station_id"] for row in tables["stations"]}
    grouped = defaultdict(lambda: {"wet_minutes": 0.0, "pulse_ids": [], "gap_count": 0})
    for row in tables["pulses"]:
        if row["station_id"] not in stations:
            raise ValueError("湿润记录引用未知站点")
        start, end = datetime.fromisoformat(row["start"]), datetime.fromisoformat(row["end"])
        if start >= end:
            raise ValueError("湿润区间终点必须晚于起点")
        if row["quality"] == "valid":
            grouped[row["station_id"]]["wet_minutes"] += (end - start).total_seconds() / 60
            grouped[row["station_id"]]["pulse_ids"].append(row["pulse_id"])
    for row in tables["gaps"]:
        grouped[row["station_id"]]["gap_count"] += 1
    return dict(sorted(grouped.items()))


def summarize(tables):
    return {"station_intervals": station_intervals(tables), "notes": tables["notes"],
            "scope": "独立原始区间摘要，不表示跨站统一过程"}
