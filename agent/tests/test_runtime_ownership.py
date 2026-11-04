"""Real local process ownership, synthetic checkpoints; no provider calls."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from agent_kit.runtime_ownership import owner_status, reconcile_abandoned, runner_lock


class RuntimeOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = self.root / 'workspace/runs/run-fixture'
        self.run.mkdir(parents=True)
        self.progress = {'run_id': 'run-fixture', 'status': 'running', 'turn': 1,
                         'messages': [{'role': 'user', 'content': 'Synthetic question'}],
                         'notes': {}}
        (self.run / 'progress.json').write_text(json.dumps(self.progress))

    def spawn_owner(self):
        code = ('from pathlib import Path; import sys,time; '
                'from agent_kit.runtime_ownership import runner_lock; '
                'ctx=runner_lock(Path(sys.argv[1]), "run-fixture"); '
                'ctx.__enter__(); print("owned",flush=True); time.sleep(30)')
        process = subprocess.Popen([sys.executable, '-c', code, str(self.root)],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        def cleanup():
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            process.stdout.close()
            process.stderr.close()
        self.addCleanup(cleanup)
        self.assertEqual(process.stdout.readline().strip(), 'owned')
        return process

    def test_process_owner_survives_independent_reader_and_refuses_duplicate(self):
        process = self.spawn_owner()
        status = owner_status(self.root)
        self.assertTrue(status['active'])
        self.assertEqual(status['pid'], process.pid)
        self.assertEqual(status['run_id'], 'run-fixture')
        with self.assertRaisesRegex(RuntimeError, 'already owns'):
            with runner_lock(self.root):
                self.fail('must not own')
        with self.assertRaisesRegex(RuntimeError, 'already owns'):
            reconcile_abandoned(self.root, 'run-fixture')
        self.assertEqual(json.loads((self.run / 'progress.json').read_text()), self.progress)

    def test_process_death_releases_lock_and_explicit_recovery_preserves_checkpoint(self):
        process = self.spawn_owner()
        process.kill()
        process.wait(timeout=5)
        self.assertFalse(owner_status(self.root)['active'])
        recovered = reconcile_abandoned(self.root, 'run-fixture')
        self.assertEqual(recovered['status'], 'paused')
        self.assertEqual(recovered['messages'], self.progress['messages'])
        self.assertEqual(recovered['turn'], 1)
        self.assertEqual(recovered['notes']['stop_reason'], 'runner_interrupted')
        self.assertEqual(len(recovered['notes']['runner_recoveries']), 1)
        self.assertFalse(owner_status(self.root)['active'])
        with self.assertRaisesRegex(RuntimeError, 'Only an abandoned'):
            reconcile_abandoned(self.root, 'run-fixture')

    def test_normal_exception_releases_lock_but_stale_metadata_is_not_active(self):
        with self.assertRaisesRegex(ValueError, 'synthetic'):
            with runner_lock(self.root, 'run-fixture'):
                self.assertTrue(owner_status(self.root)['active'])
                raise ValueError('synthetic')
        self.assertFalse(owner_status(self.root)['active'])

    def test_no_checkpoint_cannot_be_fabricated_for_resume(self):
        self.progress['messages'] = []
        (self.run / 'progress.json').write_text(json.dumps(self.progress))
        with self.assertRaisesRegex(RuntimeError, 'No saved conversation'):
            reconcile_abandoned(self.root, 'run-fixture')
        self.assertEqual(json.loads((self.run / 'progress.json').read_text()), self.progress)

    def test_foreign_or_symlinked_run_cannot_be_recovered(self):
        for invalid in ('../outside', 'run-fixture/../../x', '', 'elsewhere'):
            with self.assertRaises(ValueError):
                reconcile_abandoned(self.root, invalid)
        link = self.run.parent / 'run-link'
        link.symlink_to(self.run, target_is_directory=True)
        with self.assertRaises(ValueError):
            reconcile_abandoned(self.root, 'run-link')
