"""验证跨站过程目录的分段、合并、缺测与配置边界。"""

import copy
import json
import unittest
from pathlib import Path

from response_chain.processes import build_catalog, classify_connection, normalise_config, parse_time

ROOT = Path(__file__).resolve().parents[1]


def load():
    data = json.loads((ROOT / "fixtures/dataset.json").read_text())
    return data["tables"], data["metadata"]


def pulse(identifier, station, start, end, quality="valid", rain=1.0, error=0):
    return {"pulse_id": identifier, "station_id": station, "start": start, "end": end,
            "rain_mm": rain, "time_error_minutes": error, "quality": quality}


class ConfigTests(unittest.TestCase):
    def test_unknown_config_key_is_rejected(self):
        with self.assertRaises(ValueError):
            normalise_config({"dry_gap_minute": 30})

    def test_negative_threshold_is_rejected(self):
        with self.assertRaises(ValueError):
            normalise_config({"dry_gap_minutes": -1})

    def test_boolean_threshold_is_rejected(self):
        with self.assertRaises(ValueError):
            normalise_config({"dry_gap_minutes": True})

    def test_missing_timezone_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_time("2025-06-01T12:00:00")

    def test_offset_times_are_absolute_not_local_clock(self):
        # 同一绝对时刻的两种写法必须归到同一 UTC 时间。
        self.assertEqual(parse_time("2025-06-01T20:00:00+08:00"),
                         parse_time("2025-06-01T12:00:00+00:00"))


class SegmentationTests(unittest.TestCase):
    def test_midnight_crossing_does_not_split_a_station(self):
        tables = {"stations": [{"station_id": "a", "x": 0, "y": 0},
                               {"station_id": "b", "x": 1000, "y": 0}],
                  "pulses": [pulse("p1", "a", "2025-06-01T23:30:00+00:00", "2025-06-02T00:30:00+00:00")],
                  "gaps": [], "notes": []}
        catalog = build_catalog(tables)
        self.assertEqual(1, len(catalog["processes"]))
        process = catalog["processes"][0]
        self.assertEqual("2025-06-01T23:30:00+00:00", process["start"])
        self.assertEqual("2025-06-02T00:30:00+00:00", process["end"])
        self.assertEqual(60.0, process["duration_minutes"])

    def test_adjacent_stations_merge_into_one_process(self):
        tables, _ = load()
        tables["pulses"] = [pulse("a", "s0", "2025-06-01T12:00:00+00:00",
                                  "2025-06-01T13:00:00+00:00"),
                            pulse("b", "s1", "2025-06-01T12:07:00+00:00",
                                  "2025-06-01T13:07:00+00:00")]
        catalog = build_catalog(tables)
        self.assertEqual(1, len(catalog["processes"]))
        self.assertEqual(["s0", "s1"], catalog["processes"][0]["onset_sequence"])

    def test_long_observed_dry_gap_splits_and_proves_end(self):
        tables = {"stations": [{"station_id": "a", "x": 0, "y": 0}],
                  "pulses": [pulse("p1", "a", "2025-06-01T00:00:00+00:00", "2025-06-01T01:00:00+00:00"),
                             pulse("p2", "a", "2025-06-01T05:00:00+00:00", "2025-06-01T06:00:00+00:00")],
                  "gaps": [], "notes": []}
        catalog = build_catalog(tables)
        self.assertEqual(2, len(catalog["processes"]))
        self.assertTrue(catalog["processes"][0]["end_boundary"]["proves_boundary"])
        self.assertEqual("observed_dry_gap", catalog["processes"][0]["end_boundary"]["reason"])

    def test_short_dry_gap_merges(self):
        tables = {"stations": [{"station_id": "a", "x": 0, "y": 0}],
                  "pulses": [pulse("p1", "a", "2025-06-01T00:00:00+00:00", "2025-06-01T01:00:00+00:00"),
                             pulse("p2", "a", "2025-06-01T01:30:00+00:00", "2025-06-01T02:00:00+00:00")],
                  "gaps": [], "notes": []}
        self.assertEqual(1, len(build_catalog(tables)["processes"]))

    def test_dry_gap_threshold_is_user_configured(self):
        tables = {"stations": [{"station_id": "a", "x": 0, "y": 0}],
                  "pulses": [pulse("p1", "a", "2025-06-01T00:00:00+00:00", "2025-06-01T01:00:00+00:00"),
                             pulse("p2", "a", "2025-06-01T01:30:00+00:00", "2025-06-01T02:00:00+00:00")],
                  "gaps": [], "notes": []}
        strict = build_catalog(tables, config={"dry_gap_minutes": 10})
        self.assertEqual(2, len(strict["processes"]))

    def test_clock_offset_tolerance_is_user_configured(self):
        tables = {"stations": [{"station_id": "a", "x": 0, "y": 0},
                               {"station_id": "b", "x": 100, "y": 0}],
                  "pulses": [pulse("p1", "a", "2025-06-01T00:00:00+00:00", "2025-06-01T01:00:00+00:00"),
                             pulse("p2", "b", "2025-06-01T01:20:00+00:00", "2025-06-01T02:00:00+00:00")],
                  "gaps": [], "notes": []}
        # 20 分钟间隔：默认 15 分钟容忍下算作已观测干旱，但因未超过 120 分钟仍合并。
        # 明确验证容忍值本身被使用。
        decision = classify_connection(parse_time("2025-06-01T01:00:00+00:00"),
                                       parse_time("2025-06-01T01:10:00+00:00"),
                                       {"a", "b"}, {}, normalise_config({"clock_offset_minutes": 30}),
                                       allow_clock=True)
        self.assertEqual("clock_offset", decision["reason"])
        self.assertEqual(30.0, decision["thresholds"]["clock_offset_minutes"])


class MissingDataTests(unittest.TestCase):
    def test_missing_within_bridge_is_not_an_ending(self):
        tables = {"stations": [{"station_id": "a", "x": 0, "y": 0}],
                  "pulses": [pulse("p1", "a", "2025-06-01T00:00:00+00:00", "2025-06-01T01:00:00+00:00"),
                             pulse("p2", "a", "2025-06-01T06:00:00+00:00", "2025-06-01T07:00:00+00:00")],
                  "gaps": [{"gap_id": "g1", "station_id": "a",
                            "start": "2025-06-01T01:00:00+00:00",
                            "end": "2025-06-01T06:00:00+00:00"}],
                  "notes": []}
        catalog = build_catalog(tables)
        self.assertEqual(1, len(catalog["processes"]))
        self.assertFalse(catalog["processes"][0]["end_boundary"]["proves_boundary"])

    def test_missing_is_not_proof_of_ending(self):
        tables = {"stations": [{"station_id": "a", "x": 0, "y": 0}],
                  "pulses": [pulse("p1", "a", "2025-06-01T00:00:00+00:00", "2025-06-01T01:00:00+00:00"),
                             pulse("p2", "a", "2025-06-01T12:00:00+00:00", "2025-06-01T13:00:00+00:00")],
                  "gaps": [{"gap_id": "g1", "station_id": "a",
                            "start": "2025-06-01T01:00:00+00:00",
                            "end": "2025-06-01T12:00:00+00:00"}],
                  "notes": []}
        catalog = build_catalog(tables)
        # 缺测超过桥接上限，必须拆开，但结束未获证实。
        self.assertEqual(2, len(catalog["processes"]))
        boundary = catalog["processes"][0]["end_boundary"]
        self.assertEqual("missing_unresolved", boundary["reason"])
        self.assertFalse(boundary["proves_boundary"])

    def test_partial_observed_gap_can_still_prove_end(self):
        tables = {"stations": [{"station_id": "a", "x": 0, "y": 0}],
                  "pulses": [pulse("p1", "a", "2025-06-01T00:00:00+00:00", "2025-06-01T01:00:00+00:00"),
                             pulse("p2", "a", "2025-06-01T07:00:00+00:00", "2025-06-01T08:00:00+00:00")],
                  "gaps": [{"gap_id": "g1", "station_id": "a",
                            "start": "2025-06-01T01:00:00+00:00",
                            "end": "2025-06-01T04:00:00+00:00"}],
                  "notes": []}
        catalog = build_catalog(tables)
        self.assertEqual(2, len(catalog["processes"]))
        boundary = catalog["processes"][0]["end_boundary"]
        # 4:00-7:00 有完整观测且超过阈值，结束成立。
        self.assertEqual("observed_dry_gap", boundary["reason"])
        self.assertTrue(boundary["proves_boundary"])

    def test_withdrawn_observations_are_excluded_not_fatal(self):
        tables, _ = load()
        tables["pulses"][0]["quality"] = "withdrawn"
        catalog = build_catalog(tables)
        excluded = {row["pulse_id"] for row in catalog["diagnostics"]["excluded_pulses"]}
        self.assertIn("p0-0-0", excluded)

    def test_suspect_is_excluded_by_default_and_can_be_included(self):
        tables, _ = load()
        catalog = build_catalog(tables)
        excluded = {row["pulse_id"] for row in catalog["diagnostics"]["excluded_pulses"]}
        self.assertIn("p3-1-0", excluded)
        included = build_catalog(tables, config={"include_suspect": True})
        excluded_included = {row["pulse_id"] for row in included["diagnostics"]["excluded_pulses"]}
        self.assertNotIn("p3-1-0", excluded_included)

    def test_unknown_quality_is_rejected(self):
        tables, _ = load()
        tables["pulses"][0]["quality"] = "maybe"
        with self.assertRaises(ValueError):
            build_catalog(tables)


class OrderingTests(unittest.TestCase):
    def test_ordering_ignores_rainfall(self):
        tables, metadata = load()
        catalog = build_catalog(tables, metadata=metadata)
        starts = [process["start"] for process in catalog["processes"]]
        self.assertEqual(sorted(starts), starts)

    def test_rainfall_does_not_change_process_order(self):
        tables, metadata = load()
        baseline = build_catalog(tables, metadata=metadata)
        changed = copy.deepcopy(tables)
        for row in changed["pulses"]:
            row["rain_mm"] = 100.0 - row["rain_mm"]
        reordered = build_catalog(changed, metadata=metadata)
        self.assertEqual([process["process_id"] for process in baseline["processes"]],
                         [process["process_id"] for process in reordered["processes"]])


class NoteResolutionTests(unittest.TestCase):
    def test_manual_note_resolves_to_single_process(self):
        tables, metadata = load()
        catalog = build_catalog(tables, metadata=metadata)
        note = catalog["notes"][0]
        self.assertEqual("resolved", note["status"])
        self.assertEqual(1, len(note["process_ids"]))
        self.assertEqual(note["process_ids"][0], catalog["processes"][0]["process_id"])

    def test_note_reports_split_when_process_is_split(self):
        tables, metadata = load()
        metadata = copy.deepcopy(metadata)
        # 干旱阈值为 0 时，同站 12:00 与 15:00 的观测分属两个过程。
        metadata["manual_process_members"]["manual-1"] = ["p0-0-0", "p0-0-1"]
        catalog = build_catalog(tables, metadata=metadata,
                                config={"dry_gap_minutes": 0})
        note = catalog["notes"][0]
        self.assertEqual("split", note["status"])
        self.assertGreater(len(note["process_ids"]), 1)
        # 每个被引用的过程都能看到该备注，并知道它还与哪些过程共享。
        holders = [process for process in catalog["processes"]
                   if any(ref["note_id"] == "n1" for ref in process["manual_refs"])]
        self.assertEqual(2, len(holders))

    def test_note_with_retracted_members_is_partial(self):
        tables, metadata = load()
        metadata = copy.deepcopy(metadata)
        metadata["manual_process_members"]["manual-1"] = ["p0-0-0", "retracted-1"]
        catalog = build_catalog(tables, metadata=metadata)
        note = catalog["notes"][0]
        self.assertEqual("partial", note["status"])
        self.assertEqual(["retracted-1"], note["unresolved_pulse_ids"])

    def test_note_without_declared_members_is_unknown(self):
        tables, metadata = load()
        tables["notes"].append({"note_id": "n2", "process_ref": "manual-404", "text": "旧标签"})
        catalog = build_catalog(tables, metadata=metadata)
        statuses = {note["note_id"]: note["status"] for note in catalog["notes"]}
        self.assertEqual("unknown_ref", statuses["n2"])

    def test_malformed_manual_members_are_rejected(self):
        tables, metadata = load()
        broken = copy.deepcopy(metadata)
        broken["manual_process_members"]["manual-1"] = "p0-0-0"
        with self.assertRaises(ValueError):
            build_catalog(tables, metadata=broken)

    def test_note_scoping_does_not_leak_between_generations(self):
        # 同代 metadata 声明成员；换了 metadata 的标签必须重新解析，不沿用旧结论。
        tables, metadata = load()
        other = copy.deepcopy(metadata)
        other["manual_process_members"]["manual-1"] = ["p9-0-0"]
        first = build_catalog(tables, metadata=metadata)
        second = build_catalog(tables, metadata=other)
        self.assertNotEqual(first["notes"][0]["process_ids"], second["notes"][0]["process_ids"])


if __name__ == "__main__":
    unittest.main()
