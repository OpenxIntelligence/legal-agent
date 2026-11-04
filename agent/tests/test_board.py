"""Phase C: chronology & issue board over shared evidence.

Board mechanics over the synthetic demo matter: date-span
candidates, event confirmation, reviewer annotation, dispute
links, read-time staleness shared with the audit view, review,
diff, and redacted export. Deterministic throughout; the only
model surface reused is the audit's scripted judge for the
cross-view staleness proof.
"""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from agent_kit import artifacts, tools
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


class BoardCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.run_id = artifacts.build_demo(self.root)
        self.case = CaseRecord(self.root, self.run_id)
        self.board = self.case.artifact_get("A2")
        self.audit = self.case.artifact_get("A1")

    def events(self) -> dict:
        return {e["event_id"]: e for e in self.board["events"]}

    def save(self) -> None:
        self.case.artifact_save(self.board, "artifact_board", "")


class BoardOpenTest(BoardCase):
    def test_demo_seeds(self) -> None:
        events = self.events()
        self.assertEqual({"E1", "E2", "E3", "E4"}, set(events))
        self.assertEqual("February 10", events["E1"]["date"])
        self.assertEqual("A. Rivera", events["E2"]["owner"])
        self.assertTrue(events["E2"]["uncertainty"])
        links = {(l["a"], l["kind"], l["b"]) for l in self.board["links"]}
        self.assertIn(("E2", "depends_on", "E1"), links)
        self.assertIn(("E2", "disputed_order", "E4"), links)
        self.assertEqual(9, len(self.board["versions"]))

    def test_open_needs_facts_not_memo(self) -> None:
        self.case.data["memo"] = None
        third = self.case.artifact_open("board without memo", "case-chronology-board")
        self.assertEqual("A17", third["id"])
        self.assertEqual([], third["events"])
        with self.assertRaises(CaseError):
            self.case.artifact_open("audit without memo", "citation-claim-audit")
        with self.assertRaises(CaseError):
            self.case.artifact_open("nope", "no-such-template")

    def test_empty_record_has_no_board(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        case = CaseRecord(root, "run-empty")
        with self.assertRaises(CaseError):
            case.artifact_open("board", "case-chronology-board")


class CandidateTest(BoardCase):
    def test_demo_leaves_one_span_open(self) -> None:
        candidates = artifacts.board_candidates(self.case.data, self.board)
        self.assertEqual(
            [("F4", "February 18")],
            [(c["fact_id"], c["span"]) for c in candidates],
        )

    def test_fresh_board_sees_all_spans_but_never_withdrawn(self) -> None:
        fresh = self.case.artifact_open("fresh", "case-chronology-board")
        spans = {
            (c["fact_id"], c["span"])
            for c in artifacts.board_candidates(self.case.data, fresh)
        }
        self.assertEqual(
            {
                ("F4", "February 18"),
                ("F5", "February 10"),
                ("F6", "March 14-15"),
                ("F7", "March 15"),
                ("F8", "March 1"),
            },
            spans,
        )

    def test_dismiss_stops_suggesting(self) -> None:
        versions = len(self.board["versions"])
        artifacts.dismiss_candidate(self.board, "F4", "February 18")
        self.save()
        self.assertEqual([], artifacts.board_candidates(self.case.data, self.board))
        self.assertEqual(versions + 1, len(self.board["versions"]))

    def test_confirm_validates_and_links(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.confirm_event(
                self.board, self.case.data, "F99", "March 1", "", "x"
            )
        with self.assertRaises(CaseError):
            artifacts.confirm_event(
                self.board, self.case.data, "F2", "February 20", "", "x"
            )
        with self.assertRaises(CaseError):
            artifacts.confirm_event(
                self.board, self.case.data, "F4", "March 1", "", "x"
            )
        with self.assertRaises(CaseError):
            artifacts.confirm_event(
                self.board, self.case.data, "F4", "February 18", "", ""
            )
        event = artifacts.confirm_event(
            self.board, self.case.data,
            "F4", "February 18", "mid-February", "Subsystem tests passed",
        )
        self.assertEqual("E5", event["event_id"])
        self.assertEqual("reviewer", event["date_source"])
        self.assertEqual(["testing"], event["issue_ids"])
        self.assertEqual([], artifacts.board_candidates(self.case.data, self.board))


class EventTest(BoardCase):
    def test_add_needs_sources_or_uncertainty(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.add_event(self.board, self.case.data, "")
        with self.assertRaises(CaseError):
            artifacts.add_event(self.board, self.case.data, "vague")
        with self.assertRaises(CaseError):
            artifacts.add_event(
                self.board, self.case.data, "bad link", fact_ids=["F99"]
            )
        uncertain = artifacts.add_event(
            self.board, self.case.data, "Sign-off meeting",
            uncertainty="no record of when sign-off happened",
        )
        self.assertEqual([], uncertain["fact_ids"])
        self.assertEqual("", uncertain["date_source"])
        withdrawn = artifacts.add_event(
            self.board, self.case.data, "Draft claimed testing passed",
            date="February 20", fact_ids=["F2"],
        )
        self.assertEqual(["F2"], withdrawn["fact_ids"])

    def test_annotate_versions_only_on_change(self) -> None:
        versions = len(self.board["versions"])
        artifacts.annotate_event(self.board, "E1", owner="J. Doe")
        self.assertEqual("J. Doe", self.events()["E1"]["owner"])
        self.assertEqual(versions + 1, len(self.board["versions"]))
        artifacts.annotate_event(self.board, "E1", owner="J. Doe")
        self.assertEqual(versions + 1, len(self.board["versions"]))
        with self.assertRaises(CaseError):
            artifacts.annotate_event(self.board, "E99", owner="x")

    def test_link_rules(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.link_events(self.board, "E1", "E1", "depends_on", "x")
        with self.assertRaises(CaseError):
            artifacts.link_events(self.board, "E1", "E2", "blocks", "x")
        with self.assertRaises(CaseError):
            artifacts.link_events(self.board, "E1", "E3", "depends_on", "")
        with self.assertRaises(CaseError):
            artifacts.link_events(
                self.board, "E1", "E2", "depends_on", "again"
            )
        link = artifacts.link_events(
            self.board, "E4", "E1", "depends_on", "review needs migration facts"
        )
        self.assertEqual("reviewer", link["by"])


class StaleTest(BoardCase):
    def test_withdrawal_and_evidence_stale_events(self) -> None:
        self.case.flag_privileged("DEMO-MINUTES", "late restriction")
        stale = artifacts.event_staleness(self.case.data, self.events()["E1"])
        self.assertTrue(stale["stale"])
        self.assertIn("F5 was withdrawn", " ".join(stale["reasons"]))

    def test_new_evidence_stales_event(self) -> None:
        self.case.record_fact(
            "migration", "DEMO-STATUS", 1,
            "Overall status is yellow", "status yellow",
        )
        stale = artifacts.event_staleness(self.case.data, self.events()["E1"])
        self.assertTrue(stale["stale"])
        self.assertIn("evidence changed", " ".join(stale["reasons"]))

    def test_one_change_stales_both_views(self) -> None:
        fake = support.scripted(self.root, [])
        ctx = tools.Context(fake.client, self.root, self.run_id, self.case, True)
        rows = self.audit["rows"]
        structural = artifacts.audit_structural(self.root, self.case.data, rows)
        semantic = artifacts.audit_support(ctx, rows, structural)
        artifacts.record_checks(
            self.audit, self.case.data, self.root, structural, semantic
        )
        audit_row = next(r for r in rows if r["row_id"] == "R1")
        self.assertFalse(
            artifacts.row_staleness(self.root, self.case.data, audit_row)["structural"]
        )
        self.assertFalse(
            artifacts.event_staleness(self.case.data, self.events()["E2"])["stale"]
        )
        self.case.flag_privileged("DEMO-STATUS", "late restriction")
        self.assertTrue(
            artifacts.row_staleness(self.root, self.case.data, audit_row)["structural"]
        )
        stale = artifacts.event_staleness(self.case.data, self.events()["E2"])
        self.assertTrue(stale["stale"])
        self.assertIn("F7 was withdrawn", " ".join(stale["reasons"]))


class BoardReviewExportTest(BoardCase):
    def test_review_binds_board_snapshot(self) -> None:
        snapshot = artifacts.current_snapshot(self.board, self.case.data)
        review = artifacts.review_instance(
            self.board, self.case.data, "approved", "chronology holds",
            ["go-live still assumes board clearance"], snapshot,
        )
        self.assertEqual("approved", review["decision"])
        artifacts.annotate_event(self.board, "E1", owner="J. Doe")
        with self.assertRaises(CaseError):
            artifacts.review_instance(
                self.board, self.case.data, "approved", "", [], snapshot,
            )

    def test_diff_reports_event_and_link_changes(self) -> None:
        artifacts.annotate_event(self.board, "E1", owner="J. Doe")
        diff = artifacts.board_diff(self.board, 9, 10)
        self.assertTrue(
            any(
                c["event_id"] == "E1" and c["change"] == "owner"
                for c in diff["changes"]
            )
        )
        with self.assertRaises(CaseError):
            artifacts.board_diff(self.board, 1, 99)

    def test_export_redacts_and_limits(self) -> None:
        artifacts.add_event(
            self.board, self.case.data, "Draft claimed testing passed",
            date="February 20", fact_ids=["F2"],
        )
        report = artifacts.board_export(self.board, self.case.data, self.root)
        by_id = {e["event_id"]: e for e in report["events"]}
        draft = next(
            e for e in report["events"] if e["label"] == "Draft claimed testing passed"
        )
        self.assertEqual("[redacted: withdrawn]", draft["sources"][0]["quote"])
        self.assertTrue(
            any("stale" in limit for limit in draft["limitations"])
        )
        self.assertTrue(
            any("uncertain" in limit for limit in by_id["E2"]["limitations"])
        )
        self.assertNotIn("no open defects", json.dumps(report))
        old = artifacts.board_export(self.board, self.case.data, self.root, version=1)
        self.assertTrue(old["historical"])
        self.assertEqual([], old["events"])


class BoardCliTest(BoardCase):
    def test_show_carries_board_questions_tasks_next(self) -> None:
        cli = load_cli()
        cli.ROOT = self.root
        shown = cli.cmd_show(Namespace(run_id=self.run_id, instance="A2"))
        self.assertEqual(4, len(shown["instance"]["events"]))
        self.assertEqual(1, len(shown["candidates"]))
        questions = {q["id"]: q for q in shown["questions"]}
        self.assertEqual(3, questions["readiness"]["facts"])
        self.assertTrue(questions["testing"]["contested"])
        self.assertIn("task", shown["next"])
        self.assertIn("reason", shown["next"])
        self.assertIn("snapshot", shown)

    def test_board_ops_roundtrip(self) -> None:
        cli = load_cli()
        cli.ROOT = self.root
        confirmed = cli.cmd_board(
            Namespace(
                run_id=self.run_id, instance="A2", op="confirm",
                fact="F4", span="February 18", date="", label="Subsystem tests passed",
                issues="", facts="", uncertainty=None, owner=None,
                event="", with_="", kind="", reason="",
            )
        )
        self.assertEqual("E5", confirmed["event"])
        linked = cli.cmd_board(
            Namespace(
                run_id=self.run_id, instance="A2", op="link",
                event="E5", with_="E4", kind="depends_on", reason="tests feed review",
                fact="", span="", date=None, label=None, issues="", facts="",
                uncertainty=None, owner=None,
            )
        )
        self.assertEqual(["E5", "E4", "depends_on"], linked["linked"])
        with self.assertRaises(CaseError):
            cli.cmd_board(
                Namespace(
                    run_id=self.run_id, instance="A1", op="link",
                    event="E5", with_="E4", kind="depends_on", reason="x",
                    fact="", span="", date=None, label=None, issues="", facts="",
                    uncertainty=None, owner=None,
                )
            )


if __name__ == "__main__":
    unittest.main()
