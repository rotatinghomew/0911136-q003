"""跨站降雨过程目录：可解释分段、跨站合并与人工备注解析。

本模块只使用绝对时间、站点坐标和供数表，不按自然日切段，也不按雨量排序。
分段与合并的每一个决定都保留规则、阈值和实际间隔，便于复核。
"""

from collections import defaultdict
from datetime import datetime, timezone
from hashlib import sha256
from math import atan2, degrees, hypot, isfinite

DEFAULT_CONFIG = {
    "dry_gap_minutes": 120.0,
    "clock_offset_minutes": 15.0,
    "missing_bridge_minutes": 360.0,
    "include_suspect": False,
}

NUMERIC_CONFIG = ("dry_gap_minutes", "clock_offset_minutes", "missing_bridge_minutes")
CONFIG_KEYS = frozenset(DEFAULT_CONFIG)
EXCLUDED_QUALITY = frozenset({"withdrawn", "invalid", "retracted"})
ACCEPTED_QUALITY = frozenset({"valid", "suspect"})

RULES = {
    "time": "统一使用带时区的绝对时间并归一到 UTC；跨午夜不按自然日拆分或拼接。",
    "station_segmentation": (
        "同站湿润区间按绝对时间整理：重叠或相接必合并；其余间隔按 dry_gap_minutes、"
        "missing_bridge_minutes 和缺测覆盖判断。"
    ),
    "cross_station_merge": (
        "跨站区间重叠或相接时合并；不同站之间间隔不超过 clock_offset_minutes "
        "时优先按钟差容忍并入同一过程，不当作已观测干旱；超过容忍后按已观测干旱判断。"
    ),
    "missing_data": (
        "gaps 是无观测区间，不是干旱。间隔内只要有一站缺测，就不能把整个间隔当作"
        "已观测干旱，也不能用缺测证明过程结束。缺测间隔不超过 missing_bridge_minutes "
        "时桥接；超过时拆开并把边界标为未证实结束（proves_boundary=false）。只有未被缺测"
        "覆盖的已观测干旱超过 dry_gap_minutes 才是已证实的结束边界。"
    ),
    "ordering": "过程按开始时间升序排列，同刻按 process_id 排序；雨量不参与排序。",
    "notes": (
        "notes.process_ref 引用人工标签，成员由同代 metadata.manual_process_members 提供；"
        "成员对应的当前过程可能是一个、多个（被拆开）或没有（被撤回或无法解析）。"
        "status 取值：resolved、partial（部分成员已无法解析）、split、missing、unknown_ref。"
    ),
}


def normalise_config(config=None):
    """校验并补全使用者配置，未知项直接拒绝而不是静默忽略。"""
    if config is None:
        config = {}
    if not isinstance(config, dict):
        raise ValueError("配置必须是对象")
    unknown = set(config) - CONFIG_KEYS
    if unknown:
        raise ValueError("未知配置项：" + "、".join(sorted(unknown)))
    result = dict(DEFAULT_CONFIG)
    for key in NUMERIC_CONFIG:
        value = config.get(key, result[key])
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(key + " 必须是数值")
        if not isfinite(value) or value < 0:
            raise ValueError(key + " 必须是非负有限数值")
        result[key] = float(value)
    include = config.get("include_suspect", result["include_suspect"])
    if not isinstance(include, bool):
        raise ValueError("include_suspect 必须是布尔值")
    result["include_suspect"] = include
    return result


def parse_time(value):
    """只接受带时区的时间，避免把本地钟面时间误当绝对时间。"""
    if not isinstance(value, str):
        raise ValueError("时间必须是 ISO 8601 字符串")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError as error:
        raise ValueError("无法解析时间：" + value) from error
    if moment.tzinfo is None:
        raise ValueError("时间必须带时区偏移：" + value)
    return moment.astimezone(timezone.utc)


def iso(moment):
    return moment.astimezone(timezone.utc).isoformat()


def span_minutes(start, end):
    return (end - start).total_seconds() / 60.0


def _interval_union(intervals):
    """合并重叠区间，返回按起点排序的 [(start, end)]。"""
    ordered = sorted((start, end) for start, end in intervals if start < end)
    merged = []
    for start, end in ordered:
        if merged and start <= merged[-1][1]:
            if end > merged[-1][1]:
                merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return merged


def _covered_minutes(start, end, intervals):
    """[start, end] 被给定区间覆盖的分钟数。"""
    total = 0.0
    for left, right in _interval_union(intervals):
        left = max(left, start)
        right = min(right, end)
        if left < right:
            total += (right - left).total_seconds() / 60.0
    return total


def _gap_pairs(gaps, station_ids):
    """把各站缺测整理成 (start, end) 对，供区间运算使用。"""
    return [(gap["start"], gap["end"]) for station_id in station_ids
            for gap in gaps.get(station_id, [])]


def _observed_segments(start, end, missing):
    """[start, end] 中未被缺测覆盖的连续片段；(start, end) 必须 start < end。"""
    cursor = start
    pieces = []
    for left, right in _interval_union(missing):
        left = max(left, start)
        right = min(right, end)
        if left >= right:
            continue
        if left > cursor:
            pieces.append((cursor, left))
        cursor = max(cursor, right)
        if cursor >= end:
            break
    if cursor < end:
        pieces.append((cursor, end))
    return pieces


def classify_connection(left_end, right_start, participants, gaps, config, allow_clock=False):
    """决定两个相邻湿润片段是否属于同一过程，并给出可复核的依据。

    缺测只是“没有观测”，不能当作已观测干旱，也不能单独证明过程结束。
    """
    length = span_minutes(left_end, right_start)
    missing = _gap_pairs(gaps, participants)
    if length <= 0:
        return {
            "decision": "merge",
            "reason": "overlap_or_touch",
            "proves_end": False,
            "gap_minutes": round(length, 6),
            "missing_minutes": 0.0,
            "observed_dry_max_minutes": 0.0,
            "participants": sorted(participants),
            "left_end": iso(left_end),
            "right_start": iso(right_start),
            "thresholds": {key: config[key] for key in NUMERIC_CONFIG},
        }
    missing_minutes = _covered_minutes(left_end, right_start, missing)
    observed = _observed_segments(left_end, right_start, missing)
    observed_max = max((span_minutes(a, b) for a, b in observed), default=0.0)
    decision = {
        "gap_minutes": round(length, 6),
        "missing_minutes": round(missing_minutes, 6),
        "observed_dry_max_minutes": round(observed_max, 6),
        "participants": sorted(participants),
        "left_end": iso(left_end),
        "right_start": iso(right_start),
        "thresholds": {key: config[key] for key in NUMERIC_CONFIG},
    }
    if allow_clock and length <= config["clock_offset_minutes"]:
        # 跨站小间隔先按时钟偏移容忍解释：这类间隔可能来自钟差，不算已观测干旱。
        return {**decision, "decision": "merge", "reason": "clock_offset", "proves_end": False}
    if observed_max > config["dry_gap_minutes"]:
        # 有完整观测的干旱段超过阈值，这是已证实的结束，不是缺测推断。
        return {**decision, "decision": "split", "reason": "observed_dry_gap", "proves_end": True}
    if missing_minutes > 0:
        if length <= config["missing_bridge_minutes"]:
            return {**decision, "decision": "merge", "reason": "missing_bridged", "proves_end": False}
        return {**decision, "decision": "split", "reason": "missing_unresolved", "proves_end": False}
    return {**decision, "decision": "merge", "reason": "short_observed_gap", "proves_end": False}


def _load_stations(tables):
    stations = {}
    for row in tables.get("stations", []):
        station_id = row["station_id"]
        if station_id in stations:
            raise ValueError("站点主键重复：" + str(station_id))
        try:
            x = float(row["x"])
            y = float(row["y"])
        except (TypeError, ValueError) as error:
            raise ValueError("站点坐标必须是数值：" + str(station_id)) from error
        if not isfinite(x) or not isfinite(y):
            raise ValueError("站点坐标必须是有限数值：" + str(station_id))
        stations[station_id] = {"x": x, "y": y}
    return stations


def _load_gaps(tables, stations):
    gaps = defaultdict(list)
    identifiers = set()
    for row in tables.get("gaps", []):
        gap_id = row["gap_id"]
        if gap_id in identifiers:
            raise ValueError("缺测主键重复：" + str(gap_id))
        identifiers.add(gap_id)
        station_id = row["station_id"]
        if station_id not in stations:
            raise ValueError("缺测记录引用未知站点：" + str(station_id))
        start, end = parse_time(row["start"]), parse_time(row["end"])
        if start >= end:
            raise ValueError("缺测区间终点必须晚于起点：" + str(gap_id))
        gaps[station_id].append({"gap_id": gap_id, "start": start, "end": end})
    return gaps


def _load_pulses(tables, stations, config):
    included = []
    excluded = []
    seen = set()
    for row in tables.get("pulses", []):
        pulse_id = row["pulse_id"]
        if pulse_id in seen:
            raise ValueError("湿润记录主键重复：" + str(pulse_id))
        seen.add(pulse_id)
        station_id = row["station_id"]
        if station_id not in stations:
            raise ValueError("湿润记录引用未知站点：" + str(station_id))
        quality = row.get("quality")
        if quality not in ACCEPTED_QUALITY and quality not in EXCLUDED_QUALITY:
            raise ValueError("未知观测质量标记：" + repr(quality))
        start, end = parse_time(row["start"]), parse_time(row["end"])
        try:
            rain_mm = float(row["rain_mm"])
            error_minutes = float(row["time_error_minutes"])
        except (TypeError, ValueError) as error:
            raise ValueError("雨量或时间误差必须是数值：" + str(pulse_id)) from error
        if not isfinite(rain_mm) or rain_mm < 0:
            raise ValueError("雨量必须是非负有限数值：" + str(pulse_id))
        if not isfinite(error_minutes) or error_minutes < 0:
            raise ValueError("时间误差必须是非负有限数值：" + str(pulse_id))
        record = {
            "pulse_id": pulse_id,
            "station_id": station_id,
            "start": start,
            "end": end,
            "rain_mm": rain_mm,
            "time_error_minutes": error_minutes,
            "quality": quality,
        }
        if quality in EXCLUDED_QUALITY:
            # 已撤回或无效的观测不参与分段，也不因时间戳异常而阻断整批资料。
            excluded.append({**record, "reason": "withdrawn_or_invalid"})
            continue
        if quality == "suspect" and not config["include_suspect"]:
            excluded.append({**record, "reason": "suspect_excluded"})
            continue
        if start >= end:
            raise ValueError("湿润区间终点必须晚于起点：" + str(pulse_id))
        included.append(record)
    return included, excluded


def _segment_station(station_id, pulses, gaps, config):
    """同站按绝对时间整理成片段；每个分裂决定都记录在 finalized['ended_by']。"""
    ordered = sorted(pulses, key=lambda row: (row["start"], row["end"], row["pulse_id"]))
    segments = []
    current = None
    for pulse in ordered:
        if current is None:
            current = {"station_id": station_id, "start": pulse["start"],
                       "end": pulse["end"], "pulses": [pulse], "ended_by": None}
            continue
        decision = classify_connection(current["end"], pulse["start"], {station_id},
                                       gaps, config, allow_clock=False)
        if decision["decision"] == "merge":
            current["end"] = max(current["end"], pulse["end"])
            current["pulses"].append(pulse)
        else:
            current["ended_by"] = decision
            segments.append(current)
            current = {"station_id": station_id, "start": pulse["start"],
                       "end": pulse["end"], "pulses": [pulse], "ended_by": None}
    if current is not None:
        current["ended_by"] = {"decision": "end", "reason": "observation_window_end",
                               "boundary": "end", "proves_boundary": False}
        segments.append(current)
    return segments


def _start_boundary(decision):
    """新过程的起始边界：沿用前一段连接的证据，用 proves_boundary 表达。"""
    return {
        **{key: value for key, value in decision.items() if key != "proves_end"},
        "boundary": "start",
        "proves_boundary": bool(decision.get("proves_end")),
    }


def _end_boundary(decision):
    return {
        **{key: value for key, value in decision.items() if key != "proves_end"},
        "boundary": "end",
        "proves_boundary": bool(decision.get("proves_end")),
    }


def _cluster_segments(segments, gaps, config):
    """跨站聚类：重叠、时钟偏移容忍内或可桥接的片段归入同一过程。"""
    ordered = sorted(segments, key=lambda seg: (seg["start"], seg["end"], seg["station_id"]))
    clusters = []
    for segment in ordered:
        if not clusters:
            clusters.append({
                "start": segment["start"], "end": segment["end"],
                "segments": [segment], "stations": {segment["station_id"]},
                "start_boundary": {"decision": "start", "reason": "first_observation",
                                   "boundary": "start", "proves_boundary": False},
                "end_boundary": None,
            })
            continue
        cluster = clusters[-1]
        decision = classify_connection(cluster["end"], segment["start"],
                                       cluster["stations"] | {segment["station_id"]},
                                       gaps, config, allow_clock=True)
        if decision["decision"] == "merge":
            cluster["end"] = max(cluster["end"], segment["end"])
            cluster["segments"].append(segment)
            cluster["stations"].add(segment["station_id"])
        else:
            cluster["end_boundary"] = _end_boundary(decision)
            clusters.append({
                "start": segment["start"], "end": segment["end"],
                "segments": [segment], "stations": {segment["station_id"]},
                "start_boundary": _start_boundary(decision),
                "end_boundary": None,
            })
    if clusters and clusters[-1]["end_boundary"] is None:
        clusters[-1]["end_boundary"] = {"decision": "end", "reason": "observation_window_end",
                                        "boundary": "end", "proves_boundary": False}
    return clusters


def _bearing(from_point, to_point):
    """平面坐标下的方位角，0 度指向 +y，顺时针增加。"""
    dx = to_point["x"] - from_point["x"]
    dy = to_point["y"] - from_point["y"]
    if dx == 0 and dy == 0:
        return None
    return (degrees(atan2(dx, dy)) + 360.0) % 360.0


def _distance(first, second):
    return hypot(second["x"] - first["x"], second["y"] - first["y"])


def _round(value, digits=6):
    return round(value, digits)


def _station_episodes(cluster):
    episodes = []
    for segment in sorted(cluster["segments"], key=lambda seg: (seg["start"], seg["station_id"])):
        episodes.append({
            "station_id": segment["station_id"],
            "start": iso(segment["start"]),
            "end": iso(segment["end"]),
            "pulse_ids": sorted(pulse["pulse_id"] for pulse in segment["pulses"]),
            "ended_by": segment["ended_by"],
        })
    return episodes


def _movement(cluster, stations):
    onsets = {}
    for segment in cluster["segments"]:
        current = onsets.get(segment["station_id"])
        if current is None or segment["start"] < current:
            onsets[segment["station_id"]] = segment["start"]
    sequence = sorted(onsets, key=lambda station_id: (onsets[station_id], station_id))
    steps = []
    for first, second in zip(sequence, sequence[1:]):
        position_a, position_b = stations[first], stations[second]
        steps.append({
            "from_station": first,
            "to_station": second,
            "onset_delay_minutes": _round(span_minutes(onsets[first], onsets[second])),
            "distance_m": _round(_distance(position_a, position_b)),
            "bearing_degrees": _round(_bearing(position_a, position_b), 3)
            if _bearing(position_a, position_b) is not None else None,
        })
    net = None
    if len(sequence) >= 2:
        first, last = sequence[0], sequence[-1]
        span = span_minutes(onsets[first], onsets[last])
        distance = _distance(stations[first], stations[last])
        net = {
            "from_station": first,
            "to_station": last,
            "onset_span_minutes": _round(span),
            "displacement_m": _round(distance),
            "bearing_degrees": _round(_bearing(stations[first], stations[last]), 3)
            if _bearing(stations[first], stations[last]) is not None else None,
            "mean_speed_m_per_minute": _round(distance / span) if span > 0 else None,
        }
    return sequence, steps, net


def _spatial(cluster, stations):
    unique = sorted(cluster["stations"])
    points = [stations[station_id] for station_id in unique]
    centroid = {"x": _round(sum(p["x"] for p in points) / len(points)),
                "y": _round(sum(p["y"] for p in points) / len(points))}
    bbox = {
        "min_x": _round(min(p["x"] for p in points)),
        "min_y": _round(min(p["y"] for p in points)),
        "max_x": _round(max(p["x"] for p in points)),
        "max_y": _round(max(p["y"] for p in points)),
    }
    maximum = 0.0
    for index, first in enumerate(points):
        for second in points[index + 1:]:
            maximum = max(maximum, _distance(first, second))
    return {
        "station_ids": unique,
        "station_count": len(unique),
        "centroid": centroid,
        "centroid_basis": "unique_stations_unweighted",
        "bbox": bbox,
        "max_station_distance_m": _round(maximum),
    }


def _missing_overview(cluster, gaps, start, end):
    identifiers = set()
    by_station = {}
    for station_id in sorted(cluster["stations"]):
        clipped = []
        for gap in gaps.get(station_id, []):
            left, right = max(gap["start"], start), min(gap["end"], end)
            if left < right:
                clipped.append((left, right))
                identifiers.add(gap["gap_id"])
        by_station[station_id] = _round(sum(span_minutes(a, b) for a, b in clipped))
    union = _gap_pairs(gaps, cluster["stations"])
    union_minutes = _covered_minutes(start, end, union)
    return {
        "gap_ids": sorted(identifiers),
        # 同一时刻多站同时缺测只统计一次，避免重复放大。
        "missing_minutes_within": _round(union_minutes),
        "missing_minutes_by_station": by_station,
        "one_or_more_stations_blind_minutes": _round(union_minutes),
    }


def _process_dict(cluster, stations, gaps, config):
    pulse_ids = sorted(pulse["pulse_id"] for segment in cluster["segments"]
                       for pulse in segment["pulses"])
    process_id = "prc-" + sha256(("\n".join(pulse_ids)).encode("utf-8")).hexdigest()[:16]
    start, end = cluster["start"], cluster["end"]
    duration = span_minutes(start, end)
    sequence, steps, net = _movement(cluster, stations)
    spatial = _spatial(cluster, stations)
    rain_total = sum(pulse["rain_mm"] for segment in cluster["segments"]
                     for pulse in segment["pulses"])
    maximum_error = max(pulse["time_error_minutes"] for segment in cluster["segments"]
                        for pulse in segment["pulses"])
    signature = []
    if duration > 0:
        for station_id in sequence:
            onset = min(segment["start"] for segment in cluster["segments"]
                        if segment["station_id"] == station_id)
            signature.append({"station_id": station_id,
                              "onset_fraction": _round(span_minutes(start, onset) / duration)})
    missing = _missing_overview(cluster, gaps, start, end)
    missing["missing_fraction"] = (_round(missing["missing_minutes_within"] / duration)
                                   if duration > 0 else 0.0)
    return {
        "process_id": process_id,
        "start": iso(start),
        "end": iso(end),
        "duration_minutes": _round(duration),
        "station_ids": spatial["station_ids"],
        "station_count": spatial["station_count"],
        "pulse_ids": pulse_ids,
        "pulse_count": len(pulse_ids),
        "episode_count": len(cluster["segments"]),
        "onset_sequence": sequence,
        "movement": {"steps": steps, "net": net,
                     "ordering_basis": "station_first_onset_time"},
        "shape_signature": signature,
        "spatial": spatial,
        "rain_mm_total": _round(rain_total),
        "time_error_minutes_max": _round(maximum_error),
        "missing": missing,
        "station_episodes": _station_episodes(cluster),
        "start_boundary": cluster["start_boundary"],
        "end_boundary": cluster["end_boundary"],
    }


def _resolve_notes(tables, metadata, processes):
    members = (metadata or {}).get("manual_process_members", {})
    if not isinstance(members, dict):
        raise ValueError("metadata.manual_process_members 必须是对象")
    by_label = {}
    for label, pulse_ids in members.items():
        if not isinstance(pulse_ids, list) or not all(isinstance(item, str) for item in pulse_ids):
            raise ValueError("人工过程成员必须是 pulse_id 字符串列表：" + str(label))
        by_label[label] = set(pulse_ids)
    owners = {}
    for process in processes:
        for pulse_id in process["pulse_ids"]:
            owners.setdefault(pulse_id, set()).add(process["process_id"])
    resolved = []
    seen = set()
    for row in tables.get("notes", []):
        note_id = row["note_id"]
        if note_id in seen:
            raise ValueError("备注主键重复：" + str(note_id))
        seen.add(note_id)
        reference = row["process_ref"]
        declared = sorted(by_label.get(reference, []))
        process_ids = sorted({process_id for pulse_id in declared
                              for process_id in owners.get(pulse_id, set())})
        unresolved = sorted(pulse_id for pulse_id in declared if pulse_id not in owners)
        if reference not in by_label:
            status = "unknown_ref"
        elif not process_ids:
            status = "missing"
        elif len(process_ids) > 1:
            status = "split"
        elif unresolved:
            status = "partial"
        else:
            status = "resolved"
        resolved.append({
            "note_id": note_id,
            "process_ref": reference,
            "text": row["text"],
            "declared_pulse_ids": declared,
            "process_ids": process_ids,
            "unresolved_pulse_ids": unresolved,
            "status": status,
        })
    links = defaultdict(list)
    for note in resolved:
        for process_id in note["process_ids"]:
            links[process_id].append({
                "note_id": note["note_id"],
                "process_ref": note["process_ref"],
                "status": note["status"],
                "shared_with": [other for other in note["process_ids"] if other != process_id],
            })
    for process in processes:
        process["manual_refs"] = sorted(links.get(process["process_id"], []),
                                        key=lambda item: (item["note_id"], item["process_ref"]))
    return resolved


def build_catalog(tables, config=None, metadata=None):
    """建立跨站过程目录。排序只看时间，不看雨量。"""
    settings = normalise_config(config)
    stations = _load_stations(tables)
    gaps = _load_gaps(tables, stations)
    pulses, excluded = _load_pulses(tables, stations, settings)
    grouped = defaultdict(list)
    for pulse in pulses:
        grouped[pulse["station_id"]].append(pulse)
    segments = []
    for station_id in sorted(grouped):
        segments.extend(_segment_station(station_id, grouped[station_id], gaps, settings))
    clusters = _cluster_segments(segments, gaps, settings)
    processes = [_process_dict(cluster, stations, gaps, settings) for cluster in clusters]
    processes.sort(key=lambda process: (process["start"], process["process_id"]))
    notes = _resolve_notes(tables, metadata, processes)
    diagnostics = {
        "pulse_count": len(pulses) + len(excluded),
        "included_pulse_count": len(pulses),
        "excluded_pulses": [
            {"pulse_id": row["pulse_id"], "station_id": row["station_id"],
             "quality": row["quality"], "reason": row["reason"]}
            for row in sorted(excluded, key=lambda row: row["pulse_id"])
        ],
        "gap_count": sum(len(value) for value in gaps.values()),
        "process_count": len(processes),
        "notes_without_single_process": sorted(
            note["note_id"] for note in notes if note["status"] != "resolved"),
    }
    return {
        "scope": "跨站降雨过程目录；按绝对时间分段，缺测不作为过程结束证据，不按雨量排序。",
        "config": settings,
        "rules": RULES,
        "processes": processes,
        "notes": notes,
        "diagnostics": diagnostics,
    }

