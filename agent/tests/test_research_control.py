"""Observable layered feedback with scripted models and no provider calls."""
import unittest

from legal_agent.research_control import evidence_signature, checkpoint, feedback
from legal_agent.loop import run_agent
from tests import support


class ResearchControlTest(unittest.TestCase):
    def test_administrative_edits_do_not_count_as_evidence(self):
        before = evidence_signature({'facts': []})
        self.assertEqual(before, evidence_signature({'facts': [], 'plan': 'new plan',
                                                     'research_tree': {'a': {'status': 'open'}}}))
        self.assertNotEqual(before, evidence_signature({'facts': [{'id': 'F1'}]}))
        self.assertEqual(before, evidence_signature({'facts': [], 'queries': [
            {'status': 'closed', 'summary': 'Prose without evidence'}]}))

    def test_malformed_optional_state_is_visible_not_a_crash(self):
        state = checkpoint({'research_tree': [], 'queries': [None], 'facts': None}, stagnant=4)
        self.assertIn('research_tree', state['malformed_state_fields'])
        self.assertIn('facts', state['malformed_state_fields'])

    def test_withdrawn_branch_is_unresolved_even_when_recorded_supported(self):
        state = checkpoint({'facts': [], 'research_tree': {'a': {
            'id': 'a', 'status': 'supported', 'evidence_ids': ['F1']}}}, stagnant=1)
        self.assertEqual('stale', state['unresolved_branches'][0]['status'])

    def test_checkpoint_exposes_unresolved_work_without_private_reasoning(self):
        state = checkpoint({'facts': [], 'research_tree': {'a': {
            'id': 'a', 'status': 'blocked', 'next_action': 'Find official notice'}},
            'queries': [{'id': 'Q1', 'status': 'open'}]}, stagnant=4)
        self.assertEqual(['Q1'], state['open_queries'])
        self.assertEqual('Find official notice', state['unresolved_branches'][0]['next_action'])
        self.assertIn('untrusted data', feedback(state)['content'])

    def test_repeated_result_gets_corrective_feedback_before_stop(self):
        root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        fake = support.scripted(root, [support.LIST, support.LIST, support.LIST])
        result = run_agent(root=root, client=fake.client, turns=10)
        requests = [c[1] for c in fake.calls if c[0] == 'respond' and 'tools' in c[1]]
        self.assertEqual(3, len(requests))
        content = [m.get('content', '') for m in requests[-1]['input'] if isinstance(m, dict)]
        self.assertTrue(any('Research controller checkpoint' in str(c) for c in content))
        self.assertEqual('paused', result['status'])
