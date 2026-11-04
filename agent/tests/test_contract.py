"""Phase E: clause/term tabular review over shared evidence.

Contract mechanics over the synthetic demo matter: clauses
bound to exact quoted passages through the audit's own
deterministic citation check, reviewer-recorded effects and
risks, exception queue, read-time staleness from source
changes and revocations, review, diff, and versioned
export. Fully deterministic; no model calls.
"""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from agent_kit import artifacts, connectors
from agent_kit.case_record import CaseError, CaseRecord

CLI_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "artifact.py"
)


def load_cli():
    spec = importlib.util.spec_from_file_location("artifact_cli", CLI_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ContractCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.run_id = artifacts.build_demo(self.root)
        self.case = CaseRecord(self.root, self.run_id)
        self.review = self.case.artifact_get("A4")

    def clauses(self) -> dict:
        return {c["clause_id"]: c for c in self.review["clauses"]}

    def save(self) -> None:
        self.case.artifact_save(self.review, "artifact_contract", "")


class ContractOpenTest(ContractCase):
    def test_demo_seeds(self) -> None:
        clauses = self.clauses()
        self.assertEqual({"K1", "K2", "K3"}, set(clauses))
        self.assertEqual("Acceptance", clauses["K1"]["heading"])
        self.assertEqual("confirmed", clauses["K1"]["review_status"])
        self.assertEqual("low", clauses["K1"]["risk"])
        self.assertEqual("flagged", clauses["K2"]["review_status"])
        self.assertEqual("high", clauses["K2"]["risk"])
        self.assertEqual("DEMO-PLAN", clauses["K3"]["document_id"])
        self.assertEqual("unreviewed", clauses["K3"]["review_status"])

    def test_demo_summary(self) -> None:
        summary = artifacts.ct_summary(self.case.data, self.root, self.review)
        self.assertEqual(3, summary["total"])
        self.assertEqual(0, summary["stale"])
        self.assertEqual(1, summary["confirmed"])
        self.assertEqual(
            ["K2"], [e["clause_id"] for e in summary["exceptions"]]
        )

    def test_open_needs_read_documents(self) -> None:
        fifth = self.case.artifact_open("second review", "contract-review")
        self.assertEqual("A17", fifth["id"])
        self.assertEqual([], fifth["clauses"])
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        empty = CaseRecord(Path(tmp.name), "run-empty")
        with self.assertRaises(CaseError):
            empty.artifact_open("no reads", "contract-review")

    def test_four_templates_share_one_matter(self) -> None:
        for instance_id in ("A1", "A2", "A3", "A4"):
            self.assertEqual(
                self.run_id, self.case.artifact_get(instance_id)["matter"]
            )
        self.assertEqual(7, len(self.case.data["facts"]))


class ClauseAddTest(ContractCase):
    def test_add_binds_exact_quote(self) -> None:
        clause = artifacts.add_clause(
            self.review, self.case.data, self.root,
            "DEMO-CONTRACT", 2,
            "Termination does not excuse unpaid fees.",
            "Survival",
        )
        self.assertEqual("K4", clause["clause_id"])
        self.assertTrue(clause["pin_sha"])
        status = artifacts.clause_status(self.case.data, self.root, clause)
        self.assertFalse(status["stale"])
        self.assertEqual("passed", status["outcome"])

    def test_add_refuses_unverified_passages(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.add_clause(
                self.review, self.case.data, self.root,
                "DEMO-CONTRACT", 1, "Silence means acceptance.", "Wrong",
            )
        with self.assertRaises(CaseError):
            artifacts.add_clause(
                self.review, self.case.data, self.root,
                "DEMO-CONTRACT", 9,
                "Fees are due within thirty days of invoice.", "Wrong page",
            )
        with self.assertRaises(CaseError):
            artifacts.add_clause(
                self.review, self.case.data, self.root,
                "DEMO-NOPE", 1, "Some passage text here.", "Unknown doc",
            )
        with self.assertRaises(CaseError):
            artifacts.add_clause(
                self.review, self.case.data, self.root,
                "DEMO-CONTRACT", "two",
                "Fees are due within thirty days of invoice.", "Bad page",
            )
        self.assertEqual({"K1", "K2", "K3"}, set(self.clauses()))

    def test_add_refuses_privileged_documents(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.add_clause(
                self.review, self.case.data, self.root,
                "DEMO-DRAFT", 1,
                "Load testing passed on February 20 with no open defects.",
                "Revoked",
            )
        self.assertEqual({"K1", "K2", "K3"}, set(self.clauses()))

    def test_add_needs_read_documents(self) -> None:
        self.case.data["read_ids"] = [
            d for d in self.case.data["read_ids"] if d != "DEMO-MINUTES"
        ]
        with self.assertRaises(CaseError):
            artifacts.add_clause(
                self.review, self.case.data, self.root,
                "DEMO-MINUTES", 1,
                "Final sign-off is pending the payments queue.", "Unread",
            )


class ClauseAssessTest(ContractCase):
    def test_set_effect_and_risk(self) -> None:
        artifacts.set_clause(
            self.review, "K3",
            effect="Rollback gate needs two approvals (reviewer reading).",
            risk="low",
            risk_note="Notice filing is the slow step.",
        )
        clause = self.clauses()["K3"]
        self.assertIn("two approvals", clause["effect"])
        self.assertEqual("low", clause["risk"])
        with self.assertRaises(CaseError):
            artifacts.set_clause(self.review, "K3", risk="critical")

    def test_quote_binding_is_immutable(self) -> None:
        before = dict(self.clauses()["K1"])
        artifacts.set_clause(self.review, "K1", heading="Acceptance clock")
        after = self.clauses()["K1"]
        for key in ("document_id", "page", "quote", "pin_sha"):
            self.assertEqual(before[key], after[key])

    def test_flag_and_retract(self) -> None:
        artifacts.flag_clause(self.review, "K3", "confirmed", "fixture reviewed")
        self.assertEqual("confirmed", self.clauses()["K3"]["review_status"])
        with self.assertRaises(CaseError):
            artifacts.flag_clause(self.review, "K3", "maybe", "")
        with self.assertRaises(CaseError):
            artifacts.retract_clause(self.review, "K3", "")
        artifacts.retract_clause(self.review, "K3", "superseded by amendment (fixture)")
        self.assertTrue(self.clauses()["K3"]["retracted"])
        summary = artifacts.ct_summary(self.case.data, self.root, self.review)
        self.assertEqual(2, summary["total"])


class ClauseStaleTest(ContractCase):
    def test_changed_source_stales_its_clauses(self) -> None:
        inbox = self.root / "workspace/inbox/DEMO-CONTRACT.pdf"
        inbox.write_bytes(inbox.read_bytes() + b"\nAmendment.")
        stale_k1 = artifacts.clause_status(
            self.case.data, self.root, self.clauses()["K1"]
        )
        self.assertTrue(stale_k1["stale"])
        self.assertTrue(
            any("changed since" in reason for reason in stale_k1["reasons"])
        )
        fresh_k3 = artifacts.clause_status(
            self.case.data, self.root, self.clauses()["K3"]
        )
        self.assertFalse(fresh_k3["stale"])
        summary = artifacts.ct_summary(self.case.data, self.root, self.review)
        self.assertEqual(2, summary["stale"])

    def test_revocation_stales_its_clauses(self) -> None:
        self.case.flag_privileged("DEMO-PLAN", "counsel restricted the plan")
        status = artifacts.clause_status(
            self.case.data, self.root, self.clauses()["K3"]
        )
        self.assertTrue(status["stale"])
        self.assertTrue(
            any("revoked" in reason for reason in status["reasons"])
        )


class ContractReviewTest(ContractCase):
    def test_approval_binds_and_mutation_refuses(self) -> None:
        snapshot = artifacts.current_snapshot(self.review, self.case.data)
        artifacts.review_instance(
            self.review, self.case.data, "approved", "fixture review", [], snapshot
        )
        self.assertEqual("approved", self.review["review"]["decision"])
        artifacts.set_clause(self.review, "K3", risk_note="late note")
        self.assertEqual("pending", self.review["review"]["decision"])
        with self.assertRaises(CaseError):
            artifacts.review_instance(
                self.review, self.case.data, "approved", "", [], snapshot
            )

    def test_diff_reports_value_changes(self) -> None:
        old = len(self.review["versions"])
        artifacts.set_clause(self.review, "K2", risk="low")
        diff = artifacts.ct_diff(self.review, old, old + 1)
        change = next(
            c for c in diff["changes"]
            if c["clause_id"] == "K2" and c["change"] == "risk"
        )
        self.assertEqual("high", change["before"])
        self.assertEqual("low", change["after"])
        with self.assertRaises(CaseError):
            artifacts.ct_diff(self.review, old, old + 9)

    def test_export_limits_and_history(self) -> None:
        report = artifacts.ct_export(self.review, self.case.data, self.root)
        self.assertFalse(report["historical"])
        self.assertEqual(1, len(report["summary"]["exceptions"]))
        by_id = {c["clause_id"]: c for c in report["clauses"]}
        self.assertTrue(
            any("high risk" in limit for limit in by_id["K2"]["limitations"])
        )
        self.assertIn("review: unreviewed", by_id["K3"]["limitations"])
        self.assertTrue(
            any("reviewer judgment" in limit for limit in report["limits"])
        )
        old = artifacts.ct_export(self.review, self.case.data, self.root, version=1)
        self.assertTrue(old["historical"])
        self.assertEqual([], old["clauses"])
        self.assertEqual({}, old["summary"])
        with self.assertRaises(CaseError):
            artifacts.ct_export(self.review, self.case.data, self.root, version=999)


class ContractCliTest(ContractCase):
    def test_show_carries_summary_and_reads(self) -> None:
        cli = load_cli()
        cli.ROOT = self.root
        shown = cli.cmd_show(Namespace(run_id=self.run_id, instance="A4"))
        self.assertEqual(3, len(shown["instance"]["clauses"]))
        self.assertEqual(1, len(shown["summary"]["exceptions"]))
        self.assertIn("DEMO-CONTRACT", shown["read_documents"])
        self.assertIn("snapshot", shown)

    def test_contract_ops_roundtrip(self) -> None:
        cli = load_cli()
        cli.ROOT = self.root
        added = cli.cmd_contract(
            Namespace(
                run_id=self.run_id, instance="A4", op="add",
                document="DEMO-CONTRACT", page="1",
                quote="Fees are due within thirty days of invoice.",
                heading="Payment terms",
                clause="", effect=None, risk=None, risk_note=None,
                status="", note="", reason="",
            )
        )
        self.assertEqual("K4", added["clause"])
        flagged = cli.cmd_contract(
            Namespace(
                run_id=self.run_id, instance="A4", op="flag", clause="K4",
                status="flagged", note="terms need counsel",
                document="", page="", quote="", heading=None,
                effect=None, risk=None, risk_note=None, reason="",
            )
        )
        self.assertEqual("K4", flagged["clause"])
        case = CaseRecord(self.root, self.run_id)
        clause = next(
            c for c in case.artifact_get("A4")["clauses"] if c["clause_id"] == "K4"
        )
        self.assertEqual("flagged", clause["review_status"])
        with self.assertRaises(CaseError):
            cli.cmd_contract(
                Namespace(
                    run_id=self.run_id, instance="A1", op="add",
                    document="DEMO-CONTRACT", page="1",
                    quote="Fees are due within thirty days of invoice.",
                    heading="Payment terms",
                    clause="", effect=None, risk=None, risk_note=None,
                    status="", note="", reason="",
                )
            )

    def test_cli_export_covers_matrix_and_contract(self) -> None:
        cli = load_cli()
        cli.ROOT = self.root
        matrix = cli.cmd_export(
            Namespace(run_id=self.run_id, instance="A3", version=None)
        )
        self.assertEqual(3, len(matrix["cells"]))
        contract = cli.cmd_export(
            Namespace(run_id=self.run_id, instance="A4", version=None)
        )
        self.assertEqual(3, len(contract["clauses"]))


class ConnectorContractTest(unittest.TestCase):
    def test_demo_configures_no_connectors(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        run_id = artifacts.build_demo(root)
        case = CaseRecord(root, run_id)
        self.assertEqual([], connectors.configured(case.data))

    def test_malformed_connectors_fail_closed(self) -> None:
        with self.assertRaises(CaseError):
            connectors.configured({"connectors": [{"id": "room-a"}]})
        with self.assertRaises(CaseError):
            connectors.configured({"connectors": ["room-a"]})
        with self.assertRaises(CaseError):
            connectors.configured({"connectors": {}})

    def test_complete_connector_validates(self) -> None:
        entry = {
            "id": "room-a",
            "system": "fictional data room v3",
            "auth": "per-user token, viewers excluded",
            "can_write": False,
            "freshness": "nightly sync, last checked unknown",
            "revocation": "deleted records surface as revoked",
            "limits": "100 calls/day with backoff",
            "record_uri": "room-a://record/{id}",
            "retention": "minimized, encrypted, 30-day audit",
            "extra": "dropped",
        }
        valid = connectors.configured({"connectors": [entry]})
        self.assertEqual(
            sorted(connectors.REQUIRED_FIELDS), sorted(valid[0].keys())
        )
        self.assertFalse(valid[0]["can_write"])


if __name__ == "__main__":
    unittest.main()
