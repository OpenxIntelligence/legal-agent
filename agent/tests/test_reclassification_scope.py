"""Reclassification scope and policy lineage (closeout §7.3/§7.4/§7.1).

1. Model-actor material reduction: a model call
   litigation(12 classes) -> investigation(6) is refused: a
   model actor may not narrow the assessed scope. The operator
   path still allows it with a recorded basis, and model moves
   that preserve or widen the class set still succeed.

2. Approval staleness: reclassification stales prior grants
   (decision + stale marker + reason), and approval_status
   reports them stale instead of granted until the operator
   revalidates. Denials and pending requests are untouched.

3. Legacy by omission: a new-run-shaped case ('created'
   present) carrying memo findings but no task type,
   foundational answers, or policy fails closed demanding
   identification. Runs carrying an operator-approved review
   keep their identified historical policy; legacy runs with
   no findings still pass.
"""

from __future__ import annotations

import unittest

from agent_kit import reasoning, tools
from agent_kit.case_record import CaseRecord
from agent_kit.progress import new_run
from agent_kit.tools import reclassify_task
from tests import support


def _litigation_case(test: unittest.TestCase):
    root, tmp = support.make_root()
    test.addCleanup(tmp.cleanup)
    run_id = new_run(root, instructions="i", objective="o",
                     verification=True)
    case = CaseRecord(root, run_id)
    case.data["task_type"] = "litigation"
    case.data["assessment_policy"] = {
        "task_type": "litigation", "gate": reasoning.GATE_VERSION,
        "required_classes": list(
            reasoning.REQUIRED_CLASSES["litigation"]),
        "pinned_at": "2026-01-01T00:00:00+00:00",
        "basis": "task_type_set",
    }
    return root, run_id, case


def _ctx(root, run_id, case, actor="model"):
    fake = support.scripted(root, [])
    return tools.Context(fake.client, root, run_id, case, True,
                         actor=actor)


class ModelActorScopeTest(unittest.TestCase):
    def test_model_material_reduction_refused(self) -> None:
        root, run_id, case = _litigation_case(self)
        ctx = _ctx(root, run_id, case)
        self.assertEqual("model", ctx.actor)
        try:
            out = reclassify_task.run(
                ctx, {"task_type": "investigation",
                      "basis": "looks simpler"})
        except Exception:
            out = {"error": "raised"}
        self.assertIn("error", out)
        self.assertIn("may not narrow", out["error"])
        self.assertEqual("litigation",
                         case.data.get("task_type"))

    def test_operator_narrowing_allowed_with_basis(self) -> None:
        root, run_id, case = _litigation_case(self)
        ctx = _ctx(root, run_id, case, actor="op-human")
        out = reclassify_task.run(
            ctx, {"task_type": "investigation",
                  "basis": "no court action filed"})
        self.assertNotIn("error", out)
        self.assertEqual("investigation",
                         case.data.get("task_type"))

    def test_model_widening_move_allowed(self) -> None:
        root, run_id, case = _litigation_case(self)
        case.data["task_type"] = "investigation"
        case.data["assessment_policy"] = {
            "task_type": "investigation",
            "gate": reasoning.GATE_VERSION,
            "required_classes": list(
                reasoning.REQUIRED_CLASSES["investigation"]),
            "pinned_at": "2026-01-01T00:00:00+00:00",
            "basis": "task_type_set",
        }
        ctx = _ctx(root, run_id, case)
        out = reclassify_task.run(
            ctx, {"task_type": "litigation",
                  "basis": "complaint filed"})
        self.assertNotIn("error", out)
        self.assertEqual("litigation",
                         case.data.get("task_type"))


class ApprovalStalenessTest(unittest.TestCase):
    def _granted(self, case):
        req = case.request_approval(action="file memo",
                                    scope="current draft",
                                    reason="ready")
        case.decide_approval(approval_id=req["id"],
                             decision="grant",
                             decided_by="op-human", note="ok")
        return req

    def test_reclassification_stales_grant(self) -> None:
        _, _, case = _litigation_case(self)
        req = self._granted(case)
        denied = case.request_approval(action="send letter",
                                       scope="draft",
                                       reason="ask")
        case.decide_approval(approval_id=denied["id"],
                             decision="deny",
                             decided_by="op-human", note="no")
        case.reclassify_task_type("research",
                                  "actually research")
        appr = [a for a in case.data["approvals"]
                if a["id"] == req["id"]][0]
        self.assertTrue(appr.get("stale"))
        self.assertIn("reclassif",
                      str(appr.get("stale_reason", "")))
        self.assertEqual(
            "stale",
            reasoning.approval_status(case.data, "file memo"),
        )
        kept = [a for a in case.data["approvals"]
                if a["id"] == denied["id"]][0]
        self.assertNotIn("stale", kept)
        self.assertEqual(
            "denied",
            reasoning.approval_status(case.data,
                                      "send letter"),
        )

    def test_same_type_repin_stales_grant(self) -> None:
        _, _, case = _litigation_case(self)
        req = self._granted(case)
        case.data["assessment_policy"]["gate"] = "reasoning-v0"
        case.reclassify_task_type("litigation",
                                  "heal the drifted pin")
        appr = [a for a in case.data["approvals"]
                if a["id"] == req["id"]][0]
        self.assertTrue(appr.get("stale"))
        self.assertEqual(
            "stale",
            reasoning.approval_status(case.data, "file memo"),
        )


class LegacyOmissionTest(unittest.TestCase):
    MEMO = {"findings": [{"item": "timing",
                          "status": "asserted",
                          "text": "deadline is June 1"}]}

    def test_findings_without_identification_demand_it(
            self) -> None:
        out = reasoning.run_gate(
            {"created": "2026-10-30T00:00:00+00:00"},
            dict(self.MEMO),
        )
        self.assertFalse(
            out["passed"],
            "new-run findings with no identification passed as legacy",
        )
        self.assertTrue(
            any("task type" in p.lower() or "identif" in p.lower()
                for p in out["problems"]),
            f"no identification demand: {out['problems']!r}",
        )

    def test_historical_approval_keeps_legacy(self) -> None:
        out = reasoning.run_gate(
            {"created": "2026-10-30T00:00:00+00:00",
             "memo": {"review": {"decision": "approved"}}},
            dict(self.MEMO),
        )
        self.assertTrue(out["passed"])
        self.assertEqual("legacy", out["mode"])

    def test_legacy_without_findings_passes(self) -> None:
        out = reasoning.run_gate(
            {"created": "2026-10-30T00:00:00+00:00"},
            {"findings": []},
        )
        self.assertTrue(out["passed"])
        self.assertEqual("legacy", out["mode"])


if __name__ == "__main__":
    unittest.main()
