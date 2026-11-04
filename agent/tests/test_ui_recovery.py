"""Recovery is local state reconciliation, never a provider operation."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('recovery_ui', REPO / 'ui/ui.py')
ui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ui)


class UIRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.console = ui.Console(REPO, data_root=self.root, parent=ui.Console(REPO))
        self.run = self.root / 'workspace/runs/run-fixture'
        self.run.mkdir(parents=True)
        self.progress = {'run_id': 'run-fixture', 'status': 'running', 'turn': 1,
                         'messages': [{'role': 'user', 'content': 'Fictional question'}],
                         'notes': {}, 'turns': []}
        self.save()

    def save(self):
        (self.run / 'progress.json').write_text(json.dumps(self.progress))

    def test_restarted_console_offers_explicit_recovery_without_launch(self):
        with patch.object(self.console, '_require_live'), patch.object(self.console, '_launch') as launch:
            before = self.console.run_detail('run-fixture')
            self.assertEqual('available', before['recovery']['state'])
            self.assertEqual('running', json.loads((self.run / 'progress.json').read_text())['status'])
            after = self.console.recover_run('run-fixture')
            self.assertEqual('paused', after['status'])
            self.assertEqual('recovered', after['recovery']['state'])
            self.assertTrue(after['recovery']['can_resume'])
            launch.assert_not_called()

    def test_owner_blocks_recovery_and_mutations_after_console_restart(self):
        runtime = self.console._runtime_module()
        with runtime.runner_lock(self.root, 'run-fixture'):
            self.assertTrue(self.console._running())
            self.assertTrue(self.console._settings_busy())
            self.assertEqual('active_elsewhere', self.console.run_detail('run-fixture')['recovery']['state'])
            with patch.object(self.console, '_require_live'):
                with self.assertRaises(RuntimeError):
                    self.console.recover_run('run-fixture')
        self.assertEqual('running', json.loads((self.run / 'progress.json').read_text())['status'])

    def test_missing_checkpoint_cannot_resume(self):
        self.progress['messages'] = []
        self.save()
        recovery = self.console.run_detail('run-fixture')['recovery']
        self.assertEqual('unavailable', recovery['state'])
        self.assertFalse(recovery['can_resume'])

    def test_unknown_matter_owner_blocks_shared_provider_replacement(self):
        unopened = self.root / 'workspace/matters/matter-fixture'
        with self.console._runtime_module().runner_lock(unopened):
            self.assertTrue(self.console._settings_busy())


if __name__ == '__main__':
    unittest.main()
