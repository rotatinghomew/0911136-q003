"""资料修订沿革：比较两代过程目录，给出合并、拆分与延续的对应关系。

修订由供数端发布新 generation 引起；本模块不猜测修订意图，只按 pulse 成员
重叠与时间边界给出可解释的对应，并保留人工备注的对位沿革。
"""

def _members(process):
    return set(process["pulse_ids"])


def compare_catalogs(old, new):
    """按 pulse 成员重叠比较两代目录。

    重叠的 pulse 成员决定对应关系；完全不共享 pulse 的过程视为无沿革。
    """
    old_index = {process["process_id"]: process for process in old["processes"]}
    new_index = {process["process_id"]: process for process in new["processes"]}
    links = []
    for old_id in sorted(old_index):
        old_process = old_index[old_id]
        old_members = _members(old_process)
        matches = []
        for new_id in sorted(new_index):
            new_process = new_index[new_id]
            shared = old_members & _members(new_process)
            if shared:
                matches.append({
                    "process_id": new_id,
                    "shared_pulse_count": len(shared),
                    "shared_pulse_ids": sorted(shared),
                    "old_only_pulse_ids": sorted(old_members - _members(new_process)),
                    "new_only_pulse_ids": sorted(_members(new_process) - old_members),
                })
        if not matches:
            relation = "disappeared"
        elif len(matches) == 1 and not matches[0]["old_only_pulse_ids"] \
                and not matches[0]["new_only_pulse_ids"]:
            relation = "unchanged"
        elif len(matches) == 1:
            relation = "revised"
        else:
            relation = "split"
        links.append({
            "old_process_id": old_id,
            "old_start": old_process["start"],
            "old_end": old_process["end"],
            "relation": relation,
            "matches": sorted(matches, key=lambda item: (-item["shared_pulse_count"], item["process_id"])),
        })
    merges = []
    for new_id in sorted(new_index):
        new_process = new_index[new_id]
        sources = sorted(old_id for old_id, old_process in old_index.items()
                         if _members(old_process) & _members(new_process))
        if len(sources) > 1:
            merges.append({
                "new_process_id": new_id,
                "new_start": new_process["start"],
                "new_end": new_process["end"],
                "old_process_ids": sources,
            })
    return {"links": links, "merges": merges,
            "old_process_count": len(old_index), "new_process_count": len(new_index)}


def _note_trail(old, new):
    """人工备注跨修订的沿革：标签声明成员可能增减、消失或对应过程改变。"""
    old_notes = {}
    for note in old["notes"]:
        old_notes.setdefault(note["process_ref"], []).append(note)
    new_notes = {}
    for note in new["notes"]:
        new_notes.setdefault(note["process_ref"], []).append(note)
    trail = []
    for reference in sorted(set(old_notes) | set(new_notes)):
        before = old_notes.get(reference)
        after = new_notes.get(reference)
        entry = {"process_ref": reference}
        if before is None:
            entry["change"] = "new_note"
        elif after is None:
            entry["change"] = "removed_note"
        else:
            old_ids = sorted({process_id for note in before for process_id in note["process_ids"]})
            new_ids = sorted({process_id for note in after for process_id in note["process_ids"]})
            old_members = set().union(*[set(note["declared_pulse_ids"]) for note in before])
            new_members = set().union(*[set(note["declared_pulse_ids"]) for note in after])
            old_status = sorted(note["status"] for note in before)
            new_status = sorted(note["status"] for note in after)
            if old_status != new_status:
                entry["change"] = "status_changed"
            elif old_ids == new_ids and old_members == new_members:
                entry["change"] = "unchanged"
            elif old_members - new_members:
                entry["change"] = "references_retracted"
            elif new_members - old_members:
                entry["change"] = "references_extended"
            elif len(old_ids) < len(new_ids):
                entry["change"] = "process_split"
            elif len(old_ids) > len(new_ids):
                entry["change"] = "process_merged"
            else:
                entry["change"] = "remapped"
            entry["old_members"] = sorted(old_members)
            entry["new_members"] = sorted(new_members)
            entry["old_process_ids"] = old_ids
            entry["new_process_ids"] = new_ids
            entry["old_status"] = old_status
            entry["new_status"] = new_status
        trail.append(entry)
    return trail


def lineage_report(old_catalog, new_catalog):
    """把两代目录的差异整理为可保存的沿革报告，旧目录保持不变。

    两代若使用了不同的分段配置，过程差异可能来自配置而非资料修订，
    必须显式披露，避免把配置变化误报成资料沿革。
    """
    comparison = compare_catalogs(old_catalog, new_catalog)
    notes = _note_trail(old_catalog, new_catalog)
    old_config = old_catalog.get("config")
    new_config = new_catalog.get("config")
    config_changed = old_config != new_config
    return {
        "scope": "两代过程目录沿革；按 pulse 成员重叠对应，不按雨量或开始时间近似匹配。",
        "old_process_count": comparison["old_process_count"],
        "new_process_count": comparison["new_process_count"],
        "config": {
            "old": old_config,
            "new": new_config,
            "changed": config_changed,
            "note": ("分段配置不同，下列差异可能部分来自配置而非资料修订；"
                     "如需纯粹比较资料变化，请用同一配置重建两代目录。") if config_changed else
                    "两代分段配置一致。",
        },
        "relations": comparison["links"],
        "merged_into_single": comparison["merges"],
        "notes": notes,
        "changed": bool(comparison["merges"] or any(
            link["relation"] != "unchanged" for link in comparison["links"])
            or any(entry["change"] != "unchanged" for entry in notes)),
    }
