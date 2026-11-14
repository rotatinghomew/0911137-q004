import json
from pathlib import Path
import tempfile
from urllib.error import HTTPError
import unittest

from experiment_family.application import Application, ROOT, parse_cutoff
from experiment_family.confirmatory import (
    cumulative_spending,
    evaluate,
    readiness,
    spending_table,
)
from experiment_family.storage import AnalysisStore, read_dataset


def base_options(**overrides):
    options = {
        'cutoff': 60,
        'group': '',
        'control': 'control',
        'treatments': ['basic', 'plus'],
        'metrics': ['revenue', 'refund'],
        'roles': {'revenue': 'benefit', 'refund': 'guardrail'},
        'alpha': 0.05,
        'max_looks': 4,
        'min_observed': 2,
        'max_missing_rate': 0.2,
    }
    options.update(overrides)
    return options


class SpendingTests(unittest.TestCase):
    def test_spending_is_monotone_and_exhausts_alpha(self):
        table = spending_table(0.05, 4)
        self.assertEqual([row['look'] for row in table], [1, 2, 3, 4])
        self.assertTrue(all(row['incremental_alpha'] > 0 for row in table))
        self.assertAlmostEqual(sum(row['incremental_alpha'] for row in table), 0.05, places=12)
        self.assertAlmostEqual(table[-1]['cumulative_alpha'], 0.05, places=12)
        self.assertEqual(table[-1]['information_fraction'], 1)

    def test_obrien_fleming_is_conservative_early(self):
        table = spending_table(0.05, 4)
        self.assertLess(table[0]['cumulative_alpha'], 0.05 / 4)
        self.assertGreater(table[2]['incremental_alpha'], table[0]['incremental_alpha'])
        self.assertEqual(cumulative_spending(0.05, 0), 0)

    def test_bad_parameters_rejected(self):
        for alpha, looks in [(0, 3), (1, 3), (0.05, 0), (0.05, 2.5), (0.05, True)]:
            with self.assertRaises(ValueError):
                spending_table(alpha, looks)


class ConfirmatoryFixtureTests(unittest.TestCase):
    def setUp(self):
        self.data, self.digest = read_dataset(ROOT / 'fixtures/data.json')
        self.tables = self.data['tables']
        self.data_digest = self.digest
        self.design = {
            'control': 'control',
            'treatments': ['basic', 'plus'],
            'group': '',
            'metrics': [
                {'metric': 'revenue', 'name': '收益', 'direction': 'higher', 'margin': 0, 'maturity_days': 7, 'role': 'benefit'},
                {'metric': 'refund', 'name': '退款率', 'direction': 'lower', 'margin': 0.02, 'maturity_days': 30, 'role': 'guardrail'},
            ],
            'alpha': 0.05,
            'max_looks': 4,
            'min_observed': 2,
            'max_missing_rate': 0.2,
            'decision_rule': '规则',
            'planned_at_cutoff': 20,
            'source': {'digest': self.digest, 'metrics': ['refund', 'revenue'], 'arms': ['basic', 'control', 'plus']},
        }

    def result(self, cutoff, look=3):
        return evaluate(self.tables, self.design, cutoff, look, self.digest)

    def test_unmatured_refund_is_blocked_not_zero(self):
        result = self.result(20, 1)
        refund = [row for row in result['comparisons'] if row['metric'] == 'refund']
        self.assertTrue(refund)
        for row in refund:
            self.assertIsNone(row['effect'])
            self.assertIsNone(row['pass'])
            self.assertEqual(row['verdict'], None)
            self.assertEqual(row['treatment']['observed'], 0)
            self.assertIsNone(row['treatment']['average'])
        self.assertEqual(result['decision']['decision'], 'wait')
        self.assertIn('尚未成熟', str(result['decision']['arms']))

    def test_mature_missing_is_not_zero_and_blocks_unreliable(self):
        strict = dict(self.design, max_missing_rate=0.0)
        result = evaluate(self.tables, strict, 120, 4, self.digest)
        for row in result['comparisons']:
            self.assertEqual(row['status'], 'blocked')
            self.assertIsNone(row['effect'])
        summaries = result['summaries']
        for summary in summaries:
            self.assertEqual(summary['missing'], summary['mature'] - summary['observed'])
            self.assertGreater(summary['missing'], 0)
            self.assertIsNone(summary['missing_rate'] if summary['mature'] == 0 else None)

    def test_shared_control_significance_uses_adjusted_level(self):
        result = self.result(60, 3)
        budget = result['error_budget']
        self.assertEqual(budget['comparisons'], 4)
        self.assertAlmostEqual(budget['alpha_per_comparison'], budget['incremental_alpha'] / 4, places=15)
        for row in result['comparisons']:
            self.assertAlmostEqual(row['alpha_per_comparison'], budget['alpha_per_comparison'])
            self.assertAlmostEqual(row['critical_value'], budget['critical_value'])
            if row['z'] is not None:
                expected = 1 - __import__('statistics').NormalDist().cdf(row['z'])
                self.assertAlmostEqual(row['p_value'], expected, places=12)
        self.assertTrue(budget['checks']['equals_alpha'])
        self.assertGreater(budget['critical_value'], 2.0)
        # 共用对照：每个方案都与同一对照比较，未调整误差不能当作确认性依据。
        self.assertEqual(len({row['arm'] for row in result['comparisons']}), 2)

    def test_guardrail_direction_is_oriented(self):
        result = self.result(120, 4)
        for row in result['comparisons']:
            if row['metric'] == 'refund' and row['z'] is not None:
                sign = 1 if row['direction'] == 'higher' else -1
                self.assertAlmostEqual(row['oriented_effect'], sign * (row['effect'] - row['margin']))
                self.assertAlmostEqual(row['z'], row['oriented_effect'] / row['standard_error'])

    def test_benefit_and_guardrail_must_both_pass_for_recommendation(self):
        result = self.result(120, 4)
        for arm in result['decision']['arms']:
            rows = [row for row in result['comparisons'] if row['arm'] == arm['arm']]
            if arm['status'] == 'recommendable':
                self.assertTrue(all(row['verdict'] == 'pass' for row in rows))
            else:
                self.assertTrue(any(row['verdict'] != 'pass' for row in rows))

    def test_strength_of_evidence_is_reported(self):
        result = self.result(120, 4)
        self.assertTrue(result['decision']['fully_accrued'])
        self.assertEqual(result['decision']['looks_remaining'], 0)
        self.assertIn(result['decision']['decision'], {'wait', 'inconclusive', 'none', 'recommend', 'ambiguous'})
        if result['decision']['decision'] == 'inconclusive':
            self.assertIn('证据不足', result['decision']['explanation'])

    def test_readiness_does_not_leak_effects(self):
        report = readiness(self.tables, self.design, 40)
        self.assertNotIn('comparisons', report)
        self.assertNotIn('decision', report)
        for row in report['readiness']:
            self.assertEqual(set(row), {'arm', 'metric', 'assigned', 'mature', 'observed', 'missing', 'null', 'pending', 'missing_rate', 'status', 'status_label'})


class DesignWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name)
        self.store = AnalysisStore(self.path / 'state.sqlite3')

    def tearDown(self):
        self.directory.cleanup()

    def create(self, app, **overrides):
        return app.create_design({'title': '评审会', 'preregistered': True, 'options': base_options(**overrides)})

    def test_look_budget_is_bound_to_design(self):
        app = Application(ROOT / 'fixtures/data.json', self.path / 'a.sqlite3', ROOT / 'web')
        design = self.create(app)
        self.assertTrue(design['confirmatory'])
        self.assertEqual(design['looks_remaining'], 4)
        app.look(design['id'], {'cutoff': 30})
        app.look(design['id'], {'cutoff': 60})
        details = app.store.get_design(design['id'])
        self.assertEqual(details['looks_used'], 2)
        self.assertEqual(details['looks_remaining'], 2)
        with self.assertRaises(ValueError):
            app.look(design['id'], {'cutoff': 40})
        for cutoff in (80, 100):
            app.look(design['id'], {'cutoff': cutoff})
        with self.assertRaises(ValueError) as caught:
            app.look(design['id'], {'cutoff': 110})
        self.assertIn('用尽', str(caught.exception))

    def test_same_cutoff_same_digest_is_reused_not_recharged(self):
        app = Application(ROOT / 'fixtures/data.json', self.path / 'b.sqlite3', ROOT / 'web')
        design = self.create(app)
        first = app.look(design['id'], {'cutoff': 60})
        again = app.look(design['id'], {'cutoff': 60})
        self.assertFalse(first['reused'])
        self.assertTrue(again['reused'])
        self.assertEqual(first['look_id'], again['look_id'])
        self.assertEqual(app.store.get_design(design['id'])['looks_used'], 1)

    def test_updated_dataset_does_not_reopen_used_cutoff(self):
        app = Application(ROOT / 'fixtures/data.json', self.path / 'c.sqlite3', ROOT / 'web')
        design = self.create(app)
        frozen = app.look(design['id'], {'cutoff': 60})
        changed = self.path / 'data.json'
        payload = json.loads((ROOT / 'fixtures/data.json').read_text())
        payload['tables']['looks'].append(61)
        changed.write_text(json.dumps(payload))
        other = Application(changed, self.path / 'c.sqlite3', ROOT / 'web')
        with self.assertRaises(ValueError):
            other.look(design['id'], {'cutoff': 60})
        later = other.look(design['id'], {'cutoff': 70})
        self.assertEqual(later['look_index'], 2)
        self.assertEqual(other.store.get_design(design['id'])['looks_used'], 2)
        stored = other.store.get_look(design['id'], 1)
        self.assertEqual(stored['result'], frozen['result'])
        self.assertEqual(other.store.get_design(design['id'])['digest'], design['digest'])
        self.assertNotEqual(later['digest'], frozen['digest'])

    def test_after_look_changes_are_exploratory_and_quota_not_renewed(self):
        app = Application(ROOT / 'fixtures/data.json', self.path / 'd.sqlite3', ROOT / 'web')
        design = self.create(app, group='新客')
        app.look(design['id'], {'cutoff': 60})
        with self.assertRaises(ValueError):
            self.create(app, group='老客')
        exploratory = app.create_design({'title': '改人群', 'preregistered': False, 'options': base_options(group='老客')})
        self.assertFalse(exploratory['confirmatory'])
        self.assertEqual(exploratory['looks_remaining'], 0)
        with self.assertRaises(ValueError):
            app.look(exploratory['id'], {'cutoff': 60})
        original = app.store.get_design(design['id'])
        self.assertEqual(original['looks_used'], 1)
        self.assertTrue(original['can_look'])

    def test_concurrent_looks_never_break_cutoff_order(self):
        import threading
        app = Application(ROOT / 'fixtures/data.json', self.path / 'g.sqlite3', ROOT / 'web')
        design = self.create(app, max_looks=3)
        outcomes = []

        def open_at(cutoff):
            try:
                outcomes.append(('ok', cutoff, app.look(design['id'], {'cutoff': cutoff})['look_index']))
            except ValueError:
                outcomes.append(('err', cutoff, None))

        threads = [threading.Thread(target=open_at, args=(cutoff,)) for cutoff in (50, 60, 70)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        details = app.store.get_design(design['id'])
        cutoffs = [look['cutoff'] for look in details['looks']]
        self.assertEqual(cutoffs, sorted(cutoffs))
        self.assertEqual(details['looks_used'], sum(1 for item in outcomes if item[0] == 'ok'))
        self.assertEqual(len({item[2] for item in outcomes if item[0] == 'ok'}), details['looks_used'])
        self.assertEqual(details['looks_used'], len(details['looks']))

    def test_unopened_design_can_be_superseded_without_double_spending(self):
        app = Application(ROOT / 'fixtures/data.json', self.path / 'e.sqlite3', ROOT / 'web')
        first = self.create(app, group='新客')
        changed = self.create(app, group='老客')
        self.assertTrue(changed['confirmatory'])
        details = app.store.get_design(first['id'])
        self.assertFalse(details['can_look'])
        self.assertEqual(details['superseded_by'], changed['id'])
        with self.assertRaises(ValueError):
            app.look(first['id'], {'cutoff': 60})
        app.look(changed['id'], {'cutoff': 60})
        self.assertEqual(app.store.get_design(changed['id'])['looks_used'], 1)


class ConfirmatoryApiTests(unittest.TestCase):
    def setUp(self):
        import threading
        from urllib.request import Request, urlopen
        from experiment_family.application import server

        self.Request = Request
        self.urlopen = urlopen
        self.directory = tempfile.TemporaryDirectory()
        self.application = Application(ROOT / 'fixtures/data.json', Path(self.directory.name) / 'state.sqlite3', ROOT / 'web')
        self.httpd = server(self.application)
        self.worker = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.worker.start()
        self.base = f'http://127.0.0.1:{self.httpd.server_port}'

    def tearDown(self):
        self.httpd.shutdown()
        self.worker.join()
        self.httpd.server_close()
        self.directory.cleanup()

    def get(self, path):
        with self.urlopen(self.base + path) as response:
            return json.load(response)

    def post(self, path, payload):
        request = self.Request(self.base + path, data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
        with self.urlopen(request) as response:
            return response.status, json.load(response)

    def test_catalog_exposes_metrics_and_rule(self):
        catalog = self.get('/api/catalog')
        self.assertEqual({item['metric'] for item in catalog['metrics']}, {'revenue', 'refund'})
        self.assertEqual(catalog['arms'], ['basic', 'control', 'plus'])
        self.assertIn('收益', catalog['decision_rule'])

    def test_end_to_end_traceable_result(self):
        status, design = self.post('/api/designs', {'title': '确认性', 'preregistered': True, 'options': base_options()})
        self.assertEqual(status, 201)
        self.assertEqual(design['confirmatory'], True)
        status, look = self.post(f"/api/designs/{design['id']}/looks", {'cutoff': 40})
        self.assertEqual(status, 201)
        result = look['result']
        self.assertTrue(result['confirmatory'])
        self.assertEqual(result['trace']['design']['control'], 'control')
        self.assertEqual(result['trace']['digest'], look['digest'])
        self.assertEqual(result['trace']['cutoff'], 40)
        self.assertEqual(look['look_index'], 1)
        # 复核可追回：页面拿到的记录编号可重新读取，结果与当时一致。
        stored = self.get(f"/api/designs/{design['id']}/looks/1")
        self.assertEqual(stored['result'], result)
        schedule = self.get(f"/api/designs/{design['id']}/schedule")
        self.assertEqual(len(schedule['schedule']), 4)
        self.assertAlmostEqual(sum(row['incremental_alpha'] for row in schedule['schedule']), 0.05, places=12)

    def test_preview_consumes_nothing(self):
        _, design = self.post('/api/designs', {'title': '预备', 'preregistered': True, 'options': base_options()})
        preview = self.get(f"/api/designs/{design['id']}/preview?cutoff=40")
        self.assertFalse(preview['confirmatory'])
        self.assertNotIn('comparisons', preview)
        self.assertEqual(self.get(f"/api/designs/{design['id']}")['looks_used'], 0)
        self.assertEqual(preview['look_index_if_opened'], 1)

    def test_bad_design_requests_rejected(self):
        for payload in [
            {'title': '', 'options': base_options()},
            {'title': 'x', 'options': base_options(control='missing')},
            {'title': 'x', 'options': base_options(treatments=[])},
            {'title': 'x', 'options': base_options(treatments=['control'])},
            {'title': 'x', 'options': base_options(metrics=[])},
            {'title': 'x', 'options': base_options(metrics=['unknown'])},
            {'title': 'x', 'options': base_options(alpha=0)},
            {'title': 'x', 'options': base_options(alpha=1)},
            {'title': 'x', 'options': base_options(max_looks=0)},
            {'title': 'x', 'options': base_options(max_looks=100)},
            {'title': 'x', 'options': base_options(min_observed=1)},
            {'title': 'x', 'options': base_options(max_missing_rate=1)},
            {'title': 'x', 'options': base_options(group='missing')},
            {'title': 'x', 'options': base_options(cutoff=1.5)},
            {'title': 'x', 'options': base_options(cutoff=999)},
            {'title': 'x', 'options': base_options(extra=1)},
            {'title': 'x', 'preregistered': 'yes', 'options': base_options()},
        ]:
            with self.assertRaises(HTTPError) as caught:
                self.post('/api/designs', payload)
            self.assertEqual(caught.exception.code, 400, payload)
        self.assertEqual(self.get('/api/designs'), [])

    def test_exploratory_design_cannot_open_results(self):
        _, design = self.post('/api/designs', {'title': '探索', 'preregistered': False, 'options': base_options()})
        self.assertFalse(design['confirmatory'])
        with self.assertRaises(HTTPError) as caught:
            self.post(f"/api/designs/{design['id']}/looks", {'cutoff': 60})
        self.assertEqual(caught.exception.code, 400)

    def test_unknown_design_and_look_are_not_found(self):
        for path in ['/api/designs/missing', '/api/designs/missing/schedule', '/api/designs/missing/looks/1']:
            with self.assertRaises(HTTPError) as caught:
                self.get(path)
            self.assertEqual(caught.exception.code, 404)
        with self.assertRaises(HTTPError) as caught:
            self.post('/api/designs/missing/looks', {'cutoff': 60})
        self.assertEqual(caught.exception.code, 404)


if __name__ == '__main__':
    unittest.main()
