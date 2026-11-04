"""Milestone 6: source inspection and human-review workspace.

Qualifications persist with approvals, structured review comments
reach the agent on rejection, memo revisions are retained, and
source pages are servable for inspection. Offline only: synthetic
fixtures and temporary workspaces. Page rendering itself is not
inspected (no browser tooling); the data paths behind it are.
"""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from agent_kit.case_record import CaseRecord
from agent_kit.progress import new_run, resume, save_progress
from agent_kit.tools import submit_memo
from tests import support
from tests.test_claims_v2 import ClaimsCase
from tests.test_verification import GOOD_MEMO


class ReviewCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        self.run_id = new_run(
            self.root, instructions="i", objective="o", verification=False
        )
        self.case = CaseRecord(self.root, self.run_id)

    def legacy_record(self, memo) -> Path:
        path = self.root / "workspace/case_records" / f"{self.run_id}.json"
        path.write_text(
            json.dumps(
                {
                    "run_id": self.run_id,
                    "created": "2026-01-01T00:00:00+00:00",
                    "checklist": {},
                    "checklist_checks": [],
                    "memo_checks": [],
                    "read_ids": [],
                    "facts": [],
                    "rejected_facts": [],
                    "flags": [],
                    "signature_checks": [],
                    "requests": [],
                    "memo": memo,
                    "audit": [],
                }
            )
        )
        return path


class QualificationsTest(ReviewCase):
    def test_qualifications_persist_with_approval(self) -> None:
        self.case.file_memo(copy.deepcopy(support.MEMO[1]), "awaiting_signoff")
        memo = self.case.review_memo(
            True, "ok", qualifications=["Subject to X", "Pending Y", "  "]
        )
        self.assertEqual("approved", memo["status"])
        self.assertEqual(["Subject to X", "Pending Y"], memo["review"]["qualifications"])
        reloaded = CaseRecord(self.root, self.run_id)
        self.assertEqual(
            ["Subject to X", "Pending Y"],
            reloaded.data["memo"]["review"]["qualifications"],
        )

    def test_legacy_review_gains_no_invented_fields(self) -> None:
        path = self.legacy_record(
            {
                "conclusion": "c",
                "findings": [],
                "gaps": "",
                "status": "rejected",
                "filed": "2026-01-01T00:00:00+00:00",
                "review": {
                    "decision": "rejected",
                    "notes": "fix",
                    "at": "2026-01-01T00:00:00+00:00",
                },
            }
        )
        before = path.read_bytes()
        case = CaseRecord(self.root, self.run_id)
        self.assertNotIn("qualifications", case.data["memo"]["review"])
        self.assertNotIn("comments", case.data["memo"]["review"])
        self.assertNotIn("memo_history", case.data)
        self.assertEqual(before, path.read_bytes())


class CommentsTest(ReviewCase):
    def test_comments_normalize_and_reach_the_agent(self) -> None:
        self.case.file_memo(copy.deepcopy(support.MEMO[1]), "awaiting_signoff")
        memo = self.case.review_memo(
            False,
            "fix it",
            comments=[
                {"target": "timing", "text": "date it"},
                "overall pass",
                {"target": "", "text": "  "},
            ],
        )
        self.assertEqual(
            [
                {"target": "timing", "text": "date it", "status": "open"},
                {"target": "general", "text": "overall pass", "status": "open"},
            ],
            memo["review"]["comments"],
        )
        save_progress(self.root, self.run_id, [], status="awaiting_signoff")
        step = resume(self.root, self.run_id)
        self.assertIn("[timing] date it", step["message"])
        self.assertIn("[general] overall pass", step["message"])
        self.assertIn("revised memo", step["message"])

    def test_legacy_rejection_message_unchanged(self) -> None:
        self.legacy_record(
            {
                "conclusion": "c",
                "findings": [],
                "gaps": "",
                "status": "awaiting_signoff",
                "filed": "2026-01-01T00:00:00+00:00",
                "review": {
                    "decision": "rejected",
                    "notes": "fix",
                    "at": "2026-01-01T00:00:00+00:00",
                },
            }
        )
        save_progress(self.root, self.run_id, [], status="awaiting_signoff")
        step = resume(self.root, self.run_id)
        self.assertNotIn("Reviewer comments", step["message"])
        self.assertIn("rejected the memo: fix", step["message"])


class HistoryTest(ReviewCase):
    def test_refilings_retain_prior_memos(self) -> None:
        v1 = copy.deepcopy(support.MEMO[1])
        self.case.file_memo(v1, "awaiting_signoff")
        self.case.review_memo(False, "more counterevidence")
        v2 = copy.deepcopy(support.MEMO[1])
        v2["conclusion"] = "Revised conclusion."
        self.case.file_memo(v2, "awaiting_signoff")
        history = self.case.data["memo_history"]
        self.assertEqual(1, len(history))
        self.assertEqual(v1["conclusion"], history[0]["conclusion"])
        self.assertEqual("rejected", history[0]["review"]["decision"])
        self.assertIn("superseded", history[0])
        self.assertEqual("Revised conclusion.", self.case.data["memo"]["conclusion"])
        self.assertIsNone(self.case.data["memo"]["review"])

    def test_first_filing_starts_an_empty_history(self) -> None:
        self.case.file_memo(copy.deepcopy(support.MEMO[1]), "unverified")
        self.assertEqual([], self.case.data["memo_history"])

    def test_refile_marks_addressed_comments_and_keeps_the_rest_open(self) -> None:
        self.case.file_memo(copy.deepcopy(support.MEMO[1]), "awaiting_signoff")
        self.case.review_memo(
            False,
            "revise",
            comments=[
                {"target": "timing", "text": "date it"},
                {"target": "gaps", "text": "say more"},
            ],
        )
        v2 = copy.deepcopy(support.MEMO[1])
        v2["conclusion"] = "Revised conclusion."
        self.case.file_memo(v2, "awaiting_signoff", addresses=["timing"])
        (prior,) = self.case.data["memo_history"]
        by_target = {c["target"]: c for c in prior["review"]["comments"]}
        self.assertEqual("addressed", by_target["timing"]["status"])
        self.assertEqual(1, by_target["timing"]["addressed_in"])
        self.assertEqual("open", by_target["gaps"]["status"])
        self.assertNotIn("addressed_in", by_target["gaps"])

    def test_legacy_memo_is_preserved_on_refile(self) -> None:
        self.legacy_record(
            {
                "conclusion": "old",
                "findings": [],
                "gaps": "",
                "status": "awaiting_signoff",
                "filed": "2026-01-01T00:00:00+00:00",
                "review": None,
            }
        )
        case = CaseRecord(self.root, self.run_id)
        case.file_memo(copy.deepcopy(support.MEMO[1]), "awaiting_signoff")
        self.assertEqual(["old"], [h["conclusion"] for h in case.data["memo_history"]])


class SourcePageTest(unittest.TestCase):
    def fixture(self, directory: str):
        workshop = Path(directory)
        (workshop / "agent/helper").parent.mkdir(parents=True, exist_ok=True)
        (workshop / "agent/helper").write_text("")
        return workshop

    def test_page_image_serves_and_rejects_cleanly(self) -> None:
        ui = support.console_ui()
        with tempfile.TemporaryDirectory() as directory:
            workshop = self.fixture(directory)
            pages = workshop / "agent/workspace/inbox/pages"
            pages.mkdir(parents=True)
            (pages / "DOC-X-p1.png").write_bytes(b"fake-png")
            console = ui.Console(None)
            console.set_workshop(str(workshop))
            self.assertEqual(b"fake-png", console.page_image("DOC-X", "1"))
            with self.assertRaises(FileNotFoundError):
                console.page_image("NOPE", "1")
            with self.assertRaises(FileNotFoundError):
                console.page_image("../x", "1")

    def test_review_data_reaches_run_detail(self) -> None:
        ui = support.console_ui()
        with tempfile.TemporaryDirectory() as directory:
            workshop = self.fixture(directory)
            runs = workshop / "agent/workspace/runs/run-test"
            runs.mkdir(parents=True)
            (runs / "progress.json").write_text(
                json.dumps(
                    {
                        "run_id": "run-test",
                        "verification": True,
                        "model": "scripted",
                        "started": "2026-01-01T00:00:00+00:00",
                        "status": "done",
                        "turn": 1,
                        "messages": [],
                        "turns": [],
                        "notes": {},
                    }
                )
            )
            records = workshop / "agent/workspace/case_records"
            records.mkdir(parents=True)
            memo = dict(
                support.MEMO[1],
                status="approved",
                filed="2026-01-01T00:00:00+00:00",
                review={
                    "decision": "approved",
                    "notes": "ok",
                    "qualifications": ["Subject to X"],
                    "comments": [],
                    "at": "2026-01-01T00:00:00+00:00",
                },
            )
            (records / "run-test.json").write_text(
                json.dumps(
                    {"run_id": "run-test", "memo": memo, "memo_history": [memo]}
                )
            )
            console = ui.Console(None)
            console.set_workshop(str(workshop))
            detail = console.run_detail("run-test")
            self.assertEqual(
                ["Subject to X"],
                detail["case"]["memo"]["review"]["qualifications"],
            )
            self.assertEqual(1, len(detail["case"]["memo_history"]))


class ApprovalBindingTest(ClaimsCase):
    """C8: approvals pin the report and evidence snapshot reviewed."""

    def test_approval_binds_versions_and_stays_current(self) -> None:
        self.prepare_good_case()
        filed = submit_memo.run(self.ctx, copy.deepcopy(GOOD_MEMO[1]))
        self.assertEqual("passed", filed["outcome"])
        memo = self.case.review_memo(True, "ok")
        snapshot = memo["review"]["snapshot"]
        self.assertEqual("passed", snapshot["claims_outcome"])
        self.assertTrue(snapshot["content"])
        self.assertTrue(snapshot["evidence"])
        status = self.case.approval_status()
        self.assertTrue(status["bound"])
        self.assertTrue(status["current"])
        self.assertEqual({}, status["stale"])
        self.assertEqual(status, self.case.data["approval_status"])

    def test_new_relevant_evidence_marks_approval_stale(self) -> None:
        self.prepare_good_case()
        submit_memo.run(self.ctx, copy.deepcopy(GOOD_MEMO[1]))
        self.case.review_memo(True, "ok")
        self.case.record_fact(
            "timing", "DOC-REPORT", 1, "Weekly status report", "late arrival"
        )
        status = self.case.approval_status()
        self.assertTrue(status["bound"])
        self.assertFalse(status["current"])
        self.assertIn("finding:not_ready", status["stale"])

    def test_new_filing_preserves_historical_approval(self) -> None:
        self.prepare_good_case()
        submit_memo.run(self.ctx, copy.deepcopy(GOOD_MEMO[1]))
        self.case.review_memo(True, "ok", qualifications=["Subject to X"])
        revised = copy.deepcopy(GOOD_MEMO[1])
        revised["conclusion"] = "Yes, with the late record noted."
        filed = submit_memo.run(self.ctx, revised)
        self.assertEqual("awaiting_signoff", filed["status"])
        (prior,) = self.case.data["memo_history"]
        self.assertEqual("approved", prior["review"]["decision"])
        self.assertIn("snapshot", prior["review"])
        self.assertEqual(["Subject to X"], prior["review"]["qualifications"])
        self.assertIsNone(self.case.data["memo"]["review"])
        self.assertEqual({"bound": False}, self.case.approval_status())

    def test_submit_addresses_marks_comments_through_the_tool(self) -> None:
        self.prepare_good_case()
        submit_memo.run(self.ctx, copy.deepcopy(GOOD_MEMO[1]))
        self.case.review_memo(
            False, "date it", comments=[{"target": "timing", "text": "date it"}]
        )
        revised = copy.deepcopy(GOOD_MEMO[1])
        revised["addresses"] = ["timing"]
        filed = submit_memo.run(self.ctx, revised)
        self.assertEqual("awaiting_signoff", filed["status"])
        (prior,) = self.case.data["memo_history"]
        (comment,) = prior["review"]["comments"]
        self.assertEqual("addressed", comment["status"])
        self.assertEqual(1, comment["addressed_in"])
        self.assertNotIn("addresses", self.case.data["memo"])

    def test_rejection_binds_no_approval(self) -> None:
        self.prepare_good_case()
        submit_memo.run(self.ctx, copy.deepcopy(GOOD_MEMO[1]))
        memo = self.case.review_memo(False, "fix it")
        self.assertNotIn("snapshot", memo["review"])
        self.assertEqual({"bound": False}, self.case.approval_status())


class LegacyApprovalTest(ReviewCase):
    def test_legacy_approval_without_snapshot_is_unknown(self) -> None:
        self.legacy_record(
            {
                "conclusion": "c",
                "findings": [],
                "gaps": "",
                "status": "approved",
                "filed": "2026-01-01T00:00:00+00:00",
                "review": {
                    "decision": "approved",
                    "notes": "ok",
                    "at": "2026-01-01T00:00:00+00:00",
                },
            }
        )
        case = CaseRecord(self.root, self.run_id)
        status = case.approval_status()
        self.assertTrue(status["bound"])
        self.assertIsNone(status["current"])


class UIDataTest(unittest.TestCase):
    def test_memo_diff_reports_changed_sections(self) -> None:
        ui = support.console_ui()
        v1 = {
            "conclusion": "Yes.",
            "gaps": "",
            "findings": [
                {
                    "item": "a",
                    "status": "asserted",
                    "summary": "One.",
                    "citations": [{"document_id": "D", "page": 1, "quote": "q"}],
                }
            ],
        }
        v2 = {
            "conclusion": "No.",
            "gaps": "",
            "findings": [
                {
                    "item": "a",
                    "status": "asserted",
                    "summary": "One, qualified.",
                    "citations": [{"document_id": "D", "page": 1, "quote": "q"}],
                },
                {"item": "b", "status": "unresolved", "summary": "New.", "citations": []},
            ],
        }
        diff = ui.memo_diff(v2, v1)
        by_section = {d["section"]: d for d in diff}
        self.assertEqual("changed", by_section["conclusion"]["change"])
        self.assertEqual("Yes.", by_section["conclusion"]["before"])
        self.assertEqual("changed", by_section["finding:a"]["change"])
        self.assertEqual("added", by_section["finding:b"]["change"])
        self.assertNotIn("gaps", by_section)
        self.assertEqual([], ui.memo_diff(v2, None))
        self.assertEqual([], ui.memo_diff(None, v1))

    def test_source_changes_flags_replaced_and_missing_files(self) -> None:
        import hashlib

        ui = support.console_ui()
        with tempfile.TemporaryDirectory() as directory:
            workshop = Path(directory)
            (workshop / "agent/helper").parent.mkdir(parents=True, exist_ok=True)
            (workshop / "agent/helper").write_text("")
            inbox = workshop / "agent/workspace/inbox"
            inbox.mkdir(parents=True)
            (inbox / "DOC-X.pdf").write_bytes(b"v1-bytes")
            case = {
                "facts": [
                    {
                        "id": "F1",
                        "document_id": "DOC-X",
                        "sha256": hashlib.sha256(b"v1-bytes").hexdigest(),
                    },
                    {"id": "F2", "document_id": "DOC-Y", "sha256": "abc"},
                    {"id": "F3", "document_id": "DOC-X"},
                ]
            }
            console = ui.Console(None)
            console.set_workshop(str(workshop))
            self.assertEqual(
                {"changed": [], "unavailable": ["F2"]},
                console.source_changes(case),
            )
            (inbox / "DOC-X.pdf").write_bytes(b"v2-bytes")
            later = ui.Console(None)
            later.set_workshop(str(workshop))
            self.assertEqual(
                {"changed": ["F1"], "unavailable": ["F2"]},
                later.source_changes(case),
            )

    def test_run_detail_carries_review_panels(self) -> None:
        ui = support.console_ui()
        with tempfile.TemporaryDirectory() as directory:
            workshop = Path(directory)
            (workshop / "agent/helper").parent.mkdir(parents=True, exist_ok=True)
            (workshop / "agent/helper").write_text("")
            runs = workshop / "agent/workspace/runs/run-test"
            runs.mkdir(parents=True)
            (runs / "progress.json").write_text(
                json.dumps(
                    {
                        "run_id": "run-test",
                        "verification": True,
                        "status": "awaiting_signoff",
                        "messages": [],
                        "turns": [],
                        "turn": 1,
                        "notes": {
                            "config_changes": [
                                {
                                    "at": "2026-01-02T00:00:00+00:00",
                                    "changes": {
                                        "model": {"was": "a", "now": "b"},
                                    },
                                }
                            ]
                        },
                    }
                )
            )
            records = workshop / "agent/workspace/case_records"
            records.mkdir(parents=True)
            memo = {"conclusion": "No.", "gaps": "", "findings": [], "status": "awaiting_signoff", "review": None}
            prior = dict(memo, conclusion="Yes.")
            (records / "run-test.json").write_text(
                json.dumps(
                    {
                        "run_id": "run-test",
                        "checklist": {},
                        "checklist_checks": [],
                        "memo_checks": [],
                        "read_ids": [],
                        "facts": [],
                        "rejected_facts": [],
                        "flags": [],
                        "signature_checks": [],
                        "requests": [],
                        "memo": memo,
                        "memo_history": [prior],
                        "audit": [],
                        "claims_staleness": {"stale": {}, "unassessed": [], "unknown": False},
                        "approval_status": {"bound": False},
                    }
                )
            )
            console = ui.Console(None)
            console.set_workshop(str(workshop))
            detail = console.run_detail("run-test")
            self.assertEqual(1, len(detail["config_changes"]))
            self.assertEqual("model", list(detail["config_changes"][0]["changes"])[0])
            self.assertEqual("conclusion", detail["memo_diff"][0]["section"])
            self.assertEqual([], detail["source_changed"])
            self.assertEqual({"bound": False}, detail["case"]["approval_status"])


if __name__ == "__main__":
    unittest.main()
