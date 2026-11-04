"""Phase 3: regulatory horizon scan.

Reviewer-logged regulatory watches over the synthetic demo
matter: lifecycle, registry-validated regimes, required
provenance, unknown effective dates, matrix cell links with
pin staleness, review, diff, and versioned export. Nothing
here is live monitoring. Fully deterministic; no model calls.
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


class HorizonCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.run_id = artifacts.build_demo(self.root)
        self.case = CaseRecord(self.root, self.run_id)
        self.scan = self.case.artifact_get("A10")

    def watches(self) -> dict:
        return {w["wh_id"]: w for w in self.scan["watches"]}

    def save(self) -> None:
        self.case.artifact_save(self.scan, "artifact_horizon", "")

    def save_source(self) -> None:
        source = self.case.artifact_get(self.scan["source_instance"])
        self.case.artifact_save(source, "artifact_matrix", "")


class HorizonOpenTest(HorizonCase):
    def test_demo_seeds(self) -> None:
        self.assertEqual("A3", self.scan["source_instance"])
        watches = self.watches()
        self.assertEqual({"WH1", "WH2", "WH3"}, set(watches))
        self.assertEqual("watching", watches["WH1"]["status"])
        self.assertEqual("C1", watches["WH1"]["cell_id"])
        self.assertEqual("", watches["WH1"]["effective_date"])
        self.assertEqual("", watches["WH1"]["last_checked"])
        self.assertEqual("assessing", watches["WH2"]["status"])
        self.assertEqual("2027-06-01", watches["WH2"]["effective_date"])
        self.assertEqual("2027-01-15", watches["WH2"]["last_checked"])
        self.assertTrue(watches["WH2"]["relevance"])
        self.assertEqual("closed", watches["WH3"]["status"])
        self.assertEqual("", watches["WH3"]["cell_id"])
        self.assertIn("Out of scope", watches["WH3"]["resolution"])

    def test_ten_templates_share_one_matter(self) -> None:
        for instance_id in (
            "A1", "A2", "A3", "A4", "A5",
            "A6", "A7", "A8", "A9", "A10",
        ):
            self.assertEqual(
                self.run_id, self.case.artifact_get(instance_id)["matter"]
            )

    def test_open_needs_a_matrix(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        empty = CaseRecord(Path(tmp.name), "run-empty")
        with self.assertRaises(CaseError):
            empty.artifact_open("no source", "regulatory-horizon")
        with self.assertRaises(CaseError):
            self.case.artifact_open("wrong kind", "regulatory-horizon", "A1")

    def test_open_defaults_and_names_sources(self) -> None:
        default = self.case.artifact_open("default source", "regulatory-horizon")
        self.assertEqual("A3", default["source_instance"])
        second = self.case.artifact_open("second survey", "jurisdiction-matrix")
        with self.assertRaises(CaseError):
            self.case.artifact_open("ambiguous", "regulatory-horizon")
        named = self.case.artifact_open(
            "named source", "regulatory-horizon", second["id"]
        )
        self.assertEqual(second["id"], named["source_instance"])


class WatchAddTest(HorizonCase):
    def test_add_validates_everything(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.add_wh(
                self.scan, self.case.data, "", "summary",
                "ca", "seen somewhere", "2027-02-01",
            )
        with self.assertRaises(CaseError):
            artifacts.add_wh(
                self.scan, self.case.data, "title", "",
                "ca", "seen somewhere", "2027-02-01",
            )
        with self.assertRaises(CaseError):
            artifacts.add_wh(
                self.scan, self.case.data, "title", "summary",
                "ca", "", "2027-02-01",
            )
        with self.assertRaises(CaseError):
            artifacts.add_wh(
                self.scan, self.case.data, "title", "summary",
                "ca", "seen somewhere", "",
            )
        with self.assertRaises(CaseError):
            artifacts.add_wh(
                self.scan, self.case.data, "title", "summary",
                "ca", "seen somewhere", "Feb 1",
            )
        with self.assertRaises(CaseError):
            artifacts.add_wh(
                self.scan, self.case.data, "title", "summary",
                "ca", "seen somewhere", "2027-02-01",
                effective_date="someday",
            )

    def test_unknown_regime_fails_closed(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.add_wh(
                self.scan, self.case.data, "title", "summary",
                "atlantis", "seen somewhere", "2027-02-01",
            )

    def test_cell_link_must_be_live(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.add_wh(
                self.scan, self.case.data, "title", "summary",
                "ca", "seen somewhere", "2027-02-01", cell_id="C99",
            )
        source = self.case.artifact_get("A3")
        artifacts.retract_cell(source, "C3", "fixture retraction")
        self.save_source()
        with self.assertRaises(CaseError):
            artifacts.add_wh(
                self.scan, self.case.data, "title", "summary",
                "ew", "seen somewhere", "2027-02-01", cell_id="C3",
            )

    def test_add_unlinked_ok(self) -> None:
        watch = artifacts.add_wh(
            self.scan, self.case.data, "Fee schedule note",
            "A fictional fee note (fixture).", "us-ny",
            "Fixture circular (fictional)", "2027-02-01",
        )
        self.assertEqual("WH4", watch["wh_id"])
        self.assertEqual("watching", watch["status"])
        self.assertEqual("", watch["cell_id"])
        self.assertIsNone(watch["cell_pin"])


class WatchSetTest(HorizonCase):
    def test_set_revalidates(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.set_wh(self.scan, self.case.data, "WH1", regime="xx")
        with self.assertRaises(CaseError):
            artifacts.set_wh(
                self.scan, self.case.data, "WH1", observed_date="yesterday"
            )
        with self.assertRaises(CaseError):
            artifacts.set_wh(self.scan, self.case.data, "WH1", title="  ")
        with self.assertRaises(CaseError):
            artifacts.set_wh(self.scan, self.case.data, "WH1", cell_id="C99")
        watch = artifacts.set_wh(
            self.scan, self.case.data, "WH1",
            effective_date="2027-09-01", last_checked="2027-02-01",
            assignee="J. Doe",
        )
        self.assertEqual("2027-09-01", watch["effective_date"])
        self.assertEqual("2027-02-01", watch["last_checked"])
        self.assertEqual("J. Doe", watch["assignee"])

    def test_set_clears_link_and_dates(self) -> None:
        watch = artifacts.set_wh(
            self.scan, self.case.data, "WH1", cell_id=""
        )
        self.assertEqual("", watch["cell_id"])
        self.assertIsNone(watch["cell_pin"])
        watch = artifacts.set_wh(
            self.scan, self.case.data, "WH2", effective_date=""
        )
        self.assertEqual("", watch["effective_date"])

    def test_finished_watches_lock_until_reopened(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.set_wh(
                self.scan, self.case.data, "WH3", assignee="J. Doe"
            )
        artifacts.decide_wh(self.scan, "WH3", "watching")
        watch = artifacts.set_wh(
            self.scan, self.case.data, "WH3", assignee="J. Doe"
        )
        self.assertEqual("J. Doe", watch["assignee"])

    def test_relink_repins(self) -> None:
        before = self.watches()["WH1"]["cell_pin"]
        watch = artifacts.set_wh(
            self.scan, self.case.data, "WH1", cell_id="C2"
        )
        self.assertNotEqual(before, watch["cell_pin"])
        status = artifacts.wh_status(self.scan, self.case.data, watch)
        self.assertEqual("current", status["state"])


class HorizonLifecycleTest(HorizonCase):
    def test_transitions_need_reasons(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.decide_wh(self.scan, "WH1", "assessing")
        with self.assertRaises(CaseError):
            artifacts.decide_wh(self.scan, "WH1", "acted", "skip ahead")
        watch = artifacts.decide_wh(self.scan, "WH1", "assessing", "reading")
        self.assertEqual("assessing", watch["status"])
        with self.assertRaises(CaseError):
            artifacts.decide_wh(self.scan, "WH1", "acted", "no relevance yet")
        artifacts.set_wh(
            self.scan, self.case.data, "WH1", relevance="matters (fixture)"
        )
        watch = artifacts.decide_wh(self.scan, "WH1", "acted", "briefed")
        self.assertEqual("acted", watch["status"])
        self.assertEqual("briefed", watch["resolution"])
        watch = artifacts.decide_wh(self.scan, "WH1", "watching")
        self.assertEqual("watching", watch["status"])

    def test_close_and_reopen(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.decide_wh(self.scan, "WH2", "closed")
        watch = artifacts.decide_wh(self.scan, "WH2", "closed", "moot")
        self.assertEqual("closed", watch["status"])
        watch = artifacts.decide_wh(self.scan, "WH2", "watching")
        self.assertEqual("watching", watch["status"])


class HorizonStaleTest(HorizonCase):
    def test_cell_change_stales(self) -> None:
        source = self.case.artifact_get("A3")
        artifacts.set_cell(
            source, self.case.data, "C1", proposition="Changed (fixture)"
        )
        self.save_source()
        status = artifacts.wh_status(
            self.scan, self.case.data, self.watches()["WH1"]
        )
        self.assertEqual("stale", status["state"])
        self.assertIn("C1", status["reasons"][0])
        summary = artifacts.hz_summary(self.scan, self.case.data)
        self.assertEqual("WH1", summary["stale"][0]["wh_id"])
        self.assertEqual("stale", summary["queue"][0]["link_state"])

    def test_cell_retract_orphans(self) -> None:
        source = self.case.artifact_get("A3")
        artifacts.retract_cell(source, "C1", "superseded (fixture)")
        self.save_source()
        status = artifacts.wh_status(
            self.scan, self.case.data, self.watches()["WH1"]
        )
        self.assertEqual("orphaned", status["state"])

    def test_unknown_dates_are_limitations_not_staleness(self) -> None:
        status = artifacts.wh_status(
            self.scan, self.case.data, self.watches()["WH1"]
        )
        self.assertEqual("current", status["state"])
        summary = artifacts.hz_summary(self.scan, self.case.data)
        self.assertEqual([], summary["stale"])
        self.assertEqual(["WH1", "WH3"], summary["unknown_effective"])
        self.assertEqual(["WH1", "WH3"], summary["unchecked"])
        self.assertEqual(
            {"watching": 1, "assessing": 1, "closed": 1}, summary["counts"]
        )
        self.assertEqual(
            ["WH1", "WH2"], [q["wh_id"] for q in summary["queue"]]
        )


class HorizonReviewTest(HorizonCase):
    def test_approval_binds_and_mutation_refuses(self) -> None:
        snapshot = artifacts.current_snapshot(self.scan, self.case.data)
        artifacts.review_instance(
            self.scan, self.case.data, "approved", "fixture review",
            [], snapshot,
        )
        self.assertEqual("approved", self.scan["review"]["decision"])
        artifacts.set_wh(
            self.scan, self.case.data, "WH1", assignee="J. Doe"
        )
        self.assertEqual("pending", self.scan["review"]["decision"])
        with self.assertRaises(CaseError):
            artifacts.review_instance(
                self.scan, self.case.data, "approved", "", [], snapshot
            )

    def test_diff_reports_watch_changes(self) -> None:
        old = len(self.scan["versions"])
        artifacts.decide_wh(self.scan, "WH1", "assessing", "reading")
        diff = artifacts.hz_diff(self.scan, old, old + 1)
        moved = next(
            c for c in diff["changes"]
            if c.get("wh_id") == "WH1" and c["change"] == "status"
        )
        self.assertEqual("watching", moved["before"])
        self.assertEqual("assessing", moved["after"])
        artifacts.add_wh(
            self.scan, self.case.data, "Fee schedule note",
            "A fictional fee note (fixture).", "us-ny",
            "Fixture circular (fictional)", "2027-02-01",
        )
        diff = artifacts.hz_diff(self.scan, old + 1, old + 2)
        added = next(c for c in diff["changes"] if c.get("wh_id") == "WH4")
        self.assertEqual("added", added["change"])
        with self.assertRaises(CaseError):
            artifacts.hz_diff(self.scan, old, old + 9)

    def test_export_limits_and_history(self) -> None:
        report = artifacts.hz_export(self.scan, self.case.data)
        self.assertFalse(report["historical"])
        self.assertEqual(2, len(report["summary"]["queue"]))
        by_id = {w["wh_id"]: w for w in report["watches"]}
        self.assertIn("next: assess or close", by_id["WH1"]["limitations"][0])
        self.assertIn("effective date unknown", by_id["WH1"]["limitations"])
        self.assertIn("never re-checked", by_id["WH1"]["limitations"])
        self.assertNotIn("effective date unknown", by_id["WH2"]["limitations"])
        self.assertTrue(
            any("never live monitoring" in line for line in report["limits"])
        )
        old = artifacts.hz_export(self.scan, self.case.data, version=1)
        self.assertTrue(old["historical"])
        self.assertEqual([], old["watches"])
        self.assertEqual({}, old["summary"])
        with self.assertRaises(CaseError):
            artifacts.hz_export(self.scan, self.case.data, version=999)


class HorizonCliTest(HorizonCase):
    def test_show_carries_summary_cells_and_regimes(self) -> None:
        cli = load_cli()
        cli.ROOT = self.root
        shown = cli.cmd_show(Namespace(run_id=self.run_id, instance="A10"))
        self.assertEqual(3, len(shown["instance"]["watches"]))
        self.assertEqual(2, len(shown["summary"]["queue"]))
        self.assertEqual(3, len(shown["cells"]))
        self.assertIn("ca", shown["regimes"])
        self.assertIn("snapshot", shown)

    def test_horizon_ops_roundtrip(self) -> None:
        cli = load_cli()
        cli.ROOT = self.root
        added = cli.cmd_horizon(
            Namespace(
                run_id=self.run_id, instance="A10", op="add",
                title="Fee schedule note",
                summary="A fictional fee note (fixture).",
                regime="us-ny", source_desc="Fixture circular (fictional)",
                observed="2027-02-01", effective="", checked="",
                relevance="", assignee="", cell="",
                watch="", status="", reason="",
            )
        )
        self.assertEqual("WH4", added["watch"])
        revised = cli.cmd_horizon(
            Namespace(
                run_id=self.run_id, instance="A10", op="set", watch="WH4",
                title=None, summary=None, regime=None, source_desc=None,
                observed=None, effective="2027-05-01", checked=None,
                relevance="Could shift fees (fixture).", assignee="J. Doe",
                cell="C3", status="", reason="",
            )
        )
        self.assertEqual("WH4", revised["watch"])
        decided = cli.cmd_horizon(
            Namespace(
                run_id=self.run_id, instance="A10", op="decide", watch="WH4",
                status="assessing", reason="reading (fixture)",
                title=None, summary=None, regime=None, source_desc=None,
                observed=None, effective=None, checked=None,
                relevance=None, assignee=None, cell="",
            )
        )
        self.assertEqual("WH4", decided["watch"])
        case = CaseRecord(self.root, self.run_id)
        watch = next(
            w for w in case.artifact_get("A10")["watches"]
            if w["wh_id"] == "WH4"
        )
        self.assertEqual("assessing", watch["status"])
        self.assertEqual("C3", watch["cell_id"])
        self.assertEqual("2027-05-01", watch["effective_date"])
        with self.assertRaises(CaseError):
            cli.cmd_horizon(
                Namespace(
                    run_id=self.run_id, instance="A1", op="add",
                    title="x", summary="y", regime="ca",
                    source_desc="z", observed="2027-02-01",
                    effective="", checked="", relevance="", assignee="",
                    cell="", watch="", status="", reason="",
                )
            )

    def test_cli_export_covers_horizon(self) -> None:
        cli = load_cli()
        cli.ROOT = self.root
        report = cli.cmd_export(
            Namespace(run_id=self.run_id, instance="A10", version=None)
        )
        self.assertEqual(3, len(report["watches"]))


if __name__ == "__main__":
    unittest.main()
