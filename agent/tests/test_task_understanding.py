"""Task interpretation is revisable model state with server-owned context identity."""
import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agent_kit import task_understanding as understanding
from agent_kit.case_record import CaseError, CaseRecord
from agent_kit.progress import checkpoint_steering, new_run
from agent_kit.tools import update_task_understanding, submit_memo
from tests import support


class TaskUnderstandingTest(unittest.TestCase):
    def setUp(self):
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        self.original = 'Did the agency miss its notice deadline?'
        self.run_id = new_run(self.root, instructions='i', objective=self.original, verification=False)
        self.case = CaseRecord(self.root, self.run_id)
        self.args = dict(actor='Agency', event='No notice reported',
                         interpretation='Government action deadline, not an individual filing deadline',
                         intended_outcome='Explain the general rule and identify missing applicability facts',
                         success_criteria=['Cite governing rule', 'Separate rule from application'],
                         assumptions=['No notice has yet been found'],
                         alternatives=[{'interpretation': 'Individual missed re-registration', 'material': True,
                                        'next_action': 'Ask which deadline the user means'}],
                         revision_reason='Initial task assessment')

    def test_original_context_and_version_are_server_owned(self):
        result = update_task_understanding.run(SimpleNamespace(case=self.case), self.args)
        record = result['assessment']
        self.assertEqual(self.original, record['original_request'])
        self.assertEqual('model', record['assessed_by'])
        self.assertEqual(1, record['version'])
        self.assertFalse(result['stale'])
        self.assertIn('not user confirmation', result['label'])
        self.assertEqual(64, len(record['context_version']))
        self.assertEqual(self.case.data['task_understanding'], CaseRecord(self.root, self.run_id).data['task_understanding'])
        for key in ('original_request', 'context_version', 'stale', 'version', 'assessed_by'):
            with self.assertRaises(CaseError):
                understanding.update(self.case, dict(self.args, **{key: 'model override'}))

    def test_invalid_records_never_partially_mutate(self):
        invalids = [False, [], {}, dict(self.args, actor=''), dict(self.args, event=None),
                    dict(self.args, interpretation='x' * 1201), dict(self.args, success_criteria=[]),
                    dict(self.args, success_criteria=['x'] * 7), dict(self.args, assumptions=['x'] * 9),
                    dict(self.args, assumptions=[False]), dict(self.args, alternatives=[{}]),
                    dict(self.args, alternatives=[dict(self.args['alternatives'][0], material='false')]),
                    dict(self.args, alternatives=[dict(self.args['alternatives'][0], hidden=True)]),
                    dict(self.args, alternatives=self.args['alternatives'] * 7)]
        before = copy.deepcopy(self.case.data)
        for args in invalids:
            with self.subTest(args=args):
                with self.assertRaises(CaseError):
                    understanding.update(self.case, args)
                self.assertEqual(before, self.case.data)

    def test_new_steering_marks_stale_once_and_revision_refreshes(self):
        understanding.update(self.case, self.args)
        old = copy.deepcopy(self.case.data['task_understanding'])
        checkpoint_steering(self.root, self.run_id, [{'role': 'user', 'content': 'Clarification'}], ['QUEUE:first'])
        self.assertTrue(understanding.readout(self.case)['stale'])
        with patch.object(self.case, '_save', wraps=self.case._save) as save:
            self.assertTrue(understanding.refresh(self.case)['stale'])
            understanding.refresh(self.case)
            self.assertEqual(1, save.call_count)
        self.assertEqual(old['interpretation'], self.case.data['task_understanding']['interpretation'])
        updated = understanding.update(self.case, dict(self.args, revision_reason='New steering clarified actor'))
        self.assertFalse(updated['stale'])
        self.assertEqual(2, updated['assessment']['version'])
        self.assertNotEqual(old['context_version'], updated['assessment']['context_version'])
        self.assertEqual(2, len(self.case.data['task_understanding_history']))
        self.assertEqual(old, self.case.data['task_understanding_history'][0])

    def test_history_and_readout_are_bounded_and_sanitized(self):
        for index in range(22):
            understanding.update(self.case, dict(self.args, revision_reason=f'Revision {index}'))
        self.assertEqual(20, len(self.case.data['task_understanding_history']))
        self.assertEqual(22, self.case.data['task_understanding']['version'])
        record = self.case.data['task_understanding']
        record.update(actor=['bad'], assumptions=['x' * 2000] * 12,
                      alternatives=[{'interpretation': 'x' * 3000, 'material': 'yes'}] * 12,
                      hidden='must not leak')
        before = copy.deepcopy(self.case.data)
        result = understanding.readout(self.case)['assessment']
        self.assertEqual('', result['actor'])
        self.assertEqual(8, len(result['assumptions']))
        self.assertEqual(600, len(result['assumptions'][0]))
        self.assertEqual(6, len(result['alternatives']))
        self.assertFalse(result['alternatives'][0]['material'])
        self.assertNotIn('hidden', result)
        self.assertEqual(before, self.case.data)
        self.assertLess(len(json.dumps(result)), 20000)

    def test_new_direction_blocks_even_unverified_submission_until_reassessed(self):
        understanding.update(self.case, self.args)
        checkpoint_steering(self.root, self.run_id, [], ['QUEUE:correction'])
        ctx = SimpleNamespace(case=self.case, verification=False)
        result = submit_memo.run(ctx, {'conclusion': 'Old reading', 'findings': [], 'gaps': ''})
        self.assertFalse(result['filed'])
        self.assertIn('reassessment', result['error'])
        self.assertIsNone(self.case.data.get('memo'))
        self.assertTrue(self.case.data['task_understanding']['stale'])
        understanding.update(self.case, dict(self.args, revision_reason='Reassessed correction and affected findings'))
        self.assertFalse(understanding.readout(self.case)['stale'])
