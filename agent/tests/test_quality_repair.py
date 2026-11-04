"""Quality-control and bounded repair mechanics (offline, mocked judges).

These tests prove controller mechanics only: modules run
deterministic-first with declared contracts, defects become bounded
repair tasks, revisions are rechecked, regressions flagged, and
acceptance is never blocked or weakened. They say nothing about
judge quality, which needs live held-out evaluation.
"""

from __future__ import annotations

import copy
import json
import os
import unittest

from agent_kit import tools, usage
from agent_kit.case_record import CaseRecord
from agent_kit.progress import tokens_spent
from agent_kit.settings import ConfigError
from agent_kit.tools import analyze_document, submit_memo
from agent_kit.progress import new_run
from quality import controller, records, registry
from tests import support

SUPPORTS = {
    "document_id": "DOC-REPORT",
    "page": 1,
    "quote": "Open Severity 1 defects: 21",
}
OPPOSES = {
    "document_id": "DOC-MINUTES",
    "page": 1,
    "quote": "confirmed the system is ready for go-live",
}


def materiality_calls(fake) -> list:
    return [
        call
        for call in fake.calls
        if call[0] == "respond"
        and "COUNTEREVIDENCE MATERIALITY" in json.dumps(call[1])
    ]


def claims_calls(fake) -> list:
    return [
        call
        for call in fake.calls
        if call[0] == "respond" and "CLAIMS TO CHECK" in json.dumps(call[1])
    ]


class EnvCase(unittest.TestCase):
    def set_env(self, key, value):
        sentinel = object()
        old = os.environ.get(key, sentinel)

        def restore():
            if old is sentinel:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old

        self.addCleanup(restore)
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


class QCCase(EnvCase):
    """One contested item: supports F1 recorded, opposes F2 recorded."""

    def setUp(self) -> None:
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        self.set_env("RUN_TOKEN_BUDGET", None)
        self.set_env("MAX_CONTEXT_BYTES", None)
        self.fake = support.scripted(self.root, [])
        self.run_id = new_run(
            self.root, instructions="i", objective="o", verification=True
        )
        self.case = CaseRecord(self.root, self.run_id)
        self.case.set_checklist([support.checklist_item("timing")])
        self.ctx = tools.Context(
            self.fake.client, self.root, self.run_id, self.case, True
        )
        analyze_document.run(
            self.ctx, {"document_ids": list(support.INITIAL), "request": "x"}
        )
        self.case.record_fact("timing", "DOC-REPORT", 1, SUPPORTS["quote"], "defects")
        self.case.record_fact(
            "timing",
            "DOC-MINUTES",
            1,
            OPPOSES["quote"],
            "ready claimed",
            stance="opposes",
        )
        support.satisfy_reasoning_gate(self.case, issues=["timing"])

    def memo(self, citations, gaps="", conclusion="Conclusion."):
        return {
            "conclusion": conclusion,
            "findings": [
                {
                    "item": "timing",
                    "status": "asserted",
                    "summary": "Defects were open at period end.",
                    "citations": copy.deepcopy(citations),
                }
            ],
            "gaps": gaps,
        }

    def review(self, memo):
        return controller.review(self.ctx, memo)

    def use_material(self, verdicts, addressed=None):
        self.fake = support.scripted(
            self.root,
            [],
            material=lambda text: verdicts,
            addressed=(lambda text: addressed) if addressed is not None else None,
        )
        self.ctx = tools.Context(
            self.fake.client, self.root, self.run_id, self.case, True
        )


class RegistryTest(unittest.TestCase):
    def test_builtins_declare_the_full_contract(self) -> None:
        from quality import modules  # noqa: F401  (register builtins)

        self.assertTrue(registry.ordered())
        for spec in registry.ordered():
            for key in registry.REQUIRED:
                self.assertIn(key, spec, spec.get("name"))
            self.assertIn(spec["kind"], registry.KINDS)
            self.assertTrue(spec["version"])
            self.assertTrue(callable(spec["applies"]))
            self.assertTrue(callable(spec["run"]))

    def test_deterministic_modules_run_first(self) -> None:
        from quality import modules  # noqa: F401

        kinds = [s["kind"] for s in registry.ordered()]
        self.assertEqual(sorted(kinds), kinds)
        names = [s["name"] for s in registry.ordered()]
        self.assertLess(names.index("counterevidence"), names.index("objection-materiality"))

    def test_incomplete_specs_fail_loud(self) -> None:
        with self.assertRaises(ValueError):
            registry.register({"name": "half"})
        with self.assertRaises(ValueError):
            registry.register(
                {
                    "name": "weird",
                    "version": "1",
                    "kind": "quantum",
                    "purpose": "x",
                    "applies": lambda *a: (True, ""),
                    "inputs": "x",
                    "output_schema": {},
                    "dependencies": [],
                    "budget": {},
                    "obligation": "required",
                    "run": lambda *a: {},
                }
            )
        with self.assertRaises(ValueError):
            registry.register(
                {
                    "name": "sneaky",
                    "version": "1",
                    "kind": "deterministic",
                    "purpose": "x",
                    "applies": lambda *a: (True, ""),
                    "inputs": "x",
                    "output_schema": {},
                    "dependencies": [],
                    "budget": {},
                    "obligation": "whenever",
                    "run": lambda *a: {},
                }
            )

    def test_builtins_declare_required_obligations(self) -> None:
        from quality import modules  # noqa: F401

        for spec in registry.ordered():
            self.assertEqual("required", spec["obligation"], spec["name"])

    def test_malformed_budgets_fail_loud(self) -> None:
        from quality import modules  # noqa: F401  (register builtins)

        base = {
            "name": "spendy",
            "version": "1",
            "kind": "deterministic",
            "purpose": "x",
            "applies": lambda *a: (True, ""),
            "inputs": "x",
            "output_schema": {},
            "dependencies": [],
            "obligation": "required",
            "run": lambda *a: {},
        }
        bad_budgets = (
            {},
            {"max_judge_calls_per_review": -1},
            {"max_judge_calls_per_review": "1"},
            {"max_judge_calls_per_review": True},
        )
        for bad in bad_budgets:
            with self.assertRaises(ValueError, msg=bad):
                registry.register(dict(base, budget=bad))
        self.assertNotIn("spendy", registry.MODULES)


class PolicyTest(unittest.TestCase):
    def test_review_aggregates(self) -> None:
        from quality import policy

        verdict, reasons = policy.aggregate_review([], {"k": "fix"}, {})
        self.assertEqual("repair", verdict)
        self.assertTrue(reasons)
        verdict, reasons = policy.aggregate_review(
            [{"module": "counterevidence", "outcome": "unavailable", "summary": "x"}],
            {},
            {},
        )
        self.assertEqual("incomplete", verdict)
        self.assertTrue(any("counterevidence" in r for r in reasons))
        verdict, _ = policy.aggregate_review(
            [{"module": "counterevidence", "outcome": "failed", "summary": "x"}],
            {},
            {},
        )
        self.assertEqual("clean", verdict)
        tasks = {"k": {"status": "open"}}
        verdict, reasons = policy.aggregate_review([], {}, tasks, skipped=True)
        self.assertEqual("deferred", verdict)
        self.assertTrue(any("k" in r for r in reasons))
        verdict, _ = policy.aggregate_review([], {}, {}, skipped=True)
        self.assertEqual("clean", verdict)

    def test_memo_aggregates_and_labels(self) -> None:
        from quality import policy

        self.assertEqual(
            "revision-required", policy.aggregate_memo(False, "unavailable")
        )
        self.assertEqual("revision-required", policy.aggregate_memo(True, "failed"))
        self.assertEqual(
            "assessment-incomplete", policy.aggregate_memo(True, "unavailable")
        )
        self.assertEqual("ready-for-review", policy.aggregate_memo(True, "passed"))
        self.assertEqual(
            "MEMO passes", policy.primary_memo_label(True, [], "passed")
        )
        self.assertEqual(
            "MEMO fails", policy.primary_memo_label(False, [], "failed")
        )
        self.assertEqual(
            "MEMO fails", policy.primary_memo_label(False, ["rule"], "unavailable")
        )
        self.assertEqual(
            "MEMO incomplete: claims check unavailable",
            policy.primary_memo_label(False, [], "unavailable"),
        )


class RecordsTest(unittest.TestCase):
    def test_versions_bind_memo_and_evidence(self) -> None:
        memo = {"conclusion": "Yes.", "findings": [], "gaps": ""}
        self.assertEqual(records.memo_hash(memo), records.memo_hash(dict(memo)))
        self.assertNotEqual(
            records.memo_hash(memo), records.memo_hash({**memo, "gaps": "x"})
        )
        self.assertEqual(
            {"filed": 0, "hash": records.memo_hash(memo)},
            records.memo_version({"memo_history": []}, memo),
        )
        facts = {
            "facts": [
                {
                    "id": "F1",
                    "document_id": "D",
                    "page": 1,
                    "quote": "q",
                    "stance": "supports",
                }
            ]
        }
        first = records.evidence_fingerprint(facts)
        facts["facts"][0]["stance"] = "opposes"
        self.assertNotEqual(first, records.evidence_fingerprint(facts))

    def test_unknown_outcomes_fail_loud(self) -> None:
        with self.assertRaises(ValueError):
            records.assessment(
                module="m",
                module_version="1",
                memo_version={},
                evidence_version="x",
                outcome="maybe",
            )


class DetectionTest(QCCase):
    def outcomes(self, result):
        return {
            a["module"]: a["outcome"] for a in result["review"]["assessments"]
        }

    def test_ignored_opposing_evidence_is_an_objection(self) -> None:
        result = self.review(self.memo([SUPPORTS]))
        assessments = {
            a["module"]: a for a in result["review"]["assessments"]
        }
        self.assertEqual("failed", assessments["counterevidence"]["outcome"])
        (objection,) = assessments["counterevidence"]["findings"]
        self.assertEqual("finding:timing", objection["unit_id"])
        self.assertEqual("F2", objection["fact_id"])
        self.assertIn("F2", objection["reason"])

    def test_cited_opposing_evidence_passes(self) -> None:
        result = self.review(self.memo([SUPPORTS, OPPOSES]))
        self.assertEqual("passed", self.outcomes(result)["counterevidence"])
        self.assertEqual("clean", result["verdict"])

    def test_not_proven_findings_are_not_flagged(self) -> None:
        memo = self.memo([SUPPORTS])
        memo["findings"][0]["status"] = "unresolved"
        result = self.review(memo)
        self.assertEqual("passed", self.outcomes(result)["counterevidence"])

    def test_no_opposing_evidence_is_inapplicable(self) -> None:
        self.case.data["facts"] = [
            f for f in self.case.data["facts"] if f.get("stance") != "opposes"
        ]
        result = self.review(self.memo([SUPPORTS]))
        assessments = {
            a["module"]: a for a in result["review"]["assessments"]
        }
        self.assertEqual("inapplicable", assessments["counterevidence"]["outcome"])
        self.assertIn("no opposing", assessments["counterevidence"]["summary"])
        self.assertEqual("inapplicable", assessments["objection-materiality"]["outcome"])
        self.assertEqual([], materiality_calls(self.fake))
        self.assertEqual("clean", result["verdict"])

    def test_assessments_bind_memo_and_evidence_versions(self) -> None:
        memo = self.memo([SUPPORTS])
        result = self.review(memo)
        for assessment in result["review"]["assessments"]:
            self.assertEqual(
                records.memo_version(self.case.data, memo),
                assessment["target"]["memo"],
            )
            self.assertEqual(
                records.evidence_fingerprint(self.case.data),
                assessment["target"]["evidence"],
            )


class MaterialityTest(QCCase):
    def materiality(self, result):
        return next(
            a
            for a in result["review"]["assessments"]
            if a["module"] == "objection-materiality"
        )

    def test_material_objection_fails(self) -> None:
        self.use_material([{"id": "F2", "material": True, "reason": "cuts scope"}])
        result = self.review(self.memo([SUPPORTS]))
        assessment = self.materiality(result)
        self.assertEqual("failed", assessment["outcome"])
        self.assertEqual(
            [{"fact_id": "F2", "material": True, "reason": "cuts scope"}],
            assessment["findings"],
        )

    def test_immaterial_objection_passes(self) -> None:
        self.use_material([{"id": "F2", "material": False, "reason": "in gaps"}])
        result = self.review(self.memo([SUPPORTS]))
        self.assertEqual("passed", self.materiality(result)["outcome"])

    def test_malformed_judge_output_is_unavailable(self) -> None:
        original = self.fake.responder

        def broken(kind, payload):
            if kind == "respond" and "COUNTEREVIDENCE MATERIALITY" in json.dumps(
                payload
            ):
                return {
                    "output_text": '{"verdicts": [{"id": "F2"}]}',
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                }
            return original(kind, payload)

        self.fake.responder = broken
        result = self.review(self.memo([SUPPORTS]))
        assessment = self.materiality(result)
        self.assertEqual("unavailable", assessment["outcome"])
        self.assertIn("required fields", assessment["summary"])

    def test_missing_resolutions_are_unavailable(self) -> None:
        original = self.fake.responder

        def broken(kind, payload):
            if kind == "respond" and "COUNTEREVIDENCE MATERIALITY" in json.dumps(
                payload
            ):
                return {
                    "output_text": json.dumps(
                        {
                            "verdicts": [
                                {
                                    "id": "F2",
                                    "material": True,
                                    "reason": "cuts scope",
                                }
                            ]
                        }
                    ),
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                }
            return original(kind, payload)

        self.fake.responder = broken
        result = self.review(self.memo([SUPPORTS]))
        assessment = self.materiality(result)
        self.assertEqual("unavailable", assessment["outcome"])
        self.assertIn("resolutions", assessment["summary"])

    def test_provider_failure_is_unavailable(self) -> None:
        from tests.fake_model import Refused

        original = self.fake.responder

        def broken(kind, payload):
            if kind == "respond" and "COUNTEREVIDENCE MATERIALITY" in json.dumps(
                payload
            ):
                raise Refused("judge overloaded")
            return original(kind, payload)

        self.fake.responder = broken
        result = self.review(self.memo([SUPPORTS]))
        assessment = self.materiality(result)
        self.assertEqual("unavailable", assessment["outcome"])
        self.assertIn("judge request failed", assessment["summary"])

    def test_materiality_judge_calls_are_ledgered(self) -> None:
        self.use_material([{"id": "F2", "material": True, "reason": "x"}])
        self.review(self.memo([SUPPORTS]))
        totals = usage.totals(self.root, self.run_id)
        self.assertIn("materiality_judge", totals["by_purpose"])
        entry = totals["by_purpose"]["materiality_judge"]
        self.assertEqual(1, entry["calls"])
        self.assertTrue(entry["input_tokens"] > 0)
        self.assertTrue(tokens_spent(self.root, self.run_id) > 0)


class ObligationTest(QCCase):
    def test_advisory_unavailable_does_not_block(self) -> None:
        def run(ctx, memo, review):
            return records.assessment(
                module="test-advisory",
                module_version="1",
                memo_version=review["memo_version"],
                evidence_version=review["evidence_version"],
                outcome="unavailable",
                summary="advisory source down",
            )

        registry.register(
            {
                "name": "test-advisory",
                "version": "1",
                "kind": "deterministic",
                "purpose": "test double",
                "applies": lambda ctx, memo, prior: (True, ""),
                "inputs": "none",
                "output_schema": {},
                "dependencies": [],
                "budget": {"max_judge_calls_per_review": 0},
                "obligation": "advisory",
                "run": run,
            }
        )
        self.addCleanup(lambda: registry.MODULES.pop("test-advisory", None))
        result = self.review(self.memo([SUPPORTS, OPPOSES]))
        self.assertEqual("clean", result["verdict"])
        assessments = {
            a["module"]: a for a in result["review"]["assessments"]
        }
        self.assertEqual("unavailable", assessments["test-advisory"]["outcome"])
        self.assertIn("advisory source down", assessments["test-advisory"]["summary"])

    def test_advisory_chain_records_unexecuted_without_blocking(self) -> None:
        def down(ctx, memo, review):
            raise AssertionError("must not run with its dependency down")

        def up(ctx, memo, review):
            return records.assessment(
                module="test-advisory-a",
                module_version="1",
                memo_version=review["memo_version"],
                evidence_version=review["evidence_version"],
                outcome="unavailable",
                summary="advisory source down",
            )

        for name, run, deps in (
            ("test-advisory-b", down, ["test-advisory-a"]),
            ("test-advisory-a", up, []),
        ):
            registry.register(
                {
                    "name": name,
                    "version": "1",
                    "kind": "deterministic",
                    "purpose": "test double",
                    "applies": lambda ctx, memo, prior: (True, ""),
                    "inputs": "none",
                    "output_schema": {},
                    "dependencies": deps,
                    "budget": {"max_judge_calls_per_review": 0},
                    "obligation": "advisory",
                    "run": run,
                }
            )
            self.addCleanup(lambda n=name: registry.MODULES.pop(n, None))
        result = self.review(self.memo([SUPPORTS, OPPOSES]))
        self.assertEqual("clean", result["verdict"])
        assessments = {
            a["module"]: a for a in result["review"]["assessments"]
        }
        self.assertEqual("unavailable", assessments["test-advisory-a"]["outcome"])
        self.assertEqual("unexecuted", assessments["test-advisory-b"]["outcome"])
        self.assertIn(
            "dependency test-advisory-a",
            assessments["test-advisory-b"]["summary"],
        )

    def test_unknown_module_outcome_is_conservatively_required(self) -> None:
        from quality import policy

        verdict, _ = policy.aggregate_review(
            [{"module": "no-such-module", "outcome": "unavailable", "summary": "x"}],
            {},
            {},
        )
        self.assertEqual("incomplete", verdict)


class RepairLifecycleTest(QCCase):
    KEY = "counterevidence:finding:timing:F2"

    def test_material_defect_opens_a_repair_task(self) -> None:
        self.use_material([{"id": "F2", "material": True, "reason": "cuts scope"}])
        result = self.review(self.memo([SUPPORTS]))
        self.assertEqual("repair", result["verdict"])
        self.assertEqual(1, len(result["repair_feedback"]))
        self.assertIn("Resolution:", result["repair_feedback"][0])
        self.assertIn("F2", result["repair_feedback"][0])
        task = self.case.data["repair_tasks"][self.KEY]
        self.assertEqual(
            ("counterevidence", "finding:timing", "open", 1, None),
            (
                task["module"],
                task["target"]["unit"],
                task["status"],
                task["attempts"],
                task["disposition"],
            ),
        )
        self.assertIn("F2", task["description"])
        self.assertIn("gaps", task["resolution"])
        self.assertEqual([self.KEY], result["review"]["tasks_opened"])
        self.assertEqual([self.KEY], result["review"]["open_tasks"])

    def test_citation_alone_does_not_resolve_an_unaddressed_task(self) -> None:
        self.use_material([{"id": "F2", "material": True, "reason": "cuts scope"}])
        self.review(self.memo([SUPPORTS]))
        # The citation is added but the wording still overstates: the
        # re-judge (default: unaddressed) keeps the task open.
        result = self.review(self.memo([SUPPORTS, OPPOSES]))
        self.assertEqual("repair", result["verdict"])
        task = self.case.data["repair_tasks"][self.KEY]
        self.assertEqual("open", task["status"])
        self.assertIsNone(task["disposition"])
        self.assertEqual([], result["review"]["tasks_resolved"])
        self.assertIn("still unaddressed", " ".join(task["history"]))

    def test_addressed_wording_resolves_the_task(self) -> None:
        key = self.KEY
        self.use_material(
            [{"id": "F2", "material": True, "reason": "cuts scope"}],
            addressed=[{"id": key, "addressed": True, "reason": "narrowed"}],
        )
        self.review(self.memo([SUPPORTS]))
        result = self.review(self.memo([SUPPORTS, OPPOSES], conclusion="v2."))
        self.assertEqual("clean", result["verdict"])
        task = self.case.data["repair_tasks"][key]
        self.assertEqual("resolved", task["status"])
        self.assertEqual("resolved", task["disposition"])
        self.assertIn("addressed", " ".join(task["history"]))
        self.assertEqual([key], result["review"]["tasks_resolved"])

    def test_gaps_covered_objection_resolves_as_qualified(self) -> None:
        self.use_material([{"id": "F2", "material": True, "reason": "cuts scope"}])
        self.review(self.memo([SUPPORTS]))
        self.use_material([{"id": "F2", "material": False, "reason": "in gaps now"}])
        result = self.review(self.memo([SUPPORTS], gaps="Timing is uncertain."))
        self.assertEqual("clean", result["verdict"])
        task = self.case.data["repair_tasks"][self.KEY]
        self.assertEqual("resolved", task["status"])
        self.assertEqual("qualified", task["disposition"])

    def test_renamed_finding_does_not_close_the_task(self) -> None:
        self.use_material([{"id": "F2", "material": True, "reason": "cuts scope"}])
        self.review(self.memo([SUPPORTS]))
        renamed = self.memo([SUPPORTS], conclusion="v2.")
        renamed["findings"][0]["item"] = "timing_renamed"
        result = self.review(renamed)
        self.assertEqual("repair", result["verdict"])
        task = self.case.data["repair_tasks"][self.KEY]
        self.assertEqual("open", task["status"])
        self.assertEqual([], result["review"]["tasks_resolved"])

    def test_unavailable_judge_with_clean_detection_keeps_the_task_open(self) -> None:
        from tests.fake_model import Refused

        self.use_material([{"id": "F2", "material": True, "reason": "cuts scope"}])
        self.review(self.memo([SUPPORTS]))
        original = self.fake.responder

        def broken(kind, payload):
            if kind == "respond" and "COUNTEREVIDENCE MATERIALITY" in json.dumps(
                payload
            ):
                raise Refused("judge overloaded")
            return original(kind, payload)

        self.fake.responder = broken
        result = self.review(self.memo([SUPPORTS, OPPOSES], conclusion="v2."))
        self.assertEqual("incomplete", result["verdict"])
        task = self.case.data["repair_tasks"][self.KEY]
        self.assertEqual("open", task["status"])
        self.assertEqual([], result["review"]["tasks_resolved"])

    def test_material_again_reopens_a_qualified_task(self) -> None:
        material = [{"id": "F2", "material": True, "reason": "cuts scope"}]
        immaterial = [{"id": "F2", "material": False, "reason": "in gaps"}]
        self.fake = support.scripted(
            self.root,
            [],
            material=lambda text: immaterial if "v2" in text else material,
        )
        self.ctx = tools.Context(
            self.fake.client, self.root, self.run_id, self.case, True
        )
        self.review(self.memo([SUPPORTS], gaps="v1"))
        self.review(self.memo([SUPPORTS], gaps="v2"))
        task = self.case.data["repair_tasks"][self.KEY]
        self.assertEqual("resolved", task["status"])
        self.assertEqual("qualified", task["disposition"])
        result = self.review(self.memo([SUPPORTS], gaps="v3"))
        self.assertEqual("repair", result["verdict"])
        self.assertEqual("open", task["status"])
        self.assertIsNone(task["disposition"])
        self.assertIn("reopened", " ".join(task["history"]))

    def test_third_strike_marks_unresolved_and_proceeds(self) -> None:
        self.use_material([{"id": "F2", "material": True, "reason": "cuts scope"}])
        first = self.review(self.memo([SUPPORTS], conclusion="v1."))
        second = self.review(self.memo([SUPPORTS], conclusion="v2."))
        third = self.review(self.memo([SUPPORTS], conclusion="v3."))
        self.assertEqual("repair", first["verdict"])
        self.assertEqual("repair", second["verdict"])
        self.assertEqual("clean", third["verdict"])
        task = self.case.data["repair_tasks"][self.KEY]
        self.assertEqual("unresolved", task["status"])
        self.assertEqual("unresolved", task["disposition"])
        self.assertEqual(3, task["attempts"])
        self.assertEqual([self.KEY], third["review"]["unresolved_tasks"])

    def test_identical_resubmit_costs_nothing_and_repeats(self) -> None:
        self.use_material([{"id": "F2", "material": True, "reason": "cuts scope"}])
        memo = self.memo([SUPPORTS])
        first = self.review(memo)
        second = self.review(copy.deepcopy(memo))
        self.assertEqual("QC1", first["review"]["id"])
        self.assertEqual("QC2", second["review"]["id"])
        self.assertEqual("QC1", second["review"]["duplicate_of"])
        self.assertEqual(first["repair_feedback"], second["repair_feedback"])
        self.assertEqual(1, len(materiality_calls(self.fake)))
        task = self.case.data["repair_tasks"][self.KEY]
        self.assertEqual(1, task["attempts"])
        self.assertTrue(
            all(
                a["outcome"] == "unexecuted"
                for a in second["review"]["assessments"]
            )
        )

    def test_regression_is_flagged(self) -> None:
        self.use_material([{"id": "F2", "material": True, "reason": "cuts scope"}])
        self.review(self.memo([SUPPORTS, OPPOSES]))
        result = self.review(self.memo([SUPPORTS], conclusion="dropped the cite."))
        self.assertEqual("repair", result["verdict"])
        self.assertEqual(["finding:timing"], result["review"]["regressions"])
        self.assertTrue(result["repair_feedback"][0].startswith("REGRESSION:"))
        task = self.case.data["repair_tasks"][self.KEY]
        self.assertIn("regressed", " ".join(task["history"]))

    def test_unavailable_materiality_is_incomplete_not_clean(self) -> None:
        from tests.fake_model import Refused

        original = self.fake.responder

        def broken(kind, payload):
            if kind == "respond" and "COUNTEREVIDENCE MATERIALITY" in json.dumps(
                payload
            ):
                raise Refused("judge overloaded")
            return original(kind, payload)

        self.fake.responder = broken
        result = self.review(self.memo([SUPPORTS]))
        self.assertEqual("incomplete", result["verdict"])
        self.assertEqual([], result["repair_feedback"])
        self.assertTrue(
            any("objection-materiality" in r for r in result["review"]["state_reasons"])
        )
        task = self.case.data["repair_tasks"][self.KEY]
        self.assertEqual("open", task["status"])
        self.assertEqual(0, task["attempts"])
        self.assertIn("pending", " ".join(task["history"]))


class ScopeGuardTest(QCCase):
    KEY = "checklist-scope:timing"

    def test_removed_question_demands_restore_or_escalate(self) -> None:
        self.review(self.memo([SUPPORTS, OPPOSES]))
        del self.case.data["checklist"]["timing"]
        result = self.review(self.memo([SUPPORTS, OPPOSES], conclusion="narrower."))
        self.assertEqual("repair", result["verdict"])
        self.assertIn("restore the original question", result["repair_feedback"][0])
        task = self.case.data["repair_tasks"][self.KEY]
        self.assertEqual("open", task["status"])

    def test_restored_question_resolves_the_task(self) -> None:
        self.review(self.memo([SUPPORTS, OPPOSES]))
        del self.case.data["checklist"]["timing"]
        self.review(self.memo([SUPPORTS, OPPOSES], conclusion="narrower."))
        self.case.data["checklist"]["timing"] = support.checklist_item("timing")
        result = self.review(self.memo([SUPPORTS, OPPOSES], conclusion="restored."))
        self.assertEqual("clean", result["verdict"])
        task = self.case.data["repair_tasks"][self.KEY]
        self.assertEqual("resolved", task["status"])

    def test_baseline_is_captured_at_acceptance_and_never_moves(self) -> None:
        self.assertEqual(
            {"timing": "Question for timing"}, self.case.data["scope_baseline"]
        )
        item = support.checklist_item("timing")
        item["question"] = "Question for timing, weakened"
        self.case.set_checklist([item])
        self.assertEqual(
            {"timing": "Question for timing"}, self.case.data["scope_baseline"]
        )

    def test_reworded_question_demands_amendment_or_restore(self) -> None:
        self.review(self.memo([SUPPORTS, OPPOSES]))
        item = support.checklist_item("timing")
        item["question"] = "Question for timing, weakened"
        self.case.set_checklist([item])
        result = self.review(self.memo([SUPPORTS, OPPOSES], conclusion="narrower."))
        self.assertEqual("repair", result["verdict"])
        self.assertIn("reworded", result["repair_feedback"][0])
        task = self.case.data["repair_tasks"][self.KEY]
        self.assertEqual("open", task["status"])
        self.assertEqual("changed", task["kind"])

    def test_recorded_amendment_clears_the_rewording(self) -> None:
        self.review(self.memo([SUPPORTS, OPPOSES]))
        item = support.checklist_item("timing")
        item["question"] = "Question for timing, refined"
        item["amendment"] = "Same obligation; narrowed to the dated record."
        self.case.set_checklist([item])
        result = self.review(self.memo([SUPPORTS, OPPOSES], conclusion="refined."))
        self.assertEqual("clean", result["verdict"])

    def test_split_with_mapping_is_allowed(self) -> None:
        del self.case.data["scope_baseline"]
        self.case.set_checklist(
            [support.checklist_item("timing"), support.checklist_item("scope_only")]
        )
        first = dict(support.checklist_item("scope_a"), amends=["scope_only"])
        second = dict(support.checklist_item("scope_b"), amends=["scope_only"])
        self.case.set_checklist([support.checklist_item("timing"), first, second])
        result = self.review(self.memo([SUPPORTS, OPPOSES]))
        self.assertEqual("clean", result["verdict"])

    def test_unmapped_split_is_flagged(self) -> None:
        del self.case.data["scope_baseline"]
        self.case.set_checklist(
            [support.checklist_item("timing"), support.checklist_item("scope_only")]
        )
        self.case.set_checklist(
            [
                support.checklist_item("timing"),
                support.checklist_item("scope_a"),
                support.checklist_item("scope_b"),
            ]
        )
        result = self.review(self.memo([SUPPORTS, OPPOSES]))
        self.assertEqual("repair", result["verdict"])
        task = self.case.data["repair_tasks"]["checklist-scope:scope_only"]
        self.assertEqual("open", task["status"])
        self.assertEqual("removed", task["kind"])

    def test_recurring_removal_reopens_the_task(self) -> None:
        self.review(self.memo([SUPPORTS, OPPOSES]))
        del self.case.data["checklist"]["timing"]
        self.review(self.memo([SUPPORTS, OPPOSES], conclusion="narrower."))
        self.case.data["checklist"]["timing"] = support.checklist_item("timing")
        restored = self.review(self.memo([SUPPORTS, OPPOSES], conclusion="restored."))
        self.assertEqual("clean", restored["verdict"])
        del self.case.data["checklist"]["timing"]
        result = self.review(self.memo([SUPPORTS, OPPOSES], conclusion="narrower."))
        self.assertEqual("repair", result["verdict"])
        task = self.case.data["repair_tasks"][self.KEY]
        self.assertEqual("open", task["status"])
        self.assertIsNone(task["disposition"])
        self.assertIn("reopened", " ".join(task["history"]))


class ReviewLimitsTest(QCCase):
    def test_run_review_budget_skips_with_unexecuted(self) -> None:
        self.use_material([{"id": "F2", "material": True, "reason": "x"}])
        verdicts = []
        for n in range(6):
            result = self.review(self.memo([SUPPORTS], conclusion=f"v{n}."))
            verdicts.append(result["verdict"])
        self.assertEqual(
            ["repair", "repair", "clean", "clean", "clean", "clean"], verdicts
        )
        sixth = self.case.data["qc_reviews"][-1]
        self.assertIn("budget exhausted", sixth["skipped"])
        self.assertTrue(
            all(a["outcome"] == "unexecuted" for a in sixth["assessments"])
        )
        self.assertEqual(5, self.case.data["qc_state"]["reviews"])

    def test_token_budget_preempts_semantic_modules(self) -> None:
        self.set_env("RUN_TOKEN_BUDGET", "1")
        result = self.review(self.memo([SUPPORTS]))
        assessments = {
            a["module"]: a for a in result["review"]["assessments"]
        }
        self.assertEqual("failed", assessments["counterevidence"]["outcome"])
        self.assertEqual("unexecuted", assessments["objection-materiality"]["outcome"])
        self.assertIn("budget exhausted", assessments["objection-materiality"]["summary"])
        self.assertEqual([], materiality_calls(self.fake))
        self.assertEqual("incomplete", result["verdict"])
        task = self.case.data["repair_tasks"][
            "counterevidence:finding:timing:F2"
        ]
        self.assertEqual("open", task["status"])

    def test_declared_calls_drive_preemption_not_kind(self) -> None:
        from quality import modules  # noqa: F401  (register builtins)

        self.set_env("RUN_TOKEN_BUDGET", "1")
        ran = []
        registry.register(
            {
                "name": "test-metered-deterministic",
                "version": "1",
                "kind": "deterministic",
                "purpose": "test double",
                "applies": lambda ctx, memo, prior: (True, ""),
                "inputs": "none",
                "output_schema": {},
                "dependencies": [],
                "budget": {"max_judge_calls_per_review": 1},
                "obligation": "advisory",
                "run": lambda ctx, memo, review: ran.append(1) or {},
            }
        )
        self.addCleanup(lambda: registry.MODULES.pop("test-metered-deterministic", None))
        result = self.review(self.memo([SUPPORTS]))
        assessments = {
            a["module"]: a for a in result["review"]["assessments"]
        }
        self.assertEqual(
            "unexecuted", assessments["test-metered-deterministic"]["outcome"]
        )
        self.assertEqual([], ran)

    def test_invalid_budget_fails_loud_in_review(self) -> None:
        self.set_env("RUN_TOKEN_BUDGET", "bogus")
        with self.assertRaises(ConfigError):
            self.review(self.memo([SUPPORTS]))

    def test_module_crash_is_unavailable_not_fatal(self) -> None:
        spec = registry.MODULES["counterevidence"]
        original = spec["run"]
        self.addCleanup(lambda: spec.update(run=original))

        def crash(ctx, memo, review):
            raise RuntimeError("boom")

        spec.update(run=crash)
        result = self.review(self.memo([SUPPORTS]))
        assessments = {
            a["module"]: a for a in result["review"]["assessments"]
        }
        self.assertEqual("unavailable", assessments["counterevidence"]["outcome"])
        self.assertIn("boom", assessments["counterevidence"]["summary"])
        self.assertEqual("unexecuted", assessments["objection-materiality"]["outcome"])
        self.assertIn(
            "dependency counterevidence",
            assessments["objection-materiality"]["summary"],
        )
        self.assertEqual("incomplete", result["verdict"])

    def test_invalid_module_output_is_unavailable(self) -> None:
        spec = registry.MODULES["counterevidence"]
        original = spec["run"]
        self.addCleanup(lambda: spec.update(run=original))
        spec.update(run=lambda ctx, memo, review: {"outcome": "bogus"})
        result = self.review(self.memo([SUPPORTS]))
        assessments = {
            a["module"]: a for a in result["review"]["assessments"]
        }
        self.assertEqual("unavailable", assessments["counterevidence"]["outcome"])


class SubmitIntegrationTest(QCCase):
    def submit(self, citations, **memo_kwargs):
        return submit_memo.run(self.ctx, self.memo(citations, **memo_kwargs))

    def test_repair_returns_without_filing_or_refusal(self) -> None:
        self.use_material([{"id": "F2", "material": True, "reason": "cuts scope"}])
        result = self.submit([SUPPORTS])
        self.assertEqual("memo returned for repair", result["error"])
        self.assertEqual("qc", result["check"])
        self.assertTrue(result["problems"])
        self.assertEqual("QC1", result["qc_review"])
        self.assertIsNone(self.case.data["memo"])
        self.assertEqual(0, self.case.memo_refusals())
        self.assertEqual([], claims_calls(self.fake))
        self.assertEqual(1, len(self.case.data["qc_reviews"]))

    def test_repair_then_revise_then_file(self) -> None:
        self.use_material(
            [{"id": "F2", "material": True, "reason": "cuts scope"}],
            addressed=[
                {
                    "id": "counterevidence:finding:timing:F2",
                    "addressed": True,
                    "reason": "narrowed",
                }
            ],
        )
        first = self.submit([SUPPORTS])
        self.assertEqual("qc", first["check"])
        filed = self.submit([SUPPORTS, OPPOSES])
        self.assertEqual("awaiting_signoff", filed["status"])
        self.assertEqual("passed", filed["outcome"])
        self.assertEqual("awaiting_signoff", self.case.data["memo"]["status"])
        self.assertEqual(0, self.case.memo_refusals())
        task = self.case.data["repair_tasks"]["counterevidence:finding:timing:F2"]
        self.assertEqual("resolved", task["status"])

    def test_unresolved_proceeds_to_claims(self) -> None:
        self.use_material([{"id": "F2", "material": True, "reason": "cuts scope"}])
        self.submit([SUPPORTS], conclusion="v1.")
        self.submit([SUPPORTS], conclusion="v2.")
        third = self.submit([SUPPORTS], conclusion="v3.")
        self.assertEqual("awaiting_signoff", third["status"])
        self.assertEqual("passed", third["outcome"])

    def test_filed_result_carries_qc_obligations(self) -> None:
        filed = self.submit([SUPPORTS, OPPOSES])
        self.assertEqual(
            {"enabled": True, "reviews": 1, "open": [], "unresolved": []},
            filed["qc"],
        )
        self.use_material([{"id": "F2", "material": True, "reason": "cuts scope"}])
        self.submit([SUPPORTS], conclusion="v1.")
        self.submit([SUPPORTS], conclusion="v2.")
        third = self.submit([SUPPORTS], conclusion="v3.")
        self.assertEqual(
            ["counterevidence:finding:timing:F2"], third["qc"]["unresolved"]
        )
        self.assertEqual([], third["qc"]["open"])

    def test_incomplete_review_pauses_submit(self) -> None:
        from tests.fake_model import Refused

        original = self.fake.responder

        def broken(kind, payload):
            if kind == "respond" and "COUNTEREVIDENCE MATERIALITY" in json.dumps(
                payload
            ):
                raise Refused("judge overloaded")
            return original(kind, payload)

        self.fake.responder = broken
        result = self.submit([SUPPORTS])
        self.assertEqual("paused", result["status"])
        self.assertEqual("qc_incomplete", result["reason"])
        self.assertEqual("qc", result["check"])
        self.assertEqual("incomplete", result["outcome"])
        self.assertIn("incomplete", result["error"])
        self.assertIsNone(self.case.data["memo"])
        self.assertEqual(0, self.case.memo_refusals())
        self.assertEqual([], claims_calls(self.fake))

    def test_incomplete_review_retries_after_recovery(self) -> None:
        from tests.fake_model import Refused

        original = self.fake.responder

        def broken(kind, payload):
            if kind == "respond" and "COUNTEREVIDENCE MATERIALITY" in json.dumps(
                payload
            ):
                raise Refused("judge overloaded")
            return original(kind, payload)

        self.fake.responder = broken
        memo = self.memo([SUPPORTS])
        first = self.review(memo)
        self.assertEqual("incomplete", first["verdict"])
        self.fake.responder = original
        second = self.review(copy.deepcopy(memo))
        self.assertEqual("QC2", second["review"]["id"])
        self.assertNotIn("duplicate_of", second["review"])
        self.assertEqual("clean", second["verdict"])
        self.assertEqual(2, len(materiality_calls(self.fake)))
        task = self.case.data["repair_tasks"]["counterevidence:finding:timing:F2"]
        self.assertEqual("qualified", task["disposition"])

    def test_deferred_review_escalates_with_obligations(self) -> None:
        self.use_material([{"id": "F2", "material": True, "reason": "cuts scope"}])
        first = self.submit([SUPPORTS], conclusion="v1.")
        self.assertEqual("qc", first["check"])
        self.case.data["qc_state"]["reviews"] = controller.MAX_QC_REVIEWS_PER_RUN
        deferred = self.submit([SUPPORTS], conclusion="v2.")
        self.assertTrue(deferred.get("escalated"))
        self.assertEqual("needs_input", deferred["status"])
        self.assertEqual("timing", deferred["item"])
        (request,) = self.case.data["requests"]
        self.assertEqual("qc_exhausted", request["rule"])
        self.assertIn("counterevidence:finding:timing:F2", request["need"])
        blocked = self.submit([SUPPORTS], conclusion="v2.")
        self.assertEqual("memo refused", blocked["error"])
        self.assertEqual("memo", blocked["check"])

    def test_answered_deferral_records_a_waiver_and_proceeds(self) -> None:
        self.use_material([{"id": "F2", "material": True, "reason": "cuts scope"}])
        self.submit([SUPPORTS], conclusion="v1.")
        self.case.data["qc_state"]["reviews"] = controller.MAX_QC_REVIEWS_PER_RUN
        deferred = self.submit([SUPPORTS], conclusion="v2.")
        self.assertTrue(deferred.get("escalated"))
        self.case.answer_request(
            self.case.data["requests"][-1]["id"], "accept the limitation"
        )
        filed = self.submit([SUPPORTS], conclusion="v2.")
        self.assertEqual("awaiting_signoff", filed["status"])
        self.assertEqual(1, len(self.case.data["requests"]))
        task = self.case.data["repair_tasks"]["counterevidence:finding:timing:F2"]
        self.assertEqual("unresolved", task["status"])
        self.assertEqual("waived", task["disposition"])
        self.assertIn("accept the limitation", " ".join(task["history"]))
        self.assertEqual(
            ["counterevidence:finding:timing:F2"], filed["qc"]["unresolved"]
        )

    def test_clean_memo_proceeds_to_claims(self) -> None:
        filed = self.submit([SUPPORTS, OPPOSES])
        self.assertEqual("awaiting_signoff", filed["status"])
        self.assertEqual(1, len(self.case.data["qc_reviews"]))
        self.assertEqual("clean", self.case.data["qc_reviews"][0]["verdict"])

    def test_rules_failure_skips_qc(self) -> None:
        result = submit_memo.run(self.ctx, {"conclusion": "x", "findings": []})
        self.assertEqual("memo refused", result["error"])
        self.assertEqual("memo", result["check"])
        self.assertNotIn("qc_reviews", self.case.data)

    def test_verification_off_skips_qc(self) -> None:
        ctx = tools.Context(
            self.fake.client, self.root, self.run_id, self.case, False
        )
        result = submit_memo.run(ctx, self.memo([SUPPORTS]))
        self.assertEqual("answered", result["status"])
        self.assertNotIn("qc_reviews", self.case.data)


class QCUITest(unittest.TestCase):
    def setUp(self) -> None:
        self.ui = support.console_ui()

    def test_qc_event_renders_repair_without_rejection(self) -> None:
        from tests.test_claims_v2 import synthetic_progress

        progress = synthetic_progress(
            "submit_memo",
            {"conclusion": "x"},
            {
                "error": "memo returned for repair",
                "check": "qc",
                "problems": ["finding timing ignores F2. Resolution: cite F2."],
                "qc_review": "QC1",
            },
            "running",
        )
        events = self.ui.build_events(progress, {"requests": []}, None)
        (qc,) = [
            e
            for e in events
            if e["event_type"] == "verification"
            and e["payload"].get("check") == "qc"
        ]
        self.assertFalse(qc["payload"]["passed"])
        self.assertEqual(1, len(qc["payload"]["problems"]))
        self.assertEqual(0, self.ui.metrics(events, None)["rejected"])

    def test_incomplete_pause_reaches_run_finished(self) -> None:
        from tests.test_claims_v2 import synthetic_progress

        progress = synthetic_progress(
            "submit_memo",
            {"conclusion": "x"},
            {
                "status": "paused",
                "reason": "qc_incomplete",
                "check": "qc",
                "outcome": "incomplete",
                "problems": ["quality review incomplete: materiality unavailable"],
                "error": "quality review incomplete: retry submit_memo.",
                "qc_review": "QC1",
            },
            "paused",
        )
        events = self.ui.build_events(progress, {"requests": []}, None)
        (finished,) = [
            e for e in events if e["event_type"] == "run_finished"
        ]
        self.assertEqual("paused", finished["payload"]["status"])
        self.assertEqual("qc_incomplete", finished["payload"]["reason"])
        self.assertIn("incomplete", finished["payload"]["detail"])
        (qc,) = [
            e
            for e in events
            if e["event_type"] == "verification"
            and e["payload"].get("check") == "qc"
        ]
        self.assertEqual("incomplete", qc["payload"]["outcome"])
        self.assertEqual("QC1", qc["payload"]["qc_review"])
        memo_passed = [
            e
            for e in events
            if e["event_type"] == "verification"
            and e["payload"].get("check") == "memo"
        ]
        self.assertTrue(memo_passed)
        self.assertEqual(0, self.ui.metrics(events, None)["rejected"])

    def test_surfaces_agree_on_failed_and_incomplete(self) -> None:
        from scripts import verify
        from tests.test_claims_v2 import synthetic_progress

        failed_submit = {
            "error": "memo refused",
            "check": "claims",
            "outcome": "failed",
            "problems": ["timing: the finding is not supported"],
        }
        events = self.ui.build_events(
            synthetic_progress(
                "submit_memo", {"conclusion": "x"}, failed_submit, "running"
            ),
            {"requests": []},
            None,
        )
        (claims,) = [
            e
            for e in events
            if e["event_type"] == "verification"
            and e["payload"].get("check") == "claims"
        ]
        self.assertFalse(claims["payload"]["passed"])
        manual_failed = {
            "passed": False,
            "rules": [],
            "claims": ["timing: the finding is not supported"],
            "claims_outcome": "failed",
        }
        self.assertTrue(verify.format_memo(manual_failed).startswith("MEMO fails"))
        unavailable_submit = {
            "status": "paused",
            "reason": "verification_unavailable",
            "check": "claims",
            "outcome": "unavailable",
            "problems": ["the judge request failed"],
            "error": "claims check unavailable.",
        }
        events = self.ui.build_events(
            synthetic_progress(
                "submit_memo", {"conclusion": "x"}, unavailable_submit, "paused"
            ),
            {"requests": []},
            None,
        )
        (finished,) = [
            e for e in events if e["event_type"] == "run_finished"
        ]
        self.assertEqual("verification_unavailable", finished["payload"]["reason"])
        manual_unavailable = dict(manual_failed, claims_outcome="unavailable")
        self.assertTrue(
            verify.format_memo(manual_unavailable).startswith("MEMO incomplete")
        )

    def test_run_detail_carries_qc_history_for_the_card(self) -> None:
        import tempfile
        from pathlib import Path

        from tests.test_claims_v2 import synthetic_progress

        with tempfile.TemporaryDirectory() as directory:
            workshop = Path(directory)
            (workshop / "agent").mkdir(parents=True, exist_ok=True)
            (workshop / "agent/helper").write_text("")
            runs = workshop / "agent/workspace/runs/run-test"
            runs.mkdir(parents=True)
            (runs / "progress.json").write_text(
                json.dumps(
                    synthetic_progress(
                        "submit_memo", {"conclusion": "x"}, {"filed": False}, "running"
                    )
                )
            )
            records = workshop / "agent/workspace/case_records"
            records.mkdir(parents=True)
            (records / "run-test.json").write_text(
                json.dumps(
                    {
                        "requests": [],
                        "qc_reviews": [
                            {
                                "id": "QC1",
                                "verdict": "repair",
                                "assessments": [
                                    {
                                        "module": "counterevidence",
                                        "outcome": "failed",
                                        "summary": "1 finding(s) ignore opposing evidence",
                                    }
                                ],
                                "regressions": [],
                                "tasks_resolved": [],
                            }
                        ],
                        "repair_tasks": {
                            "counterevidence:finding:timing:F2": {
                                "id": "T1",
                                "status": "open",
                                "disposition": None,
                                "attempts": 1,
                                "description": "finding timing ignores F2",
                                "resolution": "cite F2",
                            }
                        },
                    }
                )
            )
            console = self.ui.Console(None)
            console.set_workshop(str(workshop))
            detail = console.run_detail("run-test")
            self.assertEqual("QC1", detail["case"]["qc_reviews"][0]["id"])
            self.assertIn(
                "counterevidence:finding:timing:F2", detail["case"]["repair_tasks"]
            )
