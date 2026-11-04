"""Record explorer binds the selected matter and current source-access policy."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('records_console_ui', REPO / 'ui/ui.py')
ui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ui)


class MatterRecordConsoleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.console = ui.Console(REPO, data_root=Path(self.temp.name))
        self.a = self.console.matter_store().create({'question': 'Fictional supplier dispute for matter A.'})
        self.b = self.console.matter_store().create({'question': 'Fictional supplier dispute for matter B.'})
        self.child = self.console.matter_context(self.a['id'])
        inbox = self.child.agent / 'workspace/inbox'
        inbox.mkdir(parents=True, exist_ok=True)
        (inbox / 'record.txt').write_text('FICTIONAL_ALLOWED_SOURCE')
        store = self.console.matter_store()
        matter_path = store.path(self.a['id']) / 'matter.json'
        record = json.loads(matter_path.read_text())
        record['documents'] = [{'id': 'record', 'file': 'record.txt', 'state': 'transcribed'}]
        matter_path.write_text(json.dumps(record))

    def seed_run(self, child, run_id, case):
        runs = child.agent / 'workspace/runs' / run_id
        runs.mkdir(parents=True)
        (runs / 'progress.json').write_text(json.dumps({'run_id': run_id, 'status': 'paused'}))
        records = child.agent / 'workspace/case_records'
        records.mkdir(parents=True, exist_ok=True)
        (records / f'{run_id}.json').write_text(json.dumps({'run_id': run_id, **case}))

    def test_record_views_are_local_and_source_linked_without_a_run(self):
        result = self.console.matter_records(self.a['id'])
        self.assertEqual(result['snapshot']['matter_id'], self.a['id'])
        self.assertFalse(result['auto_ontology_connected'])
        self.assertEqual(result['row_count'], 1)
        self.assertEqual(result['source_handles'][0]['document_id'], 'record')
        self.assertNotIn(self.temp.name, json.dumps(result))

    def test_run_from_another_matter_and_arbitrary_sql_are_rejected(self):
        other = self.console.matter_context(self.b['id'])
        self.seed_run(other, 'run-other-only', {})
        with self.assertRaises(FileNotFoundError):
            self.console.matter_records(self.a['id'], 'run-other-only', 'claims')
        with self.assertRaises(ValueError):
            self.console.matter_records(self.a['id'], '', 'SELECT * FROM claims')

    def test_record_revision_tracks_source_and_other_run_policy_changes(self):
        initial = self.console.matter_detail(self.a['id'])['record_revision']
        (self.child.agent / 'workspace/inbox/record.txt').write_text('FICTIONAL_CHANGED_SOURCE')
        changed = self.console.matter_detail(self.a['id'])['record_revision']
        self.assertNotEqual(initial, changed)
        self.seed_run(self.child, 'run-policy', {'flags': [{'kind': 'privileged', 'document_id': 'record'}]})
        self.assertNotEqual(changed, self.console.matter_detail(self.a['id'])['record_revision'])

    def test_privilege_from_any_run_and_quarantine_remove_document(self):
        self.seed_run(self.child, 'run-revoking', {'flags': [{'kind': 'privileged', 'document_id': 'record'}]})
        self.assertEqual(self.console.matter_records(self.a['id'])['row_count'], 0)
        (self.child.agent / 'workspace/case_records/run-revoking.json').unlink()
        (self.child.agent / 'workspace/quarantine.json').write_text(json.dumps({'record': {'reason': 'fixture'}}))
        self.assertEqual(self.console.matter_records(self.a['id'])['row_count'], 0)


if __name__ == '__main__':
    unittest.main()
