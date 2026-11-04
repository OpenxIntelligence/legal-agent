"""Phase 3: venture term-sheet comparison.

Reviewer-logged sheets and comparison terms over
the synthetic demo matter: per-sheet positions,
completeness-gated verdicts, sheet pin staleness,
review, diff, and versioned export. A missing
position never counts as agreement. Fully
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


class TermsheetCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.run_id = artifacts.build_demo(self.root)
        self.case = CaseRecord(self.root, self.run_id)
        self.comparison = self.case.artifact_get("A13")

    def sheets(self) -> dict:
        return {s["ts_id"]: s for s in self.comparison["sheets"]}

    def terms(self) -> dict:
        return {t["tm_id"]: t for t in self.comparison["terms"]}

    def save(self) -> None:
        self.case.artifact_save(self.comparison, "artifact_termsheet", "")


class TermsheetOpenTest(TermsheetCase):
    def test_demo_seeds(self) -> None:
        sheets = self.sheets()
        self.assertEqual({"TS1", "TS2"}, set(sheets))
        self.assertEqual("Fund A (fixture)", sheets["TS1"]["party"])
        terms = self.terms()
        self.assertEqual({"TM1", "TM2", "TM3"}, set(terms))
        self.assertEqual("divergent", terms["TM1"]["status"])
        self.assertEqual(2, len(terms["TM1"]["positions"]))
        self.assertEqual("aligned", terms["TM2"]["status"])
        self.assertEqual("open", terms["TM3"]["status"])
        self.assertEqual(1, len(terms["TM3"]["positions"]))
        self.assertEqual(["TS2"], artifacts.tm_missing(self.comparison, terms["TM3"]))

    def test_thirteen_templates_share_one_matter(self) -> None:
        for instance_id in (
            "A1", "A2", "A3", "A4", "A5", "A6", "A7",
            "A8", "A9", "A10", "A11", "A12", "A13",
        ):
            self.assertEqual(
                self.run_id, self.case.artifact_get(instance_id)["matter"]
            )

    def test_open_needs_no_prerequisites(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        empty = CaseRecord(Path(tmp.name), "run-empty")
        fresh = empty.artifact_open("fresh comparison", "term-sheet")
        self.assertEqual([], fresh["sheets"])
        self.assertEqual([], fresh["terms"])
        second = self.case.artifact_open("second comparison", "term-sheet")
        self.assertEqual("A17", second["id"])


class SheetTest(TermsheetCase):
    def test_add_ts_validates(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.add_ts(self.comparison, "  ")
        with self.assertRaises(CaseError):
            artifacts.add_ts(self.comparison, "Offer C", date="someday")
        sheet = artifacts.add_ts(
            self.comparison, "Offer C (fixture)", "Fund C", "2027-02-01"
        )
        self.assertEqual("TS3", sheet["ts_id"])

    def test_set_ts_revalidates(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.set_ts(self.comparison, "TS1", label="")
        with self.assertRaises(CaseError):
            artifacts.set_ts(self.comparison, "TS1", date="someday")
        with self.assertRaises(CaseError):
            artifacts.set_ts(self.comparison, "TS99", label="x")
        sheet = artifacts.set_ts(self.comparison, "TS2", party="Fund B2")
        self.assertEqual("Fund B2", sheet["party"])


class TermTest(TermsheetCase):
    def test_add_tm_validates(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.add_tm(self.comparison, " ", "proposition")
        with self.assertRaises(CaseError):
            artifacts.add_tm(self.comparison, "topic", " ")
        term = artifacts.add_tm(
            self.comparison, "Pro-rata (fixture)", "Pro-rata rights offered."
        )
        self.assertEqual("TM4", term["tm_id"])
        self.assertEqual("open", term["status"])

    def test_set_tm_locks_decided_terms(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.set_tm(self.comparison, "TM1", topic="Changed")
        artifacts.decide_tm(self.comparison, "TM1", "open")
        term = artifacts.set_tm(self.comparison, "TM1", topic="Valuation v2")
        self.assertEqual("Valuation v2", term["topic"])
        with self.assertRaises(CaseError):
            artifacts.set_tm(self.comparison, "TM3", proposition=" ")


class PositionTest(TermsheetCase):
    def test_position_validates(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.position_tm(self.comparison, "TM3", "TS99", "text")
        with self.assertRaises(CaseError):
            artifacts.position_tm(self.comparison, "TM3", "TS2", "  ")
        with self.assertRaises(CaseError):
            artifacts.position_tm(self.comparison, "TM99", "TS2", "text")
        with self.assertRaises(CaseError):
            artifacts.position_tm(self.comparison, "TM1", "TS1", "locked")

    def test_position_records_and_supersedes(self) -> None:
        term = artifacts.position_tm(
            self.comparison, "TM3", "TS2", "15% post-money (fixture)."
        )
        self.assertEqual([], artifacts.tm_missing(self.comparison, term))
        term = artifacts.position_tm(
            self.comparison, "TM3", "TS2", "14% post-money (fixture)."
        )
        texts = [p["text"] for p in term["positions"] if p["ts_id"] == "TS2"]
        self.assertEqual(["14% post-money (fixture)."], texts)


class TermsheetLifecycleTest(TermsheetCase):
    def test_verdicts_need_reasons_and_complete_positions(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.decide_tm(self.comparison, "TM3", "aligned")
        with self.assertRaises(CaseError):
            artifacts.decide_tm(self.comparison, "TM3", "aligned", "too soon")
        artifacts.position_tm(
            self.comparison, "TM3", "TS2", "15% post-money (fixture)."
        )
        term = artifacts.decide_tm(self.comparison, "TM3", "divergent", "gap")
        self.assertEqual("divergent", term["status"])
        term = artifacts.decide_tm(self.comparison, "TM3", "resolved", "met halfway")
        self.assertEqual("resolved", term["status"])
        term = artifacts.decide_tm(self.comparison, "TM3", "open")
        self.assertEqual("open", term["status"])
        self.assertEqual("", term["resolution"])

    def test_bad_transitions_refused(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.decide_tm(self.comparison, "TM2", "divergent", "skip")
        with self.assertRaises(CaseError):
            artifacts.decide_tm(self.comparison, "TM1", "aligned", "skip")
        with self.assertRaises(CaseError):
            artifacts.decide_tm(self.comparison, "TM3", "resolved", "skip")

    def test_new_sheet_reopens_completeness(self) -> None:
        artifacts.add_ts(self.comparison, "Offer C (fixture)")
        with self.assertRaises(CaseError):
            artifacts.decide_tm(self.comparison, "TM1", "resolved", "stale set")
        self.assertEqual(
            ["TS3"],
            artifacts.tm_missing(self.comparison, self.terms()["TM1"]),
        )


class TermsheetStaleTest(TermsheetCase):
    def test_sheet_revision_stales(self) -> None:
        artifacts.set_ts(self.comparison, "TS1", party="Fund A2")
        for tm_id in ("TM1", "TM2", "TM3"):
            status = artifacts.tm_status(
                self.comparison, self.terms()[tm_id]
            )
            self.assertEqual("stale", status["state"])
            self.assertIn("TS1", status["reasons"][0])
        summary = artifacts.ts_summary(self.comparison)
        self.assertEqual(3, len(summary["stale"]))

    def test_reposition_clears_staleness(self) -> None:
        artifacts.set_ts(self.comparison, "TS1", party="Fund A2")
        artifacts.decide_tm(self.comparison, "TM1", "open")
        term = artifacts.position_tm(
            self.comparison, "TM1", "TS1", "$8M pre-money (fixture)."
        )
        self.assertEqual("current", artifacts.tm_status(self.comparison, term)["state"])
        self.assertEqual(
            "stale",
            artifacts.tm_status(self.comparison, self.terms()["TM2"])["state"],
        )

    def test_missing_sheet_orphans(self) -> None:
        status = artifacts.tm_status(
            self.comparison,
            {"tm_id": "TMX", "positions": [{"ts_id": "TS99"}]},
        )
        self.assertEqual("orphaned", status["state"])

    def test_missing_positions_limit_but_never_stale(self) -> None:
        status = artifacts.tm_status(
            self.comparison, self.terms()["TM3"]
        )
        self.assertEqual("current", status["state"])
        summary = artifacts.ts_summary(self.comparison)
        self.assertEqual([], summary["stale"])
        self.assertEqual(["TM3"], [i["tm_id"] for i in summary["incomplete"]])
        self.assertEqual(
            {"divergent": 1, "aligned": 1, "open": 1}, summary["counts"]
        )
        self.assertEqual(["TM3"], [q["tm_id"] for q in summary["queue"]])


class TermsheetReviewTest(TermsheetCase):
    def test_approval_binds_and_mutation_refuses(self) -> None:
        snapshot = artifacts.current_snapshot(self.comparison, self.case.data)
        artifacts.review_instance(
            self.comparison, self.case.data, "approved", "fixture review",
            [], snapshot,
        )
        self.assertEqual("approved", self.comparison["review"]["decision"])
        artifacts.set_tm(self.comparison, "TM3", topic="Pool v2")
        self.assertEqual("pending", self.comparison["review"]["decision"])
        with self.assertRaises(CaseError):
            artifacts.review_instance(
                self.comparison, self.case.data, "approved", "", [], snapshot
            )

    def test_diff_reports_sheet_term_and_position_changes(self) -> None:
        old = len(self.comparison["versions"])
        artifacts.set_ts(self.comparison, "TS2", party="Fund B2")
        artifacts.position_tm(
            self.comparison, "TM3", "TS2", "15% post-money (fixture)."
        )
        artifacts.decide_tm(self.comparison, "TM3", "aligned", "close enough")
        diff = artifacts.ts_diff(self.comparison, old, old + 3)
        renamed = next(
            c for c in diff["changes"]
            if c.get("ts_id") == "TS2" and c["change"] == "party"
        )
        self.assertEqual("Fund B (fixture)", renamed["before"])
        self.assertEqual("Fund B2", renamed["after"])
        moved = next(
            c for c in diff["changes"]
            if c.get("tm_id") == "TM3" and c["change"] == "status"
        )
        self.assertEqual("open", moved["before"])
        self.assertEqual("aligned", moved["after"])
        placed = next(
            c for c in diff["changes"]
            if c.get("tm_id") == "TM3" and c["change"] == "positions"
        )
        self.assertEqual(2, len(placed["after"]))
        with self.assertRaises(CaseError):
            artifacts.ts_diff(self.comparison, old, old + 9)

    def test_export_limits_and_history(self) -> None:
        report = artifacts.ts_export(self.comparison, self.case.data)
        self.assertFalse(report["historical"])
        self.assertEqual(1, len(report["summary"]["queue"]))
        self.assertEqual(2, len(report["sheets"]))
        by_id = {t["tm_id"]: t for t in report["terms"]}
        self.assertIn("missing positions from: TS2", by_id["TM3"]["limitations"])
        self.assertNotIn(
            "missing positions from: TS2", by_id["TM1"]["limitations"]
        )
        self.assertTrue(
            any("never counts as agreement" in line for line in report["limits"])
        )
        old = artifacts.ts_export(self.comparison, self.case.data, version=1)
        self.assertTrue(old["historical"])
        self.assertEqual([], old["sheets"])
        self.assertEqual([], old["terms"])
        self.assertEqual({}, old["summary"])
        with self.assertRaises(CaseError):
            artifacts.ts_export(self.comparison, self.case.data, version=999)


class TermsheetCliTest(TermsheetCase):
    def test_show_carries_summary_and_missing(self) -> None:
        cli = load_cli()
        cli.ROOT = self.root
        shown = cli.cmd_show(Namespace(run_id=self.run_id, instance="A13"))
        self.assertEqual(3, len(shown["instance"]["terms"]))
        self.assertEqual(2, len(shown["instance"]["sheets"]))
        self.assertEqual(1, len(shown["summary"]["queue"]))
        self.assertEqual(["TS2"], shown["instance"]["terms"][2]["missing"])
        self.assertIn("snapshot", shown)

    def test_termsheet_ops_roundtrip(self) -> None:
        cli = load_cli()
        cli.ROOT = self.root
        added_sheet = cli.cmd_termsheet(
            Namespace(
                run_id=self.run_id, instance="A13", op="add-sheet",
                label="Offer C (fixture)", party="Fund C",
                date="2027-02-01", notes="", sheet=None, term="",
                topic=None, proposition=None, text="", status="", reason="",
            )
        )
        self.assertEqual("TS3", added_sheet["saved"])
        added = cli.cmd_termsheet(
            Namespace(
                run_id=self.run_id, instance="A13", op="add",
                topic="Pro-rata (fixture)",
                proposition="Pro-rata rights offered.",
                sheet=None, term="", label=None, party=None, date=None,
                notes=None, text="", status="", reason="",
            )
        )
        self.assertEqual("TM4", added["saved"])
        for ts_id in ("TS1", "TS2", "TS3"):
            placed = cli.cmd_termsheet(
                Namespace(
                    run_id=self.run_id, instance="A13", op="position",
                    term="TM4", sheet=ts_id, text=f"{ts_id} rights (fixture).",
                    label=None, party=None, date=None, notes=None,
                    topic=None, proposition=None, status="", reason="",
                )
            )
            self.assertEqual("TM4", placed["saved"])
        decided = cli.cmd_termsheet(
            Namespace(
                run_id=self.run_id, instance="A13", op="decide", term="TM4",
                status="aligned", reason="same rights (fixture)",
                sheet=None, label=None, party=None, date=None, notes=None,
                topic=None, proposition=None, text="",
            )
        )
        self.assertEqual("TM4", decided["saved"])
        case = CaseRecord(self.root, self.run_id)
        term = next(
            t for t in case.artifact_get("A13")["terms"] if t["tm_id"] == "TM4"
        )
        self.assertEqual("aligned", term["status"])
        with self.assertRaises(CaseError):
            cli.cmd_termsheet(
                Namespace(
                    run_id=self.run_id, instance="A1", op="add",
                    topic="x", proposition="y", sheet=None, term="",
                    label=None, party=None, date=None, notes=None,
                    text="", status="", reason="",
                )
            )

    def test_cli_export_covers_termsheet(self) -> None:
        cli = load_cli()
        cli.ROOT = self.root
        report = cli.cmd_export(
            Namespace(run_id=self.run_id, instance="A13", version=None)
        )
        self.assertEqual(3, len(report["terms"]))
        self.assertEqual(2, len(report["sheets"]))


if __name__ == "__main__":
    unittest.main()
