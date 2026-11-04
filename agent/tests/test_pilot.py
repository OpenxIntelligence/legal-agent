"""Pilot plumbing: matters run, verdicts deterministic, keys draft.

Proves the harness only — NOT legal correctness. Keys stay
DRAFT-SYNTHETIC until an attorney signs them; the key-status
guard fails if anyone marks them reviewed without that step.
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from eval import pilot_cases as pilot
from eval import pilot_run as runner
from scripts import pilot as script


class PilotPlumbingTest(unittest.TestCase):
    def test_keys_are_draft(self) -> None:
        self.assertIn("DRAFT", pilot.KEY_STATUS)
        self.assertIn("draft", pilot.PILOT_VERSION)

    def test_matters_well_formed(self) -> None:
        self.assertEqual(6, len(pilot.MATTERS))
        for matter in pilot.MATTERS:
            self.assertTrue(matter["id"])
            self.assertTrue(matter["issues"])
            for issue in matter["issues"]:
                self.assertIn(
                    issue["key_verdict"],
                    ("sufficient", "insufficient", "abstain"),
                )
                self.assertTrue(issue["required"])

    def test_run_deterministic(self) -> None:
        first, second = [], []
        for buf in (first, second):
            out = io.StringIO()
            with redirect_stdout(out):
                runner.main()
            buf.append(out.getvalue())
        self.assertEqual(first[0], second[0])
        report = json.loads(first[0])
        self.assertEqual(6, report["matters"])
        self.assertEqual(9, report["issues"])


class PilotReviewTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_list_pending_then_signed(self) -> None:
        listed = script.list_keys(self.root)
        self.assertEqual(6, len(listed["matters"]))
        self.assertTrue(all(
            i["state"] == "pending"
            for m in listed["matters"] for i in m["issues"]))
        entry = script.record_review(
            self.root, "pm-clean-breach", "duty", "approved",
            "Counsel", "matches text", pilot.PILOT_VERSION)
        self.assertEqual("approved", entry["decision"])
        self.assertEqual("Counsel", entry["reviewer"])
        relisted = script.list_keys(self.root)
        duty = [i for m in relisted["matters"] if m["id"] == "pm-clean-breach"
                for i in m["issues"] if i["id"] == "duty"][0]
        self.assertEqual("approved", duty["state"])
        self.assertEqual(1, len(duty["history"]))

    def test_corrupt_entries_downgraded(self) -> None:
        path = self.root / "workspace" / "pilot_reviews.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "pm-clean-breach/duty": ["junk", {"decision": "weird"}],
        }))
        listed = script.list_keys(self.root)
        duty = [i for m in listed["matters"] if m["id"] == "pm-clean-breach"
                for i in m["issues"] if i["id"] == "duty"][0]
        self.assertEqual("weird", duty["state"])
        self.assertEqual(1, len(duty["history"]))

    def test_agreement_kappa(self) -> None:
        # Hand-computed: A=[p,p,f,f], B=[p,f,f,f] -> obs .75,
        # exp .5, kappa .5.
        votes = [
            ("Adjudicator", "pm-clean-breach", "duty", "approved"),
            ("Adjudicator", "pm-clean-breach", "formality", "approved"),
            ("Adjudicator", "pm-multi-bonus", "entitlement",
             "changes_requested"),
            ("Adjudicator", "pm-version-rate", "current-rate",
             "changes_requested"),
            ("Reviewer", "pm-clean-breach", "duty", "approved"),
            ("Reviewer", "pm-clean-breach", "formality",
             "changes_requested"),
            ("Reviewer", "pm-multi-bonus", "entitlement",
             "changes_requested"),
            ("Reviewer", "pm-version-rate", "current-rate",
             "changes_requested"),
        ]
        for reviewer, matter, issue, decision in votes:
            script.record_review(
                self.root, matter, issue, decision, reviewer, "",
                pilot.PILOT_VERSION)
        report = script.agreement_report(self.root)
        self.assertEqual(["Adjudicator", "Reviewer"],
                         report["reviewers"])
        self.assertEqual(1, len(report["pairs"]))
        pair = report["pairs"][0]
        self.assertEqual(4, pair["shared"])
        self.assertEqual(0.5, pair["kappa"])
        self.assertEqual(0.75, pair["agreement"])
        self.assertEqual(["pm-clean-breach/formality"],
                         pair["disagreements"])

    def test_agreement_needs_overlap(self) -> None:
        script.record_review(
            self.root, "pm-clean-breach", "duty", "approved",
            "Solo", "", pilot.PILOT_VERSION)
        report = script.agreement_report(self.root)
        self.assertEqual([], report["pairs"])
        script.record_review(
            self.root, "pm-clean-breach", "formality", "approved",
            "Other", "", pilot.PILOT_VERSION)
        report = script.agreement_report(self.root)
        self.assertEqual(1, len(report["pairs"]))
        self.assertIsNone(report["pairs"][0]["kappa"])

    def test_stale_unknown_refused(self) -> None:
        with self.assertRaises(RuntimeError):
            script.record_review(
                self.root, "pm-clean-breach", "duty", "approved",
                "Counsel", "", "stale")
        with self.assertRaises(RuntimeError):
            script.record_review(
                self.root, "nope", "duty", "approved",
                "Counsel", "", pilot.PILOT_VERSION)
        with self.assertRaises(RuntimeError):
            script.record_review(
                self.root, "pm-clean-breach", "duty", "approved",
                "", "", pilot.PILOT_VERSION)
        self.assertFalse(
            (self.root / "workspace" / "pilot_reviews.json").exists())


if __name__ == "__main__":
    unittest.main()
