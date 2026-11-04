"""Phase B: artifact contracts and the Citation & Claim Audit slice.

Backend mechanics over synthetic demo matters in temporary
workspaces: template/instance contracts, deterministic citation
checks, semantic support through the existing claims verifier,
revision/versioning, snapshot-bound review, staleness, redacted
export, and gallery honesty. Judge behavior is scripted; nothing
here measures live model quality.
"""

from __future__ import annotations

import importlib.util
import json
import os
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


class ArtifactCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.run_id = artifacts.build_demo(self.root)
        self.case = CaseRecord(self.root, self.run_id)
        self.instance = self.case.artifact_get("A1")

    def ctx(self, client) -> tools.Context:
        return tools.Context(client, self.root, self.run_id, self.case, True)

    def rows(self) -> dict:
        return {row["row_id"]: row for row in self.instance["rows"]}

    def check_all(self, client) -> list[str]:
        rows = self.instance["rows"]
        structural = artifacts.audit_structural(self.root, self.case.data, rows)
        semantic = artifacts.audit_support(
            self.ctx(client), rows, structural
        )
        changed = artifacts.record_checks(
            self.instance, self.case.data, self.root, structural, semantic
        )
        self.case.artifact_save(self.instance, "artifact_checked", "")
        return changed


class OpenTest(ArtifactCase):
    def test_open_builds_rows_from_memo(self) -> None:
        rows = self.rows()
        self.assertEqual(
            {"R1", "R2", "R3", "R4", "R5", "R6", "R7", "RC", "RG"}, set(rows)
        )
        self.assertEqual("conclusion", rows["RC"]["kind"])
        self.assertEqual(7, len(rows["RC"]["citations"]))
        self.assertEqual([], rows["RG"]["citations"])
        self.assertEqual("citation-claim-audit", self.instance["template"])
        self.assertEqual("1.0.0", self.instance["template_version"])
        self.assertEqual(self.run_id, self.instance["matter"])
        self.assertEqual(1, len(self.instance["versions"]))
        self.assertTrue(all(
            citation.get("pin_sha")
            for row in self.instance["rows"]
            for citation in row["citations"]
            if citation.get("document_id") != "DOC-VANISHED"
        ))

    def test_open_refuses_memo_without_citations(self) -> None:
        self.case.file_memo(
            {
                "conclusion": "c",
                "findings": [{"item": "x", "summary": "s", "citations": []}],
                "gaps": "",
            },
            "unverified",
        )
        with self.assertRaises(CaseError):
            self.case.artifact_open("empty")

    def test_two_instances_share_facts_without_copies(self) -> None:
        second = self.case.artifact_open("second look")
        self.assertEqual("A17", second["id"])
        first_ids = [fid for row in self.instance["rows"] for fid in row["fact_ids"]]
        second_ids = [fid for row in second["rows"] for fid in row["fact_ids"]]
        self.assertEqual(first_ids, second_ids)
        self.assertEqual(7, len(self.case.data["facts"]))
        blob = json.dumps(second)
        self.assertNotIn("four sev-1 defects open", blob)

    def test_demo_is_idempotent(self) -> None:
        again = artifacts.build_demo(self.root)
        self.assertEqual(self.run_id, again)
        fresh = CaseRecord(self.root, self.run_id)
        self.assertEqual(
            ["A1", "A10", "A11", "A12", "A13", "A14", "A15", "A16", "A2", "A3", "A4", "A5", "A6", "A7", "A8", "A9"],
            sorted(fresh.data["artifacts"]),
        )


class StructuralTest(ArtifactCase):
    def test_fixture_matrix(self) -> None:
        verdicts = artifacts.audit_structural(
            self.root, self.case.data, self.instance["rows"]
        )
        self.assertEqual("passed", verdicts["R1"]["outcome"])
        self.assertEqual("failed", verdicts["R2"]["outcome"])
        self.assertIn("revoked", json.dumps(verdicts["R2"]["citations"]))
        self.assertEqual("passed", verdicts["R3"]["outcome"])
        self.assertEqual("failed", verdicts["R4"]["outcome"])
        self.assertIn("changed since", json.dumps(verdicts["R4"]["citations"]))
        self.assertEqual("failed", verdicts["R5"]["outcome"])
        self.assertIn("page 2", json.dumps(verdicts["R5"]["citations"]))
        self.assertEqual("failed", verdicts["R6"]["outcome"])
        self.assertIn("not in the inbox", json.dumps(verdicts["R6"]["citations"]))
        self.assertEqual("failed", verdicts["R7"]["outcome"])
        self.assertIn("no quotation", json.dumps(verdicts["R7"]["citations"]))
        self.assertEqual("failed", verdicts["RC"]["outcome"])
        self.assertEqual("not_run", verdicts["RG"]["outcome"])

    def test_untranscribed_is_incomplete_not_failed(self) -> None:
        for path in (self.root / "workspace/cache/text").glob("*.json"):
            cached = json.loads(path.read_text())
            if any("Okafor" in p.get("text", "") for p in cached.get("pages", [])):
                path.unlink()
        verdicts = artifacts.audit_structural(
            self.root, self.case.data, self.instance["rows"]
        )
        self.assertEqual("incomplete", verdicts["R3"]["outcome"])
        self.assertIn("no saved extraction", json.dumps(verdicts["R3"]["citations"]))


class SupportTest(ArtifactCase):
    def test_unsupported_inference_fails_semantic_only(self) -> None:
        fake = support.scripted(
            self.root,
            [],
            unsupported=lambda text: [
                {"unit": "finding:R3", "reason": "certification is not in evidence"},
                {"unit": "conclusion", "reason": "overreaches every finding"},
            ],
        )
        self.check_all(fake.client)
        rows = self.rows()
        r3 = rows["R3"]["checks"]
        self.assertEqual("passed", r3["structural"]["outcome"])
        self.assertEqual("failed", r3["semantic"]["outcome"])
        self.assertNotIn("established", json.dumps(r3))
        self.assertEqual("failed", rows["RC"]["checks"]["semantic"]["outcome"])
        self.assertEqual("passed", rows["R1"]["checks"]["semantic"]["outcome"])
        self.assertEqual("passed", rows["RG"]["checks"]["semantic"]["outcome"])

    def test_invalid_rows_never_reach_the_judge(self) -> None:
        fake = support.scripted(self.root, [])
        self.check_all(fake.client)
        rows = self.rows()
        for row_id in ("R2", "R4", "R5", "R6", "R7"):
            self.assertEqual("not_run", rows[row_id]["checks"]["semantic"]["outcome"])
        claims_calls = [
            call for call in fake.calls if "CLAIMS TO CHECK" in json.dumps(call)
        ]
        self.assertEqual(1, len(claims_calls))

    def test_malformed_judge_is_unavailable(self) -> None:
        fake = support.scripted(self.root, [])
        original = fake.responder

        def broken(kind, payload):
            if "CLAIMS TO CHECK" in json.dumps(payload):
                return {
                    "output_text": "not json{",
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                }
            return original(kind, payload)

        fake.responder = broken
        self.check_all(fake.client)
        rows = self.rows()
        self.assertEqual("unavailable", rows["R1"]["checks"]["semantic"]["outcome"])
        self.assertNotEqual("failed", rows["R1"]["checks"]["semantic"]["outcome"])
        self.assertTrue(rows["R1"]["checks"]["semantic"]["problems"])

    def test_provider_error_is_unavailable(self) -> None:
        fake = support.scripted(self.root, [])

        def down(_kind, _payload):
            raise RuntimeError("provider down")

        fake.responder = down
        self.check_all(fake.client)
        rows = self.rows()
        self.assertEqual("unavailable", rows["R1"]["checks"]["semantic"]["outcome"])


class ReviseTest(ArtifactCase):
    def test_revise_assertion_versions_and_stales(self) -> None:
        fake = support.scripted(self.root, [])
        self.check_all(fake.client)
        artifacts.revise_row(
            self.instance, self.case.data, self.root, "R3",
            assertion="The migration finished February 10.",
        )
        self.case.artifact_save(self.instance, "artifact_revised", "")
        self.assertEqual(2, len(self.instance["versions"]))
        stale = artifacts.row_staleness(
            self.root, self.case.data, self.rows()["R3"]
        )
        self.assertTrue(stale["structural"])
        self.assertTrue(stale["semantic"])
        self.assertEqual("pending", self.instance["review"]["decision"])

    def test_revise_citation_repins_and_fixes_wrong_page(self) -> None:
        fake = support.scripted(self.root, [])
        self.check_all(fake.client)
        artifacts.revise_row(
            self.instance, self.case.data, self.root, "R5",
            citation={"page": 1}, index=0,
        )
        self.case.artifact_save(self.instance, "artifact_revised", "")
        verdicts = artifacts.audit_structural(
            self.root, self.case.data, self.instance["rows"]
        )
        self.assertEqual("passed", verdicts["R5"]["outcome"])

    def test_targeted_recheck_keeps_untouched_rows(self) -> None:
        fake = support.scripted(self.root, [])
        self.check_all(fake.client)
        before = json.dumps(self.rows()["R1"]["checks"], sort_keys=True)
        artifacts.revise_row(
            self.instance, self.case.data, self.root, "R3",
            assertion="The migration finished February 10.",
        )
        scoped = [
            row for row in self.instance["rows"]
            if row["row_id"] in ("R3", "RC")
        ]
        structural = artifacts.audit_structural(self.root, self.case.data, scoped)
        semantic = artifacts.audit_support(
            self.ctx(fake.client), scoped, structural
        )
        artifacts.record_checks(
            self.instance, self.case.data, self.root, structural, semantic
        )
        self.assertEqual(before, json.dumps(self.rows()["R1"]["checks"], sort_keys=True))
        self.assertNotEqual(
            "failed", self.rows()["R3"]["checks"]["semantic"]["outcome"]
        )

    def test_feedback_is_annotation_not_version(self) -> None:
        artifacts.revise_row(
            self.instance, self.case.data, self.root, "R1",
            feedback="double-check the defect count",
        )
        self.assertEqual(1, len(self.instance["versions"]))
        self.assertEqual(1, len(self.rows()["R1"]["feedback"]))

    def test_rerun_identical_changes_nothing(self) -> None:
        fake = support.scripted(self.root, [])
        first = self.check_all(fake.client)
        self.assertTrue(first)
        versions = len(self.instance["versions"])
        history = len(self.instance["history"])
        second = self.check_all(fake.client)
        self.assertEqual([], second)
        self.assertEqual(versions, len(self.instance["versions"]))
        self.assertEqual(history, len(self.instance["history"]))

    def test_unknown_row_and_citation_refused(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.revise_row(
                self.instance, self.case.data, self.root, "R99",
                assertion="x",
            )
        with self.assertRaises(CaseError):
            artifacts.revise_row(
                self.instance, self.case.data, self.root, "R1",
                citation={"page": 1}, index=5,
            )


class ReviewTest(ArtifactCase):
    def test_approve_binds_snapshot(self) -> None:
        fake = support.scripted(self.root, [])
        self.check_all(fake.client)
        snapshot = artifacts.current_snapshot(self.instance, self.case.data)
        review = artifacts.review_instance(
            self.instance, self.case.data, "approved", "looks right",
            ["conclusion still broad"], snapshot,
        )
        self.assertEqual("approved", review["decision"])
        self.assertEqual(snapshot, review["snapshot"])
        self.assertEqual(["conclusion still broad"], review["qualifications"])
        self.assertEqual(2, len(self.instance["versions"]))

    def test_approve_after_change_refused(self) -> None:
        fake = support.scripted(self.root, [])
        self.check_all(fake.client)
        snapshot = artifacts.current_snapshot(self.instance, self.case.data)
        artifacts.revise_row(
            self.instance, self.case.data, self.root, "R1", feedback="note",
        )
        artifacts.revise_row(
            self.instance, self.case.data, self.root, "R1",
            assertion="Four Severity 1 defects were open.",
        )
        with self.assertRaises(CaseError):
            artifacts.review_instance(
                self.instance, self.case.data, "approved", "", [], snapshot,
            )
        self.assertEqual("pending", self.instance["review"]["decision"])

    def test_bad_decision_refused(self) -> None:
        snapshot = artifacts.current_snapshot(self.instance, self.case.data)
        with self.assertRaises(CaseError):
            artifacts.review_instance(
                self.instance, self.case.data, "maybe", "", [], snapshot,
            )


class ReviewCurrencyTest(ArtifactCase):
    DIFFS = {
        "A10": artifacts.hz_diff,
        "A11": artifacts.gv_diff,
        "A12": artifacts.ec_diff,
        "A13": artifacts.ts_diff,
        "A14": artifacts.pt_diff,
        "A15": artifacts.qa_diff,
        "A16": artifacts.sp_diff,
    }

    def test_approval_stays_current(self) -> None:
        for instance_id in sorted(self.DIFFS):
            with self.subTest(instance=instance_id):
                instance = self.case.artifact_get(instance_id)
                snapshot = artifacts.current_snapshot(
                    instance, self.case.data
                )
                artifacts.review_instance(
                    instance, self.case.data, "approved", "probe",
                    [], snapshot,
                )
                self.assertEqual(
                    snapshot,
                    artifacts.current_snapshot(instance, self.case.data),
                )

    def test_review_versions_carry_content(self) -> None:
        for instance_id, diff in sorted(self.DIFFS.items()):
            with self.subTest(instance=instance_id):
                instance = self.case.artifact_get(instance_id)
                snapshot = artifacts.current_snapshot(
                    instance, self.case.data
                )
                old = len(instance["versions"])
                artifacts.review_instance(
                    instance, self.case.data, "approved", "probe",
                    [], snapshot,
                )
                compared = diff(instance, old, old + 1)
                self.assertEqual([], compared["changes"])


class AuditReviewCurrencyTest(ArtifactCase):
    def _approve(self) -> None:
        fake = support.scripted(self.root, [])
        self.check_all(fake.client)
        snapshot = artifacts.current_snapshot(
            self.instance, self.case.data)
        artifacts.review_instance(
            self.instance, self.case.data, "approved", "probe",
            [], snapshot,
        )
        self.case.artifact_save(self.instance, "artifact_reviewed", "")

    def test_fresh_approval_is_current(self) -> None:
        self._approve()
        status = artifacts.instance_status(
            self.instance, self.case.data, self.root)
        self.assertTrue(status["review_current"])
        report = artifacts.export_instance(
            self.instance, self.case.data, self.root)
        self.assertTrue(report["review"]["current"])

    def test_source_change_stales_approval(self) -> None:
        self._approve()
        path = self.root / "workspace/inbox/DEMO-STATUS.pdf"
        path.write_bytes(path.read_bytes() + b"\nappendix: nothing new.")
        status = artifacts.instance_status(
            self.instance, self.case.data, self.root)
        self.assertGreater(status["counts"]["stale"], 0)
        self.assertFalse(status["review_current"])
        report = artifacts.export_instance(
            self.instance, self.case.data, self.root)
        self.assertFalse(report["review"]["current"])
        self.assertEqual("approved", report["review"]["decision"])

    def test_historical_export_leaves_currency_unknown(self) -> None:
        self._approve()
        report = artifacts.export_instance(
            self.instance, self.case.data, self.root, version=1)
        self.assertTrue(report["historical"])
        self.assertIsNone(report["review"]["current"])


class StaleTest(ArtifactCase):
    def test_source_change_stales_structural(self) -> None:
        fake = support.scripted(self.root, [])
        self.check_all(fake.client)
        path = self.root / "workspace/inbox/DEMO-STATUS.pdf"
        path.write_bytes(path.read_bytes() + b"\nappendix: nothing new.")
        stale = artifacts.row_staleness(
            self.root, self.case.data, self.rows()["R1"]
        )
        self.assertTrue(stale["structural"])
        self.assertIn("file changed", " ".join(stale["reasons"]))

    def test_revocation_stales_and_fails_recheck(self) -> None:
        fake = support.scripted(self.root, [])
        self.check_all(fake.client)
        self.case.flag_privileged("DEMO-STATUS", "late restriction")
        stale = artifacts.row_staleness(
            self.root, self.case.data, self.rows()["R1"]
        )
        self.assertTrue(stale["structural"])
        self.assertIn("access changed", " ".join(stale["reasons"]))
        verdicts = artifacts.audit_structural(
            self.root, self.case.data, self.instance["rows"]
        )
        self.assertEqual("failed", verdicts["R1"]["outcome"])

    def test_new_evidence_stales_semantic_only(self) -> None:
        fake = support.scripted(self.root, [])
        self.check_all(fake.client)
        self.case.record_fact(
            "readiness",
            "DEMO-STATUS",
            1,
            "Overall status is yellow",
            "status yellow",
        )
        stale = artifacts.row_staleness(
            self.root, self.case.data, self.rows()["R1"]
        )
        self.assertFalse(stale["structural"])
        self.assertTrue(stale["semantic"])
        self.assertIn("evidence changed", " ".join(stale["reasons"]))

    def test_contradiction_stays_visible(self) -> None:
        info = artifacts.contested_info(self.case.data, "testing")
        self.assertTrue(info["contested"])
        self.assertEqual(1, len(info["opposing"]))
        self.assertEqual("DEMO-STATUS", info["opposing"][0]["document_id"])
        self.assertNotIn(
            "no open defects", json.dumps(info)
        )


class ExportTest(ArtifactCase):
    def test_export_preserves_provenance_and_limits(self) -> None:
        fake = support.scripted(
            self.root, [],
            unsupported=lambda text: [{"unit": "conclusion", "reason": "overreach"}],
        )
        self.check_all(fake.client)
        report = artifacts.export_instance(
            self.instance, self.case.data, self.root
        )
        self.assertEqual("citation-claim-audit", report["template"])
        self.assertEqual("1.0.0", report["template_version"])
        self.assertTrue(report["evidence_fingerprint"])
        by_id = {row["row_id"]: row for row in report["rows"]}
        self.assertTrue(by_id["R1"]["fact_ids"])
        self.assertTrue(by_id["R1"]["citations"][0]["pin_sha"])
        self.assertTrue(by_id["R1"]["citations"][0]["current_sha"])
        self.assertTrue(
            any("structural: failed" in limit for limit in by_id["R5"]["limitations"])
        )
        self.assertTrue(
            any("semantic: failed" in limit for limit in by_id["RC"]["limitations"])
        )
        self.assertEqual("no approval recorded", report["review"]["scope"])
        self.assertTrue(
            any("never establishes" in limit for limit in report["limits"])
        )

    def test_export_redacts_privileged(self) -> None:
        report = artifacts.export_instance(
            self.instance, self.case.data, self.root
        )
        by_id = {row["row_id"]: row for row in report["rows"]}
        r2 = by_id["R2"]["citations"][0]
        self.assertEqual("[redacted: privileged]", r2["quote"])
        self.assertEqual("[redacted: privileged]", r2["passage"])
        self.assertNotIn("no open defects", json.dumps(report))

    def test_historical_export_is_frozen(self) -> None:
        fake = support.scripted(self.root, [])
        self.check_all(fake.client)
        artifacts.revise_row(
            self.instance, self.case.data, self.root, "R1",
            assertion="Four Severity 1 defects were open.",
        )
        report = artifacts.export_instance(
            self.instance, self.case.data, self.root, version=1
        )
        self.assertTrue(report["historical"])
        by_id = {row["row_id"]: row for row in report["rows"]}
        self.assertEqual(
            "Four Severity 1 defects were open at period end.",
            by_id["R1"]["assertion"],
        )
        self.assertTrue(
            any("not recomputed" in limit for limit in report["limits"])
        )
        with self.assertRaises(CaseError):
            artifacts.export_instance(
                self.instance, self.case.data, self.root, version=99
            )


class GalleryTest(ArtifactCase):
    def test_cards_are_honest(self) -> None:
        cards = {card["id"]: card for card in artifacts.gallery_cards()}
        for card_id in (
            "citation-claim-audit",
            "case-chronology-board",
            "jurisdiction-matrix",
            "contract-review",
            "playbook-deviation",
            "obligation-calendar",
            "legal-intake",
            "matter-deadlines",
            "discovery-log",
            "regulatory-horizon",
            "ai-governance",
            "entity-compliance",
            "term-sheet",
            "patent-claims",
            "sellside-qa",
        ):
            self.assertEqual("Demo", cards[card_id]["status"])
            self.assertIn("open_demo", cards[card_id]["actions"])
            self.assertIn("open_in_matter", cards[card_id]["actions"])
        planned = [c for c in cards.values() if c["status"] == "Planned"]
        for card in planned:
            self.assertEqual([], card["actions"])
            self.assertTrue(card["needs"])


class CompatTest(unittest.TestCase):
    def test_legacy_record_reads_leave_no_trace(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        path = root / "workspace/case_records/run-legacy.json"
        path.parent.mkdir(parents=True)
        legacy = {
            "run_id": "run-legacy",
            "checklist": {},
            "facts": [],
            "flags": [],
            "memo": None,
            "memo_history": [],
        }
        path.write_text(json.dumps(legacy))
        before = path.read_bytes()
        case = CaseRecord(root, "run-legacy")
        with self.assertRaises(CaseError):
            case.artifact_get("A1")
        with self.assertRaises(CaseError):
            case.artifact_open("x")
        self.assertTrue(artifacts.gallery_cards())
        self.assertEqual(before, path.read_bytes())


class CliTest(ArtifactCase):
    def test_cli_show_diff_export_roundtrip(self) -> None:
        cli = load_cli()
        cli.ROOT = self.root
        fake = support.scripted(self.root, [])
        self.check_all(fake.client)
        shown = cli.cmd_show(Namespace(run_id=self.run_id, instance="A1"))
        self.assertEqual(9, shown["status"]["rows"])
        self.assertIn("snapshot", shown)
        artifacts.revise_row(
            self.instance, self.case.data, self.root, "R1",
            assertion="Four Severity 1 defects were open.",
        )
        self.case.artifact_save(self.instance, "artifact_revised", "")
        diff = cli.cmd_diff(
            Namespace(run_id=self.run_id, instance="A1", old=1, new=2)
        )
        self.assertTrue(
            any(
                change["row_id"] == "R1" and change["change"] == "assertion"
                for change in diff["changes"]
            )
        )
        report = cli.cmd_export(
            Namespace(run_id=self.run_id, instance="A1", version=None)
        )
        self.assertEqual("A1", report["instance"])

    def test_cli_check_without_key_is_unavailable(self) -> None:
        cli = load_cli()
        cli.ROOT = self.root
        old = os.environ.pop("MODEL_API_KEY", None)
        self.addCleanup(
            lambda: os.environ.__setitem__("MODEL_API_KEY", old)
            if old is not None
            else None
        )
        result = cli.cmd_check(
            Namespace(run_id=self.run_id, instance="A1", rows="")
        )
        self.assertIn("R1", result["checked"])
        fresh = CaseRecord(self.root, self.run_id)
        semantic = fresh.artifact_get("A1")["rows"][0]["checks"]["semantic"]
        self.assertEqual("unavailable", semantic["outcome"])


class RegistryTest(unittest.TestCase):
    def test_every_template_registers_complete_handlers(self) -> None:
        expected = {
            artifacts.TEMPLATE_BOARD,
            artifacts.TEMPLATE_JX,
            artifacts.TEMPLATE_CT,
            artifacts.TEMPLATE_PB,
            artifacts.TEMPLATE_CAL,
            artifacts.TEMPLATE_IN,
            artifacts.TEMPLATE_DL,
            artifacts.TEMPLATE_DR,
            artifacts.TEMPLATE_HZ,
            artifacts.TEMPLATE_GV,
            artifacts.TEMPLATE_EC,
            artifacts.TEMPLATE_TS,
            artifacts.TEMPLATE_PT,
            artifacts.TEMPLATE_QA,
            artifacts.TEMPLATE_SP,
        }
        for template in expected:
            with self.subTest(template=template):
                found = artifacts.handlers(template)
                for key in (
                    "open", "snapshot", "diff", "export",
                    "export_takes_root", "review_keys", "count_key",
                ):
                    self.assertIn(key, found)
                self.assertTrue(found["review_keys"])
        audit = artifacts.handlers(artifacts.TEMPLATE_AUDIT)
        self.assertNotIn("open", audit)
        self.assertNotIn("snapshot", audit)

    def test_unknown_template_fails_closed(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.handlers("no-such-template")


def _challenge(case, tag: str, revision_needed: bool = False):
    return case.record_adversarial(
        strongest_against=f"against {tag}",
        fact_risks=f"risks {tag}",
        adverse_authority=f"authority {tag}",
        overlooked=f"missed {tag}",
        opposing_counsel=f"counsel {tag}",
        judge_needs=f"needs {tag}",
        alternative_theory=f"theory {tag}",
        reversing_fact=f"reverser {tag}",
        revision_needed=revision_needed,
        revision=f"rev {tag}",
    )


class AdversarialSurfaceTest(ArtifactCase):
    def test_status_shows_latest_challenge(self) -> None:
        first = _challenge(self.case, "one")
        second = _challenge(self.case, "two", revision_needed=True)
        status = artifacts.instance_status(
            self.instance, self.case.data, self.root)
        shown = status["adversarial"]
        self.assertEqual(second["id"], shown["id"])
        self.assertTrue(shown["revision_needed"])
        self.assertEqual("against two", shown["strongest_against"])
        self.assertEqual(2, shown["n"])
        self.assertNotEqual(first["id"], shown["id"])

    def test_status_without_challenge_is_none(self) -> None:
        status = artifacts.instance_status(
            self.instance, self.case.data, self.root)
        self.assertIsNone(status["adversarial"])

    def test_export_carries_challenge(self) -> None:
        entry = _challenge(self.case, "one")
        report = artifacts.export_instance(
            self.instance, self.case.data, self.root)
        self.assertEqual(entry["id"], report["adversarial"]["id"])
        text = artifacts.export_markdown(report)
        self.assertIn(entry["id"], text)
        self.assertIn("against one", text)

    def test_historical_export_omits_live_challenge(self) -> None:
        _challenge(self.case, "one")
        report = artifacts.export_instance(
            self.instance, self.case.data, self.root, version=1)
        self.assertTrue(report["historical"])
        self.assertIsNone(report["adversarial"])


class QuarantineSealTest(ArtifactCase):
    def test_export_seals_quarantined_quotes(self) -> None:
        from agent_kit import redactions

        quote = next(
            f["quote"] for f in self.case.data["facts"] if f["id"] == "F1")
        redactions.quarantine(self.root, "DEMO-STATUS", reason="test seal")
        report = artifacts.export_instance(
            self.instance, self.case.data, self.root)
        blob = json.dumps(report)
        self.assertNotIn(quote, blob)
        by_id = {row["row_id"]: row for row in report["rows"]}
        r1cite = by_id["R1"]["citations"][0]
        self.assertEqual("[sealed: quarantined]", r1cite["quote"])
        self.assertEqual("[sealed: quarantined]", r1cite["passage"])
        text = artifacts.export_markdown(report)
        self.assertNotIn(quote, text)
        self.assertIn("[sealed: quarantined]", text)

    def test_contested_opposing_sealed_with_root(self) -> None:
        from agent_kit import redactions

        # F3 opposes "testing" from DEMO-STATUS.
        quote = next(
            f["quote"] for f in self.case.data["facts"] if f["id"] == "F3")
        redactions.quarantine(self.root, "DEMO-STATUS", reason="test seal")
        info = artifacts.contested_info(self.case.data, "testing", self.root)
        self.assertTrue(info["contested"])
        (entry,) = [e for e in info["opposing"]
                    if e["fact_id"] == "F3"]
        self.assertEqual("[sealed: quarantined]", entry["quote"])
        report = artifacts.export_instance(
            self.instance, self.case.data, self.root)
        self.assertNotIn(quote, json.dumps(report))

    def test_release_restores_quotes(self) -> None:
        from agent_kit import redactions

        quote = next(
            f["quote"] for f in self.case.data["facts"] if f["id"] == "F1")
        redactions.quarantine(self.root, "DEMO-STATUS", reason="test seal")
        redactions.release(self.root, "DEMO-STATUS", note="reviewed")
        report = artifacts.export_instance(
            self.instance, self.case.data, self.root)
        by_id = {row["row_id"]: row for row in report["rows"]}
        self.assertEqual(quote, by_id["R1"]["citations"][0]["quote"])

    def test_view_scope_seals_and_never_mutates(self) -> None:
        from agent_kit import redactions

        self.case.flag_privileged("DEMO-PLAN", "probe")
        redactions.quarantine(self.root, "DEMO-STATUS", reason="test seal")
        cites = [
            {"document_id": "DEMO-PLAN", "page": 1, "quote": "plan words",
             "passage": "plan passage"},
            {"document_id": "DEMO-STATUS", "page": 1, "quote": "status words",
             "passage": "status passage"},
            {"document_id": "DEMO-MINUTES", "page": 1, "quote": "open words"},
        ]
        scoped = artifacts.scope_citations(cites, self.case.data, self.root)
        self.assertEqual("[redacted: privileged]", scoped[0]["quote"])
        self.assertEqual("[redacted: privileged]", scoped[0]["passage"])
        self.assertEqual("[sealed: quarantined]", scoped[1]["quote"])
        self.assertEqual("[sealed: quarantined]", scoped[1]["passage"])
        self.assertEqual("open words", scoped[2]["quote"])
        self.assertNotIn("passage", scoped[2])
        self.assertEqual("plan words", cites[0]["quote"])
        self.assertEqual("status words", cites[1]["quote"])


def _lensed_challenge(case, tag: str, lens: str):
    entry = _challenge(case, tag)
    entry["lens"] = lens
    for stored in case.data.get("adversarial", []) or []:
        if stored.get("id") == entry["id"]:
            stored["lens"] = lens
    return entry


class PanelWiringTest(ArtifactCase):
    def test_status_and_export_carry_panel(self) -> None:
        _lensed_challenge(self.case, "one", "evidence")
        _lensed_challenge(self.case, "two", "law")
        self.case.raise_objection("No standing", reporter="AR1")
        self.case.disprove_objection(
            "No standing", "Standing was cured", reporter="AR2")
        status = artifacts.instance_status(
            self.instance, self.case.data, self.root)
        panel = status["panel"]
        self.assertEqual(2, panel["reviews"])
        self.assertEqual(["evidence", "law"], panel["lenses"])
        self.assertFalse(panel["complete"])
        report = artifacts.export_instance(
            self.instance, self.case.data, self.root)
        self.assertEqual(panel, report["panel"])
        text = artifacts.export_markdown(report)
        self.assertIn("## Review panel", text)
        self.assertIn("objections open: 0", text)

    def test_absent_and_historical_panel_is_none(self) -> None:
        status = artifacts.instance_status(
            self.instance, self.case.data, self.root)
        self.assertIsNone(status["panel"])
        report = artifacts.export_instance(
            self.instance, self.case.data, self.root, version=1)
        self.assertIsNone(report["panel"])


class MarkCitationTest(ArtifactCase):
    def test_mark_records_category_without_revision(self) -> None:
        versions = len(self.instance["versions"])
        row = artifacts.mark_citation(
            self.instance, "R1", 0, "wrong", note="not on page")
        (mark,) = row["marks"]
        self.assertEqual("wrong", mark["category"])
        self.assertEqual("not on page", mark["note"])
        self.assertEqual("DEMO-STATUS", mark["document_id"])
        self.assertEqual(versions, len(self.instance["versions"]))
        self.assertIn("marked", repr(row["history"]))

    def test_mark_rejects_bad_target_and_category(self) -> None:
        with self.assertRaises(CaseError):
            artifacts.mark_citation(self.instance, "RX", 0, "wrong")
        with self.assertRaises(CaseError):
            artifacts.mark_citation(self.instance, "R1", 9, "wrong")
        with self.assertRaises(CaseError):
            artifacts.mark_citation(self.instance, "R1", 0, "bogus")

    def test_marks_reach_export_and_markdown(self) -> None:
        artifacts.mark_citation(self.instance, "R1", 0, "irrelevant")
        report = artifacts.export_instance(
            self.instance, self.case.data, self.root)
        by_id = {row["row_id"]: row for row in report["rows"]}
        self.assertEqual(
            "irrelevant", by_id["R1"]["marks"][0]["category"])
        text = artifacts.export_markdown(report)
        self.assertIn("marked `irrelevant`", text)


if __name__ == "__main__":
    unittest.main()
