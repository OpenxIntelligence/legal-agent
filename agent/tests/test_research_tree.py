"""Public research decisions remain bounded, source-linked and atomically validated."""
import copy
import unittest
from types import SimpleNamespace

from agent_kit import research_tree
from agent_kit.case_record import CaseError, CaseRecord
from agent_kit.progress import new_run
from agent_kit.tools import update_research_tree
from tests import support


class ResearchTreeTest(unittest.TestCase):
    def setUp(self):
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        self.run_id = new_run(self.root, instructions='i', objective='o', verification=False)
        self.case = CaseRecord(self.root, self.run_id)

    def apply(self, nodes):
        return research_tree.update(self.case, nodes)

    def reject_unchanged(self, nodes):
        before = copy.deepcopy(self.case.data)
        with self.assertRaises(CaseError):
            self.apply(nodes)
        self.assertEqual(before, self.case.data)

    def test_branch_batch_can_name_later_parent_and_persists(self):
        result = update_research_tree.run(SimpleNamespace(case=self.case), {'nodes': [
            {'id': 'notice', 'parent': 'root', 'question': 'Was notice timely?',
             'hypothesis': 'Notice may have been late', 'next_action': 'Retrieve notice'},
            {'id': 'root', 'question': 'What rule governs agency inaction?'},
        ]})
        self.assertEqual(2, result['coverage']['total'])
        self.assertEqual(2, len(result['unresolved']))
        self.assertEqual(self.case.data['research_tree'], CaseRecord(self.root, self.run_id).data['research_tree'])
        self.apply([{'id': 'notice', 'status': 'blocked', 'summary': 'Notice not found'}])
        node = self.case.data['research_tree']['notice']
        self.assertEqual('Retrieve notice', node['next_action'])
        self.assertEqual('Was notice timely?', node['question'])
        self.assertEqual('root', node['parent'])

    def test_evidence_assessments_need_real_active_facts(self):
        self.case.data['facts'] = [{'id': 'F1'}, {'id': 'F2', 'withdrawn': True}]
        self.apply([{'id': 'root', 'question': 'Which rule applies?'}])
        for status in ('supported', 'contradicted', 'mixed'):
            for evidence in ([], ['imaginary'], ['F2'], ['F1', 'F1'], [True]):
                self.reject_unchanged([{'id': 'root', 'status': status,
                                       'summary': 'Public assessment', 'evidence_ids': evidence}])
            result = self.apply([{'id': 'root', 'status': status,
                                 'summary': 'Public assessment', 'evidence_ids': ['F1']}])
            self.assertEqual(1, result['coverage']['assessed'])
        self.assertEqual(1, len(result['unresolved']))  # mixed remains unresolved
        self.assertIn('do not certify', result['note'])

    def test_cycles_depth_and_invalid_batches_do_not_partially_mutate(self):
        self.apply([{'id': 'root', 'question': 'Main question'}])
        invalids = [[], 'nodes', [{'id': 'root', 'unexpected': True}],
                    [{'id': 'child', 'parent': 'missing', 'question': 'Question'}],
                    [{'id': 'root', 'parent': 'root'}],
                    [{'id': 'root', 'parent': 'child'}, {'id': 'child', 'parent': 'root', 'question': 'Child'}],
                    [{'id': 'new', 'question': 'Good'}, {'id': 'bad', 'question': ''}],
                    [{'id': 'new', 'question': 'Good'}, {'id': 'new', 'question': 'Duplicate'}],
                    [{'id': 'root', 'status': 'blocked', 'summary': 'Missing source'}],
                    [{'id': 'root', 'status': 'blocked', 'next_action': 'Retrieve'}],
                    [{'id': 'root', 'parent': []}],
                    [{'id': 'root', 'summary': 'x' * 1201}]]
        for nodes in invalids:
            with self.subTest(nodes=nodes):
                self.reject_unchanged(nodes)
        chain = [{'id': f'n{i}', 'parent': f'n{i-1}' if i else '', 'question': 'Question'} for i in range(6)]
        self.reject_unchanged(chain)
        self.apply(chain[:5])
        self.reject_unchanged([{'id': 'n0', 'parent': 'n4'}])

    def test_withdrawn_or_missing_support_becomes_effectively_stale(self):
        self.case.data['facts'] = [{'id': 'F1'}, {'id': 'F2'}]
        self.apply([{'id': 'root', 'question': 'Which rule?', 'status': 'supported',
                     'summary': 'Initial assessment', 'evidence_ids': ['F1', 'F2']}])
        for facts in ([{'id': 'F1'}, {'id': 'F2', 'withdrawn': True}], [{'id': 'F1'}], []):
            with self.subTest(facts=facts):
                self.case.data['facts'] = facts
                before = copy.deepcopy(self.case.data)
                result = research_tree.readout(self.case)
                self.assertEqual('stale', result['nodes'][0]['effective_status'])
                self.assertTrue(result['nodes'][0]['stale'])
                self.assertEqual('supported', result['nodes'][0]['status'])
                self.assertEqual(0, result['coverage']['assessed'])
                self.assertEqual(1, result['coverage']['by_status']['stale'])
                self.assertEqual('root', result['unresolved'][0]['id'])
                self.assertTrue(result['unresolved'][0]['next_action'])
                self.assertEqual(before, self.case.data)
        self.case.data['facts'] = [{'id': 'F1'}, {'id': 'F2'}]
        self.assertEqual('supported', research_tree.effective_status(
            self.case.data, self.case.data['research_tree']['root']))
        self.assertEqual('open', research_tree.effective_status({}, {'status': 'open'}))

    def test_capacity_limits_apply_before_any_save(self):
        self.reject_unchanged([{'id': f'n{i}', 'question': 'Question'} for i in range(13)])
        for start in (0, 10, 20, 30):
            self.apply([{'id': f'n{i}', 'question': 'Question'} for i in range(start, start + 10)])
        self.reject_unchanged([{'id': 'overflow', 'question': 'Question'}])
        self.apply([{'id': 'n0', 'next_action': 'Retrieve authority'}])
        self.assertEqual(40, len(self.case.data['research_tree']))
