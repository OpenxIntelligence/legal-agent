"""Increment 2: matter context, durable tasks, attempts, budget reserve.

The scheduler tracks obligations across slices and restarts: versioned
matter context with explicit unknowns, one task per checklist item,
single-worker attempt history, dependency gating, and a token budget
whose synthesis reserve gathering work cannot spend. Synthetic
fixtures and scripted clients only.
"""

from __future__ import annotations

import os
import unittest

from agent_kit import scheduler, settings, tools
from agent_kit.case_record import CaseError, CaseRecord
from agent_kit.progress import new_run
from agent_kit.settings import ConfigError, verification_enabled
from agent_kit.tools.analyze_document import analyze
from legal_agent.loop import resume_run, run_agent
from tests import support
from tests.test_kit import call

ITEMS = [
    {"id": "alpha", "question": "Was alpha true?", "evidence_needed": "a record of alpha"},
    {"id": "beta", "question": "Was beta true?", "evidence_needed": "a record of beta"},
]


class SchedulerCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        self.set_env("RUN_TOKEN_BUDGET", None)
        self.set_env("SYNTHESIS_RESERVE_TOKENS", None)
        self.fake = support.scripted(self.root, [])
        self.run_id = new_run(
            self.root, instructions="i", objective="o", verification=True
        )
        self.case = CaseRecord(self.root, self.run_id)

    def set_env(self, key, value):
        sentinel = object()
        old = os.environ.get(key, sentinel)

        def restore():
            if old is sentinel:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old

        self.addCleanup(restore)
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value

    def read(self, *ids: str) -> None:
        analyze(self.fake.client, self.root, list(ids), "x")

    def fresh(self) -> CaseRecord:
        return CaseRecord(self.root, self.run_id)


class MatterTest(SchedulerCase):
    def test_notes_version_fields_and_list_unknowns(self) -> None:
        self.case.note_context("purpose", "litigation support", "known", "assignment")
        self.case.note_context("forum", "probably state court", "inferred", "counsel")
        self.case.note_context("damages", "lost profits", "disputed", "pleadings")
        self.case.note_context("governing_law", "", "unknown", "")
        self.assertEqual(4, self.case.matter_version())
        self.assertEqual(["governing_law"], self.case.matter_unknowns())
        stored = self.fresh().data["matter"]["fields"]["forum"]
        self.assertEqual("probably state court", stored["value"])
        self.assertEqual("inferred", stored["status"])

    def test_note_validation(self) -> None:
        with self.assertRaises(CaseError):
            self.case.note_context("Bad Name", "x")
        with self.assertRaises(CaseError):
            self.case.note_context("purpose", "x", "guessed")
        with self.assertRaises(CaseError):
            self.case.note_context("governing_law", "Rome I", "unknown")
        with self.assertRaises(CaseError):
            self.case.note_context("purpose", "", "known")
        self.assertEqual(0, self.case.matter_version())

    def test_identical_note_is_a_noop(self) -> None:
        first = self.case.note_context("purpose", "x", "known", "a")
        second = self.case.note_context("purpose", "x", "known", "a")
        self.assertEqual(first["matter_version"], second["matter_version"])
        self.assertEqual(1, self.case.matter_version())

    def test_note_context_tool(self) -> None:
        result = tools.run(
            self.fake.client,
            self.root,
            self.run_id,
            call(
                "note_context",
                {"field": "governing_law", "value": "", "status": "unknown"},
            ),
        )
        self.assertEqual("unknown", result["noted"]["status"])
        self.assertEqual(["governing_law"], result["unknown"])
        error = tools.run(
            self.fake.client,
            self.root,
            self.run_id,
            call("note_context", {"field": "purpose", "value": ""}),
        )
        self.assertIn("needs a value", error["error"])


class TaskLedgerTest(SchedulerCase):
    def test_checklist_syncs_issue_tasks(self) -> None:
        self.case.set_checklist(ITEMS)
        tasks = self.fresh().data["tasks"]
        self.assertEqual({"T0", "T1", "T2"}, set(tasks))
        self.assertEqual("open", tasks["T1"]["status"])
        self.assertEqual(["alpha"], tasks["T1"]["issues"])
        self.assertIn("Was alpha true?", tasks["T1"]["question"])
        self.assertIn("a record of alpha", tasks["T1"]["acceptance"])
        self.assertEqual(1, self.fresh().data["checklist_version"])
        revised = ITEMS + [
            {
                "id": "gamma",
                "question": "Was gamma true?",
                "evidence_needed": "a record of gamma",
            }
        ]
        self.case.set_checklist(revised)
        self.assertEqual("open", self.fresh().task("T3")["status"])
        self.assertEqual(2, self.fresh().data["checklist_version"])
        self.case.set_checklist(ITEMS)
        task = self.fresh().task("T3")
        self.assertEqual("withdrawn", task["status"])
        self.assertTrue(task["history"])
        self.case.set_checklist(revised)
        task = self.fresh().task("T3")
        self.assertEqual("open", task["status"])
        self.assertIn("re-added", task["history"][-1]["reason"])

    def test_mark_answered_completes_and_new_evidence_stales(self) -> None:
        self.case.set_checklist(ITEMS)
        self.read("DOC-REPORT")
        text = support.INITIAL["DOC-REPORT"][0]
        self.case.record_fact("alpha", "DOC-REPORT", 1, text[:40], "c")
        self.case.mark_answered(["alpha"])
        task = self.fresh().task("T1")
        self.assertEqual("completed", task["status"])
        self.assertEqual(self.case.input_versions(), task["inputs"])
        self.case.record_fact("alpha", "DOC-REPORT", 1, text[8:48], "c2")
        task = self.fresh().task("T1")
        self.assertEqual("stale", task["status"])
        self.assertIn("F2", task["history"][-1]["reason"])
        # A passed reassessment completes it again with fresh versions.
        self.case.mark_answered(["alpha"])
        task = self.fresh().task("T1")
        self.assertEqual("completed", task["status"])
        self.assertEqual(self.case.input_versions(), task["inputs"])

    def test_request_blocks_only_its_item_and_answer_unblocks(self) -> None:
        self.case.set_checklist(ITEMS)
        request = self.case.open_request("alpha", "the alpha record", "need", ["d1"])
        tasks = self.fresh().data["tasks"]
        self.assertEqual("blocked", tasks["T1"]["status"])
        self.assertEqual(request["id"], tasks["T1"]["blocked_on"])
        self.assertEqual("open", tasks["T2"]["status"])
        self.case.answer_request(request["id"], "here it is")
        tasks = self.fresh().data["tasks"]
        self.assertEqual("open", tasks["T1"]["status"])
        self.assertEqual("", tasks["T1"]["blocked_on"])

    def test_request_on_answered_work_stales_its_task(self) -> None:
        self.case.set_checklist(ITEMS)
        self.read("DOC-REPORT")
        text = support.INITIAL["DOC-REPORT"][0]
        self.case.record_fact("alpha", "DOC-REPORT", 1, text[:40], "c")
        self.case.mark_answered(["alpha"])
        request = self.case.open_request("alpha", "corroboration", "need", ["d1"])
        task = self.fresh().task("T1")
        self.assertEqual("stale", task["status"])
        self.assertIn(request["id"], task["history"][-1]["reason"])
        self.assertEqual("answered", self.fresh().data["checklist"]["alpha"]["status"])

    def test_withdrawal_stales_completed_tasks(self) -> None:
        self.case.set_checklist(ITEMS)
        self.read("DOC-MEMO")
        text = support.INITIAL["DOC-MEMO"][0]
        self.case.record_fact("alpha", "DOC-MEMO", 1, text[:40], "c")
        self.case.mark_answered(["alpha"])
        self.case.flag_privileged("DOC-MEMO", "attorney work product")
        task = self.fresh().task("T1")
        self.assertEqual("stale", task["status"])
        self.assertIn("DOC-MEMO", task["history"][-1]["reason"])

    def test_checklist_revision_stales_completed_tasks(self) -> None:
        self.case.set_checklist(ITEMS)
        self.read("DOC-REPORT")
        text = support.INITIAL["DOC-REPORT"][0]
        self.case.record_fact("alpha", "DOC-REPORT", 1, text[:40], "c")
        self.case.mark_answered(["alpha"])
        self.case.set_checklist(
            ITEMS
            + [
                {
                    "id": "gamma",
                    "question": "Was gamma true?",
                    "evidence_needed": "a record of gamma",
                }
            ]
        )
        task = self.fresh().task("T1")
        self.assertEqual("stale", task["status"])
        self.assertIn("checklist revised", task["history"][-1]["reason"])

    def test_matter_note_stales_completed_tasks(self) -> None:
        self.case.set_checklist(ITEMS)
        self.read("DOC-REPORT")
        text = support.INITIAL["DOC-REPORT"][0]
        self.case.record_fact("alpha", "DOC-REPORT", 1, text[:40], "c")
        self.case.mark_answered(["alpha"])
        self.case.note_context("forum", "state court", "known", "assignment")
        task = self.fresh().task("T1")
        self.assertEqual("stale", task["status"])

    def test_fail_and_reopen(self) -> None:
        self.case.set_checklist(ITEMS)
        self.case.fail_task("T1", "the only source was withdrawn")
        self.assertEqual("failed", self.fresh().task("T1")["status"])
        with self.assertRaises(CaseError):
            self.case.reopen_task("T1", "")
        self.case.reopen_task("T1", "a replacement source arrived")
        self.assertEqual("open", self.fresh().task("T1")["status"])
        with self.assertRaises(CaseError):
            self.case.reopen_task("T2", "it never failed")


class DependencyTest(SchedulerCase):
    def test_dependencies_gate_selection_and_reject_cycles(self) -> None:
        self.case.set_checklist(ITEMS)
        self.case.add_dependency("T2", "T1")
        picked = scheduler.select_next(self.case)
        self.assertEqual("T1", picked["task"])
        self.case.mark_answered(["alpha"])
        picked = scheduler.select_next(self.case)
        self.assertEqual("T2", picked["task"])
        with self.assertRaises(CaseError):
            self.case.add_dependency("T1", "T2")
        with self.assertRaises(CaseError):
            self.case.add_dependency("T1", "T1")
        with self.assertRaises(CaseError):
            self.case.add_dependency("T1", "T99")

    def test_selection_prefers_stale_and_explains_dead_ends(self) -> None:
        self.case.set_checklist(ITEMS)
        picked = scheduler.select_next(self.case)
        self.assertEqual("T1", picked["task"])
        self.assertIn("Was alpha true?", picked["reason"])
        self.read("DOC-REPORT", "DOC-MINUTES")
        report = support.INITIAL["DOC-REPORT"][0]
        minutes = support.INITIAL["DOC-MINUTES"][0]
        self.case.record_fact("alpha", "DOC-REPORT", 1, report[:40], "c")
        self.case.record_fact("beta", "DOC-MINUTES", 1, minutes[:40], "c")
        self.case.mark_answered(["alpha", "beta"])
        self.case.record_fact("beta", "DOC-MINUTES", 1, minutes[8:48], "c2")
        picked = scheduler.select_next(self.case)
        self.assertEqual("T2", picked["task"])
        self.assertIn("reassess", picked["reason"])
        self.case.mark_answered(["alpha", "beta"])
        picked = scheduler.select_next(self.case)
        self.assertIsNone(picked["task"])
        self.assertIn("completed", picked["reason"])
        run_id = new_run(
            self.root, instructions="i", objective="o", verification=True
        )
        other = CaseRecord(self.root, run_id)
        other.set_checklist(ITEMS)
        request = other.open_request("alpha", "more", "need", ["d1"])
        other.open_request("beta", "more", "need", ["d1"])
        picked = scheduler.select_next(other)
        self.assertIsNone(picked["task"])
        self.assertIn(request["id"], picked["reason"])

    def test_selection_before_any_checklist(self) -> None:
        self.case.ensure_root_task()
        picked = scheduler.select_next(self.case)
        self.assertEqual("T0", picked["task"])
        self.assertIn("checklist", picked["reason"])


class AttemptTest(SchedulerCase):
    def test_begin_commit_and_single_worker_guard(self) -> None:
        self.case.set_checklist(ITEMS)
        first = self.case.begin_attempt("T1", worker="loop", allocated_turns=10)
        self.assertEqual(1, first["n"])
        self.assertEqual("running", self.fresh().task("T1")["status"])
        with self.assertRaises(CaseError):
            self.case.begin_attempt("T2", worker="loop")
        with self.assertRaises(CaseError):
            self.case.commit_attempt("T1", status="done", summary="x")
        with self.assertRaises(CaseError):
            self.case.commit_attempt("T1", status="partial", summary="")
        done = self.case.commit_attempt(
            "T1", status="partial", summary="slice ended", stop_reason="turn_slice_exhausted"
        )
        self.assertEqual("partial", done["status"])
        self.assertEqual("turn_slice_exhausted", done["stop_reason"])
        self.assertEqual("open", self.fresh().task("T1")["status"])
        with self.assertRaises(CaseError):
            self.case.commit_attempt("T1", status="partial", summary="again")
        second = self.case.begin_attempt("T2", worker="loop")
        self.assertEqual(1, second["n"])

    def test_crashed_attempt_is_marked_interrupted_on_recovery(self) -> None:
        self.case.set_checklist(ITEMS)
        self.case.begin_attempt("T1", worker="loop")
        recovered = self.case.begin_attempt("T1", worker="loop")
        self.assertEqual(2, recovered["n"])
        attempts = self.fresh().task("T1")["attempts"]
        self.assertEqual("interrupted", attempts[0]["status"])
        self.assertNotIn("ended", attempts[1])

    def test_terminal_and_blocked_tasks_refuse_attempts(self) -> None:
        self.case.set_checklist(ITEMS)
        self.read("DOC-REPORT")
        text = support.INITIAL["DOC-REPORT"][0]
        self.case.record_fact("alpha", "DOC-REPORT", 1, text[:40], "c")
        self.case.mark_answered(["alpha"])
        with self.assertRaises(CaseError):
            self.case.begin_attempt("T1", worker="loop")
        self.case.open_request("beta", "the beta record", "need", ["d1"])
        with self.assertRaises(CaseError):
            self.case.begin_attempt("T2", worker="loop")

    def test_attempts_persist_across_reload(self) -> None:
        self.case.set_checklist(ITEMS)
        self.case.begin_attempt("T0", worker="loop", allocated_turns=3)
        self.case.commit_attempt("T0", status="partial", summary="slice ended")
        reloaded = self.fresh().task("T0")["attempts"]
        self.assertEqual(1, len(reloaded))
        self.assertEqual("partial", reloaded[0]["status"])
        self.assertEqual("loop", reloaded[0]["worker"])
        self.assertIn("tokens_used", reloaded[0])


class SliceBindingTest(SchedulerCase):
    def test_slices_commit_attempts_across_resume(self) -> None:
        fake = support.scripted(self.root, [support.PLAN, support.LIST])
        result = run_agent(root=self.root, client=fake.client, turns=1)
        attempts = self.fresh_case(result["run_id"]).task("T0")["attempts"]
        self.assertEqual(1, len(attempts))
        self.assertEqual("partial", attempts[0]["status"])
        self.assertEqual("turn_slice_exhausted", attempts[0]["stop_reason"])
        self.assertEqual(1, attempts[0]["turns_used"])
        resumed = resume_run(
            root=self.root, run_id=result["run_id"], client=fake.client, turns=1
        )
        attempts = self.fresh_case(resumed["run_id"]).task("T0")["attempts"]
        self.assertEqual(2, len(attempts))
        self.assertEqual("partial", attempts[1]["status"])
        self.assertEqual("open", self.fresh_case(resumed["run_id"]).task("T0")["status"])

    def fresh_case(self, run_id: str) -> CaseRecord:
        return CaseRecord(self.root, run_id)

    @unittest.skipIf(
        verification_enabled(),
        "memo filing without verification; on-mode covered by test_verification",
    )
    def test_filing_unverified_completes_the_root_task(self) -> None:
        fake = support.scripted(self.root, [support.MEMO])
        result = run_agent(root=self.root, client=fake.client, turns=5)
        self.assertEqual("answered", result["status"])
        root = self.fresh_case(result["run_id"]).task("T0")
        self.assertEqual("completed", root["status"])
        self.assertEqual("completed", root["attempts"][0]["status"])
        self.assertTrue(root["inputs"])

    def test_outcome_mapping(self) -> None:
        self.assertEqual("completed", scheduler.attempt_outcome("answered", None))
        self.assertEqual(
            "completed", scheduler.attempt_outcome("awaiting_signoff", None)
        )
        self.assertEqual("completed", scheduler.attempt_outcome("needs_input", None))
        self.assertEqual(
            "partial", scheduler.attempt_outcome("paused", "turn_slice_exhausted")
        )
        self.assertEqual(
            "partial", scheduler.attempt_outcome("paused", "synthesis_reserve_reached")
        )
        self.assertEqual("partial", scheduler.attempt_outcome("paused", None))
        self.assertEqual(
            "failed", scheduler.attempt_outcome("paused", "stall_no_progress")
        )
        self.assertEqual(
            "failed", scheduler.attempt_outcome("paused", "provider_error")
        )


class ReserveTest(SchedulerCase):
    def test_reserve_pauses_gathering_with_its_reason(self) -> None:
        self.set_env("RUN_TOKEN_BUDGET", "20100")
        self.set_env("SYNTHESIS_RESERVE_TOKENS", "20000")
        fake = support.scripted(self.root, [support.PLAN, support.LIST])
        result = run_agent(root=self.root, client=fake.client, turns=5)
        self.assertEqual("paused", result["status"])
        notes = support.progress(self.root, result["run_id"])["notes"]
        self.assertEqual("synthesis_reserve_reached", notes["stop_reason"])
        self.assertIn("synthesis reserve", notes["budget_exceeded"])
        self.assertIn("RUN_TOKEN_BUDGET", notes["stop_action"])
        self.assertEqual(1, len(support.agent_requests(fake)))

    def test_resume_blocked_in_reserve_band_until_raised(self) -> None:
        self.set_env("RUN_TOKEN_BUDGET", "20100")
        self.set_env("SYNTHESIS_RESERVE_TOKENS", "20000")
        fake = support.scripted(self.root, [support.PLAN, support.LIST])
        result = run_agent(root=self.root, client=fake.client, turns=5)
        stuck = resume_run(
            root=self.root, run_id=result["run_id"], client=fake.client, turns=2
        )
        self.assertEqual("paused", stuck["status"])
        self.assertEqual(1, len(support.agent_requests(fake)))
        self.set_env("RUN_TOKEN_BUDGET", "1000000")
        resumed = resume_run(
            root=self.root, run_id=result["run_id"], client=fake.client, turns=1
        )
        self.assertEqual(2, len(support.agent_requests(fake)))
        self.assertEqual("paused", resumed["status"])

    def test_invalid_reserve_refuses_the_run(self) -> None:
        for value in ("bogus", "-5"):
            self.set_env("SYNTHESIS_RESERVE_TOKENS", value)
            with self.assertRaises(ConfigError):
                settings.synthesis_reserve_tokens()
        self.set_env("SYNTHESIS_RESERVE_TOKENS", "bogus")
        fake = support.scripted(self.root, [support.LIST])
        with self.assertRaises(ConfigError):
            run_agent(root=self.root, client=fake.client, turns=2)
        self.assertEqual([], fake.calls)

    def test_budget_state_math(self) -> None:
        self.set_env("RUN_TOKEN_BUDGET", None)
        state = scheduler.budget_state(self.root, self.run_id)
        self.assertFalse(state["enforced"])
        self.assertIsNone(state["remaining_for_work"])
        self.set_env("RUN_TOKEN_BUDGET", "1000")
        self.set_env("SYNTHESIS_RESERVE_TOKENS", "100")
        state = scheduler.budget_state(self.root, self.run_id)
        self.assertTrue(state["enforced"])
        self.assertEqual(
            state["allowance"] - state["observed"] - state["reserve"],
            state["remaining_for_work"],
        )


class WorkStatusTest(SchedulerCase):
    def test_status_reports_tasks_next_budget_and_unknowns(self) -> None:
        self.set_env("RUN_TOKEN_BUDGET", None)
        self.case.set_checklist(ITEMS)
        self.read("DOC-REPORT")
        text = support.INITIAL["DOC-REPORT"][0]
        self.case.record_fact("alpha", "DOC-REPORT", 1, text[:40], "c")
        self.case.open_request("alpha", "the alpha record", "need", ["d1"])
        self.case.note_context("governing_law", "", "unknown", "")
        result = tools.run(
            self.fake.client, self.root, self.run_id, call("work_status", {})
        )
        rows = {row["id"]: row for row in result["tasks"]}
        self.assertEqual("blocked", rows["T1"]["status"])
        self.assertEqual("open", rows["T2"]["status"])
        self.assertEqual("T2", result["next"]["task"])
        briefing = result["briefing"]
        self.assertEqual("T2", briefing["task"])
        self.assertIn("Was beta true?", briefing["question"])
        self.assertEqual(["governing_law"], briefing["matter_unknown"])
        self.assertEqual(["governing_law"], result["matter_unknowns"])
        self.assertFalse(result["budget"]["enforced"])
        specific = tools.run(
            self.fake.client,
            self.root,
            self.run_id,
            call("work_status", {"task": "T1"}),
        )
        self.assertEqual("T1", specific["briefing"]["task"])
        self.assertEqual(1, specific["briefing"]["evidence_total"])
        self.assertEqual("F1", specific["briefing"]["evidence"][0]["id"])
        self.assertEqual("blocked", specific["briefing"]["status"])
        error = tools.run(
            self.fake.client, self.root, self.run_id, call("work_status", {"task": "T99"})
        )
        self.assertIn("unknown task", error["error"])

    def test_status_before_any_checklist(self) -> None:
        result = tools.run(
            self.fake.client, self.root, self.run_id, call("work_status", {})
        )
        self.assertEqual("T0", result["next"]["task"])
        self.assertIn("checklist", result["next"]["reason"])
        self.assertEqual("T0", result["briefing"]["task"])

    def test_review_decisions_move_the_root_task(self) -> None:
        memo = {"conclusion": "c", "findings": [], "gaps": ""}
        self.case.file_memo(dict(memo), "awaiting_signoff")
        self.case.review_memo(True, notes="looks right")
        root = self.fresh().task("T0")
        self.assertEqual("completed", root["status"])
        self.assertTrue(root["inputs"])
        run_id = new_run(
            self.root, instructions="i", objective="o", verification=True
        )
        other = CaseRecord(self.root, run_id)
        other.file_memo(dict(memo), "awaiting_signoff")
        other.review_memo(False, notes="redo the conclusion")
        self.assertEqual("open", CaseRecord(self.root, run_id).task("T0")["status"])

    def test_prompt_mentions_context_and_status_tools(self) -> None:
        prompt = support.instructions_file()
        self.assertIn("note_context", prompt)
        self.assertIn("work_status", prompt)


if __name__ == "__main__":
    unittest.main()
