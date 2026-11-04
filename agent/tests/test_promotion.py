"""NV-3 promotion + replay + holdout seal.

Promoted examples carry an immutable input snapshot, original
vs repaired outputs, expected behavior, reference provenance,
dataset role, and exposure history. Sealed release examples
replay only on explicit opt-in, are never rewritten, and are
unreadable from investigator code and normal endpoints.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from agent_kit import artifacts, promotion, review_labels, tools
from agent_kit import document_text
from agent_kit.case_record import CaseError, CaseRecord
from eval import review_cases, review_run
from tests import support

KIT_ROOT = Path(__file__).resolve().parents[1]


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


def _truth(failed: bool) -> dict:
    return {
        "R1": {"class": "unsupported_inference" if failed else "clean",
               "structural": "passed",
               "semantic": "failed" if failed else "passed"},
        "RC": {"class": "clean",
               "structural": "passed", "semantic": "passed"},
        "RG": {"class": "clean",
               "structural": "not_run", "semantic": "passed"},
    }


def _label(inst, actor="sim:r1", verdict="insufficient"):
    return review_labels.record_label(
        inst, row_id="R1", verdict=verdict,
        rationale=f"{actor} timing judgment",
        actor=actor, origin="simulated", exposure="blinded")


class PromoteCase(unittest.TestCase):
    def materialize(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        run_id, record, instance = review_cases.materialize(
            root, "review-temporal-support")
        return root, run_id, record, instance

    def promote(self, instance, record, root, dest, **over):
        args = {
            "example_id": "rev-timing",
            "role": "regression",
            "truth": _truth(failed=True),
            "failure": "pre-approval knowledge asserted from a "
            "post-approval source",
            "behavior": "flag the inference unsupported; pass the "
            "matching quote",
            "actor": "sim:promoter",
        }
        args.update(over)
        return promotion.promote_example(
            instance, record.data, root, dest, **args)


class PromoteTest(PromoteCase):
    def test_before_and_after_examples(self) -> None:
        root, run_id, record, instance = self.materialize()
        case = review_cases.CASES["review-temporal-support"]
        _audit(root, run_id, record, instance,
               case["unsupported_before"])
        _label(instance)
        _label(instance, actor="sim:r2")
        dest = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, dest, True)
        before = self.promote(
            instance, record, root, dest, example_id="rev-before",
            role="development")
        self.assertEqual("development", before["role"])
        self.assertEqual("reviewed-example/v1", before["version"])
        self.assertEqual(
            ["Status email. Reyes learned of the shortfall on May 6."],
            before["documents"]["REVIEW-EMAIL"]["pages"])
        self.assertEqual(
            "Reyes knew of the shortfall before the May 5 approval.",
            before["original_output"][0]["assertion"])
        self.assertEqual(["L1", "L2"],
                         before["reference"]["R1"]["labels"])
        self.assertEqual(["simulated"],
                         before["reference"]["R1"]["origins"])
        self.assertEqual("promoted", before["exposure"][0]["use"])
        self.assertTrue((dest / "rev-before.json").is_file())

        artifacts.revise_row(
            instance, record.data, root, "R1",
            assertion=case["repaired_assertion"])
        _audit(root, run_id, record, instance,
               case["unsupported_after"])
        review_labels.record_label(
            instance, row_id="R1", verdict="supported",
            rationale="qualified wording matches the source",
            actor="sim:r1", origin="simulated", exposure="blinded")
        after = self.promote(
            instance, record, root, dest, example_id="rev-after",
            truth=_truth(failed=False))
        self.assertEqual(
            case["repaired_assertion"],
            after["repaired_output"][0]["assertion"])
        self.assertEqual(
            before["original_output"][0]["assertion"],
            after["original_output"][0]["assertion"])

    def test_refusals(self) -> None:
        root, run_id, record, instance = self.materialize()
        dest = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, dest, True)
        with self.assertRaises(CaseError):
            self.promote(instance, record, root, dest,
                         role="sealed")
        with self.assertRaises(CaseError):
            self.promote(instance, record, root, dest,
                         role="staging")
        with self.assertRaises(CaseError):
            self.promote(instance, record, root, dest,
                         example_id="Bad Id!")
        with self.assertRaises(CaseError):
            self.promote(instance, record, root, dest, actor=" ")
        with self.assertRaises(CaseError):
            self.promote(instance, record, root, dest,
                         truth={"R1": _truth(True)["R1"]})
        bad = _truth(True)
        bad["RX"] = {"structural": "passed", "semantic": "passed"}
        with self.assertRaises(CaseError):
            self.promote(instance, record, root, dest, truth=bad)
        with self.assertRaises(CaseError):
            self.promote(instance, record, root, dest)
        _label(instance)
        _label(instance, actor="sim:r2", verdict="supported")
        with self.assertRaises(CaseError):
            self.promote(instance, record, root, dest)
        review_labels.adjudicate(
            instance, row_id="R1", decision="corrected",
            rationale="overreach stands", actor="sim:arbiter")
        self.promote(instance, record, root, dest)
        with self.assertRaises(CaseError):
            self.promote(instance, record, root, dest)

    def test_stale_labels_and_missing_transcript_refused(self) -> None:
        root, run_id, record, instance = self.materialize()
        case = review_cases.CASES["review-temporal-support"]
        dest = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, dest, True)
        _label(instance)
        _label(instance, actor="sim:r2")
        artifacts.revise_row(
            instance, record.data, root, "R1",
            assertion=case["repaired_assertion"])
        with self.assertRaises(CaseError):
            self.promote(instance, record, root, dest)

        root2, run_id2, record2, instance2 = self.materialize()
        _label(instance2)
        _label(instance2, actor="sim:r2")
        cache = root2 / "workspace" / "cache" / "text"
        for path in cache.glob("*.json"):
            path.unlink()
        with self.assertRaises(CaseError):
            self.promote(instance2, record2, root2, dest,
                         example_id="rev-no-transcript")


class ReplayTest(PromoteCase):
    def test_promoted_example_replays_complete(self) -> None:
        root, run_id, record, instance = self.materialize()
        case = review_cases.CASES["review-temporal-support"]
        _audit(root, run_id, record, instance,
               case["unsupported_before"])
        _label(instance)
        _label(instance, actor="sim:r2")
        reviewed = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, reviewed, True)
        holdout = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, holdout, True)
        self.promote(instance, record, root, reviewed)
        old_reviewed, old_holdout = (
            review_run.REVIEWED_DIR, review_run.HOLDOUT_DIR)
        review_run.REVIEWED_DIR, review_run.HOLDOUT_DIR = (
            reviewed, holdout)
        try:
            report = review_run.run_all()
        finally:
            review_run.REVIEWED_DIR, review_run.HOLDOUT_DIR = (
                old_reviewed, old_holdout)
        self.assertEqual("scripted", report["client"])
        self.assertFalse(report["sealed_included"])
        (entry,) = report["reports"]
        self.assertEqual("rev-timing", entry["case"])
        self.assertTrue(entry["complete"])
        self.assertTrue(all(row["match"]
                            for row in entry["rows"].values()))

    def test_sealed_opt_in_only_and_never_rewritten(self) -> None:
        reviewed = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, reviewed, True)
        holdout = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, holdout, True)
        shutil.copy(
            KIT_ROOT / "eval" / "sealed_holdout"
            / "sealed-timing-waiver.json",
            holdout / "sealed-timing-waiver.json")
        before = hashlib.sha256(
            (holdout / "sealed-timing-waiver.json").read_bytes()
        ).hexdigest()
        old_reviewed, old_holdout = (
            review_run.REVIEWED_DIR, review_run.HOLDOUT_DIR)
        review_run.REVIEWED_DIR, review_run.HOLDOUT_DIR = (
            reviewed, holdout)
        try:
            default = review_run.run_all()
            sealed = review_run.run_all(include_sealed=True)
        finally:
            review_run.REVIEWED_DIR, review_run.HOLDOUT_DIR = (
                old_reviewed, old_holdout)
        self.assertEqual([], default["reports"])
        self.assertEqual([], default["exposure"])
        (entry,) = sealed["reports"]
        self.assertTrue(entry["complete"])
        self.assertTrue(sealed["sealed_files_unchanged"])
        self.assertEqual("sealed-replay", sealed["exposure"][0]["use"])
        after = hashlib.sha256(
            (holdout / "sealed-timing-waiver.json").read_bytes()
        ).hexdigest()
        self.assertEqual(before, after)


class SealTest(unittest.TestCase):
    MARKERS = ("SEALED-NOTE", "waiver was signed on April 2")

    def test_investigator_code_never_names_holdout(self) -> None:
        allowed = {
            KIT_ROOT / "eval" / "review_run.py",
        }
        offenders = []
        for base in ("agent_kit", "scripts", "verification"):
            for path in (KIT_ROOT / base).rglob("*.py"):
                if path in allowed:
                    continue
                if "sealed_holdout" in path.read_text():
                    offenders.append(str(path))
        for base in ("ui", "ui-harness"):
            root = KIT_ROOT.parent / base
            if not root.is_dir():
                continue
            for path in root.rglob("*.py"):
                if "sealed_holdout" in path.read_text():
                    offenders.append(str(path))
        self.assertEqual([], offenders)
        self.assertNotIn(
            "sealed_holdout",
            Path(promotion.__file__).read_text())

    def test_endpoints_and_search_cannot_see_holdout(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        run_id = artifacts.build_demo(root)
        record = CaseRecord(root, run_id)
        instance = record.artifact_get("A1")
        blob = json.dumps({
            "status": artifacts.instance_status(
                instance, record.data, root),
            "export": artifacts.export_instance(
                instance, record.data, root),
            "markdown": artifacts.export_markdown(
                artifacts.export_instance(
                    instance, record.data, root)),
        })
        for marker in self.MARKERS:
            self.assertNotIn(marker, blob)
        hits = document_text.search_corpus(root, "waiver signed April")
        found = json.dumps(hits)
        for marker in self.MARKERS:
            self.assertNotIn(marker, found)


if __name__ == "__main__":
    unittest.main()
