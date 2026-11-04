"""Contested advisories persist for the reviewer (closeout §6).

A one-sided filing on a contested item returns the advisory
in the submit result (peer's FiledAdvisoriesTest) AND
persists it on the memo-check record (submit/draft) with
rendering in manual verify --memo and the sign-off view
(peer fix, verified through this hard regression). If this
fails, advisories evaporate again and the filed record
shows an unqualified pass.
"""

from __future__ import annotations

import json
import unittest

from agent_kit.tools import submit_memo
from tests.test_quality_repair import QCCase, SUPPORTS


class AdvisoryPersistenceTest(QCCase):
    def test_filed_contested_advisory_persisted_for_reviewer(self) -> None:
        result = submit_memo.run(self.ctx, self.memo([SUPPORTS]))
        self.assertEqual("awaiting_signoff", result["status"], result)
        self.assertTrue(
            any("is contested" in a
                for a in result.get("advisories") or []),
            "advisory must at least be emitted (peer contract)",
        )
        record = json.dumps({
            "memo_checks": self.case.data.get("memo_checks"),
            "memo": self.case.data.get("memo"),
        })
        self.assertIn(
            "is contested", record,
            "filed record shows an unqualified pass: the contested "
            "advisory evaporated after the tool result",
        )


if __name__ == "__main__":
    unittest.main()
