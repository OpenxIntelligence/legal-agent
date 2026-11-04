"""Phase E continued: matter and deadlines board.

Board mechanics over the synthetic demo matter: deadlines
with owners, optionally tracking calendar obligations,
unknown dates kept apart, overdue relative to an explicit
as-of date only, staleness from linked obligation
changes, review, diff, and versioned export. Fully
deterministic; no model calls.
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


class DeadlinesCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.run_id = artifacts.build_demo(self.root)
        self.case = CaseRecord(self.root, self.run_id)
        self.board = self.case.artifact_get("A8")
        self.source = self.case.artifact_get("A6")

    def items(self) -> dict:
        return {d["dl_id"]: d for d in self.board["deadlines"]}

    def save(self) -> None:
        self.case.artifact_save(self.board, "artifact_deadlines", "")


class DeadlinesOpenTest(DeadlinesCase):
    def test_demo_seeds(self) -> None:
        self.assertEqual("A6", self.board["source_instance"])
        items = self.items()
        self.assertEqual({"DL1", "DL2", "DL3"}, set(items))
        self.assertEqual("2027-01-01", items["DL1"]["due_date"])
        self.assertEqual("R. Alvarez", items["DL1"]["owner"])
        self.assertEqual("OB1", items["DL1"]["ob_id"])
        self.assertEqual("", items["DL2"]["due_date"])
        self.assertEqual("", items["DL2"]["owner"])
        self.assertEqual("done", items["DL3"]["status"])
        self.assertTrue(items["DL3"]["resolution"])

    def test_open_needs_a_calendar(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        empty = CaseRecord(Path(tmp.name), "run-empty")
        with self.assertRaises(CaseError):
            empty.artifact_open("no source", "matter-deadlines")
        with self.assertRaises(CaseError):
            self.case.artifact_open("wrong kind", "matter-deadlines", "A4")

    def test_open_defaults_and_names_sources(self) -> None:
        default = self.case.artifact_open("default source", "matter-deadlines")
        self.assertEqual("A6", default["source_instance"])
        second = self.case.artifact_open("second calendar", "obligation-calendar")
        with self.assertRaises(CaseError):
            self.case.artifact_open("ambiguous", "matter-deadlines")
        named = self.case.artifact_open(
            "named source", "matter-deadlines", second["id"]
        )
        self.assertEqual(second["id"], named["source_instance"])

    def test_eight_templates_share_one_matter(self) -> None:
        for instance_id in ("A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8"):
            self.assertEqual(
                self.run_id, self.case.artifact_get(instance_id)["matter"]
            )


class DeadlineAddTest(DeadlinesCase):
    def test_add_contract(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.add_dl(self.board, self.case.data, "", "filing")
        with self.assertRaises(CaseError):
            artifacts.add_dl(self.board, self.case.data, "Title", "urgent")
        with self.assertRaises(CaseError):
            artifacts.add_dl(
                self.board, self.case.data, "Title", "filing", due_date="soon"
            )
        with self.assertRaises(CaseError):
            artifacts.add_dl(
                self.board, self.case.data, "Title", "filing", ob_id="OB9"
            )
        self.assertEqual({"DL1", "DL2", "DL3"}, set(self.items()))

    def test_link_is_optional(self) -> None:
        linked = artifacts.add_dl(
            self.board, self.case.data, "Linked", "meeting", ob_id="OB1"
        )
        self.assertEqual("OB1", linked["ob_id"])
        self.assertTrue(linked["ob_pin"])
        plain = artifacts.add_dl(self.board, self.case.data, "Plain", "meeting")
        self.assertEqual("", plain["ob_id"])
        self.assertIsNone(plain["ob_pin"])

    def test_summary_orders_upcoming_and_marks_overdue(self) -> None:
        summary = artifacts.dl_summary(self.board, self.case.data, "2027-02-01")
        self.assertEqual(["DL1"], [u["dl_id"] for u in summary["upcoming"]])
        self.assertEqual(["DL1"], summary["overdue"])
        self.assertEqual(["DL2"], [u["dl_id"] for u in summary["unknown_dated"]])
        self.assertEqual(1, summary["unowned"])
        self.assertIsNone(
            artifacts.dl_summary(self.board, self.case.data)["overdue"]
        )
        with self.assertRaises(CaseError):
            artifacts.dl_summary(self.board, self.case.data, "tomorrow")


class DeadlineSetTest(DeadlinesCase):
    def test_set_validates(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.set_dl(self.board, self.case.data, "DL1", title="  ")
        with self.assertRaises(CaseError):
            artifacts.set_dl(self.board, self.case.data, "DL1", kind="urgent")
        with self.assertRaises(CaseError):
            artifacts.set_dl(self.board, self.case.data, "DL1", due_date="soon")
        with self.assertRaises(CaseError):
            artifacts.set_dl(self.board, self.case.data, "DL1", ob_id="OB9")
        artifacts.set_dl(self.board, self.case.data, "DL1", ob_id="")
        self.assertEqual("", self.items()["DL1"]["ob_id"])
        self.assertIsNone(self.items()["DL1"]["ob_pin"])

    def test_resolve_needs_reasons(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.resolve_dl(self.board, "DL1", "done", "")
        with self.assertRaises(CaseError):
            artifacts.resolve_dl(self.board, "DL1", "finished", "x")
        artifacts.resolve_dl(self.board, "DL1", "done", "Opened (fixture).")
        item = self.items()["DL1"]
        self.assertEqual("done", item["status"])
        artifacts.resolve_dl(self.board, "DL1", "open")
        self.assertEqual("open", self.items()["DL1"]["status"])
        self.assertEqual("", self.items()["DL1"]["resolution"])


class DeadlineStaleTest(DeadlinesCase):
    def test_obligation_change_stales_and_set_rebounds(self) -> None:
        artifacts.set_ob(
            self.source, self.case.data, "OB1", due_date="2027-04-01"
        )
        self.case.artifact_save(self.source, "artifact_calendar", "OB1")
        stale = artifacts.dl_status(
            self.board, self.case.data, self.items()["DL1"]
        )
        self.assertTrue(stale["stale"])
        self.assertIn("the linked obligation changed", stale["reasons"])
        artifacts.set_ob(
            self.source, self.case.data, "OB1", due_date="2027-03-01"
        )
        self.case.artifact_save(self.source, "artifact_calendar", "OB1")
        artifacts.set_dl(
            self.board, self.case.data, "DL1", notes="rechecked after move"
        )
        rebound = artifacts.dl_status(
            self.board, self.case.data, self.items()["DL1"]
        )
        self.assertFalse(rebound["stale"])

    def test_obligation_resolution_stales(self) -> None:
        artifacts.resolve_ob(self.source, "OB1", "satisfied", "Done (fixture).")
        self.case.artifact_save(self.source, "artifact_calendar", "OB1")
        status = artifacts.dl_status(
            self.board, self.case.data, self.items()["DL1"]
        )
        self.assertTrue(status["stale"])

    def test_unlinked_items_stay_current(self) -> None:
        artifacts.set_ob(self.source, self.case.data, "OB2", notes="late note")
        self.case.artifact_save(self.source, "artifact_calendar", "OB2")
        plain = artifacts.add_dl(self.board, self.case.data, "Plain", "meeting")
        status = artifacts.dl_status(self.board, self.case.data, plain)
        self.assertFalse(status["stale"])
        self.assertFalse(status["orphaned"])


class DeadlinesReviewTest(DeadlinesCase):
    def test_approval_binds_and_mutation_refuses(self) -> None:
        snapshot = artifacts.current_snapshot(self.board, self.case.data)
        artifacts.review_instance(
            self.board, self.case.data, "approved", "fixture review",
            [], snapshot,
        )
        self.assertEqual("approved", self.board["review"]["decision"])
        artifacts.set_dl(self.board, self.case.data, "DL2", notes="late note")
        self.assertEqual("pending", self.board["review"]["decision"])
        with self.assertRaises(CaseError):
            artifacts.review_instance(
                self.board, self.case.data, "approved", "", [], snapshot
            )

    def test_diff_reports_value_changes(self) -> None:
        old = len(self.board["versions"])
        artifacts.set_dl(self.board, self.case.data, "DL1", owner="J. Osei")
        artifacts.resolve_dl(self.board, "DL2", "waived", "moot (fixture)")
        diff = artifacts.dl_diff(self.board, old, old + 2)
        owner = next(
            c for c in diff["changes"]
            if c.get("dl_id") == "DL1" and c["change"] == "owner"
        )
        self.assertEqual("J. Osei", owner["after"])
        waived = next(
            c for c in diff["changes"]
            if c.get("dl_id") == "DL2" and c["change"] == "status"
        )
        self.assertEqual("waived", waived["after"])
        with self.assertRaises(CaseError):
            artifacts.dl_diff(self.board, old, old + 9)

    def test_export_limits_and_history(self) -> None:
        report = artifacts.dl_export(self.board, self.case.data)
        self.assertFalse(report["historical"])
        self.assertEqual("A6", report["source_instance"])
        by_id = {d["dl_id"]: d for d in report["deadlines"]}
        self.assertIn("due date unknown", by_id["DL2"]["limitations"])
        self.assertIn("no owner assigned", by_id["DL2"]["limitations"])
        self.assertTrue(
            any("as-of date" in limit for limit in report["limits"])
        )
        old = artifacts.dl_export(self.board, self.case.data, version=1)
        self.assertTrue(old["historical"])
        self.assertEqual([], old["deadlines"])
        self.assertEqual({}, old["summary"])
        with self.assertRaises(CaseError):
            artifacts.dl_export(self.board, self.case.data, version=999)


class DeadlinesCliTest(DeadlinesCase):
    def test_show_carries_summary_and_obligations(self) -> None:
        cli = load_cli()
        cli.ROOT = self.root
        shown = cli.cmd_show(Namespace(run_id=self.run_id, instance="A8"))
        self.assertEqual(3, len(shown["instance"]["deadlines"]))
        self.assertEqual(2, shown["summary"]["by_status"]["open"])
        self.assertEqual(1, len(shown["summary"]["unknown_dated"]))
        self.assertEqual(3, len(shown["obligations"]))
        self.assertIn("snapshot", shown)

    def test_deadlines_ops_roundtrip(self) -> None:
        cli = load_cli()
        cli.ROOT = self.root
        added = cli.cmd_deadlines(
            Namespace(
                run_id=self.run_id, instance="A8", op="add",
                title="Fee hearing", kind="filing", owner="J. Osei",
                due_date="2027-05-01", ob="", notes="",
                deadline="", status="", reason="",
            )
        )
        self.assertEqual("DL4", added["dl"])
        resolved = cli.cmd_deadlines(
            Namespace(
                run_id=self.run_id, instance="A8", op="resolve", deadline="DL4",
                status="done", reason="held (fixture)",
                title=None, kind=None, owner=None, due_date=None,
                ob=None, notes=None,
            )
        )
        self.assertEqual("DL4", resolved["dl"])
        case = CaseRecord(self.root, self.run_id)
        item = next(
            d for d in case.artifact_get("A8")["deadlines"] if d["dl_id"] == "DL4"
        )
        self.assertEqual("done", item["status"])
        with self.assertRaises(CaseError):
            cli.cmd_deadlines(
                Namespace(
                    run_id=self.run_id, instance="A6", op="add",
                    title="x", kind="filing", owner="", due_date="",
                    ob="", notes="",
                    deadline="", status="", reason="",
                )
            )

    def test_cli_export_covers_board(self) -> None:
        cli = load_cli()
        cli.ROOT = self.root
        report = cli.cmd_export(
            Namespace(run_id=self.run_id, instance="A8", version=None)
        )
        self.assertEqual(3, len(report["deadlines"]))


if __name__ == "__main__":
    unittest.main()
