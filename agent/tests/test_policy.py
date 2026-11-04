"""Server-owned assessment policy: pin, enforce, reclassify.

Pins the Priority B contract: identification freezes the policy
(task type + gate version + required classes) server-side; the
gate enforces the pin, never the live label or live class table;
type changes go through reclassification with basis and stale
the validated decomposition. Pre-pin records keep live lookup.
"""

from __future__ import annotations

import unittest

from agent_kit import reasoning, tools
from agent_kit.case_record import CaseError, CaseRecord
from agent_kit.progress import new_run
from agent_kit.tools import reclassify_task
from tests import support
from tests.test_reasoning import answer, classification, identification


class PinRecordTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        self.run_id = new_run(self.root, instructions="i", objective="o")
        self.case = CaseRecord(self.root, self.run_id)
        self.case.set_checklist(support.CHECKLIST_ITEMS)

    def test_first_set_pins_policy(self) -> None:
        self.case.set_task_type("research")
        pin = self.case.data["assessment_policy"]
        self.assertEqual("research", pin["task_type"])
        self.assertEqual(reasoning.GATE_VERSION, pin["gate"])
        self.assertEqual(
            list(reasoning.REQUIRED_CLASSES["research"]),
            pin["required_classes"],
        )
        self.assertTrue(pin["pinned_at"])
        self.assertEqual("task_type_set", pin["basis"])

    def test_same_type_set_is_idempotent(self) -> None:
        self.case.set_task_type("research")
        first = dict(self.case.data["assessment_policy"])
        self.case.set_task_type("research")
        self.assertEqual(first, self.case.data["assessment_policy"])

    def test_change_via_set_is_refused(self) -> None:
        self.case.set_task_type("research")
        with self.assertRaises(CaseError) as raised:
            self.case.set_task_type("litigation")
        self.assertIn("reclassify", str(raised.exception))
        self.assertEqual("research", self.case.data["task_type"])

    def test_identify_pins_when_absent_first_pin_wins(self) -> None:
        self.case.set_task_type("investigation")
        first = dict(self.case.data["assessment_policy"])
        self.case.identify_task(**identification(), **classification())
        self.assertEqual(first, self.case.data["assessment_policy"])
        self.assertEqual("investigation", self.case.data["task_type"])

    def test_identify_pins_fresh(self) -> None:
        self.case.identify_task(**identification(), **classification())
        pin = self.case.data["assessment_policy"]
        self.assertEqual("investigation", pin["task_type"])
        self.assertEqual("identification", pin["basis"])


class ReclassifyRecordTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        self.run_id = new_run(self.root, instructions="i", objective="o")
        self.case = CaseRecord(self.root, self.run_id)
        self.case.set_checklist(support.CHECKLIST_ITEMS)
        self.case.identify_task(**identification(), **classification())

    def test_basis_and_type_required(self) -> None:
        with self.assertRaises(CaseError):
            self.case.reclassify_task_type("litigation", "  ")
        with self.assertRaises(CaseError):
            self.case.reclassify_task_type("divination", "why")

    def test_same_type_returns_pin_unchanged(self) -> None:
        first = dict(self.case.data["assessment_policy"])
        pin = self.case.reclassify_task_type("investigation", "confirm")
        self.assertEqual(first, pin)
        self.assertNotIn("reclassifications", self.case.data)

    def test_change_logs_updates_and_repins(self) -> None:
        pin = self.case.reclassify_task_type(
            "litigation", "complaint filed; now contested"
        )
        self.assertEqual("litigation", pin["task_type"])
        self.assertEqual(reasoning.GATE_VERSION, pin["gate"])
        self.assertEqual(
            list(reasoning.REQUIRED_CLASSES["litigation"]),
            pin["required_classes"],
        )
        self.assertEqual("reclassification", pin["basis"])
        self.assertEqual("litigation", self.case.data["task_type"])
        self.assertEqual(
            "litigation", self.case.data["classification"]["gate_task_type"]
        )
        (entry,) = self.case.data["reclassifications"]
        self.assertEqual("investigation", entry["from"])
        self.assertEqual("litigation", entry["to"])
        self.assertIn("complaint", entry["basis"])

    def test_change_stales_decomposition(self) -> None:
        self.case.data["decompositions"] = [
            {"verdict": "pass", "questions": [{"id": "FQ1"}], "at": "t"}
        ]
        self.case.reclassify_task_type("litigation", "now contested")
        latest = self.case.data["decompositions"][-1]
        self.assertEqual("stale", latest["verdict"])
        self.assertIn("investigation", latest["stale_reason"])
        self.assertIn("litigation", latest["stale_reason"])
        # History preserved: the passed entry is still there.
        self.assertEqual(
            "pass", self.case.data["decompositions"][0]["verdict"]
        )


class PinnedGateTest(unittest.TestCase):
    def _pin(self, **kw) -> dict:
        pin = {
            "task_type": "research",
            "gate": reasoning.GATE_VERSION,
            "required_classes": ["objective", "jurisdiction"],
            "pinned_at": "t",
            "basis": "task_type_set",
        }
        pin.update(kw)
        return pin

    def _answers(self, *classes) -> list:
        return [answer(qclass=c) for c in classes]

    def test_pin_subset_enforced_not_live_table(self) -> None:
        # Live research requires 7 classes; the pin only 2.
        problems = reasoning._check_required_classes(
            "research", self._answers("objective", "jurisdiction"),
            self._pin(),
        )
        self.assertEqual([], problems)

    def test_pin_extra_class_blocks(self) -> None:
        pin = self._pin(required_classes=["objective", "deadlines"])
        problems = reasoning._check_required_classes(
            "research", self._answers("objective"), pin
        )
        self.assertEqual(1, len(problems))
        self.assertIn("deadlines", problems[0])

    def test_tampered_label_fails(self) -> None:
        problems = reasoning._check_required_classes(
            "litigation", self._answers("objective", "jurisdiction"),
            self._pin(),
        )
        self.assertEqual(1, len(problems))
        self.assertIn("reclassify", problems[0])

    def test_version_drift_fails(self) -> None:
        pin = self._pin(gate="reasoning-v0")
        problems = reasoning._check_required_classes(
            "research", self._answers("objective", "jurisdiction"), pin
        )
        self.assertEqual(1, len(problems))
        self.assertIn("reasoning-v0", problems[0])
        self.assertIn(reasoning.GATE_VERSION, problems[0])

    def test_malformed_pin_fails_closed(self) -> None:
        for bad in ({"required_classes": []}, {"required_classes": ["nope"]},
                    {"required_classes": "objective"}):
            with self.subTest(bad=bad):
                problems = reasoning._check_required_classes(
                    "research", self._answers("objective"), self._pin(**bad)
                )
                self.assertEqual(1, len(problems))
                self.assertIn("malformed", problems[0])

    def test_no_pin_keeps_live_lookup(self) -> None:
        problems = reasoning._check_required_classes(
            "research", self._answers("objective"), None
        )
        self.assertTrue(any("jurisdiction" in p for p in problems))

    def test_stale_decomposition_blocks_identified_gate(self) -> None:
        data = {
            "identification": dict(
                identification(), at="t",
                classification=dict(classification(), at="t"),
            ),
            "classification": dict(classification(), at="t"),
            "task_type": "investigation",
            "decompositions": [
                {"verdict": "stale", "stale_reason": "r", "questions": []}
            ],
        }
        problems, _ = reasoning.run_identified_checks(data)
        self.assertTrue(any("stale after reclassification" in p for p in problems))

    def test_gate_names_tamper_in_full_run(self) -> None:
        data = {
            "task_type": "litigation",
            "assessment_policy": self._pin(),
            "foundational": self._answers("objective", "jurisdiction"),
        }
        result = reasoning.run_gate(data, {})
        self.assertFalse(result["passed"])
        self.assertTrue(
            any("pinned assessment" in p for p in result["problems"]),
            result["problems"],
        )


class PolicyToolTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        self.fake = support.scripted(self.root, [])
        self.run_id = new_run(
            self.root, instructions="i", objective="o", verification=True
        )
        self.case = CaseRecord(self.root, self.run_id)
        self.case.set_checklist(support.CHECKLIST_ITEMS)
        self.ctx = tools.Context(
            self.fake.client, self.root, self.run_id, self.case, True
        )

    def test_reclassify_tool_round_trip(self) -> None:
        self.case.identify_task(**identification(), **classification())
        result = reclassify_task.run(
            self.ctx, {"task_type": "litigation", "basis": "now contested"}
        )
        self.assertTrue(result["reclassified"])
        self.assertEqual("litigation", result["task_type"])
        self.assertEqual("litigation", result["policy"]["task_type"])
        self.assertEqual(
            "litigation", self.case.data["classification"]["gate_task_type"]
        )

    def test_tool_registered(self) -> None:
        self.assertIn("reclassify_task", tools.BASE_TOOLS)
        self.assertIn("reclassify_task", tools.MODULES)
        names = [t["name"] for t in tools.definitions(self.root, self.run_id)]
        self.assertIn("reclassify_task", names)


class ScopeCoverageTest(unittest.TestCase):
    """Scope gate: declared law basis + jurisdiction (Item 2, G9)."""

    def setUp(self) -> None:
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        self.run_id = new_run(self.root, instructions="i", objective="o")
        self.case = CaseRecord(self.root, self.run_id)

    def _data(self, **kw):
        return {
            "identification": identification(**kw),
            "classification": classification(),
            "task_type": "investigation",
        }

    def _scope(self, data):
        problems, advisory = reasoning.run_identified_checks(data)
        scope_problems = [p for p in problems if "law basis" in p]
        scope_advisory = [
            a for a in advisory
            if "law basis" in a or "no jurisdiction recorded" in a
        ]
        return scope_problems, scope_advisory

    def test_supplied_basis_with_jurisdiction_passes(self) -> None:
        problems, advisory = self._scope(self._data(
            jurisdiction="California (operator-stated)",
            law_basis="supplied-documents",
        ))
        self.assertEqual([], problems)
        self.assertEqual([], advisory)

    def test_background_basis_refuses(self) -> None:
        for basis in ("background-needed", "mixed"):
            with self.subTest(basis=basis):
                problems, _ = self._scope(self._data(
                    jurisdiction="California", law_basis=basis
                ))
                self.assertEqual(1, len(problems))
                self.assertIn("human sign-off", problems[0])

    def test_missing_basis_and_jurisdiction_advise(self) -> None:
        problems, advisory = self._scope(self._data())
        self.assertEqual([], problems)
        self.assertTrue(
            any("no law basis declared" in a for a in advisory), advisory
        )
        self.assertTrue(
            any("no jurisdiction recorded" in a for a in advisory), advisory
        )

    def test_unknown_basis_fails_closed(self) -> None:
        problems, _ = self._scope(self._data(law_basis="vibes"))
        self.assertEqual(1, len(problems))
        self.assertIn("unknown law basis", problems[0])

    def test_record_stores_scope_and_rejects_unknown_basis(self) -> None:
        entry = self.case.identify_task(
            **identification(), **classification(),
            jurisdiction="California", law_basis="supplied-documents",
        )
        self.assertEqual("California", entry["jurisdiction"])
        self.assertEqual("supplied-documents", entry["law_basis"])
        with self.assertRaises(CaseError):
            self.case.identify_task(
                **identification(), **classification(), law_basis="vibes",
            )


if __name__ == "__main__":
    unittest.main()
