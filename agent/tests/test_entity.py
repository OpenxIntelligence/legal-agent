"""Phase 3: entity compliance calendar.

Reviewer-logged entities and filings over the
synthetic demo matter: entity registry with
validated regimes, filing lifecycle, unknown
due dates, explicit as-of overdue, entity pin
staleness with rebind, review, diff, and
versioned export. Fully deterministic; no
model calls.
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


class EntityCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.run_id = artifacts.build_demo(self.root)
        self.case = CaseRecord(self.root, self.run_id)
        self.calendar = self.case.artifact_get("A12")

    def entities(self) -> dict:
        return {e["en_id"]: e for e in self.calendar["entities"]}

    def filings(self) -> dict:
        return {f["cf_id"]: f for f in self.calendar["filings"]}

    def save(self) -> None:
        self.case.artifact_save(self.calendar, "artifact_entity", "")


class EntityOpenTest(EntityCase):
    def test_demo_seeds(self) -> None:
        entities = self.entities()
        self.assertEqual({"EN1", "EN2"}, set(entities))
        self.assertEqual("ca", entities["EN1"]["regime"])
        self.assertEqual("ew", entities["EN2"]["regime"])
        filings = self.filings()
        self.assertEqual({"CF1", "CF2", "CF3"}, set(filings))
        self.assertEqual("pending", filings["CF1"]["status"])
        self.assertEqual("2027-04-01", filings["CF1"]["due_date"])
        self.assertEqual("EN1", filings["CF1"]["en_id"])
        self.assertEqual("", filings["CF2"]["due_date"])
        self.assertEqual("filed", filings["CF3"]["status"])
        self.assertEqual("EN2", filings["CF3"]["en_id"])

    def test_twelve_templates_share_one_matter(self) -> None:
        for instance_id in (
            "A1", "A2", "A3", "A4", "A5", "A6",
            "A7", "A8", "A9", "A10", "A11", "A12",
        ):
            self.assertEqual(
                self.run_id, self.case.artifact_get(instance_id)["matter"]
            )

    def test_open_needs_no_prerequisites(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        empty = CaseRecord(Path(tmp.name), "run-empty")
        fresh = empty.artifact_open("fresh calendar", "entity-compliance")
        self.assertEqual([], fresh["entities"])
        self.assertEqual([], fresh["filings"])
        second = self.case.artifact_open(
            "second calendar", "entity-compliance"
        )
        self.assertEqual("A17", second["id"])


class EntityRegistryTest(EntityCase):
    def test_add_en_validates(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.add_en(self.calendar, "  ", "ca")
        with self.assertRaises(CaseError):
            artifacts.add_en(self.calendar, "Name", "atlantis")
        entity = artifacts.add_en(
            self.calendar, "Harborview Inc (fixture)", "us-ny", "NY-1"
        )
        self.assertEqual("EN3", entity["en_id"])
        self.assertEqual("NY-1", entity["identifier"])

    def test_set_en_revalidates(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.set_en(self.calendar, "EN1", regime="xx")
        with self.assertRaises(CaseError):
            artifacts.set_en(self.calendar, "EN1", name="")
        with self.assertRaises(CaseError):
            artifacts.set_en(self.calendar, "EN99", name="x")
        entity = artifacts.set_en(
            self.calendar, "EN2", identifier="EW-9 (fixture)"
        )
        self.assertEqual("EW-9 (fixture)", entity["identifier"])


class FilingAddTest(EntityCase):
    def test_add_cf_validates(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.add_cf(
                self.calendar, "EN99", "title", "annual-report"
            )
        with self.assertRaises(CaseError):
            artifacts.add_cf(self.calendar, "EN1", "  ", "annual-report")
        with self.assertRaises(CaseError):
            artifacts.add_cf(self.calendar, "EN1", "title", "crowning")
        with self.assertRaises(CaseError):
            artifacts.add_cf(
                self.calendar, "EN1", "title", "annual-report",
                due_date="someday",
            )
        filing = artifacts.add_cf(
            self.calendar, "EN2", "Statement (fixture)",
            "statement-of-information", period="2027",
        )
        self.assertEqual("CF4", filing["cf_id"])
        self.assertEqual("pending", filing["status"])
        self.assertEqual("", filing["due_date"])


class FilingSetTest(EntityCase):
    def test_set_cf_revalidates(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.set_cf(self.calendar, "CF1", kind="crowning")
        with self.assertRaises(CaseError):
            artifacts.set_cf(self.calendar, "CF1", due_date="someday")
        with self.assertRaises(CaseError):
            artifacts.set_cf(self.calendar, "CF1", en_id="EN99")
        with self.assertRaises(CaseError):
            artifacts.set_cf(self.calendar, "CF1", title=" ")
        filing = artifacts.set_cf(
            self.calendar, "CF2", due_date="2027-05-01",
            notes="Assessed (fixture).",
        )
        self.assertEqual("2027-05-01", filing["due_date"])

    def test_finished_filings_lock_until_reopened(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.set_cf(self.calendar, "CF3", notes="x")
        artifacts.decide_cf(self.calendar, "CF3", "pending")
        filing = artifacts.set_cf(self.calendar, "CF3", notes="reopened")
        self.assertEqual("reopened", filing["notes"])

    def test_move_repins(self) -> None:
        before = self.filings()["CF1"]["en_pin"]
        filing = artifacts.set_cf(self.calendar, "CF1", en_id="EN2")
        self.assertEqual("EN2", filing["en_id"])
        self.assertNotEqual(before, filing["en_pin"])
        status = artifacts.cf_status(self.calendar, filing)
        self.assertEqual("current", status["state"])


class FilingLifecycleTest(EntityCase):
    def test_transitions_need_reasons(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.decide_cf(self.calendar, "CF1", "filed")
        with self.assertRaises(CaseError):
            artifacts.decide_cf(self.calendar, "CF3", "waived")
        filing = artifacts.decide_cf(self.calendar, "CF1", "filed", "sent")
        self.assertEqual("filed", filing["status"])
        self.assertEqual("sent", filing["resolution"])
        filing = artifacts.decide_cf(self.calendar, "CF1", "pending")
        self.assertEqual("pending", filing["status"])
        self.assertEqual("", filing["resolution"])

    def test_waive_and_reopen(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.decide_cf(self.calendar, "CF2", "waived")
        filing = artifacts.decide_cf(
            self.calendar, "CF2", "waived", "not required (fixture)"
        )
        self.assertEqual("waived", filing["status"])
        filing = artifacts.decide_cf(self.calendar, "CF2", "pending")
        self.assertEqual("pending", filing["status"])


class EntityStaleTest(EntityCase):
    def test_entity_revision_stales(self) -> None:
        artifacts.set_en(self.calendar, "EN1", identifier="CA-0002")
        for cf_id in ("CF1", "CF2"):
            status = artifacts.cf_status(
                self.calendar, self.filings()[cf_id]
            )
            self.assertEqual("stale", status["state"])
            self.assertIn("EN1", status["reasons"][0])
        status = artifacts.cf_status(
            self.calendar, self.filings()["CF3"]
        )
        self.assertEqual("current", status["state"])
        summary = artifacts.ec_summary(self.calendar)
        self.assertEqual(2, len(summary["stale"]))
        self.assertEqual("stale", summary["queue"][0]["link_state"])

    def test_rebind_clears_staleness(self) -> None:
        artifacts.set_en(self.calendar, "EN1", identifier="CA-0002")
        filing = artifacts.set_cf(self.calendar, "CF1", rebind=True)
        status = artifacts.cf_status(self.calendar, filing)
        self.assertEqual("current", status["state"])
        status = artifacts.cf_status(
            self.calendar, self.filings()["CF2"]
        )
        self.assertEqual("stale", status["state"])

    def test_plain_set_keeps_staleness(self) -> None:
        artifacts.set_en(self.calendar, "EN1", identifier="CA-0002")
        filing = artifacts.set_cf(self.calendar, "CF1", notes="still stale")
        status = artifacts.cf_status(self.calendar, filing)
        self.assertEqual("stale", status["state"])

    def test_missing_entity_orphans(self) -> None:
        status = artifacts.cf_status(
            self.calendar, {"cf_id": "CFX", "en_id": "EN99"}
        )
        self.assertEqual("orphaned", status["state"])

    def test_unknown_dates_limit_but_never_stale(self) -> None:
        status = artifacts.cf_status(
            self.calendar, self.filings()["CF2"]
        )
        self.assertEqual("current", status["state"])
        summary = artifacts.ec_summary(self.calendar)
        self.assertEqual([], summary["stale"])
        self.assertEqual(["CF2"], [u["cf_id"] for u in summary["unknown_dated"]])
        self.assertIsNone(summary["overdue"])
        self.assertIsNone(summary["as_of"])


class EntityAsOfTest(EntityCase):
    def test_overdue_needs_explicit_as_of(self) -> None:
        summary = artifacts.ec_summary(self.calendar, "2027-05-01")
        self.assertEqual("2027-05-01", summary["as_of"])
        self.assertEqual(1, summary["overdue"])
        self.assertTrue(summary["upcoming"][0]["overdue"])
        early = artifacts.ec_summary(self.calendar, "2027-01-01")
        self.assertEqual(0, early["overdue"])
        self.assertFalse(early["upcoming"][0]["overdue"])
        with self.assertRaises(CaseError):
            artifacts.ec_summary(self.calendar, "someday")

    def test_upcoming_sorts_and_skips_unknowns(self) -> None:
        artifacts.add_cf(
            self.calendar, "EN2", "License renewal (fixture)", "license",
            due_date="2027-02-01",
        )
        summary = artifacts.ec_summary(self.calendar)
        self.assertEqual(
            ["CF4", "CF1"], [u["cf_id"] for u in summary["upcoming"]]
        )
        self.assertEqual(
            {"pending": 3, "filed": 1}, summary["counts"]
        )
        self.assertEqual(2, summary["entities"])


class EntityReviewTest(EntityCase):
    def test_approval_binds_and_mutation_refuses(self) -> None:
        snapshot = artifacts.current_snapshot(self.calendar, self.case.data)
        artifacts.review_instance(
            self.calendar, self.case.data, "approved", "fixture review",
            [], snapshot,
        )
        self.assertEqual("approved", self.calendar["review"]["decision"])
        artifacts.set_cf(self.calendar, "CF1", notes="J. Doe")
        self.assertEqual("pending", self.calendar["review"]["decision"])
        with self.assertRaises(CaseError):
            artifacts.review_instance(
                self.calendar, self.case.data, "approved", "", [], snapshot
            )

    def test_diff_reports_entity_and_filing_changes(self) -> None:
        old = len(self.calendar["versions"])
        artifacts.set_en(self.calendar, "EN1", identifier="CA-0002")
        artifacts.decide_cf(self.calendar, "CF1", "filed", "sent")
        diff = artifacts.ec_diff(self.calendar, old, old + 2)
        renamed = next(
            c for c in diff["changes"]
            if c.get("en_id") == "EN1" and c["change"] == "identifier"
        )
        self.assertEqual("CA-0001 (fixture)", renamed["before"])
        self.assertEqual("CA-0002", renamed["after"])
        moved = next(
            c for c in diff["changes"]
            if c.get("cf_id") == "CF1" and c["change"] == "status"
        )
        self.assertEqual("pending", moved["before"])
        self.assertEqual("filed", moved["after"])
        with self.assertRaises(CaseError):
            artifacts.ec_diff(self.calendar, old, old + 9)

    def test_export_limits_and_history(self) -> None:
        report = artifacts.ec_export(self.calendar, self.case.data)
        self.assertFalse(report["historical"])
        self.assertEqual(2, len(report["summary"]["queue"]))
        self.assertEqual(2, len(report["entities"]))
        by_id = {f["cf_id"]: f for f in report["filings"]}
        self.assertIn("next: file or waive", by_id["CF1"]["limitations"][0])
        self.assertIn("due date unknown", by_id["CF2"]["limitations"])
        self.assertNotIn("due date unknown", by_id["CF1"]["limitations"])
        self.assertTrue(
            any("explicit as-of" in line for line in report["limits"])
        )
        old = artifacts.ec_export(self.calendar, self.case.data, version=1)
        self.assertTrue(old["historical"])
        self.assertEqual([], old["entities"])
        self.assertEqual([], old["filings"])
        self.assertEqual({}, old["summary"])
        with self.assertRaises(CaseError):
            artifacts.ec_export(self.calendar, self.case.data, version=999)


class EntityCliTest(EntityCase):
    def test_show_carries_summary_and_regimes(self) -> None:
        cli = load_cli()
        cli.ROOT = self.root
        shown = cli.cmd_show(Namespace(run_id=self.run_id, instance="A12"))
        self.assertEqual(3, len(shown["instance"]["filings"]))
        self.assertEqual(2, len(shown["instance"]["entities"]))
        self.assertEqual(2, len(shown["summary"]["queue"]))
        self.assertIn("ca", shown["regimes"])
        self.assertIn("snapshot", shown)

    def test_entity_ops_roundtrip(self) -> None:
        cli = load_cli()
        cli.ROOT = self.root
        added_en = cli.cmd_entity(
            Namespace(
                run_id=self.run_id, instance="A12", op="add-en",
                name="Harborview Inc (fixture)", regime="us-ny",
                identifier="NY-1", entity=None, filing="",
                title=None, kind=None, due_date=None, period=None,
                notes=None, status="", reason="",
            )
        )
        self.assertEqual("EN3", added_en["saved"])
        added = cli.cmd_entity(
            Namespace(
                run_id=self.run_id, instance="A12", op="add",
                entity="EN3", title="License renewal (fixture)",
                kind="license", due_date="2027-06-01", period="2027",
                notes="", filing="", name=None, regime=None,
                identifier=None, status="", reason="",
            )
        )
        self.assertEqual("CF4", added["saved"])
        revised = cli.cmd_entity(
            Namespace(
                run_id=self.run_id, instance="A12", op="set", filing="CF4",
                entity=None, title=None, kind=None, due_date=None,
                period=None, notes="Assessed (fixture).",
                name=None, regime=None, identifier=None,
                status="", reason="",
            )
        )
        self.assertEqual("CF4", revised["saved"])
        decided = cli.cmd_entity(
            Namespace(
                run_id=self.run_id, instance="A12", op="decide", filing="CF4",
                status="filed", reason="sent (fixture)",
                entity=None, title=None, kind=None, due_date=None,
                period=None, notes=None, name=None, regime=None,
                identifier=None,
            )
        )
        self.assertEqual("CF4", decided["saved"])
        case = CaseRecord(self.root, self.run_id)
        filing = next(
            f for f in case.artifact_get("A12")["filings"]
            if f["cf_id"] == "CF4"
        )
        self.assertEqual("filed", filing["status"])
        with self.assertRaises(CaseError):
            cli.cmd_entity(
                Namespace(
                    run_id=self.run_id, instance="A1", op="add",
                    entity="EN1", title="x", kind="other",
                    due_date="", period="", notes="", filing="",
                    name=None, regime=None, identifier=None,
                    status="", reason="",
                )
            )

    def test_cli_export_covers_entity(self) -> None:
        cli = load_cli()
        cli.ROOT = self.root
        report = cli.cmd_export(
            Namespace(run_id=self.run_id, instance="A12", version=None)
        )
        self.assertEqual(3, len(report["filings"]))
        self.assertEqual(2, len(report["entities"]))


if __name__ == "__main__":
    unittest.main()
