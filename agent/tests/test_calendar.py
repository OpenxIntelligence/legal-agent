"""Phase E continued: renewal and obligation calendar.

Calendar mechanics over the synthetic demo matter: dated
obligations bound to clauses in the source contract
review, unknown dates kept apart, overdue relative to an
explicit as-of date only, staleness from clause revision,
visible orphaning on retraction, review, diff, and
versioned export. Fully deterministic; no model calls.
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


class CalendarCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.run_id = artifacts.build_demo(self.root)
        self.case = CaseRecord(self.root, self.run_id)
        self.calendar = self.case.artifact_get("A6")
        self.source = self.case.artifact_get("A4")

    def obligations(self) -> dict:
        return {o["ob_id"]: o for o in self.calendar["obligations"]}

    def save(self) -> None:
        self.case.artifact_save(self.calendar, "artifact_calendar", "")


class CalendarOpenTest(CalendarCase):
    def test_demo_seeds(self) -> None:
        self.assertEqual("A4", self.calendar["source_instance"])
        obs = self.obligations()
        self.assertEqual({"OB1", "OB2", "OB3"}, set(obs))
        self.assertEqual("2027-03-01", obs["OB1"]["due_date"])
        self.assertEqual(60, obs["OB1"]["notice_days"])
        self.assertEqual("", obs["OB2"]["due_date"])
        self.assertEqual("open", obs["OB2"]["status"])
        self.assertEqual("satisfied", obs["OB3"]["status"])
        self.assertTrue(obs["OB3"]["resolution"])

    def test_open_needs_a_contract_review(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        empty = CaseRecord(Path(tmp.name), "run-empty")
        with self.assertRaises(CaseError):
            empty.artifact_open("no source", "obligation-calendar")
        with self.assertRaises(CaseError):
            self.case.artifact_open("wrong kind", "obligation-calendar", "A1")

    def test_open_defaults_and_names_sources(self) -> None:
        default = self.case.artifact_open("default source", "obligation-calendar")
        self.assertEqual("A4", default["source_instance"])
        second = self.case.artifact_open("second review", "contract-review")
        with self.assertRaises(CaseError):
            self.case.artifact_open("ambiguous", "obligation-calendar")
        named = self.case.artifact_open(
            "named source", "obligation-calendar", second["id"]
        )
        self.assertEqual(second["id"], named["source_instance"])

    def test_six_templates_share_one_matter(self) -> None:
        for instance_id in ("A1", "A2", "A3", "A4", "A5", "A6"):
            self.assertEqual(
                self.run_id, self.case.artifact_get(instance_id)["matter"]
            )


class ObligationAddTest(CalendarCase):
    def test_add_contract(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.add_ob(
                self.calendar, self.case.data, "K9", "notice", "Nope"
            )
        with self.assertRaises(CaseError):
            artifacts.add_ob(
                self.calendar, self.case.data, "K1", "escalation", "Nope"
            )
        with self.assertRaises(CaseError):
            artifacts.add_ob(
                self.calendar, self.case.data, "K1", "notice", "  "
            )
        with self.assertRaises(CaseError):
            artifacts.add_ob(
                self.calendar, self.case.data, "K1", "notice", "Nope",
                due_date="March 1",
            )
        with self.assertRaises(CaseError):
            artifacts.add_ob(
                self.calendar, self.case.data, "K1", "notice", "Nope",
                notice_days="sixty",
            )
        with self.assertRaises(CaseError):
            artifacts.add_ob(
                self.calendar, self.case.data, "K1", "notice", "Nope",
                notice_days=-1,
            )
        self.assertEqual({"OB1", "OB2", "OB3"}, set(self.obligations()))

    def test_unknown_dates_stay_unknown(self) -> None:
        obligation = artifacts.add_ob(
            self.calendar, self.case.data, "K1", "payment", "Fee true-up"
        )
        self.assertEqual("OB4", obligation["ob_id"])
        self.assertEqual("", obligation["due_date"])
        self.assertIsNone(obligation["notice_days"])
        summary = artifacts.cal_summary(self.calendar, self.case.data)
        self.assertEqual(
            {"OB2", "OB4"},
            {u["ob_id"] for u in summary["unknown_dated"]},
        )
        self.assertIsNone(summary["overdue"])

    def test_summary_orders_upcoming_and_marks_overdue(self) -> None:
        artifacts.add_ob(
            self.calendar, self.case.data, "K2", "payment", "Fee true-up",
            due_date="2027-01-15",
        )
        summary = artifacts.cal_summary(
            self.calendar, self.case.data, "2027-02-01"
        )
        self.assertEqual(
            ["OB4", "OB1"],
            [u["ob_id"] for u in summary["upcoming"]],
        )
        self.assertEqual(["OB4"], summary["overdue"])
        self.assertEqual("2027-02-01", summary["as_of"])
        with self.assertRaises(CaseError):
            artifacts.cal_summary(self.calendar, self.case.data, "tomorrow")


class ObligationSetTest(CalendarCase):
    def test_set_revises_and_rebounds(self) -> None:
        artifacts.set_clause(self.source, "K1", risk_note="late note")
        self.case.artifact_save(self.source, "artifact_contract", "K1")
        stale = artifacts.ob_status(
            self.calendar, self.case.data, self.obligations()["OB1"]
        )
        self.assertTrue(stale["stale"])
        artifacts.set_ob(
            self.calendar, self.case.data, "OB1", notes="rechecked after note"
        )
        fresh = artifacts.ob_status(
            self.calendar, self.case.data, self.obligations()["OB1"]
        )
        self.assertFalse(fresh["stale"])
        self.assertIn(
            "rebound", self.obligations()["OB1"]["history"][-1]["detail"]
        )

    def test_set_validates(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.set_ob(self.calendar, self.case.data, "OB1", title="  ")
        with self.assertRaises(CaseError):
            artifacts.set_ob(self.calendar, self.case.data, "OB1", kind="urgent")
        with self.assertRaises(CaseError):
            artifacts.set_ob(
                self.calendar, self.case.data, "OB1", due_date="soon"
            )
        artifacts.set_ob(self.calendar, self.case.data, "OB1", due_date="")
        self.assertEqual("", self.obligations()["OB1"]["due_date"])

    def test_resolve_needs_reasons(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.resolve_ob(self.calendar, "OB1", "satisfied", "")
        with self.assertRaises(CaseError):
            artifacts.resolve_ob(self.calendar, "OB1", "done", "x")
        artifacts.resolve_ob(self.calendar, "OB1", "waived", "superseded (fixture)")
        obligation = self.obligations()["OB1"]
        self.assertEqual("waived", obligation["status"])
        self.assertIn("superseded", obligation["resolution"])
        artifacts.resolve_ob(self.calendar, "OB1", "open")
        self.assertEqual("open", self.obligations()["OB1"]["status"])
        self.assertEqual("", self.obligations()["OB1"]["resolution"])


class ObligationStaleTest(CalendarCase):
    def test_clause_revision_stales(self) -> None:
        artifacts.set_clause(self.source, "K2", risk="low")
        self.case.artifact_save(self.source, "artifact_contract", "K2")
        status = artifacts.ob_status(
            self.calendar, self.case.data, self.obligations()["OB2"]
        )
        self.assertTrue(status["stale"])
        self.assertIn(
            "the clause was revised since the obligation was set",
            status["reasons"],
        )

    def test_clause_retraction_orphans_visibly(self) -> None:
        artifacts.retract_clause(self.source, "K1", "superseded (fixture)")
        self.case.artifact_save(self.source, "artifact_contract", "K1")
        status = artifacts.ob_status(
            self.calendar, self.case.data, self.obligations()["OB1"]
        )
        self.assertTrue(status["orphaned"])
        self.assertIn("the clause was retracted", status["reasons"])
        self.assertIn("OB1", self.obligations())
        report = artifacts.cal_export(self.calendar, self.case.data)
        by_id = {o["ob_id"]: o for o in report["obligations"]}
        self.assertIn("orphaned: clause retracted", by_id["OB1"]["limitations"])


class CalendarReviewTest(CalendarCase):
    def test_approval_binds_and_mutation_refuses(self) -> None:
        snapshot = artifacts.current_snapshot(self.calendar, self.case.data)
        artifacts.review_instance(
            self.calendar, self.case.data, "approved", "fixture review",
            [], snapshot,
        )
        self.assertEqual("approved", self.calendar["review"]["decision"])
        artifacts.set_ob(
            self.calendar, self.case.data, "OB2", notes="late note"
        )
        self.assertEqual("pending", self.calendar["review"]["decision"])
        with self.assertRaises(CaseError):
            artifacts.review_instance(
                self.calendar, self.case.data, "approved", "", [], snapshot
            )

    def test_diff_reports_value_changes(self) -> None:
        old = len(self.calendar["versions"])
        artifacts.set_ob(
            self.calendar, self.case.data, "OB1", due_date="2027-04-01"
        )
        artifacts.resolve_ob(self.calendar, "OB2", "waived", "moot (fixture)")
        diff = artifacts.cal_diff(self.calendar, old, old + 2)
        due = next(
            c for c in diff["changes"]
            if c.get("ob_id") == "OB1" and c["change"] == "due_date"
        )
        self.assertEqual("2027-03-01", due["before"])
        self.assertEqual("2027-04-01", due["after"])
        waived = next(
            c for c in diff["changes"]
            if c.get("ob_id") == "OB2" and c["change"] == "status"
        )
        self.assertEqual("waived", waived["after"])
        with self.assertRaises(CaseError):
            artifacts.cal_diff(self.calendar, old, old + 9)

    def test_export_limits_and_history(self) -> None:
        report = artifacts.cal_export(self.calendar, self.case.data)
        self.assertFalse(report["historical"])
        self.assertEqual("A4", report["source_instance"])
        by_id = {o["ob_id"]: o for o in report["obligations"]}
        self.assertIn("due date unknown", by_id["OB2"]["limitations"])
        self.assertTrue(
            any("as-of date" in limit for limit in report["limits"])
        )
        old = artifacts.cal_export(self.calendar, self.case.data, version=1)
        self.assertTrue(old["historical"])
        self.assertEqual([], old["obligations"])
        self.assertEqual({}, old["summary"])
        with self.assertRaises(CaseError):
            artifacts.cal_export(self.calendar, self.case.data, version=999)


class CalendarCliTest(CalendarCase):
    def test_show_carries_summary_and_clauses(self) -> None:
        cli = load_cli()
        cli.ROOT = self.root
        shown = cli.cmd_show(Namespace(run_id=self.run_id, instance="A6"))
        self.assertEqual(3, len(shown["instance"]["obligations"]))
        self.assertEqual(2, shown["summary"]["by_status"]["open"])
        self.assertEqual(1, len(shown["summary"]["unknown_dated"]))
        self.assertEqual(3, len(shown["clauses"]))
        self.assertIn("snapshot", shown)

    def test_calendar_ops_roundtrip(self) -> None:
        cli = load_cli()
        cli.ROOT = self.root
        added = cli.cmd_calendar(
            Namespace(
                run_id=self.run_id, instance="A6", op="add",
                clause="K2", kind="payment", title="Fee true-up",
                due_date="2027-01-15", notice_days="30", notes="",
                ob="", status="", reason="",
            )
        )
        self.assertEqual("OB4", added["ob"])
        resolved = cli.cmd_calendar(
            Namespace(
                run_id=self.run_id, instance="A6", op="resolve", ob="OB4",
                status="satisfied", reason="paid (fixture)",
                clause="", kind=None, title=None, due_date=None,
                notice_days=None, notes=None,
            )
        )
        self.assertEqual("OB4", resolved["ob"])
        case = CaseRecord(self.root, self.run_id)
        obligation = next(
            o for o in case.artifact_get("A6")["obligations"]
            if o["ob_id"] == "OB4"
        )
        self.assertEqual("satisfied", obligation["status"])
        with self.assertRaises(CaseError):
            cli.cmd_calendar(
                Namespace(
                    run_id=self.run_id, instance="A4", op="add",
                    clause="K2", kind="payment", title="Fee true-up",
                    due_date="", notice_days=None, notes="",
                    ob="", status="", reason="",
                )
            )

    def test_cli_export_covers_calendar(self) -> None:
        cli = load_cli()
        cli.ROOT = self.root
        report = cli.cmd_export(
            Namespace(run_id=self.run_id, instance="A6", version=None)
        )
        self.assertEqual(3, len(report["obligations"]))


if __name__ == "__main__":
    unittest.main()
