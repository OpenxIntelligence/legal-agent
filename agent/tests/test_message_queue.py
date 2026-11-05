"""Queue timing, mutation races and restart delivery without network/model calls."""
import tempfile
import unittest
from pathlib import Path
from agent_kit import message_queue
from agent_kit.progress import new_run, load_progress, checkpoint_steering

class MessageQueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.run = new_run(self.root, instructions='Test', objective='Test', verification=False)

    def test_queue_waits_steer_delivers_and_edit_cancel_are_guarded(self):
        queued = message_queue.enqueue(self.root, self.run, 'Wait for resume')
        steering = message_queue.enqueue(self.root, self.run, 'Use the government deadline', mode='steer')
        edited = message_queue.change(self.root, queued['queue_id'], 'edit', 1, 'Changed before delivery')
        with self.assertRaises(ValueError):
            message_queue.change(self.root, queued['queue_id'], 'cancel', 1)
        delivered = message_queue.deliver(self.root, self.run, [])
        self.assertEqual(1, len(delivered))
        self.assertIn('government deadline', delivered[0]['content'])
        with self.assertRaises(ValueError):
            message_queue.change(self.root, steering['queue_id'], 'edit', 1, 'Too late')
        message_queue.change(self.root, queued['queue_id'], 'cancel', edited['revision'])
        self.assertEqual([], message_queue.deliver(self.root, self.run, delivered, include_queue=True))

    def test_queue_delivered_on_resume_once_and_crash_receipt_reconciles(self):
        item = message_queue.enqueue(self.root, self.run, 'Only once')
        self.assertEqual([], message_queue.deliver(self.root, self.run, []))
        added = message_queue.deliver(self.root, self.run, [], include_queue=True)
        self.assertEqual(1, len(added))
        self.assertEqual([], message_queue.deliver(self.root, self.run, added, include_queue=True))
        # Simulate a crash after durable checkpoint but before receipt write.
        with message_queue.locked(self.root) as rows:
            rows[0]['state'] = 'queued'
        self.assertEqual([], message_queue.deliver(self.root, self.run, added, include_queue=True))
        self.assertEqual('delivered', message_queue.listing(self.root)[0]['state'])
        self.assertEqual(1, len(load_progress(self.root, self.run)['messages']))

    def test_another_run_cannot_receive_direction(self):
        message_queue.enqueue(self.root, self.run, 'Matter A', mode='steer')
        other = new_run(self.root, instructions='Other', objective='Other', verification=False)
        self.assertEqual([], message_queue.deliver(self.root, other, []))

    def test_running_loop_injects_steer_after_tool_but_leaves_queue(self):
        from unittest.mock import patch
        from legal_agent import loop
        from tests import support
        root, cleanup = support.make_root()
        self.addCleanup(cleanup.cleanup)
        fake = support.scripted(root, [support.LIST, support.MEMO])
        original = loop.tools.run
        receipts = []
        def tool_boundary(client, root, run_id, call, **kwargs):
            result = original(client, root, run_id, call, **kwargs)
            if not receipts:
                receipts.append(message_queue.enqueue(root, run_id, 'Immediate direction', mode='steer'))
                receipts.append(message_queue.enqueue(root, run_id, 'Deferred direction', mode='queue'))
            return result
        with patch.object(loop.tools, 'run', side_effect=tool_boundary), \
             patch.object(loop.settings, 'verification_enabled', return_value=False):
            result = loop.run_agent(root=root, client=fake.client, turns=3)
        second = support.agent_requests(fake)[1]['input']
        self.assertTrue(any('Immediate direction' in m.get('content', '') for m in second))
        self.assertFalse(any('Deferred direction' in m.get('content', '') for m in second))
        states = {row['mode']: row['state'] for row in message_queue.listing(root, result['run_id'])}
        self.assertEqual({'steer': 'delivered', 'queue': 'queued'}, states)

    def test_edit_cancel_do_not_resurrect_original_in_new_objective(self):
        import importlib.util
        repo = Path(__file__).resolve().parents[2]
        spec = importlib.util.spec_from_file_location('queue_matter_store', repo / 'ui/matters.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        store = module.MatterStore(self.root / 'matters', lambda *_: {})
        matter = store.create({'question': 'Which government renewal deadline applies?'})
        path = store.path(matter['id'])
        item = message_queue.enqueue(path, 'run-fixture', 'Stale instruction')
        store.message(matter['id'], item['text'], delivery=item)
        updated = message_queue.change(path, item['queue_id'], 'edit', 1, 'Current instruction')
        store.refresh_deliveries(matter['id'])
        objective = (path / 'case/objective.md').read_text()
        self.assertNotIn('Stale instruction', objective)
        self.assertIn('Current instruction', objective)
        message_queue.change(path, item['queue_id'], 'cancel', updated['revision'])
        store.refresh_deliveries(matter['id'])
        self.assertNotIn('Current instruction', (path / 'case/objective.md').read_text())
        self.assertEqual('followup_cancelled', store.get(matter['id'])['messages'][-2]['kind'])

    def test_real_request_answer_is_delivered_before_resume_eligibility(self):
        from unittest.mock import patch
        from agent_kit.case_record import CaseRecord
        from agent_kit.progress import save_progress
        from agent_kit import document_text
        from legal_agent import loop
        from tests import support
        root, cleanup = support.make_root()
        self.addCleanup(cleanup.cleanup)
        run = new_run(root, instructions='Test', objective='Test', verification=False)
        case = CaseRecord(root, run)
        case.set_checklist([support.checklist_item('designation')])
        visible = [d['id'] for d in document_text.list_documents(root)]
        request = case.open_request('designation', 'Which country?', 'missing context', visible)
        save_progress(root, run, [], status='needs_input')
        message_queue.enqueue(root, run, 'The designation is fictional country A.', request_id=request['id'])
        fake = support.scripted(root, [support.LIST])
        with patch.object(loop.settings, 'verification_enabled', return_value=False):
            result = loop.resume_run(root=root, run_id=run, client=fake.client, turns=1)
        self.assertEqual('paused', result['status'])
        self.assertEqual('answered', CaseRecord(root, run).data['requests'][-1]['status'])
        sent = str(support.agent_requests(fake)[0]['input'])
        self.assertIn('The designation is fictional country A.', sent)
        self.assertEqual('delivered', message_queue.listing(root, run)[0]['state'])

    def test_invalid_resume_does_not_consume_directions(self):
        from unittest.mock import patch
        from agent_kit.progress import save_progress
        from legal_agent import loop
        save_progress(self.root, self.run, [], status='answered')
        message_queue.enqueue(self.root, self.run, 'Retain until a valid continuation')
        with patch.object(loop.settings, 'validate_runtime_config'), \
             patch.object(loop, '_run_turns') as turns:
            result = loop.resume_run(root=self.root, run_id=self.run, client=object())
        turns.assert_not_called()
        self.assertEqual('answered', result['status'])
        self.assertEqual('queued', message_queue.listing(self.root, self.run)[0]['state'])
        self.assertEqual([], load_progress(self.root, self.run)['messages'])

    def test_cancel_delivery_race_has_one_winner(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        item = message_queue.enqueue(self.root, self.run, 'Race direction', mode='steer')
        ready = Barrier(2)
        def cancel():
            ready.wait()
            try:
                message_queue.change(self.root, item['queue_id'], 'cancel', 1)
                return True
            except ValueError:
                return False
        def deliver():
            ready.wait()
            return message_queue.deliver(self.root, self.run, [])
        with ThreadPoolExecutor(2) as pool:
            cancelled, additions = pool.submit(cancel), pool.submit(deliver)
            won, added = cancelled.result(), additions.result()
        self.assertEqual(not won, bool(added))
        self.assertEqual('cancelled' if won else 'delivered', message_queue.listing(self.root)[0]['state'])

if __name__ == '__main__': unittest.main()
