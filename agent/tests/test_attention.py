"""Observable work priorities, not model-internal reasoning."""
import unittest
from unittest.mock import patch

from agent_kit import message_queue
from agent_kit.case_record import CaseRecord
from legal_agent import loop
from legal_agent.attention import priorities
from legal_agent.research_control import checkpoint, evidence_signature
from tests import support


FRAME = dict(actor='USCIS', event='Reported agency notice deadline passed',
             interpretation='Agency renewal duty, not individual re-registration',
             intended_outcome='Explain governing rule and missing application facts',
             success_criteria=['Cite primary rule'], revision_reason='Initial reading')


class AttentionTests(unittest.TestCase):
    def test_ranking_keeps_evidence_visible_and_grouping_is_not_dependency(self):
        data = {'task_understanding': {'stale': True, 'alternatives': [
            {'interpretation': str(i), 'material': True, 'next_action': 'Test source'} for i in range(6)]},
            'facts': [], 'research_tree': {
                'parent': {'status': 'open', 'question': 'General rule'},
                'child': {'parent_id': 'parent', 'status': 'supported', 'evidence_ids': ['gone'], 'question': 'Application'}}}
        queue = priorities(data)
        self.assertEqual('alignment', queue[0]['kind'])
        self.assertEqual(2, sum(item['kind'] == 'interpretation' for item in queue))
        self.assertEqual(['child', 'parent'], [item['target'] for item in queue if item['kind'] == 'research'])
        self.assertEqual([], priorities(data, limit=0))
        self.assertEqual([], priorities({'task_understanding': [], 'research_tree': None}))

    def test_alignment_is_not_evidence_and_request_truncation_is_explicit(self):
        data = {'task_understanding': dict(FRAME, original_request='x' * 5000)}
        self.assertEqual(evidence_signature({}), evidence_signature(data))
        saved = checkpoint(data, stagnant=0)['task_understanding']
        self.assertEqual('USCIS', saved['actor'])
        self.assertIn('not individual', saved['interpretation'])
        self.assertTrue(saved['original_request_truncated'])

    def test_steering_triggers_next_call_reassessment_not_every_call(self):
        root, cleanup = support.make_root()
        self.addCleanup(cleanup.cleanup)
        fake = support.scripted(root, [('update_task_understanding', FRAME), support.LIST, support.LIST])
        original = loop.tools.run
        def boundary(client, root, run_id, call, **kwargs):
            result = original(client, root, run_id, call, **kwargs)
            if call.name == 'update_task_understanding':
                message_queue.enqueue(root, run_id, 'I mean the agency notice duty, not my filing deadline', mode='steer')
            return result
        with patch.object(loop.tools, 'run', side_effect=boundary), patch.object(loop.settings, 'verification_enabled', return_value=False):
            result = loop.run_agent(root=root, client=fake.client, turns=3)
        requests = support.agent_requests(fake)
        self.assertEqual(3, len(requests))
        def checkpoints(request):
            return [m for m in request['input'] if isinstance(m, dict) and 'Research controller checkpoint' in str(m.get('content', ''))]
        self.assertEqual(0, len(checkpoints(requests[0])))
        self.assertEqual(1, len(checkpoints(requests[1])))
        self.assertEqual(1, len(checkpoints(requests[2])))
        self.assertIn('agency notice duty', str(requests[1]['input']))
        self.assertTrue(CaseRecord(root, result['run_id']).data['task_understanding']['stale'])
