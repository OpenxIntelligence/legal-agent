"""User-facing promotion path: CLI verbs over the NV-3 authority.

``promotion.promote_example`` stays the single promotion authority
(whole-artifact snapshot, label rules, sealed refusal); these verbs
only expose it: preservation-truth building, run-scoped example
records, listing, and replay through the real audit path. The
adversarial demo matter (unpinned DOC-VANISHED citation) is
honestly refused, never force-promoted.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agent_kit import promotion
from agent_kit.artifacts.demo import build_demo
from agent_kit.case_record import CaseError, CaseRecord
from eval import review_cases
from scripts import artifact


def _ns(**over) -> SimpleNamespace:
    args = {"run_id": "", "instance": ""}
    args.update(over)
    return SimpleNamespace(**args)


def _materialize_promotable(test: unittest.TestCase):
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    root = Path(tmp.name)
    run_id, record, instance = review_cases.materialize(
        root, "review-temporal-support")
    return root, run_id, record, instance


def _label(inst, actor="sim:r1", verdict="insufficient"):
    from agent_kit import review_labels

    return review_labels.record_label(
        inst, row_id="R1", verdict=verdict,
        rationale=f"{actor} timing judgment",
        actor=actor, origin="simulated", exposure="blinded")


def _check(test: unittest.TestCase, root, run_id, record, instance):
    from agent_kit import artifacts, tools
    from tests import support

    fake = support.scripted(root, [], unsupported=lambda text: [])
    ctx = tools.Context(fake.client, root, run_id, record, True)
    rows = instance["rows"]
    structural = artifacts.audit_structural(root, record.data, rows)
    semantic = artifacts.audit_support(ctx, rows, structural)
    artifacts.record_checks(instance, record.data, root, structural,
                            semantic)
    record.artifact_save(instance, "artifact_checked", "")


class PreservationTruthTest(unittest.TestCase):
    def test_outcomes_and_classes(self) -> None:
        inst = {"rows": [
            {"row_id": "R1", "checks": {
                "structural": {"outcome": "passed"},
                "semantic": {"outcome": "failed"}}},
            {"row_id": "R2"},
        ]}
        truth = promotion.preservation_truth(
            inst, {"R1": "clean", "R2": "missing-source"})
        self.assertEqual(
            {"class": "clean", "structural": "passed",
             "semantic": "failed"},
            truth["R1"])
        self.assertEqual(
            {"class": "missing-source", "structural": "not_run",
             "semantic": "not_run"},
            truth["R2"])

    def test_refusals(self) -> None:
        with self.assertRaises(CaseError):
            promotion.preservation_truth({"rows": []}, {})
        with self.assertRaises(CaseError):
            promotion.preservation_truth(
                {"rows": [{"row_id": "R1"}]}, {})
        with self.assertRaises(CaseError):
            promotion.preservation_truth(
                {"rows": [{"row_id": "R1"}]}, {"R1": "  "})


class PromoteVerbTest(unittest.TestCase):
    def _promote(self, root, run_id, instance, **over):
        old = artifact.ROOT
        artifact.ROOT = root
        self.addCleanup(setattr, artifact, "ROOT", old)
        record = CaseRecord(root, run_id)
        record.artifact_save(instance, "artifact_checked", "")
        args = {
            "run_id": run_id, "instance": instance["id"],
            "example_id": "cli-timing", "role": "regression",
            "actor": "sim:cli", "failure": "timing overreach",
            "behavior": "preserve current outcomes",
            "classes": json.dumps(
                {r["row_id"]: "clean" for r in instance["rows"]}),
        }
        args.update(over)
        return artifact.cmd_promote(_ns(**args))

    def test_promote_list_replay_round_trip(self) -> None:
        root, run_id, record, instance = _materialize_promotable(self)
        _label(instance)
        _check(self, root, run_id, record, instance)
        # Labels bind pre-check wording; re-check keeps wording, so
        # labels stay fresh for promotion.
        out = self._promote(root, run_id, instance)
        self.assertEqual("cli-timing", out["example"])
        self.assertEqual("regression", out["role"])
        listed = artifact.cmd_examples(_ns(
            run_id=run_id, instance=instance["id"]))
        self.assertEqual(1, len(listed["examples"]))
        self.assertEqual("cli-timing", listed["examples"][0]["id"])
        self.assertEqual("promoted",
                         listed["examples"][0]["exposure"][0]["use"])
        replayed = artifact.cmd_replay(_ns(
            run_id=run_id, instance=instance["id"],
            example="cli-timing"))
        self.assertEqual("cli-timing", replayed["example"])
        self.assertIn("complete", replayed)
        self.assertTrue(replayed["rows"])
        relisted = artifact.cmd_examples(_ns(
            run_id=run_id, instance=instance["id"]))
        uses = [stamp.get("use") for stamp in
                relisted["examples"][0]["exposure"]]
        self.assertEqual(["promoted", "replayed"], uses)

    def test_sealed_and_duplicate_refused(self) -> None:
        root, run_id, record, instance = _materialize_promotable(self)
        _label(instance)
        _check(self, root, run_id, record, instance)
        with self.assertRaises(CaseError):
            self._promote(root, run_id, instance, role="sealed")
        self._promote(root, run_id, instance)
        with self.assertRaises(CaseError):
            self._promote(root, run_id, instance)
        with self.assertRaises(CaseError):
            old = artifact.ROOT
            artifact.ROOT = root
            try:
                artifact.cmd_replay(_ns(
                    run_id=run_id, instance=instance["id"],
                    example="../escape"))
            finally:
                artifact.ROOT = old

    def test_adversarial_demo_honestly_refused(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        run_id = build_demo(root)
        record = CaseRecord(root, run_id)
        instance = record.artifact_get("A1")
        _label(instance)
        record.artifact_save(instance, "artifact_labeled", "")
        with self.assertRaises(CaseError) as ctx:
            self._promote(root, run_id, instance,
                          example_id="demo-nope")
        self.assertIn("pinned source version", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
