"""验证形态检索的可解释依据与不可比较维度，以及跨代沿革。"""

import copy
import json
import unittest
from pathlib import Path

from response_chain.lineage import lineage_report
from response_chain.processes import build_catalog
from response_chain.search import search_similar

ROOT = Path(__file__).resolve().parents[1]


def load():
    data = json.loads((ROOT / "fixtures/dataset.json").read_text())
    return data["tables"], data["metadata"]


def catalog_for(tables, metadata=None, config=None):
    return build_catalog(tables, metadata=metadata or {}, config=config)


class SearchTests(unittest.TestCase):
    def setUp(self):
        tables, metadata = load()
        self.catalog = build_catalog(tables, metadata=metadata)
        self.target = self.catalog["processes"][0]["process_id"]

    def test_ranking_uses_shape_not_rainfall(self):
        result = search_similar(self.catalog, self.target, limit=3)
        self.assertEqual("形态相似度（持续时间、移动次序、空间覆盖、起止形态），不按雨量排序",
                         result["ranking_basis"])
        candidates = [item["process_id"] for item in result["results"]]
        self.assertNotIn(self.target, candidates)
        # 雨量不同但形态相同的候选不应被雨量差异拉开。
        scores = [item["similarity"] for item in result["results"]]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_single_station_process_reports_movement_not_comparable(self):
        tables, _ = load()
        only_one = {"stations": [{"station_id": "a", "x": 0, "y": 0}],
                    "pulses": [{"pulse_id": "p1", "station_id": "a",
                                "start": "2025-06-01T00:00:00+00:00",
                                "end": "2025-06-01T01:00:00+00:00",
                                "rain_mm": 1.0, "time_error_minutes": 0, "quality": "valid"}],
                    "gaps": [], "notes": []}
        catalog = catalog_for(only_one)
        reference = catalog["processes"][0]["process_id"]
        result = search_similar(catalog, reference)
        # 只有一个过程，没有可比候选；用两站资料验证单站目标的不可比较维度。
        two = copy.deepcopy(only_one)
        two["pulses"].append({"pulse_id": "p2", "station_id": "a",
                              "start": "2025-06-02T00:00:00+00:00",
                              "end": "2025-06-02T01:00:00+00:00",
                              "rain_mm": 1.0, "time_error_minutes": 0, "quality": "valid"})
        catalog_two = catalog_for(two)
        outcome = search_similar(catalog_two, catalog_two["processes"][0]["process_id"])
        item = outcome["results"][0]
        self.assertIn("movement", item["explanation"]["not_comparable"])
        self.assertFalse(item["dimensions"]["movement"]["comparable"])

    def test_coverage_difference_is_disclosed(self):
        tables, _ = load()
        mixed = copy.deepcopy(tables)
        # 加入同形态但只覆盖两站的历史过程。
        mixed["pulses"].extend([
            {"pulse_id": "sparse-0", "station_id": "s0",
             "start": "2025-08-01T12:00:00+00:00", "end": "2025-08-01T16:21:00+00:00",
             "rain_mm": 1.0, "time_error_minutes": 0, "quality": "valid"},
            {"pulse_id": "sparse-2", "station_id": "s2",
             "start": "2025-08-01T12:14:00+00:00", "end": "2025-08-01T16:21:00+00:00",
             "rain_mm": 1.0, "time_error_minutes": 0, "quality": "valid"},
        ])
        catalog = build_catalog(mixed)
        sparse_id = next(process["process_id"] for process in catalog["processes"]
                         if process["pulse_ids"] == ["sparse-0", "sparse-2"])
        result = search_similar(catalog, self.target, limit=len(catalog["processes"]))
        item = next(candidate for candidate in result["results"]
                    if candidate["process_id"] == sparse_id)
        spatial = item["dimensions"]["spatial_coverage"]
        self.assertTrue(spatial["coverage_gap"])
        self.assertEqual(["s0", "s2"], spatial["metrics"]["shared_stations"])
        self.assertEqual(["s1", "s3"], spatial["metrics"]["only_target"])
        self.assertIn("仅目标有 s1,s3", spatial["basis"])

    def test_each_score_has_a_basis_and_dimensions(self):
        result = search_similar(self.catalog, self.target, limit=2)
        for candidate in result["results"]:
            self.assertEqual(set(("duration", "movement", "spatial_coverage", "timing_profile")),
                             set(candidate["dimensions"]))
            for dimension in candidate["dimensions"].values():
                self.assertIn("basis", dimension)
                self.assertIn("comparable", dimension)
            self.assertTrue(candidate["explanation"]["details"])

    def test_label_reference_can_locates_note(self):
        result = search_similar(self.catalog, "manual-1", limit=1)
        self.assertEqual(self.target, result["target"]["process_id"])

    def test_ambiguous_label_is_rejected(self):
        tables, metadata = load()
        metadata = copy.deepcopy(metadata)
        metadata["manual_process_members"]["manual-1"] = ["p0-0-0", "p0-0-1"]
        catalog = build_catalog(tables, metadata=metadata, config={"dry_gap_minutes": 0})
        with self.assertRaises(ValueError):
            search_similar(catalog, "manual-1")

    def test_unknown_reference_is_rejected(self):
        with self.assertRaises(ValueError):
            search_similar(self.catalog, "no-such-process")

    def test_weights_are_validated_and_applied(self):
        with self.assertRaises(ValueError):
            search_similar(self.catalog, self.target, weights={"rainfall": 1})
        weighted = search_similar(self.catalog, self.target, limit=1,
                                  weights={"duration": 0, "movement": 1,
                                           "spatial_coverage": 0, "timing_profile": 0})
        self.assertEqual(["movement"],
                         weighted["results"][0]["explanation"]["used_dimensions"])
        # 只按移动维度加权时，总分必须等于该维得分。
        self.assertEqual(weighted["results"][0]["dimensions"]["movement"]["score"],
                         weighted["results"][0]["explanation"]["score"])

    def test_same_geometry_filter_keeps_only_identical_station_sets(self):
        tables, _ = load()
        mixed = copy.deepcopy(tables)
        mixed["pulses"].extend([
            {"pulse_id": "sparse-0", "station_id": "s0",
             "start": "2025-08-01T12:00:00+00:00", "end": "2025-08-01T16:21:00+00:00",
             "rain_mm": 1.0, "time_error_minutes": 0, "quality": "valid"},
            {"pulse_id": "sparse-2", "station_id": "s2",
             "start": "2025-08-01T12:14:00+00:00", "end": "2025-08-01T16:21:00+00:00",
             "rain_mm": 1.0, "time_error_minutes": 0, "quality": "valid"},
        ])
        catalog = build_catalog(mixed)
        filtered = search_similar(catalog, self.target, same_geometry_only=True)
        self.assertTrue(filtered["results"])
        self.assertTrue(all(set(candidate["station_ids"]) == {"s0", "s1", "s2", "s3"}
                            for candidate in filtered["results"]))
        unfiltered = search_similar(catalog, self.target)
        self.assertGreater(unfiltered["total_candidates"], filtered["total_candidates"])


    def test_missing_data_is_stated_as_caveat_not_comparability_claim(self):
        tables = {"stations": [{"station_id": "a", "x": 0, "y": 0}],
                  "pulses": [
                      {"pulse_id": "p1", "station_id": "a",
                       "start": "2025-06-01T00:00:00+00:00", "end": "2025-06-01T01:00:00+00:00",
                       "rain_mm": 1.0, "time_error_minutes": 0, "quality": "valid"},
                      {"pulse_id": "p2", "station_id": "a",
                       "start": "2025-06-02T00:00:00+00:00", "end": "2025-06-02T01:00:00+00:00",
                       "rain_mm": 1.0, "time_error_minutes": 0, "quality": "valid"}],
                  "gaps": [{"gap_id": "g1", "station_id": "a",
                            "start": "2025-06-01T01:00:00+00:00",
                            "end": "2025-06-01T23:00:00+00:00"}],
                  "notes": []}
        catalog = catalog_for(tables)
        result = search_similar(catalog, catalog["processes"][0]["process_id"])
        self.assertTrue(result["results"][0]["explanation"]["caveats"])

    def test_lineage_discloses_config_mismatch(self):
        tables, metadata = load()
        old = build_catalog(tables, metadata=metadata)
        new = build_catalog(tables, metadata=metadata, config={"dry_gap_minutes": 30})
        report = lineage_report(old, new)
        self.assertTrue(report["config"]["changed"])
        self.assertIn("配置", report["config"]["note"])

    def test_lineage_reports_unchanged_config(self):
        tables, metadata = load()
        old = build_catalog(tables, metadata=metadata)
        new = build_catalog(copy.deepcopy(tables), metadata=metadata)
        report = lineage_report(old, new)
        self.assertFalse(report["config"]["changed"])


class LineageTests(unittest.TestCase):
    def setUp(self):
        tables, metadata = load()
        self.tables = tables
        self.metadata = metadata
        self.old = build_catalog(tables, metadata=metadata)

    def test_unchanged_revision_has_no_changes(self):
        new = build_catalog(self.tables, metadata=self.metadata)
        report = lineage_report(self.old, new)
        self.assertFalse(report["changed"])
        self.assertTrue(all(link["relation"] == "unchanged" for link in report["relations"]))

    def test_withdrawn_pulse_reports_revised(self):
        tables = copy.deepcopy(self.tables)
        tables["pulses"][0]["quality"] = "withdrawn"
        new = build_catalog(tables, metadata=self.metadata)
        report = lineage_report(self.old, new)
        changed = [link for link in report["relations"]
                   if link["old_process_id"] == self.old["processes"][0]["process_id"]]
        self.assertEqual(1, len(changed))
        self.assertEqual("revised", changed[0]["relation"])
        self.assertEqual(["p0-0-0"], changed[0]["matches"][0]["old_only_pulse_ids"])

    def test_timestamp_correction_merging_processes_is_reported(self):
        stations = [{"station_id": "a", "x": 0, "y": 0}]
        old_tables = {"stations": stations, "gaps": [], "notes": [],
                      "pulses": [
                          {"pulse_id": "p1", "station_id": "a",
                           "start": "2025-06-01T00:00:00+00:00",
                           "end": "2025-06-01T01:00:00+00:00",
                           "rain_mm": 1.0, "time_error_minutes": 0, "quality": "valid"},
                          {"pulse_id": "p2", "station_id": "a",
                           "start": "2025-06-01T04:00:00+00:00",
                           "end": "2025-06-01T05:00:00+00:00",
                           "rain_mm": 1.0, "time_error_minutes": 0, "quality": "valid"}]}
        old = build_catalog(old_tables)
        self.assertEqual(2, len(old["processes"]))
        # 供数端修正 p2 的时间戳，两场旧过程合并为一场。
        new_tables = copy.deepcopy(old_tables)
        new_tables["pulses"][1]["start"] = "2025-06-01T01:30:00+00:00"
        new_tables["pulses"][1]["end"] = "2025-06-01T02:30:00+00:00"
        new = build_catalog(new_tables)
        report = lineage_report(old, new)
        self.assertEqual(1, len(new["processes"]))
        self.assertTrue(report["changed"])
        self.assertEqual(1, len(report["merged_into_single"]))
        self.assertEqual(["p1", "p2"], report["merged_into_single"][0]["old_process_ids"] and
                         new["processes"][0]["pulse_ids"])

    def test_split_process_is_reported_as_split_relation(self):
        tables = copy.deepcopy(self.tables)
        # 撤回同一站的第二段观测，使首场过程被拆开。
        for row in tables["pulses"]:
            if row["pulse_id"] in ("p0-0-1", "p0-1-1", "p0-2-1", "p0-3-1"):
                row["quality"] = "withdrawn"
        new = build_catalog(tables, metadata=self.metadata)
        report = lineage_report(self.old, new)
        old_first = self.old["processes"][0]["process_id"]
        link = next(item for item in report["relations"]
                    if item["old_process_id"] == old_first)
        self.assertIn(link["relation"], {"split", "revised"})

    def test_note_trail_reports_retracted_reference(self):
        tables = copy.deepcopy(self.tables)
        tables["pulses"][0]["quality"] = "withdrawn"
        new = build_catalog(tables, metadata=self.metadata)
        report = lineage_report(self.old, new)
        entry = next(item for item in report["notes"] if item["process_ref"] == "manual-1")
        self.assertIn(entry["change"], {"references_retracted", "status_changed"})

    def test_lineage_does_not_mutate_old_catalog(self):
        tables = copy.deepcopy(self.tables)
        tables["pulses"][0]["quality"] = "withdrawn"
        before = json.dumps(self.old, sort_keys=True)
        lineage_report(self.old, build_catalog(tables, metadata=self.metadata))
        self.assertEqual(before, json.dumps(self.old, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
