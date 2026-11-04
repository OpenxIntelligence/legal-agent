"""§4 acceptance: orchestrated runs through real application paths.

Real run/resume (new CaseRecord per load), real transcripts and
quote checks, the real peer element validator, real audit
structural checks / defect synthesis / revision guards, atomic
case persistence, and the real usage ledger. Only model judgment
is replaced — by explicit scripts that fail loudly when a call
has no configured response. No model client is constructed, so
judge/token usage is recorded unknown, never zero.
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

from agent_kit import tools, usage as usage_ledger
from agent_kit.case_record import CaseRecord
from agent_kit.orchestration import contracts, runtime
from agent_kit.orchestration.runtime import UnconfiguredCall
from agent_kit.progress import new_run
from agent_kit.tools.analyze_document import analyze
from tests import support

QUOTE = "Mr. Lindgren confirmed the system is ready for go-live."
ELEMENT = {
    "test": "readiness", "element": "confirmation", "finding": "stated",
    "for_side": "minutes quote", "against_side": "no signature check",
    "rule": "minutes record what was said", "source": "DOC-MINUTES",
    "facts_evidence": QUOTE, "application": "stated, unattributed",
    "supporting": "exact quote", "opposing": "none recorded",
    "missing": "signature comparison", "effect": "stated only",
}


def fresh_case(test: unittest.TestCase, docs=("DOC-MINUTES",)):
    root, tmp = support.make_root()
    test.addCleanup(tmp.cleanup)
    fake = support.scripted(root, [])
    run_id = new_run(root, instructions="i", objective="o", verification=True)
    analyze(fake.client, root, list(docs), "x")
    return root, run_id, CaseRecord(root, run_id)


def fact_script(stance="supports"):
    return {
        "gather": {
            "facts": [{
                "document_id": "DOC-MINUTES", "page": 1,
                "quote": QUOTE, "stance": stance,
            }],
            "coverage": "complete",
        }
    }


def run_cli(root, run_id, scripts, slices, *, seeds=None, per_call=10 ** 9,
            context=None, audit=""):
    scripts_path = Path(root) / "scripts.json"
    scripts_path.write_text(json.dumps(scripts))
    cmd = [sys.executable, "-m", "agent_kit.orchestration.runtime",
           str(root), run_id, "--scripts", str(scripts_path),
           "--slices", str(slices), "--per-call", str(per_call),
           "--context", json.dumps(context or {"purpose": "lookup x"})]
    if audit:
        cmd += ["--audit", audit]
    if seeds is not None:
        seeds_path = Path(root) / "seeds.json"
        seeds_path.write_text(json.dumps(seeds))
        cmd += ["--seeds", str(seeds_path)]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=300,
                         cwd=Path(__file__).resolve().parents[1])
    test = out.returncode == 0
    if not test:
        raise AssertionError(f"runtime CLI failed: {out.stderr[-500:]}")
    return json.loads(out.stdout)


class RuntimeAcceptanceTest(unittest.TestCase):
    maxDiff = 2000

    def test_slices_span_subprocess_restart(self) -> None:
        root, run_id, _ = fresh_case(self)
        seeds = [
            {"text": "What was confirmed?", "class": "evidence"},
            {"text": "What does it establish?", "class": "analysis",
             "deps": ["Q1"]},
        ]
        scripts = {"Q1": fact_script(),
                   "Q2": {"assess": {"elements": [ELEMENT]}}}
        first = run_cli(root, run_id, scripts, 4, seeds=seeds, per_call=1)
        self.assertEqual("paused: call slice cap reached, resume to continue",
                         first["outcome"])
        self.assertEqual("answered", first["questions"]["Q1"]["status"])
        self.assertEqual("proposed", first["questions"]["Q2"]["status"])
        state = CaseRecord(root, run_id).data["orchestration"]
        findings_after_first = len(state["findings"])
        self.assertEqual(1, state["allowance"]["used"])
        second = run_cli(root, run_id, scripts, 999)
        self.assertEqual("answered", second["questions"]["Q2"]["status"])
        state = CaseRecord(root, run_id).data["orchestration"]
        # Parent allowance pinned on first run: the resume's 999
        # did not reset it, and Q1 was not re-run.
        self.assertEqual(4, state["allowance"]["total"])
        self.assertEqual(2, state["allowance"]["used"])
        runs = [h for h in state["questions"]["Q1"]["history"]
                if h.get("change", "").endswith("->in_progress")]
        self.assertEqual(1, len(runs))
        self.assertEqual(findings_after_first + 1, len(state["findings"]))
        # Usage ledger carries the slices with unknown tokens.
        totals = usage_ledger.totals(root, run_id)
        orch = [p for p in totals.get("by_purpose", {})
                if p.startswith("orchestration/")]
        self.assertEqual(2, len(orch))
        entries = json.loads(
            (root / "workspace/runs" / run_id / "usage.json").read_text()
        )
        ours = [e for e in entries
                if str(e.get("purpose", "")).startswith("orchestration/")]
        self.assertTrue(ours)
        for entry in ours:
            self.assertIsNone(entry["input_tokens"])
            self.assertFalse(entry["measured"])

    def test_premise_change_reopens_only_dependents(self) -> None:
        root, run_id, case = fresh_case(self)
        coord = runtime.Coordinator(
            case,
            {"Q1": fact_script(), "Q3": fact_script(),
             "Q2": {"assess": {"elements": [ELEMENT]}}},
            {"total": 8}, context={"purpose": "lookup x"})
        pid = contracts.add_premise(
            coord.orch.state, "deadline stands")["id"]
        coord.orch.classify()
        report = coord.run(seeds=[
            {"text": "What was confirmed?", "class": "evidence",
             "premises": [pid]},
            {"text": "What follows?", "class": "analysis", "deps": ["Q1"]},
            {"text": "Side note?", "class": "evidence"},
        ])
        self.assertEqual("answered", report["questions"]["Q1"]["status"])
        case = CaseRecord(root, run_id)
        coord = runtime.Coordinator(case, {}, {"total": 8})
        coord.orch.invalidate_premise(pid, "deadline moved", "new statute")
        coord.commit("test bump")
        state = CaseRecord(root, run_id).data["orchestration"]
        # Q1 names the premise and Q2 hangs off Q1: both requeue.
        # The independent Q3 is untouched.
        self.assertEqual("stale", state["questions"]["Q1"]["status"])
        self.assertEqual("stale", state["questions"]["Q2"]["status"])
        self.assertEqual("answered", state["questions"]["Q3"]["status"])

    def test_unrelated_change_cannot_close_contradiction(self) -> None:
        root, run_id, case = fresh_case(self)
        scripts = {"Q1": {"gather": {
            "facts": [
                {"document_id": "DOC-MINUTES", "page": 1, "quote": QUOTE},
                {"document_id": "DOC-MINUTES", "page": 1, "quote": QUOTE,
                 "stance": "opposes"},
            ], "coverage": "complete"}}}
        runtime.run_orchestrated(
            root, run_id, scripts, {"total": 4},
            seeds=[{"text": "Is it confirmed?", "class": "evidence"}],
            context={"purpose": "lookup x"})
        case = CaseRecord(root, run_id)
        state = case.data["orchestration"]
        self.assertEqual("open", state["contradictions"][0]["status"])
        coord = runtime.Coordinator(case, {}, {"total": 8})
        far = contracts.add_premise(coord.orch.state, "other matter standing")
        coord.orch.invalidate_premise(far["id"], "other matter moved",
                                      "unrelated")
        coord.commit("test bump")
        state = CaseRecord(root, run_id).data["orchestration"]
        self.assertEqual("open", state["contradictions"][0]["status"])
        self.assertEqual("answered", state["questions"]["Q1"]["status"])

    def test_persistent_gaps_terminate_disclosed(self) -> None:
        root, run_id, _ = fresh_case(self)
        report = runtime.run_orchestrated(
            root, run_id,
            {"Q1": {"gather": {"facts": [], "coverage": "partial",
                               "gaps": ["page missing"]}}},
            {"total": 12},
            seeds=[{"text": "What is missing?", "class": "evidence"}],
            context={"purpose": "lookup x"})
        self.assertEqual("disclosed", report["questions"]["Q1"]["status"])
        state = CaseRecord(root, run_id).data["orchestration"]
        runs = [h for h in state["questions"]["Q1"]["history"]
                if h.get("change", "").endswith("->in_progress")]
        self.assertEqual(2, len(runs))

    def test_conditional_report_behind_unresolved(self) -> None:
        root, run_id, _ = fresh_case(self)
        report = runtime.run_orchestrated(
            root, run_id,
            {"Q1": {"gather": {"facts": [], "coverage": "partial",
                               "gaps": ["page missing"]}},
             "Q2": {"challenge": {"counterarguments": "none found",
                                  "risks": "Q1 gap"}}},
            {"total": 12},
            seeds=[{"text": "What is missing?", "class": "evidence"},
                   {"text": "Integrate.", "class": "review", "deps": ["Q1"]}],
            context={"purpose": "lookup x"})
        self.assertEqual("disclosed", report["questions"]["Q1"]["status"])
        self.assertEqual("answered", report["questions"]["Q2"]["status"])
        self.assertEqual(["Q1 is disclosed"],
                         report["questions"]["Q2"]["conditions"])

    def test_inconclusive_reaches_review_obligation_preserved(self) -> None:
        root, run_id, _ = fresh_case(self)
        report = runtime.run_orchestrated(
            root, run_id,
            {"Q1": {"gather": {"facts": [], "coverage": "partial",
                               "gaps": ["signature page missing"]}},
             "Q2": {"challenge": {"counterarguments": "none found",
                                  "risks": "attribution open"}}},
            {"total": 12},
            seeds=[{"text": "Who signed?", "class": "evidence"},
                   {"text": "Integrate.", "class": "review", "deps": ["Q1"]}],
            context={"purpose": "lookup x"})
        self.assertEqual("answered", report["questions"]["Q2"]["status"])
        case = CaseRecord(root, run_id)
        # The original obligation stays visible and untouched: the
        # checklist was never created, and Q1 keeps its disclosure.
        self.assertEqual({}, case.data.get("checklist", {}))
        state = case.data["orchestration"]
        self.assertEqual("disclosed", state["questions"]["Q1"]["status"])
        self.assertTrue(
            any("signature page missing" in d for d in state["disclosures"])
        )

    def test_unknown_task_escalates_without_questions(self) -> None:
        root, run_id, _ = fresh_case(self)
        report = runtime.run_orchestrated(
            root, run_id, {}, {"total": 4},
            seeds=[{"text": "Do something?", "class": "evidence"}],
            context={})
        self.assertTrue(
            any("unknown task type" in e for e in report["escalations"])
        )
        answered = [q for q in report["questions"].values()
                    if q["status"] == "answered"]
        self.assertEqual([], answered)

    def test_unconfigured_call_escalates_never_answers(self) -> None:
        root, run_id, _ = fresh_case(self)
        report = runtime.run_orchestrated(
            root, run_id, {}, {"total": 8},
            seeds=[{"text": "What was confirmed?", "class": "evidence"}],
            context={"purpose": "lookup x"})
        self.assertEqual("escalated", report["questions"]["Q1"]["status"])
        state = CaseRecord(root, run_id).data["orchestration"]
        self.assertEqual(3, state["questions"]["Q1"]["failures"])
        self.assertTrue(
            any("failed repeatedly" in e for e in state["escalations"])
        )

    def test_malformed_state_fails_closed(self) -> None:
        root, run_id, case = fresh_case(self)
        case.data["orchestration"] = {"questions": {"Q1": {}}}
        case._save("test corrupt", "test", {})
        with self.assertRaises(contracts.PlanError):
            runtime.run_orchestrated(
                root, run_id, {}, {"total": 4},
                context={"purpose": "lookup x"})

    def test_defect_free_audit_needs_no_repair(self) -> None:
        root, run_id, case = fresh_case(self)
        case.data.setdefault("artifacts", {})["A9"] = {
            "id": "A9", "template": "citation-claim-audit",
            "matter": run_id, "rows": [], "versions": [],
        }
        case._save("test instance", "test", {})
        report = runtime.run_orchestrated(
            root, run_id, {"Q1": {"fix": {}}}, {"total": 4},
            seeds=[{"text": "Repair open defects.", "class": "repair"}],
            context={"purpose": "lookup x"}, audit_id="A9")
        self.assertEqual("answered", report["questions"]["Q1"]["status"])
        case = CaseRecord(root, run_id)
        state = case.data["orchestration"]
        findings = [f["finding"] for f in state["findings"].values()]
        self.assertEqual("not_applicable", findings[0]["state"])
        self.assertEqual(0, findings[0]["defects_seen"])
        # No revision was executed: versions untouched.
        self.assertEqual([], case.data["artifacts"]["A9"]["versions"])

    def test_repair_executes_for_concrete_defect(self) -> None:
        from agent_kit import artifacts as artifacts_mod

        root, run_id, case = fresh_case(self)
        row = {"row_id": "R1", "kind": "finding",
               "assertion": "The minutes confirm readiness.",
               "citations": [{"document_id": "DOC-MINUTES", "page": 9,
                              "quote": QUOTE}],
               "history": [],
               "checks": {"structural": {"outcome": "failed",
                                         "problems": ["wrong page"]},
                          "semantic": {"outcome": "not_run"}}}
        case.data.setdefault("artifacts", {})["A9"] = {
            "id": "A9", "template": "citation-claim-audit",
            "matter": run_id, "rows": [row], "versions": [{"n": 1}],
            "defects": [], "defect_seq": 0, "history": [],
        }
        case._save("test instance", "test", {})
        before = artifacts_mod.synthesize_defects(
            case.data["artifacts"]["A9"], case.data, root)
        self.assertTrue(before)
        report = runtime.run_orchestrated(
            root, run_id,
            {"Q1": {"fix": {"revision": {
                "row_id": "R1",
                "citation": {"document_id": "DOC-MINUTES", "page": 1,
                             "quote": QUOTE}}}}},
            {"total": 4},
            seeds=[{"text": "Repair open defects.", "class": "repair"}],
            context={"purpose": "lookup x"}, audit_id="A9")
        self.assertEqual("answered", report["questions"]["Q1"]["status"])
        case = CaseRecord(root, run_id)
        after = artifacts_mod.synthesize_defects(
            case.data["artifacts"]["A9"], case.data, root)
        # The concrete defect was revised away; none invented.
        self.assertEqual([], [d for d in after
                              if d.get("target") == "R1"
                              and "page" in str(d).lower()]
                         or [d for d in after if d not in before])
        self.assertGreater(
            len(case.data["artifacts"]["A9"]["versions"]), 1)

    def test_provider_failure_records_and_recovers(self) -> None:
        root, run_id, case = fresh_case(self)
        report = runtime.run_orchestrated(
            root, run_id,
            {"Q1": {"gather": [
                {"error": "provider timeout"},
                fact_script()["gather"]]}},
            {"total": 6},
            seeds=[{"text": "What was confirmed?", "class": "evidence"}],
            context={"purpose": "lookup x"})
        self.assertEqual("answered", report["questions"]["Q1"]["status"])
        state = CaseRecord(root, run_id).data["orchestration"]
        self.assertEqual(1, state["questions"]["Q1"]["failures"])
        self.assertEqual(2, state["allowance"]["used"])
        # Re-recording after an interruption is safe: record_fact
        # returns the existing fact unchanged.
        case.set_checklist([support.checklist_item("item_x")])
        first = case.record_fact("item_x", "DOC-MINUTES", 1, QUOTE, "c")
        again = case.record_fact("item_x", "DOC-MINUTES", 1, QUOTE, "c")
        self.assertEqual(first["id"], again["id"])

    def test_readiness_is_not_human_approval(self) -> None:
        root, run_id, _ = fresh_case(self)
        report = runtime.run_orchestrated(
            root, run_id,
            {"Q1": fact_script(),
             "Q2": {"assess": {"elements": [ELEMENT]}},
             "Q3": {"check": {"passed": True, "problems": []}},
             "Q4": {"challenge": {"counterarguments": "none",
                                  "risks": "none"}}},
            {"total": 10},
            seeds=[{"text": "What was confirmed?", "class": "evidence"},
                   {"text": "What follows?", "class": "analysis",
                    "deps": ["Q1"]},
                   {"text": "Verify.", "class": "verify", "deps": ["Q2"]},
                   {"text": "Integrate.", "class": "review", "deps": ["Q3"]}],
            context={"purpose": "lookup x"})
        self.assertEqual("ready_for_review", report["outcome"])
        self.assertEqual("machine_ready", report["approval"]["decision"])
        case = CaseRecord(root, run_id)
        self.assertIsNone(case.data.get("memo"))
        self.assertFalse(case.data.get("memo_history"))
        # New source versions stale the readiness.
        coord = runtime.Coordinator(case, {}, {"total": 10})
        pid = contracts.add_premise(coord.orch.state, "v1 standing")["id"]
        coord.orch.invalidate_premise(pid, "v2 arrived", "new version")
        coord.commit("test bump")
        state = CaseRecord(root, run_id).data["orchestration"]
        self.assertIsNone(state["approval"])

    def test_legacy_run_has_no_orchestration_key(self) -> None:
        root, run_id, case = fresh_case(self)
        case.set_checklist([support.checklist_item("item_x")])
        case.record_fact("item_x", "DOC-MINUTES", 1, QUOTE, "c")
        reloaded = CaseRecord(root, run_id)
        self.assertNotIn("orchestration", reloaded.data)

    def test_briefing_is_bounded_and_linked(self) -> None:
        root, run_id, _ = fresh_case(self)
        runtime.run_orchestrated(
            root, run_id,
            {"Q1": fact_script(),
             "Q2": {"assess": {"elements": [ELEMENT]}}},
            {"total": 4},
            seeds=[{"text": "What was confirmed?", "class": "evidence"},
                   {"text": "What follows?", "class": "analysis",
                    "deps": ["Q1"]}],
            context={"purpose": "lookup x"})
        state = CaseRecord(root, run_id).data["orchestration"]
        briefing = state["briefings"]["Q2"]
        self.assertEqual("Q2", briefing["question_id"])
        self.assertEqual("analysis", briefing["class"])
        self.assertTrue(briefing["findings"])
        self.assertLessEqual(
            len(json.dumps(briefing)), runtime.BRIEFING_CHARS + 2000)

    def test_reserve_blocks_working_allows_review(self) -> None:
        root, run_id, case = fresh_case(self)
        coord = runtime.Coordinator(
            case,
            {"Q1": fact_script(),
             "Q2": {"challenge": {"counterarguments": "none",
                                  "risks": "none"}}},
            {"total": 2, "reserve": 2},
            context={"purpose": "lookup x"})
        report = coord.run(seeds=[
            {"text": "What was confirmed?", "class": "evidence"},
            {"text": "Integrate and disclose.", "class": "review"},
        ])
        state = CaseRecord(root, run_id).data["orchestration"]
        # The review ran inside the reserve without a budget
        # increase; new evidence investigation stayed paused.
        self.assertEqual("answered", report["questions"]["Q2"]["status"])
        self.assertEqual("proposed", report["questions"]["Q1"]["status"])
        self.assertEqual(1, state["allowance"]["used"])
        self.assertEqual(
            "paused: working allowance exhausted, reserve preserved",
            report["outcome"])
        self.assertTrue(any("reserve preserved" in d
                            for d in state["disclosures"]))

    def test_cancellation_preserves_partial_progress(self) -> None:
        root, run_id, case = fresh_case(self)
        coord = runtime.Coordinator(
            case,
            {"Q1": fact_script(),
             "Q2": {"assess": {"elements": [ELEMENT]}}},
            {"total": 4, "per_call": 1},
            context={"purpose": "lookup x"})
        first = coord.run(seeds=[
            {"text": "What was confirmed?", "class": "evidence"},
            {"text": "What does it establish?", "class": "analysis",
             "deps": ["Q1"]},
        ])
        self.assertEqual("answered", first["questions"]["Q1"]["status"])
        coord.cancel("operator stop")
        resumed = runtime.Coordinator(CaseRecord(root, run_id), {},
                                      {"total": 4})
        again = resumed.run()
        self.assertTrue(again["outcome"].startswith(
            "cancelled: operator stop"))
        self.assertEqual("answered", again["questions"]["Q1"]["status"])
        self.assertEqual("proposed", again["questions"]["Q2"]["status"])
        state = CaseRecord(root, run_id).data["orchestration"]
        self.assertEqual(1, state["allowance"]["used"])
        # Re-running neither resumes work nor duplicates usage:
        # the empty script map proves no driver call was attempted.
        third = runtime.Coordinator(
            CaseRecord(root, run_id), {}, {"total": 4}).run()
        self.assertEqual(again["outcome"], third["outcome"])
        entries = json.loads(
            (root / "workspace/runs" / run_id / "usage.json").read_text()
        )
        ours = [e for e in entries
                if str(e.get("purpose", "")).startswith("orchestration/")]
        self.assertEqual(1, len(ours))

    def test_recorded_opposing_facts_raise_runtime_contradiction(self) -> None:
        root, run_id, case = fresh_case(
            self, docs=("DOC-MINUTES", "DOC-REPORT"))
        case.set_checklist([support.checklist_item("item_x")])
        case.record_fact("item_x", "DOC-MINUTES", 1, QUOTE, "ready stated")
        oppose = "Open Severity 1 defects: 21 at period end."
        case.record_fact("item_x", "DOC-REPORT", 1, oppose, "not ready",
                         stance="opposes")
        report = runtime.run_orchestrated(
            root, run_id,
            {"Q1": fact_script(),
             "Q2": {"assess": {"elements": [ELEMENT]}},
             "Q3": {"challenge": {"counterarguments": "considered",
                                  "risks": "stated"}}},
            {"total": 8},
            seeds=[{"text": "What was confirmed?", "class": "evidence"},
                   {"text": "Is the system ready?", "class": "analysis",
                    "deps": ["Q1"]},
                   {"text": "Integrate and disclose.", "class": "review",
                    "deps": ["Q2"]}],
            context={"purpose": "lookup x"})
        # The analysis never addressed the recorded opposing
        # passage, so the runtime raised a real contradiction
        # from case data and the review integrated it.
        state = CaseRecord(root, run_id).data["orchestration"]
        self.assertEqual(1, len(state["contradictions"]))
        self.assertEqual("disclosed", state["contradictions"][0]["status"])
        self.assertEqual("Q2", state["contradictions"][0]["about"])
        self.assertIn("C1", " ".join(state["disclosures"]))
        self.assertEqual("answered", report["questions"]["Q2"]["status"])
        self.assertEqual("disclosed", report["questions"]["Q3"]["status"])

    def test_rerun_after_invalidation_replays_shared_scripts(self) -> None:
        root, run_id, case = fresh_case(self)
        scripts = {"Q1": {"gather": [fact_script()["gather"]]}}
        coord = runtime.Coordinator(case, scripts, {"total": 8},
                                    context={"purpose": "lookup x"})
        pid = contracts.add_premise(
            coord.orch.state, "deadline stands")["id"]
        coord.orch.classify()
        report = coord.run(seeds=[
            {"text": "What was confirmed?", "class": "evidence",
             "premises": [pid]},
        ])
        self.assertEqual("answered", report["questions"]["Q1"]["status"])
        # Invalidate so Q1 requeues; the same scripts object must
        # replay from the start instead of resuming mid-sequence.
        case = CaseRecord(root, run_id)
        coord = runtime.Coordinator(case, scripts, {"total": 8})
        coord.orch.invalidate_premise(pid, "deadline moved", "new statute")
        report = coord.run()
        self.assertEqual("answered", report["questions"]["Q1"]["status"])

    def test_run_leaves_caller_scripts_unmutated(self) -> None:
        import copy

        root, run_id, _ = fresh_case(self)
        scripts = {"Q1": {"gather": [fact_script()["gather"]] * 2}}
        before = copy.deepcopy(scripts)
        runtime.run_orchestrated(
            root, run_id, scripts, {"total": 4},
            seeds=[{"text": "What was confirmed?", "class": "evidence"}],
            context={"purpose": "lookup x"})
        self.assertEqual(before, scripts)

    def test_provide_excludes_flagged_transcripts(self) -> None:
        # M3-ext: engine context must not carry flagged docs. No
        # model client exists on this path today (scripted
        # judgments), so the leak is latent — pin it before any
        # future driver can transmit context externally.
        root, run_id, case = fresh_case(
            self, docs=("DOC-MINUTES", "DOC-REPORT"))
        case.flag_privileged("DOC-MINUTES", "synthetic privilege")
        case = CaseRecord(root, run_id)
        coord = runtime.Coordinator(case, {}, {"total": 8})
        provided = coord.orch.context_provider({}, coord.orch.state)
        self.assertNotIn("DOC-MINUTES", provided["transcripts"])
        self.assertIn("DOC-REPORT", provided["transcripts"])
        self.assertEqual(
            [], [e for e in provided["evidence"]
                 if e["document_id"] == "DOC-MINUTES"])


if __name__ == "__main__":
    unittest.main()
