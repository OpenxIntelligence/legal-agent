"""Phase F stage 1: the audit-eval harness measures what it claims.

Schema, determinism, fixture self-consistency, and summary
math for `eval/artifact_run.py`. These tests guard the
measurement plumbing; they assert nothing about live model
quality.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from eval import artifact_cases, artifact_run


class ArtifactEvalCase(unittest.TestCase):
    def test_every_fixture_grades_complete(self) -> None:
        for report in artifact_run.run_all():
            with self.subTest(case=report["case"]):
                self.assertTrue(
                    report["complete"],
                    f"mismatches: {[r for r, v in report['rows'].items() if not v['match']]}",
                )
                self.assertEqual(0, report["clean_false_flags"])
                self.assertEqual(
                    artifact_run.REPORT_KEYS, set(report),
                )
                self.assertEqual("scripted", report["client"])
                self.assertIn("not measured", report["semantic_quality"])

    def test_runs_are_deterministic(self) -> None:
        first = artifact_run.run_all()
        second = artifact_run.run_all()
        self.assertEqual(first, second)

    def test_truth_covers_every_row_and_classes_are_balanced(self) -> None:
        seen: dict[str, int] = {}
        for name in sorted(artifact_cases.CASES):
            with tempfile.TemporaryDirectory() as tmp:
                _, _, instance = artifact_cases.materialize(
                    Path(tmp), name
                )
            truth = artifact_cases.CASES[name]["truth"]
            self.assertEqual(
                {r["row_id"] for r in instance["rows"]}, set(truth)
            )
            self.assertIn("clean", {v["class"] for v in truth.values()})
            for row_id, expected in truth.items():
                seen[expected["class"]] = seen.get(expected["class"], 0) + 1
        for wanted in (
            "clean", "wrong_page", "absent_source", "missing_quote",
            "revoked_source", "untranscribed", "changed_source",
            "unsupported_inference", "qualified_uncertainty",
            "unsupported_conclusion",
        ):
            self.assertGreaterEqual(
                seen.get(wanted, 0), 1, f"no fixture row for {wanted}"
            )

    def test_scripted_units_resolve_to_real_rows(self) -> None:
        for name, case in artifact_cases.CASES.items():
            with tempfile.TemporaryDirectory() as tmp:
                _, _, instance = artifact_cases.materialize(
                    Path(tmp), name
                )
            row_ids = {r["row_id"] for r in instance["rows"]}
            for unit in case.get("unsupported", []):
                if unit in ("conclusion", "gaps"):
                    continue
                kind, _, row_id = unit.partition(":")
                self.assertEqual("finding", kind)
                self.assertIn(row_id, row_ids)

    def test_summary_math(self) -> None:
        reports = artifact_run.run_all()
        summary = artifact_run.summarize(reports)
        self.assertEqual(len(reports), summary["cases"])
        self.assertEqual(len(reports), summary["cases_complete"])
        self.assertEqual(0, summary["clean_false_flags"])
        total = sum(b["rows"] for b in summary["classes"].values())
        matched = sum(b["matched"] for b in summary["classes"].values())
        self.assertEqual(total, matched)
        self.assertEqual(
            total, sum(len(r["rows"]) for r in reports)
        )


if __name__ == "__main__":
    unittest.main()
