"""Milestone 5: missing-evidence discipline and bounded reflection.

Repeat evidence requests without new releases are refused; the loop
pauses instead of repeating the same call with the same outcome; and
resumes after new evidence nudge reconsideration. Offline only:
synthetic case, temporary workspaces, mocked clients.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agent_kit import tools
from agent_kit.case_record import MAX_READ_ATTEMPTS_PER_DOC, CaseRecord
from agent_kit.progress import new_run, resume, save_progress
from agent_kit.tools import analyze_document, request_evidence
from agent_kit.settings import verification_enabled
from legal_agent.loop import resume_run, run_agent
from tests import support
from tests.fake_model import Refused
from tests.test_claims_v2 import break_claims, synthetic_progress
from tests.test_verification import (
    AFTER_RELEASE,
    ASK,
    EARLY_MEMO,
    FACTS,
    FLAG,
    GOOD_MEMO,
    IDENTIFY,
    READ_ALL,
)
from verification import verify_evidence_request

SKIP_ON = unittest.skipUnless(verification_enabled(), "needs VERIFICATION=on")


def initial():
    return [support.CHECKLIST, READ_ALL, FLAG, *FACTS, EARLY_MEMO, ASK]


class RequestDisciplineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        self.fake = support.scripted(self.root, [])
        self.run_id = new_run(
            self.root, instructions="i", objective="o", verification=True
        )
        self.case = CaseRecord(self.root, self.run_id)
        self.case.set_checklist([support.checklist_item("timing")])
        self.case.mark_read(list(support.INITIAL))
        self.ctx = tools.Context(
            self.fake.client, self.root, self.run_id, self.case, True
        )

    def ask(self):
        return verify_evidence_request.run_verification(
            self.ctx, {"item": "timing", "need": "a dated assessment"}
        )

    def test_open_request_blocks_a_repeat(self) -> None:
        request = self.case.open_request(
            "timing", "a dated assessment", "missing_evidence", list(support.INITIAL)
        )
        result = self.ask()
        self.assertFalse(result["passed"])
        self.assertIn("still open", result["feedback"])
        self.assertIn(request["id"], result["feedback"])

    def test_answered_request_blocks_a_repeat_without_new_releases(self) -> None:
        request = self.case.open_request(
            "timing", "a dated assessment", "missing_evidence", list(support.INITIAL)
        )
        self.case.answer_request(request["id"], "nothing found")
        result = self.ask()
        self.assertFalse(result["passed"])
        self.assertIn("already asked", result["feedback"])
        self.assertIn(request["id"], result["feedback"])
        self.assertIn("no documents were released", result["feedback"])

    def test_repeat_allowed_after_a_release(self) -> None:
        request = self.case.open_request(
            "timing", "a dated assessment", "missing_evidence", list(support.INITIAL)
        )
        self.case.answer_request(request["id"], "nothing found")
        support.release(self.root)
        self.case.mark_read(["DOC-DRAFT", "DOC-CERT"])
        self.assertTrue(self.ask()["passed"])


class LegitimateRepeatTest(unittest.TestCase):
    """Finding 3: corroboration, contradiction, different follow-ups, replacements."""

    def setUp(self) -> None:
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        self.fake = support.scripted(self.root, [])
        self.run_id = new_run(
            self.root, instructions="i", objective="o", verification=True
        )
        self.case = CaseRecord(self.root, self.run_id)
        self.case.set_checklist([support.checklist_item("timing")])
        self.ctx = tools.Context(
            self.fake.client, self.root, self.run_id, self.case, True
        )
        analyze_document.run(
            self.ctx, {"document_ids": list(support.INITIAL), "request": "x"}
        )

    def ask(self, need: str):
        return verify_evidence_request.run_verification(
            self.ctx, {"item": "timing", "need": need}
        )

    def answer(self) -> None:
        self.case.record_fact(
            "timing", "DOC-REPORT", 1, "Open Severity 1 defects: 21", "defects open"
        )
        self.case.mark_answered(["timing"])
        self.assertEqual("answered", self.case.data["checklist"]["timing"]["status"])

    def test_plain_repeat_on_an_answered_item_still_refused(self) -> None:
        self.answer()
        result = self.ask("a dated assessment")
        self.assertFalse(result["passed"])
        self.assertIn("already answered", result["feedback"])

    def test_corroboration_allowed_on_an_answered_item(self) -> None:
        self.answer()
        result = request_evidence.run(
            self.ctx,
            {
                "item": "timing",
                "need": "independent corroboration of the timing evidence",
            },
        )
        self.assertTrue(result.get("escalated"))
        stored = self.case.data["requests"][-1]
        self.assertEqual("corroboration", stored["purpose"])
        self.assertEqual("open", stored["status"])

    def test_second_corroboration_without_releases_refused(self) -> None:
        self.answer()
        need = "independent corroboration of the timing evidence"
        first = request_evidence.run(self.ctx, {"item": "timing", "need": need})
        self.case.answer_request(self.case.data["requests"][-1]["id"], "none found")
        self.assertTrue(first.get("escalated"))
        result = self.ask(need)
        self.assertFalse(result["passed"])
        self.assertIn("already asked", result["feedback"])

    def test_contradiction_allowed_on_a_contested_item(self) -> None:
        self.case.record_fact(
            "timing", "DOC-REPORT", 1, "Open Severity 1 defects: 21", "defects open"
        )
        self.case.record_fact(
            "timing",
            "DOC-MINUTES",
            1,
            "confirmed the system is ready for go-live",
            "ready claimed",
            stance="opposes",
        )
        self.assertIn("timing", self.case.contested_items())
        result = self.ask("resolve the contradiction between the collected quotes")
        self.assertTrue(result["passed"])

    def test_contradiction_refused_without_a_live_contest(self) -> None:
        self.answer()
        result = self.ask("resolve the contradiction between the collected quotes")
        self.assertFalse(result["passed"])
        self.assertIn("record the opposing", result["feedback"])
        self.assertIn("suspected_discrepancy", result["feedback"])

    def test_reworded_follow_up_is_still_a_duplicate(self) -> None:
        request = self.case.open_request(
            "timing", "a dated assessment", "missing_evidence", list(support.INITIAL)
        )
        self.case.answer_request(request["id"], "nothing found")
        result = self.ask("who approved the budget overspend")
        self.assertFalse(result["passed"])
        self.assertIn("already asked", result["feedback"])
        self.assertIn("different material target", result["feedback"])

    def test_different_target_is_a_new_need(self) -> None:
        request = self.case.open_request(
            "timing", "a dated assessment", "missing_evidence", list(support.INITIAL)
        )
        self.case.answer_request(request["id"], "nothing found")
        result = verify_evidence_request.run_verification(
            self.ctx,
            {
                "item": "timing",
                "need": "a dated assessment of the December draft",
                "purpose": "follow_up",
                "target": "DOC-DRAFT",
            },
        )
        self.assertTrue(result["passed"])

    def test_reworded_repeat_still_refused(self) -> None:
        request = self.case.open_request(
            "timing", "a dated assessment", "missing_evidence", list(support.INITIAL)
        )
        self.case.answer_request(request["id"], "nothing found")
        result = self.ask("a dated assessment please")
        self.assertFalse(result["passed"])
        self.assertIn("already asked", result["feedback"])
        self.assertIn("paraphrase", result["feedback"])

    def test_replacement_allowed_after_bounded_failures(self) -> None:
        support.release(self.root)
        self.case.mark_read(["DOC-CERT"])
        for _ in range(MAX_READ_ATTEMPTS_PER_DOC):
            self.case.mark_read_failed({"DOC-DRAFT": "error: corrupt"})
        self.assertEqual(
            MAX_READ_ATTEMPTS_PER_DOC, self.case.read_attempts("DOC-DRAFT")
        )
        result = self.ask(
            "a readable replacement copy; the released file could not be read"
        )
        self.assertTrue(result["passed"])

    def test_replacement_refused_before_attempts_exhausted(self) -> None:
        support.release(self.root)
        self.case.mark_read(["DOC-CERT"])
        self.case.mark_read_failed({"DOC-DRAFT": "error: corrupt"})
        result = self.ask(
            "a readable replacement copy; the released file could not be read"
        )
        self.assertFalse(result["passed"])
        self.assertIn("Re-read them", result["feedback"])

    def test_exhausted_failures_need_a_replacement_need(self) -> None:
        support.release(self.root)
        self.case.mark_read(["DOC-CERT"])
        for _ in range(MAX_READ_ATTEMPTS_PER_DOC):
            self.case.mark_read_failed({"DOC-DRAFT": "error: corrupt"})
        result = self.ask("a dated assessment")
        self.assertFalse(result["passed"])
        self.assertIn("readable replacement", result["feedback"])

    def test_open_request_blocks_even_corroboration(self) -> None:
        self.answer()
        self.case.open_request(
            "timing",
            "independent corroboration of the timing evidence",
            "missing_evidence",
            list(support.INITIAL),
            purpose="corroboration",
        )
        result = self.ask("independent corroboration of the timing evidence")
        self.assertFalse(result["passed"])
        self.assertIn("still open", result["feedback"])

    def test_attempts_count_and_legacy_strings_read_as_one(self) -> None:
        self.case.mark_read_failed({"DOC-DRAFT": "error: corrupt"})
        self.case.mark_read_failed({"DOC-DRAFT": "error: corrupt"})
        self.assertEqual(2, self.case.read_attempts("DOC-DRAFT"))
        self.case.data["failed_reads"]["DOC-CERT"] = "error: legacy"
        self.assertEqual(1, self.case.read_attempts("DOC-CERT"))
        self.case.mark_read_failed({"DOC-CERT": "error: again"})
        self.assertEqual(
            {"note": "error: again", "attempts": 2, "class": "unknown"},
            self.case.data["failed_reads"]["DOC-CERT"],
        )

    def test_purpose_detection_and_need_similarity(self) -> None:
        purposes = verify_evidence_request.purpose_of
        self.assertEqual("corroboration", purposes("Corroborating witness needed"))
        self.assertEqual("contradiction", purposes("resolve this contradiction"))
        self.assertEqual("replacement", purposes("a readable copy, please"))
        self.assertEqual("follow_up", purposes("a dated assessment"))
        differ = verify_evidence_request.needs_differ
        self.assertTrue(differ("a dated assessment", "who approved the overspend"))
        self.assertFalse(differ("a dated assessment", "a dated assessment please"))
        self.assertFalse(differ("", "a dated assessment"))


class ReadExceptionTest(unittest.TestCase):
    """B10/B11: a documented exception scopes the read gate; failures
    recover by class, and restrictions are never bypassed."""

    def setUp(self) -> None:
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        self.fake = support.scripted(self.root, [])
        self.run_id = new_run(
            self.root, instructions="i", objective="o", verification=True
        )
        self.case = CaseRecord(self.root, self.run_id)
        self.case.set_checklist([support.checklist_item("timing")])
        self.ctx = tools.Context(
            self.fake.client, self.root, self.run_id, self.case, True
        )
        analyze_document.run(
            self.ctx, {"document_ids": list(support.INITIAL), "request": "x"}
        )
        support.release(self.root)
        self.case.mark_read(["DOC-CERT"])

    def ask(self, need: str, **extra):
        return verify_evidence_request.run_verification(
            self.ctx, {"item": "timing", "need": need, **extra}
        )

    def test_unread_release_blocks_without_an_exception(self) -> None:
        result = self.ask("a dated assessment")
        self.assertFalse(result["passed"])
        self.assertIn("DOC-DRAFT", result["feedback"])

    def test_documented_exception_permits_an_unrelated_request(self) -> None:
        result = self.ask(
            "a dated assessment",
            read_exception=(
                "DOC-DRAFT is a signature worksheet; the timing "
                "question needs only dated approvals"
            ),
        )
        self.assertTrue(result["passed"])

    def test_exception_never_overrides_a_restriction(self) -> None:
        self.case.mark_read_failed(
            {
                "DOC-DRAFT": {
                    "note": "permission denied",
                    "class": "restricted",
                }
            }
        )
        result = self.ask("a dated assessment", read_exception="unrelated to timing")
        self.assertFalse(result["passed"])
        self.assertIn("restricted", result["feedback"])

    def test_internal_failures_need_diagnosis_not_an_exception(self) -> None:
        self.case.mark_read_failed(
            {"DOC-DRAFT": {"note": "traceback ref T-1", "class": "internal"}}
        )
        result = self.ask("a dated assessment", read_exception="unrelated to timing")
        self.assertFalse(result["passed"])
        self.assertIn("Diagnose", result["feedback"])

    def test_corrupt_document_goes_straight_to_replacement(self) -> None:
        self.case.mark_read_failed(
            {"DOC-DRAFT": {"note": "unsupported format", "class": "corrupt"}}
        )
        result = self.ask(
            "a readable replacement copy; the released file could not be read"
        )
        self.assertTrue(result["passed"])


class GroundedSuspicionTest(unittest.TestCase):
    """B9: a grounded suspicion can request the missing evidence
    without first proving the contradiction it would resolve."""

    def setUp(self) -> None:
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        self.fake = support.scripted(self.root, [])
        self.run_id = new_run(
            self.root, instructions="i", objective="o", verification=True
        )
        self.case = CaseRecord(self.root, self.run_id)
        self.case.set_checklist([support.checklist_item("timing")])
        self.ctx = tools.Context(
            self.fake.client, self.root, self.run_id, self.case, True
        )
        analyze_document.run(
            self.ctx, {"document_ids": list(support.INITIAL), "request": "x"}
        )

    def test_suspected_discrepancy_allowed_with_grounded_rationale(self) -> None:
        result = request_evidence.run(
            self.ctx,
            {
                "item": "timing",
                "need": "the figures do not add up",
                "purpose": "suspected_discrepancy",
                "rationale": (
                    "the defect table in DOC-REPORT totals 21 but its "
                    "conclusion says zero blocking defects"
                ),
            },
        )
        self.assertTrue(result.get("escalated"))
        stored = self.case.data["requests"][-1]
        self.assertEqual("suspected_discrepancy", stored["purpose"])

    def test_suspected_discrepancy_allowed_on_an_answered_item(self) -> None:
        self.case.record_fact(
            "timing", "DOC-REPORT", 1, "Open Severity 1 defects: 21", "defects open"
        )
        self.case.mark_answered(["timing"])
        result = verify_evidence_request.run_verification(
            self.ctx,
            {
                "item": "timing",
                "need": "the figures do not add up",
                "purpose": "suspected_discrepancy",
                "rationale": "DOC-MINUTES dates the count after the statement",
            },
        )
        self.assertTrue(result["passed"])

    def test_suspected_discrepancy_refused_without_grounding(self) -> None:
        result = verify_evidence_request.run_verification(
            self.ctx,
            {
                "item": "timing",
                "need": "something feels off",
                "purpose": "suspected_discrepancy",
                "rationale": "a hunch that the numbers disagree",
            },
        )
        self.assertFalse(result["passed"])
        self.assertIn("grounded rationale", result["feedback"])

    def test_missing_primary_allowed_with_a_named_target(self) -> None:
        result = verify_evidence_request.run_verification(
            self.ctx,
            {
                "item": "timing",
                "need": "the referenced attachment behind the inconsistency",
                "purpose": "missing_primary",
                "target": "Attachment B to DOC-REPORT",
            },
        )
        self.assertTrue(result["passed"])

    def test_missing_primary_refused_without_a_target(self) -> None:
        result = verify_evidence_request.run_verification(
            self.ctx,
            {
                "item": "timing",
                "need": "the referenced attachment behind the inconsistency",
                "purpose": "missing_primary",
            },
        )
        self.assertFalse(result["passed"])
        self.assertIn("must name its target", result["feedback"])


class StallTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)

    def test_triple_repeat_pauses_with_a_stall_note(self) -> None:
        fake = support.scripted(
            self.root, [support.PLAN, support.PLAN, support.PLAN, support.LIST]
        )
        result = run_agent(root=self.root, client=fake.client, turns=6)
        self.assertEqual("paused", result["status"])
        self.assertIn("update_plan", result["notes"]["stall"])
        self.assertIn("3 times", result["notes"]["stall"])
        self.assertIn("identical outcomes", result["notes"]["stall"])
        calls = support.tool_calls(self.root, result["run_id"])
        self.assertEqual(3, len(calls))
        self.assertEqual(3, len(support.agent_requests(fake)))

    def test_resume_after_a_stall_nudges_a_different_action(self) -> None:
        fake = support.scripted(
            self.root, [support.PLAN, support.PLAN, support.PLAN, support.LIST]
        )
        result = run_agent(root=self.root, client=fake.client, turns=6)
        resumed = resume_run(
            root=self.root, run_id=result["run_id"], client=fake.client, turns=3
        )
        fourth = support.agent_requests(fake)[3]
        self.assertIn("different action", json.dumps(fourth["input"][-1]))
        self.assertIsNone(support.progress(self.root, result["run_id"])["notes"].get("stall"))

    @SKIP_ON
    def test_changed_outcome_resets_the_count(self) -> None:
        partial = support.PARTIAL_CHECKLIST
        fake = support.scripted(self.root, [partial, partial, partial])
        result = run_agent(root=self.root, client=fake.client, turns=3)
        self.assertEqual("paused", result["status"])
        self.assertIsNone(result["notes"].get("stall"))
        case = support.case(self.root, result["run_id"])
        self.assertEqual(5, len(case["checklist"]))

    @SKIP_ON
    def test_unavailable_retry_is_not_a_stall(self) -> None:
        fake = support.scripted(
            self.root, initial() + AFTER_RELEASE + IDENTIFY + [GOOD_MEMO, GOOD_MEMO]
        )
        result = run_agent(root=self.root, client=fake.client, turns=20)
        self.assertEqual("needs_input", result["status"])
        support.release(self.root)
        original = break_claims(fake, error=Refused("overloaded"))
        paused = resume_run(
            root=self.root, run_id=result["run_id"], client=fake.client, turns=20
        )
        self.assertEqual("paused", paused["status"])
        self.assertIsNone(paused["notes"].get("stall"))
        self.assertIsNone(support.case(self.root, result["run_id"])["memo"])
        fake.responder = original
        filed = resume_run(
            root=self.root, run_id=result["run_id"], client=fake.client, turns=10
        )
        self.assertEqual("awaiting_signoff", filed["status"])
        self.assertEqual(
            "awaiting_signoff",
            support.case(self.root, result["run_id"])["memo"]["status"],
        )


class ReconsiderationTest(unittest.TestCase):
    def test_release_resume_message_nudges_rechecking(self) -> None:
        root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        run_id = new_run(root, instructions="i", objective="o", verification=False)
        case = CaseRecord(root, run_id)
        case.set_checklist([support.checklist_item("timing")])
        case.open_request(
            "timing", "a dated assessment", "missing_evidence", list(support.INITIAL)
        )
        save_progress(root, run_id, [], status="needs_input")
        support.release(root)
        step = resume(root, run_id)
        self.assertIn("Released documents: DOC-CERT, DOC-DRAFT", step["message"])
        self.assertIn("Re-check findings", step["message"])
        self.assertIn("stance opposes", step["message"])

    def test_instructions_cover_rechecking_after_new_evidence(self) -> None:
        self.assertIn("re-check the findings", support.instructions_file())


class StallCardTest(unittest.TestCase):
    def test_stall_note_reaches_run_detail(self) -> None:
        ui = support.console_ui()
        with tempfile.TemporaryDirectory() as directory:
            workshop = Path(directory)
            (workshop / "agent/helper").parent.mkdir(parents=True, exist_ok=True)
            (workshop / "agent/helper").write_text("")
            runs = workshop / "agent/workspace/runs/run-test"
            runs.mkdir(parents=True)
            progress = synthetic_progress(
                "update_plan", {"plan": ["a"]}, {"plan": ["a"]}, "paused"
            )
            progress["notes"] = {
                "stall": "update_plan was called 3 times in a row."
            }
            (runs / "progress.json").write_text(json.dumps(progress))
            records = workshop / "agent/workspace/case_records"
            records.mkdir(parents=True)
            (records / "run-test.json").write_text("{}")
            console = ui.Console(None)
            console.set_workshop(str(workshop))
            detail = console.run_detail("run-test")
            self.assertIn("3 times", detail["stalled"])
            progress["notes"] = {}
            (runs / "progress.json").write_text(json.dumps(progress))
            detail = console.run_detail("run-test")
            self.assertIsNone(detail["stalled"])


if __name__ == "__main__":
    unittest.main()
