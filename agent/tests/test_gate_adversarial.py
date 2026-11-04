"""Priority-E adversarial counterexamples for the reasoning gate.

Each case feeds the gate hostile-but-plausible input and asserts the
honest outcome: real gaps fail, mere suspicion advises, and valid
limited work is not punished. Calls are read-only against the
current implementation. Structural-only by design: none of these
assert the gate judges correctness.
"""

from __future__ import annotations

import unittest

from agent_kit.reasoning import REQUIRED_CLASSES, run_gate
from quality.modules import materiality


def problems_for(data: dict, memo: dict | None = None) -> list[str]:
    return run_gate(data, memo or {"findings": []})["problems"]


def clean_research_case() -> tuple[dict, dict]:
    """A fully linked, fully reviewed filing: the preserved success case."""
    answers = [
        {"id": f"A-{c}", "class": c, "status": "answered",
         "answer": f"answer for {c}", "issue": "Q1"}
        for c in REQUIRED_CLASSES["research"]
    ]
    data = {
        "task_type": "research",
        "jurisdiction": "California",
        "foundational": answers,
        "adversarial": [{"id": "RV1", "against": "preliminary",
                         "revision_needed": False}],
        "sources": {"DOC-A": {"repository": "r", "citation": "c",
                              "version": "v1"}},
        "facts": [{"id": "F1", "document_id": "DOC-A", "page": 1,
                   "quote": "the deadline is June 1"}],
    }
    memo = {"findings": [{"item": "Q1", "status": "supported",
                          "cited": ["F1"]}]}
    return data, memo


def advisory_for(data: dict, memo: dict | None = None) -> list[str]:
    return run_gate(data, memo or {"findings": []})["advisory"]


class GateAdversarialTest(unittest.TestCase):
    def test_asserted_finding_without_linked_answer_fails(self) -> None:
        problems = problems_for(
            {"task_type": "research"},
            {"findings": [{"item": "timing", "status": "asserted"}]},
        )
        self.assertTrue(
            any("no linked foundational answer" in p for p in problems)
        )

    def test_legal_rule_without_registered_authority_fails(self) -> None:
        problems = problems_for(
            {"task_type": "research",
             "facts": [{"id": "F9", "kind": "legal_rule",
                        "document_id": "NO-SUCH-DOC"}],
             "authorities": {}},
            {"findings": []},
        )
        self.assertTrue(
            any("no registered authority" in p for p in problems)
        )

    def test_unverified_authority_advises(self) -> None:
        data = {
            "task_type": "research",
            "facts": [{"id": "F9", "kind": "legal_rule",
                       "document_id": "DOC-X"}],
            "authorities": {
                "A1": {"document_id": "DOC-X", "level": "persuasive",
                       "verification": "provisional"}
            },
        }
        advisory = advisory_for(data, {"findings": []})
        self.assertTrue(
            any("no binding verified authority" in a for a in advisory)
        )
        self.assertFalse(
            any("no registered authority" in p
                for p in problems_for(data, {"findings": []}))
        )

    def test_evidence_after_review_advises_recheck(self) -> None:
        advisory = advisory_for(
            {"task_type": "research",
             "adversarial": [{"at": "2026-01-01T00:00:00Z"}],
             "facts": [{"id": "FNEW", "at": "2026-06-01T00:00:00Z"}]},
            {"findings": []},
        )
        self.assertTrue(
            any("recorded after the latest adversarial review" in a
                for a in advisory)
        )

    def test_gate_task_type_mismatch_fails(self) -> None:
        problems = problems_for(
            {"task_type": "contract",
             "identification": {"stage": 1},
             "classification": {"categories": [], "dependencies": [],
                                "primary": "", "gate_task_type": "research"}},
            {"findings": []},
        )
        self.assertTrue(any("does not match" in p for p in problems))

    def test_unresolved_finding_not_punished_for_no_link(self) -> None:
        problems = problems_for(
            {"task_type": "research"},
            {"findings": [{"item": "timing", "status": "unresolved"}]},
        )
        self.assertFalse(
            any("no linked foundational answer" in p for p in problems)
        )

    def test_unresolved_with_resolution_path_not_flagged(self) -> None:
        problems = problems_for(
            {"task_type": "research",
             "foundational": [{"id": "F1", "status": "unresolved",
                               "resolution": "find the signed ledger"}]},
            {"findings": []},
        )
        self.assertFalse(
            any("question F1 is unresolved" in p for p in problems)
        )

    def test_unresolved_without_resolution_path_fails(self) -> None:
        problems = problems_for(
            {"task_type": "research",
             "foundational": [{"id": "F1", "status": "unresolved"}]},
            {"findings": []},
        )
        self.assertTrue(
            any("question F1 is unresolved" in p for p in problems)
        )

    def test_unknown_objection_verdict_rejected(self) -> None:
        with self.assertRaises(ValueError):
            materiality._validate(
                {"verdicts": [{"id": "OB-X", "material": True,
                               "reason": "looks bad"}],
                 "resolutions": []},
                ["OB-1"],
                [],
            )

    def test_non_bool_objection_flag_rejected(self) -> None:
        with self.assertRaises(ValueError):
            materiality._validate(
                {"verdicts": [{"id": "OB-1", "material": "yes",
                               "reason": "sloppy"}],
                 "resolutions": []},
                ["OB-1"],
                [],
            )

    def test_clean_supported_filing_passes(self) -> None:
        data, memo = clean_research_case()
        out = run_gate(data, memo)
        self.assertTrue(out["passed"])
        self.assertEqual(out["problems"], [])

    def test_supported_negative_finding_passes(self) -> None:
        data, _ = clean_research_case()
        memo = {"findings": [{"item": "Q1", "status": "supported",
                              "cited": ["F1"],
                              "text": "the ledger was never signed"}]}
        out = run_gate(data, memo)
        self.assertTrue(out["passed"])
        self.assertEqual(out["problems"], [])


if __name__ == "__main__":
    unittest.main()
