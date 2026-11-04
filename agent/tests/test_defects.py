"""Defect ledger: synthesis, lifecycle, review binding, export.

Defects unify existing signals (row checks, contested flags, the
QC counterevidence module, memo coverage rules, gaps text) into
actionable records. Nothing here invents verdicts: unavailable
and not_run outcomes yield no defects, repairs need positive
current verdicts, and approval accepts (never overwrites) failed
checks.
"""

from __future__ import annotations

import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from agent_kit import artifacts
from agent_kit.case_record import CaseRecord
from tests import support

CLI_PATH = Path(__file__).resolve().parents[1] / "scripts" / "artifact.py"


def load_cli():
    import importlib.util

    spec = importlib.util.spec_from_file_location("artifact_cli_def", CLI_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DefectCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.run_id = artifacts.build_demo(self.root)
        self.case = CaseRecord(self.root, self.run_id)
        self.instance = self.case.artifact_get("A1")

    def ctx(self, client):
        from agent_kit import tools

        return tools.Context(client, self.root, self.run_id, self.case, True)

    def check_all(self, client) -> None:
        rows = self.instance["rows"]
        structural = artifacts.audit_structural(self.root, self.case.data, rows)
        semantic = artifacts.audit_support(self.ctx(client), rows, structural)
        artifacts.record_checks(
            self.instance, self.case.data, self.root, structural, semantic
        )
        self.case.artifact_save(self.instance, "artifact_checked", "")

    def defects_by_key(self) -> dict:
        return {d["key"]: d for d in self.instance.get("defects") or []}


class SynthesisTest(DefectCase):
    def test_failed_structural_row_opens_citation_defect(self) -> None:
        fake = support.scripted(self.root, [])
        self.check_all(fake.client)
        found = artifacts.synthesize_defects(
            self.instance, self.case.data, self.root
        )
        by_target = {(d["category"], d["target"]) for d in found}
        # Demo R2 cites the revoked draft: structurally failed.
        self.assertIn(("citation_failed", "R2"), by_target)
        defect = next(d for d in found if d["target"] == "R2"
                      and d["category"] == "citation_failed")
        self.assertEqual("high", defect["severity"])
        self.assertTrue(defect["rationale"])
        self.assertTrue(defect["action"])
        self.assertTrue(defect["sources"])

    def test_unavailable_and_not_run_yield_no_defects(self) -> None:
        rows = self.instance["rows"]
        structural = artifacts.audit_structural(self.root, self.case.data, rows)
        semantic = {
            row["row_id"]: {"outcome": "unavailable", "verdict": None,
                            "problems": ["judge down"], "support": []}
            for row in rows
        }
        artifacts.record_checks(
            self.instance, self.case.data, self.root, structural, semantic
        )
        found = artifacts.synthesize_defects(
            self.instance, self.case.data, self.root
        )
        kinds = {d["category"] for d in found}
        self.assertNotIn("unsupported_claim", kinds)
        self.assertNotIn("unsupported_conclusion", kinds)

    def test_failed_semantic_opens_support_defects(self) -> None:
        fake = support.scripted(
            self.root, [],
            unsupported=lambda text: [
                {"unit": "finding:R1", "reason": "overreach"},
                {"unit": "conclusion", "reason": "overreach"},
            ],
        )
        self.check_all(fake.client)
        found = artifacts.synthesize_defects(
            self.instance, self.case.data, self.root
        )
        by_target = {(d["category"], d["target"]) for d in found}
        self.assertIn(("unsupported_claim", "R1"), by_target)
        self.assertIn(("unsupported_conclusion", "RC"), by_target)

    def test_contested_item_opens_contradiction_defect(self) -> None:
        fake = support.scripted(self.root, [])
        self.check_all(fake.client)
        found = artifacts.synthesize_defects(
            self.instance, self.case.data, self.root
        )
        by_target = {(d["category"], d["target"]) for d in found}
        # Demo "testing" item has supporting + opposing recorded facts.
        self.assertIn(("contradictory_evidence", "R2"), by_target)
        defect = next(d for d in found
                      if d["category"] == "contradictory_evidence")
        self.assertNotIn("quote", " ".join(defect["evidence"]))
        self.assertTrue(
            any("document_id" in s for s in defect["sources"])
        )

    def test_qc_objection_opens_counterevidence_defect(self) -> None:
        fake = support.scripted(self.root, [])
        self.check_all(fake.client)
        found = artifacts.synthesize_defects(
            self.instance, self.case.data, self.root
        )
        matches = [d for d in found
                   if d["category"] == "uncited_counterevidence"]
        self.assertTrue(matches)
        self.assertEqual("R2", matches[0]["target"])
        self.assertIn("F", matches[0]["evidence"][0])

    def test_no_memo_yields_no_coverage_crash(self) -> None:
        data = {"checklist": {"a": {"question": "q", "evidence_needed": "e",
                                    "status": "open", "facts": []}},
                "facts": []}
        found = artifacts.synthesize_defects({"rows": []}, data, self.root)
        self.assertEqual([], [d for d in found
                              if d["category"] == "uncited_counterevidence"])

    def test_conclusion_inherits_finding_defects_silently(self) -> None:
        fake = support.scripted(self.root, [])
        self.check_all(fake.client)
        found = artifacts.synthesize_defects(
            self.instance, self.case.data, self.root
        )
        # Demo RC is structurally failed only via inherited citations.
        self.assertNotIn(
            ("citation_failed", "RC"),
            {(d["category"], d["target"]) for d in found},
        )

    def test_conclusion_with_unique_bad_citation_gets_defect(self) -> None:
        rows = [
            {"row_id": "R1", "kind": "finding", "item": "x",
             "assertion": "a", "assertion_version": 1,
             "citations": [{"document_id": "D", "page": 1, "quote": "q"}],
             "checks": {"structural": {"outcome": "passed", "citations": []},
                        "semantic": {"outcome": "passed"}}},
            {"row_id": "RC", "kind": "conclusion", "item": "",
             "assertion": "c", "assertion_version": 1,
             "citations": [{"document_id": "D", "page": 1, "quote": "q"},
                           {"document_id": "X", "page": 9, "quote": "zzz"}],
             "checks": {"structural": {
                 "outcome": "failed",
                 "citations": [
                     {"document_id": "D", "page": 1, "quote": "q", "problems": []},
                     {"document_id": "X", "page": 9, "quote": "zzz",
                      "problems": ["X is not in the inbox"]},
                 ]},
                 "semantic": {"outcome": "not_run"}}},
        ]
        found = artifacts.synthesize_defects(
            {"rows": rows}, {"checklist": {}, "facts": []}, self.root
        )
        self.assertIn(
            ("citation_failed", "RC"),
            {(d["category"], d["target"]) for d in found},
        )

    def test_incomplete_structural_opens_low_defect(self) -> None:
        row = {"row_id": "R1", "kind": "finding", "item": "x",
               "assertion": "a", "assertion_version": 1, "citations": [],
               "checks": {"structural": {"outcome": "incomplete",
                                         "problems": ["no extraction"]},
                          "semantic": {"outcome": "not_run"}}}
        found = artifacts.synthesize_defects(
            {"rows": [row]}, {"checklist": {}, "facts": []}, self.root
        )
        self.assertEqual(1, len(found))
        self.assertEqual("citation_incomplete", found[0]["category"])
        self.assertEqual("low", found[0]["severity"])


class CoverageDefectTest(unittest.TestCase):
    def test_uncovered_issue_and_unstated_gaps(self) -> None:
        from eval import quality_cases

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        run_id, record, instance = quality_cases.materialize(
            root, "quality-coverage"
        )
        fake = support.scripted(root, [])
        from agent_kit import tools

        ctx = tools.Context(fake.client, root, run_id, record, True)
        rows = instance["rows"]
        structural = artifacts.audit_structural(root, record.data, rows)
        semantic = artifacts.audit_support(ctx, rows, structural)
        artifacts.record_checks(instance, record.data, root, structural, semantic)
        found = artifacts.synthesize_defects(instance, record.data, root)
        by_target = {(d["category"], d["target"]) for d in found}
        self.assertIn(("uncovered_issue", "qv_access"), by_target)
        self.assertIn(("unstated_open_issues", "RG"), by_target)
        self.assertEqual(
            "high",
            next(d for d in found if d["category"] == "uncovered_issue")["severity"],
        )


class ConclusionInheritanceTest(DefectCase):
    def rows(self) -> dict:
        return {r["row_id"]: r for r in self.instance["rows"]}

    def test_finding_revise_refreshes_conclusion_union(self) -> None:
        before = [dict(c) for c in self.rows()["RC"]["citations"]]
        artifacts.revise_row(
            self.instance, self.case.data, self.root, "R1",
            assertion="Four Severity 1 defects were open at period end, revised.",
        )
        # Assertion-only revisions leave citations alone.
        self.assertEqual(before, self.rows()["RC"]["citations"])
        artifacts.revise_row(
            self.instance, self.case.data, self.root, "R5",
            citation={"document_id": "DEMO-STATUS", "page": 1,
                      "quote": "Open Severity 1 defects: 4 at period end"},
            index=0,
        )
        rc = self.rows()["RC"]
        self.assertIn(
            ("DEMO-STATUS", 1, "Open Severity 1 defects: 4 at period end"),
            [(c["document_id"], c["page"], c["quote"]) for c in rc["citations"]],
        )
        self.assertTrue(
            any(h["change"] == "inherited-citations" for h in rc["history"])
        )
        # Untouched entries keep their pins: change detection survives.
        by_quote = {c["quote"]: c for c in rc["citations"]}
        old = {c["quote"]: c for c in before}
        for quote, citation in by_quote.items():
            if quote in old:
                self.assertEqual(old[quote]["pin_sha"], citation["pin_sha"])

    def test_reviewer_edited_conclusion_citation_survives(self) -> None:
        artifacts.revise_row(
            self.instance, self.case.data, self.root, "RC",
            citation={"document_id": "DEMO-STATUS", "page": 1,
                      "quote": "reviewer-owned passage"},
            index=0,
        )
        artifacts.revise_row(
            self.instance, self.case.data, self.root, "R1",
            citation={"document_id": "DEMO-STATUS", "page": 1,
                      "quote": "Go-live is scheduled for March 15"},
            index=0,
        )
        quotes = [c["quote"] for c in self.rows()["RC"]["citations"]]
        self.assertIn("reviewer-owned passage", quotes)
        self.assertIn("Go-live is scheduled for March 15", quotes)


class LifecycleTest(DefectCase):
    def refresh(self) -> list:
        events = artifacts.refresh_defects(
            self.instance, self.case.data, self.root
        )
        self.case.artifact_save(self.instance, "artifact_checked", "")
        return events

    def test_refresh_opens_with_stable_ids_and_is_idempotent(self) -> None:
        fake = support.scripted(self.root, [])
        self.check_all(fake.client)
        first = self.refresh()
        self.assertTrue(first)
        ids = [d["id"] for d in self.instance["defects"]]
        self.assertEqual(sorted(ids), ids)
        self.assertEqual(len(set(ids)), len(ids))
        history_len = len(self.instance["history"])
        second = self.refresh()
        self.assertEqual([], second)
        self.assertEqual(history_len, len(self.instance["history"]))
        self.assertEqual(ids, [d["id"] for d in self.instance["defects"]])

    def test_repair_regression_and_unresolved(self) -> None:
        fake = support.scripted(self.root, [])
        self.check_all(fake.client)
        self.refresh()
        key = "citation_failed:R2"
        self.assertEqual("open", self.defects_by_key()[key]["status"])
        # Repair: point R2 at a live source with an exact quote.
        artifacts.revise_row(
            self.instance, self.case.data, self.root, "R2",
            citation={"document_id": "DEMO-STATUS", "page": 1,
                      "quote": "Open Severity 1 defects: 4 at period end"},
            index=0,
        )
        self.check_all(fake.client)
        self.refresh()
        repaired = self.defects_by_key()[key]
        self.assertEqual("repaired", repaired["status"])
        # Regression: break it again.
        artifacts.revise_row(
            self.instance, self.case.data, self.root, "R2",
            citation={"document_id": "DOC-VANISHED", "page": 1,
                      "quote": "gone"},
            index=0,
        )
        self.check_all(fake.client)
        events = self.refresh()
        reopened = self.defects_by_key()[key]
        self.assertEqual("open", reopened["status"])
        self.assertTrue(any("regressed" in e for e in events))
        self.assertTrue(
            any(h["change"] == "regressed" for h in reopened["history"])
        )
        # Unavailable after failure is unresolved, never repaired.
        rows = self.instance["rows"]
        structural = artifacts.audit_structural(self.root, self.case.data, rows)
        semantic = {
            row["row_id"]: {"outcome": "unavailable", "verdict": None,
                            "problems": ["judge down"], "support": []}
            for row in rows
        }
        artifacts.record_checks(
            self.instance, self.case.data, self.root, structural, semantic
        )
        self.refresh()
        # citation_failed:R2 persists (structural still failed); force a
        # semantic defect unresolved instead via a failed-then-unavailable row.
        self.assertEqual("open", self.defects_by_key()[key]["status"])

    def test_ids_are_never_recycled(self) -> None:
        fake = support.scripted(self.root, [])
        self.check_all(fake.client)
        self.refresh()
        before = [d["id"] for d in self.instance["defects"]]
        self.assertIn("D1", before)
        artifacts.revise_row(
            self.instance, self.case.data, self.root, "R2",
            citation={"document_id": "DEMO-STATUS", "page": 1,
                      "quote": "Open Severity 1 defects: 4 at period end"},
            index=0,
        )
        self.check_all(fake.client)
        self.refresh()
        after = [d["id"] for d in self.instance["defects"]]
        self.assertTrue(all(i in after for i in before))
        self.assertEqual(len(set(after)), len(after))

    def test_stale_pass_cannot_repair(self) -> None:
        fake = support.scripted(self.root, [])
        self.check_all(fake.client)
        self.refresh()
        key = "citation_failed:R2"
        self.assertEqual("open", self.defects_by_key()[key]["status"])
        # A pass recorded without refresh, then invalidated by a
        # source change: the ledger must not call that a repair.
        artifacts.revise_row(
            self.instance, self.case.data, self.root, "R2",
            citation={"document_id": "DEMO-STATUS", "page": 1,
                      "quote": "Open Severity 1 defects: 4 at period end"},
            index=0,
        )
        self.check_all(fake.client)
        inbox = self.root / "workspace/inbox/DEMO-STATUS.pdf"
        inbox.write_bytes(inbox.read_bytes() + b"\nappended after check")
        events = self.refresh()
        defect = self.defects_by_key()[key]
        self.assertEqual("unresolved", defect["status"])
        self.assertTrue(any("unresolved" in e for e in events))

    def test_contradiction_never_auto_repairs(self) -> None:
        fake = support.scripted(self.root, [])
        self.check_all(fake.client)
        self.refresh()
        key = "contradictory_evidence:R2"
        self.assertIn(key, self.defects_by_key())
        # Withdrawing the opposing fact ends the condition, but that is
        # evidence change — not a repair.
        opposing = next(
            f for f in self.case.data["facts"]
            if f.get("stance") == "opposes"
        )
        self.case.data["facts"] = [
            f for f in self.case.data["facts"] if f is not opposing
        ]
        events = self.refresh()
        defect = self.defects_by_key()[key]
        self.assertEqual("unresolved", defect["status"])
        self.assertTrue(any("unresolved" in e for e in events))


class ReviewDispositionTest(DefectCase):
    def test_approval_accepts_defects_without_clearing_checks(self) -> None:
        fake = support.scripted(self.root, [])
        self.check_all(fake.client)
        artifacts.refresh_defects(self.instance, self.case.data, self.root)
        self.case.artifact_save(self.instance, "artifact_checked", "")
        snapshot = artifacts.current_snapshot(self.instance, self.case.data)
        self.assertIn("defects", snapshot)
        review = artifacts.review_instance(
            self.instance, self.case.data, "approved", "ok",
            ["demo risk accepted"], snapshot,
        )
        self.assertEqual("approved", review["decision"])
        for defect in self.instance["defects"]:
            with self.subTest(defect=defect["id"]):
                self.assertEqual("accepted", defect["status"])
                self.assertEqual("accepted", defect["disposition"]["decision"])
                self.assertIn("demo risk accepted",
                              defect["disposition"]["qualifications"])
        # Failed checks still failed: approval overwrites nothing.
        rows = {r["row_id"]: r for r in self.instance["rows"]}
        self.assertEqual(
            "failed", rows["R2"]["checks"]["structural"]["outcome"]
        )

    def test_revise_reopens_accepted_defects(self) -> None:
        fake = support.scripted(self.root, [])
        self.check_all(fake.client)
        artifacts.refresh_defects(self.instance, self.case.data, self.root)
        snapshot = artifacts.current_snapshot(self.instance, self.case.data)
        artifacts.review_instance(
            self.instance, self.case.data, "approved", "", [], snapshot
        )
        artifacts.revise_row(
            self.instance, self.case.data, self.root, "R1",
            assertion="Four Severity 1 defects were open, revised.",
        )
        for defect in self.instance["defects"]:
            with self.subTest(defect=defect["id"]):
                self.assertEqual("open", defect["status"])
                self.assertIsNone(defect["disposition"])

    def test_new_defect_invalidates_inspected_snapshot(self) -> None:
        fake = support.scripted(self.root, [])
        self.check_all(fake.client)
        snapshot = artifacts.current_snapshot(self.instance, self.case.data)
        artifacts.refresh_defects(self.instance, self.case.data, self.root)
        from agent_kit.case_record import CaseError

        with self.assertRaises(CaseError):
            artifacts.review_instance(
                self.instance, self.case.data, "approved", "", [], snapshot
            )


class DefectExportTest(DefectCase):
    def test_export_and_markdown_carry_defects(self) -> None:
        fake = support.scripted(self.root, [])
        self.check_all(fake.client)
        artifacts.refresh_defects(self.instance, self.case.data, self.root)
        self.case.artifact_save(self.instance, "artifact_checked", "")
        report = artifacts.export_instance(
            self.instance, self.case.data, self.root
        )
        self.assertTrue(report["defects"])
        self.assertTrue(all("key" not in d for d in report["defects"]))
        text = artifacts.export_markdown(report)
        self.assertIn("## Defects", text)
        self.assertIn("citation\\_failed", text)

    def test_historical_export_omits_live_ledger(self) -> None:
        fake = support.scripted(self.root, [])
        self.check_all(fake.client)
        artifacts.refresh_defects(self.instance, self.case.data, self.root)
        artifacts.revise_row(
            self.instance, self.case.data, self.root, "R1",
            assertion="Four Severity 1 defects were open, revised.",
        )
        self.case.artifact_save(self.instance, "artifact_revised", "")
        report = artifacts.export_instance(
            self.instance, self.case.data, self.root, version=1
        )
        self.assertEqual([], report["defects"])
        self.assertTrue(
            any("ledger is current-state only" in limit
                for limit in report["limits"])
        )

    def test_markdown_escapes_hostile_defect_evidence(self) -> None:
        artifacts.revise_row(
            self.instance, self.case.data, self.root, "R1",
            citation={"document_id": "XSS](javascript:x)", "page": 1,
                      "quote": "whatever words here"},
            index=0,
        )
        fake = support.scripted(self.root, [])
        self.check_all(fake.client)
        artifacts.refresh_defects(self.instance, self.case.data, self.root)
        report = artifacts.export_instance(
            self.instance, self.case.data, self.root
        )
        text = artifacts.export_markdown(report)
        # Code spans render literally, so the raw id is safe there;
        # rationale/evidence prose must carry the escaped form.
        self.assertIn("`XSS](javascript:x)`", text)
        self.assertIn("XSS\\]\\(javascript:x\\)", text)
        self.assertNotIn("[XSS](javascript:x)", text)

    def test_legacy_instance_without_ledger_exports_cleanly(self) -> None:
        report = artifacts.export_instance(
            self.instance, self.case.data, self.root
        )
        self.assertEqual([], report["defects"])
        text = artifacts.export_markdown(report)
        self.assertIn("none open or recorded", text)


class DefectPanelStaticTest(unittest.TestCase):
    def test_audit_workspace_renders_defects(self) -> None:
        path = Path(__file__).resolve().parents[2] / "ui" / "index.html"
        text = path.read_text()
        self.assertIn("function defectsHtml(inst)", text)
        self.assertIn("${defectsHtml(inst)}", text)
        self.assertIn("<h2>Defects</h2>", text)
        self.assertIn("defectSeverityBadge", text)
        self.assertIn("onclick=\"selectRow(", text)
        self.assertIn("No defects recorded.", text)


class DefectCliTest(DefectCase):
    def test_check_reports_defect_transitions(self) -> None:
        cli = load_cli()
        cli.ROOT = self.root
        result = cli.cmd_check(
            Namespace(run_id=self.run_id, instance="A1", rows="")
        )
        self.assertIn("R1", result["checked"])
        self.assertTrue(result["defects"])
        fresh = CaseRecord(self.root, self.run_id)
        self.assertTrue(fresh.artifact_get("A1").get("defects"))
