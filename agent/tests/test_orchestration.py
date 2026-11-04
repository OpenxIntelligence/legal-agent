"""Orchestration control plane: contracts, engines, loop behavior.

All drivers are scripted fakes: these tests prove planning,
ordering, budgets, invalidation, and approval mechanics —
never model judgment. Peer-dependent branches are tested by
injecting fakes, so the suite passes with or without the
peer's uncommitted modules.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from agent_kit.orchestration import contracts, engines
from agent_kit.orchestration import orchestrator as orch_module
from agent_kit.orchestration.engines import Engine
from agent_kit.orchestration.orchestrator import Orchestrator


class ScriptedDriver:
    """Per-engine scripted driver: {question_id: [responses]}.

    Exhausted queues repeat their last response (a stuck
    driver keeps finding the same thing); only never-scripted
    questions get clean defaults. Returning vacuous-clean
    passes after exhaustion would let engines clear gaps
    without doing work.
    """

    def __init__(self, scripts=None) -> None:
        self.scripts = scripts or {}
        self.last: dict[str, dict] = {}
        self.calls: list[tuple[str, str]] = []

    def _pop(self, method: str, question: dict, default: dict) -> dict:
        self.calls.append((method, question["id"]))
        queue = self.scripts.setdefault(question["id"], [])
        if queue:
            self.last[question["id"]] = queue.pop(0)
        return dict(self.last.get(question["id"], default))

    def gather(self, question, needs):
        return self._pop("gather", question, {"facts": [], "coverage": "complete"})

    def assess(self, question, evidence):
        return self._pop("assess", question, {"elements": []})

    def check(self, question):
        return self._pop("check", question, {"passed": True, "problems": []})

    def fix(self, question, defects):
        return self._pop("fix", question, {})

    def challenge(self, position):
        self.calls.append(("challenge", "-"))
        return {"counterarguments": "none found", "risks": "none stated"}


def drivers_for(scripts=None):
    scripts = scripts or {}
    one = ScriptedDriver(scripts)
    return {"research": one, "analysis": one, "verification": one,
            "repair": one, "review": one}


TRANSCRIPTS = {
    "VD-CONTRACT": {1: "Section 4. Payment is due within thirty days of delivery."},
    "VD-EMAIL": {1: "Rivera wrote that delivery slipped by two weeks."},
}


class ContractsTest(unittest.TestCase):
    def test_classify_each_type(self) -> None:
        self.assertEqual(
            "dispute_investigation",
            contracts.classify_task({"purpose": "contract dispute"})["task_type"],
        )
        self.assertEqual(
            "transaction_review",
            contracts.classify_task({"purpose": "acquisition transaction"})["task_type"],
        )
        self.assertEqual(
            "intake_triage",
            contracts.classify_task({"purpose": "triage new intake"})["task_type"],
        )
        self.assertEqual(
            "multi_regime_analysis",
            contracts.classify_task(
                {"purpose": "compare rules", "realms": ["ca", "ew"]}
            )["task_type"],
        )
        self.assertEqual(
            "single_issue_research",
            contracts.classify_task({"purpose": "lookup deadline"})["task_type"],
        )
        self.assertEqual(
            "unknown", contracts.classify_task({})["task_type"]
        )

    def test_classification_names_premises_engines_bar(self) -> None:
        result = contracts.classify_task({"purpose": "contract dispute"})
        self.assertTrue(result["premises"])
        self.assertIn("repair", result["engines"])
        self.assertIn("evidence", result["bar"]["required_classes"])

    def test_transitions_enforced(self) -> None:
        state = contracts.new_state()
        question = contracts.add_question(state, "q?", "evidence")
        contracts.set_status(state, question["id"], "ready")
        with self.assertRaises(contracts.PlanError):
            contracts.set_status(state, question["id"], "answered")

    def test_unknown_class_and_dep_refused(self) -> None:
        state = contracts.new_state()
        with self.assertRaises(contracts.PlanError):
            contracts.add_question(state, "q?", "divination")
        with self.assertRaises(contracts.PlanError):
            contracts.add_question(state, "q?", "evidence", deps=["Q99"])

    def test_cycle_refused(self) -> None:
        state = contracts.new_state()
        contracts.add_question(state, "a", "evidence")
        contracts.add_question(state, "b", "evidence", deps=["Q1"])
        state["questions"]["Q1"]["deps"].append("Q2")
        with self.assertRaises(contracts.PlanError):
            contracts._check_cycles(state)

    def test_ready_gating_and_transitive_dependents(self) -> None:
        state = contracts.new_state()
        contracts.add_question(state, "a", "evidence", premises=["P1"])
        contracts.add_question(state, "b", "evidence", deps=["Q1"])
        contracts.add_question(state, "c", "evidence", deps=["Q2"])
        self.assertEqual(["Q1"], [q["id"] for q in contracts.ready_questions(state)])
        self.assertEqual(["Q1", "Q2", "Q3"],
                         contracts.dependents_of(state, "P1"))

    def test_ready_status_is_runnable_not_stranded(self) -> None:
        state = contracts.new_state()
        question = contracts.add_question(state, "a", "evidence")
        contracts.set_status(state, question["id"], "ready")
        self.assertEqual(["Q1"],
                         [q["id"] for q in contracts.ready_questions(state)])

    def test_redundant_questions_refused(self) -> None:
        state = contracts.new_state()
        contracts.add_question(state, "When is payment due?", "evidence")
        with self.assertRaises(contracts.PlanError):
            contracts.add_question(state, "when is PAYMENT due?!", "evidence")

    def test_empty_texts_skip_redundancy(self) -> None:
        state = contracts.new_state()
        contracts.add_question(state, "", "evidence")
        contracts.add_question(state, "", "evidence")
        self.assertEqual(2, len(state["questions"]))

    def test_premise_versioning(self) -> None:
        state = contracts.new_state()
        premise = contracts.add_premise(state, "single regime", source="t")
        self.assertEqual(1, premise["version"])
        contracts.bump_premise(state, premise["id"], "two regimes", "found")
        self.assertEqual(2, state["premises"][premise["id"]]["version"])

    def test_budgets_name_their_caps(self) -> None:
        budgets = contracts.Budgets({"tool_calls": 1})
        budgets.consume("tool_calls")
        with self.assertRaises(contracts.BudgetExhausted) as raised:
            budgets.consume("tool_calls")
        self.assertIn("tool_calls", str(raised.exception))


class EngineContractTest(unittest.TestCase):
    def test_every_engine_declares_its_contract(self) -> None:
        for name, cls in engines.ENGINE_CLASSES.items():
            with self.subTest(engine=name):
                for key in ("responsibility", "inputs", "outputs",
                            "verification", "dependencies", "stopping"):
                    self.assertIn(key, cls.contract)
                for key in ("responsibility", "verification", "stopping"):
                    self.assertTrue(cls.contract.get(key), key)
                self.assertGreaterEqual(cls.max_passes, 1)


class ResearchEngineTest(unittest.TestCase):
    def run_engine(self, scripts):
        engine = engines.ResearchEngine()
        ctx = {"drivers": drivers_for(scripts),
               "budgets": contracts.Budgets(),
               "transcripts": TRANSCRIPTS}
        question = {"id": "Q1", "text": "When is payment due?",
                    "support": []}
        return engine.run(question, ctx)

    def test_verified_quote_becomes_finding(self) -> None:
        result = self.run_engine({
            "Q1": [{"facts": [{
                "document_id": "VD-CONTRACT", "page": 1,
                "quote": "Payment is due within thirty days of delivery.",
            }], "coverage": "complete"}],
        })
        self.assertTrue(result["verified"])
        self.assertEqual(1, len(result["findings"]))

    def test_bad_quote_is_a_gap_never_a_fact(self) -> None:
        result = self.run_engine({
            "Q1": [{"facts": [{
                "document_id": "VD-CONTRACT", "page": 1,
                "quote": "Payment is due whenever convenient.",
            }], "coverage": "complete"}],
        })
        self.assertFalse(result["verified"])
        self.assertEqual([], result["findings"])
        self.assertTrue(any("unverified quote" in g for g in result["gaps"]))

    def test_incomplete_coverage_reports_unresolved(self) -> None:
        result = self.run_engine({
            "Q1": [{"facts": [], "coverage": "partial", "gaps": ["page 2 missing"]}],
        })
        self.assertTrue(result["unresolved"])

    def test_verified_facts_carry_reclassification_signals(self) -> None:
        result = self.run_engine({
            "Q1": [{"facts": [{
                "document_id": "VD-CONTRACT", "page": 1,
                "quote": "Payment is due within thirty days of delivery.",
                "flags": {"dispute": True}, "realms": ["ca", "ew"],
            }], "coverage": "complete"}],
        })
        self.assertTrue(result["verified"])
        self.assertEqual({"dispute": True}, result["findings"][0]["flags"])
        self.assertEqual(["ca", "ew"], result["findings"][0]["realms"])

    def test_unverified_quotes_carry_no_signals(self) -> None:
        result = self.run_engine({
            "Q1": [{"facts": [{
                "document_id": "VD-CONTRACT", "page": 1,
                "quote": "Payment is due whenever convenient.",
                "flags": {"dispute": True},
            }], "coverage": "complete"}],
        })
        self.assertEqual([], result["findings"])

    def test_malformed_fact_entry_is_a_gap(self) -> None:
        result = self.run_engine({
            "Q1": [{"facts": ["oops", 42], "coverage": "complete"}],
        })
        self.assertFalse(result["verified"])
        self.assertEqual([], result["findings"])
        self.assertTrue(any("malformed fact" in g for g in result["gaps"]))


class AnalysisEngineTest(unittest.TestCase):
    def test_schema_gaps_and_unaddressed_opposing(self) -> None:
        engine = engines.AnalysisEngine()
        ctx = {"drivers": drivers_for({
            "Q1": [{"elements": [{"element": "duty", "test": "t"}],
                    "missing": ["causation memo"]}],
        }), "budgets": contracts.Budgets(),
            "evidence": [{"document_id": "VD-EMAIL", "page": 1,
                          "quote": "Rivera wrote that delivery slipped.",
                          "stance": "opposes"}]}
        question = {"id": "Q1", "text": "analyze", "support": []}
        result = engine.run(question, ctx)
        self.assertTrue(any("missing" in g for g in result["gaps"]))
        self.assertTrue(any("not addressed" in u for u in result["uncertainty"]))
        self.assertIn("causation memo", result["unresolved"][0])

    def test_peer_validator_branch_is_labeled(self) -> None:
        real = engines._peer_validator
        try:
            engines._peer_validator = lambda kind: (lambda e: ["peer says no"])
            engine = engines.AnalysisEngine()
            ctx = {"drivers": drivers_for({
                "Q1": [{"elements": [{"element": "d", "test": "t",
                                      "finding": "f", "for_side": "a",
                                      "against_side": "b"}]}],
            }), "budgets": contracts.Budgets(), "evidence": []}
            result = engine.run({"id": "Q1", "text": "a", "support": []}, ctx)
            self.assertTrue(any("peer validate_element ran" in n
                                for n in result["notes"]))
            self.assertTrue(any("peer validation" in g for g in result["gaps"]))
        finally:
            engines._peer_validator = real

    def test_malformed_element_entry_is_a_gap(self) -> None:
        engine = engines.AnalysisEngine()
        ctx = {"drivers": drivers_for({
            "Q1": [{"elements": ["oops", 42]}],
        }), "budgets": contracts.Budgets(), "evidence": []}
        result = engine.run({"id": "Q1", "text": "a", "support": []}, ctx)
        self.assertEqual([], result["findings"])
        self.assertTrue(any("malformed element" in g for g in result["gaps"]))


class VerificationEngineTest(unittest.TestCase):
    def test_driver_stated_path_is_labeled(self) -> None:
        engine = engines.VerificationEngine()
        ctx = {"drivers": drivers_for({
            "Q1": [{"passed": True, "problems": []}],
        }), "budgets": contracts.Budgets()}
        result = engine.run({"id": "Q1", "text": "v", "support": []}, ctx)
        self.assertTrue(any("not system-verified" in n for n in result["notes"]))

    def test_real_checks_reuse_defect_synthesis(self) -> None:
        import tempfile
        from pathlib import Path

        from agent_kit import artifacts
        from agent_kit.case_record import CaseRecord

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        run_id = artifacts.build_demo(root)
        record = CaseRecord(root, run_id)
        instance = record.artifact_get("A1")
        rows = instance["rows"]
        structural = artifacts.audit_structural(root, record.data, rows)
        semantic = {
            row["row_id"]: {"outcome": "unavailable", "verdict": None,
                            "problems": ["no judge"], "support": []}
            for row in rows
        }
        artifacts.record_checks(
            instance, record.data, root, structural, semantic
        )
        engine = engines.VerificationEngine()
        ctx = {"drivers": drivers_for(), "budgets": contracts.Budgets(),
               "audit": {"instance": instance, "data": record.data,
                         "root": root, "record": record}}
        result = engine.run({"id": "Q1", "text": "v", "support": []}, ctx)
        self.assertTrue(any("citation_failed" in g for g in result["gaps"]))


class ReviewEngineTest(unittest.TestCase):
    def test_open_items_must_be_disclosed(self) -> None:
        engine = engines.ReviewEngine()
        ctx = {"drivers": drivers_for(), "budgets": contracts.Budgets(),
               "contradictions": [{"id": "C1", "status": "open"}],
               "defects": []}
        result = engine.run({"id": "Q9", "text": "review", "support": []}, ctx)
        self.assertTrue(any("C1" in u for u in result["unresolved"]))
        self.assertFalse(result["verified"])


class RepairEngineTest(unittest.TestCase):
    def test_malformed_revision_plan_is_unresolved(self) -> None:
        from agent_kit import artifacts

        real = artifacts.synthesize_defects
        try:
            artifacts.synthesize_defects = lambda *a: [
                {"category": "bad-quote", "target": "R1"}]
            engine = engines.RepairEngine()
            ctx = {"drivers": drivers_for({
                "Q1": [{"revision": {"assertion": "fixed"}}],
            }), "budgets": contracts.Budgets(),
                "audit": {"instance": {"rows": []}, "data": {},
                          "root": Path(".")}}
            result = engine.run({"id": "Q1", "text": "fix",
                                 "support": []}, ctx)
            self.assertTrue(
                any("malformed revision" in u
                    for u in result["unresolved"])
            )
        finally:
            artifacts.synthesize_defects = real

    def test_register_rejects_non_engine(self) -> None:
        with self.assertRaises(contracts.PlanError):
            engines.register("bogus", "not-a-class")
        self.assertNotIn("bogus", engines.REGISTRY)


class OrchestratorTest(unittest.TestCase):
    def make_run(self, scripts, context, seeds, caps=None, provider=None):
        data: dict = {}
        orch = Orchestrator(data, drivers_for(scripts), caps=caps,
                            context=context)
        if provider is not None:
            orch.context_provider = provider
        contracts.add_premise(orch.state, "sources complete")
        orch.classify()
        orch.seed_questions(seeds)
        return orch, orch.run()

    def test_happy_path_approves(self) -> None:
        scripts = {
            "Q1": [{"facts": [{
                "document_id": "VD-CONTRACT", "page": 1,
                "quote": "Payment is due within thirty days of delivery.",
            }], "coverage": "complete"}],
            "Q2": [{"elements": [{"test": "breach", "element": "duty",
                                  "finding": "owed", "for_side": "contract",
                                  "against_side": "waiver argued",
                                  "rule": "duty then breach",
                                  "source": "VD-CONTRACT section 4",
                                  "facts_evidence": "payment thirty days "
                                                    "after delivery",
                                  "application": "delivery slipped two weeks",
                                  "supporting": "contract sets the deadline",
                                  "opposing": "waiver argued",
                                  "missing": "none identified",
                                  "effect": "duty owed"}]}],
            "Q3": [{"passed": True, "problems": []}],
        }
        seeds = [
            {"text": "When is payment due?", "class": "evidence",
             "premises": ["P1"]},
            {"text": "Was the duty breached?", "class": "analysis",
             "deps": ["Q1"], "premises": ["P1"]},
            {"text": "Verify the memo.", "class": "verify",
             "deps": ["Q2"], "premises": ["P1"]},
            {"text": "Integrate and disclose.", "class": "review",
             "deps": ["Q3"], "premises": ["P1"]},
        ]
        orch, report = self.make_run(
            scripts, {"purpose": "lookup deadline"}, seeds,
            provider=lambda q, s: {"transcripts": TRANSCRIPTS},
        )
        self.assertEqual("ready_for_review", report["outcome"])
        self.assertEqual("machine_ready", report["approval"]["decision"])
        for qid in ("Q1", "Q2", "Q3", "Q4"):
            self.assertEqual("answered", report["questions"][qid]["status"])

    def test_unknown_task_escalates_without_guessing(self) -> None:
        data: dict = {}
        orch = Orchestrator(data, drivers_for(), context={})
        report = orch.run()
        self.assertTrue(report["outcome"].startswith("escalated"))
        self.assertTrue(report["escalations"])

    def test_missing_driver_fails_closed(self) -> None:
        data: dict = {}
        orch = Orchestrator(data, {"research": ScriptedDriver()},
                            context={"purpose": "contract dispute"})
        with self.assertRaises(contracts.PlanError):
            orch.classify()

    def test_invalidation_is_selective(self) -> None:
        data: dict = {}
        orch = Orchestrator(
            data,
            drivers_for({
                "Q1": [{"facts": [{
                    "document_id": "VD-CONTRACT", "page": 1,
                    "quote": "Payment is due within thirty days of delivery.",
                }], "coverage": "complete"}],
                "Q2": [{"facts": [{
                    "document_id": "VD-EMAIL", "page": 1,
                    "quote": "Rivera wrote that delivery slipped by two weeks.",
                }], "coverage": "complete"}],
            }),
            context={"purpose": "lookup deadline"},
        )
        orch.context_provider = lambda q, s: {"transcripts": TRANSCRIPTS}
        contracts.add_premise(orch.state, "ledger complete")
        contracts.add_premise(orch.state, "mail complete")
        orch.classify()
        orch.seed_questions([
            {"text": "payment?", "class": "evidence", "premises": ["P1"]},
            {"text": "delivery?", "class": "evidence", "premises": ["P2"]},
        ])
        orch.run()
        before = {fid: dict(f) for fid, f in orch.state["findings"].items()
                  if f["question_id"] == "Q2"}
        staled = orch.invalidate_premise("P1", "ledger revised", "source changed")
        self.assertEqual(["Q1"], staled)
        self.assertEqual(
            "answered", orch.state["questions"]["Q2"]["status"])
        after = {fid: dict(f) for fid, f in orch.state["findings"].items()
                 if f["question_id"] == "Q2"}
        self.assertEqual(before, after)
        self.assertTrue(all(f["stale"] for f in
                            orch.state["findings"].values()
                            if f["question_id"] == "Q1"))

    def test_repeated_failure_escalates(self) -> None:
        class Failing:
            def gather(self, question, needs):
                raise EngineFailed("driver down")

        data: dict = {}
        drivers = drivers_for()
        drivers["research"] = Failing()
        orch = Orchestrator(data, drivers,
                            caps={"max_failures": 2},
                            context={"purpose": "lookup x"})
        orch.classify()
        orch.seed_questions([{"text": "q?", "class": "evidence"}])
        report = orch.run()
        self.assertEqual("escalated",
                         report["questions"]["Q1"]["status"])
        self.assertTrue(report["escalations"])

    def test_diminishing_value_discloses(self) -> None:
        data: dict = {}
        orch = Orchestrator(
            data, drivers_for({"Q1": [{"facts": [], "coverage": "partial",
                                       "gaps": ["nothing found"]}]}),
            caps={"no_progress_passes": 1},
            context={"purpose": "lookup x"},
        )
        orch.context_provider = lambda q, s: {"transcripts": TRANSCRIPTS}
        orch.classify()
        orch.seed_questions([{"text": "q?", "class": "evidence"}])
        report = orch.run()
        self.assertEqual("disclosed", report["questions"]["Q1"]["status"])
        self.assertTrue(any("no progress" in d for d in report["disclosures"]))

    def test_identical_findings_do_not_reset_stagnation(self) -> None:
        scripts = {
            "Q1": [{"elements": [{
                "test": "vesting", "element": "timing", "finding": "due",
                "for_side": "code", "against_side": "amendment",
                "rule": "fees vest on filing", "source": "WV-CODE",
                "facts_evidence": "filing dated", "application": "vested",
                "supporting": "code text", "opposing": "amendment text",
                "missing": "none identified", "effect": "due"}],
                "missing": ["decisive memo"]}],
        }
        orch, report = self.make_run(
            scripts, {"purpose": "lookup x"},
            [{"text": "analyze?", "class": "analysis"}],
        )
        self.assertEqual("disclosed", report["questions"]["Q1"]["status"])
        passes = sum(
            1 for h in orch.state["questions"]["Q1"]["history"]
            if h.get("change", "").endswith("->in_progress")
        )
        self.assertEqual(3, passes)

    def test_answered_behind_disclosed_dep_carries_conditions(self) -> None:
        scripts = {
            "Q1": [{"facts": [], "coverage": "partial",
                    "gaps": ["nothing found"]}],
            "Q2": [{"passed": True, "problems": []}],
        }
        seeds = [
            {"text": "q?", "class": "evidence"},
            {"text": "v?", "class": "verify", "deps": ["Q1"]},
        ]
        orch, report = self.make_run(
            scripts, {"purpose": "lookup x"}, seeds,
            provider=lambda q, s: {"transcripts": TRANSCRIPTS},
        )
        self.assertEqual("disclosed", report["questions"]["Q1"]["status"])
        self.assertEqual("answered", report["questions"]["Q2"]["status"])
        self.assertEqual(["Q1 is disclosed"],
                         report["questions"]["Q2"]["conditions"])

    def test_answered_behind_settled_deps_has_no_conditions(self) -> None:
        scripts = {
            "Q1": [{"facts": [{
                "document_id": "VD-CONTRACT", "page": 1,
                "quote": "Payment is due within thirty days of delivery.",
            }], "coverage": "complete"}],
            "Q2": [{"passed": True, "problems": []}],
        }
        seeds = [
            {"text": "q?", "class": "evidence"},
            {"text": "v?", "class": "verify", "deps": ["Q1"]},
        ]
        orch, report = self.make_run(
            scripts, {"purpose": "lookup x"}, seeds,
            provider=lambda q, s: {"transcripts": TRANSCRIPTS},
        )
        self.assertEqual([], report["questions"]["Q2"]["conditions"])

    def test_budget_exhaustion_discloses_and_escalates(self) -> None:
        data: dict = {}
        orch = Orchestrator(
            data, drivers_for(),
            caps={"tool_calls": 0},
            context={"purpose": "lookup x"},
        )
        orch.classify()
        orch.seed_questions([{"text": "q?", "class": "evidence"}])
        report = orch.run()
        self.assertTrue(any("budget exhausted" in d
                            for d in report["disclosures"]))
        self.assertTrue(report["escalations"])

    def test_contradiction_resolves_on_reanswer(self) -> None:
        data: dict = {}
        orch = Orchestrator(data, drivers_for(),
                            context={"purpose": "lookup x"})
        orch.classify()
        orch.seed_questions([{"text": "q?", "class": "evidence"}])
        orch._record_contradiction("Q1", {"about": "Q1", "sides": ["a", "b"]})
        self.assertEqual("open", orch.state["contradictions"][0]["status"])
        orch.state["questions"]["Q1"]["answer_version"] = 1
        orch.state["questions"]["Q1"]["status"] = "answered"
        orch._reconcile_contradictions()
        # Same inputs: not a resolution.
        self.assertEqual("open", orch.state["contradictions"][0]["status"])
        orch.state["invalidations"].append({"premise": "P1"})
        orch._reconcile_contradictions()
        # An unrelated later invalidation, without a re-answer
        # after it, is not a resolution either.
        self.assertEqual("open", orch.state["contradictions"][0]["status"])
        orch.state["questions"]["Q1"]["answer_version"] = 2
        orch.state["questions"]["Q1"]["answered_at_inv"] = 1
        orch._reconcile_contradictions()
        # A re-answer with byte-identical support is a repeat,
        # not new assessment.
        self.assertEqual("open", orch.state["contradictions"][0]["status"])
        orch.state["questions"]["Q1"]["support"].append(
            {"premise": "P1", "premise_version": 2, "refs": ["new"]}
        )
        orch._reconcile_contradictions()
        resolved = orch.state["contradictions"][0]
        self.assertEqual("resolved", resolved["status"])
        self.assertEqual("resolved", resolved["disposition"]["kind"])
        self.assertEqual(2, resolved["disposition"]["question_version"])

    def test_disclosure_matching_is_exact_token(self) -> None:
        data: dict = {}
        orch = Orchestrator(data, drivers_for(),
                            context={"purpose": "lookup x"})
        orch.classify()
        orch.seed_questions([{"text": "q?", "class": "evidence"}])
        orch._record_contradiction("Q1", {"about": "Q1", "sides": ["a", "b"]})
        orch.state["disclosures"].append("C10 needs more evidence")
        orch._reconcile_contradictions()
        # C1 is a substring of C10: exact-token matching must not fire.
        self.assertEqual("open", orch.state["contradictions"][0]["status"])
        orch.state["disclosures"].append("C1 cannot be settled here")
        orch._reconcile_contradictions()
        closed = orch.state["contradictions"][0]
        self.assertEqual("disclosed", closed["status"])
        self.assertEqual("disclosed-unresolved",
                         closed["disposition"]["kind"])

    def test_dependent_bump_reopens_settled_contradiction(self) -> None:
        data: dict = {}
        orch = Orchestrator(data, drivers_for(),
                            context={"purpose": "lookup x"})
        contracts.add_premise(orch.state, "ledger complete")
        contracts.add_premise(orch.state, "mail complete")
        orch.classify()
        orch.seed_questions([{"text": "q?", "class": "evidence",
                              "premises": ["P1"]}])
        orch._record_contradiction("Q1", {"about": "Q1", "sides": ["a", "b"]})
        orch.state["contradictions"][0]["status"] = "resolved"
        orch.invalidate_premise("P2", "mail revised", "unrelated")
        self.assertEqual("resolved",
                         orch.state["contradictions"][0]["status"])
        orch.invalidate_premise("P1", "ledger revised", "dependent")
        reopened = orch.state["contradictions"][0]
        self.assertEqual("open", reopened["status"])
        self.assertEqual("resolved", reopened["reopens"][0]["prior"])
        self.assertEqual("P1", reopened["reopens"][0]["premise"])

    def test_policy_pinned_on_classify(self) -> None:
        data: dict = {}
        orch = Orchestrator(data, drivers_for(),
                            context={"purpose": "lookup x"})
        orch.classify()
        policy = orch.state["policy"]
        self.assertEqual(contracts.PLANE_VERSION, policy["plane"])
        self.assertEqual("single_issue_research", policy["task_type"])
        self.assertIn("research", policy["engines"])
        self.assertEqual([], orch.state["policy_history"])

    def test_reclassification_supersedes_policy_with_history(self) -> None:
        data: dict = {}
        orch = Orchestrator(data, drivers_for(),
                            context={"purpose": "lookup x"})
        orch.classify()
        orch.state["findings"]["F1"] = {
            "id": "F1", "question_id": "Q9", "engine": "research",
            "finding": {"flags": {"dispute": True}}, "stale": False,
        }
        self.assertTrue(orch._maybe_reclassify())
        self.assertEqual("dispute_investigation",
                         orch.state["policy"]["task_type"])
        self.assertEqual(1, len(orch.state["policy_history"]))
        self.assertEqual("single_issue_research",
                         orch.state["policy_history"][0]["task_type"])

    def test_no_defect_repair_is_justified_not_applicable(self) -> None:
        engine = engines.RepairEngine()
        ctx = {"drivers": drivers_for({}),
               "budgets": contracts.Budgets(),
               "audit": {"instance": {"rows": []}, "data": {},
                         "root": Path(".")}}
        question = {"id": "Q1", "text": "Repair open defects.", "support": []}
        result = engine.run(question, ctx)
        self.assertTrue(result["verified"])
        self.assertEqual(1, len(result["findings"]))
        finding = result["findings"][0]
        self.assertEqual("not_applicable", finding["state"])
        self.assertIn("no repair executed", finding["basis"])
        self.assertEqual(0, finding["defects_seen"])

    def test_later_invalidation_clears_readiness(self) -> None:
        data: dict = {}
        orch = Orchestrator(data, drivers_for(),
                            context={"purpose": "lookup x"})
        contracts.add_premise(orch.state, "ledger complete")
        orch.classify()
        orch.seed_questions([{"text": "q?", "class": "evidence",
                              "premises": ["P1"]}])
        orch.state["approval"] = {"decision": "machine_ready", "at_inv": 0}
        orch.invalidate_premise("P1", "ledger revised", "new evidence")
        self.assertIsNone(orch.state["approval"])

    def test_corrupt_state_fails_closed(self) -> None:
        with self.assertRaises(contracts.PlanError):
            Orchestrator({"orchestration": {"questions": {"Q1": {}}}},
                         drivers_for())

    def test_corrupt_question_unknown_status_fails_closed(self) -> None:
        bad = {"status": "transmogrified", "class": "evidence",
               "deps": [], "history": []}
        with self.assertRaises(contracts.PlanError):
            Orchestrator({"orchestration": {"questions": {"Q1": bad}}},
                         drivers_for())

    def test_corrupt_question_missing_keys_fails_closed(self) -> None:
        for missing in ("class", "deps", "history"):
            good = {"status": "proposed", "class": "evidence",
                    "deps": [], "history": []}
            del good[missing]
            with self.assertRaises(contracts.PlanError, msg=missing):
                Orchestrator(
                    {"orchestration": {"questions": {"Q1": good}}},
                    drivers_for())

    def test_classify_non_list_realms_ignored(self) -> None:
        self.assertEqual(
            "unknown", contracts.classify_task({"realms": 7})["task_type"]
        )
        self.assertEqual(
            "single_issue_research",
            contracts.classify_task(
                {"purpose": "lookup x", "realms": "ca"})["task_type"],
        )

    def test_seed_from_foundation_uses_peer_converter(self) -> None:
        real = orch_module._peer_foundation
        try:
            orch_module._peer_foundation = lambda: (lambda data: [
                {"text": "first?", "class": "evidence"},
                {"text": "second?", "class": "analysis", "deps": ["Q1"]},
            ], None)
            data: dict = {}
            orch = Orchestrator(data, drivers_for(),
                                context={"purpose": "lookup x"})
            orch.classify()
            ids = orch.seed_from_foundation({"foundational": []})
            self.assertEqual(["Q1", "Q2"], ids)
            self.assertEqual(["Q1"],
                             orch.state["questions"]["Q2"]["deps"])
            self.assertIn("foundation:2",
                          orch.state["seed_source"])
        finally:
            orch_module._peer_foundation = real

    def test_seed_gate_refuses_problems_before_seeding(self) -> None:
        real = orch_module._peer_foundation
        try:
            orch_module._peer_foundation = lambda: (
                lambda data: [{"text": "q?", "class": "evidence"}],
                lambda *a: (["plan has no criterion"], ["check theory"]),
            )
            data: dict = {}
            orch = Orchestrator(data, drivers_for(),
                                context={"purpose": "lookup x"})
            orch.classify()
            with self.assertRaises(contracts.PlanError):
                orch.seed_from_foundation({"foundational": []},
                                          gate_task_type="research")
            self.assertEqual({}, orch.state["questions"])
            self.assertIn("check theory",
                          orch.state["decomposition_advisory"])
        finally:
            orch_module._peer_foundation = real

    def test_seed_gate_pass_records_advisory(self) -> None:
        real = orch_module._peer_foundation
        try:
            orch_module._peer_foundation = lambda: (
                lambda data: [{"text": "q?", "class": "evidence"}],
                lambda *a: ([], ["confirm the theory"]),
            )
            data: dict = {}
            orch = Orchestrator(data, drivers_for(),
                                context={"purpose": "lookup x"})
            orch.classify()
            orch.seed_from_foundation({"foundational": []},
                                      gate_task_type="research")
            self.assertIn("confirm the theory",
                          orch.state["decomposition_advisory"])
            self.assertIn("gated: research", orch.state["seed_source"])
        finally:
            orch_module._peer_foundation = real

    def test_seed_from_foundation_completes_bar(self) -> None:
        real = orch_module._peer_foundation
        try:
            orch_module._peer_foundation = lambda: (lambda data: [
                {"text": "first?", "class": "evidence"},
                {"text": "second?", "class": "evidence"},
            ], None)
            data: dict = {}
            orch = Orchestrator(data, drivers_for(),
                                context={"purpose": "contract dispute"})
            orch.classify()
            ids = orch.seed_from_foundation({"foundational": []})
            self.assertEqual(["Q1", "Q2"], ids)
            questions = orch.state["questions"]
            # Dispute bar: analysis, verify, repair, review added.
            self.assertEqual("analysis", questions["Q3"]["class"])
            self.assertEqual([], questions["Q3"]["deps"])
            self.assertEqual("verify", questions["Q4"]["class"])
            self.assertEqual(["Q1", "Q2", "Q3"],
                             questions["Q4"]["deps"])
            self.assertEqual(["Q4"], questions["Q5"]["deps"])
            self.assertEqual(["Q5"], questions["Q6"]["deps"])
            self.assertIn("+4 bar completion", orch.state["seed_source"])
        finally:
            orch_module._peer_foundation = real

    def test_seed_from_foundation_skips_completion_when_met(self) -> None:
        real = orch_module._peer_foundation
        try:
            orch_module._peer_foundation = lambda: (lambda data: [
                {"text": "e?", "class": "evidence"},
                {"text": "a?", "class": "analysis"},
                {"text": "v?", "class": "verify"},
                {"text": "r?", "class": "review"},
            ], None)
            data: dict = {}
            orch = Orchestrator(data, drivers_for(),
                                context={"purpose": "lookup x"})
            orch.classify()
            ids = orch.seed_from_foundation({"foundational": []})
            self.assertEqual(4, len(ids))
            self.assertEqual(4, len(orch.state["questions"]))
            self.assertNotIn("bar completion", orch.state["seed_source"])
        finally:
            orch_module._peer_foundation = real

    def test_seed_from_foundation_absent_peer_fails_closed(self) -> None:
        real = orch_module._peer_foundation
        try:
            orch_module._peer_foundation = lambda: (None, None)
            data: dict = {}
            orch = Orchestrator(data, drivers_for(),
                                context={"purpose": "lookup x"})
            orch.classify()
            with self.assertRaises(contracts.PlanError):
                orch.seed_from_foundation({"foundational": []})
        finally:
            orch_module._peer_foundation = real

    def test_identical_premise_update_is_a_noop(self) -> None:
        data: dict = {}
        orch = Orchestrator(data, drivers_for(),
                            context={"purpose": "lookup x"})
        contracts.add_premise(orch.state, "ledger complete")
        orch.classify()
        orch.seed_questions([{"text": "q?", "class": "evidence",
                              "premises": ["P1"]}])
        orch.run()
        self.assertEqual("answered",
                         orch.state["questions"]["Q1"]["status"])
        orch._apply_premise_updates([{"premise": "P1",
                                      "statement": "ledger complete",
                                      "reason": "re-stated"}])
        self.assertEqual([], orch.state["invalidations"])
        self.assertEqual("answered",
                         orch.state["questions"]["Q1"]["status"])
        self.assertEqual(1, orch.state["premises"]["P1"]["version"])
