"""Key engine: resolution, fingerprints, redaction, hygiene.

Pins the industry-standard contract: env beats _FILE beats a
clear error; fingerprints identify without revealing; redaction
scrubs values from log-bound text; .env hygiene warns on loose
permissions without ever printing values. Uses synthetic secret
values only — never the operator's real keys.
"""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent_kit import secrets
from agent_kit.client import MissingKeyError, make_client

FAKE = "test-secret-value-abc123"


class ResolveTest(unittest.TestCase):
    def test_env_beats_file(self) -> None:
        with tempfile.NamedTemporaryFile("w", delete=False) as handle:
            handle.write("file-value")
            path = handle.name
        self.addCleanup(lambda: Path(path).unlink(missing_ok=True))
        env = {"T_KEY": "env-value", "T_KEY_FILE": path}
        with mock.patch.dict(os.environ, env, clear=False):
            self.assertEqual("env-value", secrets.resolve("T_KEY"))

    def test_file_indirection(self) -> None:
        with tempfile.NamedTemporaryFile("w", delete=False) as handle:
            handle.write("  file-value\n")
            path = handle.name
        self.addCleanup(lambda: Path(path).unlink(missing_ok=True))
        env = {"T_KEY_FILE": path}
        with mock.patch.dict(os.environ, env, clear=False):
            os.environ.pop("T_KEY", None)
            self.assertEqual("file-value", secrets.resolve("T_KEY"))

    def test_blank_counts_missing(self) -> None:
        with mock.patch.dict(os.environ, {"T_KEY": "  "}, clear=False):
            os.environ.pop("T_KEY_FILE", None)
            with self.assertRaises(secrets.MissingSecretError):
                secrets.resolve("T_KEY")

    def test_unreadable_file_is_clear_error(self) -> None:
        env = {"T_KEY_FILE": "/tmp/no-such-secret-file-xyz"}
        with mock.patch.dict(os.environ, env, clear=False):
            os.environ.pop("T_KEY", None)
            with self.assertRaises(secrets.MissingSecretError) as raised:
                secrets.resolve("T_KEY")
        self.assertIn("unreadable", str(raised.exception))

    def test_source_of(self) -> None:
        with mock.patch.dict(os.environ, {"T_KEY": FAKE}, clear=False):
            self.assertEqual("environment", secrets.source_of("T_KEY"))
        with mock.patch.dict(os.environ, {"T_KEY_FILE": "/x"}, clear=False):
            os.environ.pop("T_KEY", None)
            self.assertEqual("file", secrets.source_of("T_KEY"))
            os.environ.pop("T_KEY_FILE", None)
            self.assertEqual("missing", secrets.source_of("T_KEY"))


class FingerprintTest(unittest.TestCase):
    def test_stable_and_revealing_nothing(self) -> None:
        first = secrets.fingerprint(FAKE)
        self.assertEqual(first, secrets.fingerprint(FAKE))
        self.assertTrue(first.startswith("sha256:"))
        self.assertNotIn(FAKE, first)
        self.assertNotIn(FAKE[-4:], first)
        self.assertNotEqual(first, secrets.fingerprint(FAKE + "x"))


class RedactTest(unittest.TestCase):
    def test_values_scrubbed(self) -> None:
        text = f"key={FAKE} then key={FAKE} end"
        out = secrets.redact(text, [FAKE])
        self.assertNotIn(FAKE, out)
        self.assertEqual(2, out.count(secrets.REDACTED))

    def test_empty_values_ignored(self) -> None:
        self.assertEqual("abc", secrets.redact("abc", ["", FAKE]))


class EnvFileTest(unittest.TestCase):
    def _env(self, body: str, mode: int) -> str:
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".env", delete=False
        )
        with handle:
            handle.write(body)
        os.chmod(handle.name, mode)
        self.addCleanup(lambda: Path(handle.name).unlink(missing_ok=True))
        return handle.name

    def test_tight_file_clean(self) -> None:
        path = self._env("MODEL_API_KEY=x\n", 0o600)
        self.assertEqual([], secrets.check_env_file(path))

    def test_loose_permissions_warned(self) -> None:
        path = self._env("MODEL_API_KEY=x\n", 0o644)
        warnings = secrets.check_env_file(path)
        self.assertTrue(any("readable" in w for w in warnings), warnings)
        self.assertTrue(any("chmod 600" in w for w in warnings), warnings)

    def test_missing_secret_listed_by_name(self) -> None:
        path = self._env("OTHER_THING=x\n", 0o600)
        warnings = secrets.check_env_file(path)
        self.assertTrue(
            any("MODEL_API_KEY not listed" in w for w in warnings), warnings
        )

    def test_values_never_in_warnings(self) -> None:
        path = self._env(f"MODEL_API_KEY={FAKE}\n", 0o644)
        for warning in secrets.check_env_file(path):
            self.assertNotIn(FAKE, warning)

    def test_missing_file_guided(self) -> None:
        warnings = secrets.check_env_file("/tmp/no-such-env-xyz/.env")
        self.assertTrue(any(".env.example" in w for w in warnings), warnings)


class RepoHygieneTest(unittest.TestCase):
    def test_env_ignored_by_git(self) -> None:
        root = Path(__file__).resolve().parents[2]
        proc = subprocess.run(
            ["git", "check-ignore", "-q", "agent/.env"],
            cwd=root, check=False,
        )
        self.assertEqual(0, proc.returncode, "agent/.env must stay git-ignored")


class ClientWiringTest(unittest.TestCase):
    def test_missing_key_error_preserved(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MODEL_API_KEY", None)
            os.environ.pop("MODEL_API_KEY_FILE", None)
            with self.assertRaises(MissingKeyError) as raised:
                make_client()
        self.assertIn("agent/.env", str(raised.exception))

    def test_file_key_builds_client(self) -> None:
        with tempfile.NamedTemporaryFile("w", delete=False) as handle:
            handle.write("file-key")
            path = handle.name
        self.addCleanup(lambda: Path(path).unlink(missing_ok=True))
        env = {"MODEL_API_KEY_FILE": path}
        with mock.patch.dict(os.environ, env, clear=False):
            os.environ.pop("MODEL_API_KEY", None)
            client = make_client()
        self.assertEqual("file-key", client.api_key)


if __name__ == "__main__":
    unittest.main()
