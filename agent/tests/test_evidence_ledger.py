"""Milestone 4: evidence storage, provenance, versions, contradictions.

A fuller per-item ledger (ten facts, selective memo citations),
idempotent re-recording, fact provenance with stale-citation
detection, and contested-item surfacing. Offline only: synthetic
case, temporary workspaces, mocked clients.
"""

from __future__ import annotations

import hashlib
import json
import unittest

from agent_kit import case_record, tools
from agent_kit.case_record import CaseError, CaseRecord, FactLimitError
from agent_kit.progress import new_run
from agent_kit.tools.analyze_document import analyze
from tests import support
from verification import verify_claims, verify_memo

REPORT = ("DOC-REPORT", 1, "Open Severity 1 defects: 21")
GREEN = ("DOC-REPORT", 1, "Overall status is green")


def windows(text: str, n: int = 10) -> list:
    quotes = [text[i : i + 16] for i in range(0, 70, 7)][:n]
    assert len({q.strip().lower() for q in quotes}) == n
    assert all(len(q.strip()) >= 12 for q in quotes)
    return quotes


class LedgerCase(unittest.TestCase):
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
        self.case.set_checklist([support.checklist_item("not_ready")])
        analyze(self.fake.client, self.root, ["DOC-REPORT"], "x")

    def record(self, quote, stance="supports", claim="c"):
        return self.case.record_fact(
            "not_ready", "DOC-REPORT", 1, quote, claim, stance
        )

    def memo_citing(self, *quotes) -> dict:
        return {
            "conclusion": "c",
            "gaps": "",
            "findings": [
                {
                    "item": "not_ready",
                    "status": "asserted",
                    "summary": "s",
                    "citations": [
                        {"document_id": "DOC-REPORT", "page": 1, "quote": q}
                        for q in quotes
                    ],
                }
            ],
        }


class LedgerTest(LedgerCase):
    def test_ledger_holds_many_facts_per_item(self) -> None:
        analyze(self.fake.client, self.root, ["DOC-MINUTES"], "x")
        ids = [self.record(q)["id"] for q in windows(support.INITIAL["DOC-REPORT"][0])]
        eleventh = self.case.record_fact(
            "not_ready",
            "DOC-MINUTES",
            1,
            "Mr. Lindgren confirmed the system is ready for go-live",
            "c",
        )
        self.assertEqual([f"F{i + 1}" for i in range(10)], ids)
        self.assertEqual("F11", eleventh["id"])
        self.assertEqual(11, len(self.case.data["facts"]))

    def test_case_cap_is_an_explicit_resource_limit(self) -> None:
        self.assertEqual(500, case_record.MAX_FACTS_PER_CASE)
        original, case_record.MAX_FACTS_PER_CASE = case_record.MAX_FACTS_PER_CASE, 2
        self.addCleanup(setattr, case_record, "MAX_FACTS_PER_CASE", original)
        quotes = windows(support.INITIAL["DOC-REPORT"][0])
        self.record(quotes[0])
        self.record(quotes[1])
        with self.assertRaisesRegex(FactLimitError, "already has 2 facts"):
            self.record(quotes[2])

    def test_selective_citations_for_memos(self) -> None:
        self.assertEqual(3, case_record.MAX_CITATIONS_PER_FINDING)
        quotes = windows(support.INITIAL["DOC-REPORT"][0])
        for quote in quotes[:4]:
            self.record(quote)
        memo = self.memo_citing(*[f["quote"] for f in self.case.data["facts"][:3]])
        self.assertEqual([], verify_memo.run_verification(self.ctx, memo)["feedback"])
        over = self.memo_citing(*[f["quote"] for f in self.case.data["facts"][:4]])
        with self.assertRaisesRegex(CaseError, "cite its 3 strongest"):
            self.case.file_memo(over, "awaiting_signoff")

    def test_rerecord_is_idempotent(self) -> None:
        first = self.record(REPORT[2])
        again = self.record(REPORT[2])
        self.assertEqual(first["id"], again["id"])
        self.assertEqual(1, len(self.case.data["facts"]))
        self.assertIn("progress", again)
        changed_claim = self.case.record_fact(
            "not_ready", "DOC-REPORT", 1, REPORT[2], "different words"
        )
        self.assertEqual(first["id"], changed_claim["id"])
        self.assertEqual("c", changed_claim["claim"])
        other_stance = self.record(REPORT[2], "opposes")
        self.assertNotEqual(first["id"], other_stance["id"])

    def test_rerecord_at_cap_returns_the_fact(self) -> None:
        original, case_record.MAX_FACTS_PER_CASE = case_record.MAX_FACTS_PER_CASE, 2
        self.addCleanup(setattr, case_record, "MAX_FACTS_PER_CASE", original)
        quotes = windows(support.INITIAL["DOC-REPORT"][0])
        self.record(quotes[0])
        self.record(quotes[1])
        again = self.record(quotes[0])
        self.assertEqual("F1", again["id"])
        self.assertEqual(2, len(self.case.data["facts"]))

    def test_fact_carries_document_provenance(self) -> None:
        fact = self.record(REPORT[2])
        sha = hashlib.sha256(
            (self.root / "workspace/inbox/DOC-REPORT.pdf").read_bytes()
        ).hexdigest()
        self.assertEqual(sha, fact["sha256"])


class StaleTest(LedgerCase):
    def rewrite_and_reextract(self, text: str) -> None:
        (self.root / "workspace/inbox/DOC-REPORT.pdf").write_bytes(b"revised bytes")
        self.fake.responder.transcripts["DOC-REPORT"] = [
            {"page": 1, "text": text}
        ]
        analyze(self.fake.client, self.root, ["DOC-REPORT"], "x")

    def test_changed_document_makes_citations_stale(self) -> None:
        self.record(REPORT[2])
        self.rewrite_and_reextract("Weekly status report. Revised edition.")
        problems = verify_memo.run_verification(
            self.ctx, self.memo_citing(REPORT[2])
        )["feedback"]
        self.assertEqual(1, len(problems))
        self.assertIn("stale fact", problems[0])
        self.assertIn("document changed", problems[0])
        self.assertIn("re-record", problems[0])

    def test_fresh_citations_pass(self) -> None:
        self.record(REPORT[2])
        self.rewrite_and_reextract("Weekly status report. Revised edition.")
        self.record("Revised edition", claim="re-recorded")
        problems = verify_memo.run_verification(
            self.ctx, self.memo_citing("Revised edition")
        )["feedback"]
        self.assertEqual([], problems)

    def test_legacy_fact_without_sha_is_checked_by_quote(self) -> None:
        path = self.root / "workspace/case_records" / f"{self.run_id}.json"
        data = json.loads(path.read_text())
        data["facts"] = [
            {
                "id": "F1",
                "item": "not_ready",
                "document_id": "DOC-REPORT",
                "page": 1,
                "quote": "A verdict was reached unanimously on every count",
                "claim": "c",
                "at": "2026-01-01T00:00:00+00:00",
            }
        ]
        data["checklist"]["not_ready"]["status"] = "asserted"
        data["checklist"]["not_ready"]["facts"] = ["F1"]
        path.write_text(json.dumps(data))
        before = path.read_bytes()
        case = CaseRecord(self.root, self.run_id)
        ctx = tools.Context(
            self.fake.client, self.root, self.run_id, case, True
        )
        problems = verify_memo.run_verification(
            ctx,
            self.memo_citing("A verdict was reached unanimously on every count"),
        )["feedback"]
        self.assertEqual(1, len(problems))
        self.assertIn("stale fact", problems[0])
        self.assertNotIn("document changed", problems[0])
        self.assertEqual(before, path.read_bytes())

    def test_citation_to_a_removed_document_is_flagged(self) -> None:
        self.record(REPORT[2])
        (self.root / "workspace/inbox/DOC-REPORT.pdf").unlink()
        problems = verify_memo.run_verification(
            self.ctx, self.memo_citing(REPORT[2])
        )["feedback"]
        self.assertEqual(1, len(problems))
        self.assertIn("no longer in the inbox", problems[0])


class ContestedTest(LedgerCase):
    def test_mixed_stances_mark_the_item_contested(self) -> None:
        self.record(GREEN[2])
        self.assertEqual([], self.case.contested_items())
        self.record(REPORT[2], "opposes")
        self.assertEqual(["not_ready"], self.case.contested_items())
        progress = self.case.progress()
        self.assertEqual(["not_ready"], progress["contested"])
        self.assertIn("Contested: not_ready", progress["summary"])
        states = self.case.summary()["checklist"]
        self.assertTrue(states["not_ready"]["contested"])
        self.assertIn("not_ready", verify_claims.record_summary(self.ctx)["contested"])

    def test_contested_items_are_not_blocked_without_a_resolution_flow(self) -> None:
        self.record(GREEN[2])
        self.record(REPORT[2], "opposes")
        problems = verify_memo.run_verification(
            self.ctx, self.memo_citing(GREEN[2])
        )["feedback"]
        self.assertEqual([], problems)


class SupersessionTest(LedgerCase):
    def test_corrected_fact_links_to_the_one_it_supersedes(self) -> None:
        quotes = windows(support.INITIAL["DOC-REPORT"][0])
        first = self.record(quotes[0])
        second = self.case.record_fact(
            "not_ready", "DOC-REPORT", 1, quotes[1], "c", supersedes=first["id"]
        )
        self.assertEqual(first["id"], second["supersedes"])
        self.assertEqual("", first["supersedes"])
        # The original stays live: the link documents the correction,
        # it does not withdraw evidence.
        self.assertEqual(2, len(self.case.data["facts"]))

    def test_superseding_an_unknown_fact_is_refused(self) -> None:
        quotes = windows(support.INITIAL["DOC-REPORT"][0])
        with self.assertRaisesRegex(CaseError, "no live fact F9"):
            self.case.record_fact(
                "not_ready", "DOC-REPORT", 1, quotes[0], "c", supersedes="F9"
            )


class SharedQuotesTest(LedgerCase):
    def test_same_quote_in_two_documents_is_flagged(self) -> None:
        self.case.data["facts"] = [
            {"id": "F1", "document_id": "DOC-REPORT", "quote": "Overall status is green"},
            {"id": "F2", "document_id": "DOC-MINUTES", "quote": "overall STATUS is green"},
            {"id": "F3", "document_id": "DOC-MINUTES", "quote": "something else entirely"},
        ]
        shared = self.case.shared_quotes()
        self.assertEqual(1, len(shared))
        (ids,) = shared.values()
        self.assertEqual(["F1", "F2"], ids)
        self.assertEqual(shared, self.case.summary()["shared_quotes"])
        self.record(windows(support.INITIAL["DOC-REPORT"][0])[0])
        self.assertEqual(shared, self.case.data["shared_quotes"])


if __name__ == "__main__":
    unittest.main()
