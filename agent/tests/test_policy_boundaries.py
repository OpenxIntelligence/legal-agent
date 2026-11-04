"""Policy-pin boundary conditions (closeout §7).

Two demonstrated defects in the peer-owned pin/gate code:

1. Malformed pin falls back silently: a non-dict (or
   task_type-less) assessment_policy makes
   _check_required_classes use the LIVE class table with no
   pin problem reported. Required: fail closed with an
   explicit pin problem.

2. Gate-drift deadlock: a pin under an older gate version
   makes run_gate demand reclassification, but
   reclassify_task_type to the SAME type returns the pin
   unchanged — so drift is unresolvable without inventing
   a different task type. Required: same-type
   reclassification re-pins under the current gate (or an
   explicit revalidation path).

Both marked expectedFailure until the peer fix lands.
Related handoff (design-level, no test): new runs record
no policy lineage, so legacy-by-omission is undetectable;
reclassification needs no authorized scope decision and
stales decompositions only (memo checks, approvals, and
orchestration policy untouched).
"""

from __future__ import annotations

import unittest

from agent_kit import reasoning
from agent_kit.case_record import CaseRecord
from agent_kit.progress import new_run
from tests import support


class PolicyBoundaryTest(unittest.TestCase):
    def _answers(self, task_type):
        return [
            {"id": f"F{i + 1}", "class": c, "question": f"q {c}?",
             "criterion": f"resolve {c}", "status": "answered"}
            for i, c in enumerate(
                reasoning.REQUIRED_CLASSES[task_type])
        ]

    def test_malformed_pin_fails_closed(self) -> None:
        data = {"task_type": "investigation",
                "foundational": self._answers("investigation"),
                "assessment_policy": "garbage-not-a-dict"}
        out = reasoning.run_gate(data, {"findings": []})
        pin_problems = [p for p in out["problems"]
                        if "pin" in p.lower() or "policy" in p.lower()]
        self.assertTrue(
            pin_problems,
            "malformed pin silently fell back to the live class table",
        )

    def test_same_type_reclassification_repins_after_drift(self) -> None:
        root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        run_id = new_run(root, instructions="i", objective="o",
                         verification=True)
        case = CaseRecord(root, run_id)
        case.data["task_type"] = "investigation"
        case.data["assessment_policy"] = {
            "task_type": "investigation", "gate": "reasoning-v0",
            "required_classes": list(
                reasoning.REQUIRED_CLASSES["investigation"]),
            "pinned_at": "2026-01-01T00:00:00+00:00",
            "basis": "task_type_set",
        }
        pin = case.reclassify_task_type("investigation",
                                        "version refresh")
        self.assertEqual(
            reasoning.GATE_VERSION, pin["gate"],
            "same-type reclassification must re-pin under the live "
            "gate; drift is otherwise unresolvable",
        )


if __name__ == "__main__":
    unittest.main()
