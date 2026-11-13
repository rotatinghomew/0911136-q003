"""验证资料契约、传输边界与现有摘要产品。"""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from response_chain.analysis import summarize
from response_chain.datasource import GenerationChanged, SourceClient, SourceError, Snapshot, digest
from response_chain.reports import load_report, package_report, save_report
from response_chain.service import DataRepository, load_repository, serve


ROOT = Path(__file__).resolve().parents[1]


class ApplicationTests(unittest.TestCase):
    def setUp(self):
        self.schema = json.loads((ROOT / "response_chain/schema.json").read_text())
        self.data = json.loads((ROOT / "fixtures/dataset.json").read_text())
        self.repository = DataRepository(self.data, self.schema)

    def test_local_http_matches_direct_domain_summary(self):
        with serve(self.repository) as address:
            snapshot = SourceClient(address).read()
        self.assertEqual(self.data["generation"], snapshot.generation)
        self.assertEqual(summarize(self.data["tables"]), summarize(snapshot.tables))
        self.assertEqual(set(self.schema), set(snapshot.evidence))

    def test_stale_generation_is_rejected_by_actual_http(self):
        with serve(self.repository) as address:
            client = SourceClient(address)
            table = next(iter(self.schema))
            with self.assertRaises(GenerationChanged):
                client.request("/tables/" + table + "?generation=obsolete")

    def test_unknown_table_fails_instead_of_becoming_empty(self):
        with serve(self.repository) as address:
            with self.assertRaises(SourceError):
                SourceClient(address).request("/tables/absent?generation=" + self.data["generation"])

    def test_duplicate_primary_key_is_rejected(self):
        data = copy.deepcopy(self.data)
        name = next(name for name, rows in data["tables"].items() if rows)
        data["tables"][name].append(copy.deepcopy(data["tables"][name][0]))
        with self.assertRaises(ValueError):
            DataRepository(data, self.schema)

    def test_missing_required_column_is_rejected(self):
        data = copy.deepcopy(self.data)
        name = next(name for name, rows in data["tables"].items() if rows)
        del data["tables"][name][0][self.schema[name]["columns"][-1]]
        with self.assertRaises(ValueError):
            DataRepository(data, self.schema)

    def test_report_roundtrip_and_corruption_detection(self):
        snapshot = Snapshot(self.data["generation"], self.data["tables"], {}, {})
        report = package_report(snapshot, summarize(snapshot.tables))
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "report.json"
            save_report(target, report)
            self.assertEqual(report, load_report(target))
            changed = json.loads(target.read_text())
            changed["generation"] = "changed"
            target.write_text(json.dumps(changed))
            with self.assertRaises(ValueError):
                load_report(target)

    def test_generation_replacement_does_not_mutate_prior_manifest(self):
        manifest = self.repository.manifest()
        data = copy.deepcopy(self.data)
        data["generation"] += "-revised"
        self.repository.replace(data)
        self.assertNotEqual(manifest["generation"], self.repository.manifest()["generation"])
        self.assertEqual(self.data["generation"], manifest["generation"])

    def test_digest_is_independent_of_object_key_order(self):
        self.assertEqual(digest({"a": 1, "b": 2}), digest({"b": 2, "a": 1}))

    def test_manifest_content_mismatch_is_not_accepted(self):
        repository = self.repository
        original = repository.manifest
        def bad_manifest():
            manifest = original()
            name = next(iter(manifest["tables"]))
            manifest["tables"][name]["sha256"] = "0" * 64
            return manifest
        repository.manifest = bad_manifest
        with serve(repository) as address:
            with self.assertRaises(SourceError):
                SourceClient(address).read()

    def test_same_generation_content_cannot_be_overwritten(self):
        changed = copy.deepcopy(self.data)
        changed["metadata"]["new_information"] = True
        with self.assertRaises(ValueError):
            self.repository.replace(changed)

    def test_swap_after_manifest_is_rejected_not_blended(self):
        repository = self.repository
        original = repository.manifest
        def swapping_manifest():
            manifest = original()
            changed = copy.deepcopy(self.data)
            changed["generation"] += "-next"
            repository.replace(changed)
            return manifest
        repository.manifest = swapping_manifest
        with serve(repository) as address:
            with self.assertRaises(GenerationChanged):
                SourceClient(address).read()
