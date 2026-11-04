"""Panel lenses: optional review roles with advisory coverage.

Each adversarial challenge may name the panel role it speaks from;
coverage reports which roles spoke without gating anything, so
lens-less (legacy) reviews work exactly as before.
"""

from __future__ import annotations

import unittest

from agent_kit import tools
from agent_kit.case_record import (
    ADVERSARIAL_LENSES,
    CaseError,
    CaseRecord,
)
from agent_kit.progress import new_run
from agent_kit.tools import adversarial_review
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


class ReviewLensTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        self.run_id = new_run(self.root, instructions="i", objective="o")
        self.case = CaseRecord(self.root, self.run_id)
        self.fake = support.scripted(self.root, [])

    def call(self, name, args):
        return tools.run(
            self.fake.client,
            self.root,
            self.run_id,
            {"name": name, "arguments": args, "call_id": "call-1"},
        )

    def test_lens_stored_on_the_review(self) -> None:
        entry = self.case.record_adversarial(**_review(lens="law"))
        self.assertEqual(("AR1", "law"), (entry["id"], entry["lens"]))
        self.assertEqual(
            "law", CaseRecord(self.root, self.run_id).data["adversarial"][0]["lens"]
        )

    def test_unknown_lens_rejected(self) -> None:
        with self.assertRaisesRegex(CaseError, "unknown review lens"):
            self.case.record_adversarial(**_review(lens="astrology"))

    def test_tool_accepts_lens_and_rejects_bad_lens(self) -> None:
        result = self.call("adversarial_review", _review(lens="evidence"))
        self.assertEqual("AR1", result["reviewed"])
        bad = self.call("adversarial_review", _review(lens="nope"))
        self.assertIn("unknown review lens", bad["error"])

    def test_tool_result_shape_unchanged_without_lens(self) -> None:
        result = self.call("adversarial_review", _review())
        self.assertEqual(
            {"reviewed": "AR1", "revision_needed": False}, result
        )

    def test_legacy_reviews_without_lens_unaffected(self) -> None:
        entry = self.case.record_adversarial(**_review())
        self.assertEqual("", entry["lens"])
        self.assertEqual([], self.case.adversarial_lenses())

    def test_coverage_lists_distinct_lenses_sorted(self) -> None:
        self.case.record_adversarial(**_review(lens="strategy"))
        self.case.record_adversarial(**_review(lens="law"))
        self.case.record_adversarial(**_review(lens="law"))
        self.case.record_adversarial(**_review())
        self.assertEqual(["law", "strategy"], self.case.adversarial_lenses())

    def test_definition_advertises_the_lens_enum(self) -> None:
        lens = adversarial_review.DEFINITION["parameters"]["properties"]["lens"]
        self.assertEqual(list(ADVERSARIAL_LENSES), lens["enum"])
        self.assertNotIn(
            "lens", adversarial_review.DEFINITION["parameters"]["required"]
        )

    def test_lens_challenge_still_feeds_the_ledger(self) -> None:
        self.case.record_adversarial(
            **_review(
                lens="procedure",
                dropped=[
                    {"objection": "Service late", "counter_evidence": "Receipt"}
                ],
            )
        )
        findings = self.case.data["objections"]
        self.assertEqual(1, len(findings))
        self.assertEqual(["AR1"], findings[0]["reporters"])


class PanelCoordinatorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        self.run_id = new_run(self.root, instructions="i", objective="o")
        self.case = CaseRecord(self.root, self.run_id)
        self.fake = support.scripted(self.root, [])

    def call(self, name, args):
        return tools.run(
            self.fake.client,
            self.root,
            self.run_id,
            {"name": name, "arguments": args, "call_id": "call-1"},
        )

    def test_empty_panel_reports_all_missing(self) -> None:
        status = self.case.panel_status()
        self.assertEqual(
            (["evidence", "law", "procedure", "strategy"], False),
            (status["missing"], status["complete"]),
        )
        self.assertEqual([], status["covered"])

    def test_first_lens_nudges_remaining_seats(self) -> None:
        result = self.call("adversarial_review", _review(lens="law"))
        self.assertEqual(
            {"covered": ["law"], "missing": ["evidence", "procedure", "strategy"]},
            result["panel"],
        )

    def test_coverage_grows_and_completes(self) -> None:
        self.call("adversarial_review", _review(lens="law"))
        result = self.call("adversarial_review", _review(lens="law"))
        self.assertEqual(["law"], result["panel"]["covered"])
        for lens in ("evidence", "procedure", "strategy"):
            result = self.call("adversarial_review", _review(lens=lens))
        self.assertEqual([], result["panel"]["missing"])
        fresh = CaseRecord(self.root, self.run_id)
        self.assertTrue(fresh.panel_status()["complete"])

    def test_lens_less_challenge_after_panel_started_still_nudges(self) -> None:
        self.call("adversarial_review", _review(lens="law"))
        result = self.call("adversarial_review", _review())
        self.assertIn("panel", result)
        self.assertEqual(["law"], result["panel"]["covered"])

    def test_required_subset_and_unknown_lens(self) -> None:
        self.case.record_adversarial(**_review(lens="law"))
        status = self.case.panel_status(["law", "evidence"])
        self.assertEqual(
            (["law"], ["evidence"], False),
            (status["covered"], status["missing"], status["complete"]),
        )
        with self.assertRaisesRegex(CaseError, "unknown panel lens"):
            self.case.panel_status(["law", "nope"])


if __name__ == "__main__":
    unittest.main()
