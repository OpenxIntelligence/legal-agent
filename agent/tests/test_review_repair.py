"""NV-2 review-to-repair chain through the real audit path.

Temporal support: label an overreaching timing inference, raise
a source-linked issue, qualify the claim, reassess — while the
original timing question stays visibly unresolved. A second
fixture proves a correct claim survives an irrelevant objection
without an invented repair. Judges are scripted; nothing here
measures live model quality.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent_kit import artifacts, review_labels, tools
from agent_kit.case_record import CaseError
from eval import review_cases
from tests import support


def _audit(root, run_id, record, instance, unsupported):
    def mark(text: str) -> list:
        return [
            {"unit": unit, "reason": "scripted: fixture truth"}
            for unit in unsupported
        ]

    fake = support.scripted(root, [], unsupported=mark)
    ctx = tools.Context(fake.client, root, run_id, record, True)
    rows = instance["rows"]
    structural = artifacts.audit_structural(root, record.data, rows)
    semantic = artifacts.audit_support(ctx, rows, structural)
    changed = artifacts.record_checks(
        instance, record.data, root, structural, semantic
    )
    record.artifact_save(instance, "artifact_checked", "")
    return changed


def _outcome(row, layer):
    return (row.get("checks") or {}).get(layer, {}).get("outcome")


class ReviewCase(unittest.TestCase):
    def materialize(self, name):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        run_id, record, instance = review_cases.materialize(root, name)
        return root, run_id, record, instance


class TemporalChainTest(ReviewCase):
    def test_label_issue_repair_reassess(self) -> None:
        case = review_cases.CASES["review-temporal-support"]
        root, run_id, record, instance = self.materialize(
            "review-temporal-support")
        original = "Reyes knew of the shortfall before the May 5 approval."

        _audit(root, run_id, record, instance, case["unsupported_before"])
        rows = {r["row_id"]: r for r in instance["rows"]}
        self.assertEqual("passed", _outcome(rows["R1"], "structural"))
        self.assertEqual("failed", _outcome(rows["R1"], "semantic"))

        first = review_labels.record_label(
            instance, row_id="R1", verdict="insufficient",
            rationale="May 6 source cannot show pre-May 5 knowledge",
            actor="sim:r1", origin="simulated", exposure="blinded",
            missing_evidence=True)
        second = review_labels.record_label(
            instance, row_id="R1", verdict="insufficient",
            rationale="no passage predates the approval",
            actor="sim:r2", origin="simulated", exposure="blinded",
            missing_evidence=True)
        issue = record.raise_objection(
            "R1 overstates timing: REVIEW-EMAIL p1 establishes "
            "knowledge May 6, not before the May 5 approval "
            f"(labels {first['id']}/{second['id']})",
            reporter="sim:r1")
        self.assertEqual("open", issue["status"])
        self.assertIn("sim:r1", issue["reporters"])

        revised = artifacts.revise_row(
            instance, record.data, root, "R1",
            assertion=case["repaired_assertion"])
        record.artifact_save(instance, "artifact_revised", "R1")
        self.assertEqual(2, len(instance["versions"]))
        self.assertEqual("pending", instance["review"]["decision"])
        self.assertEqual(
            original,
            instance["versions"][0]["rows"][0]["assertion"])

        _audit(root, run_id, record, instance, case["unsupported_after"])
        rows = {r["row_id"]: r for r in instance["rows"]}
        self.assertEqual("passed", _outcome(rows["R1"], "semantic"))
        self.assertEqual("passed", _outcome(rows["RC"], "semantic"))
        self.assertFalse(review_labels.label_fresh(
            first, instance, rows["R1"]))

        review_labels.record_label(
            instance, row_id="R1", verdict="supported",
            rationale="qualified wording matches the May 6 source",
            actor="sim:r1", origin="simulated", exposure="blinded")
        entry = review_labels.adjudicate(
            instance, row_id="R1", decision="corrected",
            rationale="revision narrowed the claim to what the "
            "source shows", actor="sim:arbiter")
        self.assertEqual(["L1", "L2", "L3"], entry["labels"])

        findings = record.data.get("objections") or []
        self.assertEqual("open", findings[0]["status"])
        self.assertIn("May 5 approval", findings[0]["objection"])
        memo = record.data.get("memo") or {}
        self.assertIn("unresolved", memo.get("gaps") or "")
        self.assertEqual(
            "adjudicated",
            review_labels.label_status(instance, "R1")["state"])
        changes = [h.get("change") for h in revised["history"]]
        self.assertIn("revised-assertion", changes)


class IrrelevantObjectionTest(ReviewCase):
    def test_correct_claim_survives(self) -> None:
        case = review_cases.CASES["review-irrelevant-objection"]
        root, run_id, record, instance = self.materialize(
            "review-irrelevant-objection")
        _audit(root, run_id, record, instance, case["unsupported_before"])
        rows = {r["row_id"]: r for r in instance["rows"]}
        self.assertEqual("passed", _outcome(rows["R1"], "structural"))
        self.assertEqual("passed", _outcome(rows["R1"], "semantic"))

        review_labels.record_label(
            instance, row_id="R1", verdict="supported",
            rationale="quote states the total exactly",
            actor="sim:r1", origin="simulated", exposure="blinded")
        review_labels.record_label(
            instance, row_id="R1", verdict="supported",
            rationale="ledger page confirms",
            actor="sim:r2", origin="simulated", exposure="blinded")
        record.raise_objection(
            "The ledger typeface is unusual and hard to read.",
            reporter="sim:r2")
        with self.assertRaises(CaseError):
            record.disprove_objection("OBJ1", "  ", reporter="sim:r1")
        dropped = record.disprove_objection(
            "OBJ1", "Typeface has no bearing on the stated total.",
            reporter="sim:r1")
        self.assertEqual("disproven", dropped["status"])
        self.assertEqual(
            "Typeface has no bearing on the stated total.",
            dropped["counter_evidence"])

        self.assertEqual(1, len(instance["versions"]))
        events = [h.get("event") for h in instance.get("history") or []]
        self.assertNotIn("revised", events)
        rows = {r["row_id"]: r for r in instance["rows"]}
        self.assertEqual("passed", _outcome(rows["R1"], "semantic"))
        for label in instance["review_labels"]:
            self.assertTrue(review_labels.label_fresh(
                label, instance, rows["R1"]))


class ObligationPreservedTest(ReviewCase):
    def test_labels_and_unrelated_edits_keep_issue_open(self) -> None:
        root, run_id, record, instance = self.materialize(
            "review-temporal-support")
        memo_before = json.dumps(record.data.get("memo"), sort_keys=True)
        rows_before = json.dumps(
            [(r["row_id"], r.get("assertion")) for r in instance["rows"]])
        versions_before = len(instance["versions"])
        review_labels.record_label(
            instance, row_id="R1", verdict="insufficient",
            rationale="timing overreach", actor="sim:r1",
            origin="simulated", exposure="blinded")
        review_labels.adjudicate(
            instance, row_id="R1", decision="unresolved",
            rationale="awaiting second reviewer", actor="sim:arbiter")
        self.assertEqual(
            memo_before, json.dumps(record.data.get("memo"), sort_keys=True))
        self.assertEqual(
            rows_before,
            json.dumps([(r["row_id"], r.get("assertion"))
                        for r in instance["rows"]]))
        self.assertEqual(versions_before, len(instance["versions"]))

        record.raise_objection("R1 timing is unsupported.",
                               reporter="sim:r1")
        artifacts.revise_row(
            instance, record.data, root, "RC",
            assertion="The approval stands; timing qualified.")
        findings = record.data.get("objections") or []
        self.assertEqual("open", findings[0]["status"])


if __name__ == "__main__":
    unittest.main()
