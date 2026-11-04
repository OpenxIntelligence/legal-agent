"""Phase E continued: legal intake and routing.

Intake mechanics over the synthetic demo matter: requests
with classification and urgency, triage with an owner,
routing with a rationale, reasoned decline/close/reopen
decisions, an urgent-first queue, staleness from linked
issue revisions, review, diff, and versioned export.
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

CLI_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "artifact.py"
)


def load_cli():
    spec = importlib.util.spec_from_file_location("artifact_cli", CLI_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class IntakeCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.run_id = artifacts.build_demo(self.root)
        self.case = CaseRecord(self.root, self.run_id)
        self.intake = self.case.artifact_get("A7")

    def requests(self) -> dict:
        return {r["in_id"]: r for r in self.intake["intakes"]}

    def save(self) -> None:
        self.case.artifact_save(self.intake, "artifact_intake", "")


class IntakeOpenTest(IntakeCase):
    def test_demo_seeds(self) -> None:
        requests = self.requests()
        self.assertEqual({"IN1", "IN2", "IN3"}, set(requests))
        self.assertEqual("triaged", requests["IN1"]["status"])
        self.assertEqual("R. Alvarez", requests["IN1"]["assignee"])
        self.assertEqual("readiness", requests["IN1"]["issue_id"])
        self.assertEqual("new", requests["IN2"]["status"])
        self.assertEqual("critical", requests["IN2"]["urgency"])
        self.assertEqual("", requests["IN2"]["assignee"])
        self.assertEqual("routed", requests["IN3"]["status"])
        self.assertEqual("Regulatory desk", requests["IN3"]["route"])
        self.assertTrue(requests["IN3"]["rationale"])

    def test_demo_queue_orders_urgent_first(self) -> None:
        summary = artifacts.in_summary(self.intake, self.case.data)
        queue = summary["queue"]
        self.assertEqual(["IN2", "IN1"], [q["in_id"] for q in queue])
        self.assertEqual(2, summary["unassigned"])
        self.assertEqual(0, summary["stale"])
        self.assertEqual(0, summary["orphaned"])

    def test_open_needs_no_prerequisites(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        empty = CaseRecord(Path(tmp.name), "run-empty")
        queue = empty.artifact_open("fresh queue", "legal-intake")
        self.assertEqual([], queue["intakes"])
        second = self.case.artifact_open("second queue", "legal-intake")
        self.assertEqual("A17", second["id"])

    def test_seven_templates_share_one_matter(self) -> None:
        for instance_id in ("A1", "A2", "A3", "A4", "A5", "A6", "A7"):
            self.assertEqual(
                self.run_id, self.case.artifact_get(instance_id)["matter"]
            )


class IntakeAddTest(IntakeCase):
    def test_add_contract(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.add_in(
                self.intake, self.case.data, "", "desc", "contract", "routine"
            )
        with self.assertRaises(CaseError):
            artifacts.add_in(
                self.intake, self.case.data, "Title", "  ", "contract", "routine"
            )
        with self.assertRaises(CaseError):
            artifacts.add_in(
                self.intake, self.case.data, "Title", "desc", "tax", "routine"
            )
        with self.assertRaises(CaseError):
            artifacts.add_in(
                self.intake, self.case.data, "Title", "desc", "contract", "asap"
            )
        with self.assertRaises(CaseError):
            artifacts.add_in(
                self.intake, self.case.data, "Title", "desc", "contract",
                "routine", issue_id="damages",
            )
        self.assertEqual({"IN1", "IN2", "IN3"}, set(self.requests()))

    def test_issue_link_is_optional(self) -> None:
        linked = artifacts.add_in(
            self.intake, self.case.data, "Linked", "desc", "other", "routine",
            issue_id="testing",
        )
        self.assertEqual("testing", linked["issue_id"])
        self.assertTrue(linked["issue_pin"])
        plain = artifacts.add_in(
            self.intake, self.case.data, "Plain", "desc", "other", "routine"
        )
        self.assertEqual("", plain["issue_id"])
        self.assertIsNone(plain["issue_pin"])


class IntakeLifecycleTest(IntakeCase):
    def test_triage_confirms_and_assigns(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.triage_in(self.intake, "IN2", "")
        with self.assertRaises(CaseError):
            artifacts.triage_in(self.intake, "IN1", "Someone")
        artifacts.triage_in(self.intake, "IN2", "J. Osei")
        request = self.requests()["IN2"]
        self.assertEqual("triaged", request["status"])
        self.assertEqual("J. Osei", request["assignee"])

    def test_route_needs_destination_and_rationale(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.route_in(self.intake, "IN1", "", "reason")
        with self.assertRaises(CaseError):
            artifacts.route_in(self.intake, "IN1", "Desk", "")
        with self.assertRaises(CaseError):
            artifacts.route_in(self.intake, "IN3", "Desk", "again")
        artifacts.route_in(
            self.intake, "IN2", "Litigation desk", "Allegation needs review."
        )
        request = self.requests()["IN2"]
        self.assertEqual("routed", request["status"])
        queue = artifacts.in_summary(self.intake, self.case.data)["queue"]
        self.assertEqual(["IN1"], [q["in_id"] for q in queue])

    def test_decide_follows_the_lifecycle(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.decide_in(self.intake, "IN1", "triaged", "x")
        with self.assertRaises(CaseError):
            artifacts.decide_in(self.intake, "IN3", "declined", "x")
        with self.assertRaises(CaseError):
            artifacts.decide_in(self.intake, "IN2", "declined", "")
        artifacts.decide_in(self.intake, "IN2", "declined", "Duplicate (fixture).")
        self.assertEqual("declined", self.requests()["IN2"]["status"])
        artifacts.decide_in(self.intake, "IN2", "new", "Not a duplicate.")
        self.assertEqual("new", self.requests()["IN2"]["status"])
        artifacts.decide_in(self.intake, "IN3", "closed", "Answered (fixture).")
        self.assertEqual("closed", self.requests()["IN3"]["status"])
        artifacts.decide_in(self.intake, "IN3", "routed", "Follow-up arrived.")
        self.assertEqual("routed", self.requests()["IN3"]["status"])

    def test_set_locks_finished_requests(self) -> None:
        artifacts.decide_in(self.intake, "IN2", "declined", "Duplicate (fixture).")
        with self.assertRaises(CaseError):
            artifacts.set_in(self.intake, self.case.data, "IN2", urgency="routine")
        artifacts.decide_in(self.intake, "IN2", "new", "Not a duplicate.")
        artifacts.set_in(self.intake, self.case.data, "IN2", urgency="expedited")
        self.assertEqual("expedited", self.requests()["IN2"]["urgency"])

    def test_set_validates(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.set_in(self.intake, self.case.data, "IN1", title="  ")
        with self.assertRaises(CaseError):
            artifacts.set_in(self.intake, self.case.data, "IN1", category="tax")
        with self.assertRaises(CaseError):
            artifacts.set_in(self.intake, self.case.data, "IN1", urgency="asap")
        with self.assertRaises(CaseError):
            artifacts.set_in(
                self.intake, self.case.data, "IN1", issue_id="damages"
            )


class IntakeStaleTest(IntakeCase):
    def test_issue_revision_stales_and_set_rebounds(self) -> None:
        self.case.data["checklist"]["readiness"]["question"] = "Was it ready?"
        stale = artifacts.in_status(
            self.intake, self.case.data, self.requests()["IN1"]
        )
        self.assertTrue(stale["stale"])
        self.assertIn("the linked issue was revised", stale["reasons"])
        fresh = artifacts.in_status(
            self.intake, self.case.data, self.requests()["IN2"]
        )
        self.assertFalse(fresh["stale"])
        artifacts.set_in(
            self.intake, self.case.data, "IN1", urgency="critical"
        )
        rebound = artifacts.in_status(
            self.intake, self.case.data, self.requests()["IN1"]
        )
        self.assertFalse(rebound["stale"])

    def test_issue_removal_orphans_visibly(self) -> None:
        del self.case.data["checklist"]["readiness"]
        status = artifacts.in_status(
            self.intake, self.case.data, self.requests()["IN1"]
        )
        self.assertTrue(status["orphaned"])
        self.assertIn("the linked issue was removed", status["reasons"])
        queue = artifacts.in_summary(self.intake, self.case.data)["queue"]
        self.assertIn("IN1", [q["in_id"] for q in queue])
        report = artifacts.in_export(self.intake, self.case.data)
        by_id = {r["in_id"]: r for r in report["intakes"]}
        self.assertIn("orphaned: linked issue removed", by_id["IN1"]["limitations"])


class IntakeReviewTest(IntakeCase):
    def test_approval_binds_and_mutation_refuses(self) -> None:
        snapshot = artifacts.current_snapshot(self.intake, self.case.data)
        artifacts.review_instance(
            self.intake, self.case.data, "approved", "fixture review",
            [], snapshot,
        )
        self.assertEqual("approved", self.intake["review"]["decision"])
        artifacts.set_in(
            self.intake, self.case.data, "IN2", urgency="expedited"
        )
        self.assertEqual("pending", self.intake["review"]["decision"])
        with self.assertRaises(CaseError):
            artifacts.review_instance(
                self.intake, self.case.data, "approved", "", [], snapshot
            )

    def test_diff_reports_value_changes(self) -> None:
        old = len(self.intake["versions"])
        artifacts.triage_in(self.intake, "IN2", "J. Osei")
        artifacts.route_in(self.intake, "IN1", "Contracts desk", "Renewal (fixture).")
        diff = artifacts.in_diff(self.intake, old, old + 2)
        triaged = next(
            c for c in diff["changes"]
            if c.get("in_id") == "IN2" and c["change"] == "status"
        )
        self.assertEqual("triaged", triaged["after"])
        routed = next(
            c for c in diff["changes"]
            if c.get("in_id") == "IN1" and c["change"] == "route"
        )
        self.assertEqual("Contracts desk", routed["after"])
        with self.assertRaises(CaseError):
            artifacts.in_diff(self.intake, old, old + 9)

    def test_export_limits_and_history(self) -> None:
        report = artifacts.in_export(self.intake, self.case.data)
        self.assertFalse(report["historical"])
        self.assertEqual(2, len(report["summary"]["queue"]))
        by_id = {r["in_id"]: r for r in report["intakes"]}
        self.assertIn("awaiting routing", by_id["IN2"]["limitations"][0])
        self.assertEqual(
            "Was the rollout ready?", by_id["IN1"]["issue_question"]
        )
        old = artifacts.in_export(self.intake, self.case.data, version=1)
        self.assertTrue(old["historical"])
        self.assertEqual([], old["intakes"])
        self.assertEqual({}, old["summary"])
        with self.assertRaises(CaseError):
            artifacts.in_export(self.intake, self.case.data, version=999)


class IntakeCliTest(IntakeCase):
    def test_show_carries_summary_and_issues(self) -> None:
        cli = load_cli()
        cli.ROOT = self.root
        shown = cli.cmd_show(Namespace(run_id=self.run_id, instance="A7"))
        self.assertEqual(3, len(shown["instance"]["intakes"]))
        self.assertEqual(2, len(shown["summary"]["queue"]))
        self.assertEqual(4, len(shown["issues"]))
        self.assertIn("snapshot", shown)

    def test_intake_ops_roundtrip(self) -> None:
        cli = load_cli()
        cli.ROOT = self.root
        added = cli.cmd_intake(
            Namespace(
                run_id=self.run_id, instance="A7", op="add",
                title="Fee dispute", description="Vendor contests an invoice.",
                category="contract", urgency="routine",
                requester="Finance (fixture)", issue="",
                intake="", assignee=None, route="", rationale="",
                status="", reason="",
            )
        )
        self.assertEqual("IN4", added["in"])
        triaged = cli.cmd_intake(
            Namespace(
                run_id=self.run_id, instance="A7", op="triage", intake="IN4",
                assignee="R. Alvarez",
                title=None, description=None, category=None, urgency=None,
                requester=None, issue=None, route="", rationale="",
                status="", reason="",
            )
        )
        self.assertEqual("IN4", triaged["in"])
        routed = cli.cmd_intake(
            Namespace(
                run_id=self.run_id, instance="A7", op="route", intake="IN4",
                route="Contracts desk", rationale="Standard dispute (fixture).",
                title=None, description=None, category=None, urgency=None,
                requester=None, assignee=None, issue=None,
                status="", reason="",
            )
        )
        self.assertEqual("IN4", routed["in"])
        case = CaseRecord(self.root, self.run_id)
        request = next(
            r for r in case.artifact_get("A7")["intakes"] if r["in_id"] == "IN4"
        )
        self.assertEqual("routed", request["status"])
        with self.assertRaises(CaseError):
            cli.cmd_intake(
                Namespace(
                    run_id=self.run_id, instance="A1", op="add",
                    title="x", description="y", category="other",
                    urgency="routine", requester="", issue="",
                    intake="", assignee=None, route="", rationale="",
                    status="", reason="",
                )
            )

    def test_cli_export_covers_intake(self) -> None:
        cli = load_cli()
        cli.ROOT = self.root
        report = cli.cmd_export(
            Namespace(run_id=self.run_id, instance="A7", version=None)
        )
        self.assertEqual(3, len(report["intakes"]))


if __name__ == "__main__":
    unittest.main()
