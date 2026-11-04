"""NV-4 quality reporting: work-product vs system-version panels.

instance_status and exports carry a read-only quality roll-up
(checks, failures, staleness, open issues, label states, review)
plus the latest evaluated-configuration receipt — or an
explicit none when no evaluation is attached. Historical
exports show neither. No self-validated accuracy anywhere.
"""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from agent_kit import artifacts, review_labels, tools
from agent_kit.case_record import CaseError, CaseRecord
from eval import review_cases
from tests import support

CLI_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "artifact.py"
)


def load_cli():
    spec = importlib.util.spec_from_file_location("artifact_cli", CLI_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _audit(root, run_id, record, instance, unsupported):
    def mark(text: str) -> list:
        return [
            {"unit": unit, "reason": "scripted: fixture truth"}
            for unit in unsupported
        ]

    fake = support.scripted(root, [], unsupported=mark)
    ctx = tools.Context(fake.client, root, run_id, record, True)
    rows = instance["rows"]
    structural = artifacts.audit_structural(root, record.data, rows)
    semantic = artifacts.audit_support(ctx, rows, structural)
    artifacts.record_checks(
        instance, record.data, root, structural, semantic
    )
    record.artifact_save(instance, "artifact_checked", "")


class QualityCase(unittest.TestCase):
    def demo(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        run_id = artifacts.build_demo(root)
        record = CaseRecord(root, run_id)
        return root, run_id, record, record.artifact_get("A1")

    def temporal(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        run_id, record, instance = review_cases.materialize(
            root, "review-temporal-support")
        return root, run_id, record, instance


class StatusTest(QualityCase):
    def test_unchecked_instance_reports_scope_and_no_eval(self) -> None:
        root, _, record, instance = self.demo()
        status = artifacts.instance_status(
            instance, record.data, root)
        quality = status["quality"]
        self.assertEqual(9, quality["rows"])
        self.assertEqual(0, quality["checked"])
        self.assertEqual([], quality["failed_rows"])
        self.assertEqual([], quality["open_objections"])
        self.assertEqual({}, quality["label_states"])
        self.assertEqual("pending", quality["review"]["decision"])
        self.assertIsNone(status["eval_version"])

    def test_receipt_validated_and_shown(self) -> None:
        root, _, record, instance = self.demo()
        with self.assertRaises(CaseError):
            artifacts.record_eval_receipt(instance, "suite")
        with self.assertRaises(CaseError):
            artifacts.record_eval_receipt(
                instance, {"suite": "nv-slice", "client": ""})
        entry = artifacts.record_eval_receipt(instance, {
            "suite": "nv-slice", "client": "scripted",
            "config": {"mode": "off"}, "limits": "9 rows",
            "summary": "plumbing only"})
        self.assertEqual("nv-slice", entry["suite"])
        status = artifacts.instance_status(
            instance, record.data, root)
        self.assertEqual("scripted", status["eval_version"]["client"])

    def test_failed_open_and_labeled_rows_listed(self) -> None:
        case = review_cases.CASES["review-temporal-support"]
        root, run_id, record, instance = self.temporal()
        _audit(root, run_id, record, instance,
               case["unsupported_before"])
        record.raise_objection("R1 timing is unsupported.",
                               reporter="sim:r1")
        review_labels.record_label(
            instance, row_id="R1", verdict="insufficient",
            rationale="timing overreach", actor="sim:r1",
            origin="simulated", exposure="blinded")
        quality = artifacts.instance_status(
            instance, record.data, root)["quality"]
        self.assertEqual(3, quality["rows"])
        self.assertEqual(3, quality["checked"])
        self.assertEqual(["R1"], quality["failed_rows"])
        self.assertEqual(["OBJ1"], quality["open_objections"])
        self.assertEqual({"R1": "submitted"}, quality["label_states"])

        artifacts.revise_row(
            instance, record.data, root, "R1",
            assertion=case["repaired_assertion"])
        _audit(root, run_id, record, instance,
               case["unsupported_after"])
        quality = artifacts.instance_status(
            instance, record.data, root)["quality"]
        self.assertEqual([], quality["failed_rows"])
        self.assertEqual(["OBJ1"], quality["open_objections"])


class ExportTest(QualityCase):
    def test_export_and_markdown_carry_panels(self) -> None:
        case = review_cases.CASES["review-temporal-support"]
        root, run_id, record, instance = self.temporal()
        _audit(root, run_id, record, instance,
               case["unsupported_before"])
        report = artifacts.export_instance(
            instance, record.data, root)
        self.assertEqual(["R1"], report["quality"]["failed_rows"])
        self.assertIsNone(report["eval_version"])
        text = artifacts.export_markdown(report)
        self.assertIn("## Quality", text)
        self.assertIn("failed rows: R1", text)
        self.assertIn("evaluated configuration: none attached", text)

        artifacts.record_eval_receipt(instance, {
            "suite": "nv-slice", "client": "scripted"})
        report = artifacts.export_instance(
            instance, record.data, root)
        self.assertEqual("nv-slice",
                         report["eval_version"]["suite"])
        text = artifacts.export_markdown(report)
        self.assertIn("evaluated: nv-slice (scripted)", text)

    def test_historical_export_shows_neither(self) -> None:
        case = review_cases.CASES["review-temporal-support"]
        root, run_id, record, instance = self.temporal()
        _audit(root, run_id, record, instance,
               case["unsupported_before"])
        artifacts.revise_row(
            instance, record.data, root, "R1",
            assertion=case["repaired_assertion"])
        artifacts.record_eval_receipt(instance, {
            "suite": "nv-slice", "client": "scripted"})
        report = artifacts.export_instance(
            instance, record.data, root, version=1)
        self.assertTrue(report["historical"])
        self.assertIsNone(report["quality"])
        self.assertIsNone(report["eval_version"])


class CliReceiptTest(QualityCase):
    def test_receipt_saved_and_shown(self) -> None:
        root, run_id, _, _ = self.demo()
        cli = load_cli()
        cli.ROOT = root
        receipt = root / "receipt.json"
        receipt.write_text(json.dumps({
            "suite": "nv-slice", "client": "scripted",
            "summary": "plumbing only"}))
        result = cli.cmd_eval_receipt(Namespace(
            run_id=run_id, instance="A1",
            receipt=str(receipt)))
        self.assertEqual("nv-slice", result["suite"])
        fresh = CaseRecord(root, run_id)
        instance = fresh.artifact_get("A1")
        status = artifacts.instance_status(
            instance, fresh.data, root)
        self.assertEqual("nv-slice",
                         status["eval_version"]["suite"])

    def test_bad_receipt_refused_without_save(self) -> None:
        root, run_id, _, _ = self.demo()
        cli = load_cli()
        cli.ROOT = root
        with self.assertRaises(CaseError):
            cli.cmd_eval_receipt(Namespace(
                run_id=run_id, instance="A1",
                receipt=str(root / "missing.json")))
        broken = root / "broken.json"
        broken.write_text("{nope")
        with self.assertRaises(CaseError):
            cli.cmd_eval_receipt(Namespace(
                run_id=run_id, instance="A1",
                receipt=str(broken)))
        empty = root / "empty.json"
        empty.write_text(json.dumps({"suite": "x"}))
        with self.assertRaises(CaseError):
            cli.cmd_eval_receipt(Namespace(
                run_id=run_id, instance="A1",
                receipt=str(empty)))
        fresh = CaseRecord(root, run_id)
        self.assertEqual(
            [], fresh.artifact_get("A1").get("eval_receipts") or [])


if __name__ == "__main__":
    unittest.main()
