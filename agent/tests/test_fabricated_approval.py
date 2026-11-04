"""Model-generated approval grants must be rejected (closeout §7).

Fixed: decide_approval is no longer model-offered (gated out
of names()/definitions()), Context carries a server-derived
actor, run() refuses model actors and confused-deputy
decided_by values, and decisions record only through the
trusted operator path bound to the recorded request. This
regression pins the request -> fabricated-grant rejection and
the empty decision record. External execution does not exist
today; any future execute tool inherits this trust boundary.
"""

from __future__ import annotations

import unittest

from agent_kit import tools
from agent_kit.case_record import CaseRecord
from agent_kit.progress import new_run
from tests import support


class FabricatedApprovalTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        self.run_id = new_run(self.root, instructions="i", objective="o")
        self.fake = support.scripted(self.root, [])

    def _call(self, name, args):
        return tools.run(
            self.fake.client, self.root, self.run_id,
            {"name": name, "arguments": args, "call_id": "call-1"},
        )

    def test_model_grant_with_fabricated_reviewer_rejected(self) -> None:
        requested = self._call(
            "request_approval",
            {"action": "file the notice", "scope": "one filing",
             "reason": "deadline"},
        )
        self.assertEqual("APR1", requested["requested"])
        granted = self._call(
            "decide_approval",
            {"approval_id": "APR1", "decision": "grant",
             "decided_by": "Not A Real Attorney"},
        )
        self.assertIn(
            "error", granted,
            f"model-supplied identity was recorded as a human "
            f"decision: {granted}",
        )
        record = CaseRecord(self.root, self.run_id)
        approvals = [a for a in (record.data.get("approvals") or [])
                     if isinstance(a, dict)]
        self.assertEqual([], [a for a in approvals if a.get("decision")])


if __name__ == "__main__":
    unittest.main()
