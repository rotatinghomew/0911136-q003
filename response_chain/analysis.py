"""整理各站原始湿润区间与明确缺测，并建立跨站过程目录。"""

from collections import defaultdict
from datetime import datetime

from .processes import build_catalog


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


def summarize(tables, metadata=None, config=None):
    """现行摘要保留独立区间统计，并新增跨站过程目录。"""
    catalog = build_catalog(tables, config=config, metadata=metadata or {})
    return {"station_intervals": station_intervals(tables), "notes": tables["notes"],
            "catalog": catalog,
            "scope": "独立原始区间摘要，并附跨站统一过程目录；过程按绝对时间分段，不按雨量排序"}
