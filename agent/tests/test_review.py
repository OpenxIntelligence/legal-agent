"""Increment 6: scope/workflow/dependency/budget console views.

Synthetic fixture workshops only — no live runs, no browser.
The page script gets a syntax check plus these data-contract
tests; rendered appearance is not inspected here.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests import support

TURNS = [
    {"input_tokens": 100, "output_tokens": 50},
    {"input_tokens": 200, "output_tokens": 100},
]
USAGE = [
    {
        "purpose": "extract_text",
        "model": "scripted",
        "input_tokens": 3000,
        "output_tokens": 900,
    }
]
CASE = {
    "matter": {
        "version": 2,
        "fields": {
            "purpose": {
                "value": "litigation support",
                "status": "known",
                "provenance": "assignment",
            },
            "governing_law": {"value": "", "status": "unknown", "provenance": ""},
        },
    },
    "branches": {
        "B1": {
            "id": "B1",
            "issue": "alpha",
            "regime": "ca",
            "regime_label": "California",
            "status": "disputed",
            "basis": "clause text unclear",
        }
    },
    "tasks": {
        "ROOT": {"id": "ROOT", "kind": "root", "seq": 0, "status": "open"},
        "T1": {
            "id": "T1",
            "kind": "issue",
            "seq": 1,
            "question": "Was alpha true?",
            "issues": ["alpha"],
            "status": "open",
            "attempts": [{"status": "partial", "summary": "first pass"}],
        },
        "T2": {
            "id": "T2",
            "kind": "issue",
            "seq": 2,
            "question": "Was beta true?",
            "issues": ["beta"],
            "status": "blocked",
            "blocked_on": "waiting for the beta record",
            "attempts": [],
        },
        "T3": {
            "id": "T3",
            "kind": "issue",
            "seq": 3,
            "question": "Was gamma true?",
            "issues": ["gamma"],
            "status": "stale",
            "attempts": [{"status": "completed", "summary": "done"}],
            "history": [{"reason": "new evidence recorded"}],
        },
        "T4": {
            "id": "T4",
            "kind": "issue",
            "seq": 4,
            "question": "Was delta true?",
            "issues": ["delta"],
            "status": "open",
            "dependencies": ["T1"],
            "attempts": [],
        },
    },
    "requests": [
        {"id": "R1", "item": "beta", "need": "the beta record", "status": "open"},
        {"id": "R0", "item": "alpha", "need": "old need", "status": "answered"},
    ],
    "facts": [],
}


class ReviewConsoleCase(unittest.TestCase):
    def setUp(self) -> None:
        self.ui = support.console_ui()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.workshop = Path(tmp.name)
        (self.workshop / "agent/helper").parent.mkdir(parents=True, exist_ok=True)
        (self.workshop / "agent/helper").write_text("")
        self.runs = self.workshop / "agent/workspace/runs/run-test"
        self.runs.mkdir(parents=True)
        records = self.workshop / "agent/workspace/case_records"
        records.mkdir(parents=True)
        (records / "run-test.json").write_text(json.dumps(CASE))
        self.progress = {
            "run_id": "run-test",
            "verification": True,
            "model": "scripted",
            "started": "2026-01-01T00:00:00+00:00",
            "status": "paused",
            "turn": 3,
            "messages": [],
            "turns": TURNS,
            "notes": {},
            "config": {
                "run_token_budget": 10000,
                "synthesis_reserve_tokens": 1000,
            },
        }
        self.write_progress()
        (self.runs / "usage.json").write_text(json.dumps(USAGE))
        self.console = self.ui.Console(None)
        self.console.set_workshop(str(self.workshop))

    def write_progress(self) -> None:
        (self.runs / "progress.json").write_text(json.dumps(self.progress))

    def detail(self) -> dict:
        return self.console.run_detail("run-test")


class ScopeViewTest(ReviewConsoleCase):
    def test_scope_lists_known_unknown_and_branches(self) -> None:
        scope = self.detail()["scope"]
        self.assertEqual(2, scope["matter_version"])
        self.assertEqual(
            [
                {
                    "field": "purpose",
                    "value": "litigation support",
                    "status": "known",
                    "provenance": "assignment",
                }
            ],
            scope["known"],
        )
        self.assertEqual(["governing_law"], scope["unknown"])
        self.assertEqual(
            [
                {
                    "id": "B1",
                    "issue": "alpha",
                    "regime": "California",
                    "status": "disputed",
                    "basis": "clause text unclear",
                }
            ],
            scope["branches"],
        )

    def test_legacy_record_has_empty_scope(self) -> None:
        scope = self.ui.scope_view({})
        self.assertIsNone(scope["matter_version"])
        self.assertEqual([], scope["known"])
        self.assertEqual([], scope["unknown"])
        self.assertEqual([], scope["branches"])


class WorkflowViewTest(ReviewConsoleCase):
    def test_tasks_report_state_attempts_and_waiting(self) -> None:
        rows = {row["id"]: row for row in self.detail()["workflow"]["tasks"]}
        self.assertEqual("open", rows["T1"]["status"])
        self.assertEqual(1, rows["T1"]["attempts"])
        self.assertEqual("partial", rows["T1"]["last_outcome"])
        self.assertEqual("blocked", rows["T2"]["status"])
        self.assertEqual("waiting for the beta record", rows["T2"]["blocked_on"])
        self.assertEqual(["T1"], rows["T4"]["waiting_on"])
        self.assertEqual([], rows["T1"]["waiting_on"])
        counts = self.detail()["workflow"]["counts"]
        self.assertEqual(3, counts["open"])
        self.assertEqual(1, counts["blocked"])
        self.assertEqual(1, counts["stale"])

    def test_legacy_record_has_no_tasks(self) -> None:
        self.assertEqual({"tasks": [], "counts": {}}, self.ui.workflow_view({}))


class DependencyViewTest(ReviewConsoleCase):
    def test_blocked_waiting_stale_and_requests(self) -> None:
        deps = self.detail()["dependencies"]
        self.assertEqual(
            [{"task": "T2", "blocked_on": "waiting for the beta record"}],
            deps["blocked"],
        )
        self.assertEqual([{"task": "T4", "waits_on": ["T1"]}], deps["waiting"])
        self.assertEqual(
            [{"task": "T3", "reason": "new evidence recorded"}], deps["stale"]
        )
        self.assertEqual(
            [
                {
                    "id": "R1",
                    "item": "beta",
                    "need": "the beta record",
                    "status": "open",
                }
            ],
            deps["open_requests"],
        )

    def test_completed_prerequisite_unblocks(self) -> None:
        case = json.loads(json.dumps(CASE))
        case["tasks"]["T1"]["status"] = "completed"
        deps = self.ui.dependency_view(case)
        self.assertEqual([], deps["waiting"])


class BudgetViewTest(ReviewConsoleCase):
    def test_budget_math_mirrors_policy(self) -> None:
        budget = self.detail()["budget"]
        self.assertTrue(budget["enforced"])
        self.assertEqual(10000, budget["allowance"])
        self.assertEqual(450, budget["observed_agent"])
        self.assertEqual(3900, budget["observed_tool"])
        self.assertEqual(4350, budget["observed"])
        self.assertEqual(1000, budget["reserve"])
        self.assertEqual(4650, budget["remaining_for_work"])
        self.assertFalse(budget["tool_tokens_unknown"])
        self.assertEqual(3, budget["turns_used"])

    def test_missing_ledger_is_unknown_not_zero(self) -> None:
        (self.runs / "usage.json").unlink()
        budget = self.detail()["budget"]
        self.assertIsNone(budget["observed_tool"])
        self.assertTrue(budget["tool_tokens_unknown"])
        self.assertEqual(450, budget["observed"])

    def test_no_allowance_is_unenforced(self) -> None:
        self.progress["config"] = {}
        self.write_progress()
        budget = self.detail()["budget"]
        self.assertFalse(budget["enforced"])
        self.assertIsNone(budget["allowance"])
        self.assertIsNone(budget["remaining_for_work"])
        self.assertEqual(4350, budget["observed"])

    def test_malformed_allowance_is_unenforced(self) -> None:
        budget = self.ui.budget_view(
            {"turns": [], "config": {"run_token_budget": "lots"}}, None
        )
        self.assertFalse(budget["enforced"])
        self.assertIsNone(budget["allowance"])


if __name__ == "__main__":
    unittest.main()
