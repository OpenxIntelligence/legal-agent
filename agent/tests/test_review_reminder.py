"""Loop review reminder: runs low on turns without a review get one nudge."""

from __future__ import annotations

import unittest

from agent_kit.case_record import CaseRecord
from agent_kit.settings import verification_enabled
from legal_agent.loop import REVIEW_REMINDER, run_agent
from tests import support


def _review_args(lens: str = "") -> dict:
    args = {
        "strongest_against": "a",
        "fact_risks": "b",
        "adverse_authority": "c",
        "overlooked": "d",
        "opposing_counsel": "e",
        "judge_needs": "f",
        "alternative_theory": "g",
        "reversing_fact": "h",
        "revision_needed": False,
        "revision": "no change needed",
    }
    if lens:
        args["lens"] = lens
    return args


def _reminders(messages: list) -> list:
    return [
        m for m in messages
        if isinstance(m, dict) and m.get("content") == REVIEW_REMINDER
    ]


class ReviewReminderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)

    def test_reminder_fires_when_turns_run_low_without_review(self) -> None:
        fake = support.scripted(
            self.root,
            [support.CHECKLIST, support.PLAN, support.LIST, support.READ,
             support.FACT, support.MEMO],
        )
        result = run_agent(root=self.root, client=fake.client, turns=10)
        # Off-mode files the memo; on-mode gates refuse or pause it. The
        # reminder is the subject here; either terminal state proves the
        # run completed normally.
        expected = "paused" if verification_enabled() else "answered"
        self.assertEqual(expected, result["status"])
        messages = support.progress(self.root, result["run_id"])["messages"]
        self.assertEqual(1, len(_reminders(messages)))

    def test_no_reminder_once_a_review_is_recorded(self) -> None:
        fake = support.scripted(
            self.root,
            [support.CHECKLIST,
             ("adversarial_review", _review_args()),
             support.PLAN, support.LIST, support.MEMO],
        )
        result = run_agent(root=self.root, client=fake.client, turns=10)
        messages = support.progress(self.root, result["run_id"])["messages"]
        self.assertEqual([], _reminders(messages))

    def test_reminder_fires_exactly_once(self) -> None:
        fake = support.scripted(
            self.root, [support.CHECKLIST, support.PLAN, support.LIST]
        )
        result = run_agent(root=self.root, client=fake.client, turns=10)
        self.assertEqual("paused", result["status"])
        messages = support.progress(self.root, result["run_id"])["messages"]
        self.assertEqual(1, len(_reminders(messages)))

    def test_lensed_reviews_in_loop_drive_the_panel_nudge(self) -> None:
        fake = support.scripted(
            self.root,
            [support.CHECKLIST,
             ("adversarial_review", _review_args("law")),
             ("adversarial_review", _review_args("evidence")),
             support.MEMO],
        )
        result = run_agent(root=self.root, client=fake.client, turns=10)
        calls = support.tool_calls(self.root, result["run_id"])
        panels = [
            out["panel"] for _n, _a, out in calls
            if _n == "adversarial_review" and "panel" in (out or {})
        ]
        self.assertEqual(2, len(panels))
        self.assertEqual(["law"], panels[0]["covered"])
        self.assertEqual(["evidence", "law"], panels[1]["covered"])
        record = CaseRecord(self.root, result["run_id"])
        self.assertEqual(["evidence", "law"], record.adversarial_lenses())


if __name__ == "__main__":
    unittest.main()
