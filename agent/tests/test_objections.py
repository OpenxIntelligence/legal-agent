"""Objection ledger: raise -> disprove -> restore across reviews.

Dropped objections from adversarial reviews register as disproven
findings (deduplicated); wrongly dropped ones restore to open. No
duplicate findings for repeat reports of the same objection.
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from agent_kit.artifacts.demo import build_demo
from agent_kit.case_record import CaseError, CaseRecord
from agent_kit.progress import new_run
from scripts import objections
from tests import support


def _review(**kw):
    args = {
        "strongest_against": "x",
        "fact_risks": "x",
        "adverse_authority": "x",
        "overlooked": "x",
        "opposing_counsel": "x",
        "judge_needs": "x",
        "alternative_theory": "x",
        "reversing_fact": "x",
        "revision_needed": False,
        "revision": "no change needed",
    }
    args.update(kw)
    return args


class ObjectionLedgerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        self.run_id = new_run(self.root, instructions="i", objective="o")
        self.case = CaseRecord(self.root, self.run_id)

    def test_dropped_registers_disproven_finding(self) -> None:
        entry = self.case.record_adversarial(
            **_review(
                dropped=[
                    {
                        "objection": "Limitations ran",
                        "counter_evidence": "Tolling agreement",
                    }
                ]
            )
        )
        self.assertEqual("AR1", entry["id"])
        findings = self.case.data["objections"]
        self.assertEqual(1, len(findings))
        self.assertEqual(
            ("OBJ1", "disproven", "Tolling agreement", ["AR1"]),
            (
                findings[0]["id"],
                findings[0]["status"],
                findings[0]["counter_evidence"],
                findings[0]["reporters"],
            ),
        )

    def test_repeat_reports_dedupe_onto_one_finding(self) -> None:
        self.case.record_adversarial(
            **_review(
                dropped=[
                    {"objection": "Limitations  ran",
                     "counter_evidence": "Tolling"}
                ]
            )
        )
        self.case.record_adversarial(
            **_review(
                dropped=[
                    {"objection": "  LIMITATIONS ran ",
                     "counter_evidence": "Tolling affirmed"}
                ]
            )
        )
        findings = self.case.data["objections"]
        self.assertEqual(1, len(findings))
        self.assertEqual(["AR1", "AR2"], findings[0]["reporters"])
        self.assertEqual("Tolling affirmed", findings[0]["counter_evidence"])

    def test_raise_disprove_restore_lifecycle(self) -> None:
        raised = self.case.raise_objection("No standing", reporter="AR1")
        self.assertEqual(("OBJ1", "open"), (raised["id"], raised["status"]))
        self.assertEqual(1, len(self.case.open_objections()))
        disproven = self.case.disprove_objection(
            "OBJ1", "Assignment recorded", reporter="QC1"
        )
        self.assertEqual("disproven", disproven["status"])
        self.assertEqual([], self.case.open_objections())
        restored = self.case.restore_objection(
            "no  STANDING", "assignment was void", reporter="J1"
        )
        self.assertEqual("open", restored["status"])
        self.assertEqual(["AR1", "QC1", "J1"], restored["reporters"])
        self.assertEqual(1, len(self.case.open_objections()))
        kinds = [h["status"] for h in restored["history"]]
        self.assertEqual(["open", "disproven", "open"], kinds)

    def test_restore_rejects_open_unknown_and_empty_reason(self) -> None:
        self.case.raise_objection("Standing?")
        with self.assertRaisesRegex(CaseError, "already open"):
            self.case.restore_objection("OBJ1", "oops")
        with self.assertRaisesRegex(CaseError, "unknown objection"):
            self.case.restore_objection("OBJ9", "oops")
        self.case.disprove_objection("OBJ1", "deed")
        with self.assertRaisesRegex(CaseError, "needs the reason"):
            self.case.restore_objection("OBJ1", "  ")

    def test_empty_objection_and_empty_evidence_rejected(self) -> None:
        with self.assertRaisesRegex(CaseError, "must say what is challenged"):
            self.case.raise_objection("  ")
        with self.assertRaisesRegex(CaseError, "needs the counter-evidence"):
            self.case.disprove_objection("OBJ1", " ")

    def test_review_without_dropped_leaves_ledger_empty(self) -> None:
        self.case.record_adversarial(**_review())
        self.assertNotIn("objections", self.case.data)
        self.assertEqual([], self.case.open_objections())

    def test_ledger_survives_reopening_the_record(self) -> None:
        self.case.record_adversarial(
            **_review(
                dropped=[{"objection": "Laches", "counter_evidence": "Excuse"}]
            )
        )
        reopened = CaseRecord(self.root, self.run_id)
        self.assertEqual("OBJ1", reopened.data["objections"][0]["id"])
        self.assertEqual(
            "disproven", reopened.data["objections"][0]["status"]
        )


class ObjectionsCliCase(unittest.TestCase):
    """CLI slice: the UI posts through ./helper objections."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.run_id = build_demo(self.root)
        old = os.environ.get("LEGAL_AGENT_ROOT")
        os.environ["LEGAL_AGENT_ROOT"] = str(self.root)
        self.addCleanup(
            lambda: os.environ.__setitem__("LEGAL_AGENT_ROOT", old)
            if old is not None
            else os.environ.pop("LEGAL_AGENT_ROOT", None))

    def run_cli(self, *argv: str) -> tuple[int, dict]:
        out = io.StringIO()
        with redirect_stdout(out):
            code = objections.main([self.run_id, *argv])
        body = out.getvalue().strip()
        return code, json.loads(body) if body else {}

    def findings(self) -> list:
        return CaseRecord(self.root, self.run_id).data.get(
            "objections") or []


class ObjectionsCliTest(ObjectionsCliCase):
    def test_raise_disprove_restore(self) -> None:
        code, raised = self.run_cli(
            "raise", "--text", "R1 timing is unsupported.",
            "--reporter", "Counsel")
        self.assertEqual(0, code)
        self.assertEqual("OBJ1", raised["id"])
        self.assertEqual("open", raised["status"])
        code, dropped = self.run_cli(
            "disprove", "--ref", "OBJ1",
            "--counter", "May 6 email bounds the claim.")
        self.assertEqual(0, code)
        self.assertEqual("disproven", dropped["status"])
        code, back = self.run_cli(
            "restore", "--ref", "OBJ1",
            "--reason", "Counter-evidence misread the date.")
        self.assertEqual(0, code)
        self.assertEqual("open", back["status"])
        (finding,) = self.findings()
        self.assertEqual(
            ["open", "disproven", "open"],
            [h["status"] for h in finding["history"]])

    def test_refusals_store_nothing(self) -> None:
        code, _ = self.run_cli("raise", "--text", "   ")
        self.assertEqual(1, code)
        code, _ = self.run_cli(
            "disprove", "--ref", "OBJ9", "--counter", "x")
        self.assertEqual(1, code)
        code, raised = self.run_cli("raise", "--text", "Standing?")
        self.assertEqual(0, code)
        code, _ = self.run_cli(
            "disprove", "--ref", raised["id"], "--counter", "  ")
        self.assertEqual(1, code)
        code, _ = self.run_cli(
            "restore", "--ref", raised["id"], "--reason", "x")
        self.assertEqual(1, code)
        (finding,) = self.findings()
        self.assertEqual("open", finding["status"])
        self.assertEqual(1, len(finding["history"]))
        code, made = self.run_cli(
            "disprove", "--ref", "The totals look rounded.",
            "--counter", "Ledger shows exact cents.")
        self.assertEqual(0, code)
        self.assertEqual("disproven", made["status"])


if __name__ == "__main__":
    unittest.main()
