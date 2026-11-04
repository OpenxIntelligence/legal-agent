"""Phase 3: outside counsel spend tracker.

Reviewer-logged vendors, engagements, and
invoices over the synthetic demo matter: exact
decimal totals per currency, pin staleness with
explicit rebind, a decision lifecycle with
reasons, review, diff, and versioned export.
Fully deterministic; no model calls.
"""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from agent_kit import artifacts
from agent_kit.case_record import CaseError, CaseRecord
from tests import support

CLI_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "artifact.py"
)


def load_cli():
    spec = importlib.util.spec_from_file_location("artifact_cli", CLI_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SpendCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.run_id = artifacts.build_demo(self.root)
        self.case = CaseRecord(self.root, self.run_id)
        self.tracker = self.case.artifact_get("A16")

    def vendors(self) -> dict:
        return {v["vn_id"]: v for v in self.tracker["vendors"]}

    def engagements(self) -> dict:
        return {e["eg_id"]: e for e in self.tracker["engagements"]}

    def invoices(self) -> dict:
        return {i["iv_id"]: i for i in self.tracker["invoices"]}

    def save(self) -> None:
        self.case.artifact_save(self.tracker, "artifact_spend", "")


class SpendOpenTest(SpendCase):
    def test_demo_seeds(self) -> None:
        vendors = self.vendors()
        engagements = self.engagements()
        invoices = self.invoices()
        self.assertEqual({"VN1"}, set(vendors))
        self.assertEqual({"EG1", "EG2"}, set(engagements))
        self.assertEqual({"IV1", "IV2", "IV3"}, set(invoices))
        self.assertEqual("approved", invoices["IV1"]["status"])
        self.assertEqual("received", invoices["IV2"]["status"])
        self.assertEqual("", engagements["EG2"]["budget"])

    def test_sixteen_templates_share_one_matter(self) -> None:
        for instance_id in (
            "A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8",
            "A9", "A10", "A11", "A12", "A13", "A14", "A15", "A16",
        ):
            self.assertEqual(
                self.run_id, self.case.artifact_get(instance_id)["matter"]
            )

    def test_open_needs_no_prerequisites(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        empty = CaseRecord(Path(tmp.name), "run-empty")
        fresh = empty.artifact_open("fresh tracker", "counsel-spend")
        self.assertEqual([], fresh["invoices"])
        second = self.case.artifact_open("second tracker", "counsel-spend")
        self.assertEqual("A17", second["id"])


class SpendVendorTest(SpendCase):
    def test_add_validates(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.add_vn(self.tracker, "  ")
        vendor = artifacts.add_vn(self.tracker, "Second Firm", "Counsel C")
        self.assertEqual("VN2", vendor["vn_id"])

    def test_vendor_revision_stales_engagement(self) -> None:
        eg1 = self.engagements()["EG1"]
        self.assertEqual(
            "current", artifacts.eg_status(self.tracker, eg1)["state"]
        )
        artifacts.set_vn(self.tracker, "VN1", name="Renamed Firm LLP")
        status = artifacts.eg_status(self.tracker, eg1)
        self.assertEqual("stale", status["state"])
        self.assertTrue(status["reasons"])


class SpendEngagementTest(SpendCase):
    def test_add_validates(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.add_eg(self.tracker, "VN99", "Title")
        with self.assertRaises(CaseError):
            artifacts.add_eg(self.tracker, "VN1", "  ")
        with self.assertRaises(CaseError):
            artifacts.add_eg(self.tracker, "VN1", "Title", currency="YEN")
        with self.assertRaises(CaseError):
            artifacts.add_eg(
                self.tracker, "VN1", "Title", budget="ten thousand"
            )
        engagement = artifacts.add_eg(
            self.tracker, "VN1", "Harborview tax (fixture)", "10000"
        )
        self.assertEqual("EG3", engagement["eg_id"])
        self.assertEqual("10000.00", engagement["budget"])

    def test_currency_locks_once_invoices_exist(self) -> None:
        artifacts.set_eg(self.tracker, "EG2", budget="5000.00")
        with self.assertRaises(CaseError):
            artifacts.set_eg(self.tracker, "EG1", currency="EUR")
        artifacts.add_eg(self.tracker, "VN1", "Fresh (fixture)", "100.00")
        artifacts.set_eg(self.tracker, "EG3", currency="EUR")
        self.assertEqual("EUR", self.engagements()["EG3"]["currency"])
        self.assertEqual("USD", self.engagements()["EG1"]["currency"])

    def test_engagement_revision_stales_invoice(self) -> None:
        iv1 = self.invoices()["IV1"]
        self.assertEqual(
            "current", artifacts.iv_status(self.tracker, iv1)["state"]
        )
        artifacts.set_eg(self.tracker, "EG1", title="Harborview deal (fixture)")
        status = artifacts.iv_status(self.tracker, iv1)
        self.assertEqual("stale", status["state"])

    def test_rebind_restores_current_after_confirmation(self) -> None:
        artifacts.set_eg(self.tracker, "EG1", title="Harborview deal (fixture)")
        artifacts.set_iv(self.tracker, "IV2", rebind=True)
        status = artifacts.iv_status(self.tracker, self.invoices()["IV2"])
        self.assertEqual("current", status["state"])
        artifacts.set_vn(self.tracker, "VN1", name="Renamed Firm LLP")
        artifacts.set_eg(self.tracker, "EG1", rebind=True)
        status = artifacts.eg_status(self.tracker, self.engagements()["EG1"])
        self.assertEqual("current", status["state"])


class SpendInvoiceTest(SpendCase):
    def test_add_validates(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.add_iv(self.tracker, "EG99", "Label", "10.00")
        with self.assertRaises(CaseError):
            artifacts.add_iv(self.tracker, "EG1", "  ", "10.00")
        with self.assertRaises(CaseError):
            artifacts.add_iv(self.tracker, "EG1", "Label", "")
        with self.assertRaises(CaseError):
            artifacts.add_iv(self.tracker, "EG1", "Label", "1,000.00")
        invoice = artifacts.add_iv(self.tracker, "EG1", "March (fixture)", "9.5")
        self.assertEqual("IV4", invoice["iv_id"])
        self.assertEqual("9.50", invoice["amount"])
        self.assertEqual("received", invoice["status"])

    def test_exact_decimal_totals(self) -> None:
        artifacts.add_iv(self.tracker, "EG1", "Pence (fixture)", "0.10")
        artifacts.add_iv(self.tracker, "EG1", "More pence (fixture)", "0.20")
        spend = artifacts.sp_spend(self.tracker, "EG1")
        self.assertEqual("20000.30", spend["invoiced"])
        self.assertEqual("12000.00", spend["approved"])

    def test_currencies_never_converted(self) -> None:
        artifacts.add_eg(
            self.tracker, "VN1", "Harborview London (fixture)",
            "4000.00", "GBP",
        )
        artifacts.add_iv(self.tracker, "EG3", "London invoice (fixture)", "500")
        summary = artifacts.sp_summary(self.tracker)
        totals = summary["totals_by_currency"]
        self.assertEqual("23000.00", totals.get("USD"))
        self.assertEqual("500.00", totals.get("GBP"))
        self.assertNotIn("EUR", totals)
        london = next(
            s for s in summary["spend"] if s["eg_id"] == "EG3"
        )
        self.assertEqual("GBP", london["currency"])
        self.assertEqual("3500.00", london["remaining"])

    def test_paid_locks_until_reopened(self) -> None:
        artifacts.decide_iv(self.tracker, "IV1", "paid", "Wire sent (fixture).")
        with self.assertRaises(CaseError):
            artifacts.set_iv(self.tracker, "IV1", notes="x")
        artifacts.decide_iv(self.tracker, "IV1", "received")
        invoice = artifacts.set_iv(self.tracker, "IV1", notes="Clerical (fixture)")
        self.assertEqual("Clerical (fixture)", invoice["notes"])


class SpendDecideTest(SpendCase):
    def test_lifecycle_needs_reasons(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.decide_iv(self.tracker, "IV2", "paid", "")
        with self.assertRaises(CaseError):
            artifacts.decide_iv(self.tracker, "IV2", "approved", "  ")
        invoice = artifacts.decide_iv(
            self.tracker, "IV2", "approved", "In scope (fixture)."
        )
        self.assertEqual("approved", invoice["status"])
        self.assertEqual("In scope (fixture).", invoice["resolution"])

    def test_illegal_transitions_refused(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.decide_iv(self.tracker, "IV2", "filed", "reason")
        with self.assertRaises(CaseError):
            artifacts.decide_iv(self.tracker, "IV99", "approved", "reason")


class SpendSummaryTest(SpendCase):
    def test_summary_counts_queue_and_stale(self) -> None:
        summary = artifacts.sp_summary(self.tracker)
        self.assertEqual(
            {"approved": 1, "received": 2}, summary["counts"]
        )
        self.assertEqual(1, summary["vendors"])
        self.assertEqual(["EG2"], summary["unbudgeted"])
        self.assertEqual([], summary["over"])
        self.assertEqual({"IV2", "IV3"}, {
            row["iv_id"] for row in summary["queue"]
        })
        self.assertEqual([], summary["stale"])

    def test_over_budget_is_exact(self) -> None:
        artifacts.add_iv(self.tracker, "EG1", "Over (fixture)", "30000.01")
        summary = artifacts.sp_summary(self.tracker)
        self.assertEqual(["EG1"], summary["over"])
        spend = next(
            s for s in summary["spend"] if s["eg_id"] == "EG1"
        )
        self.assertEqual("50000.01", spend["invoiced"])
        self.assertEqual("-0.01", spend["remaining"])


class SpendReviewTest(SpendCase):
    def test_approval_binds_and_mutation_refuses(self) -> None:
        snapshot = artifacts.current_snapshot(self.tracker, self.case.data)
        artifacts.review_instance(
            self.tracker, self.case.data, "approved", "fixture review",
            [], snapshot,
        )
        self.assertEqual("approved", self.tracker["review"]["decision"])
        self.assertEqual(
            snapshot,
            artifacts.current_snapshot(self.tracker, self.case.data),
        )
        artifacts.set_vn(self.tracker, "VN1", name="Renamed Firm LLP")
        self.assertEqual("pending", self.tracker["review"]["decision"])
        with self.assertRaises(CaseError):
            artifacts.review_instance(
                self.tracker, self.case.data, "approved", "", [], snapshot
            )

    def test_review_versions_carry_spend_content(self) -> None:
        snapshot = artifacts.current_snapshot(self.tracker, self.case.data)
        old = len(self.tracker["versions"])
        artifacts.review_instance(
            self.tracker, self.case.data, "approved", "probe", [], snapshot
        )
        compared = artifacts.sp_diff(self.tracker, old, old + 1)
        self.assertEqual([], compared["changes"])
        entry = self.tracker["versions"][-1]
        self.assertIn("vendors", entry)
        self.assertIn("engagements", entry)
        self.assertIn("invoices", entry)


class SpendDiffExportTest(SpendCase):
    def test_diff_shows_vendor_engagement_invoice_changes(self) -> None:
        before = len(self.tracker["versions"])
        artifacts.set_vn(self.tracker, "VN1", name="Renamed Firm LLP")
        artifacts.set_eg(
            self.tracker, "EG1", budget="60000.00", rebind=True
        )
        artifacts.decide_iv(
            self.tracker, "IV2", "approved", "In scope (fixture)."
        )
        diff = artifacts.sp_diff(self.tracker, before, before + 3)
        changes = {
            (c.get("vn_id") or c.get("eg_id") or c.get("iv_id"), c["change"])
            for c in diff["changes"]
        }
        self.assertIn(("VN1", "name"), changes)
        self.assertIn(("EG1", "budget"), changes)
        self.assertIn(("IV2", "status"), changes)
        with self.assertRaises(CaseError):
            artifacts.sp_diff(self.tracker, before, before + 9)

    def test_export_keeps_money_and_limits(self) -> None:
        report = artifacts.sp_export(self.tracker, self.case.data)
        self.assertEqual("counsel-spend", report["template"])
        self.assertEqual("A16", report["instance"])
        spend = next(
            s for s in report["summary"]["spend"] if s["eg_id"] == "EG1"
        )
        self.assertEqual("50000.00", spend["budget"])
        self.assertEqual("20000.00", spend["invoiced"])
        self.assertTrue(
            any("never converted" in line for line in report["limits"])
        )
        iv2 = next(
            i for i in report["invoices"] if i["iv_id"] == "IV2"
        )
        self.assertTrue(
            any("received" in line for line in iv2["limitations"])
        )

    def test_historical_export_stays_frozen(self) -> None:
        report = artifacts.sp_export(self.tracker, self.case.data, 1)
        self.assertTrue(report["historical"])
        self.assertEqual([], report["invoices"])
        self.assertEqual({}, report["summary"])
        self.assertTrue(
            any("historical" in line for line in report["limits"])
        )
        with self.assertRaises(CaseError):
            artifacts.sp_export(self.tracker, self.case.data, 99)


class SpendCliTest(SpendCase):
    def setUp(self) -> None:
        super().setUp()
        self.cli = load_cli()
        self.cli.ROOT = self.root

    def spend_args(self, op: str, **fields) -> Namespace:
        base = {
            "run_id": self.run_id, "instance": "A16", "op": op,
            "vendor": None, "engagement": None, "invoice": "",
            "name": None, "contact": None, "title": None,
            "budget": None, "currency": None, "label": None,
            "amount": None, "notes": None, "rebind": False,
            "status": "", "reason": "",
        }
        base.update(fields)
        return Namespace(**base)

    def show_args(self, instance: str = "A16") -> Namespace:
        return Namespace(run_id=self.run_id, instance=instance)

    def test_spend_lifecycle(self) -> None:
        result = self.cli.cmd_spend(
            self.spend_args("add-vendor", name="Second Firm", contact="C")
        )
        self.assertEqual("VN2", result["saved"])
        result = self.cli.cmd_spend(
            self.spend_args(
                "add-eng", vendor="VN2", title="Tax (fixture)",
                budget="7000", currency="EUR",
            )
        )
        self.assertEqual("EG3", result["saved"])
        result = self.cli.cmd_spend(
            self.spend_args(
                "add", engagement="EG3", label="Q1 (fixture)", amount="700"
            )
        )
        self.assertEqual("IV4", result["saved"])
        result = self.cli.cmd_spend(
            self.spend_args(
                "decide", invoice="IV4", status="disputed",
                reason="Duplicate (fixture).",
            )
        )
        self.assertEqual("IV4", result["saved"])
        result = self.cli.cmd_spend(
            self.spend_args(
                "set", invoice="IV4", notes="Challenged (fixture)"
            )
        )
        self.assertEqual("IV4", result["saved"])
        result = self.cli.cmd_spend(
            self.spend_args("set-eng", engagement="EG3", title="Tax v2")
        )
        self.assertEqual("EG3", result["saved"])
        result = self.cli.cmd_spend(
            self.spend_args("set-vendor", vendor="VN2", contact="New")
        )
        self.assertEqual("VN2", result["saved"])

    def test_spend_rejects_wrong_template(self) -> None:
        with self.assertRaises(CaseError):
            self.cli.cmd_spend(
                self.spend_args(
                    "add", instance="A15",
                    engagement="EG1", label="x", amount="1.00",
                )
            )

    def test_show_diff_export(self) -> None:
        view = self.cli.cmd_show(self.show_args())
        self.assertEqual(2, len(view["instance"]["engagements"]))
        self.assertEqual(
            "current", view["instance"]["invoices"][0]["live"]["state"]
        )
        before = len(view["instance"]["versions"])
        self.cli.cmd_spend(
            self.spend_args(
                "decide", invoice="IV2", status="approved",
                reason="In scope (fixture).",
            )
        )
        diff = self.cli.cmd_diff(
            Namespace(run_id=self.run_id, instance="A16",
                      old=before, new=before + 1)
        )
        self.assertEqual([{"iv_id": "IV2", "change": "status",
                           "before": "received", "after": "approved"},
                          {"iv_id": "IV2", "change": "resolution",
                           "before": "", "after": "In scope (fixture)."}],
                         diff["changes"])
        report = self.cli.cmd_export(
            Namespace(run_id=self.run_id, instance="A16", version=None)
        )
        self.assertEqual("A16", report["instance"])

    def test_console_spend_posts(self) -> None:
        ui = support.console_ui()
        app = ui.Console(None)
        sent: list = []
        app._run_dir = lambda run_id: self.root
        app._artifact = lambda argv: sent.append(argv) or {"saved": "IV4"}
        saved = app.artifact_spend(
            self.run_id, "A16",
            {"op": "add", "engagement": "EG2", "label": "Extra (fixture)",
             "amount": "100.00"},
        )
        self.assertEqual("IV4", saved["saved"])
        self.assertEqual(
            ["spend", self.run_id, "A16", "--op", "add",
             "--engagement", "EG2", "--label", "Extra (fixture)",
             "--amount", "100.00"],
            sent[0],
        )
        with self.assertRaises(RuntimeError):
            app.artifact_spend(self.run_id, "A16", {"op": "wipe"})


if __name__ == "__main__":
    unittest.main()
