"""NV-1 review contracts: binding, origin, exposure, blinding, boundary.

Labels are reviewed judgments, not annotations: they bind to the
exact row state seen, carry a typed origin and exposure, and are
append-only. Simulated test actors must never pass as genuine
human review. Runtime tools must never submit labels.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from agent_kit import review_labels as labels
from agent_kit.case_record import CaseError


def _instance() -> dict:
    return {
        "instance_id": "A1",
        "versions": [{"n": 1}],
        "rows": [
            {
                "row_id": "R1",
                "assertion": "Delivery is due on June 1.",
                "citations": [
                    {"document_id": "EVAL-CONTRACT", "page": 1,
                     "quote": "Delivery is due on June 1."},
                ],
            },
        ],
    }


def _record(inst: dict, **over) -> dict:
    args = {
        "row_id": "R1",
        "verdict": "supported",
        "rationale": "quote matches the assertion",
        "actor": "sim:r1",
        "origin": "simulated",
        "exposure": "blinded",
    }
    args.update(over)
    return labels.record_label(inst, **args)


class RecordTest(unittest.TestCase):
    def test_binds_row_state(self) -> None:
        inst = _instance()
        label = _record(inst)
        self.assertEqual("L1", label["id"])
        self.assertEqual("Delivery is due on June 1.", label["claim"])
        self.assertEqual(
            [{"document_id": "EVAL-CONTRACT", "page": 1,
              "quote": "Delivery is due on June 1."}],
            label["citations"])
        self.assertEqual(1, label["instance_version"])
        self.assertEqual("blinded", label["exposure"])
        self.assertIsNone(label["memo_use"])
        second = _record(inst, actor="sim:r2")
        self.assertEqual("L2", second["id"])

    def test_rejects_bad_enums_and_blanks(self) -> None:
        inst = _instance()
        with self.assertRaises(CaseError):
            _record(inst, verdict="maybe")
        with self.assertRaises(CaseError):
            _record(inst, origin="expert")
        with self.assertRaises(CaseError):
            _record(inst, exposure="skimmed")
        with self.assertRaises(CaseError):
            _record(inst, rationale="  ")
        with self.assertRaises(CaseError):
            _record(inst, actor=" ")
        with self.assertRaises(CaseError):
            _record(inst, row_id="RX")

    def test_origin_actor_rule(self) -> None:
        inst = _instance()
        with self.assertRaises(CaseError):
            _record(inst, origin="simulated", actor="Counsel")
        with self.assertRaises(CaseError):
            _record(inst, origin="human", actor="sim:r1")
        human = _record(inst, origin="human", actor="Counsel",
                        exposure="assisted")
        self.assertEqual("human", human["origin"])

    def test_memo_use_validated_and_separate(self) -> None:
        inst = _instance()
        with self.assertRaises(CaseError):
            _record(inst, memo_use={"decision": "approve"})
        with self.assertRaises(CaseError):
            _record(inst, memo_use="accept")
        label = _record(inst, verdict="insufficient",
                        memo_use={"decision": "conditional",
                                  "qualifications": "narrow the date"})
        self.assertEqual("insufficient", label["verdict"])
        self.assertEqual("conditional", label["memo_use"]["decision"])

    def test_append_only_no_reclassification_api(self) -> None:
        inst = _instance()
        first = _record(inst, exposure="assisted")
        before = json.loads(json.dumps(first))
        _record(inst, actor="sim:r2", exposure="blinded")
        self.assertEqual(before, inst["review_labels"][0])
        public = {name for name in labels.__all__
                  if callable(getattr(labels, name))}
        self.assertEqual(
            {"record_label", "adjudicate", "label_status",
             "label_fresh", "blinded_assignment",
             "issue_assignment", "submit_assignment_label"},
            public,
        )


class AdjudicationTest(unittest.TestCase):
    def test_pending_submitted_unresolved_adjudicated(self) -> None:
        inst = _instance()
        self.assertEqual(
            "pending", labels.label_status(inst, "R1")["state"])
        _record(inst)
        status = labels.label_status(inst, "R1")
        self.assertEqual("submitted", status["state"])
        self.assertEqual(["supported"], status["verdicts"])
        _record(inst, actor="sim:r2", verdict="insufficient",
                rationale="page says nothing about duty")
        status = labels.label_status(inst, "R1")
        self.assertEqual("unresolved", status["state"])
        self.assertEqual(["insufficient", "supported"],
                         status["verdicts"])
        entry = labels.adjudicate(
            inst, row_id="R1", decision="corrected",
            rationale="second reviewer is right",
            actor="sim:arbiter")
        self.assertEqual(["L1", "L2"], entry["labels"])
        status = labels.label_status(inst, "R1")
        self.assertEqual("adjudicated", status["state"])
        self.assertEqual("corrected",
                         status["adjudication"]["decision"])

    def test_adjudication_needs_labels_and_reasons(self) -> None:
        inst = _instance()
        with self.assertRaises(CaseError):
            labels.adjudicate(
                inst, row_id="R1", decision="upheld",
                rationale="fine", actor="sim:arbiter")
        _record(inst)
        with self.assertRaises(CaseError):
            labels.adjudicate(
                inst, row_id="R1", decision="split",
                rationale="fine", actor="sim:arbiter")
        with self.assertRaises(CaseError):
            labels.adjudicate(
                inst, row_id="R1", decision="upheld",
                rationale=" ", actor="sim:arbiter")
        with self.assertRaises(CaseError):
            labels.adjudicate(
                inst, row_id="R1", decision="upheld",
                rationale="fine", actor=" ")


class FreshnessTest(unittest.TestCase):
    def test_stale_on_claim_citation_version_change(self) -> None:
        inst = _instance()
        row = inst["rows"][0]
        label = _record(inst)
        self.assertTrue(labels.label_fresh(label, inst, row))
        row["assertion"] = "Delivery is due on June 2."
        self.assertFalse(labels.label_fresh(label, inst, row))
        row["assertion"] = label["claim"]
        self.assertTrue(labels.label_fresh(label, inst, row))
        row["citations"][0]["page"] = 2
        self.assertFalse(labels.label_fresh(label, inst, row))
        row["citations"][0]["page"] = 1
        inst["versions"].append({"n": 2})
        self.assertFalse(labels.label_fresh(label, inst, row))
        self.assertFalse(labels.label_fresh(
            label, inst, {"row_id": "R2"}))


class BlindingTest(unittest.TestCase):
    FORBIDDEN = ("checks", "review_labels", "adversarial", "panel",
                 "verdict", "labels", "adjudication", "semantic",
                 "structural", "review", "marks")

    def test_assignment_carries_claim_and_evidence_only(self) -> None:
        inst = _instance()
        inst["rows"][0]["checks"] = {
            "structural": {"outcome": "passed"}}
        _record(inst)
        assignment = labels.blinded_assignment(inst, {"docs": {}}, "R1")
        self.assertEqual("R1", assignment["row_id"])
        self.assertEqual("Delivery is due on June 1.",
                         assignment["claim"])
        self.assertEqual(1, len(assignment["citations"]))
        blob = json.dumps(assignment)
        for key in self.FORBIDDEN:
            self.assertNotIn(f'"{key}"', blob)
        with self.assertRaises(CaseError):
            labels.blinded_assignment(inst, {}, "RX")


class AssignmentTest(unittest.TestCase):
    def test_issue_submit_and_burn(self) -> None:
        inst = _instance()
        assignment = labels.issue_assignment(inst, {"docs": {}}, "R1")
        self.assertEqual("R1", assignment["row_id"])
        self.assertIn("token", assignment)
        self.assertNotIn("checks", json.dumps(assignment))
        label = labels.submit_assignment_label(
            inst, token=assignment["token"],
            verdict="insufficient", rationale="timing overreach",
            actor="sim:r1", origin="simulated", missing_evidence=True)
        self.assertEqual("blinded", label["exposure"])
        self.assertTrue(label["missing_evidence"])
        with self.assertRaises(CaseError):
            labels.submit_assignment_label(
                inst, token=assignment["token"],
                verdict="supported", rationale="again",
                actor="sim:r2", origin="simulated")
        with self.assertRaises(CaseError):
            labels.submit_assignment_label(
                inst, token="nope", verdict="supported",
                rationale="x", actor="sim:r2", origin="simulated")

    def test_stale_version_and_origin_rule(self) -> None:
        inst = _instance()
        assignment = labels.issue_assignment(inst, {}, "R1")
        inst["versions"].append({"n": 2})
        with self.assertRaises(CaseError):
            labels.submit_assignment_label(
                inst, token=assignment["token"],
                verdict="supported", rationale="x",
                actor="sim:r1", origin="simulated")
        fresh = labels.issue_assignment(inst, {}, "R1")
        with self.assertRaises(CaseError):
            labels.submit_assignment_label(
                inst, token=fresh["token"],
                verdict="supported", rationale="x",
                actor="Counsel", origin="simulated")
        with self.assertRaises(CaseError):
            labels.issue_assignment(inst, {}, "RX")


class BoundaryTest(unittest.TestCase):
    def test_no_runtime_tool_touches_labels(self) -> None:
        tools_dir = (
            Path(__file__).resolve().parents[1]
            / "agent_kit" / "tools"
        )
        offenders = []
        for path in sorted(tools_dir.glob("*.py")):
            text = path.read_text()
            if "review_labels" in text or "record_label" in text:
                offenders.append(path.name)
        self.assertEqual([], offenders)


if __name__ == "__main__":
    unittest.main()
