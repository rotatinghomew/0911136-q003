"""按过程形态检索历史相似过程。

排序依据是持续时间、移动次序与空间覆盖等形态特征，不使用雨量大小。
每个得分都带可复核的分项与依据；覆盖不同的维度被显式列为无法比较。
"""

from math import hypot, isfinite

from .processes import parse_time

SHAPE_DIMENSIONS = ("duration", "movement", "spatial_coverage", "timing_profile")

DIMENSION_LABELS = {
    "duration": "持续时间",
    "movement": "移动次序与速度",
    "spatial_coverage": "空间覆盖",
    "timing_profile": "起止形态",
}

DEFAULT_WEIGHTS = {
    "duration": 1.0,
    "movement": 1.5,
    "spatial_coverage": 1.0,
    "timing_profile": 1.0,
}

# 每个特征的容差尺度：差异达到该尺度时该维得分降为 0。
DEFAULT_SCALES = {
    "duration_minutes": 240.0,
    "onset_span_minutes": 120.0,
    "speed_m_per_minute": 80.0,
    "max_station_distance_m": 2000.0,
    "centroid_distance_m": 1500.0,
    "bearing_degrees": 90.0,
    "onset_fraction": 0.5,
}

_MOVEMENT_LABELS = {
    "onset_span_minutes": "起爆时间跨度",
    "speed_m_per_minute": "平均移动速度",
    "bearing_degrees": "移动方向",
}


def _validate_metric(name, value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(name + " 必须是数值")
    if not isfinite(value) or value < 0:
        raise ValueError(name + " 必须是非负有限数值")
    return float(value)


def normalise_weights(weights=None):
    merged = dict(DEFAULT_WEIGHTS)
    if weights is not None:
        if not isinstance(weights, dict):
            raise ValueError("权重必须是对象")
        unknown = set(weights) - set(SHAPE_DIMENSIONS)
        if unknown:
            raise ValueError("未知权重维度：" + "、".join(sorted(unknown)))
        for name, value in weights.items():
            merged[name] = _validate_metric("权重 " + name, value)
    return merged


def normalise_scales(scales=None):
    merged = dict(DEFAULT_SCALES)
    if scales is not None:
        if not isinstance(scales, dict):
            raise ValueError("容差必须是对象")
        unknown = set(scales) - set(DEFAULT_SCALES)
        if unknown:
            raise ValueError("未知容差项：" + "、".join(sorted(unknown)))
        for name, value in scales.items():
            merged[name] = _validate_metric("容差 " + name, value)
    return merged


def _closeness(difference, scale):
    if scale <= 0:
        return 1.0 if difference == 0 else 0.0
    return max(0.0, 1.0 - min(1.0, abs(difference) / scale))


def _bearing_difference(first, second):
    if first is None or second is None:
        return None
    difference = abs(first - second) % 360.0
    return min(difference, 360.0 - difference)


def _process_index(catalog):
    return {process["process_id"]: process for process in catalog["processes"]}


def _shape(process):
    net = process["movement"]["net"] or {}
    centroid = process["spatial"]["centroid"]
    return {
        "start": parse_time(process["start"]),
        "end": parse_time(process["end"]),
        "duration_minutes": process["duration_minutes"],
        "onset_span_minutes": net.get("onset_span_minutes"),
        "speed_m_per_minute": net.get("mean_speed_m_per_minute"),
        "bearing_degrees": net.get("bearing_degrees"),
        "max_station_distance_m": process["spatial"]["max_station_distance_m"],
        "centroid_x": centroid["x"],
        "centroid_y": centroid["y"],
        "onset_positions": {item["station_id"]: item["onset_fraction"]
                            for item in process["shape_signature"]},
        "stations": set(process["station_ids"]),
        "missing_fraction": process["missing"]["missing_fraction"],
        "unproven_boundary_reasons": sorted({
            boundary["reason"]
            for boundary in (process["start_boundary"], process["end_boundary"])
            if boundary.get("reason") in ("missing_unresolved", "observation_window_end")
            and not boundary.get("proves_boundary", False)
        }),
    }


def _duration_score(target, candidate, scale):
    result = _closeness(target["duration_minutes"] - candidate["duration_minutes"], scale["duration_minutes"])
    return {
        "score": round(result, 4),
        "comparable": True,
        "basis": "持续分钟差 " + str(round(abs(target["duration_minutes"] - candidate["duration_minutes"]), 3))
                 + "，容差 " + str(scale["duration_minutes"]),
        "metrics": {"target": target["duration_minutes"], "candidate": candidate["duration_minutes"]},
    }


def _movement_score(target, candidate, scale):
    missing = []
    for name in ("onset_span_minutes", "speed_m_per_minute", "bearing_degrees"):
        if target[name] is None or candidate[name] is None:
            missing.append(name)
    if all(target[name] is None for name in ("onset_span_minutes", "speed_m_per_minute",
                                             "bearing_degrees")) or \
            all(candidate[name] is None for name in ("onset_span_minutes", "speed_m_per_minute",
                                                     "bearing_degrees")):
        return {
            "score": None,
            "comparable": False,
            "basis": "至少一方没有跨站移动方向（需至少两站按时间先后出现），无法比较移动次序",
            "metrics": {},
        }
    pieces = []
    if target["onset_span_minutes"] is not None and candidate["onset_span_minutes"] is not None:
        pieces.append(_closeness(target["onset_span_minutes"] - candidate["onset_span_minutes"],
                                 scale["onset_span_minutes"]))
    if target["speed_m_per_minute"] is not None and candidate["speed_m_per_minute"] is not None:
        pieces.append(_closeness(target["speed_m_per_minute"] - candidate["speed_m_per_minute"],
                                 scale["speed_m_per_minute"]))
    bearing_note = ""
    if target["bearing_degrees"] is not None and candidate["bearing_degrees"] is not None:
        difference = _bearing_difference(target["bearing_degrees"], candidate["bearing_degrees"])
        pieces.append(_closeness(difference, scale["bearing_degrees"]))
        bearing_note = "，方向差 " + str(round(difference, 3)) + " 度"
    order_note = _order_note(target, candidate)
    score = sum(pieces) / len(pieces) if pieces else None
    omitted = ""
    if missing:
        omitted = ("；其中 " + "、".join(_MOVEMENT_LABELS[name] for name in missing)
                   + " 至少一方缺失，未参与平均")
    return {
        "score": round(score, 4) if score is not None else None,
        "comparable": score is not None,
        "basis": "起爆次序" + order_note + bearing_note + omitted,
        "metrics": {
            "target": {name: target[name] for name in
                       ("onset_span_minutes", "speed_m_per_minute", "bearing_degrees")},
            "candidate": {name: candidate[name] for name in
                          ("onset_span_minutes", "speed_m_per_minute", "bearing_degrees")},
            "omitted": missing,
        },
    }


def _same_order(target, candidate):
    shared = target["stations"] & candidate["stations"]
    if len(shared) < 2:
        return None
    def order(shape):
        return [station for station in shape["onset_positions"] if station in shared]
    # onset_positions 按开始时间插入，dict 保持插入顺序。
    return order(target) == order(candidate)


def _order_note(target, candidate):
    same = _same_order(target, candidate)
    if same is None:
        return "共同站不足两处，无法判断"
    return "一致" if same else "不同"


def _spatial_score(target, candidate, scale):
    centroid_distance = hypot(target["centroid_x"] - candidate["centroid_x"],
                              target["centroid_y"] - candidate["centroid_y"])
    extent_difference = abs(target["max_station_distance_m"] - candidate["max_station_distance_m"])
    pieces = [
        _closeness(centroid_distance, scale["centroid_distance_m"]),
        _closeness(extent_difference, scale["max_station_distance_m"]),
    ]
    score = sum(pieces) / len(pieces)
    shared = sorted(target["stations"] & candidate["stations"])
    only_target = sorted(target["stations"] - candidate["stations"])
    only_candidate = sorted(candidate["stations"] - target["stations"])
    return {
        "score": round(score, 4),
        "comparable": True,
        "basis": ("中心距 " + str(round(centroid_distance, 3)) + " 米，站点跨度差 "
                  + str(round(extent_difference, 3)) + " 米；共同站 "
                  + (",".join(shared) if shared else "无")
                  + ("；仅目标有 " + ",".join(only_target) if only_target else "")
                  + ("；仅候选有 " + ",".join(only_candidate) if only_candidate else "")),
        "metrics": {
            "centroid_distance_m": round(centroid_distance, 3),
            "extent_difference_m": round(extent_difference, 3),
            "shared_stations": shared,
            "only_target": only_target,
            "only_candidate": only_candidate,
        },
        "coverage_gap": bool(only_target or only_candidate),
    }


def _timing_score(target, candidate, scale):
    """起止形态：从过程开始到各站起爆的相对位置，独立于观测站集合。"""
    left = target["onset_positions"]
    right = candidate["onset_positions"]
    if not left and not right:
        return {"score": None, "comparable": False,
                "basis": "两者都没有站点起爆信息", "metrics": {}}
    common = set(left) & set(right)
    if common:
        differences = [abs(left[station] - right[station]) for station in sorted(common)]
        score = sum(_closeness(value, scale["onset_fraction"]) for value in differences) / len(differences)
        basis = ("按共同站 " + ",".join(sorted(common)) + " 的相对起爆位置比较，"
                 + "最大偏差 " + str(round(max(differences), 4)))
        metrics = {"shared_stations": sorted(common),
                   "onsets": {station: {"target": left[station], "candidate": right[station]}
                              for station in sorted(common)}}
    else:
        differences = [abs(a - b) for a, b in
                       zip(sorted(left.values(), key=lambda value: (value,)),
                           sorted(right.values(), key=lambda value: (value,)))]
        if not differences:
            return {"score": None, "comparable": False,
                    "basis": "起爆位置集合为空", "metrics": {}}
        score = sum(_closeness(value, scale["onset_fraction"]) for value in differences) / len(differences)
        basis = ("无共同站，仅按起爆位置分布比较，最大偏差 "
                 + str(round(max(differences), 4))
                 + "；分布比较不保证站点一一对应")
        metrics = {"shared_stations": [], "sorted_onsets": {
            "target": sorted(left.values()), "candidate": sorted(right.values())}}
    return {"score": round(score, 4), "comparable": True, "basis": basis, "metrics": metrics}


def _combine(dimensions, weights):
    available = [(name, dimension) for name, dimension in dimensions.items()
                 if dimension["comparable"] and dimension["score"] is not None
                 and weights[name] > 0]
    if not available:
        return None, []
    total_weight = sum(weights[name] for name, _ in available)
    score = sum(weights[name] * dimension["score"] for name, dimension in available) / total_weight
    return round(score, 4), [name for name, _ in available]


def _explain(target, candidate, dimensions, used, score, weights):
    parts = []
    not_comparable = []
    for name in SHAPE_DIMENSIONS:
        if name in used:
            parts.append(DIMENSION_LABELS[name] + "：" + dimensions[name]["basis"])
        elif weights[name] <= 0:
            parts.append(DIMENSION_LABELS[name] + "：本次比较权重为 0，未参与")
        else:
            not_comparable.append(name)
            parts.append(DIMENSION_LABELS[name] + "：无法比较（"
                         + dimensions[name]["basis"] + "）")
    caveats = []
    if target["missing_fraction"] > 0 or candidate["missing_fraction"] > 0:
        caveats.append("过程时段内存在缺测（目标 "
                       + str(round(target["missing_fraction"], 4)) + "，候选 "
                       + str(round(candidate["missing_fraction"], 4))
                       + "），形态比较不改变缺测不等于干旱的结论")
    if "missing_unresolved" in target["unproven_boundary_reasons"] + \
            candidate["unproven_boundary_reasons"]:
        caveats.append("至少一方存在因缺测无法证实的过程边界；持续时间可能被截断，"
                       "不应把该边界当作已确认的过程起止")
    return {"score": score,
            "used_dimensions": used,
            "not_comparable": not_comparable,
            "caveats": caveats,
            "details": parts}


def _find_process(catalog, reference):
    index = _process_index(catalog)
    if reference in index:
        return index[reference], index
    # 允许用人工备注标签检索：标签可能对应一个或多个过程，必须唯一。
    labels = {}
    for note in catalog["notes"]:
        labels.setdefault(note["process_ref"], set()).update(note["process_ids"])
    if reference in labels:
        matches = sorted(labels[reference])
        if len(matches) != 1:
            raise ValueError("人工标签 " + reference + " 对应 "
                             + (str(len(matches)) + " 个过程，请指定 process_id"
                                if matches else "0 个过程（已被撤回或拆分）"))
        return index[matches[0]], index
    raise ValueError("找不到过程或人工标签：" + reference)


def search_similar(catalog, reference, weights=None, scales=None,
                   limit=5, same_geometry_only=False):
    """返回按形态相似度排序的候选，并说明依据与不可比较维度。"""
    weight = normalise_weights(weights)
    scale = normalise_scales(scales)
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
        raise ValueError("limit 必须是非负整数")
    target_process, index = _find_process(catalog, reference)
    target = _shape(target_process)
    results = []
    for process_id in sorted(index):
        if process_id == target_process["process_id"]:
            continue
        candidate_process = index[process_id]
        candidate = _shape(candidate_process)
        if same_geometry_only and candidate["stations"] != target["stations"]:
            continue
        dimensions = {
            "duration": _duration_score(target, candidate, scale),
            "movement": _movement_score(target, candidate, scale),
            "spatial_coverage": _spatial_score(target, candidate, scale),
            "timing_profile": _timing_score(target, candidate, scale),
        }
        score, used = _combine(dimensions, weight)
        results.append({
            "process_id": process_id,
            "start": candidate_process["start"],
            "end": candidate_process["end"],
            "station_ids": candidate_process["station_ids"],
            "duration_minutes": candidate_process["duration_minutes"],
            "similarity": score,
            "explanation": _explain(target, candidate, dimensions, used, score, weight),
            "dimensions": dimensions,
        })
    # 有可比依据的排在前；同分按开始时间，绝不用雨量。
    results.sort(key=lambda item: (
        item["similarity"] is None,
        -(item["similarity"] or 0.0),
        item["start"],
        item["process_id"],
    ))
    return {
        "target": {
            "process_id": target_process["process_id"],
            "start": target_process["start"],
            "end": target_process["end"],
            "station_ids": target_process["station_ids"],
            "onset_sequence": target_process["onset_sequence"],
            "duration_minutes": target_process["duration_minutes"],
        },
        "ranking_basis": "形态相似度（持续时间、移动次序、空间覆盖、起止形态），不按雨量排序",
        "weights": weight,
        "scales": scale,
        "dimension_labels": DIMENSION_LABELS,
        "results": results[:limit],
        "total_candidates": len(results),
    }
