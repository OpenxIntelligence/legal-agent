"""Evidence-needs set path: freeze one item's obligations via CLI.

Complements the read-only needs display: the set subcommand
parses 'label @ DOC:1' lines, freezes them through the same
case-record validation (re-freezes keep history), and refuses
unknown items, blank lines, and malformed pairs loudly.
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from agent_kit.case_record import CaseRecord
from scripts import needs
from tests import support


class NeedsSetCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.run_id = "run-needs-cli"
        case = CaseRecord(self.root, self.run_id)
        case.set_checklist([support.checklist_item("item_x")])
        old = os.environ.get("LEGAL_AGENT_ROOT")
        os.environ["LEGAL_AGENT_ROOT"] = str(self.root)
        self.addCleanup(
            lambda: os.environ.__setitem__("LEGAL_AGENT_ROOT", old)
            if old is not None
            else os.environ.pop("LEGAL_AGENT_ROOT", None))

    def run_cli(self, *argv: str) -> tuple[int, dict]:
        out = io.StringIO()
        with redirect_stdout(out):
            code = needs.main([self.run_id, *argv])
        body = out.getvalue().strip()
        return code, json.loads(body) if body else {}


class NeedsSetTest(NeedsSetCase):
    def test_set_and_refreeze_keep_history(self) -> None:
        code, first = self.run_cli(
            "set", "--item", "item_x",
            "--required", "duty @ LEDGER:1",
            "--required", "breach")
        self.assertEqual(0, code)
        self.assertEqual("item_x", first["item"])
        self.assertEqual(
            [["LEDGER", 1]],
            first["required"][0]["passages"])
        code, second = self.run_cli(
            "set", "--item", "item_x",
            "--required", "duty @ LEDGER:1")
        self.assertEqual(0, code)
        stored = (CaseRecord(self.root, self.run_id)
                  .data["evidence_needs"]["item_x"])
        self.assertEqual(1, len(stored["history"]))
        self.assertEqual(2, len(stored["history"][0]["required"]))

    def test_refusals(self) -> None:
        code, _ = self.run_cli(
            "set", "--item", "item_nope",
            "--required", "duty @ LEDGER:1")
        self.assertEqual(1, code)
        code, _ = self.run_cli("set", "--item", "item_x")
        self.assertEqual(1, code)
        code, _ = self.run_cli(
            "set", "--item", "item_x", "--required", "   ")
        self.assertEqual(1, code)
        code, _ = self.run_cli(
            "set", "--item", "item_x", "--required", "duty @ LEDGER")
        self.assertEqual(1, code)
        stored = (CaseRecord(self.root, self.run_id)
                  .data.get("evidence_needs") or {})
        self.assertEqual({}, stored)


class NeedsAbstainTest(NeedsSetCase):
    def test_known_absent_item_reads_abstain(self) -> None:
        code, _ = self.run_cli(
            "set", "--item", "item_x",
            "--required", "insurer")
        self.assertEqual(0, code)
        code, shown = self.run_cli()
        self.assertEqual(0, code)
        row = next(i for i in shown["items"]
                   if i["item"] == "item_x")
        self.assertEqual("abstain", row["state"])
        self.assertTrue(row["abstain"])

    def test_unretrieved_evidence_stays_insufficient(self) -> None:
        code, _ = self.run_cli(
            "set", "--item", "item_x",
            "--required", "duty @ LEDGER:1")
        self.assertEqual(0, code)
        code, shown = self.run_cli()
        self.assertEqual(0, code)
        row = next(i for i in shown["items"]
                   if i["item"] == "item_x")
        self.assertEqual("insufficient", row["state"])
        self.assertFalse(row["abstain"])


if __name__ == "__main__":
    unittest.main()
