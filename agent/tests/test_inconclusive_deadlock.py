"""Priority-A regression: the inconclusive-result deadlock must not recur.

Historical failure: quote-backed facts left a question marked
"proven", signature checks blocked asserting it, and the claims
judge rejected an unresolved answer as conflicting with the
recorded "proven" status — with reviewer notes and resubmissions
looping forever.

Current code separates these states (B vocabulary, a memo-rule
escape directing unresolved+gaps, a judge prompt that supports
bounded unresolved findings, and mark_answered recording the
resolution). This fixture pins that behavior on synthetic data:
it reproduces the historical *conditions* and asserts the current
*honest* outcome at each boundary.
"""

from __future__ import annotations

import unittest

from agent_kit import tools
from agent_kit.case_record import CaseRecord, effective_status
from agent_kit.progress import new_run
from agent_kit.tools.analyze_document import analyze
from tests import support
from verification import verify_claims, verify_memo

QUOTE = "Mr. Lindgren confirmed the system is ready for go-live."


class DeadlockCase(unittest.TestCase):
    """One signature-gated item, one exact quote, one unclear check."""

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
        self.case.set_checklist(
            [support.checklist_item("authority", signature=True)]
        )
        analyze(self.fake.client, self.root, ["DOC-MINUTES"], "x")
        self.case.record_fact(
            "authority",
            "DOC-MINUTES",
            1,
            QUOTE,
            "the minutes state Lindgren confirmed readiness",
        )
        self.case.add_signature_check(
            {
                "document_id": "DOC-MINUTES",
                "page": 1,
                "signer": "Erik Lindgren",
                "verdict": "unclear",
            }
        )

    def memo(self, status: str) -> dict:
        return {
            "conclusion": "Readiness is stated in the minutes.",
            "findings": [
                {
                    "item": "authority",
                    "status": status,
                    "summary": "The minutes state confirmation but the "
                               "signature comparison is unclear.",
                    "citations": [
                        {"document_id": "DOC-MINUTES", "page": 1,
                         "quote": QUOTE}
                    ],
                }
            ],
            "gaps": "authority: signature comparison unclear; attribution "
                    "unresolved",
        }

    def test_asserted_refused_with_named_escape(self) -> None:
        result = verify_memo.run_verification(self.ctx, self.memo("asserted"))
        self.assertFalse(result["passed"])
        joined = " ".join(result["feedback"])
        self.assertIn("cannot be asserted", joined)
        self.assertIn("as a gap", joined)

    def test_unresolved_passes_memo_rules(self) -> None:
        result = verify_memo.run_verification(
            self.ctx, self.memo("unresolved")
        )
        self.assertEqual([], result["feedback"])
        self.assertTrue(result["passed"])

    def test_supported_unresolved_completes_as_unresolved(self) -> None:
        result = verify_claims.run_verification(
            self.ctx, self.memo("unresolved")
        )
        self.assertTrue(result["passed"])
        self.assertEqual(
            "supported", result["verdicts"].get("finding:authority")
        )
        # The exact completion submit_memo performs for a supported
        # unresolved finding: answered, but recorded as unresolved —
        # never rewritten as an established positive.
        self.case.mark_answered(["authority"], {"authority": "unresolved"})
        entry = self.case.data["checklist"]["authority"]
        self.assertEqual("answered", entry["status"])
        self.assertEqual("unresolved", entry["resolution"])
        self.assertEqual(
            "answered",
            effective_status(entry, self.case.data.get("memo_checks", [])),
        )

    def test_recording_facts_marks_nothing_proven(self) -> None:
        entry = self.case.data["checklist"]["authority"]
        self.assertEqual("open", entry["status"])
        blob = repr(self.case.data)
        self.assertNotIn("proven", blob.replace("provenance", ""))

    def test_legacy_proven_reads_open_without_passed_claims(self) -> None:
        self.assertEqual("open", effective_status({"status": "proven"}, []))
        checks = [{"check": "claims", "outcome": "passed"}]
        self.assertEqual(
            "answered", effective_status({"status": "proven"}, checks)
        )


if __name__ == "__main__":
    unittest.main()
