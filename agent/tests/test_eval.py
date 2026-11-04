"""Milestone 8: eval harness mechanics (scripted runs only).

The harness materializes synthetic cases, runs them with mocked
clients on temp workspaces, and emits schema-valid final-state
reports. These tests prove the mechanics; they say nothing about
reasoning or retrieval quality.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from eval import cases, compare, run


class FixtureTest(unittest.TestCase):
    def test_materialize_writes_a_runnable_case(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "agent"
            case = cases.CASES["harbor-readiness"]
            cases.materialize(root, case)
            self.assertEqual(
                case["objective"], (root / "case/objective.md").read_text()
            )
            self.assertTrue((root / "case/instructions.md").is_file())
            reference = json.loads((root / "ground_truth/case.json").read_text())
            self.assertEqual(case["reference"], reference["items"])
            for doc_id, pages in case["documents"].items():
                self.assertTrue((root / "workspace/inbox" / f"{doc_id}.pdf").is_file())
                for n in range(1, len(pages) + 1):
                    self.assertTrue(
                        (
                            root / "workspace/inbox/pages" / f"{doc_id}-p{n}.png"
                        ).is_file()
                    )
            transcripts = cases.transcripts(case)
            self.assertEqual(
                ["DOC-NOTE", "DOC-STATUS"], sorted(transcripts)
            )


class RunnerTest(unittest.TestCase):
    def test_clean_case_passes_with_a_final_state_report(self) -> None:
        report = run.run_case("harbor-readiness", verification=True)
        self.assertEqual(set(run.REPORT_KEYS), set(report))
        self.assertEqual("scripted", report["client"])
        self.assertEqual("awaiting_signoff", report["status"])
        self.assertEqual(
            {"answered": 2, "escalated": 0, "open": 0, "total": 2},
            report["coverage"],
        )
        self.assertEqual({"status": "awaiting_signoff"}, report["memo"])
        self.assertEqual(
            [{"outcome": "passed", "passed": True, "assessed": 4}], report["claims"]
        )
        self.assertEqual(0, report["escalations"])
        self.assertEqual(0, report["refusals"])
        self.assertEqual(set(run.DIAGNOSTIC_KEYS), set(report["diagnostics"]))
        self.assertGreater(report["diagnostics"]["turns"], 0)
        self.assertGreater(report["diagnostics"]["agent_tokens"], 0)

    def test_gap_case_escalates(self) -> None:
        report = run.run_case("harbor-gap", verification=True)
        self.assertEqual("needs_input", report["status"])
        self.assertEqual(1, report["escalations"])
        self.assertIsNone(report["memo"])
        self.assertEqual([], report["claims"])

    def test_off_mode_files_unverified(self) -> None:
        report = run.run_case("harbor-readiness", verification=False)
        self.assertEqual("answered", report["status"])
        self.assertEqual({"status": "unverified"}, report["memo"])

    def test_environment_is_restored(self) -> None:
        before = {
            key: os.environ.get(key)
            for key in (
                "CASE_DIR",
                "GROUND_TRUTH_DIR",
                "VERIFICATION",
                "RUN_TOKEN_BUDGET",
                "MAX_CONTEXT_BYTES",
                "QC_ENABLED",
            )
        }
        run.run_case("harbor-gap", verification=True)
        after = {key: os.environ.get(key) for key in before}
        self.assertEqual(before, after)


class CompareTest(unittest.TestCase):
    def test_paired_arms_share_everything_but_qc(self) -> None:
        report = compare.compare_case("harbor-readiness")
        self.assertEqual(set(compare.COMPARISON_KEYS), set(report))
        self.assertEqual("scripted", report["client"])
        self.assertIn("not measured", report["semantic_quality"])
        self.assertIn("no pricing version", report["cost_usd"])
        on, off = report["arms"]["qc_on"], report["arms"]["qc_off"]
        self.assertGreaterEqual(on["qc"]["reviews"], 1)
        self.assertFalse(on["qc"]["skipped"])
        self.assertEqual(0, off["qc"]["reviews"])
        self.assertTrue(off["qc"]["skipped"])
        self.assertTrue(report["delta"]["same_status"])
        self.assertEqual(on["status"], off["status"])

    def test_delta_carries_value_free_structural_diffs(self) -> None:
        report = compare.compare_case("harbor-readiness")
        diffs = report["delta"]["diffs"]
        self.assertFalse(report["delta"]["truncated"])
        # The arms differ by design in QC (1 review vs skipped).
        self.assertTrue(
            any(d["path"].startswith("$.qc.") for d in diffs),
            diffs,
        )
        for entry in diffs:
            self.assertEqual(
                {"path", "kind"} | ({"delta"} if "delta" in entry else set()),
                set(entry),
            )

    def test_comparison_is_deterministic_apart_from_timing(self) -> None:
        first = compare.compare_case("harbor-gap")
        second = compare.compare_case("harbor-gap")
        for report in (first, second):
            for arm in report["arms"].values():
                arm["diagnostics"].pop("elapsed_s")
                arm["diagnostics"].pop("latency_ms")
            report["delta"].pop("latency_ms")
        self.assertEqual(first, second)

    def test_gap_case_never_reaches_qc_in_either_arm(self) -> None:
        report = compare.compare_case("harbor-gap")
        self.assertEqual("needs_input", report["arms"]["qc_on"]["status"])
        self.assertEqual(0, report["delta"]["qc_reviews_on"])
        self.assertEqual(0, report["delta"]["qc_reviews_off"])


if __name__ == "__main__":
    unittest.main()
