"""Actual Console collab writes reach the resume boundary exactly once, offline."""
from contextlib import redirect_stdout
import importlib.util
from io import StringIO
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'agent'))
spec = importlib.util.spec_from_file_location('matter_queue_ui', REPO / 'ui/ui.py')
ui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ui)
from agent_kit.case_record import CaseRecord
from agent_kit.progress import new_run, load_progress
from legal_agent import loop


class MatterQueueSemanticsTests(unittest.TestCase):
    def test_real_queue_persists_and_next_resume_injects_once_without_applying(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            root = Path(tmp)
            console = ui.Console(REPO, data_root=root, parent=ui.Console(REPO))
            run_id = new_run(root, instructions='Synthetic instructions', objective='Synthetic objective', verification=False)
            env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'PYTHON': sys.executable,
                   'LEGAL_AGENT_ROOT': str(root), 'CASE_DIR': str(root / 'case')}
            with patch.object(console, '_process_env', return_value=env):
                receipt = console.queue_message(run_id, 'Inspect the fictional acceptance email first.')
            self.assertEqual('queued', receipt['state'])
            case = CaseRecord(root, run_id)
            directive = case.directives()[0]
            self.assertEqual(receipt['directive_id'], directive['id'])
            self.assertEqual('proposed', directive['state'])
            self.assertEqual('client', directive['author']['role'])
            self.assertNotIn(run_id, directive.get('injected_runs', []))
            delivered = []

            def no_provider_turns(**kwargs):
                delivered.append(kwargs['messages'])
                return load_progress(root, run_id)

            with patch('socket.socket', side_effect=AssertionError('No provider or network allowed')), \
                    patch.object(loop.settings, 'validate_runtime_config'), \
                    patch.object(loop, 'resume', return_value={'message': 'Continue the synthetic investigation.'}), \
                    patch.object(loop, '_begin_slice'), patch.object(loop, '_commit_slice'), \
                    patch.object(loop, '_run_turns', side_effect=no_provider_turns), redirect_stdout(StringIO()):
                loop.resume_run(root=root, run_id=run_id, client=object(), turns=1)
                loop.resume_run(root=root, run_id=run_id, client=object(), turns=1)
            first = [m for m in delivered[0] if 'Steering directive' in m.get('content', '')]
            second = [m for m in delivered[1] if 'Steering directive' in m.get('content', '')]
            self.assertEqual(1, len(first))
            self.assertIn('Inspect the fictional acceptance email first.', first[0]['content'])
            # The durable conversation includes the already delivered direction,
            # but it is never inserted a second time on resume.
            self.assertEqual(1, len(second))
            self.assertEqual(first, second)
            persisted = CaseRecord(root, run_id).directives()[0]
            self.assertEqual([run_id], persisted['injected_runs'])
            self.assertEqual('proposed', persisted['state'])  # delivery is never application

    def test_deferred_answer_metadata_unblocks_resume_choice_and_clears_after_flush(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            root = Path(tmp)
            console = ui.Console(REPO, data_root=root, parent=ui.Console(REPO))
            run_id = new_run(root, instructions='Synthetic instructions', objective='Synthetic objective', verification=False)
            case = CaseRecord(root, run_id)
            case.data['requests'] = [{'id': 'R1', 'status': 'open', 'item': 'acceptance',
                                     'need': 'Which fictional email?', 'rule': 'missing evidence', 'visible_ids': []}]
            case._save('synthetic_fixture', 'test', {})
            env = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'), 'PYTHON': sys.executable,
                   'LEGAL_AGENT_ROOT': str(root), 'CASE_DIR': str(root / 'case')}
            with patch.object(console.jobs, 'any_running', return_value=True):
                console.queue_message(run_id, 'Inspect the fictional email dated Tuesday.', 'R1')
                console.queue_message(run_id, 'Also compare the fictional delivery log.', 'R1')
            detail = console.run_detail(run_id)
            self.assertEqual(['R1'], detail['pending_request_ids'])
            self.assertEqual('open', detail['case']['requests'][0]['status'])
            self.assertNotIn('Inspect the fictional email', str(detail['pending_request_ids']))
            # The metadata survives a server Console restart; it is not an in-memory promise.
            reopened = ui.Console(REPO, data_root=root, parent=ui.Console(REPO))
            self.assertEqual(['R1'], reopened.run_detail(run_id)['pending_request_ids'])
            with patch.object(console, '_process_env', return_value=env):
                console._flush_steering(run_id)
            self.assertEqual([], console.run_detail(run_id)['pending_request_ids'])
            self.assertEqual('answered', CaseRecord(root, run_id).data['requests'][0]['status'])

    def test_queue_refuses_cross_matter_run_before_collab_mutation(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            root = Path(tmp)
            a = root / 'a'; b = root / 'b'; a.mkdir(); b.mkdir()
            run_id = new_run(a, instructions='Synthetic instructions', objective='Synthetic objective', verification=False)
            console = ui.Console(REPO, data_root=b, parent=ui.Console(REPO))
            with patch.object(console, 'collab_op') as collab:
                with self.assertRaises(FileNotFoundError):
                    console.queue_message(run_id, 'A direction for another matter.')
            collab.assert_not_called()


if __name__ == '__main__':
    unittest.main()
