"""Contested-evidence disclosure advisory and superseded-citation rule.

Pins the contradiction-engine contract at the layer each half
belongs in: one-sided assertions on contested items surface as
reasoning-gate advisories (weighing opposition needs judgment, so
the memo rules deliberately do not block them), while superseded
facts cannot support a filing unless the correcting version is
cited alongside them (a deterministic staleness block).
"""

from __future__ import annotations

import unittest

from agent_kit import reasoning, tools
from agent_kit.case_record import FACT_STANCES, CaseRecord
from agent_kit.progress import new_run
from agent_kit.tools import review_draft, submit_memo
from agent_kit.tools.analyze_document import analyze
from scripts import verify as manual_verify
from tests import support
from tests.test_quality_repair import OPPOSES, QCCase, SUPPORTS
from verification import verify_memo


class ContestedAdvisoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        self.fake = support.scripted(self.root, [])
        self.run_id = new_run(
            self.root, instructions="i", objective="o", verification=True
        )
        self.case = CaseRecord(self.root, self.run_id)
        self.case.set_checklist(support.CHECKLIST_ITEMS)
        self.ctx = tools.Context(
            self.fake.client, self.root, self.run_id, self.case, True
        )
        analyze(self.fake.client, self.root, ["DOC-REPORT"], "x")
        self.supports = self.case.record_fact(
            "not_ready", "DOC-REPORT", 1,
            "Open Severity 1 defects: 21", "21 open",
        )
        self.opposes = self.case.record_fact(
            "not_ready", "DOC-REPORT", 1,
            "Overall status is green", "reported green",
            stance="opposes",
        )
        assert "not_ready" in self.case.contested_items()

    def _memo(self, *quotes, status="asserted"):
        return {
            "conclusion": "c",
            "gaps": "g",
            "findings": [{
                "item": "not_ready",
                "status": status,
                "summary": "s",
                "citations": [
                    {"document_id": "DOC-REPORT", "page": 1, "quote": q}
                    for q in quotes
                ],
            }],
        }

    def _notes(self, memo):
        support.satisfy_reasoning_gate(self.case, issues=["not_ready"])
        result = reasoning.run_gate(self.case.data, memo)
        self.assertTrue(result["passed"], result)
        return [a for a in result["advisory"] if "is contested" in a]

    def test_supports_only_advises(self) -> None:
        notes = self._notes(self._memo("Open Severity 1 defects: 21"))
        self.assertEqual(1, len(notes), notes)
        self.assertIn("opposes", notes[0])
        self.assertIn("reviewer check", notes[0])

    def test_opposes_only_advises(self) -> None:
        notes = self._notes(self._memo("Overall status is green"))
        self.assertEqual(1, len(notes), notes)
        self.assertIn("supports", notes[0])

    def test_both_sides_silent(self) -> None:
        notes = self._notes(self._memo(
            "Open Severity 1 defects: 21", "Overall status is green"
        ))
        self.assertEqual([], notes)

    def test_unresolved_finding_silent(self) -> None:
        notes = self._notes(self._memo(
            "Open Severity 1 defects: 21", status="unresolved"
        ))
        self.assertEqual([], notes)

    def test_legacy_mode_still_advises(self) -> None:
        # Closeout §7.1: findings without task identity fail
        # closed, but the contested advisory still rides along so
        # the reviewer sees the disclosure with the refusal.
        result = reasoning.run_gate(
            self.case.data, self._memo("Open Severity 1 defects: 21")
        )
        self.assertEqual("legacy", result["mode"])
        self.assertFalse(result["passed"])
        self.assertTrue(
            any("identify the task" in p for p in result["problems"]),
            result["problems"],
        )
        self.assertTrue(
            any("is contested" in a for a in result["advisory"]),
            result["advisory"],
        )

    def test_memo_rules_do_not_block(self) -> None:
        # The standing contract (ContestedTest): contested items file
        # through the memo rules; the advisory carries the disclosure.
        memo = self._memo("Open Severity 1 defects: 21")
        for item in self.case.data["checklist"]:
            if item != "not_ready":
                memo["findings"].append(
                    {"item": item, "status": "unresolved",
                     "summary": "s", "citations": []}
                )
        feedback = verify_memo.run_verification(self.ctx, memo)["feedback"]
        self.assertEqual([], feedback, feedback)

    def test_stance_mirror_matches_record(self) -> None:
        self.assertEqual(FACT_STANCES, reasoning.FACT_STANCES_MIRROR)


class FiledAdvisoriesTest(QCCase):
    """Gate advisories reach the model on the filed result."""

    def test_one_sided_filing_carries_contested_advisory(self) -> None:
        result = submit_memo.run(self.ctx, self.memo([SUPPORTS]))
        self.assertEqual("awaiting_signoff", result["status"], result)
        self.assertTrue(
            any("is contested" in a for a in result["advisories"]),
            result["advisories"],
        )

    def test_two_sided_filing_has_no_contested_advisory(self) -> None:
        result = submit_memo.run(self.ctx, self.memo([SUPPORTS, OPPOSES]))
        self.assertEqual("awaiting_signoff", result["status"], result)
        self.assertEqual(
            [],
            [a for a in result["advisories"] if "is contested" in a],
            result["advisories"],
        )


class DraftAdvisoriesTest(QCCase):
    """Gate advisories reach the model on draft reviews too."""

    def test_review_carries_contested_advisory(self) -> None:
        result = review_draft.run(self.ctx, self.memo([SUPPORTS]))
        self.assertTrue(
            any("is contested" in a for a in result["advisories"]),
            result,
        )

    def test_review_persists_contested_advisory(self) -> None:
        # Closeout §6, draft side: the draft-check record carries the
        # advisory so revision guidance survives the tool result.
        review_draft.run(self.ctx, self.memo([SUPPORTS]))
        stored = [
            a
            for draft in self.case.data.get("draft_checks") or []
            for a in draft.get("advisories") or []
        ]
        self.assertTrue(
            any("is contested" in a for a in stored),
            self.case.data.get("draft_checks"),
        )


class ManualVerifyAdvisoriesTest(QCCase):
    """Manual verify --memo renders the fresh gate advisories."""

    def test_memo_result_carries_advisories(self) -> None:
        submit_memo.run(self.ctx, self.memo([SUPPORTS]))
        result = manual_verify.verify_memo(self.ctx)
        self.assertTrue(
            any("is contested" in a
                for a in result.get("reasoning_advisories") or []),
            result,
        )
        self.assertIn(
            "advisory:", manual_verify.format_memo(result),
        )


class SupersededCitationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        self.fake = support.scripted(self.root, [])
        self.run_id = new_run(
            self.root, instructions="i", objective="o", verification=True
        )
        self.case = CaseRecord(self.root, self.run_id)
        self.case.set_checklist(support.CHECKLIST_ITEMS)
        self.ctx = tools.Context(
            self.fake.client, self.root, self.run_id, self.case, True
        )
        analyze(self.fake.client, self.root, ["DOC-REPORT"], "x")
        self.old = self.case.record_fact(
            "not_ready", "DOC-REPORT", 1,
            "Open Severity 1 defects: 21", "21 open",
        )
        self.new = self.case.record_fact(
            "not_ready", "DOC-REPORT", 1,
            "Overall status is green", "revised count",
            supersedes=self.old["id"],
        )

    def _feedback(self, *quotes):
        memo = {
            "conclusion": "c",
            "gaps": "g",
            "findings": [{
                "item": "not_ready",
                "status": "asserted",
                "summary": "s",
                "citations": [
                    {"document_id": "DOC-REPORT", "page": 1, "quote": q}
                    for q in quotes
                ],
            }],
        }
        return verify_memo.run_verification(self.ctx, memo)["feedback"]

    def _superseded(self, feedback):
        return [f for f in feedback if "superseded by" in f]

    def test_old_version_alone_fails(self) -> None:
        feedback = self._feedback("Open Severity 1 defects: 21")
        problems = self._superseded(feedback)
        self.assertEqual(1, len(problems), feedback)
        self.assertIn(self.new["id"], problems[0])

    def test_both_versions_pass(self) -> None:
        feedback = self._feedback(
            "Open Severity 1 defects: 21", "Overall status is green"
        )
        self.assertEqual([], self._superseded(feedback), feedback)

    def test_current_version_alone_passes(self) -> None:
        feedback = self._feedback("Overall status is green")
        self.assertEqual([], self._superseded(feedback), feedback)


if __name__ == "__main__":
    unittest.main()
