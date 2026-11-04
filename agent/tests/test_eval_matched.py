"""Phase F stage 2: the matched-agreement harness measures agreement.

Schema, determinism, both-fixtures-agree, gallery-only
detection, and the memo-less not-applicable shape for
`eval/matched.py`. Agreement between scripted surfaces proves
consistent interpretation — never judgment quality.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_kit.case_record import CaseRecord
from eval import matched


class MatchedCase(unittest.TestCase):
    def test_both_fixtures_agree(self) -> None:
        for report in matched.run_all():
            with self.subTest(case=report["case"]):
                self.assertTrue(report["agree"], report["disagreements"])
                self.assertEqual([], report["disagreements"])
                self.assertTrue(matched.REPORT_KEYS <= set(report))
                self.assertEqual("scripted", report["client"])
                self.assertIn("not measured", report["semantic_quality"])
                self.assertGreaterEqual(len(report["units"]), 4)

    def test_overclaim_fails_on_both_surfaces(self) -> None:
        report = next(
            r for r in matched.run_all() if r["case"] == "matched-overclaim"
        )
        units = report["units"]
        self.assertEqual("failed", units["finding:overreach"]["console"])
        self.assertEqual("failed", units["finding:overreach"]["gallery_semantic"])
        self.assertEqual("failed", units["conclusion"]["console"])
        self.assertEqual("failed", units["conclusion"]["gallery_semantic"])

    def test_wrong_page_is_gallery_only(self) -> None:
        report = next(
            r for r in matched.run_all() if r["case"] == "matched-overclaim"
        )
        units = report["units"]
        self.assertEqual("failed", units["finding:wrong-page"]["gallery_structural"])
        self.assertEqual("not_run", units["finding:wrong-page"]["gallery_semantic"])
        self.assertEqual(1, len(report["gallery_only"]))
        self.assertEqual(
            "finding:wrong-page", report["gallery_only"][0]["unit"]
        )
        clean = next(
            r for r in matched.run_all() if r["case"] == "matched-clean"
        )
        self.assertEqual([], clean["gallery_only"])

    def test_runs_are_deterministic(self) -> None:
        self.assertEqual(matched.run_all(), matched.run_all())

    def test_judge_calls_are_counted_per_arm(self) -> None:
        for report in matched.run_all():
            diagnostics = report["diagnostics"]
            self.assertEqual(1, diagnostics["console_judge_calls"])
            self.assertEqual(1, diagnostics["gallery_judge_calls"])

    def test_memo_less_record_is_not_applicable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            record = CaseRecord(Path(tmp), "matched-empty")
            self.assertIsNone(
                matched._gallery_arm(record, Path(tmp), set())
            )


if __name__ == "__main__":
    unittest.main()
