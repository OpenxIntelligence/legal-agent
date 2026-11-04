"""Orchestration benchmark integrity: versioned fixtures (closeout §9).

Cases and expected classifications are versioned; every report
carries the fixture version it ran against, and the frozen case
set is pinned by name. Truth changes require a version bump —
never a silent edit.
"""

from __future__ import annotations

import unittest

from eval import orchestration_cases, orchestration_run


class CasesVersionTest(unittest.TestCase):
    def test_cases_carry_a_version(self) -> None:
        self.assertRegex(
            orchestration_cases.CASES_VERSION,
            r"^orchestration-cases/v\d+$",
        )

    def test_report_carries_fixture_version(self) -> None:
        report = orchestration_run.run_case("meridian-bonus")
        self.assertEqual(
            orchestration_cases.CASES_VERSION,
            report["cases_version"],
        )
        self.assertIn("cases_version", orchestration_run.REPORT_KEYS)

    def test_frozen_case_set_pinned(self) -> None:
        self.assertEqual(
            ["harborlight-renewal", "lighthouse-claim", "meridian-bonus",
             "westhaven-amendment"],
            sorted(orchestration_cases.CASES),
        )
        for name, case in orchestration_cases.CASES.items():
            for key in ("truth", "baseline_truth", "context", "seeds"):
                self.assertIn(key, case, f"{name} missing {key}")


if __name__ == "__main__":
    unittest.main()
