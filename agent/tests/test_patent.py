"""Phase 3: patent claim comparison.

Reviewer-logged subjects and claim elements over
the synthetic demo matter: per-subject readings,
completeness-gated mapping verdicts with unclear
as a first-class verdict, subject pin staleness,
review, diff, and versioned export. A missing
reading never counts as mapped. Fully
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


class PatentCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.run_id = artifacts.build_demo(self.root)
        self.case = CaseRecord(self.root, self.run_id)
        self.chart = self.case.artifact_get("A14")

    def subjects(self) -> dict:
        return {s["sj_id"]: s for s in self.chart["subjects"]}

    def elements(self) -> dict:
        return {e["el_id"]: e for e in self.chart["elements"]}

    def save(self) -> None:
        self.case.artifact_save(self.chart, "artifact_patent", "")


class PatentOpenTest(PatentCase):
    def test_demo_seeds(self) -> None:
        subjects = self.subjects()
        self.assertEqual({"SJ1", "SJ2"}, set(subjects))
        self.assertEqual("patent-claim", subjects["SJ1"]["kind"])
        self.assertEqual("product", subjects["SJ2"]["kind"])
        elements = self.elements()
        self.assertEqual({"EL1", "EL2", "EL3"}, set(elements))
        self.assertEqual("mapped", elements["EL1"]["status"])
        self.assertEqual(2, len(elements["EL1"]["readings"]))
        self.assertEqual("not-mapped", elements["EL2"]["status"])
        self.assertEqual("open", elements["EL3"]["status"])
        self.assertEqual(1, len(elements["EL3"]["readings"]))
        self.assertEqual(
            ["SJ2"], artifacts.el_missing(self.chart, elements["EL3"])
        )

    def test_fourteen_templates_share_one_matter(self) -> None:
        for instance_id in (
            "A1", "A2", "A3", "A4", "A5", "A6", "A7",
            "A8", "A9", "A10", "A11", "A12", "A13", "A14",
        ):
            self.assertEqual(
                self.run_id, self.case.artifact_get(instance_id)["matter"]
            )

    def test_open_needs_no_prerequisites(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        empty = CaseRecord(Path(tmp.name), "run-empty")
        fresh = empty.artifact_open("fresh chart", "patent-claims")
        self.assertEqual([], fresh["subjects"])
        self.assertEqual([], fresh["elements"])
        second = self.case.artifact_open("second chart", "patent-claims")
        self.assertEqual("A17", second["id"])


class SubjectTest(PatentCase):
    def test_add_sj_validates(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.add_sj(self.chart, "  ", "product")
        with self.assertRaises(CaseError):
            artifacts.add_sj(self.chart, "Widget Z", "verdict")
        subject = artifacts.add_sj(
            self.chart, "Paper P (fixture)", "prior-art", "PP-1"
        )
        self.assertEqual("SJ3", subject["sj_id"])

    def test_set_sj_revalidates(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.set_sj(self.chart, "SJ1", label="")
        with self.assertRaises(CaseError):
            artifacts.set_sj(self.chart, "SJ1", kind="verdict")
        with self.assertRaises(CaseError):
            artifacts.set_sj(self.chart, "SJ99", label="x")
        subject = artifacts.set_sj(self.chart, "SJ2", ref="Widget Y rev 2")
        self.assertEqual("Widget Y rev 2", subject["ref"])


class ElementTest(PatentCase):
    def test_add_el_validates(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.add_el(self.chart, " ", "text")
        with self.assertRaises(CaseError):
            artifacts.add_el(self.chart, "claim 1, element [d]", " ")
        element = artifacts.add_el(
            self.chart, "claim 1, element [d]", "A housing (fixture)."
        )
        self.assertEqual("EL4", element["el_id"])
        self.assertEqual("open", element["status"])

    def test_set_el_locks_decided_elements(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.set_el(self.chart, "EL1", text="Changed")
        artifacts.decide_el(self.chart, "EL1", "open")
        element = artifacts.set_el(self.chart, "EL1", text="Module v2")
        self.assertEqual("Module v2", element["text"])
        with self.assertRaises(CaseError):
            artifacts.set_el(self.chart, "EL3", claim_ref=" ")


class ReadingTest(PatentCase):
    def test_reading_validates(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.reading_el(self.chart, "EL3", "SJ99", "text")
        with self.assertRaises(CaseError):
            artifacts.reading_el(self.chart, "EL3", "SJ2", "  ")
        with self.assertRaises(CaseError):
            artifacts.reading_el(self.chart, "EL99", "SJ2", "text")
        with self.assertRaises(CaseError):
            artifacts.reading_el(self.chart, "EL1", "SJ1", "locked")

    def test_reading_records_and_supersedes(self) -> None:
        element = artifacts.reading_el(
            self.chart, "EL3", "SJ2", "Channel unclear (fixture)."
        )
        self.assertEqual([], artifacts.el_missing(self.chart, element))
        element = artifacts.reading_el(
            self.chart, "EL3", "SJ2", "Channel present (fixture)."
        )
        texts = [r["text"] for r in element["readings"] if r["sj_id"] == "SJ2"]
        self.assertEqual(["Channel present (fixture)."], texts)


class PatentLifecycleTest(PatentCase):
    def test_verdicts_need_reasons_and_complete_readings(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.decide_el(self.chart, "EL3", "mapped")
        with self.assertRaises(CaseError):
            artifacts.decide_el(self.chart, "EL3", "mapped", "too soon")
        artifacts.reading_el(
            self.chart, "EL3", "SJ2", "Channel unclear (fixture)."
        )
        element = artifacts.decide_el(self.chart, "EL3", "unclear", "ambiguous")
        self.assertEqual("unclear", element["status"])
        element = artifacts.decide_el(self.chart, "EL3", "open")
        self.assertEqual("open", element["status"])
        self.assertEqual("", element["resolution"])

    def test_unclear_is_a_verdict_not_a_skip(self) -> None:
        artifacts.reading_el(
            self.chart, "EL3", "SJ2", "Channel unclear (fixture)."
        )
        element = artifacts.decide_el(
            self.chart, "EL3", "unclear", "needs construction (fixture)"
        )
        self.assertEqual("unclear", element["status"])
        with self.assertRaises(CaseError):
            artifacts.reading_el(self.chart, "EL3", "SJ2", "locked")

    def test_bad_transitions_refused(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.decide_el(self.chart, "EL1", "not-mapped", "skip")
        with self.assertRaises(CaseError):
            artifacts.decide_el(self.chart, "EL2", "mapped", "skip")
        with self.assertRaises(CaseError):
            artifacts.decide_el(self.chart, "EL3", "mapped", "skip")

    def test_new_subject_reopens_completeness(self) -> None:
        artifacts.add_sj(self.chart, "Paper P (fixture)", "prior-art")
        self.assertEqual(
            ["SJ3"],
            artifacts.el_missing(self.chart, self.elements()["EL1"]),
        )


class PatentStaleTest(PatentCase):
    def test_subject_revision_stales(self) -> None:
        artifacts.set_sj(self.chart, "SJ1", ref="US-0000002")
        for el_id in ("EL1", "EL2", "EL3"):
            status = artifacts.el_status(
                self.chart, self.elements()[el_id]
            )
            self.assertEqual("stale", status["state"])
            self.assertIn("SJ1", status["reasons"][0])
        summary = artifacts.pt_summary(self.chart)
        self.assertEqual(3, len(summary["stale"]))

    def test_rereading_clears_staleness(self) -> None:
        artifacts.set_sj(self.chart, "SJ1", ref="US-0000002")
        artifacts.decide_el(self.chart, "EL1", "open")
        element = artifacts.reading_el(
            self.chart, "EL1", "SJ1", "Element [a] as claimed (fixture)."
        )
        self.assertEqual(
            "current", artifacts.el_status(self.chart, element)["state"]
        )
        self.assertEqual(
            "stale",
            artifacts.el_status(self.chart, self.elements()["EL2"])["state"],
        )

    def test_missing_subject_orphans(self) -> None:
        status = artifacts.el_status(
            self.chart,
            {"el_id": "ELX", "readings": [{"sj_id": "SJ99"}]},
        )
        self.assertEqual("orphaned", status["state"])

    def test_missing_readings_limit_but_never_stale(self) -> None:
        status = artifacts.el_status(
            self.chart, self.elements()["EL3"]
        )
        self.assertEqual("current", status["state"])
        summary = artifacts.pt_summary(self.chart)
        self.assertEqual([], summary["stale"])
        self.assertEqual(["EL3"], [i["el_id"] for i in summary["incomplete"]])
        self.assertEqual(
            {"mapped": 1, "not-mapped": 1, "open": 1}, summary["counts"]
        )
        self.assertEqual(["EL3"], [q["el_id"] for q in summary["queue"]])


class PatentReviewTest(PatentCase):
    def test_approval_binds_and_mutation_refuses(self) -> None:
        snapshot = artifacts.current_snapshot(self.chart, self.case.data)
        artifacts.review_instance(
            self.chart, self.case.data, "approved", "fixture review",
            [], snapshot,
        )
        self.assertEqual("approved", self.chart["review"]["decision"])
        artifacts.set_el(self.chart, "EL3", text="Channel v2")
        self.assertEqual("pending", self.chart["review"]["decision"])
        with self.assertRaises(CaseError):
            artifacts.review_instance(
                self.chart, self.case.data, "approved", "", [], snapshot
            )

    def test_diff_reports_subject_element_and_reading_changes(self) -> None:
        old = len(self.chart["versions"])
        artifacts.set_sj(self.chart, "SJ2", ref="Widget Y rev 2")
        artifacts.reading_el(
            self.chart, "EL3", "SJ2", "Channel unclear (fixture)."
        )
        artifacts.decide_el(self.chart, "EL3", "unclear", "ambiguous")
        diff = artifacts.pt_diff(self.chart, old, old + 3)
        renamed = next(
            c for c in diff["changes"]
            if c.get("sj_id") == "SJ2" and c["change"] == "ref"
        )
        self.assertEqual("Accused product (fixture)", renamed["before"])
        self.assertEqual("Widget Y rev 2", renamed["after"])
        moved = next(
            c for c in diff["changes"]
            if c.get("el_id") == "EL3" and c["change"] == "status"
        )
        self.assertEqual("open", moved["before"])
        self.assertEqual("unclear", moved["after"])
        read = next(
            c for c in diff["changes"]
            if c.get("el_id") == "EL3" and c["change"] == "readings"
        )
        self.assertEqual(2, len(read["after"]))
        with self.assertRaises(CaseError):
            artifacts.pt_diff(self.chart, old, old + 9)

    def test_export_limits_and_history(self) -> None:
        report = artifacts.pt_export(self.chart, self.case.data)
        self.assertFalse(report["historical"])
        self.assertEqual(1, len(report["summary"]["queue"]))
        self.assertEqual(2, len(report["subjects"]))
        by_id = {e["el_id"]: e for e in report["elements"]}
        self.assertIn("missing readings from: SJ2", by_id["EL3"]["limitations"])
        self.assertNotIn(
            "missing readings from: SJ2", by_id["EL1"]["limitations"]
        )
        self.assertTrue(
            any("never counts as mapped" in line for line in report["limits"])
        )
        old = artifacts.pt_export(self.chart, self.case.data, version=1)
        self.assertTrue(old["historical"])
        self.assertEqual([], old["subjects"])
        self.assertEqual([], old["elements"])
        self.assertEqual({}, old["summary"])
        with self.assertRaises(CaseError):
            artifacts.pt_export(self.chart, self.case.data, version=999)


class PatentCliTest(PatentCase):
    def test_show_carries_summary_and_missing(self) -> None:
        cli = load_cli()
        cli.ROOT = self.root
        shown = cli.cmd_show(Namespace(run_id=self.run_id, instance="A14"))
        self.assertEqual(3, len(shown["instance"]["elements"]))
        self.assertEqual(2, len(shown["instance"]["subjects"]))
        self.assertEqual(1, len(shown["summary"]["queue"]))
        self.assertEqual(["SJ2"], shown["instance"]["elements"][2]["missing"])
        self.assertIn("snapshot", shown)

    def test_patent_ops_roundtrip(self) -> None:
        cli = load_cli()
        cli.ROOT = self.root
        added_subject = cli.cmd_patent(
            Namespace(
                run_id=self.run_id, instance="A14", op="add-subject",
                label="Paper P (fixture)", kind="prior-art", ref="PP-1",
                notes="", subject=None, element="",
                claim_ref=None, text=None, reading="", status="", reason="",
            )
        )
        self.assertEqual("SJ3", added_subject["saved"])
        added = cli.cmd_patent(
            Namespace(
                run_id=self.run_id, instance="A14", op="add",
                claim_ref="claim 1, element [d]", text="A housing (fixture).",
                subject=None, element="", label=None, kind=None, ref=None,
                notes=None, reading="", status="", reason="",
            )
        )
        self.assertEqual("EL4", added["saved"])
        for sj_id in ("SJ1", "SJ2", "SJ3"):
            read = cli.cmd_patent(
                Namespace(
                    run_id=self.run_id, instance="A14", op="reading",
                    element="EL4", subject=sj_id,
                    reading=f"{sj_id} reading (fixture).",
                    label=None, kind=None, ref=None, notes=None,
                    claim_ref=None, text=None, status="", reason="",
                )
            )
            self.assertEqual("EL4", read["saved"])
        decided = cli.cmd_patent(
            Namespace(
                run_id=self.run_id, instance="A14", op="decide", element="EL4",
                status="unclear", reason="ambiguous (fixture)",
                subject=None, label=None, kind=None, ref=None, notes=None,
                claim_ref=None, text=None, reading="",
            )
        )
        self.assertEqual("EL4", decided["saved"])
        case = CaseRecord(self.root, self.run_id)
        element = next(
            e for e in case.artifact_get("A14")["elements"]
            if e["el_id"] == "EL4"
        )
        self.assertEqual("unclear", element["status"])
        with self.assertRaises(CaseError):
            cli.cmd_patent(
                Namespace(
                    run_id=self.run_id, instance="A1", op="add",
                    claim_ref="x", text="y", subject=None, element="",
                    label=None, kind=None, ref=None, notes=None,
                    reading="", status="", reason="",
                )
            )

    def test_cli_export_covers_patent(self) -> None:
        cli = load_cli()
        cli.ROOT = self.root
        report = cli.cmd_export(
            Namespace(run_id=self.run_id, instance="A14", version=None)
        )
        self.assertEqual(3, len(report["elements"]))
        self.assertEqual(2, len(report["subjects"]))


if __name__ == "__main__":
    unittest.main()
