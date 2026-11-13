import copy
import json
import unittest

from experiment_family.application import ROOT
from experiment_family.domain import run


class DomainTests(unittest.TestCase):
    def setUp(self):
        self.fixture = json.loads((ROOT / 'fixtures/data.json').read_text())
        self.data = {key: [] for key in self.fixture['tables']}

    def test_fixture_is_deterministic_and_not_mutated(self):
        original = copy.deepcopy(self.fixture['tables'])
        options = {'cutoff': self.fixture['meta']['default_cutoff'], 'group': ''}
        first = run(self.fixture['tables'], options)
        second = run(self.fixture['tables'], options)
        self.assertEqual(first, second)
        self.assertEqual(original, self.fixture['tables'])
        self.assertTrue(first['rows'])
        json.dumps(first, allow_nan=False)

    def sample(self):
        self.data['assignments'] = [dict(unit='u1', arm='control', assigned_at=1, segment='甲'), dict(unit='u2', arm='control', assigned_at=6, segment='甲')]
        self.data['metrics'] = [dict(metric='gain', maturity_days=7, direction='higher', margin=0), dict(metric='refund', maturity_days=30, direction='lower', margin=.02)]
        self.data['outcomes'] = [dict(unit='u1', metric='gain', value=10, known_at=8), dict(unit='u2', metric='gain', value=None, known_at=13), dict(unit='u1', metric='refund', value=1, known_at=31)]
        self.data['looks'] = [8, 20]

    def test_maturity_is_separate_from_zero(self):
        self.sample()
        rows = run(self.data, {'cutoff': 8, 'group': ''})['rows']
        gain, refund = rows
        self.assertEqual((gain['assigned'], gain['mature'], gain['average']), (2, 1, 10))
        self.assertEqual(refund['mature'], 0)
        self.assertIsNone(refund['average'])

    def test_mature_missing_is_not_imputed(self):
        self.sample()
        gain = run(self.data, {'cutoff': 13, 'group': ''})['rows'][0]
        self.assertEqual((gain['observed'], gain['missing'], gain['average']), (1, 1, 10))

    def test_group_filter_keeps_no_false_assignment(self):
        self.sample()
        result = run(self.data, {'cutoff': 20, 'group': '乙'})
        self.assertEqual(result['rows'], [])
        self.assertEqual(result['summary']['分组用户'], 0)
