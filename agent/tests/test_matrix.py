"""Phase D: jurisdiction survey matrix over shared evidence.

Matrix mechanics over the synthetic demo matter: cell
contracts, branch binding, authority metadata, read-time
staleness from applicability inputs, readiness that gates
definitive comparison on unknowns, review, diff, and
versioned export. Fully deterministic; no model calls.
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


class MatrixCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.run_id = artifacts.build_demo(self.root)
        self.case = CaseRecord(self.root, self.run_id)
        self.matrix = self.case.artifact_get("A3")

    def cells(self) -> dict:
        return {c["cell_id"]: c for c in self.matrix["cells"]}

    def save(self) -> None:
        self.case.artifact_save(self.matrix, "artifact_matrix", "")


class MatrixOpenTest(MatrixCase):
    def test_demo_seeds(self) -> None:
        cells = self.cells()
        self.assertEqual({"C1", "C2", "C3"}, set(cells))
        self.assertEqual("readiness", cells["C1"]["issue_id"])
        self.assertEqual("B1", cells["C1"]["branch_id"])
        self.assertEqual("B2", cells["C2"]["branch_id"])
        self.assertEqual("", cells["C3"]["branch_id"])
        branches = self.case.data["branches"]
        self.assertEqual("unknown", branches["B1"]["status"])
        self.assertEqual("known", branches["B2"]["status"])
        matter = self.case.data["matter"]["fields"]
        self.assertEqual("unknown", matter["governing_law"]["status"])
        self.assertEqual("known", matter["purpose"]["status"])

    def test_demo_starts_provisional(self) -> None:
        ready = artifacts.jx_readiness(self.case.data, self.matrix)
        self.assertEqual(0, ready["resolved"])
        self.assertEqual(3, ready["total"])
        self.assertTrue(ready["provisional"])
        reasons = {b["cell_id"]: b["reasons"] for b in ready["blockers"]}
        self.assertIn("branch B1 is unknown", reasons["C1"])
        self.assertIn("no linked branch", reasons["C3"])
        self.assertTrue(all("effective date unknown" in r for r in reasons.values()))

    def test_open_needs_issues(self) -> None:
        fourth = self.case.artifact_open("second survey", "jurisdiction-matrix")
        self.assertEqual("A17", fourth["id"])
        self.assertEqual([], fourth["cells"])
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        empty = CaseRecord(Path(tmp.name), "run-empty")
        with self.assertRaises(CaseError):
            empty.artifact_open("no issues", "jurisdiction-matrix")

    def test_add_cell_contract(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.add_cell(
                self.matrix, self.case.data, "readiness", "atlantis", "substantive"
            )
        with self.assertRaises(CaseError):
            artifacts.add_cell(
                self.matrix, self.case.data, "damages", "ca", "substantive"
            )
        with self.assertRaises(CaseError):
            artifacts.add_cell(
                self.matrix, self.case.data, "readiness", "ca", "treaty"
            )
        with self.assertRaises(CaseError):
            artifacts.add_cell(
                self.matrix, self.case.data, "readiness", "ca", "substantive"
            )
        cell = artifacts.add_cell(
            self.matrix, self.case.data, "migration", "ew", "procedure"
        )
        self.assertEqual("C4", cell["cell_id"])
        self.assertEqual("unreviewed", cell["review_status"])
        self.assertFalse(cell["retracted"])


class MatrixAuthorityTest(MatrixCase):
    def test_set_values(self) -> None:
        artifacts.set_cell(
            self.matrix, self.case.data, "C2",
            effective_date="2026-02-10",
            last_checked="2026-10-28",
            caveat="fixture authority",
        )
        cell = self.cells()["C2"]
        self.assertEqual("2026-02-10", cell["authority"]["effective_date"])
        self.assertEqual("2026-10-28", cell["authority"]["last_checked"])
        self.assertIn("fixture authority", cell["caveat"])
        self.assertTrue(any("effective_date" in h["detail"] for h in cell["history"]))

    def test_bad_dates_refused_not_stored(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.set_cell(
                self.matrix, self.case.data, "C2", effective_date="Feb 10"
            )
        with self.assertRaises(CaseError):
            artifacts.set_cell(
                self.matrix, self.case.data, "C2", last_checked="2026-13-01"
            )
        cell = self.cells()["C2"]
        self.assertEqual("", cell["authority"]["effective_date"])
        self.assertEqual("", cell["authority"]["last_checked"])

    def test_unknown_branch_never_passes(self) -> None:
        artifacts.set_cell(
            self.matrix, self.case.data, "C1", effective_date="2026-02-10"
        )
        artifacts.flag_cell(self.matrix, "C1", "confirmed", "checked the fixture")
        reasons = {
            b["cell_id"]: b["reasons"]
            for b in artifacts.jx_readiness(self.case.data, self.matrix)["blockers"]
        }
        self.assertEqual(["branch B1 is unknown"], reasons["C1"])

    def test_known_cell_resolves(self) -> None:
        artifacts.set_cell(
            self.matrix, self.case.data, "C2", effective_date="2026-02-10"
        )
        artifacts.flag_cell(self.matrix, "C2", "confirmed", "fixture reviewed")
        ready = artifacts.jx_readiness(self.case.data, self.matrix)
        self.assertEqual(1, ready["resolved"])
        self.assertTrue(ready["provisional"])
        self.assertNotIn("C2", {b["cell_id"] for b in ready["blockers"]})

    def test_cross_issue_link_refused(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.link_cell_branch(self.matrix, self.case.data, "C1", "B2")
        with self.assertRaises(CaseError):
            artifacts.link_cell_branch(self.matrix, self.case.data, "C3", "B9")
        self.assertEqual("B1", self.cells()["C1"]["branch_id"])


class MatrixStalenessTest(MatrixCase):
    def test_branch_change_marks_cell_stale(self) -> None:
        before = artifacts.cell_staleness(self.case.data, self.cells()["C1"])
        self.assertFalse(before["stale"])
        self.case.resolve_branch("B1", "known", "deed names the venue (fixture)")
        after = artifacts.cell_staleness(self.case.data, self.cells()["C1"])
        self.assertTrue(after["stale"])
        self.assertIn("the linked branch changed", after["reasons"])

    def test_matter_change_marks_cells_stale(self) -> None:
        self.case.note_context("purpose", "fixture re-scoped", "known", "fixture")
        for cell in self.matrix["cells"]:
            stale = artifacts.cell_staleness(self.case.data, cell)
            self.assertTrue(stale["stale"])
            self.assertIn("matter context changed", stale["reasons"])

    def test_source_version_change_marks_cells_stale(self) -> None:
        self.case.note_source(
            "DEMO-STATUS", "ca-leginfo", "Demo fixture citation", "fixture-v1"
        )
        stale = artifacts.cell_staleness(self.case.data, self.cells()["C2"])
        self.assertTrue(stale["stale"])
        self.assertIn("a noted source version changed", stale["reasons"])

    def test_resetting_values_refreshes_binding(self) -> None:
        self.case.note_context("purpose", "fixture re-scoped", "known", "fixture")
        self.assertTrue(
            artifacts.cell_staleness(self.case.data, self.cells()["C2"])["stale"]
        )
        artifacts.set_cell(
            self.matrix, self.case.data, "C2", caveat="rebound after rescope"
        )
        self.assertFalse(
            artifacts.cell_staleness(self.case.data, self.cells()["C2"])["stale"]
        )


class MatrixReviewTest(MatrixCase):
    def test_approval_binds_and_mutation_refuses(self) -> None:
        snapshot = artifacts.current_snapshot(self.matrix, self.case.data)
        artifacts.review_instance(
            self.matrix, self.case.data, "approved", "fixture review", [], snapshot
        )
        self.assertEqual("approved", self.matrix["review"]["decision"])
        artifacts.set_cell(self.matrix, self.case.data, "C2", caveat="late caveat")
        self.assertEqual("pending", self.matrix["review"]["decision"])
        with self.assertRaises(CaseError):
            artifacts.review_instance(
                self.matrix, self.case.data, "approved", "", [], snapshot
            )

    def test_flag_statuses(self) -> None:
        artifacts.flag_cell(self.matrix, "C1", "flagged", "branch unclear")
        self.assertEqual("flagged", self.cells()["C1"]["review_status"])
        with self.assertRaises(CaseError):
            artifacts.flag_cell(self.matrix, "C1", "maybe", "")

    def test_retract_needs_reason_and_leaves_readiness(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.retract_cell(self.matrix, "C3", "")
        artifacts.retract_cell(self.matrix, "C3", "ew has no testing branch (fixture)")
        self.assertTrue(self.cells()["C3"]["retracted"])
        ready = artifacts.jx_readiness(self.case.data, self.matrix)
        self.assertEqual(2, ready["total"])
        self.assertNotIn("C3", {b["cell_id"] for b in ready["blockers"]})
        report = artifacts.jx_export(self.matrix, self.case.data)
        by_id = {c["cell_id"]: c for c in report["cells"]}
        self.assertIn("retracted", by_id["C3"]["limitations"])

    def test_diff_reports_value_changes(self) -> None:
        old = len(self.matrix["versions"])
        artifacts.set_cell(
            self.matrix, self.case.data, "C1",
            proposition="Remedies track the acceptance clause (fixture)",
        )
        diff = artifacts.jx_diff(self.matrix, old, old + 1)
        change = next(
            c for c in diff["changes"]
            if c["cell_id"] == "C1" and c["change"] == "proposition"
        )
        self.assertIn("acceptance clause", change["after"])
        self.assertIn("acceptance clause", change["before"])
        with self.assertRaises(CaseError):
            artifacts.jx_diff(self.matrix, old, old + 9)

    def test_export_limits_and_history(self) -> None:
        report = artifacts.jx_export(self.matrix, self.case.data)
        self.assertFalse(report["historical"])
        self.assertTrue(report["readiness"]["provisional"])
        by_id = {c["cell_id"]: c for c in report["cells"]}
        self.assertIn("no linked branch", by_id["C3"]["limitations"])
        self.assertTrue(
            any("synthetic demonstration" in limit for limit in report["limits"])
        )
        self.assertTrue(
            any("not a holding" in limit for limit in report["limits"])
        )
        old = artifacts.jx_export(self.matrix, self.case.data, version=1)
        self.assertTrue(old["historical"])
        self.assertEqual([], old["cells"])
        self.assertEqual({}, old["readiness"])
        with self.assertRaises(CaseError):
            artifacts.jx_export(self.matrix, self.case.data, version=999)

    def test_three_templates_share_one_matter(self) -> None:
        audit = self.case.artifact_get("A1")
        board = self.case.artifact_get("A2")
        self.assertEqual(self.run_id, audit["matter"])
        self.assertEqual(self.run_id, board["matter"])
        self.assertEqual(self.run_id, self.matrix["matter"])
        # Eight recorded, one withdrawn when DEMO-DRAFT was flagged privileged.
        self.assertEqual(7, len(self.case.data["facts"]))


class MatrixCliTest(MatrixCase):
    def test_show_carries_readiness_branches_matter(self) -> None:
        cli = load_cli()
        cli.ROOT = self.root
        shown = cli.cmd_show(Namespace(run_id=self.run_id, instance="A3"))
        self.assertEqual(3, len(shown["instance"]["cells"]))
        self.assertTrue(shown["readiness"]["provisional"])
        self.assertEqual(2, len(shown["branches"]))
        self.assertEqual(2, len(shown["matter"]["fields"]))
        self.assertIn("ca", shown["regimes"])
        self.assertIn("snapshot", shown)

    def test_matrix_ops_roundtrip(self) -> None:
        cli = load_cli()
        cli.ROOT = self.root
        added = cli.cmd_matrix(
            Namespace(
                run_id=self.run_id, instance="A3", op="add",
                issue="migration", regime="ca", layer="substantive",
                cell="", proposition=None, citation=None, effective_date=None,
                last_checked=None, caveat=None, branch="", status="", note="",
                reason="",
            )
        )
        self.assertEqual("C4", added["cell"])
        edited = cli.cmd_matrix(
            Namespace(
                run_id=self.run_id, instance="A3", op="set", cell="C4",
                proposition="Cutover window terms (fixture)",
                citation="Fixture contract — not legal research",
                effective_date="2026-02-10", last_checked=None,
                caveat="fixture text",
                issue="", regime="", layer="", branch="", status="", note="",
                reason="",
            )
        )
        self.assertEqual("C4", edited["cell"])
        case = CaseRecord(self.root, self.run_id)
        cell = next(
            c for c in case.artifact_get("A3")["cells"] if c["cell_id"] == "C4"
        )
        self.assertEqual("2026-02-10", cell["authority"]["effective_date"])
        with self.assertRaises(CaseError):
            cli.cmd_matrix(
                Namespace(
                    run_id=self.run_id, instance="A1", op="add",
                    issue="migration", regime="ca", layer="substantive",
                    cell="", proposition=None, citation=None, effective_date=None,
                    last_checked=None, caveat=None, branch="", status="", note="",
                    reason="",
                )
            )


if __name__ == "__main__":
    unittest.main()
