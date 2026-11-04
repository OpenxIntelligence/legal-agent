"""Task-category aliases: natural names accepted, stored canonical.

"investigation" is a first-class gate task type, a gate, and the
system's own vocabulary for establishing what happened — but the
classification taxonomy only knew "evidence", so callers writing
the natural name were refused and told to use 'other' for
emerging work. Investigation is core work, not emerging work.

Aliases canonicalize at the validation boundary: "investigation"
is accepted anywhere a taxonomy category is expected and stored
as "evidence", so downstream TASK_TAXONOMY lookups keep working
and primary-matching stays exact on canonical names.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_kit import reasoning
from agent_kit.case_record import CaseError, CaseRecord


def classification(name: str, rationale: str = "why") -> dict:
    return {"category": name, "rationale": rationale}


class AliasValidationTest(unittest.TestCase):
    def test_investigation_accepted_as_category(self) -> None:
        problems = reasoning.validate_classification(
            [classification("investigation")], [], "evidence",
            "investigation")
        self.assertEqual([], problems)

    def test_primary_may_use_alias_spelling(self) -> None:
        problems = reasoning.validate_classification(
            [classification("evidence")], [], "investigation",
            "investigation")
        self.assertEqual([], problems)

    def test_alias_plus_canonical_is_duplicate(self) -> None:
        problems = reasoning.validate_classification(
            [classification("evidence"), classification("investigation")],
            [], "evidence", "investigation")
        self.assertTrue(
            any("duplicate" in p for p in problems),
            f"expected duplicate, got {problems}")

    def test_refusal_lists_known_categories(self) -> None:
        problems = reasoning.validate_classification(
            [classification("nope")], [], "nope", "investigation")
        self.assertEqual(1, len(
            [p for p in problems if p.startswith("unknown task category")]))
        refusal = next(
            p for p in problems if p.startswith("unknown task category"))
        self.assertIn("evidence", refusal)
        self.assertIn("other", refusal)

    def test_other_still_accepted_for_emerging_work(self) -> None:
        problems = reasoning.validate_classification(
            [classification("other")], [], "other", "investigation")
        self.assertEqual([], problems)

    def test_plan_case_accepts_alias(self) -> None:
        plan = reasoning.plan_case(["investigation"])
        self.assertEqual(
            reasoning.plan_case(["evidence"]), plan)


class AliasStorageTest(unittest.TestCase):
    def test_identify_task_stores_canonical(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            case = CaseRecord(Path(tmp), "run-alias")
            entry = case.identify_task(
                requested="r", objective="o", deliverable="d",
                underlying_problem="u", context_available="c",
                missing_or_ambiguous="m", constraints="k",
                categories=[classification("investigation")],
                dependencies=[],
                primary="investigation",
                gate_task_type="investigation")
            stored = entry["classification"]["categories"]
            self.assertEqual(["evidence"],
                             [c["category"] for c in stored])
            self.assertEqual(
                "evidence", entry["classification"]["primary"])
            # Stored names index the taxonomy: threshold advice works.
            self.assertTrue(all(
                c["category"] in reasoning.TASK_TAXONOMY
                for c in stored))

    def test_garbage_still_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            case = CaseRecord(Path(tmp), "run-alias-bad")
            with self.assertRaises(CaseError):
                case.identify_task(
                    requested="r", objective="o", deliverable="d",
                    underlying_problem="u", context_available="c",
                    missing_or_ambiguous="m", constraints="k",
                    categories=[classification("nope")],
                    dependencies=[],
                    primary="nope",
                    gate_task_type="investigation")


if __name__ == "__main__":
    unittest.main()
