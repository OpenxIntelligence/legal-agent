"""Phase E continued: playbook deviation and exception queue.

Playbook mechanics over the synthetic demo matter: reviewer
positions, per-clause verdicts pinned to both sides, an
exception queue for deviating or undecided items, staleness
from either side's revision, visible orphaning on
retraction, review, diff, and versioned export. Fully
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


class PlaybookCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.run_id = artifacts.build_demo(self.root)
        self.case = CaseRecord(self.root, self.run_id)
        self.playbook = self.case.artifact_get("A5")
        self.source = self.case.artifact_get("A4")

    def rules(self) -> dict:
        return {r["rule_id"]: r for r in self.playbook["rules"]}

    def deviations(self) -> dict:
        return {d["dev_id"]: d for d in self.playbook["deviations"]}

    def save(self) -> None:
        self.case.artifact_save(self.playbook, "artifact_playbook", "")


class PlaybookOpenTest(PlaybookCase):
    def test_demo_seeds(self) -> None:
        self.assertEqual("A4", self.playbook["source_instance"])
        rules = self.rules()
        self.assertEqual({"R1", "R2"}, set(rules))
        self.assertEqual("critical", rules["R2"]["severity"])
        deviations = self.deviations()
        self.assertEqual({"D1", "D2", "D3"}, set(deviations))
        self.assertEqual("conforms", deviations["D1"]["verdict"])
        self.assertEqual("deviates", deviations["D2"]["verdict"])
        self.assertEqual("undecided", deviations["D3"]["verdict"])

    def test_demo_queue_orders_critical_first(self) -> None:
        summary = artifacts.pb_summary(self.playbook, self.case.data)
        self.assertEqual(2, summary["rules"])
        self.assertEqual(3, summary["deviations"])
        self.assertEqual(1, summary["stale"])
        self.assertEqual(0, summary["orphaned"])
        queue = summary["exceptions"]
        self.assertEqual(["D2", "D3"], [e["dev_id"] for e in queue])
        self.assertTrue(queue[1]["stale"])
        self.assertIn("no rationale recorded", queue[1]["reasons"])

    def test_open_needs_a_contract_review(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        empty = CaseRecord(Path(tmp.name), "run-empty")
        with self.assertRaises(CaseError):
            empty.artifact_open("no source", "playbook-deviation")
        with self.assertRaises(CaseError):
            self.case.artifact_open("wrong kind", "playbook-deviation", "A1")

    def test_open_defaults_and_names_sources(self) -> None:
        default = self.case.artifact_open("default source", "playbook-deviation")
        self.assertEqual("A4", default["source_instance"])
        second = self.case.artifact_open("second review", "contract-review")
        with self.assertRaises(CaseError):
            self.case.artifact_open("ambiguous", "playbook-deviation")
        named = self.case.artifact_open(
            "named source", "playbook-deviation", second["id"]
        )
        self.assertEqual(second["id"], named["source_instance"])

    def test_five_templates_share_one_matter(self) -> None:
        for instance_id in ("A1", "A2", "A3", "A4", "A5"):
            self.assertEqual(
                self.run_id, self.case.artifact_get(instance_id)["matter"]
            )


class RuleTest(PlaybookCase):
    def test_add_rule_contract(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.add_rule(self.playbook, "", "position", "standard")
        with self.assertRaises(CaseError):
            artifacts.add_rule(self.playbook, "Topic", "  ", "standard")
        with self.assertRaises(CaseError):
            artifacts.add_rule(self.playbook, "Topic", "position", "urgent")
        rule = artifacts.add_rule(
            self.playbook, "Notices", "Notices must be written (fixture).", "info"
        )
        self.assertEqual("R3", rule["rule_id"])
        self.assertEqual(1, rule["position_version"])

    def test_set_rule_bumps_position_version(self) -> None:
        artifacts.set_rule(self.playbook, "R1", severity="critical")
        self.assertEqual("critical", self.rules()["R1"]["severity"])
        self.assertEqual(1, self.rules()["R1"]["position_version"])
        artifacts.set_rule(self.playbook, "R1", position="New floor: five days.")
        self.assertEqual(2, self.rules()["R1"]["position_version"])
        with self.assertRaises(CaseError):
            artifacts.set_rule(self.playbook, "R1", severity="urgent")
        with self.assertRaises(CaseError):
            artifacts.set_rule(self.playbook, "R1", position="  ")

    def test_retract_rule_needs_reason(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.retract_rule(self.playbook, "R2", "")
        artifacts.retract_rule(self.playbook, "R2", "superseded (fixture)")
        self.assertTrue(self.rules()["R2"]["retracted"])
        with self.assertRaises(CaseError):
            artifacts.set_rule(self.playbook, "R2", severity="info")


class AssessTest(PlaybookCase):
    def test_assess_contract(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.assess(
                self.playbook, self.case.data, "K9", "R1", "conforms", "x"
            )
        with self.assertRaises(CaseError):
            artifacts.assess(
                self.playbook, self.case.data, "K1", "R9", "conforms", "x"
            )
        with self.assertRaises(CaseError):
            artifacts.assess(
                self.playbook, self.case.data, "K1", "R1", "mostly", "x"
            )
        artifacts.retract_rule(self.playbook, "R2", "superseded (fixture)")
        with self.assertRaises(CaseError):
            artifacts.assess(
                self.playbook, self.case.data, "K2", "R2", "conforms", "x"
            )

    def test_reassess_supersedes_with_fresh_pins(self) -> None:
        deviation = artifacts.assess(
            self.playbook, self.case.data, "K3", "R1", "conforms",
            "Rollback gate is not an acceptance clock (reviewer call).",
        )
        self.assertEqual("D3", deviation["dev_id"])
        self.assertEqual("conforms", deviation["verdict"])
        self.assertEqual(
            ["assessed", "re-assessed"],
            [h["change"] for h in deviation["history"]],
        )
        status = artifacts.deviation_status(
            self.playbook, self.case.data, deviation
        )
        self.assertFalse(status["stale"])
        queue = artifacts.pb_exceptions(self.playbook, self.case.data)
        self.assertEqual(["D2"], [e["dev_id"] for e in queue])

    def test_decided_verdicts_leave_the_queue(self) -> None:
        artifacts.assess(
            self.playbook, self.case.data, "K2", "R2", "waived",
            "Counsel accepted the cap (fixture).",
        )
        artifacts.assess(
            self.playbook, self.case.data, "K3", "R1", "conforms", "Not a clock."
        )
        queue = artifacts.pb_exceptions(self.playbook, self.case.data)
        self.assertEqual([], queue)


class DeviationStaleTest(PlaybookCase):
    def test_clause_revision_stales(self) -> None:
        artifacts.set_clause(self.source, "K1", risk_note="late note")
        self.case.artifact_save(self.source, "artifact_contract", "K1")
        status = artifacts.deviation_status(
            self.playbook, self.case.data, self.deviations()["D1"]
        )
        self.assertTrue(status["stale"])
        self.assertIn("the clause was revised since assessment", status["reasons"])
        artifacts.assess(
            self.playbook, self.case.data, "K1", "R1", "conforms",
            "Still meets the floor after the note.",
        )
        status = artifacts.deviation_status(
            self.playbook, self.case.data, self.deviations()["D1"]
        )
        self.assertFalse(status["stale"])

    def test_rule_revision_stales(self) -> None:
        artifacts.set_rule(self.playbook, "R2", position="New cap floor.")
        status = artifacts.deviation_status(
            self.playbook, self.case.data, self.deviations()["D2"]
        )
        self.assertTrue(status["stale"])
        self.assertIn("the rule was revised since assessment", status["reasons"])

    def test_rule_retraction_orphans_visibly(self) -> None:
        artifacts.retract_rule(self.playbook, "R2", "superseded (fixture)")
        status = artifacts.deviation_status(
            self.playbook, self.case.data, self.deviations()["D2"]
        )
        self.assertTrue(status["orphaned"])
        self.assertIn("the rule was retracted", status["reasons"])
        queue = artifacts.pb_exceptions(self.playbook, self.case.data)
        self.assertEqual(["D3"], [e["dev_id"] for e in queue])
        self.assertIn("D2", self.deviations())
        report = artifacts.pb_export(self.playbook, self.case.data)
        by_id = {d["dev_id"]: d for d in report["deviations"]}
        self.assertIn("orphaned: out of the queue", by_id["D2"]["limitations"])

    def test_clause_retraction_orphans(self) -> None:
        artifacts.retract_clause(self.source, "K3", "superseded (fixture)")
        self.case.artifact_save(self.source, "artifact_contract", "K3")
        status = artifacts.deviation_status(
            self.playbook, self.case.data, self.deviations()["D3"]
        )
        self.assertTrue(status["orphaned"])
        self.assertIn("the clause was retracted", status["reasons"])
        queue = artifacts.pb_exceptions(self.playbook, self.case.data)
        self.assertEqual(["D2"], [e["dev_id"] for e in queue])


class PlaybookReviewTest(PlaybookCase):
    def test_approval_binds_and_mutation_refuses(self) -> None:
        snapshot = artifacts.current_snapshot(self.playbook, self.case.data)
        artifacts.review_instance(
            self.playbook, self.case.data, "approved", "fixture review",
            [], snapshot,
        )
        self.assertEqual("approved", self.playbook["review"]["decision"])
        artifacts.assess(
            self.playbook, self.case.data, "K3", "R1", "conforms", "Late call."
        )
        self.assertEqual("pending", self.playbook["review"]["decision"])
        with self.assertRaises(CaseError):
            artifacts.review_instance(
                self.playbook, self.case.data, "approved", "", [], snapshot
            )

    def test_diff_reports_rule_and_verdict_changes(self) -> None:
        old = len(self.playbook["versions"])
        artifacts.set_rule(self.playbook, "R1", severity="critical")
        artifacts.assess(
            self.playbook, self.case.data, "K3", "R1", "deviates", "Clock too vague."
        )
        diff = artifacts.pb_diff(self.playbook, old, old + 2)
        rule_change = next(
            c for c in diff["changes"]
            if c.get("rule_id") == "R1" and c["change"] == "severity"
        )
        self.assertEqual("critical", rule_change["after"])
        verdict_change = next(
            c for c in diff["changes"]
            if c.get("dev_id") == "D3" and c["change"] == "verdict"
        )
        self.assertEqual("undecided", verdict_change["before"])
        self.assertEqual("deviates", verdict_change["after"])
        with self.assertRaises(CaseError):
            artifacts.pb_diff(self.playbook, old, old + 9)

    def test_export_limits_and_history(self) -> None:
        report = artifacts.pb_export(self.playbook, self.case.data)
        self.assertFalse(report["historical"])
        self.assertEqual("A4", report["source_instance"])
        self.assertEqual(2, len(report["summary"]["exceptions"]))
        self.assertTrue(
            any("reviewer judgment" in limit for limit in report["limits"])
        )
        old = artifacts.pb_export(self.playbook, self.case.data, version=1)
        self.assertTrue(old["historical"])
        self.assertEqual([], old["rules"])
        self.assertEqual([], old["deviations"])
        self.assertEqual({}, old["summary"])
        with self.assertRaises(CaseError):
            artifacts.pb_export(self.playbook, self.case.data, version=999)


class PlaybookCliTest(PlaybookCase):
    def test_show_carries_summary_and_clauses(self) -> None:
        cli = load_cli()
        cli.ROOT = self.root
        shown = cli.cmd_show(Namespace(run_id=self.run_id, instance="A5"))
        self.assertEqual(2, len(shown["instance"]["rules"]))
        self.assertEqual(3, len(shown["instance"]["deviations"]))
        self.assertEqual(2, len(shown["summary"]["exceptions"]))
        self.assertEqual(3, len(shown["clauses"]))
        self.assertIn("snapshot", shown)

    def test_playbook_ops_roundtrip(self) -> None:
        cli = load_cli()
        cli.ROOT = self.root
        added = cli.cmd_playbook(
            Namespace(
                run_id=self.run_id, instance="A5", op="add-rule",
                topic="Notices", position="Notices must be written (fixture).",
                severity="info",
                rule="", clause="", verdict="", rationale="", reason="",
            )
        )
        self.assertEqual("R3", added["saved"])
        decided = cli.cmd_playbook(
            Namespace(
                run_id=self.run_id, instance="A5", op="assess",
                clause="K3", rule="R3", verdict="conforms",
                rationale="Termination clause carries its own notice.",
                topic=None, position=None, severity=None, reason="",
            )
        )
        self.assertEqual("D4", decided["saved"])
        case = CaseRecord(self.root, self.run_id)
        deviation = next(
            d for d in case.artifact_get("A5")["deviations"]
            if d["dev_id"] == "D4"
        )
        self.assertEqual("conforms", deviation["verdict"])
        with self.assertRaises(CaseError):
            cli.cmd_playbook(
                Namespace(
                    run_id=self.run_id, instance="A4", op="assess",
                    clause="K3", rule="R3", verdict="conforms", rationale="x",
                    topic=None, position=None, severity=None, reason="",
                )
            )

    def test_cli_export_covers_playbook(self) -> None:
        cli = load_cli()
        cli.ROOT = self.root
        report = cli.cmd_export(
            Namespace(run_id=self.run_id, instance="A5", version=None)
        )
        self.assertEqual(2, len(report["rules"]))
        self.assertEqual(3, len(report["deviations"]))


if __name__ == "__main__":
    unittest.main()
