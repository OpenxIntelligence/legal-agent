"""Shared normalization contract tests (closeout §5).

Locks the textnorm v1 contract and proves the canonical
`document_text.normalize` cannot drift from it (same
function object). Import-free modules may depend on
`agent_kit.textnorm` without pulling the model client.
"""

from __future__ import annotations

import unittest

from agent_kit import document_text, textnorm


class TextnormContractTest(unittest.TestCase):
    def test_versioned(self) -> None:
        self.assertEqual("textnorm/v1", textnorm.NORMALIZER_VERSION)

    def test_no_drift_from_canonical(self) -> None:
        self.assertIs(document_text.normalize, textnorm.normalize)

    def test_curly_quotes_fold(self) -> None:
        self.assertEqual(
            '"ready" - \'go\'',
            textnorm.normalize("\u201cready\u201d — \u2018go\u2019"),
        )

    def test_dashes_and_nbsp_fold(self) -> None:
        self.assertEqual(
            "a-b - c d",
            textnorm.normalize("a\u2013b \u2014 c\u00a0d"),
        )

    def test_whitespace_case(self) -> None:
        self.assertEqual("mr lindgren", textnorm.normalize("  Mr\tLindgren\n"))

    def test_empty_and_none(self) -> None:
        self.assertEqual("", textnorm.normalize(""))
        self.assertEqual("", textnorm.normalize(None))

    def test_dependency_safe(self) -> None:
        import json
        import subprocess
        import sys
        from pathlib import Path

        probe = (
            "import json, sys; sys.path.insert(0, %r); "
            "import agent_kit.textnorm; "
            "print(json.dumps(sorted(sys.modules)))"
        ) % str(Path(__file__).resolve().parents[1])
        out = subprocess.run(
            [sys.executable, "-c", probe], capture_output=True, text=True,
            timeout=60,
        )
        self.assertEqual("", out.stderr, out.stderr[-500:])
        self.assertEqual(0, out.returncode)
        loaded = json.loads(out.stdout)
        self.assertIn("agent_kit.textnorm", loaded)
        self.assertNotIn("agent_kit.document_text", loaded)
        self.assertNotIn("openai", loaded)
        self.assertEqual(
            {"agent_kit", "agent_kit.textnorm"},
            {m for m in loaded if m.split(".")[0] == "agent_kit"},
        )


if __name__ == "__main__":
    unittest.main()
