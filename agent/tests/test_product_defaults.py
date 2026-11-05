"""Product launch defaults and isolation; no real configuration or network."""
from __future__ import annotations
import importlib.util
import json
import os
import tempfile
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from agent_kit import settings
ROOT = Path(__file__).resolve().parents[2]

def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

class VerificationDefaultsTest(unittest.TestCase):
    def test_new_runs_verify_without_an_environment_setting(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(settings.verification_enabled())

    def test_explicit_legacy_mode_remains_available(self):
        with patch.dict(os.environ, {"VERIFICATION": "off"}):
            self.assertFalse(settings.verification_enabled())

    def test_invalid_verification_refuses_instead_of_disabling_checks(self):
        with patch.dict(os.environ, {"VERIFICATION": "onn"}):
            with self.assertRaises(settings.ConfigError):
                settings.validate_runtime_config()

class LauncherModeTest(unittest.TestCase):
    def test_default_opens_live_console_without_starting_agent(self):
        launch = load("default_live_launch", ROOT / "scripts/launch.py")
        with patch.object(sys, "argv", ["legal-agent"]), patch.object(launch.subprocess, "call", return_value=0) as call:
            self.assertEqual(0, launch.main())
        command = call.call_args.args[0]
        self.assertNotIn("--agent-root", command)
        self.assertNotIn("LEGAL_AGENT_FAKE_MODEL", call.call_args.kwargs["env"])
        self.assertTrue(command[1].endswith("ui/ui.py"))

class DemoIsolationTest(unittest.TestCase):
    def test_demo_environment_drops_provider_credentials(self):
        launch = load("product_launch", ROOT / "scripts/launch.py")
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"MODEL_API_KEY": "synthetic-test-key", "OPENAI_API_KEY": "synthetic-other-key"}):
                env = launch.demo_environment(Path(tmp))
            self.assertNotIn("MODEL_API_KEY", env)
            self.assertNotIn("OPENAI_API_KEY", env)
            self.assertEqual("1", env["LEGAL_AGENT_FAKE_MODEL"])
            self.assertEqual(str(Path(tmp).resolve()), env["LEGAL_AGENT_ROOT"])

    def test_demo_refuses_unowned_nonempty_directory(self):
        launch = load("product_launch", ROOT / "scripts/launch.py")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "keep.txt").write_text("preserve")
            with self.assertRaises(ValueError):
                launch.prepare_demo(root)
            self.assertEqual("preserve", (root / "keep.txt").read_text())

    def test_demo_reopens_its_owned_directory(self):
        launch = load("product_launch", ROOT / "scripts/launch.py")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "demo"
            launch.prepare_demo(root)
            (root / "saved.txt").write_text("retained")
            launch.prepare_demo(root)
            self.assertEqual("retained", (root / "saved.txt").read_text())

    def test_isolated_helper_never_executes_live_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copyfile(ROOT / "agent/helper", root / "helper")
            (root / "scripts").mkdir()
            (root / "scripts/describe.py").write_text("print('isolated')")
            (root / ".env").write_text("touch leaked-secret-marker\n")
            env = dict(os.environ, LEGAL_AGENT_ROOT=str(root / "demo"), PYTHON=sys.executable)
            result = subprocess.run(["bash", "helper", "describe"], cwd=root, env=env, capture_output=True, text=True)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("isolated", result.stdout)
            self.assertFalse((root / "leaked-secret-marker").exists())

    def test_demo_core_workflow_without_site_packages(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, LEGAL_AGENT_ROOT=tmp, LEGAL_AGENT_FAKE_MODEL="1")
            def command(*args):
                result = subprocess.run([sys.executable, "-S", str(ROOT / "agent/scripts/artifact.py"), *args],
                                        env=env, capture_output=True, text=True)
                self.assertEqual(0, result.returncode, result.stderr)
                return json.loads(result.stdout)
            self.assertTrue(command("gallery")["cards"])
            run = command("demo")["run_id"]
            self.assertTrue(command("show", run, "A1"))
            self.assertTrue(command("check", run, "A1"))
            self.assertTrue(command("export", run, "A1"))

    def test_evaluation_navigation_without_site_packages(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run([sys.executable, "-S", str(ROOT / "agent/scripts/pilot.py"), "list"],
                                    env=dict(os.environ, LEGAL_AGENT_ROOT=tmp), capture_output=True, text=True)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertTrue(json.loads(result.stdout)["matters"])
