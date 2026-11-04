"""Priority-B characterization: reasoning-gate modes stay honest.

These tests pin the gate's engagement contract against the
current implementation (read-only calls): legacy passes only
with an explicit advisory, any task type or foundational
answer engages reasoning mode, and recorded identification
engages identified mode. If a future change lets substantive
work slip back to legacy silently, these fail. They do not
judge correctness — the gate is structural only, by design.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from agent_kit.reasoning import GATE_VERSION, run_gate
from verification import verify_reasoning


def ctx_with(data: dict):
    return SimpleNamespace(case=SimpleNamespace(data=data))


class GateModeTest(unittest.TestCase):
    def test_legacy_passes_only_with_advisory(self) -> None:
        result = run_gate({}, {"findings": []})
        self.assertEqual("legacy", result["mode"])
        self.assertTrue(result["passed"])
        self.assertEqual([], result["problems"])
        self.assertTrue(
            any("not engaged" in a for a in result["advisory"])
        )

    def test_task_type_engages_reasoning(self) -> None:
        result = run_gate({"task_type": "research"}, {"findings": []})
        self.assertEqual("reasoning", result["mode"])
        self.assertTrue(result["problems"])

    def test_foundational_answer_engages_reasoning(self) -> None:
        result = run_gate(
            {"foundational": [{"id": "F1", "class": "facts"}]},
            {"findings": []},
        )
        self.assertEqual("reasoning", result["mode"])

    def test_identification_engages_identified(self) -> None:
        result = run_gate(
            {"task_type": "research", "identification": {"stage": 1}},
            {"findings": []},
        )
        self.assertEqual("identified", result["mode"])

    def test_gate_version_reported(self) -> None:
        result = run_gate({}, {"findings": []})
        self.assertEqual(GATE_VERSION, result["gate"])
        wrapped = verify_reasoning.run_verification(
            ctx_with({}), {"findings": []}
        )
        self.assertEqual("legacy", wrapped["mode"])
        self.assertEqual(GATE_VERSION, wrapped["gate"])
        self.assertTrue(wrapped["passed"])

    def test_required_classes_follow_current_task_type(self) -> None:
        before = run_gate({"task_type": "research"}, {"findings": []})
        after = run_gate({"task_type": "contract"}, {"findings": []})
        self.assertNotEqual(before["problems"], after["problems"])
        self.assertTrue(
            any("governing_law" in p for p in before["problems"])
        )
        self.assertTrue(any("elements" in p for p in after["problems"]))

    def test_clearing_task_type_returns_to_legacy(self) -> None:
        # Documents current behavior, not approval of it: with no
        # task type and no answers the gate disengages. Server-side
        # policy pinning (peer's file) must close this if task
        # labels become mandatory.
        result = run_gate(
            {"task_type": "", "foundational": []}, {"findings": []}
        )
        self.assertEqual("legacy", result["mode"])


if __name__ == "__main__":
    unittest.main()
