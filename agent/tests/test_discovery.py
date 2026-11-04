"""Phase 3: discovery request/response log.

Discovery mechanics over the synthetic demo matter:
drafted, served, answered, and decided requests in both
directions, responses with pinned produced documents,
staleness when productions change, an explicit queue,
review, diff, and versioned export. Fully deterministic;
no model calls.
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


class DiscoveryCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.run_id = artifacts.build_demo(self.root)
        self.case = CaseRecord(self.root, self.run_id)
        self.log = self.case.artifact_get("A9")

    def requests(self) -> dict:
        return {r["rq_id"]: r for r in self.log["requests"]}

    def responses(self) -> dict:
        return {r["rs_id"]: r for r in self.log["responses"]}

    def save(self) -> None:
        self.case.artifact_save(self.log, "artifact_discovery", "")


class DiscoveryOpenTest(DiscoveryCase):
    def test_demo_seeds(self) -> None:
        requests = self.requests()
        self.assertEqual({"RQ1", "RQ2", "RQ3"}, set(requests))
        self.assertEqual("responded", requests["RQ1"]["status"])
        self.assertEqual("outgoing", requests["RQ1"]["direction"])
        self.assertEqual("draft", requests["RQ2"]["status"])
        self.assertEqual("objected", requests["RQ3"]["status"])
        responses = self.responses()
        self.assertEqual({"RS1"}, set(responses))
        self.assertEqual("RQ1", responses["RS1"]["rq_id"])
        self.assertEqual(["DEMO-STATUS"], responses["RS1"]["documents"])

    def test_demo_queue_names_next_actions(self) -> None:
        summary = artifacts.dr_summary(self.log, self.case.data, self.root)
        queue = {q["rq_id"]: q["next"] for q in summary["queue"]}
        self.assertEqual(
            {"RQ1": "complete", "RQ2": "serve", "RQ3": "resolve objection"},
            queue,
        )
        self.assertEqual(1, summary["responses"])
        self.assertEqual(0, summary["stale"])

    def test_open_needs_no_prerequisites(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        empty = CaseRecord(Path(tmp.name), "run-empty")
        log = empty.artifact_open("fresh log", "discovery-log")
        self.assertEqual([], log["requests"])
        second = self.case.artifact_open("second log", "discovery-log")
        self.assertEqual("A17", second["id"])

    def test_nine_templates_share_one_matter(self) -> None:
        for instance_id in (
            "A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8", "A9"
        ):
            self.assertEqual(
                self.run_id, self.case.artifact_get(instance_id)["matter"]
            )


class RequestAddTest(DiscoveryCase):
    def test_add_contract(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.add_rq(
                self.log, "", "desc", "outgoing", "documents"
            )
        with self.assertRaises(CaseError):
            artifacts.add_rq(
                self.log, "Title", "  ", "outgoing", "documents"
            )
        with self.assertRaises(CaseError):
            artifacts.add_rq(
                self.log, "Title", "desc", "sideways", "documents"
            )
        with self.assertRaises(CaseError):
            artifacts.add_rq(
                self.log, "Title", "desc", "outgoing", "subpoena"
            )
        with self.assertRaises(CaseError):
            artifacts.add_rq(
                self.log, "Title", "desc", "outgoing", "documents",
                due_date="soon",
            )
        self.assertEqual({"RQ1", "RQ2", "RQ3"}, set(self.requests()))

    def test_set_validates_and_locks_finished(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.set_rq(self.log, "RQ2", title="  ")
        with self.assertRaises(CaseError):
            artifacts.set_rq(self.log, "RQ2", direction="sideways")
        with self.assertRaises(CaseError):
            artifacts.set_rq(self.log, "RQ2", category="subpoena")
        with self.assertRaises(CaseError):
            artifacts.set_rq(self.log, "RQ2", due_date="soon")
        artifacts.decide_dr(self.log, "RQ2", "withdrawn", "Moot (fixture).")
        with self.assertRaises(CaseError):
            artifacts.set_rq(self.log, "RQ2", title="Edited")


class DiscoveryLifecycleTest(DiscoveryCase):
    def test_serve_needs_draft(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.serve_rq(self.log, "RQ1")
        artifacts.serve_rq(self.log, "RQ2")
        self.assertEqual("served", self.requests()["RQ2"]["status"])

    def test_respond_needs_served_and_valid_production(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.respond_rq(
                self.log, self.case.data, self.root,
                "RQ2", "2027-01-01", "Too early.",
            )
        artifacts.serve_rq(self.log, "RQ2")
        with self.assertRaises(CaseError):
            artifacts.respond_rq(
                self.log, self.case.data, self.root,
                "RQ2", "yesterday", "Bad date.",
            )
        with self.assertRaises(CaseError):
            artifacts.respond_rq(
                self.log, self.case.data, self.root,
                "RQ2", "2027-01-01", "  ",
            )
        with self.assertRaises(CaseError):
            artifacts.respond_rq(
                self.log, self.case.data, self.root,
                "RQ2", "2027-01-01", "Missing file.",
                ["DEMO-NOPE"],
            )
        response = artifacts.respond_rq(
            self.log, self.case.data, self.root,
            "RQ2", "2027-01-01", "Timeline produced (fixture).",
            ["DEMO-PLAN"],
        )
        self.assertEqual("RS2", response["rs_id"])
        self.assertEqual("responded", self.requests()["RQ2"]["status"])

    def test_decide_follows_the_lifecycle(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.decide_dr(self.log, "RQ1", "served", "x")
        with self.assertRaises(CaseError):
            artifacts.decide_dr(self.log, "RQ2", "complete", "x")
        with self.assertRaises(CaseError):
            artifacts.decide_dr(self.log, "RQ1", "complete", "")
        artifacts.decide_dr(self.log, "RQ1", "complete", "Sufficient (fixture).")
        self.assertEqual("complete", self.requests()["RQ1"]["status"])
        artifacts.decide_dr(self.log, "RQ1", "responded", "Follow-up arrived.")
        self.assertEqual("responded", self.requests()["RQ1"]["status"])
        artifacts.decide_dr(
            self.log, "RQ3", "served", "Objection resolved (fixture)."
        )
        self.assertEqual("served", self.requests()["RQ3"]["status"])
        artifacts.decide_dr(self.log, "RQ2", "withdrawn", "Moot (fixture).")
        artifacts.decide_dr(self.log, "RQ2", "draft", "Revived (fixture).")
        self.assertEqual("draft", self.requests()["RQ2"]["status"])


class DiscoveryStaleTest(DiscoveryCase):
    def test_changed_production_stales(self) -> None:
        inbox = self.root / "workspace/inbox/DEMO-STATUS.pdf"
        inbox.write_bytes(inbox.read_bytes() + b"\nAmendment.")
        status = artifacts.rs_status(
            self.case.data, self.root, self.responses()["RS1"]
        )
        self.assertTrue(status["stale"])
        self.assertIn("produced document DEMO-STATUS changed", status["reasons"])
        request = artifacts.rq_status(
            self.log, self.case.data, self.root, self.requests()["RQ1"]
        )
        self.assertTrue(request["stale"])
        self.assertIn("RS1:", request["reasons"][0])
        summary = artifacts.dr_summary(self.log, self.case.data, self.root)
        self.assertEqual(1, summary["stale"])

    def test_missing_production_stales(self) -> None:
        (self.root / "workspace/inbox/DEMO-STATUS.pdf").unlink()
        status = artifacts.rs_status(
            self.case.data, self.root, self.responses()["RS1"]
        )
        self.assertTrue(status["stale"])
        self.assertIn("produced document DEMO-STATUS is gone", status["reasons"])


class DiscoveryReviewTest(DiscoveryCase):
    def test_approval_binds_and_mutation_refuses(self) -> None:
        snapshot = artifacts.current_snapshot(self.log, self.case.data)
        artifacts.review_instance(
            self.log, self.case.data, "approved", "fixture review",
            [], snapshot,
        )
        self.assertEqual("approved", self.log["review"]["decision"])
        artifacts.set_rq(self.log, "RQ2", counterpart="Someone (fixture)")
        self.assertEqual("pending", self.log["review"]["decision"])
        with self.assertRaises(CaseError):
            artifacts.review_instance(
                self.log, self.case.data, "approved", "", [], snapshot
            )

    def test_diff_reports_request_and_response_changes(self) -> None:
        old = len(self.log["versions"])
        artifacts.serve_rq(self.log, "RQ2")
        artifacts.respond_rq(
            self.log, self.case.data, self.root,
            "RQ2", "2027-01-01", "Timeline produced (fixture).",
        )
        diff = artifacts.dr_diff(self.log, old, old + 2)
        served = next(
            c for c in diff["changes"]
            if c.get("rq_id") == "RQ2" and c["change"] == "status"
        )
        self.assertEqual("draft", served["before"])
        self.assertEqual("responded", served["after"])
        added = next(
            c for c in diff["changes"] if c.get("rs_id") == "RS2"
        )
        self.assertEqual("added", added["change"])
        with self.assertRaises(CaseError):
            artifacts.dr_diff(self.log, old, old + 9)

    def test_export_limits_and_history(self) -> None:
        report = artifacts.dr_export(self.log, self.case.data, self.root)
        self.assertFalse(report["historical"])
        self.assertEqual(3, len(report["summary"]["queue"]))
        self.assertEqual(1, len(report["responses"]))
        by_id = {r["rq_id"]: r for r in report["requests"]}
        self.assertIn("next: serve", by_id["RQ2"]["limitations"][0])
        old = artifacts.dr_export(self.log, self.case.data, self.root, version=1)
        self.assertTrue(old["historical"])
        self.assertEqual([], old["requests"])
        self.assertEqual([], old["responses"])
        self.assertEqual({}, old["summary"])
        with self.assertRaises(CaseError):
            artifacts.dr_export(self.log, self.case.data, self.root, version=999)


class DiscoveryCliTest(DiscoveryCase):
    def test_show_carries_summary_and_inbox(self) -> None:
        cli = load_cli()
        cli.ROOT = self.root
        shown = cli.cmd_show(Namespace(run_id=self.run_id, instance="A9"))
        self.assertEqual(3, len(shown["instance"]["requests"]))
        self.assertEqual(1, len(shown["instance"]["responses"]))
        self.assertEqual(3, len(shown["summary"]["queue"]))
        self.assertIn("DEMO-STATUS", shown["inbox"])
        self.assertIn("snapshot", shown)

    def test_discovery_ops_roundtrip(self) -> None:
        cli = load_cli()
        cli.ROOT = self.root
        added = cli.cmd_discovery(
            Namespace(
                run_id=self.run_id, instance="A9", op="add",
                title="Fee invoices", description="All invoices (fixture).",
                direction="outgoing", category="documents",
                due_date="2027-04-01", counterpart="Vendor (fixture)",
                request="", received="", summary="", documents="",
                notes="", status="", reason="",
            )
        )
        self.assertEqual("RQ4", added["saved"])
        served = cli.cmd_discovery(
            Namespace(
                run_id=self.run_id, instance="A9", op="serve", request="RQ4",
                title=None, description=None, direction=None, category=None,
                due_date=None, counterpart=None, received="", summary="",
                documents="", notes="", status="", reason="",
            )
        )
        self.assertEqual("RQ4", served["saved"])
        answered = cli.cmd_discovery(
            Namespace(
                run_id=self.run_id, instance="A9", op="respond", request="RQ4",
                received="2027-03-01", summary="Invoices produced (fixture).",
                documents="DEMO-PLAN", notes="",
                title=None, description=None, direction=None, category=None,
                due_date=None, counterpart=None, status="", reason="",
            )
        )
        self.assertEqual("RS2", answered["saved"])
        case = CaseRecord(self.root, self.run_id)
        request = next(
            r for r in case.artifact_get("A9")["requests"] if r["rq_id"] == "RQ4"
        )
        self.assertEqual("responded", request["status"])
        with self.assertRaises(CaseError):
            cli.cmd_discovery(
                Namespace(
                    run_id=self.run_id, instance="A1", op="add",
                    title="x", description="y", direction="outgoing",
                    category="other", due_date="", counterpart="",
                    request="", received="", summary="", documents="",
                    notes="", status="", reason="",
                )
            )

    def test_cli_export_covers_discovery(self) -> None:
        cli = load_cli()
        cli.ROOT = self.root
        report = cli.cmd_export(
            Namespace(run_id=self.run_id, instance="A9", version=None)
        )
        self.assertEqual(3, len(report["requests"]))
        self.assertEqual(1, len(report["responses"]))


if __name__ == "__main__":
    unittest.main()
