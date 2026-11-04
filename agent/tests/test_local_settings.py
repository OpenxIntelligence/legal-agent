"""Settings contract with temporary fake credentials; no provider processes."""
import http.client
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

REPO = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


settings = load("local_settings_test", REPO / "ui/local_settings.py")
ui = load("local_settings_console_test", REPO / "ui/ui.py")
FAKE_KEY = "fake-test-token_123456789"


class LocalSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        (self.root / "agent").mkdir()
        self.path = self.root / "agent/.env"

    def tearDown(self):
        self.temporary.cleanup()

    def test_atomic_replace_preserves_other_lines_and_owner_only_mode(self):
        original = "# keep this\r\nMODEL_API_MODEL=custom-model\r\nMODEL_API_KEY=old\nexport MODEL_API_KEY=duplicate\nOTHER=value\n"
        self.path.write_bytes(original.encode())
        self.path.chmod(0o644)
        inode = self.path.stat().st_ino
        settings.update_key(self.path, {"api_key": FAKE_KEY})
        self.assertNotEqual(inode, self.path.stat().st_ino)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        text = self.path.read_bytes().decode()
        self.assertIn("# keep this\r\nMODEL_API_MODEL=custom-model\r\n", text)
        self.assertIn("OTHER=value\n", text)
        self.assertEqual(text.count("MODEL_API_KEY="), 1)
        status = settings.status(self.path)
        self.assertTrue(status["api_key_configured"])
        self.assertNotIn(FAKE_KEY, json.dumps(status))

    def test_remove_retains_other_content_and_no_temporary_files(self):
        self.path.write_text("MODEL_API_KEY=old-token\nOTHER=one\n")
        settings.update_key(self.path, {"remove": True})
        self.assertEqual(self.path.read_text(), "OTHER=one\n")
        self.assertFalse(settings.status(self.path)["api_key_configured"])
        self.assertEqual(list(self.path.parent.glob(".legal-agent-env-*")), [])

    def test_missing_file_is_created_without_parent_creation(self):
        self.assertFalse(settings.status(self.path)["api_key_configured"])
        settings.update_key(self.path, {"api_key": FAKE_KEY})
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_rejects_shell_characters_and_malformed_commands_without_echo(self):
        self.path.write_text("OTHER=kept\n")
        bad = ["abc$HOMEbad", "abc`id`bad", "abc;idbad", "abcdefgh\nX=1", "abc'bad'", "has spaces", "x" * 4097, "short", "a\\bcdefgh", "éabcdefghi"]
        for key in bad:
            with self.subTest(key_length=len(key)):
                with self.assertRaises(ValueError) as error:
                    settings.update_key(self.path, {"api_key": key})
                self.assertNotIn(key, str(error.exception))
                self.assertEqual(self.path.read_text(), "OTHER=kept\n")
        for body in ({"api_key": None}, {"remove": False}, {"api_key": FAKE_KEY, "remove": True}, {}):
            with self.assertRaises(ValueError):
                settings.update_key(self.path, body)

    def test_refuses_symlink_file_directory_hardlink_and_nonregular(self):
        target = self.root / "outside"
        target.write_text("OTHER=untouched\n")
        self.path.symlink_to(target)
        for operation in (lambda: settings.status(self.path), lambda: settings.update_key(self.path, {"api_key": FAKE_KEY})):
            with self.assertRaises(RuntimeError):
                operation()
        self.assertEqual(target.read_text(), "OTHER=untouched\n")
        self.path.unlink()
        os.link(target, self.path)
        with self.assertRaises(ValueError):
            settings.update_key(self.path, {"api_key": FAKE_KEY})
        self.path.unlink()
        os.mkfifo(self.path)
        with self.assertRaises(ValueError):
            settings.update_key(self.path, {"api_key": FAKE_KEY})
        link = self.root / "linked-agent"
        link.symlink_to(self.path.parent, target_is_directory=True)
        with self.assertRaises(RuntimeError):
            settings.update_key(link / ".env", {"api_key": FAKE_KEY})

    def test_replace_failure_preserves_old_file_and_cleans_temp(self):
        self.path.write_text("OTHER=kept\n")
        with patch.object(settings.os, "replace", side_effect=OSError("fake failure")):
            with self.assertRaises(RuntimeError):
                settings.update_key(self.path, {"api_key": FAKE_KEY})
        self.assertEqual(self.path.read_text(), "OTHER=kept\n")
        self.assertEqual(list(self.path.parent.glob(".legal-agent-env-*")), [])


class SettingsHTTPTests(unittest.TestCase):
    def setUp(self):
        LocalSettingsTests.setUp(self)
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.path.write_text("MODEL_API_MODEL=fake-model\nMODEL_API_BASE_URL=https://example.invalid/v1\n")
        self.console = ui.Console(None)
        self.console.workshop = self.root
        self.server = ui.ThreadingHTTPServer(("127.0.0.1", 0), ui.make_handler(self.console))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]
        self.host = f"127.0.0.1:{self.port}"
        self.origin = "http://" + self.host

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.environment.stop()
        LocalSettingsTests.tearDown(self)

    def request(self, method="GET", body=None, headers=None, path="/api/settings"):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        conn.request(method, path, body, headers or {})
        response = conn.getresponse()
        result = (response.status, json.loads(response.read()), dict(response.getheaders()))
        conn.close()
        return result

    def mutation(self, body, **overrides):
        status, result, _ = self.request()
        self.assertEqual(status, 200)
        headers = {"Origin": self.origin, "Content-Type": "application/json", "X-Legal-Agent-CSRF": result["csrf_token"]}
        headers.update(overrides)
        return self.request("POST", json.dumps(body), headers, "/api/settings/key")

    def test_status_write_readback_remove_and_execution_truth(self):
        status, result, headers = self.request()
        self.assertEqual(status, 200)
        self.assertTrue(result["can_edit"])
        self.assertFalse(result["execution"]["sandboxed"])
        self.assertEqual(result["provider_host"], "example.invalid")
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertEqual(headers["Cache-Control"], "no-store")
        status, result, _ = self.mutation({"api_key": FAKE_KEY})
        self.assertEqual(status, 200)
        self.assertTrue(result["api_key_configured"])
        self.assertNotIn(FAKE_KEY, json.dumps(result))
        self.assertTrue(self.request()[1]["api_key_configured"])
        self.assertEqual(self.mutation({"remove": True})[0], 200)
        self.assertFalse(self.request()[1]["api_key_configured"])

    def test_cross_origin_host_token_and_content_type_rejected_before_body(self):
        token = self.request()[1]["csrf_token"]
        good = {"Origin": self.origin, "Content-Type": "application/json", "X-Legal-Agent-CSRF": token}
        cases = [("Origin", "https://evil.invalid", 403), ("Origin", "null", 403), ("Host", "evil.invalid", 403),
                 ("Host", "127.0.0.1:1", 403), ("X-Legal-Agent-CSRF", "wrong", 403), ("Content-Type", "text/plain", 400)]
        with patch.object(self.server.RequestHandlerClass, "_body", side_effect=AssertionError("body must not be read")):
            for field, value, expected in cases:
                headers = dict(good, **{field: value})
                response = self.request("POST", "{}", headers, "/api/settings/key")
                self.assertEqual(response[0], expected, field)
        self.assertEqual(self.request(headers={"Host": "evil.invalid"})[0], 403)
        self.assertEqual(self.request(headers={"Origin": "https://evil.invalid"})[0], 403)

    def test_missing_origin_and_token_and_oversize_rejected(self):
        for headers in ({"Content-Type": "application/json"}, {"Origin": self.origin, "Content-Type": "application/json"}):
            self.assertEqual(self.request("POST", "{}", headers, "/api/settings/key")[0], 403)
        status, _, _ = self.mutation({"api_key": "x" * 9000})
        self.assertEqual(status, 400)

    def test_demo_and_root_child_jobs_block_mutation_before_body(self):
        token = self.request()[1]["csrf_token"]
        headers = {"Origin": self.origin, "Content-Type": "application/json", "X-Legal-Agent-CSRF": token}
        with patch.object(self.server.RequestHandlerClass, "_body", side_effect=AssertionError("body must not be read")):
            with patch.object(self.console, "_isolated", return_value=True):
                self.assertFalse(self.request()[1]["can_edit"])
                self.assertEqual(self.request("POST", "{}", headers, "/api/settings/key")[0], 403)
            with patch.object(self.console.jobs, "any_running", return_value=True):
                self.assertEqual(self.request("POST", "{}", headers, "/api/settings/key")[0], 409)
            child = Mock()
            child.jobs.any_running.return_value = True
            self.console._matter_contexts["fake"] = child
            self.assertFalse(self.request()[1]["can_edit"])
            self.assertEqual(self.request("POST", "{}", headers, "/api/settings/key")[0], 409)

    def test_removed_file_key_cannot_fall_back_to_inherited_key(self):
        os.environ["MODEL_API_KEY"] = "fake-inherited-key"
        self.assertEqual(self.console._process_env()["MODEL_API_KEY"], "")
        child = ui.Console(None, data_root=self.root / "child", parent=self.console)
        child.workshop = self.root
        self.assertEqual(child._process_env()["MODEL_API_KEY"], "")
        self.mutation({"api_key": FAKE_KEY})
        self.assertEqual(self.console._process_env()["MODEL_API_KEY"], FAKE_KEY)
        self.assertEqual(child._process_env()["MODEL_API_KEY"], FAKE_KEY)
        self.mutation({"remove": True})
        self.assertEqual(child._process_env()["MODEL_API_KEY"], "")

    def test_export_form_matches_effective_process_configuration(self):
        for prefix in ("export ", "export\t", "export   "):
            self.path.write_text(prefix + "MODEL_API_KEY='" + FAKE_KEY + "'\n" + prefix + "MODEL_API_MODEL=fake-model\n")
            self.assertTrue(self.request()[1]["api_key_configured"])
            self.assertEqual(self.console._process_env()["MODEL_API_KEY"], FAKE_KEY)

    def test_file_model_provider_defaults_override_inherited_configuration(self):
        self.path.write_text("")
        os.environ.update(MODEL_API_MODEL="inherited-model", MODEL_API_BASE_URL="https://inherited.invalid")
        status = self.request()[1]
        process = self.console._process_env()
        self.assertEqual(status["model"], process["MODEL_API_MODEL"])
        self.assertEqual(status["provider_host"], "api.meta.ai")
        self.assertEqual(process["MODEL_API_BASE_URL"], "https://api.meta.ai/v1")


if __name__ == "__main__":
    unittest.main()
