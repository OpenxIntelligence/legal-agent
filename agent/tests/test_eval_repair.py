"""Defect repair loop: schema, partition, determinism.

Pins the repair runner's contract: every audit fixture completes
with its exact repaired/accepted partition, no regressions, no
silently open defects, bound reviews, and honest exports.
"""

from __future__ import annotations

import unittest

from eval import quality_remediate


class QualityRemediateTest(unittest.TestCase):
    def test_reports_carry_the_schema(self) -> None:
        for report in quality_remediate.run_all():
            with self.subTest(case=report["case"]):
                self.assertEqual(quality_remediate.REPORT_KEYS, set(report))
                self.assertEqual("scripted", report["client"])
                self.assertTrue(
                    report["semantic_quality"].startswith("not measured")
                )

    def test_every_case_completes(self) -> None:
        for report in quality_remediate.run_all():
            with self.subTest(case=report["case"]):
                self.assertTrue(report["complete"], report)
                self.assertEqual([], report["still_open"])
                self.assertEqual([], report["regressions"])
                self.assertTrue(report["review_bound"])
                self.assertTrue(report["export_ok"])

    def test_exact_repair_partition(self) -> None:
        expected = {
            "quality-quotes": (
                {"citation_failed:R2", "citation_failed:R3",
                 "citation_failed:RC"},
                set(),
            ),
            "quality-counterevidence": (
                set(),
                {"unsupported_claim:R1", "uncited_counterevidence:R1",
                 "contradictory_evidence:R1", "unsupported_conclusion:RC"},
            ),
            "quality-coverage": (
                {"unsupported_conclusion:RC"},
                {"uncovered_issue:qv_access", "unstated_open_issues:RG"},
            ),
        }
        for report in quality_remediate.run_all():
            with self.subTest(case=report["case"]):
                want_repaired, want_accepted = expected[report["case"]]
                got_repaired = {d["defect"] for d in report["repaired"]
                                if d["ok"]}
                got_accepted = {d["defect"] for d in report["accepted"]
                                if d["ok"]}
                self.assertEqual(want_repaired, got_repaired)
                self.assertEqual(want_accepted, got_accepted)

    def test_diagnostics_and_determinism(self) -> None:
        first = quality_remediate.run_all()
        second = quality_remediate.run_all()
        self.assertEqual(first, second)
        by_case = {r["case"]: r for r in first}
        self.assertEqual(2, by_case["quality-quotes"]["diagnostics"]["revisions"])
        self.assertEqual(0, by_case["quality-counterevidence"]["diagnostics"]["revisions"])
        self.assertEqual(1, by_case["quality-coverage"]["diagnostics"]["revisions"])
        for report in first:
            self.assertEqual(2, report["diagnostics"]["rechecks"])
