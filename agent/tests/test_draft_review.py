"""Increment 1: draft/incremental claim checks before final filing.

review_draft runs the memo rules, then the claims judge, on a draft
without filing it, counting a refusal, settling an item, or ending
the run. All fixtures synthetic; judges scripted, never live.
"""

from __future__ import annotations

import copy
import unittest

from agent_kit import tools
from agent_kit.case_record import MAX_DRAFT_CHECKS, CaseRecord
from agent_kit.progress import new_run
from agent_kit.tools.analyze_document import analyze
from tests import support
from tests.test_claims_v2 import break_claims, claims_inputs
from tests.test_kit import call
from tests.test_verification import (
    AFTER_RELEASE,
    ALL_INITIAL,
    FACTS,
    GOOD_MEMO,
)


class DraftReviewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        self.fake = support.scripted(self.root, [])
        self.run_id = new_run(
            self.root, instructions="i", objective="o", verification=True
        )
        self.case = CaseRecord(self.root, self.run_id)
        self.case.set_checklist(support.CHECKLIST_ITEMS)
        analyze(self.fake.client, self.root, ALL_INITIAL, "x")
        support.release(self.root)
        analyze(self.fake.client, self.root, ["DOC-DRAFT", "DOC-CERT"], "x")
        for _name, args in FACTS + [AFTER_RELEASE[1], AFTER_RELEASE[3]]:
            self.case.record_fact(
                args["item"],
                args["document_id"],
                args["page"],
                args["quote"],
                args["claim"],
            )
        self.case.add_signature_check(
            {
                "document_id": "DOC-CERT",
                "page": 1,
                "signer": "Dana Okafor",
                "verdict": "consistent",
            }
        )
        support.satisfy_reasoning_gate(self.case)

    def draft(self, memo: dict) -> dict:
        return tools.run(
            self.fake.client,
            self.root,
            self.run_id,
            call("review_draft", copy.deepcopy(memo)),
        )

    def fresh_case(self) -> CaseRecord:
        return CaseRecord(self.root, self.run_id)

    def test_supported_draft_passes_without_filing(self) -> None:
        result = self.draft(GOOD_MEMO[1])
        self.assertTrue(result["passed"])
        self.assertEqual("claims", result["check"])
        self.assertEqual("passed", result["outcome"])
        self.assertIsInstance(result["scope"], dict)
        self.assertTrue(result["verifier"])
        self.assertNotIn("status", result)
        case = self.fresh_case()
        self.assertIsNone(case.data["memo"])
        self.assertEqual(0, case.memo_refusals())
        self.assertEqual([], case.data["memo_checks"])
        self.assertEqual(0, result["facts_since_assessment"])
        drafts = case.data["draft_checks"]
        self.assertEqual(1, len(drafts))
        self.assertEqual("claims", drafts[0]["check"])
        self.assertEqual("passed", drafts[0]["outcome"])
        self.assertTrue(drafts[0]["memo_version"])
        self.assertTrue(drafts[0]["evidence_version"])
        # Nothing settled: the items stay open for the filing gate.
        self.assertTrue(case.progress()["open"])

    def test_unsupported_draft_fails_without_refusal(self) -> None:
        fake = support.scripted(
            self.root,
            [],
            unsupported=lambda text: [
                {"unit": "conclusion", "reason": "goes beyond the cited quotes"}
            ],
        )
        result = tools.run(
            fake.client, self.root, self.run_id, call("review_draft", GOOD_MEMO[1])
        )
        self.assertFalse(result["passed"])
        self.assertEqual("failed", result["outcome"])
        self.assertIn("conclusion", result["problems"][0])
        self.assertNotIn("status", result)
        case = self.fresh_case()
        self.assertIsNone(case.data["memo"])
        self.assertEqual(0, case.memo_refusals())
        self.assertEqual([], case.data["memo_checks"])
        self.assertEqual(0, case.data["facts_since_assessment"])

    def test_rules_failure_skips_the_judge(self) -> None:
        memo = copy.deepcopy(GOOD_MEMO[1])
        del memo["findings"][0]["status"]
        before = self.case.data.get("facts_since_assessment", 0)
        self.assertGreater(before, 0)
        result = self.draft(memo)
        self.assertFalse(result["passed"])
        self.assertEqual("memo", result["check"])
        self.assertEqual("failed", result["outcome"])
        self.assertTrue(result["problems"])
        self.assertEqual([], claims_inputs(self.fake))
        # No claims assessment happened, so the nudge counter stands.
        self.assertEqual(
            before, self.fresh_case().data["facts_since_assessment"]
        )

    def test_unavailable_draft_stays_unavailable(self) -> None:
        break_claims(self.fake, "{not valid json")
        before = self.case.data.get("facts_since_assessment", 0)
        result = self.draft(GOOD_MEMO[1])
        self.assertFalse(result["passed"])
        self.assertEqual("unavailable", result["outcome"])
        self.assertNotIn("status", result)
        case = self.fresh_case()
        self.assertIsNone(case.data["memo"])
        self.assertEqual(0, case.memo_refusals())
        self.assertEqual("unavailable", case.data["draft_checks"][0]["outcome"])
        self.assertEqual(before, case.data["facts_since_assessment"])

    def test_draft_history_capped_with_versions(self) -> None:
        memo = {"conclusion": "c", "findings": [], "gaps": ""}
        for n in range(MAX_DRAFT_CHECKS + 1):
            self.case.record_draft_check(
                check="claims",
                passed=True,
                outcome="passed",
                problems=[],
                memo=dict(memo, gaps=f"g{n}"),
            )
        drafts = self.fresh_case().data["draft_checks"]
        self.assertEqual(MAX_DRAFT_CHECKS, len(drafts))
        self.assertEqual(
            list(range(2, MAX_DRAFT_CHECKS + 2)), [d["n"] for d in drafts]
        )
        versions = {d["memo_version"] for d in drafts}
        self.assertEqual(MAX_DRAFT_CHECKS, len(versions))
        audit = [
            a for a in self.fresh_case().data["audit"] if a["action"] == "draft_checked"
        ]
        self.assertEqual(MAX_DRAFT_CHECKS + 1, len(audit))

    def test_nudge_appears_after_three_unassessed_facts(self) -> None:
        summary = self.case.progress()["summary"]
        self.assertIn("facts recorded since the last claims assessment", summary)
        self.assertEqual(6, self.case.progress()["facts_since_assessment"])
        single = self.case.record_fact(
            "not_ready", "DOC-REPORT", 1, "Overall status is green", "c"
        )
        self.assertEqual(7, single["progress"]["facts_since_assessment"])
        self.case.record_draft_check(
            check="claims",
            passed=True,
            outcome="passed",
            problems=[],
            memo={"conclusion": "c", "findings": [], "gaps": ""},
        )
        summary = self.fresh_case().progress()["summary"]
        self.assertNotIn("since the last claims assessment", summary)

    def test_prompt_covers_batch_draft_and_corrected_vocabulary(self) -> None:
        prompt = support.instructions_file()
        self.assertIn("record_facts", prompt)
        self.assertIn("review_draft", prompt)
        self.assertIn("never settles", prompt)
        self.assertIn("answered or escalated", prompt)
        self.assertIn("re-check the findings", prompt)
        self.assertNotIn("only `supports` facts settle", prompt)
        self.assertNotIn("proven or escalated", prompt)
        self.assertNotIn("cannot be proven", prompt)

    def test_draft_tool_unknown_without_verification(self) -> None:
        run_id = new_run(
            self.root, instructions="i", objective="o", verification=False
        )
        result = tools.run(
            self.fake.client, self.root, run_id, call("review_draft", GOOD_MEMO[1])
        )
        self.assertIn("unknown tool", result["error"])


if __name__ == "__main__":
    unittest.main()
