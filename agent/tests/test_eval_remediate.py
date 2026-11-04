"""Phase F stage 3: the correction-loop harness measures the loop.

Schema, determinism, fix/unfixable partitioning, review
binding, redaction, and diagnostics for `eval/remediate.py`.
Loop mechanics only — never judgment or human effort.
"""

from __future__ import annotations

import unittest

from eval import artifact_cases, remediate


class RemediateCase(unittest.TestCase):
    def test_every_fixture_completes(self) -> None:
        for report in remediate.run_all():
            with self.subTest(case=report["case"]):
                self.assertTrue(report["complete"], report)
                self.assertTrue(report["review_bound"])
                self.assertTrue(report["export_ok"])
                self.assertEqual(remediate.REPORT_KEYS, set(report))
                self.assertEqual("scripted", report["client"])
                self.assertIn("not measured", report["semantic_quality"])

    def test_fix_partition_covers_every_row(self) -> None:
        for report in remediate.run_all():
            truth = artifact_cases.CASES[report["case"]]["truth"]
            seen = {f["row"] for f in report["fixed"]}
            seen |= {u["row"] for u in report["unresolved"]}
            self.assertEqual(set(truth), seen)
            self.assertTrue(all(f["ok"] for f in report["fixed"]))
            self.assertTrue(all(u["ok"] for u in report["unresolved"]))

    def test_unfixable_rows_are_exactly_unfixable(self) -> None:
        report = next(
            r for r in remediate.run_all()
            if r["case"] == "eval-citation-defects"
        )
        self.assertEqual(
            {"R3", "R5", "RC"},
            {u["row"] for u in report["unresolved"]},
        )
        clean = next(
            r for r in remediate.run_all()
            if r["case"] == "eval-clean-ledger"
        )
        self.assertEqual([], clean["unresolved"])
        self.assertEqual(0, clean["diagnostics"]["revisions"])

    def test_diagnostics_count_work(self) -> None:
        report = next(
            r for r in remediate.run_all()
            if r["case"] == "eval-citation-defects"
        )
        diagnostics = report["diagnostics"]
        self.assertEqual(4, diagnostics["revisions"])
        self.assertEqual(2, diagnostics["rechecks"])
        self.assertEqual(2, diagnostics["judge_calls"])
        self.assertGreater(diagnostics["versions"], 1)

    def test_runs_are_deterministic(self) -> None:
        self.assertEqual(remediate.run_all(), remediate.run_all())


if __name__ == "__main__":
    unittest.main()
