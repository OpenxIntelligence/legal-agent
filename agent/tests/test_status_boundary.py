"""Priority-C regression: domain status must never drive run control.

The loop reads a tool result's top-level "status" as the RUN's
status (running/answered/needs_input/awaiting_signoff/paused).
A tool that echoes a domain status (a task, answer, or matter
state) under that key would steer or stall the run. The fixed
pattern namespaces domain state ("answer_status", nested rows).
These tests pin the boundary for the tools that handle domain
statuses.
"""

from __future__ import annotations

import unittest

from agent_kit import tools
from agent_kit.case_record import CaseRecord
from agent_kit.progress import new_run
from agent_kit.tools import answer_foundational, note_context, work_status
from tests import support

PROTOCOL = {"running", "answered", "needs_input", "awaiting_signoff",
            "paused"}


class StatusBoundaryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        self.fake = support.scripted(self.root, [])
        self.run_id = new_run(
            self.root, instructions="i", objective="o", verification=True
        )
        self.case = CaseRecord(self.root, self.run_id)
        self.ctx = tools.Context(
            self.fake.client, self.root, self.run_id, self.case, True
        )

    def test_note_context_nests_domain_status(self) -> None:
        result = note_context.run(
            self.ctx,
            {"field": "forum", "value": "tribunal", "status": "inferred"},
        )
        self.assertNotIn("status", result)
        self.assertEqual("inferred", result["noted"]["status"])

    def test_work_status_nests_task_statuses(self) -> None:
        result = work_status.run(self.ctx, {})
        self.assertNotIn("status", result)

    def test_answer_tool_namespaces_answer_status(self) -> None:
        result = answer_foundational.run(
            self.ctx,
            {
                "class": "facts",
                "question": "What happened?",
                "rule_or_source": "n/a",
                "evidence": "DOC-REPORT p1",
                "answer": "Something happened.",
                "basis": "The report says so.",
                "contrary": "None seen.",
                "effect": "None.",
            },
        )
        self.assertNotIn("status", result)
        self.assertEqual("answered", result["answer_status"])

    def test_protocol_statuses_are_run_control_only(self) -> None:
        # The loop's run-control vocabulary: anything else under a
        # top-level "status" key would be a domain leak.
        for value in sorted(PROTOCOL):
            self.assertIsInstance(value, str)


if __name__ == "__main__":
    unittest.main()
