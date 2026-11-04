"""M3: flagged (privileged/confidential) documents must refuse analysis on EVERY read path.

The analyze_document tool gate refuses flagged docs, but the parallel sweep path
(pool.sweep -> sweep_job -> inner analyze) admitted them: flagged content reached
the model and merged as evidence. Regression: both paths refuse loudly, nothing
is spent or merged, and unflagged sweeps still work. All fixtures synthetic.
"""

from __future__ import annotations

import json
import unittest

from agent_kit import tools
from agent_kit.case_record import CaseRecord
from agent_kit.progress import new_run
from tests import support
from tests.test_kit import call

ITEMS = [
    {"id": "alpha", "question": "Was alpha true?", "evidence_needed": "a record of alpha"},
]


class SweepConfidentialCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        self.fake = support.scripted(self.root, [])
        self.run_id = new_run(
            self.root, instructions="i", objective="o", verification=True
        )
        self.case = CaseRecord(self.root, self.run_id)
        self.case.set_checklist(ITEMS)

    def secret(self, doc: str) -> str:
        return support.INITIAL[doc][0][:40]

    def test_sweep_tool_refuses_flagged_doc(self) -> None:
        doc = "DOC-MINUTES"
        CaseRecord(self.root, self.run_id).flag_privileged(doc, "synthetic privilege")
        out = tools.run(
            self.fake.client,
            self.root,
            self.run_id,
            call("sweep_issues", {"sweeps": [{"issue": "alpha", "document_ids": [doc]}]}),
        )
        self.assertIn("error", out)
        self.assertIn("confidential", json.dumps(out).lower())
        self.assertNotIn("sweeps", out)
        self.assertNotIn(self.secret(doc), json.dumps(self.fake.calls, default=str))

    def test_analyze_tool_refuses_flagged_doc(self) -> None:
        doc = "DOC-REPORT"
        CaseRecord(self.root, self.run_id).flag_privileged(doc, "synthetic privilege")
        out = tools.run(
            self.fake.client,
            self.root,
            self.run_id,
            call("analyze_document", {"document_ids": [doc], "request": "status"}),
        )
        self.assertIn("error", out)
        self.assertIn("confidential", json.dumps(out).lower())
        self.assertNotIn(self.secret(doc), json.dumps(self.fake.calls, default=str))

    def test_unflagged_sweep_still_merges(self) -> None:
        out = tools.run(
            self.fake.client,
            self.root,
            self.run_id,
            call(
                "sweep_issues",
                {"sweeps": [{"issue": "alpha", "document_ids": ["DOC-REPORT"]}]},
            ),
        )
        self.assertEqual(["merged"], [s.get("status") for s in out.get("sweeps", [])])


if __name__ == "__main__":
    unittest.main()
