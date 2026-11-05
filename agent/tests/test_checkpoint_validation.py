"""Malformed/local-path checkpoints fail before continuation or replacement."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_kit import progress
from agent_kit.runtime_ownership import CheckpointError, reconcile_abandoned


class CheckpointValidationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.run = progress.new_run(self.root, instructions='Synthetic', objective='Question', verification=False)
        self.path = self.root / 'workspace/runs' / self.run / 'progress.json'
        self.data = progress.load_progress(self.root, self.run)

    def test_run_identifiers_cannot_escape_on_read_or_write(self):
        outside = self.root / 'outside'
        outside.mkdir()
        marker = outside / 'progress.json'
        marker.write_text(json.dumps(self.data))
        for invalid in ('../../outside', str(outside), 'run-fixture/../other', '', None, 'run-x\\outside'):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    progress.load_progress(self.root, invalid)
                with self.assertRaises(ValueError):
                    progress._write(self.root, invalid, dict(self.data, run_id=invalid))
        self.assertEqual(json.loads(marker.read_text()), self.data)

    def test_symlinked_run_and_checkpoint_are_rejected(self):
        link = self.path.parent.parent / 'run-link'
        link.symlink_to(self.path.parent, target_is_directory=True)
        with self.assertRaises(ValueError):
            progress.load_progress(self.root, 'run-link')
        original = self.path.read_text()
        outside = self.root / 'outside.json'
        outside.write_text(original)
        self.path.unlink()
        self.path.symlink_to(outside)
        with self.assertRaises(ValueError):
            progress.load_progress(self.root, self.run)
        with self.assertRaises(ValueError):
            progress._write(self.root, self.run, self.data)
        self.assertEqual(original, outside.read_text())

    def test_malformed_checkpoint_cannot_load_or_recover(self):
        invalids = [[], dict(self.data, run_id='run-other'), dict(self.data, status='success'),
                    dict(self.data, turn=True), dict(self.data, messages='saved'),
                    dict(self.data, messages=[None]), dict(self.data, notes=[]),
                    dict(self.data, verification='false'), dict(self.data, turns=[{'end': 1}]),
                    dict(self.data, turns=[{}]),
                    dict(self.data, steering_directive_ids=['Q1', 'Q1'])]
        for data in invalids:
            with self.subTest(data=data):
                raw = json.dumps(data)
                self.path.write_text(raw)
                with self.assertRaises(CheckpointError):
                    progress.load_progress(self.root, self.run)
                with self.assertRaises(CheckpointError):
                    reconcile_abandoned(self.root, self.run)
                self.assertEqual(raw, self.path.read_text())
        self.path.write_text('{partial')
        with self.assertRaisesRegex(CheckpointError, 'unreadable JSON'):
            progress.load_progress(self.root, self.run)

    def test_failed_steering_checkpoint_preserves_prior_conversation(self):
        before = self.path.read_text()
        for ids in ([], [''], ['Q1', 'Q1'], [None]):
            with self.assertRaises(CheckpointError):
                progress.checkpoint_steering(self.root, self.run, [], ids)
        with self.assertRaises(CheckpointError):
            progress.checkpoint_steering(self.root, self.run, [None], ['Q1'])
        self.assertEqual(before, self.path.read_text())
        with patch.object(progress.os, 'replace', side_effect=OSError('disk fault')):
            with self.assertRaises(OSError):
                progress.checkpoint_steering(self.root, self.run, [{'role': 'user', 'content': 'Direction'}], ['Q1'])
        self.assertEqual(before, self.path.read_text())
        self.assertEqual([], list(self.path.parent.glob('.progress-*')))

    def test_legacy_optional_metadata_is_not_required(self):
        for key in ('config', 'verification', 'turns'):
            self.data.pop(key)
        self.path.write_text(json.dumps(self.data))
        self.assertEqual(self.data, progress.load_progress(self.root, self.run))
        progress.save_progress(self.root, self.run, [], status='paused')
        self.assertEqual('paused', progress.load_progress(self.root, self.run)['status'])

    def test_legacy_fixed_temp_symlink_cannot_redirect_new_checkpoint(self):
        victim = self.root / 'victim'
        victim.write_text('Preserve this')
        self.path.with_suffix('.tmp').symlink_to(victim)
        progress.checkpoint_steering(self.root, self.run, [{'role': 'user', 'content': 'Direction'}], ['Q1'])
        self.assertEqual('Preserve this', victim.read_text())
        self.assertEqual(['Q1'], progress.load_progress(self.root, self.run)['steering_directive_ids'])
