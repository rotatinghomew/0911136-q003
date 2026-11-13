"""验证现有领域摘要的业务边界。"""
import copy
import json
import unittest
from pathlib import Path

from response_chain.analysis import station_intervals


class DomainTests(unittest.TestCase):
    def setUp(self):
        self.tables = json.loads((Path(__file__).resolve().parents[1]/'fixtures/dataset.json').read_text())['tables']

    def test_unknown_station_is_not_silently_accepted(self):
        tables = copy.deepcopy(self.tables)
        tables['pulses'][0]['station_id'] = 'absent'
        with self.assertRaises(ValueError):
            station_intervals(tables)

    def test_reversed_interval_fails(self):
        tables = copy.deepcopy(self.tables)
        tables['pulses'][0]['end'] = tables['pulses'][0]['start']
        with self.assertRaises(ValueError):
            station_intervals(tables)

    def test_suspect_pulse_does_not_add_wet_minutes(self):
        tables = copy.deepcopy(self.tables)
        tables['pulses'] = [dict(tables['pulses'][0],quality='suspect')]
        self.assertTrue(all(row['wet_minutes'] == 0 for row in station_intervals(tables).values()))

    def test_missing_interval_does_not_count_as_wet(self):
        tables = copy.deepcopy(self.tables)
        tables['pulses'] = []
        result = station_intervals(tables)
        self.assertEqual(1,result['s1']['gap_count'])
        self.assertEqual(0,result['s1']['wet_minutes'])
