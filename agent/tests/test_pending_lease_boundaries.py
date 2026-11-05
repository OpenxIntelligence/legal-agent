"""Offline regressions for persisted operation and lease boundary validation."""
import json
import unittest

from agent_kit import tools
from agent_kit.case_record import CaseRecord, LeaseError, StaleLease
from agent_kit.progress import new_run
from agent_kit.runtime_ownership import CheckpointError
from tests import support


class PendingLeaseBoundariesTest(unittest.TestCase):
    def setUp(self):
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        self.run_id = new_run(self.root, instructions='i', objective='o', verification=False)
        self.case = CaseRecord(self.root, self.run_id)
        self.case.set_checklist([{'id': 'alpha', 'question': 'What happened?', 'evidence_needed': 'records'}])

    def test_invalid_lease_inputs_cannot_mutate_record(self):
        before = json.dumps(self.case.data, sort_keys=True)
        defaults = {'task_id': 'T1', 'worker': 'W1', 'ttl': 10, 'reservation': 100,
                    'epoch_now': 1000, 'versions': {}}
        invalids = [('reservation', v) for v in (-1, True, 1.5, '100', None)]
        invalids += [('ttl', v) for v in (0, -1, True, float('nan'), float('inf'))]
        invalids += [('epoch_now', v) for v in (-1, False, float('nan'))]
        invalids += [('worker', ''), ('worker', None), ('versions', [])]
        for field, value in invalids:
            with self.subTest(field=field, value=value):
                with self.assertRaises(LeaseError):
                    self.case.acquire_lease(**dict(defaults, **{field: value}))
                self.assertEqual(before, json.dumps(self.case.data, sort_keys=True))
        lease = self.case.acquire_lease(**defaults)
        with self.assertRaises(StaleLease):
            self.case.release_lease('T1', True)
        self.assertEqual(lease, self.case.task('T1')['lease'])

    def test_pending_slots_are_run_bound_and_legacy_compatible(self):
        tools._record_pending(self.root, self.run_id, 1, 'tool', {}, {'ok': True})
        path = tools.pending_op_path(self.root, self.run_id)
        self.assertEqual({'ok': True}, tools._pending_result(self.root, self.run_id, 1, 'tool', {}))
        with self.assertRaisesRegex(CheckpointError, 'conflicts'):
            tools._pending_result(self.root, self.run_id, 1, 'different_tool', {})
        self.assertIsNone(tools._pending_result(self.root, self.run_id, 2, 'tool', {}))
        slot = json.loads(path.read_text())
        slot.pop('run_id')
        path.write_text(json.dumps(slot))
        self.assertEqual({'ok': True}, tools._pending_result(self.root, self.run_id, 1, 'tool', {}))
        for malformed in (dict(slot, run_id='run-other'), dict(slot, seq=True), [], {'seq': 1}):
            path.write_text(json.dumps(malformed))
            with self.assertRaises(CheckpointError):
                tools._pending_result(self.root, self.run_id, 1, 'tool', {})
        path.write_text('{partial')
        with self.assertRaises(CheckpointError):
            tools._pending_result(self.root, self.run_id, 1, 'tool', {})

    def test_replay_requires_valid_checkpoint_and_current_tool_authorization(self):
        tools._record_pending(self.root, self.run_id, 1, 'check_signatures', {}, {'cached': True})
        result = tools.run(None, self.root, self.run_id,
                           {'name': 'check_signatures', 'arguments': {}}, op_seq=1)
        self.assertIn('error', result)  # Verification-only tool cannot bypass its current policy.
        tools._record_pending(self.root, self.run_id, 1, 'list_documents', {}, {'cached': True})
        checkpoint = self.root / 'workspace' / 'runs' / self.run_id / 'progress.json'
        checkpoint.write_text('{partial')
        with self.assertRaises(CheckpointError):
            tools.run(None, self.root, self.run_id,
                      {'name': 'list_documents', 'arguments': {}}, op_seq=1)

    def test_pending_paths_and_atomic_temp_cannot_overwrite_external_files(self):
        victim = self.root / 'victim'
        victim.write_text('preserve')
        path = tools.pending_op_path(self.root, self.run_id)
        path.with_suffix('.tmp').symlink_to(victim)
        tools._record_pending(self.root, self.run_id, 1, 'tool', {}, {'ok': True})
        self.assertEqual('preserve', victim.read_text())
        for seq in (True, 0, -1, 1.5, '1'):
            with self.assertRaises(CheckpointError):
                tools._record_pending(self.root, self.run_id, seq, 'tool', {}, {})
        path.unlink()
        path.symlink_to(victim)
        with self.assertRaises(CheckpointError):
            tools._pending_result(self.root, self.run_id, 1, 'tool', {})
        with self.assertRaises(CheckpointError):
            tools._record_pending(self.root, self.run_id, 1, 'tool', {}, {})
        with self.assertRaises(ValueError):
            tools.pending_op_path(self.root, '../outside')
        self.assertEqual('preserve', victim.read_text())
