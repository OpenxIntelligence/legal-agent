"""Matter boundaries and provider dispatch, without provider requests."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('matter_console_ui', REPO / 'ui/ui.py')
ui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ui)


class MatterConsoleTests(unittest.TestCase):
    def test_info_matches_effective_provider_configuration(self):
        console = ui.Console(REPO)
        with patch.object(console, '_process_env', return_value={
            'MODEL_API_KEY': '   ', 'MODEL_API_MODEL': '', 'VERIFICATION': 'off'
        }):
            info = console.info()
        self.assertFalse(info['api_key_configured'])
        self.assertEqual(info['model'], 'muse-spark-1.3')
        self.assertFalse(info['verification'])

    def test_invalid_launch_options_do_not_mutate_control_or_dispatch(self):
        console = ui.Console(REPO)
        invalid = [(value, False) for value in (True, False, 0, -1, 201, 1.5, "30", [], None)]
        invalid += [(30, value) for value in ("false", 0, 1, [], {})]
        with patch.object(console, '_reset_control') as control, patch.object(console, '_launch') as launch:
            for turns, step in invalid:
                for action in (lambda: console.start_run(turns, step),
                               lambda: console.resume_run('run-probe', turns, step)):
                    with self.subTest(turns=turns, step=step), self.assertRaises(ValueError):
                        action()
            control.assert_not_called()
            launch.assert_not_called()

    def test_control_rejects_truthy_strings_and_unknown_options_before_writing(self):
        console = ui.Console(REPO)
        with patch.object(console, 'control') as read:
            for body in ({'step': 'false'}, {'next': 'false'}, {'next': 1},
                         {'steps': True}, [], None):
                with self.subTest(body=body), self.assertRaises(ValueError):
                    console.set_control(body)
            read.assert_not_called()

    def test_later_upload_rejects_running_matter_before_ingestion(self):
        from unittest.mock import Mock
        console = ui.Console(REPO)
        child = Mock()
        import threading
        child._launch_lock = threading.RLock()
        child.jobs.any_running.return_value = True
        with patch.object(console, 'matter_context', return_value=child), patch.object(console, 'matter_store') as store:
            with self.assertRaisesRegex(RuntimeError, 'pause'):
                console.matter_add_documents('matter-owned', {'documents': []})
            store.assert_not_called()

    def test_matter_commands_scope_data_and_keep_code_root(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            parent = ui.Console(REPO)
            child = ui.Console(REPO, data_root=Path(tmp), parent=parent)
            with patch.object(parent, '_env', return_value={'MODEL_API_KEY': 'test-placeholder', 'VERIFICATION': 'on'}):
                env = child._process_env()
            self.assertEqual(child.agent, Path(tmp))
            self.assertEqual(env['LEGAL_AGENT_ROOT'], tmp)
            self.assertEqual(env['CASE_DIR'], str(Path(tmp) / 'case'))
            self.assertEqual(child._code_agent, REPO / 'agent')
            with patch.object(child.jobs, 'start', return_value={}) as launch:
                with patch.object(child, '_process_env', return_value=env):
                    child.start_run(4)
            self.assertEqual(launch.call_args.args[2], REPO / 'agent')
            self.assertEqual(launch.call_args.kwargs['env']['LEGAL_AGENT_ROOT'], tmp)

    def test_isolated_matter_cannot_dispatch_provider(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'LEGAL_AGENT_ROOT':tmp}, clear=True):
            parent = ui.Console(REPO)
            child = ui.Console(REPO, data_root=Path(tmp) / 'child', parent=parent)
            with patch.object(child.jobs, 'start') as launch:
                with self.assertRaisesRegex(RuntimeError, 'disabled'):
                    child.start_run(1)
            launch.assert_not_called()

    def test_matter_does_not_discover_another_matters_runs(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            parent = ui.Console(REPO)
            a = Path(tmp)/'a'; b = Path(tmp)/'b'
            (a/'workspace/runs/run-only-a').mkdir(parents=True)
            b.mkdir()
            ca=ui.Console(REPO,data_root=a,parent=parent)
            cb=ui.Console(REPO,data_root=b,parent=parent)
            self.assertEqual([r['id'] for r in ca.list_runs()], ['run-only-a'])
            self.assertEqual(cb.list_runs(), [])
            with self.assertRaises(FileNotFoundError):
                cb.run_detail('run-only-a')

    def test_source_reads_current_pinned_text_and_blocks_quarantine_or_escape(self):
        import hashlib, json
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); inbox=root/'workspace/inbox'; inbox.mkdir(parents=True)
            source=inbox/'record.txt'; source.write_text('Fictional record')
            sha=hashlib.sha256(source.read_bytes()).hexdigest()
            cache=root/'workspace/cache/text'; cache.mkdir(parents=True)
            (cache/f'{sha}.json').write_text(json.dumps({'pages':[{'page':1,'text':'Fictional record'}]}))
            child=ui.Console(REPO,data_root=root,parent=ui.Console(REPO))
            self.assertEqual(child.document_source('record')['pages'][0]['text'],'Fictional record')
            with self.assertRaises(FileNotFoundError):child.document_source('../record')
            with patch.object(child,'_quarantine_map',return_value={'record':{}}):
                with self.assertRaisesRegex(RuntimeError,'quarantined'):child.document_source('record')
            source.write_text('Changed source')
            self.assertEqual(child.document_source('record')['state'],'pending')

    def test_followup_is_queued_and_not_reported_as_applied(self):
        with tempfile.TemporaryDirectory() as tmp:
            child=ui.Console(REPO,data_root=Path(tmp),parent=ui.Console(REPO))
            store=unittest.mock.Mock()
            with patch.object(child,'_run_dir'), patch.object(child,'collab_op',return_value={'id':'D1','state':'proposed'}) as op:
                delivery=child.queue_message('run-owned','Please challenge the date')
            self.assertEqual(delivery['state'],'queued')
            self.assertEqual(delivery['mode'],'queue')
            self.assertEqual(delivery['text'],'Please challenge the date')
            op.assert_not_called()
            with patch.object(child,'collab_op') as op:
                with self.assertRaisesRegex(ValueError,'12,000'):child.queue_message('run-owned','x'*12001)
                op.assert_not_called()

    def test_midrun_directions_wait_outside_case_record_until_resume(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'workspace').mkdir()
            child=ui.Console(REPO,data_root=root,parent=ui.Console(REPO))
            with patch.object(child,'_run_dir'), patch.object(child.jobs,'any_running',return_value=True), patch.object(child,'collab_op') as op:
                result=child.queue_message('run-owned','Challenge this premise')
            op.assert_not_called()
            self.assertEqual(result['state'],'queued')
            self.assertEqual(json.loads((root/'workspace/steering-inbox.json').read_text())[0]['text'],'Challenge this premise')
            with patch.object(child,'_run_dir'), patch.object(child,'collab_op',return_value={'id':'D1'}) as op:
                child._flush_steering('run-owned')
                child._flush_steering('run-owned')
            op.assert_not_called()  # Worker owns delivery after acquiring execution.
            self.assertEqual(json.loads((root/'workspace/steering-inbox.json').read_text())[0]['state'],'queued')

    def test_unknown_request_cannot_poison_deferred_inbox(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'workspace').mkdir()
            child=ui.Console(REPO,data_root=root,parent=ui.Console(REPO))
            with patch.object(child,'_run_dir'), patch.object(child.jobs,'any_running',return_value=True):
                with self.assertRaisesRegex(ValueError,'Unknown request'):
                    child.queue_message('run-owned','Here is my answer','missing-request')
            self.assertFalse((root/'workspace/steering-inbox.json').exists())
