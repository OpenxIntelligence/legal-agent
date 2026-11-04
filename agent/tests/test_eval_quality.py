"""Frozen quality benchmark: schema, determinism, truth coverage, math.

These tests pin the benchmark's contract, not its scores: scores
move as the implementation improves (see docs/agent-v2-evaluation.md
for the pre/post record). Grading rules stay independent of the
system: truth is planted in eval/quality_cases.py.
"""

from __future__ import annotations

import unittest

from eval import quality_cases, quality_run


class QualityBenchmarkTest(unittest.TestCase):
    def test_reports_carry_the_schema(self) -> None:
        for report in quality_run.run_all():
            with self.subTest(case=report["case"]):
                self.assertEqual(quality_run.REPORT_KEYS, set(report))
                self.assertEqual("scripted", report["client"])
                self.assertTrue(
                    report["semantic_quality"].startswith(
                        ("not measured", "not applicable")
                    )
                )

    def test_runs_are_deterministic(self) -> None:
        first = quality_run.run_all()
        second = quality_run.run_all()
        scrubbed = lambda reports: [
            {k: v for k, v in r.items()} for r in reports
        ]
        self.assertEqual(scrubbed(first), scrubbed(second))

    def test_truth_covers_every_fixture(self) -> None:
        for name, case in quality_cases.CASES.items():
            with self.subTest(case=name):
                self.assertIn("brief", case)
                for key in (
                    "expected_findings", "relevant_passages",
                    "required_issues", "prohibited_conclusions", "limitations",
                ):
                    self.assertTrue(case["brief"][key], key)
                if case.get("kind") == "matrix":
                    self.assertIn("stages", case)
                    continue
                self.assertTrue(case["truth"], "rows need truth")
                for row_id, expected in case["truth"].items():
                    self.assertIn(expected["structural"],
                                  ("passed", "failed", "incomplete", "not_run"),
                                  row_id)
                    self.assertIn(expected["semantic"],
                                  ("passed", "failed", "unavailable", "not_run"),
                                  row_id)
                for defect in case.get("defects", []):
                    self.assertIn(defect["category"],
                                  quality_cases.DEFECT_CATEGORIES)

    def test_summary_math_has_denominators(self) -> None:
        summary = quality_run.summarize(quality_run.run_all())
        self.assertEqual(len(quality_cases.CASES), summary["cases"])
        for key in (
            "structural_match", "semantic_agreement", "defect_recall",
            "defect_precision", "quotation_accuracy", "unsupported_detected",
            "abstention", "jurisdiction_stages",
        ):
            with self.subTest(metric=key):
                bucket = summary[key]
                self.assertLessEqual(bucket["hit"], bucket["total"])
        for key in ("issue_coverage", "counterevidence", "contradiction"):
            with self.subTest(metric=key):
                bucket = summary[key]
                self.assertLessEqual(bucket["observed"], bucket["expected"])
                self.assertGreater(bucket["expected"], 0)
        self.assertIsInstance(summary["clean_false_flags"], int)
