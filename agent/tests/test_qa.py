"""Phase 3: sell-side Q&A tracker.

Reviewer-logged buyer questions and answers over
the synthetic demo matter: lifecycle, unknown
due dates, explicit as-of overdue, inbox-pinned
support with staleness, review, diff, and
versioned export. Fully deterministic; no model
calls.
"""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from agent_kit import artifacts
from agent_kit.case_record import CaseError, CaseRecord

CLI_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "artifact.py"
)


def load_cli():
    spec = importlib.util.spec_from_file_location("artifact_cli", CLI_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class QaCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.run_id = artifacts.build_demo(self.root)
        self.case = CaseRecord(self.root, self.run_id)
        self.tracker = self.case.artifact_get("A15")

    def questions(self) -> dict:
        return {q["qa_id"]: q for q in self.tracker["questions"]}

    def save(self) -> None:
        self.case.artifact_save(self.tracker, "artifact_qa", "")


class QaOpenTest(QaCase):
    def test_demo_seeds(self) -> None:
        questions = self.questions()
        self.assertEqual({"QA1", "QA2", "QA3"}, set(questions))
        self.assertEqual("answered", questions["QA1"]["status"])
        self.assertEqual(["DEMO-STATUS"], questions["QA1"]["support"])
        self.assertEqual("2027-02-15", questions["QA1"]["due_date"])
        self.assertEqual("asked", questions["QA2"]["status"])
        self.assertEqual("", questions["QA2"]["due_date"])
        self.assertEqual("", questions["QA2"]["answer"])
        self.assertEqual("clarify", questions["QA3"]["status"])
        self.assertEqual(["DEMO-MINUTES"], questions["QA3"]["support"])

    def test_fifteen_templates_share_one_matter(self) -> None:
        for instance_id in (
            "A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8",
            "A9", "A10", "A11", "A12", "A13", "A14", "A15",
        ):
            self.assertEqual(
                self.run_id, self.case.artifact_get(instance_id)["matter"]
            )

    def test_open_needs_no_prerequisites(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        empty = CaseRecord(Path(tmp.name), "run-empty")
        fresh = empty.artifact_open("fresh tracker", "sellside-qa")
        self.assertEqual([], fresh["questions"])
        second = self.case.artifact_open("second tracker", "sellside-qa")
        self.assertEqual("A17", second["id"])


class QaAddTest(QaCase):
    def test_add_validates(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.add_qa(self.tracker, "  ")
        with self.assertRaises(CaseError):
            artifacts.add_qa(self.tracker, "Question?", due_date="someday")
        entry = artifacts.add_qa(
            self.tracker, "What escrow applies (fixture)?",
            asker="Buyer counsel", topic="deal", due_date="2027-03-01",
        )
        self.assertEqual("QA4", entry["qa_id"])
        self.assertEqual("asked", entry["status"])


class QaSetTest(QaCase):
    def test_set_revalidates(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.set_qa(self.tracker, "QA2", question=" ")
        with self.assertRaises(CaseError):
            artifacts.set_qa(self.tracker, "QA2", due_date="someday")
        with self.assertRaises(CaseError):
            artifacts.set_qa(self.tracker, "QA99", topic="x")
        entry = artifacts.set_qa(
            self.tracker, "QA2", due_date="2027-02-25", topic="litigation"
        )
        self.assertEqual("2027-02-25", entry["due_date"])

    def test_finished_questions_lock_until_reopened(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.set_qa(self.tracker, "QA1", topic="x")
        artifacts.decide_qa(self.tracker, "QA1", "asked")
        entry = artifacts.set_qa(self.tracker, "QA1", topic="financials v2")
        self.assertEqual("financials v2", entry["topic"])


class QaAnswerTest(QaCase):
    def test_answer_validates(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.answer_qa(self.tracker, self.root, "QA2", "  ")
        with self.assertRaises(CaseError):
            artifacts.answer_qa(
                self.tracker, self.root, "QA2", "Answer.", ["NOPE"]
            )
        with self.assertRaises(CaseError):
            artifacts.answer_qa(
                self.tracker, self.root, "QA99", "Answer."
            )

    def test_answer_records_and_pins(self) -> None:
        entry = artifacts.answer_qa(
            self.tracker, self.root, "QA2", "None pending (fixture).",
            ["DEMO-MINUTES"],
        )
        self.assertEqual(["DEMO-MINUTES"], entry["support"])
        self.assertIn("DEMO-MINUTES", entry["doc_pins"])
        self.assertEqual("asked", entry["status"])

    def test_answer_refused_on_closed(self) -> None:
        artifacts.decide_qa(self.tracker, "QA2", "closed", "withdrawn")
        with self.assertRaises(CaseError):
            artifacts.answer_qa(self.tracker, self.root, "QA2", "Too late.")

    def test_reanswer_supersedes(self) -> None:
        artifacts.answer_qa(
            self.tracker, self.root, "QA1", "Revised (fixture).", []
        )
        entry = self.questions()["QA1"]
        self.assertEqual("Revised (fixture).", entry["answer"])
        self.assertEqual([], entry["support"])
        self.assertEqual("answered", entry["status"])


class QaLifecycleTest(QaCase):
    def test_transitions_need_reasons_and_text(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.decide_qa(self.tracker, "QA2", "answered")
        with self.assertRaises(CaseError):
            artifacts.decide_qa(self.tracker, "QA2", "answered", "no text yet")
        artifacts.answer_qa(
            self.tracker, self.root, "QA2", "None pending (fixture)."
        )
        entry = artifacts.decide_qa(self.tracker, "QA2", "answered", "clear")
        self.assertEqual("answered", entry["status"])
        entry = artifacts.decide_qa(self.tracker, "QA2", "clarify", "figures?")
        self.assertEqual("clarify", entry["status"])
        entry = artifacts.decide_qa(self.tracker, "QA2", "answered", "added")
        self.assertEqual("answered", entry["status"])
        entry = artifacts.decide_qa(self.tracker, "QA2", "asked")
        self.assertEqual("asked", entry["status"])
        self.assertEqual("", entry["resolution"])

    def test_bad_transitions_refused(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.decide_qa(self.tracker, "QA1", "closed", "skip")
        with self.assertRaises(CaseError):
            artifacts.decide_qa(self.tracker, "QA2", "clarify", "skip")

    def test_close_and_reopen(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.decide_qa(self.tracker, "QA2", "closed")
        entry = artifacts.decide_qa(self.tracker, "QA2", "closed", "withdrawn")
        self.assertEqual("closed", entry["status"])
        entry = artifacts.decide_qa(self.tracker, "QA2", "asked")
        self.assertEqual("asked", entry["status"])


class QaStaleTest(QaCase):
    def test_changed_support_stales(self) -> None:
        inbox = self.root / "workspace/inbox/DEMO-STATUS.pdf"
        inbox.write_bytes(inbox.read_bytes() + b"\nAmendment.")
        status = artifacts.qa_status(
            self.tracker, self.root, self.questions()["QA1"]
        )
        self.assertEqual("stale", status["state"])
        self.assertIn("DEMO-STATUS changed", status["reasons"][0])
        summary = artifacts.qa_summary(self.tracker, self.root)
        self.assertEqual("QA1", summary["stale"][0]["qa_id"])

    def test_missing_support_orphans(self) -> None:
        (self.root / "workspace/inbox/DEMO-STATUS.pdf").unlink()
        status = artifacts.qa_status(
            self.tracker, self.root, self.questions()["QA1"]
        )
        self.assertEqual("orphaned", status["state"])
        self.assertIn("is gone", status["reasons"][0])

    def test_reanswer_repins(self) -> None:
        inbox = self.root / "workspace/inbox/DEMO-STATUS.pdf"
        inbox.write_bytes(inbox.read_bytes() + b"\nAmendment.")
        artifacts.answer_qa(
            self.tracker, self.root, "QA1", "Revised (fixture).",
            ["DEMO-STATUS"],
        )
        status = artifacts.qa_status(
            self.tracker, self.root, self.questions()["QA1"]
        )
        self.assertEqual("current", status["state"])

    def test_unknowns_limit_but_never_stale(self) -> None:
        status = artifacts.qa_status(
            self.tracker, self.root, self.questions()["QA2"]
        )
        self.assertEqual("current", status["state"])
        summary = artifacts.qa_summary(self.tracker, self.root)
        self.assertEqual([], summary["stale"])
        self.assertEqual(["QA2"], [u["qa_id"] for u in summary["unknown_dated"]])
        self.assertIsNone(summary["overdue"])
        self.assertEqual(
            {"answered": 1, "asked": 1, "clarify": 1}, summary["counts"]
        )
        self.assertEqual(
            ["QA2", "QA3"], [q["qa_id"] for q in summary["queue"]]
        )


class QaAsOfTest(QaCase):
    def test_overdue_needs_explicit_as_of(self) -> None:
        summary = artifacts.qa_summary(self.tracker, self.root, "2027-02-21")
        self.assertEqual(1, summary["overdue"])
        self.assertTrue(summary["upcoming"][0]["overdue"])
        early = artifacts.qa_summary(self.tracker, self.root, "2027-01-01")
        self.assertEqual(0, early["overdue"])
        with self.assertRaises(CaseError):
            artifacts.qa_summary(self.tracker, self.root, "someday")


class QaReviewTest(QaCase):
    def test_approval_binds_and_mutation_refuses(self) -> None:
        snapshot = artifacts.current_snapshot(self.tracker, self.case.data)
        artifacts.review_instance(
            self.tracker, self.case.data, "approved", "fixture review",
            [], snapshot,
        )
        self.assertEqual("approved", self.tracker["review"]["decision"])
        artifacts.set_qa(self.tracker, "QA2", topic="litigation v2")
        self.assertEqual("pending", self.tracker["review"]["decision"])
        with self.assertRaises(CaseError):
            artifacts.review_instance(
                self.tracker, self.case.data, "approved", "", [], snapshot
            )

    def test_diff_reports_question_changes(self) -> None:
        old = len(self.tracker["versions"])
        artifacts.answer_qa(
            self.tracker, self.root, "QA2", "None pending (fixture).",
            ["DEMO-MINUTES"],
        )
        artifacts.decide_qa(self.tracker, "QA2", "answered", "clear")
        diff = artifacts.qa_diff(self.tracker, old, old + 2)
        moved = next(
            c for c in diff["changes"]
            if c.get("qa_id") == "QA2" and c["change"] == "status"
        )
        self.assertEqual("asked", moved["before"])
        self.assertEqual("answered", moved["after"])
        text = next(
            c for c in diff["changes"]
            if c.get("qa_id") == "QA2" and c["change"] == "answer"
        )
        self.assertIn("None pending", text["after"])
        with self.assertRaises(CaseError):
            artifacts.qa_diff(self.tracker, old, old + 9)

    def test_export_limits_and_history(self) -> None:
        report = artifacts.qa_export(self.tracker, self.case.data, self.root)
        self.assertFalse(report["historical"])
        self.assertEqual(2, len(report["summary"]["queue"]))
        by_id = {q["qa_id"]: q for q in report["questions"]}
        self.assertIn("next: answer or close", by_id["QA2"]["limitations"][0])
        self.assertIn("due date unknown", by_id["QA2"]["limitations"])
        self.assertNotIn("due date unknown", by_id["QA1"]["limitations"])
        artifacts.answer_qa(
            self.tracker, self.root, "QA2", "None pending (fixture)."
        )
        report = artifacts.qa_export(self.tracker, self.case.data, self.root)
        by_id = {q["qa_id"]: q for q in report["questions"]}
        self.assertIn("no supporting documents cited", by_id["QA2"]["limitations"])
        self.assertTrue(
            any("explicit as-of" in line for line in report["limits"])
        )
        old = artifacts.qa_export(
            self.tracker, self.case.data, self.root, version=1
        )
        self.assertTrue(old["historical"])
        self.assertEqual([], old["questions"])
        self.assertEqual({}, old["summary"])
        with self.assertRaises(CaseError):
            artifacts.qa_export(
                self.tracker, self.case.data, self.root, version=999
            )


class QaCliTest(QaCase):
    def test_show_carries_summary_and_inbox(self) -> None:
        cli = load_cli()
        cli.ROOT = self.root
        shown = cli.cmd_show(Namespace(run_id=self.run_id, instance="A15"))
        self.assertEqual(3, len(shown["instance"]["questions"]))
        self.assertEqual(2, len(shown["summary"]["queue"]))
        self.assertIn("DEMO-STATUS", shown["inbox"])
        self.assertIn("snapshot", shown)

    def test_qa_ops_roundtrip(self) -> None:
        cli = load_cli()
        cli.ROOT = self.root
        added = cli.cmd_qa(
            Namespace(
                run_id=self.run_id, instance="A15", op="add",
                question="What escrow applies (fixture)?",
                asker="Buyer counsel", topic="deal", assignee="",
                due_date="2027-03-01", question_id="", answer="",
                documents="", status="", reason="",
            )
        )
        self.assertEqual("QA4", added["saved"])
        answered = cli.cmd_qa(
            Namespace(
                run_id=self.run_id, instance="A15", op="answer",
                question_id="QA4", answer="10% escrow (fixture).",
                documents="DEMO-STATUS", question=None, asker=None,
                topic=None, assignee=None, due_date=None,
                status="", reason="",
            )
        )
        self.assertEqual("QA4", answered["saved"])
        decided = cli.cmd_qa(
            Namespace(
                run_id=self.run_id, instance="A15", op="decide",
                question_id="QA4", status="answered", reason="clear",
                question=None, asker=None, topic=None, assignee=None,
                due_date=None, answer="", documents="",
            )
        )
        self.assertEqual("QA4", decided["saved"])
        case = CaseRecord(self.root, self.run_id)
        entry = next(
            q for q in case.artifact_get("A15")["questions"]
            if q["qa_id"] == "QA4"
        )
        self.assertEqual("answered", entry["status"])
        self.assertEqual(["DEMO-STATUS"], entry["support"])
        with self.assertRaises(CaseError):
            cli.cmd_qa(
                Namespace(
                    run_id=self.run_id, instance="A1", op="add",
                    question="x", asker="", topic="", assignee="",
                    due_date="", question_id="", answer="",
                    documents="", status="", reason="",
                )
            )

    def test_cli_export_covers_qa(self) -> None:
        cli = load_cli()
        cli.ROOT = self.root
        report = cli.cmd_export(
            Namespace(run_id=self.run_id, instance="A15", version=None)
        )
        self.assertEqual(3, len(report["questions"]))


if __name__ == "__main__":
    unittest.main()
