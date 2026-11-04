"""Judge unknown-escape: abstention fails closed, never blocks silently.

Pins the contract: a judge verdict of "unknown" means the supplied
material cannot decide the unit. It fails the assessment with a
judge-abstained problem (forcing human review via the refusal /
escalation path) instead of being rejected as a malformed verdict
(unavailable) or, worse, coerced into a pick. Uses a local stub
client so no shared fake infrastructure changes.
"""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from agent_kit import tools
from agent_kit.case_record import CaseRecord
from agent_kit.progress import new_run
from tests import support
from verification import verify_claims


class StubResponses:
    def __init__(self, results):
        self.results = results

    def create(self, **kwargs):
        return SimpleNamespace(
            output_text=json.dumps({"results": self.results}),
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
            model="stub-judge",
        )


def _memo():
    return {
        "conclusion": "Yes.",
        "gaps": "",
        "findings": [
            {
                "item": "not_ready",
                "status": "asserted",
                "summary": "Not ready.",
                "citations": [
                    {"document_id": "DOC-REPORT", "page": 1,
                     "quote": "Open Severity 1 defects: 21"},
                ],
            }
        ],
    }


class UnknownVerdictTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        self.run_id = new_run(self.root, instructions="i", objective="o")
        self.case = CaseRecord(self.root, self.run_id)
        self.case.set_checklist(support.CHECKLIST_ITEMS)

    def _run(self, verdicts):
        units = verify_claims.build_units(_memo(), [])
        results = []
        for unit in units:
            entry = {"id": unit["id"],
                     "verdict": verdicts.get(unit["id"], "supported")}
            if entry["verdict"] == "supported":
                entry["support"] = ["DOC-REPORT p1"]
            results.append(entry)
        client = SimpleNamespace(responses=StubResponses(results))
        ctx = tools.Context(client, self.root, self.run_id, self.case, True)
        return verify_claims.run_verification(ctx, _memo())

    # Blocked on reed-volans (verify_claims.py owner): remove these
    # decorators when the unknown escape lands; unexpected success
    # will flag the moment.
    def test_unknown_fails_closed_as_abstained(self) -> None:
        result = self._run({"finding:not_ready": "unknown"})
        self.assertEqual("failed", result["outcome"], result)
        self.assertFalse(result["passed"])
        self.assertTrue(
            any("judge-abstained" in p for p in result["feedback"]),
            result["feedback"],
        )

    def test_unknown_needs_no_support(self) -> None:
        # Abstention establishes nothing, so support lists are unjudged
        # (mirroring unsupported); the failure is the abstention itself.
        result = self._run({"finding:not_ready": "unknown"})
        self.assertEqual("failed", result["outcome"], result)
        self.assertFalse(
            any("no supporting references" in p for p in result["feedback"]),
            result["feedback"],
        )

    def test_supported_unsupported_unchanged(self) -> None:
        passed = self._run({})
        self.assertEqual("passed", passed["outcome"], passed)
        failed = self._run({"finding:not_ready": "unsupported"})
        self.assertEqual("failed", failed["outcome"], failed)
        self.assertFalse(
            any("judge-abstained" in p for p in failed["feedback"]),
            failed["feedback"],
        )

    def test_garbage_verdict_still_unavailable(self) -> None:
        result = self._run({"finding:not_ready": "maybe"})
        self.assertEqual("unavailable", result["outcome"], result)


if __name__ == "__main__":
    unittest.main()
