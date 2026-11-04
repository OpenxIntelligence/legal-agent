"""Phase 3: AI governance / use-case inventory.

Reviewer-logged AI use cases over the synthetic demo
matter: lifecycle, fixed risk tiers, required purpose
and data, intake links with pin staleness, review,
diff, and versioned export. Risk tiers are reviewer
judgments, never measured evaluations. Fully
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


class GovernanceCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.run_id = artifacts.build_demo(self.root)
        self.case = CaseRecord(self.root, self.run_id)
        self.inventory = self.case.artifact_get("A11")

    def uses(self) -> dict:
        return {u["uc_id"]: u for u in self.inventory["uses"]}

    def save(self) -> None:
        self.case.artifact_save(self.inventory, "artifact_governance", "")

    def save_source(self) -> None:
        source = self.case.artifact_get(self.inventory["source_instance"])
        self.case.artifact_save(source, "artifact_intake", "")


class GovernanceOpenTest(GovernanceCase):
    def test_demo_seeds(self) -> None:
        self.assertEqual("A7", self.inventory["source_instance"])
        uses = self.uses()
        self.assertEqual({"UC1", "UC2", "UC3"}, set(uses))
        self.assertEqual("proposed", uses["UC1"]["status"])
        self.assertEqual("unassessed", uses["UC1"]["risk"])
        self.assertEqual("IN2", uses["UC1"]["in_id"])
        self.assertEqual("assessed", uses["UC2"]["status"])
        self.assertEqual("medium", uses["UC2"]["risk"])
        self.assertEqual("IN1", uses["UC2"]["in_id"])
        self.assertEqual("approved", uses["UC3"]["status"])
        self.assertEqual("low", uses["UC3"]["risk"])
        self.assertEqual("", uses["UC3"]["in_id"])
        self.assertIn("confirms", uses["UC3"]["conditions"])

    def test_eleven_templates_share_one_matter(self) -> None:
        for instance_id in (
            "A1", "A2", "A3", "A4", "A5", "A6",
            "A7", "A8", "A9", "A10", "A11",
        ):
            self.assertEqual(
                self.run_id, self.case.artifact_get(instance_id)["matter"]
            )

    def test_open_needs_an_intake(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        empty = CaseRecord(Path(tmp.name), "run-empty")
        with self.assertRaises(CaseError):
            empty.artifact_open("no source", "ai-governance")
        with self.assertRaises(CaseError):
            self.case.artifact_open("wrong kind", "ai-governance", "A1")

    def test_open_defaults_and_names_sources(self) -> None:
        default = self.case.artifact_open("default source", "ai-governance")
        self.assertEqual("A7", default["source_instance"])
        second = self.case.artifact_open("second queue", "legal-intake")
        with self.assertRaises(CaseError):
            self.case.artifact_open("ambiguous", "ai-governance")
        named = self.case.artifact_open(
            "named source", "ai-governance", second["id"]
        )
        self.assertEqual(second["id"], named["source_instance"])


class UseAddTest(GovernanceCase):
    def test_add_validates_everything(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.add_uc(
                self.inventory, self.case.data, "", "purpose", "data"
            )
        with self.assertRaises(CaseError):
            artifacts.add_uc(
                self.inventory, self.case.data, "title", "", "data"
            )
        with self.assertRaises(CaseError):
            artifacts.add_uc(
                self.inventory, self.case.data, "title", "purpose", "  "
            )
        with self.assertRaises(CaseError):
            artifacts.add_uc(
                self.inventory, self.case.data, "title", "purpose", "data",
                in_id="IN99",
            )

    def test_risk_starts_unassessed(self) -> None:
        use = artifacts.add_uc(
            self.inventory, self.case.data, "Minutes summarizer",
            "Summarize meeting minutes (fixture).",
            "Meeting transcripts (fixture).",
        )
        self.assertEqual("UC4", use["uc_id"])
        self.assertEqual("proposed", use["status"])
        self.assertEqual("unassessed", use["risk"])
        self.assertEqual("", use["in_id"])
        self.assertIsNone(use["in_pin"])


class UseSetTest(GovernanceCase):
    def test_set_revalidates(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.set_uc(
                self.inventory, self.case.data, "UC1", risk="extreme"
            )
        with self.assertRaises(CaseError):
            artifacts.set_uc(self.inventory, self.case.data, "UC1", title=" ")
        with self.assertRaises(CaseError):
            artifacts.set_uc(
                self.inventory, self.case.data, "UC1", data_types=""
            )
        with self.assertRaises(CaseError):
            artifacts.set_uc(self.inventory, self.case.data, "UC1", in_id="IN99")
        use = artifacts.set_uc(
            self.inventory, self.case.data, "UC1",
            risk="high", system="Fictional triage assistant v2",
            conditions="Staff review every draft (fixture).",
        )
        self.assertEqual("high", use["risk"])
        self.assertIn("v2", use["system"])
        self.assertIn("Staff review", use["conditions"])

    def test_set_clears_link(self) -> None:
        use = artifacts.set_uc(self.inventory, self.case.data, "UC1", in_id="")
        self.assertEqual("", use["in_id"])
        self.assertIsNone(use["in_pin"])

    def test_finished_cases_lock_until_reopened(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.set_uc(
                self.inventory, self.case.data, "UC3", owner="J. Doe"
            )
        artifacts.decide_uc(self.inventory, "UC3", "proposed")
        use = artifacts.set_uc(
            self.inventory, self.case.data, "UC3", owner="J. Doe"
        )
        self.assertEqual("J. Doe", use["owner"])

    def test_relink_repins(self) -> None:
        before = self.uses()["UC1"]["in_pin"]
        use = artifacts.set_uc(
            self.inventory, self.case.data, "UC1", in_id="IN3"
        )
        self.assertNotEqual(before, use["in_pin"])
        status = artifacts.uc_status(self.inventory, self.case.data, use)
        self.assertEqual("current", status["state"])


class GovernanceLifecycleTest(GovernanceCase):
    def test_transitions_need_reasons(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.decide_uc(self.inventory, "UC1", "assessed")
        with self.assertRaises(CaseError):
            artifacts.decide_uc(self.inventory, "UC1", "approved", "skip")
        use = artifacts.decide_uc(self.inventory, "UC1", "assessed", "reading")
        self.assertEqual("assessed", use["status"])
        with self.assertRaises(CaseError):
            artifacts.decide_uc(self.inventory, "UC1", "approved", "too soon")
        artifacts.set_uc(self.inventory, self.case.data, "UC1", risk="high")
        use = artifacts.decide_uc(self.inventory, "UC1", "approved", "gated")
        self.assertEqual("approved", use["status"])
        self.assertEqual("gated", use["resolution"])
        use = artifacts.decide_uc(self.inventory, "UC1", "proposed")
        self.assertEqual("proposed", use["status"])

    def test_reject_and_reopen(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.decide_uc(self.inventory, "UC2", "rejected")
        use = artifacts.decide_uc(self.inventory, "UC2", "rejected", "too broad")
        self.assertEqual("rejected", use["status"])
        use = artifacts.decide_uc(self.inventory, "UC2", "proposed")
        self.assertEqual("proposed", use["status"])


class GovernanceStaleTest(GovernanceCase):
    def test_intake_change_stales(self) -> None:
        source = self.case.artifact_get("A7")
        artifacts.triage_in(source, "IN2", "M. Chen")
        self.save_source()
        status = artifacts.uc_status(
            self.inventory, self.case.data, self.uses()["UC1"]
        )
        self.assertEqual("stale", status["state"])
        self.assertIn("IN2", status["reasons"][0])
        summary = artifacts.gv_summary(self.inventory, self.case.data)
        self.assertEqual("UC1", summary["stale"][0]["uc_id"])
        self.assertEqual("stale", summary["queue"][0]["link_state"])

    def test_intake_routing_stales(self) -> None:
        source = self.case.artifact_get("A7")
        artifacts.route_in(source, "IN1", "AI desk", "fixture routing")
        self.save_source()
        status = artifacts.uc_status(
            self.inventory, self.case.data, self.uses()["UC2"]
        )
        self.assertEqual("stale", status["state"])

    def test_missing_link_orphans(self) -> None:
        status = artifacts.uc_status(
            self.inventory, self.case.data,
            {"uc_id": "UCX", "in_id": "IN99"},
        )
        self.assertEqual("orphaned", status["state"])

    def test_unassessed_risk_limits_but_never_stales(self) -> None:
        status = artifacts.uc_status(
            self.inventory, self.case.data, self.uses()["UC1"]
        )
        self.assertEqual("current", status["state"])
        summary = artifacts.gv_summary(self.inventory, self.case.data)
        self.assertEqual([], summary["stale"])
        self.assertEqual(["UC1"], summary["unassessed"])
        self.assertEqual(
            {"proposed": 1, "assessed": 1, "approved": 1}, summary["counts"]
        )
        self.assertEqual(
            {"unassessed": 1, "medium": 1, "low": 1}, summary["risk"]
        )
        self.assertEqual(
            ["UC1", "UC2"], [q["uc_id"] for q in summary["queue"]]
        )


class GovernanceReviewTest(GovernanceCase):
    def test_approval_binds_and_mutation_refuses(self) -> None:
        snapshot = artifacts.current_snapshot(self.inventory, self.case.data)
        artifacts.review_instance(
            self.inventory, self.case.data, "approved", "fixture review",
            [], snapshot,
        )
        self.assertEqual("approved", self.inventory["review"]["decision"])
        artifacts.set_uc(
            self.inventory, self.case.data, "UC1", owner="J. Doe"
        )
        self.assertEqual("pending", self.inventory["review"]["decision"])
        with self.assertRaises(CaseError):
            artifacts.review_instance(
                self.inventory, self.case.data, "approved", "", [], snapshot
            )

    def test_diff_reports_use_changes(self) -> None:
        old = len(self.inventory["versions"])
        artifacts.decide_uc(self.inventory, "UC1", "assessed", "reading")
        diff = artifacts.gv_diff(self.inventory, old, old + 1)
        moved = next(
            c for c in diff["changes"]
            if c.get("uc_id") == "UC1" and c["change"] == "status"
        )
        self.assertEqual("proposed", moved["before"])
        self.assertEqual("assessed", moved["after"])
        artifacts.add_uc(
            self.inventory, self.case.data, "Minutes summarizer",
            "Summarize meeting minutes (fixture).",
            "Meeting transcripts (fixture).",
        )
        diff = artifacts.gv_diff(self.inventory, old + 1, old + 2)
        added = next(c for c in diff["changes"] if c.get("uc_id") == "UC4")
        self.assertEqual("added", added["change"])
        with self.assertRaises(CaseError):
            artifacts.gv_diff(self.inventory, old, old + 9)

    def test_export_limits_and_history(self) -> None:
        report = artifacts.gv_export(self.inventory, self.case.data)
        self.assertFalse(report["historical"])
        self.assertEqual("A7", report["source_instance"])
        self.assertEqual(2, len(report["summary"]["queue"]))
        by_id = {u["uc_id"]: u for u in report["uses"]}
        self.assertIn("next: assess", by_id["UC1"]["limitations"][0])
        self.assertIn("risk unassessed", by_id["UC1"]["limitations"])
        self.assertNotIn("risk unassessed", by_id["UC2"]["limitations"])
        self.assertTrue(
            any("confirms" in line for line in by_id["UC3"]["limitations"])
        )
        self.assertTrue(
            any("reviewer judgments" in line for line in report["limits"])
        )
        old = artifacts.gv_export(self.inventory, self.case.data, version=1)
        self.assertTrue(old["historical"])
        self.assertEqual([], old["uses"])
        self.assertEqual({}, old["summary"])
        with self.assertRaises(CaseError):
            artifacts.gv_export(self.inventory, self.case.data, version=999)


class GovernanceCliTest(GovernanceCase):
    def test_show_carries_summary_and_intakes(self) -> None:
        cli = load_cli()
        cli.ROOT = self.root
        shown = cli.cmd_show(Namespace(run_id=self.run_id, instance="A11"))
        self.assertEqual(3, len(shown["instance"]["uses"]))
        self.assertEqual(2, len(shown["summary"]["queue"]))
        self.assertEqual(3, len(shown["intakes"]))
        self.assertIn("snapshot", shown)

    def test_governance_ops_roundtrip(self) -> None:
        cli = load_cli()
        cli.ROOT = self.root
        added = cli.cmd_governance(
            Namespace(
                run_id=self.run_id, instance="A11", op="add",
                title="Minutes summarizer",
                description="Summarize meeting minutes (fixture).",
                data="Meeting transcripts (fixture).",
                system="", owner="", intake="IN3",
                use="", status="", reason="",
                risk=None, conditions=None,
            )
        )
        self.assertEqual("UC4", added["use"])
        revised = cli.cmd_governance(
            Namespace(
                run_id=self.run_id, instance="A11", op="set", use="UC4",
                title=None, description=None, data=None, system=None,
                risk="low", owner="J. Doe", conditions=None, intake=None,
                status="", reason="",
            )
        )
        self.assertEqual("UC4", revised["use"])
        decided = cli.cmd_governance(
            Namespace(
                run_id=self.run_id, instance="A11", op="decide", use="UC4",
                status="assessed", reason="reading (fixture)",
                title=None, description=None, data=None, system=None,
                risk=None, owner=None, conditions=None, intake=None,
            )
        )
        self.assertEqual("UC4", decided["use"])
        case = CaseRecord(self.root, self.run_id)
        use = next(
            u for u in case.artifact_get("A11")["uses"] if u["uc_id"] == "UC4"
        )
        self.assertEqual("assessed", use["status"])
        self.assertEqual("IN3", use["in_id"])
        self.assertEqual("low", use["risk"])
        with self.assertRaises(CaseError):
            cli.cmd_governance(
                Namespace(
                    run_id=self.run_id, instance="A1", op="add",
                    title="x", description="y", data="z",
                    system="", owner="", intake="",
                    use="", status="", reason="",
                    risk=None, conditions=None,
                )
            )

    def test_cli_export_covers_governance(self) -> None:
        cli = load_cli()
        cli.ROOT = self.root
        report = cli.cmd_export(
            Namespace(run_id=self.run_id, instance="A11", version=None)
        )
        self.assertEqual(3, len(report["uses"]))


if __name__ == "__main__":
    unittest.main()
