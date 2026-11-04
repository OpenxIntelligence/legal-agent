"""Synthetic local execution/conflict contracts; never launches a provider."""
import copy
import http.client
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
import subprocess
import shutil
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('execution_guards_ui', REPO / 'ui/ui.py')
ui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ui)


class ExecutionGuardTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / 'agent').mkdir()
        (self.root / 'agent/helper').write_text('# synthetic fixture; never executed\n')
        self.console = ui.Console(None)
        self.console.workshop = self.root
        self.key_path = self.root / 'agent/.env'

    def test_missing_key_blocks_start_and_resume_before_any_side_effect(self):
        with patch.object(self.console, '_reset_control') as reset, \
             patch.object(self.console, '_flush_steering') as flush, \
             patch.object(self.console.jobs, 'start') as launch:
            for call in (lambda: self.console.start_run(10),
                         lambda: self.console.resume_run('run-fixture', 10)):
                with self.assertRaisesRegex(RuntimeError, 'Settings'):
                    call()
            reset.assert_not_called()
            flush.assert_not_called()
            launch.assert_not_called()

    def test_inherited_key_does_not_bypass_removed_local_key(self):
        with patch.dict(os.environ, {'MODEL_API_KEY': 'fake-inherited-token'}):
            with self.assertRaisesRegex(RuntimeError, 'No model API key'):
                self.console.start_run(10)

    def test_workers_omit_ambient_credentials_and_interpreter_injection(self):
        self.key_path.write_text('MODEL_API_KEY=fake-saved-key\nUNRELATED_SECRET=fake-private\nPYTHONPATH=/untrusted\n')
        ambient = {'AWS_SECRET_ACCESS_KEY': 'fake-aws', 'GITHUB_TOKEN': 'fake-gh',
                   'OPENAI_API_KEY': 'fake-other', 'PYTHONPATH': '/untrusted',
                   'BASH_ENV': '/untrusted', 'DYLD_INSERT_LIBRARIES': '/untrusted',
                   'MODEL_API_KEY_FILE': '/untrusted', 'HOME': '/fixture-home',
                   'PATH': '/usr/bin:/bin', 'SSL_CERT_FILE': '/fixture-ca'}
        with patch.dict(os.environ, ambient):
            env = self.console._process_env()
        for name in set(ambient) - {'HOME', 'PATH', 'SSL_CERT_FILE'}:
            self.assertNotIn(name, env)
        self.assertNotIn('UNRELATED_SECRET', env)
        self.assertEqual(env['MODEL_API_KEY'], 'fake-saved-key')
        self.assertEqual(env['SSL_CERT_FILE'], '/fixture-ca')
        self.assertEqual(env['LEGAL_AGENT_ROOT'], str(self.root / 'agent'))

    def test_known_settings_and_caselaw_credentials_are_preserved(self):
        self.key_path.write_text('VERIFICATION=on\nMAX_WORKERS=2\nRUN_TOKEN_BUDGET=1000\nCOURTLISTENER_TOKEN=fake-caselaw\n')
        with patch.dict(os.environ, {'VERIFICATION': 'off', 'MAX_CONTEXT_BYTES': '4096'}):
            env = self.console._process_env()
        self.assertEqual(env['VERIFICATION'], 'on')
        self.assertEqual(env['MAX_WORKERS'], '2')
        self.assertEqual(env['RUN_TOKEN_BUDGET'], '1000')
        self.assertEqual(env['MAX_CONTEXT_BYTES'], '4096')
        self.assertEqual(env['COURTLISTENER_TOKEN'], 'fake-caselaw')

    def test_removed_saved_key_cannot_fall_back_to_inherited_key_file(self):
        with patch.dict(os.environ, {'MODEL_API_KEY': 'fake-ambient', 'MODEL_API_KEY_FILE': '/fake-key'}):
            env = self.console._process_env()
        self.assertEqual(env['MODEL_API_KEY'], '')
        self.assertNotIn('MODEL_API_KEY_FILE', env)

    def test_demo_environment_excludes_all_credentials_and_live_config(self):
        with patch.dict(os.environ, {'COURTLISTENER_TOKEN': 'fake-token', 'MAX_WORKERS': '8'}), \
             patch.object(self.console, '_isolated', return_value=True), \
             patch.object(self.console, 'case_dir', return_value=self.root / 'agent/case'), \
             patch.object(self.console, '_env', side_effect=AssertionError('must not read config')):
            env = self.console._process_env()
        self.assertNotIn('COURTLISTENER_TOKEN', env)
        self.assertNotIn('MODEL_API_KEY', env)
        self.assertNotIn('MAX_WORKERS', env)
        self.assertEqual(env['LEGAL_AGENT_FAKE_MODEL'], '1')

    def test_real_helper_does_not_execute_env_shell_content_on_legacy_launch(self):
        # Run only a disposable script with fake values, never a model request.
        helper = self.root / 'agent/helper'
        shutil.copyfile(REPO / 'agent/helper', helper)
        scripts = self.root / 'agent/scripts'
        scripts.mkdir()
        venv_python = self.root / 'agent/.venv/bin/python'
        venv_python.parent.mkdir(parents=True)
        venv_python.symlink_to(ui.sys.executable)
        (scripts / 'describe.py').write_text('import os, json; print(json.dumps({"root": os.environ["LEGAL_AGENT_ROOT"], "workers": os.environ["MAX_WORKERS"]}))\n')
        marker = self.root / 'must-not-exist'
        self.key_path.write_text(f'MAX_WORKERS=2\ntouch "{marker}"\n')
        with patch.dict(os.environ, {'PATH': '/usr/bin:/bin'}):
            env = self.console._process_env()
            self.assertEqual(env['PYTHON'], str(venv_python))
            output = subprocess.run(['/bin/bash', str(helper), 'describe'],
                                    cwd=helper.parent, env=env,
                                    text=True, capture_output=True, timeout=10, check=True)
        self.assertFalse(marker.exists())
        self.assertEqual(json.loads(output.stdout), {'root': str(helper.parent), 'workers': '2'})

    def test_execution_status_reports_limits_without_secret_values(self):
        self.key_path.write_text('MODEL_API_KEY=fake-private-test-key\n')
        self.key_path.chmod(0o600)
        status = self.console.settings_status('fake-csrf')
        self.assertEqual(status['execution']['environment_policy'], 'allowlist-v1')
        self.assertFalse(status['execution']['sandboxed'])
        self.assertFalse(status['execution']['external_databases'])
        self.assertFalse(status['execution']['telemetry_export'])
        self.assertNotIn('fake-private-test-key', json.dumps(status))

    def test_configured_start_reaches_mock_process_with_only_fake_key(self):
        self.key_path.write_text('MODEL_API_KEY=fake-test-token\n')
        with patch.object(self.console, '_reset_control'), \
             patch.object(self.console.jobs, 'start', return_value={'id': 1}) as launch:
            self.assertEqual(self.console.start_run(10), {'id': 1})
            self.assertEqual(launch.call_args.kwargs['env']['MODEL_API_KEY'], 'fake-test-token')

    def test_matter_execution_status_and_demo_saving(self):
        matter = self.console.matter_store().create({'question': 'Fictional question for testing.'})
        missing = self.console.matter_detail(matter['id'])
        self.assertFalse(missing['can_run'])
        self.assertFalse(missing['api_key_configured'])
        self.assertEqual(missing['execution_reason'], 'model_key_missing')
        self.key_path.write_text('MODEL_API_KEY=fake-test-token\n')
        configured = self.console.matter_detail(matter['id'])
        self.assertTrue(configured['can_run'])
        self.assertTrue(configured['api_key_configured'])
        self.assertNotIn('fake-test-token', json.dumps(configured))
        with patch.object(self.console, '_isolated', return_value=True):
            saved = self.console.matter_store().message(matter['id'], 'Synthetic extra context.')
            demo = self.console.matter_detail(saved['id'])
            self.assertFalse(demo['can_run'])
            self.assertEqual(demo['execution_reason'], 'demo')
            self.assertEqual(saved['messages'][-2]['body'], 'Synthetic extra context.')

    def start_server(self):
        server = ui.ThreadingHTTPServer(('127.0.0.1', 0), ui.make_handler(self.console))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def cleanup():
            server.shutdown()
            server.server_close()
            thread.join()
        self.addCleanup(cleanup)
        return server.server_address[1]

    def post(self, port, path, body):
        conn = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
        conn.request('POST', path, json.dumps(body), {'Content-Type': 'application/json'})
        response = conn.getresponse()
        result = response.status, json.loads(response.read())
        conn.close()
        return result

    def test_missing_key_http_409_includes_actionable_recovery(self):
        with patch.object(self.console.jobs, 'start') as launch:
            status, response = self.post(self.start_server(), '/api/runs', {'turns': 1})
            self.assertEqual(status, 409)
            self.assertIn('Settings', response['error'])
            launch.assert_not_called()

    def test_stale_claim_http_409_preserves_latest_assertion(self):
        run_id = 'run-fixture'
        (self.console.runs / run_id).mkdir(parents=True)
        instance = {'id': 'audit', 'rows': [{'row_id': 'row-1', 'assertion_version': 1,
                                            'assertion': 'First synthetic assertion'}]}
        calls = []
        def artifact(argv):
            if argv[0] == 'show':
                return {'instance': copy.deepcopy(instance)}
            calls.append(argv)
            instance['rows'][0]['assertion'] = argv[argv.index('--assertion') + 1]
            instance['rows'][0]['assertion_version'] += 1
            return {'instance': copy.deepcopy(instance)}
        port = self.start_server()
        endpoint = f'/api/runs/{run_id}/artifacts/audit/revise'
        with patch.object(self.console, '_artifact', side_effect=artifact):
            first = self.post(port, endpoint, {'row': 'row-1', 'assertion': 'Tab B edit',
                                               'expected_assertion_version': 1})
            stale = self.post(port, endpoint, {'row': 'row-1', 'assertion': 'Stale tab A edit',
                                               'expected_assertion_version': 1})
            self.assertEqual(first[0], 200)
            self.assertEqual(stale[0], 409)
            self.assertIn('changed since', stale[1]['error'])
            self.assertEqual(instance['rows'][0]['assertion'], 'Tab B edit')
            self.assertEqual(len(calls), 1)
            # Deliberate reload/reconciliation can save against the new version.
            latest = self.post(port, endpoint, {'row': 'row-1', 'assertion': 'Reconciled edit',
                                                'expected_assertion_version': 2})
            self.assertEqual(latest[0], 200)
            self.assertEqual(instance['rows'][0]['assertion_version'], 3)

    def test_invalid_version_rejected_and_legacy_request_remains_supported(self):
        with patch.object(self.console, 'artifact_show') as show, \
             patch.object(self.console, '_artifact_revise', return_value={'saved': True}) as revise:
            for bad in (None, True, 0, -1, '1', 1.0):
                with self.assertRaises(ValueError):
                    self.console.artifact_revise('run-fixture', 'audit',
                                                {'row': 'row-1', 'expected_assertion_version': bad})
            show.assert_not_called()
            revise.assert_not_called()
            self.assertEqual(self.console.artifact_revise('run-fixture', 'audit', {'row': 'row-1'}),
                             {'saved': True})

    def test_simultaneous_claim_saves_accept_only_one_base_version(self):
        version = [1]
        barrier = threading.Barrier(3)
        outcomes = []
        def show(*args):
            return {'instance': {'rows': [{'row_id': 'row-1', 'assertion_version': version[0]}]}}
        def revise(*args):
            version[0] += 1
            return {'saved': True}
        def save():
            barrier.wait()
            try:
                self.console.artifact_revise('run-fixture', 'audit',
                    {'row': 'row-1', 'assertion': 'Synthetic edit', 'expected_assertion_version': 1})
                outcomes.append('saved')
            except RuntimeError:
                outcomes.append('conflict')
        with patch.object(self.console, 'artifact_show', side_effect=show), \
             patch.object(self.console, '_artifact_revise', side_effect=revise):
            threads = [threading.Thread(target=save) for _ in range(2)]
            for thread in threads:
                thread.start()
            barrier.wait()
            for thread in threads:
                thread.join(timeout=3)
                self.assertFalse(thread.is_alive())
        self.assertCountEqual(outcomes, ['saved', 'conflict'])
        self.assertEqual(version[0], 2)


if __name__ == '__main__':
    unittest.main()
