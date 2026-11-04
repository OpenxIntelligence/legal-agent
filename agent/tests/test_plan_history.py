"""Durable investigation plans: every update_plan revision persists to the case record."""

from __future__ import annotations

import unittest

from agent_kit import tools
from agent_kit.case_record import CaseRecord
from agent_kit.progress import new_run
from legal_agent.loop import run_agent
from tests import support


class PlanHistoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        self.run_id = new_run(self.root, instructions="i", objective="o")
        self.fake = support.scripted(self.root, [])

    def call(self, name, args):
        return tools.run(
            self.fake.client,
            self.root,
            self.run_id,
            {"name": name, "arguments": args, "call_id": "call-1"},
        )

    def test_tool_result_shape_is_unchanged_and_revision_1_persists(self) -> None:
        result = self.call("update_plan", {"plan": ["a", "b"]})
        self.assertEqual({"plan": ["a", "b"]}, result)
        data = CaseRecord(self.root, self.run_id).data
        self.assertEqual(["a", "b"], data["plan"]["steps"])
        self.assertEqual(1, data["plan"]["revision"])
        self.assertEqual("agent", data["plan"]["actor"])
        self.assertIn("at", data["plan"])

    def test_replanning_appends_revisions_and_refreshes_current(self) -> None:
        self.call("update_plan", {"plan": ["a"]})
        self.call("update_plan", {"plan": ["a", "b"]})
        data = CaseRecord(self.root, self.run_id).data
        history = data["plan_history"]
        self.assertEqual([1, 2], [e["revision"] for e in history])
        self.assertEqual([["a"], ["a", "b"]], [e["steps"] for e in history])
        self.assertEqual(2, data["plan"]["revision"])
        self.assertEqual(["a", "b"], data["plan"]["steps"])

    def test_history_survives_reopening_the_record(self) -> None:
        self.call("update_plan", {"plan": ["a"]})
        reopened = CaseRecord(self.root, self.run_id).data
        self.assertEqual(1, len(reopened["plan_history"]))
        self.assertEqual(["a"], reopened["plan"]["steps"])

    def test_each_revision_is_audited(self) -> None:
        self.call("update_plan", {"plan": ["a"]})
        self.call("update_plan", {"plan": ["b"]})
        actions = [
            (a.get("action"), a.get("detail", {}).get("revision"))
            for a in CaseRecord(self.root, self.run_id).data.get("audit", [])
            if a.get("action") == "plan_updated"
        ]
        self.assertEqual([("plan_updated", 1), ("plan_updated", 2)], actions)

    def test_empty_plan_records_without_error(self) -> None:
        self.assertEqual({"plan": []}, self.call("update_plan", {"plan": []}))
        data = CaseRecord(self.root, self.run_id).data
        self.assertEqual([], data["plan"]["steps"])
        self.assertEqual(1, data["plan"]["revision"])

    def test_record_plan_takes_actor_and_reason(self) -> None:
        case = CaseRecord(self.root, self.run_id)
        entry = case.record_plan(["a"], actor="human", reason="scope narrowed")
        self.assertEqual(
            ("human", "scope narrowed", 1),
            (entry["actor"], entry["reason"], entry["revision"]),
        )
        self.assertEqual(
            "scope narrowed",
            CaseRecord(self.root, self.run_id).data["plan"]["reason"],
        )

    def test_record_plan_guards_non_list_steps(self) -> None:
        case = CaseRecord(self.root, self.run_id)
        entry = case.record_plan("not a list")
        self.assertEqual([], entry["steps"])

    def test_loop_run_leaves_plan_history_in_the_saved_record(self) -> None:
        fake = support.scripted(self.root, [support.CHECKLIST, support.PLAN])
        result = run_agent(root=self.root, client=fake.client, turns=5)
        history = support.case(self.root, result["run_id"])["plan_history"]
        self.assertEqual(1, len(history))
        self.assertEqual(support.PLAN[1]["plan"], history[0]["steps"])


if __name__ == "__main__":
    unittest.main()
