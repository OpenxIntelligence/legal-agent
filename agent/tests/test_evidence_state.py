"""Milestone 2: evidence collected versus claim supported (fact stance).

Recording a quote never settles its item: collection, stance, assessed
support, and issue resolution are separate states, and only a passed
assessment marks an item answered.
Offline only: synthetic case, temporary workspaces, mocked clients.
"""

from __future__ import annotations

import json
import unittest

from agent_kit import tools
from agent_kit.case_record import CaseRecord, CaseError, stance_of
from agent_kit.progress import new_run
from agent_kit.tools import record_fact
from agent_kit.tools.analyze_document import analyze
from tests import support
from verification import verify_memo

REPORT = ("DOC-REPORT", 1, "Open Severity 1 defects: 21")
GREEN = ("DOC-REPORT", 1, "Overall status is green")
READY = ("DOC-MINUTES", 1, "Mr. Lindgren confirmed the system is ready for go-live")
MEMO = ("DOC-MEMO", 1, "Attorney work product for the review team")


class StanceCase(unittest.TestCase):
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

    def checklist(self, *ids) -> None:
        self.case.set_checklist([support.checklist_item(i) for i in ids])

    def read(self, *ids) -> None:
        analyze(self.fake.client, self.root, list(ids), "x")

    def record(self, item, doc, stance="supports"):
        doc_id, page, quote = doc
        return self.case.record_fact(item, doc_id, page, quote, "c", stance)


class StanceTest(StanceCase):
    def test_stance_round_trips_and_defaults_to_supports(self) -> None:
        self.checklist("not_ready", "timing", "seniority")
        self.read("DOC-REPORT", "DOC-MINUTES")
        self.assertEqual(
            "supports", self.record("not_ready", REPORT)["stance"]
        )
        self.assertEqual(
            "opposes", self.record("timing", GREEN, "opposes")["stance"]
        )
        self.assertEqual(
            "context", self.record("seniority", READY, "context")["stance"]
        )

    def test_invalid_stance_is_refused(self) -> None:
        self.checklist("not_ready")
        self.read("DOC-REPORT")
        with self.assertRaisesRegex(CaseError, "must be one of"):
            self.record("not_ready", REPORT, "suggests")
        self.assertEqual([], self.case.data["facts"])

    def test_tool_accepts_and_documents_stance(self) -> None:
        self.checklist("not_ready")
        self.read("DOC-REPORT")
        self.assertEqual(
            ["supports", "opposes", "context"],
            record_fact.DEFINITION["parameters"]["properties"]["stance"][
                "enum"
            ],
        )
        out = record_fact.run(
            self.ctx,
            {
                "item": "not_ready",
                "document_id": "DOC-REPORT",
                "page": 1,
                "quote": REPORT[2],
                "claim": "c",
                "stance": "opposes",
            },
        )
        self.assertEqual("opposes", out["recorded"]["stance"])
        out = record_fact.run(
            self.ctx,
            {
                "item": "not_ready",
                "document_id": "DOC-REPORT",
                "page": 1,
                "quote": GREEN[2],
                "claim": "c",
            },
        )
        self.assertEqual("supports", out["recorded"]["stance"])

    def test_recording_never_settles_the_item(self) -> None:
        self.checklist("not_ready", "timing", "seniority")
        self.read("DOC-REPORT", "DOC-MINUTES")
        self.record("not_ready", REPORT, "opposes")
        self.record("timing", GREEN, "context")
        self.record("seniority", READY)
        self.record("not_ready", GREEN)
        for item in ("not_ready", "timing", "seniority"):
            self.assertEqual("open", self.case.data["checklist"][item]["status"])
        self.assertEqual(["seniority"], self.case.mark_answered(["seniority"]))
        self.assertEqual(
            "answered", self.case.data["checklist"]["seniority"]["status"]
        )

    def test_recording_leaves_an_escalated_item_escalated(self) -> None:
        self.checklist("timing")
        self.read("DOC-REPORT")
        self.case.open_request("timing", "a dated assessment", "missing_evidence", [])
        self.record("timing", GREEN, "opposes")
        self.record("timing", REPORT)
        self.assertEqual(
            "escalated", self.case.data["checklist"]["timing"]["status"]
        )
        self.assertEqual([], self.case.mark_answered(["timing"]))
        self.assertEqual(
            "escalated", self.case.data["checklist"]["timing"]["status"]
        )

    def test_withdrawal_archives_facts_and_reopens_answered_items(self) -> None:
        self.checklist("not_ready", "timing")
        self.read("DOC-REPORT", "DOC-MEMO")
        self.record("not_ready", REPORT)
        self.record("not_ready", MEMO, "opposes")
        self.record("timing", MEMO)
        self.case.mark_answered(["not_ready", "timing"])
        self.case.flag_privileged("DOC-MEMO", "work product")
        archived = self.case.data["withdrawn_facts"]
        self.assertEqual(2, len(archived))
        self.assertTrue(all("work product" in f["withdrawn_reason"] for f in archived))
        self.assertEqual("open", self.case.data["checklist"]["not_ready"]["status"])
        self.assertEqual("open", self.case.data["checklist"]["timing"]["status"])
        self.assertNotIn("F2", [f["id"] for f in self.case.data["facts"]])
        fourth = self.record("not_ready", GREEN)
        self.assertEqual("F4", fourth["id"])

    def test_progress_names_collected_but_unassessed_items(self) -> None:
        self.checklist("not_ready", "timing")
        self.read("DOC-REPORT")
        self.record("timing", GREEN, "opposes")
        summary = self.case.progress()["summary"]
        self.assertIn("Collected but unassessed: timing", summary)
        self.assertIn("Open:", summary)
        states = self.case.summary()["checklist"]
        self.assertFalse(states["timing"]["supported"])
        self.assertFalse(states["not_ready"]["supported"])

    def test_asserted_finding_may_cite_opposing_evidence(self) -> None:
        self.checklist("not_ready")
        self.read("DOC-REPORT")
        self.record("not_ready", REPORT, "opposes")
        memo = {
            "conclusion": "c",
            "gaps": "",
            "findings": [
                {
                    "item": "not_ready",
                    "status": "asserted",
                    "summary": "s",
                    "citations": [
                        {
                            "document_id": REPORT[0],
                            "page": REPORT[1],
                            "quote": REPORT[2],
                        }
                    ],
                }
            ],
        }
        result = verify_memo.run_verification(self.ctx, memo)
        self.assertTrue(result["passed"], result["feedback"])

    def test_asserted_finding_without_any_fact_is_refused(self) -> None:
        self.checklist("not_ready")
        self.read("DOC-REPORT")
        memo = {
            "conclusion": "c",
            "gaps": "",
            "findings": [
                {
                    "item": "not_ready",
                    "status": "asserted",
                    "summary": "s",
                    "citations": [
                        {
                            "document_id": REPORT[0],
                            "page": REPORT[1],
                            "quote": REPORT[2],
                        }
                    ],
                }
            ],
        }
        problems = verify_memo.run_verification(self.ctx, memo)["feedback"]
        self.assertTrue(any("no recorded fact" in p for p in problems), problems)

    def test_finding_for_unknown_item_is_refused(self) -> None:
        self.checklist("not_ready")
        memo = {
            "conclusion": "c",
            "gaps": "",
            "findings": [
                {
                    "item": "ghost",
                    "status": "asserted",
                    "summary": "s",
                    "citations": [],
                },
                {
                    "item": "not_ready",
                    "status": "unresolved",
                    "summary": "s",
                    "citations": [],
                },
            ],
        }
        problems = verify_memo.run_verification(self.ctx, memo)["feedback"]
        self.assertTrue(
            any("unknown checklist item ghost" in p for p in problems), problems
        )

    def test_legacy_facts_read_as_supports_without_rewriting(self) -> None:
        path = self.root / "workspace/case_records" / f"{self.run_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        fact = {
            "id": "F1",
            "item": "not_ready",
            "document_id": REPORT[0],
            "page": REPORT[1],
            "quote": REPORT[2],
            "claim": "c",
            "at": "2026-01-01T00:00:00+00:00",
        }
        self.assertNotIn("stance", fact)
        path.write_text(
            json.dumps(
                {
                    "run_id": self.run_id,
                    "created": "2026-01-01T00:00:00+00:00",
                    "checklist": {
                        "not_ready": {
                            "question": "q",
                            "evidence_needed": "e",
                            "needs_signature_check": False,
                            "status": "asserted",
                            "facts": ["F1"],
                            "request": None,
                        }
                    },
                    "checklist_checks": [],
                    "memo_checks": [],
                    "read_ids": [],
                    "facts": [fact],
                    "rejected_facts": [],
                    "flags": [],
                    "signature_checks": [],
                    "requests": [],
                    "memo": None,
                    "audit": [],
                }
            )
        )
        before = path.read_bytes()
        case = CaseRecord(self.root, self.run_id)
        self.assertEqual("supports", stance_of(case.data["facts"][0]))
        self.assertTrue(case.summary()["checklist"]["not_ready"]["supported"])
        self.read("DOC-REPORT")
        ctx = tools.Context(
            self.fake.client, self.root, self.run_id, case, True
        )
        memo = {
            "conclusion": "c",
            "gaps": "",
            "findings": [
                {
                    "item": "not_ready",
                    "status": "asserted",
                    "summary": "s",
                    "citations": [
                        {
                            "document_id": REPORT[0],
                            "page": REPORT[1],
                            "quote": REPORT[2],
                        }
                    ],
                }
            ],
        }
        problems = verify_memo.run_verification(ctx, memo)["feedback"]
        self.assertEqual([], problems)
        self.assertEqual(before, path.read_bytes())


if __name__ == "__main__":
    unittest.main()
