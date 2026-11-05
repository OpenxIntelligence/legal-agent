"""First-principles reasoning: the deterministic library, its storage and
tools, the filing gate, and the reasoning benchmark (both modes offline)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from agent_kit import reasoning, tools
from agent_kit.case_record import CaseError, CaseRecord
from agent_kit.progress import new_run
from agent_kit.settings import verification_enabled
from tests import support


def answer(qclass="facts", **kw):
    entry = {
        "class": qclass,
        "question": f"Fixture question for {qclass}?",
        "rule_or_source": "fixture rule",
        "evidence": "fixture evidence",
        "answer": "fixture answer",
        "basis": "fixture basis",
        "contrary": "fixture contrary",
        "effect": "fixture effect",
        "status": "answered",
        "resolution": "",
        "issue": "",
    }
    entry.update(kw)
    return entry


def element(issue="not_ready", **kw):
    entry = {
        "issue": issue,
        "test": "fixture test",
        "element": "fixture element",
        "rule": "fixture rule",
        "source": "fixture source",
        "facts_evidence": "fixture facts",
        "application": "fixture application",
        "supporting": "fixture supporting",
        "opposing": "fixture opposing",
        "missing": "none identified",
        "finding": "fixture finding",
        "effect": "fixture effect",
    }
    entry.update(kw)
    return entry


def review(**kw):
    entry = {
        "strongest_against": "a",
        "fact_risks": "b",
        "adverse_authority": "c",
        "overlooked": "d",
        "opposing_counsel": "e",
        "judge_needs": "f",
        "alternative_theory": "g",
        "reversing_fact": "h",
        "revision_needed": False,
        "revision": "no change needed",
    }
    entry.update(kw)
    return entry


class ReasoningLibraryTest(unittest.TestCase):
    """agent_kit/reasoning.py: pure planning, validation, and gating."""

    def test_plan_covers_the_tasks_required_classes(self) -> None:
        for task in reasoning.TASK_TYPES:
            with self.subTest(task=task):
                planned = reasoning.plan_questions(task)
                self.assertEqual(
                    list(reasoning.REQUIRED_CLASSES[task]),
                    [p["class"] for p in planned],
                )
                self.assertTrue(all(p["prompt"] and p["why"] for p in planned))

    def test_plan_rejects_an_unknown_task(self) -> None:
        with self.assertRaises(ValueError):
            reasoning.plan_questions("divination")

    def test_answer_validation_needs_every_field(self) -> None:
        self.assertEqual([], reasoning.validate_answer(answer()))
        for field in reasoning.ANSWER_FIELDS:
            with self.subTest(field=field):
                bad = answer(**{field: "  "})
                self.assertTrue(reasoning.validate_answer(bad), field)

    def test_answer_validation_rejects_class_and_status(self) -> None:
        self.assertTrue(reasoning.validate_answer(answer(qclass="vibes")))
        self.assertTrue(reasoning.validate_answer(answer(status="maybe")))

    def test_unresolved_answers_need_a_resolution_path(self) -> None:
        bad = answer(status="unresolved")
        self.assertTrue(any("resolution" in p for p in reasoning.validate_answer(bad)))
        good = answer(status="unresolved", resolution="ask the client")
        self.assertEqual([], reasoning.validate_answer(good))

    def test_element_and_review_validation(self) -> None:
        self.assertEqual([], reasoning.validate_element(element()))
        self.assertTrue(reasoning.validate_element(element(rule="")))
        self.assertEqual([], reasoning.validate_adversarial(review()))
        self.assertTrue(reasoning.validate_adversarial(review(judge_needs="")))
        self.assertTrue(
            reasoning.validate_adversarial(review(revision_needed="yes"))
        )

    def test_numeric_confidence_detected_amounts_ignored(self) -> None:
        self.assertTrue(
            reasoning.detect_numeric_confidence("We are 85% confident it holds.")
        )
        self.assertTrue(
            reasoning.detect_numeric_confidence("Confidence: 0.9 that it holds.")
        )
        self.assertTrue(
            reasoning.detect_numeric_confidence(
                "The probability of success is 70%."
            )
        )
        for innocent in (
            "Paid invoices total 41000.",
            "Filed on June 1, 2026.",
            "4 of 14 criteria not met.",
            "100% of the pages were read.",
        ):
            self.assertEqual([], reasoning.detect_numeric_confidence(innocent))

    def test_legacy_runs_pass_with_an_advisory(self) -> None:
        result = reasoning.run_gate({"checklist": {}}, {"findings": []})
        self.assertEqual(
            ("legacy", True), (result["mode"], result["passed"])
        )
        self.assertEqual([], result["problems"])
        self.assertTrue(result["advisory"])

    def test_complete_reasoning_passes_the_gate(self) -> None:
        data = {
            "task_type": "investigation",
            "checklist": {"defects": {}},
            "foundational": [
                dict(answer(c), issue="defects" if c in ("facts", "evidence") else "")
                for c in reasoning.REQUIRED_CLASSES["investigation"]
            ],
            "adversarial": [review()],
        }
        memo = {
            "conclusion": "The count is 4.",
            "findings": [
                {"item": "defects", "status": "asserted", "summary": "Count is 4."}
            ],
            "gaps": "None.",
        }
        result = reasoning.run_gate(data, memo)
        self.assertEqual(("reasoning", True), (result["mode"], result["passed"]))

    def test_gate_names_each_missing_piece(self) -> None:
        data = {
            "task_type": "litigation",
            "checklist": {"timely": {}},
            "foundational": [answer("objective")],
            "facts": [{"id": "F1", "kind": "inference", "basis": ""}],
            "adversarial": [],
        }
        memo = {
            "conclusion": "We are 90% confident it is timely.",
            "findings": [
                {
                    "item": "timely",
                    "status": "asserted",
                    "summary": "Timely.",
                    "kind": "legal_rule",
                }
            ],
            "gaps": "",
        }
        result = reasoning.run_gate(data, memo)
        self.assertFalse(result["passed"])
        problems = " ".join(result["problems"])
        for want in (
            "'deadlines' is unanswered",
            "no linked foundational answer",
            "without an element analysis",
            "no adversarial review",
            "numeric confidence",
            "no basis",
        ):
            self.assertIn(want, problems)

    def test_gate_allows_explicit_unresolved_with_gaps(self) -> None:
        data = {
            "task_type": "investigation",
            "checklist": {"cert": {}},
            "foundational": [
                answer("objective"),
                answer("facts", issue="cert", status="unresolved",
                       resolution="release the certificate"),
                answer("evidence", issue="cert", status="unresolved",
                       resolution="release the certificate"),
                answer("adverse"),
                answer("unknowns"),
                answer("action"),
            ],
            "adversarial": [review()],
        }
        memo = {
            "conclusion": "Certification unknown.",
            "findings": [
                {"item": "cert", "status": "unresolved", "summary": "Unknown."}
            ],
            "gaps": "The certificate is outstanding.",
        }
        self.assertTrue(reasoning.run_gate(data, memo)["passed"])

    def test_gate_requires_provenance_for_legal_rules(self) -> None:
        data = {
            "task_type": "research",
            "checklist": {"limit": {}},
            "facts": [
                {"id": "F1", "kind": "legal_rule", "document_id": "UA-LAW"}
            ],
            "sources": {},
            "foundational": [
                dict(answer(c), issue="limit" if c == "governing_law" else "")
                for c in reasoning.REQUIRED_CLASSES["research"]
            ],
            "adversarial": [review()],
        }
        memo = {
            "conclusion": "Three years.",
            "findings": [
                {"item": "limit", "status": "asserted", "summary": "Three years."}
            ],
            "gaps": "None.",
        }
        result = reasoning.run_gate(data, memo)
        self.assertFalse(result["passed"])
        self.assertTrue(any("provenance" in p for p in result["problems"]))


class ReasoningRecordTest(unittest.TestCase):
    """CaseRecord storage for task type, answers, elements, reviews."""

    def setUp(self) -> None:
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        self.run_id = new_run(self.root, instructions="i", objective="o")
        self.case = CaseRecord(self.root, self.run_id)

    def test_task_type_validates_and_audits(self) -> None:
        self.assertEqual("research", self.case.set_task_type("research"))
        with self.assertRaises(CaseError):
            self.case.set_task_type("divination")

    def ask(self, **kw):
        entry = answer(kw.pop("qclass", "facts"), **kw)
        entry["qclass"] = entry.pop("class")
        return self.case.answer_foundational(**entry)

    def test_answers_validate_link_and_dedupe(self) -> None:
        self.case.set_checklist(support.CHECKLIST_ITEMS)
        first = self.ask(issue="not_ready", task_type="investigation")
        self.assertEqual("FQ1", first["id"])
        self.assertEqual("investigation", self.case.data["task_type"])
        again = self.ask(issue="not_ready")
        self.assertEqual("FQ1", again["id"])
        with self.assertRaises(CaseError):
            self.ask(issue="nope")
        with self.assertRaises(CaseError):
            self.ask(answer="")

    def test_elements_need_a_checklist_item_and_every_field(self) -> None:
        with self.assertRaises(CaseError):
            self.case.analyze_element(**element())
        self.case.set_checklist(support.CHECKLIST_ITEMS)
        entry = self.case.analyze_element(**element())
        self.assertEqual("EL1", entry["id"])
        with self.assertRaises(CaseError):
            self.case.analyze_element(**element(rule=""))

    def test_reviews_record_revision_decisions(self) -> None:
        entry = self.case.record_adversarial(**review())
        self.assertEqual("AR1", entry["id"])
        with self.assertRaises(CaseError):
            self.case.record_adversarial(**review(revision=""))


class ReasoningToolsTest(unittest.TestCase):
    """The three reasoning tools: offered, validated, recorded."""

    def setUp(self) -> None:
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        self.run_id = new_run(self.root, instructions="i", objective="o")
        self.fake = support.scripted(self.root, [])

    def call(self, name, args):
        return tools.run(
            self.fake.client, self.root, self.run_id,
            {"name": name, "arguments": args, "call_id": "call-1"},
        )

    def test_new_tools_are_offered(self) -> None:
        offered = {t["name"] for t in tools.definitions(self.root, self.run_id)}
        self.assertTrue(
            {"answer_foundational", "analyze_element", "adversarial_review"}
            <= offered
        )

    def test_answer_tool_sets_task_type_and_records(self) -> None:
        self.call("set_checklist", {"items": support.CHECKLIST_ITEMS})
        args = dict(answer("facts", issue="not_ready"), **{"class": "facts"})
        result = self.call("answer_foundational", {**args, "task_type": "investigation"})
        self.assertEqual("FQ1", result["answered"])
        self.assertEqual("investigation", result["task_type"])
        bad = dict(args, answer="")
        self.assertIn("error", self.call("answer_foundational", bad))

    def test_element_and_review_tools(self) -> None:
        self.call("set_checklist", {"items": support.CHECKLIST_ITEMS})
        result = self.call("analyze_element", element())
        self.assertEqual("EL1", result["analyzed"])
        self.assertIn("error", self.call("analyze_element", element(rule="")))
        result = self.call("adversarial_review", review())
        self.assertEqual("AR1", result["reviewed"])


SKIP = unittest.skipUnless(
    verification_enabled(),
    "reasoning gate tests (VERIFICATION=on, or ./helper test --verification)",
)


@SKIP
class ReasoningGateFilingTest(unittest.TestCase):
    """The gate inside real runs: sound memos file, unreasoned ones refuse."""

    def setUp(self) -> None:
        from legal_agent.loop import run_agent

        self.run_agent = run_agent
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)

    def answers(self):
        return [
            ("answer_foundational", {
                **answer("objective", task_type="investigation"),
                "class": "objective", "task_type": "investigation"}),
            ("answer_foundational", {
                **answer("facts", issue="not_ready"), "class": "facts"}),
            ("answer_foundational", {
                **answer("evidence", issue="ready_statement"), "class": "evidence"}),
            ("answer_foundational", {**answer("adverse"), "class": "adverse"}),
            ("answer_foundational", {**answer("unknowns"), "class": "unknowns"}),
            ("answer_foundational", {**answer("action"), "class": "action"}),
        ]

    def facts(self):
        return [
            ("record_fact", {
                "item": "not_ready", "document_id": "DOC-REPORT", "page": 1,
                "quote": "Open Severity 1 defects: 21",
                "claim": "21 Sev-1 open"}),
            ("record_fact", {
                "item": "ready_statement", "document_id": "DOC-MINUTES", "page": 1,
                "quote": "Mr. Lindgren confirmed the system is ready for go-live",
                "claim": "said ready"}),
        ]

    def memo(self):
        findings = [
            {"item": "not_ready", "status": "asserted",
             "summary": "Records show 21 open Sev-1 defects.",
             "citations": [{"document_id": "DOC-REPORT", "page": 1,
                            "quote": "Open Severity 1 defects: 21"}]},
            {"item": "ready_statement", "status": "asserted",
             "summary": "The vendor said the system was ready.",
             "citations": [{"document_id": "DOC-MINUTES", "page": 1,
                            "quote": "Mr. Lindgren confirmed the system is ready for go-live"}]},
        ]
        for item in ("timing", "seniority", "signatures", "counterevidence"):
            findings.append(
                {"item": item, "status": "unresolved",
                 "summary": f"{item} left for a later pass.", "citations": []}
            )
        return ("submit_memo", {
            "conclusion": "The system was not ready when readiness was declared.",
            "findings": findings,
            "gaps": "Timing, seniority, signatures, and counterevidence unresolved.",
        })

    def test_reasoned_memo_files(self) -> None:
        read = ("analyze_document", {
            "document_ids": ["DOC-REPORT", "DOC-MINUTES"],
            "request": "defects and statements"})
        script = ([support.CHECKLIST, *self.answers(), read, *self.facts(),
                   ("adversarial_review", review()), self.memo()])
        fake = support.scripted(self.root, script)
        result = self.run_agent(root=self.root, client=fake.client, turns=20)
        self.assertEqual("awaiting_signoff", result["status"])
        case = support.case(self.root, result["run_id"])
        self.assertEqual(
            ["memo", "reasoning", "claims"],
            [c["check"] for c in case["memo_checks"]],
        )

    def test_unreviewed_memo_refuses_at_the_gate(self) -> None:
        read = ("analyze_document", {
            "document_ids": ["DOC-REPORT", "DOC-MINUTES"],
            "request": "defects and statements"})
        script = [support.CHECKLIST, *self.answers(), read, *self.facts(),
                  self.memo()]
        fake = support.scripted(self.root, script)
        result = self.run_agent(root=self.root, client=fake.client, turns=20)
        refused = [
            out for name, _args, out in support.tool_calls(self.root, result["run_id"])
            if name == "submit_memo"
        ][-1]
        self.assertEqual("reasoning", refused["check"])
        self.assertTrue(any("adversarial" in p for p in refused["problems"]))

    def test_identified_memo_files_with_full_chain(self) -> None:
        read = ("analyze_document", {
            "document_ids": ["DOC-REPORT", "DOC-MINUTES"],
            "request": "defects and statements"})
        identify = ("identify_task", {
            **identification(), **classification()})
        validate = ("validate_decomposition", {
            "questions": [
                dict(q, issue="not_ready" if q["class"] == "facts"
                     else ("ready_statement" if q["class"] == "evidence" else ""))
                for q in investigation_plan()
            ],
        })
        workflow = ("plan_workflow", {
            "components": [
                {"workflow": "fact_evidence", "purpose": "p"},
                {"workflow": "quality_control", "purpose": "p",
                 "after": ["fact_evidence"]},
            ],
            "rationale": "r",
        })
        product = ("declare_work_product", {
            "product": "direct_answer", "sections": ["answer"],
            "traceability": "t",
        })
        script = ([identify, support.CHECKLIST, validate, *self.answers(),
                   read, *self.facts(), workflow, product,
                   ("adversarial_review", review()), self.memo()])
        fake = support.scripted(self.root, script)
        result = self.run_agent(root=self.root, client=fake.client, turns=25)
        self.assertEqual("awaiting_signoff", result["status"])
        case = support.case(self.root, result["run_id"])
        self.assertEqual(
            ["memo", "reasoning", "claims"],
            [c["check"] for c in case["memo_checks"]],
        )
        self.assertEqual("evidence", case["classification"]["primary"])

    def test_draft_runs_the_gate_without_filing(self) -> None:
        read = ("analyze_document", {
            "document_ids": ["DOC-REPORT", "DOC-MINUTES"],
            "request": "defects and statements"})
        name, args = self.memo()
        draft = ("review_draft", args)
        script = [support.CHECKLIST, *self.answers(), read, *self.facts(), draft]
        fake = support.scripted(self.root, script)
        result = self.run_agent(root=self.root, client=fake.client, turns=20)
        drafts = [
            out for name, _args, out in support.tool_calls(self.root, result["run_id"])
            if name == "review_draft"
        ]
        self.assertEqual("reasoning", drafts[0]["check"])
        self.assertFalse(drafts[0]["passed"])
        self.assertIsNone(support.case(self.root, result["run_id"])["memo"])


class ReasoningBenchmarkTest(unittest.TestCase):
    """The benchmark contract: schema, determinism, two-sided comparison."""

    def test_reports_carry_the_schema(self) -> None:
        from eval import reasoning_run

        for report in reasoning_run.run_all():
            with self.subTest(case=report["case"], mode=report["mode"]):
                self.assertEqual(reasoning_run.REPORT_KEYS, set(report))
                self.assertEqual("scripted", report["client"])
                self.assertTrue(
                    report["semantic_quality"].startswith("not measured")
                )

    def test_runs_are_deterministic(self) -> None:
        from eval import reasoning_run

        self.assertEqual(reasoning_run.run_all(), reasoning_run.run_all())

    def test_fixtures_are_two_sided_with_truth(self) -> None:
        from eval import reasoning_cases, reasoning_run

        sounds = [n for n, c in reasoning_cases.CASES.items() if c["sound"]]
        defective = [n for n, c in reasoning_cases.CASES.items() if not c["sound"]]
        self.assertTrue(sounds and defective)
        for name, case in reasoning_cases.CASES.items():
            with self.subTest(case=name):
                self.assertIn(case["task_type"], reasoning.TASK_TYPES)
                for key in ("gate", "rules", "claims"):
                    self.assertIn(key, case["truth"])
                self.assertTrue(reasoning_run.run_fixture(name, with_reasoning=True))

    def test_upgraded_discriminates_while_baseline_fails_closed(
        self,
    ) -> None:
        # Closeout §7.1: unidentified filings fail closed, so the
        # baseline refuses every fixture — sound included. The
        # upgraded path must discriminate instead: file all sound
        # fixtures, catch all defective ones, and catch each for
        # its planted defect (gate verdicts match truth).
        from eval import reasoning_run

        summary = reasoning_run.summarize(reasoning_run.run_all())
        base, up = summary["baseline"], summary["upgraded"]
        self.assertEqual(
            up["sound_filed"]["total"], up["sound_filed"]["hit"]
        )
        self.assertEqual(
            up["defects_caught"]["total"],
            up["defects_caught"]["hit"],
        )
        self.assertEqual(0, base["sound_filed"]["hit"])
        self.assertEqual(
            base["defects_caught"]["total"],
            base["defects_caught"]["hit"],
        )
        self.assertEqual(
            up["gate_correct"]["total"], up["gate_correct"]["hit"]
        )
        self.assertTrue(summary["comparison"]["verdict"].startswith("IMPROVED"))


def synthetic_progress(tool_name, call_args, result, status, turn=1):
    call_id = "call-1"
    return {
        "run_id": "run-test",
        "verification": True,
        "model": "scripted",
        "started": "2026-01-01T00:00:00+00:00",
        "status": status,
        "turn": turn,
        "messages": [
            {"role": "user", "content": "objective"},
            {
                "type": "function_call",
                "call_id": call_id,
                "name": tool_name,
                "arguments": json.dumps(call_args),
            },
            {
                "type": "function_call_output",
                "call_id": call_id,
                "output": json.dumps(result),
            },
        ],
        "turns": [
            {
                "time": "2026-01-01T00:01:00+00:00",
                "status": status,
                "end": 3,
                "turn": turn,
                "saw": 0,
                "output": 2,
                "input_tokens": 10,
                "output_tokens": 5,
            }
        ],
        "notes": {},
    }


class ReasoningConsoleTest(unittest.TestCase):
    """Console data path for the reasoning gate, plus static render checks.

    Rendered states are covered at the data path plus `node --check`
    and headless execution of reasoningHtml/reasoningSummary — never
    by looking at pixels (see docs/agent-v2-ui-validation.md).
    """

    def setUp(self) -> None:
        self.ui = support.console_ui()

    def check_events(self, events, check):
        return [
            e
            for e in events
            if e["event_type"] == "verification"
            and e["payload"].get("check") == check
        ]

    def test_refusal_emits_memo_passed_then_reasoning_failed(self) -> None:
        progress = synthetic_progress(
            "submit_memo",
            {"conclusion": "x"},
            {
                "error": "memo refused",
                "check": "reasoning",
                "problems": ["no adversarial review recorded"],
            },
            "running",
        )
        events = self.ui.build_events(progress, {"requests": []}, None)
        (memo,) = self.check_events(events, "memo")
        self.assertTrue(memo["payload"]["passed"])
        (gate,) = self.check_events(events, "reasoning")
        self.assertFalse(gate["payload"]["passed"])
        self.assertEqual(
            ["no adversarial review recorded"],
            gate["payload"]["problems"],
        )
        self.assertEqual(1, self.ui.metrics(events, None)["rejected"])

    def test_filed_reasoning_run_emits_reasoning_passed(self) -> None:
        progress = synthetic_progress(
            "submit_memo",
            {"conclusion": "x"},
            {
                "filed": True,
                "memo": "awaiting_signoff",
                "status": "awaiting_signoff",
                "check": "claims",
                "outcome": "passed",
            },
            "awaiting_signoff",
        )
        case = {
            "requests": [],
            "memo_checks": [
                {"check": "memo", "passed": True, "problems": []},
                {"check": "reasoning", "passed": True, "problems": []},
                {"check": "claims", "passed": True, "problems": []},
            ],
        }
        events = self.ui.build_events(progress, case, None)
        (gate,) = self.check_events(events, "reasoning")
        self.assertTrue(gate["payload"]["passed"])

    def test_legacy_filing_has_no_reasoning_event(self) -> None:
        progress = synthetic_progress(
            "submit_memo",
            {"conclusion": "x"},
            {
                "filed": True,
                "memo": "awaiting_signoff",
                "status": "awaiting_signoff",
                "check": "claims",
                "outcome": "passed",
            },
            "awaiting_signoff",
        )
        case = {
            "requests": [],
            "memo_checks": [
                {"check": "memo", "passed": True, "problems": []},
                {"check": "claims", "passed": True, "problems": []},
            ],
        }
        events = self.ui.build_events(progress, case, None)
        self.assertEqual([], self.check_events(events, "reasoning"))

    def test_reasoning_panel_is_rendered_and_wired(self) -> None:
        text = (
            Path(__file__).resolve().parents[2] / "ui" / "index.html"
        ).read_text()
        for snippet in (
            "function reasoningHtml(cs)",
            "function reasoningSummary(cs)",
            '<h2>Reasoning <span id="rscount"',
            'setHtml($("reasoning"), reasoningHtml(cs))',
            "No first-principles reasoning recorded",
            '<b>Reasoning</b><span>${esc(reasoningSummary(cs))}</span>',
            'p.check === "reasoning"',
            "Memo refused by the reasoning gate",
            "· reasoning: ",
            "the reasoning gate, then the claims check",
            "answer_foundational",
            "analyze_element",
            "adversarial_review",
            "Disproven: ",
            "d.counter_evidence",
            "identify_task",
            "plan_workflow",
            "triage_gap",
            "declare_work_product",
            "Task identified",
            "Work product declared",
            "Identification</span>",
            "validate_decomposition",
            "Decomposition v",
            "register_authority",
            "suggest_next_action",
            "request_approval",
            "decide_approval",
            "Authority ",
            "treatment ",
        ):
            with self.subTest(snippet=snippet[:40]):
                self.assertIn(snippet, text)


def identification(**kw):
    entry = {
        "requested": "req",
        "objective": "obj",
        "deliverable": "del",
        "underlying_problem": "prob",
        "context_available": "ctx",
        "missing_or_ambiguous": "gaps",
        "constraints": "none",
    }
    entry.update(kw)
    return entry


def classification(names=("evidence",), **kw):
    entry = {
        "categories": [
            {"category": n, "subcategory": "", "rationale": f"why {n}"}
            for n in names
        ],
        "dependencies": kw.pop("dependencies", []),
        "primary": kw.pop("primary", names[0]),
        "gate_task_type": kw.pop("gate_task_type", "investigation"),
    }
    entry.update(kw)
    return entry


class IdentificationLibraryTest(unittest.TestCase):
    """Taxonomy, workflow catalog, validators, dependency checks."""

    def test_taxonomy_is_self_consistent(self) -> None:
        self.assertIn("other", reasoning.TASK_TAXONOMY)
        for name, entry in reasoning.TASK_TAXONOMY.items():
            with self.subTest(category=name):
                self.assertIn(entry["gate"], reasoning.TASK_TYPES)
                for workflow in entry["workflows"]:
                    self.assertIn(workflow, reasoning.WORKFLOWS)
                for product in entry["products"]:
                    self.assertIn(product, reasoning.WORK_PRODUCTS)

    def test_workflow_tools_are_registered(self) -> None:
        from agent_kit.tools import MODULES

        for name, entry in reasoning.WORKFLOWS.items():
            with self.subTest(workflow=name):
                for tool in entry["tools"]:
                    self.assertIn(tool, MODULES)

    def test_plan_case_unions_guidance(self) -> None:
        plan = reasoning.plan_case(["contract", "litigation"])
        self.assertIn("governing law", plan["thresholds"])
        self.assertIn("jurisdiction", plan["thresholds"])
        self.assertIn("contract_interpretation", plan["workflows"])
        self.assertIn("procedure_deadlines", plan["workflows"])
        self.assertIn("draft_agreement", plan["products"])
        self.assertIn("litigation_analysis", plan["products"])
        with self.assertRaises(ValueError):
            reasoning.plan_case(["divination"])

    def test_identification_needs_every_field(self) -> None:
        self.assertEqual([], reasoning.validate_identification(identification()))
        bad = identification(objective="  ")
        self.assertTrue(reasoning.validate_identification(bad))

    def test_classification_validates_multi_label(self) -> None:
        good = classification(("contract", "litigation"))
        self.assertEqual(
            [], reasoning.validate_classification(**good)
        )
        self.assertTrue(
            reasoning.validate_classification([], [], "x", "investigation")
        )
        self.assertTrue(
            reasoning.validate_classification(
                [{"category": "nope", "rationale": "r"}],
                [], "nope", "investigation",
            )
        )
        self.assertEqual(
            [],
            reasoning.validate_classification(
                good["categories"], good["dependencies"], "litigation",
                "investigation",
            ),
        )
        self.assertTrue(
            reasoning.validate_classification(
                good["categories"], good["dependencies"], "other",
                "investigation",
            )
        )
        self.assertTrue(
            reasoning.validate_classification(
                good["categories"],
                [{"from": "contract", "to": "contract"}],
                "contract", "investigation",
            )
        )
        self.assertTrue(
            reasoning.validate_classification(
                good["categories"],
                [
                    {"from": "contract", "to": "litigation"},
                    {"from": "litigation", "to": "contract"},
                ],
                "contract", "investigation",
            )
        )

    def test_workflow_validates_order(self) -> None:
        good = [
            {"workflow": "fact_evidence", "purpose": "p", "after": []},
            {"workflow": "quality_control", "purpose": "p",
             "after": ["fact_evidence"]},
        ]
        self.assertEqual([], reasoning.validate_workflow(good))
        self.assertTrue(reasoning.validate_workflow([]))
        self.assertTrue(
            reasoning.validate_workflow(
                [{"workflow": "nope", "purpose": "p", "after": []}]
            )
        )
        self.assertTrue(
            reasoning.validate_workflow(
                [
                    {"workflow": "fact_evidence", "purpose": "p",
                     "after": ["quality_control"]},
                    {"workflow": "quality_control", "purpose": "p",
                     "after": ["fact_evidence"]},
                ]
            )
        )

    def test_triage_and_product_validate(self) -> None:
        triage = {
            "ref": "FQ1", "gap": "g", "needed_for": "n",
            "sources_checked": "s", "decision": "ask_user", "detail": "d",
        }
        self.assertEqual([], reasoning.validate_triage(triage))
        self.assertTrue(
            reasoning.validate_triage(dict(triage, decision="guess"))
        )
        self.assertTrue(
            reasoning.validate_triage(dict(triage, detail=""))
        )
        product = {
            "product": "direct_answer",
            "sections": ["answer"],
            "traceability": "t",
        }
        self.assertEqual([], reasoning.validate_work_product(product))
        self.assertTrue(
            reasoning.validate_work_product(dict(product, product="novel"))
        )
        self.assertTrue(
            reasoning.validate_work_product(dict(product, sections=[]))
        )

    def test_answer_edges_catch_cycles_and_dangles(self) -> None:
        good = [
            {"id": "FQ1", "depends_on": []},
            {"id": "FQ2", "depends_on": ["FQ1"]},
        ]
        self.assertEqual([], reasoning.validate_answer_edges(good))
        self.assertTrue(
            reasoning.validate_answer_edges(
                [{"id": "FQ1", "depends_on": ["FQ9"]}]
            )
        )
        self.assertTrue(
            reasoning.validate_answer_edges(
                [
                    {"id": "FQ1", "depends_on": ["FQ2"]},
                    {"id": "FQ2", "depends_on": ["FQ1"]},
                ]
            )
        )


class IdentificationRecordTest(unittest.TestCase):
    """CaseRecord storage for the identification chain."""

    def setUp(self) -> None:
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        self.run_id = new_run(self.root, instructions="i", objective="o")
        self.case = CaseRecord(self.root, self.run_id)
        self.case.set_checklist(support.CHECKLIST_ITEMS)

    def test_identify_sets_chain_and_task_type(self) -> None:
        entry = self.case.identify_task(
            **identification(), **classification()
        )
        self.assertEqual("investigation", self.case.data["task_type"])
        self.assertTrue(reasoning.identified_mode(self.case.data))
        self.assertEqual("evidence", entry["classification"]["primary"])
        with self.assertRaises(CaseError):
            self.case.identify_task(
                **identification(objective=""), **classification()
            )
        with self.assertRaises(CaseError):
            self.case.identify_task(
                **identification(),
                **classification(names=("nope",), primary="nope"),
            )

    def test_workflow_triage_product_record(self) -> None:
        plan = self.case.plan_workflow(
            components=[
                {"workflow": "fact_evidence", "purpose": "p"},
                {"workflow": "quality_control", "purpose": "p",
                 "after": ["fact_evidence"]},
            ],
            rationale="r",
        )
        self.assertEqual(2, len(plan["components"]))
        with self.assertRaises(CaseError):
            self.case.plan_workflow(components=[], rationale="r")
        asked = self.ask()
        triage = self.case.triage_gap(
            ref=asked["id"], gap="g", needed_for="n",
            sources_checked="s", decision="investigate", detail="d",
        )
        self.assertEqual("TG1", triage["id"])
        with self.assertRaises(CaseError):
            self.case.triage_gap(
                ref="FQ9", gap="g", needed_for="n",
                sources_checked="s", decision="investigate", detail="d",
            )
        self.case.note_context("forum", "", "unknown", "p")
        self.case.triage_gap(
            ref="matter:forum", gap="g", needed_for="n",
            sources_checked="s", decision="ask_user", detail="d",
        )
        with self.assertRaises(CaseError):
            self.case.triage_gap(
                ref="matter:nope", gap="g", needed_for="n",
                sources_checked="s", decision="ask_user", detail="d",
            )
        declared = self.case.declare_work_product(
            product="direct_answer", sections=["answer"],
            traceability="t",
        )
        self.assertEqual("direct_answer", declared["product"])
        with self.assertRaises(CaseError):
            self.case.declare_work_product(
                product="novel", sections=["answer"], traceability="t"
            )

    def test_answers_take_dependencies_in_order(self) -> None:
        first = self.ask(qclass="objective")
        second = self.ask(qclass="claim", depends_on=[first["id"]])
        self.assertEqual([first["id"]], second["depends_on"])
        with self.assertRaises(CaseError):
            self.ask(qclass="claim", depends_on=["FQ9"])

    def ask(self, qclass="facts", **kw):
        entry = answer(qclass, **kw)
        entry["qclass"] = entry.pop("class")
        return self.case.answer_foundational(**entry)


class IdentificationToolsTest(unittest.TestCase):
    """The four identification tools: offered, validated, recorded."""

    def setUp(self) -> None:
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        self.run_id = new_run(self.root, instructions="i", objective="o")
        self.fake = support.scripted(self.root, [])

    def call(self, name, args):
        return tools.run(
            self.fake.client, self.root, self.run_id,
            {"name": name, "arguments": args, "call_id": "call-1"},
        )

    def test_new_tools_are_offered(self) -> None:
        offered = {t["name"] for t in tools.definitions(self.root, self.run_id)}
        self.assertTrue(
            {"identify_task", "plan_workflow", "triage_gap",
             "declare_work_product"} <= offered
        )

    def test_identify_tool_records_chain(self) -> None:
        self.call("set_checklist", {"items": support.CHECKLIST_ITEMS})
        result = self.call(
            "identify_task",
            {**identification(), **classification()},
        )
        self.assertTrue(result["identified"])
        self.assertEqual("investigation", result["task_type"])
        bad = {**identification(objective=""), **classification()}
        self.assertIn("error", self.call("identify_task", bad))

    def test_workflow_triage_product_tools(self) -> None:
        self.call("set_checklist", {"items": support.CHECKLIST_ITEMS})
        result = self.call(
            "plan_workflow",
            {"components": [{"workflow": "fact_evidence", "purpose": "p"}]},
        )
        self.assertEqual(["fact_evidence"], result["planned"])
        self.assertIn("error", self.call("plan_workflow", {"components": []}))
        args = dict(answer("facts"), **{"class": "facts"})
        fq = self.call("answer_foundational", args)["answered"]
        result = self.call(
            "triage_gap",
            {"ref": fq, "gap": "g", "needed_for": "n",
             "sources_checked": "s", "decision": "accept", "detail": "d"},
        )
        self.assertEqual("TG1", result["triaged"])
        self.assertIn(
            "error",
            self.call(
                "triage_gap",
                {"ref": "FQ9", "gap": "g", "needed_for": "n",
                 "sources_checked": "s", "decision": "accept", "detail": "d"},
            ),
        )
        result = self.call(
            "declare_work_product",
            {"product": "direct_answer", "sections": ["answer"],
             "traceability": "t"},
        )
        self.assertEqual("direct_answer", result["declared"])

    def test_answer_tool_takes_dependencies(self) -> None:
        self.call("set_checklist", {"items": support.CHECKLIST_ITEMS})
        first = self.call(
            "answer_foundational", dict(answer("objective"), **{"class": "objective"})
        )["answered"]
        result = self.call(
            "answer_foundational",
            dict(answer("claim"), **{"class": "claim", "depends_on": [first],
                                     "threshold": True}),
        )
        self.assertEqual("FQ2", result["answered"])
        bad = dict(
            answer("claim"),
            **{"class": "claim", "depends_on": ["FQ9"]},
        )
        self.assertIn("error", self.call("answer_foundational", bad))


class IdentifiedGateTest(unittest.TestCase):
    """Identified mode: the full chain gates filing."""

    def plan(self):
        return [
            {
                "id": f"p{n + 1}",
                "class": c,
                "question": f"Fixture plan for {c}?",
                "criterion": "Fixture criterion.",
                "issue": "defects" if c in ("facts", "evidence") else "",
                "depends_on": [],
                "threshold": False,
            }
            for n, c in enumerate(reasoning.REQUIRED_CLASSES["investigation"])
        ]

    def chain(self):
        return {
            "task_type": "investigation",
            "checklist": {"defects": {}},
            "identification": identification(),
            "classification": classification(),
            "workflow": {
                "components": [
                    {"workflow": "fact_evidence", "purpose": "p", "after": []},
                    {"workflow": "quality_control", "purpose": "p",
                     "after": ["fact_evidence"]},
                ],
            },
            "work_product": {
                "product": "direct_answer",
                "sections": ["answer"],
                "traceability": "t",
            },
            "decompositions": [
                {
                    "version": 1,
                    "questions": self.plan(),
                    "waived": [],
                    "verdict": "pass",
                    "problems": [],
                    "advisory": [],
                }
            ],
            "foundational": [
                dict(answer(c), id=f"FQ{n + 1}",
                     issue="defects" if c in ("facts", "evidence") else "")
                for n, c in enumerate(reasoning.REQUIRED_CLASSES["investigation"])
            ],
            "adversarial": [review()],
        }

    def memo(self):
        return {
            "conclusion": "The count is 4.",
            "findings": [
                {"item": "defects", "status": "asserted", "summary": "Count is 4."}
            ],
            "gaps": "None.",
        }

    def test_complete_chain_passes_identified(self) -> None:
        result = reasoning.run_gate(self.chain(), self.memo())
        self.assertEqual("identified", result["mode"])
        self.assertTrue(result["passed"], result["problems"])

    def test_each_missing_link_fails(self) -> None:
        variants = {
            "workflow": ("workflow", {}),
            "work product": ("work_product", {}),
            "decomposition": ("decompositions", []),
        }
        for label, (key, empty) in variants.items():
            with self.subTest(missing=label):
                data = self.chain()
                data[key] = empty
                result = reasoning.run_gate(data, self.memo())
                self.assertFalse(result["passed"])
        data = self.chain()
        data["classification"]["gate_task_type"] = "research"
        result = reasoning.run_gate(data, self.memo())
        self.assertFalse(result["passed"])

    def test_failed_and_uncovered_decompositions_fail(self) -> None:
        data = self.chain()
        data["decompositions"][-1]["verdict"] = "fail"
        data["decompositions"][-1]["problems"] = ["plan omits deadlines"]
        result = reasoning.run_gate(data, self.memo())
        self.assertTrue(
            any("latest decomposition failed" in p for p in result["problems"])
        )
        data = self.chain()
        data["decompositions"][-1]["questions"].append(
            {
                "id": "p9", "class": "deadlines",
                "question": "Any deadlines?", "criterion": "c",
                "issue": "", "depends_on": [], "threshold": False,
            }
        )
        result = reasoning.run_gate(data, self.memo())
        self.assertTrue(
            any("has no recorded answer" in p for p in result["problems"])
        )

    def test_untriaged_unknowns_fail(self) -> None:
        data = self.chain()
        data["foundational"][1]["status"] = "unresolved"
        data["foundational"][1]["resolution"] = "ask the client"
        result = reasoning.run_gate(data, self.memo())
        self.assertTrue(any("no triage decision" in p for p in result["problems"]))
        data["triages"] = [
            {"ref": "FQ2", "decision": "ask_user", "detail": "d"},
        ]
        data["matter"] = {"fields": {"forum": {"status": "unknown"}}}
        result = reasoning.run_gate(data, self.memo())
        self.assertTrue(any("matter field forum" in p for p in result["problems"]))

    def test_threshold_and_order_advisories(self) -> None:
        data = self.chain()
        data["classification"]["categories"] = [
            {"category": "litigation", "subcategory": "",
             "rationale": "r"}
        ]
        data["classification"]["primary"] = "litigation"
        result = reasoning.run_gate(data, self.memo())
        self.assertTrue(
            any("threshold areas" in a for a in result["advisory"])
        )


class IdentificationBenchmarkTest(unittest.TestCase):
    """The benchmark contract: schema, determinism, two-sided comparison."""

    def test_reports_carry_the_schema(self) -> None:
        from eval import identification_run

        for report in identification_run.run_all():
            with self.subTest(case=report["case"], mode=report["mode"]):
                self.assertEqual(identification_run.REPORT_KEYS, set(report))
                self.assertEqual("scripted", report["client"])
                self.assertTrue(
                    report["semantic_quality"].startswith("not measured")
                )

    def test_runs_are_deterministic(self) -> None:
        from eval import identification_run

        self.assertEqual(
            identification_run.run_all(), identification_run.run_all()
        )

    def test_fixtures_are_two_sided_with_truth(self) -> None:
        from eval import identification_cases, identification_run

        sounds = [n for n, c in identification_cases.CASES.items() if c["sound"]]
        defective = [
            n for n, c in identification_cases.CASES.items() if not c["sound"]
        ]
        self.assertTrue(sounds and defective)
        for name, case in identification_cases.CASES.items():
            with self.subTest(case=name):
                for key in (
                    "identified_gate", "baseline_gate", "rules", "claims"
                ):
                    self.assertIn(key, case["truth"])
                self.assertTrue(
                    identification_run.run_fixture(
                        name, with_identification=True
                    )
                )

    def test_identified_path_catches_more_preserves_sound(self) -> None:
        from eval import identification_run

        summary = identification_run.summarize(identification_run.run_all())
        base, up = summary["baseline"], summary["identified"]
        self.assertEqual(up["sound_filed"]["total"], up["sound_filed"]["hit"])
        self.assertGreater(
            up["defects_caught"]["hit"], base["defects_caught"]["hit"]
        )
        self.assertGreater(
            up["deterministic_catches"]["hit"],
            base["deterministic_catches"]["hit"],
        )
        self.assertTrue(summary["comparison"]["verdict"].startswith("IMPROVED"))


def planned(qid, qclass, text, **kw):
    entry = {
        "id": qid, "class": qclass, "question": text,
        "criterion": "criterion", "issue": "",
        "depends_on": [], "threshold": False,
    }
    entry.update(kw)
    return entry


def investigation_plan():
    return [
        planned(f"p{n + 1}", c, f"Plan for {c}?")
        for n, c in enumerate(reasoning.REQUIRED_CLASSES["investigation"])
    ]


class DecompositionValidatorTest(unittest.TestCase):
    """The decomposition validator: plan quality before work."""

    def test_complete_plan_passes(self) -> None:
        problems, advisory = reasoning.validate_decomposition(
            investigation_plan(), [], ["evidence"], "investigation"
        )
        self.assertEqual([], problems)
        self.assertTrue(advisory)

    def test_missing_class_and_criterion_fail(self) -> None:
        plan = [q for q in investigation_plan() if q["class"] != "action"]
        problems, _ = reasoning.validate_decomposition(
            plan, [], ["evidence"], "investigation"
        )
        self.assertTrue(any("'action' is not planned" in p for p in problems))
        plan = investigation_plan()
        plan[0]["criterion"] = "  "
        problems, _ = reasoning.validate_decomposition(
            plan, [], ["evidence"], "investigation"
        )
        self.assertTrue(any("resolution criterion" in p for p in problems))

    def test_redundant_and_cyclic_plans_fail(self) -> None:
        plan = investigation_plan()
        plan[1]["question"] = plan[0]["question"]
        problems, _ = reasoning.validate_decomposition(
            plan, [], ["evidence"], "investigation"
        )
        self.assertTrue(any("redundant" in p for p in problems))
        plan = investigation_plan()
        plan[0]["depends_on"] = ["p2"]
        plan[1]["depends_on"] = ["p1"]
        problems, _ = reasoning.validate_decomposition(
            plan, [], ["evidence"], "investigation"
        )
        self.assertTrue(any("cyclic" in p for p in problems))
        plan = investigation_plan()
        plan[0]["depends_on"] = ["p9"]
        problems, _ = reasoning.validate_decomposition(
            plan, [], ["evidence"], "investigation"
        )
        self.assertTrue(any("unknown" in p for p in problems))

    def test_thresholds_need_plans_or_waivers(self) -> None:
        problems, _ = reasoning.validate_decomposition(
            investigation_plan(), [], ["advice"], "investigation"
        )
        self.assertTrue(
            any("governing law" in p for p in problems),
            "advice threshold governing law unplanned",
        )
        problems, _ = reasoning.validate_decomposition(
            investigation_plan(),
            [{"area": "governing law", "reason": "forum unknown"}],
            ["advice"],
            "investigation",
        )
        self.assertEqual([], problems)
        problems, _ = reasoning.validate_decomposition(
            investigation_plan(),
            [{"area": "nope", "reason": "r"}],
            ["advice"],
            "investigation",
        )
        self.assertTrue(any("unknown threshold area" in p for p in problems))

    def test_coverage_matches_pairs(self) -> None:
        planned_list = investigation_plan()
        planned_list[1]["issue"] = "defects"
        answers = [
            {"id": "FQ1", "class": "objective", "issue": ""},
            {"id": "FQ2", "class": "facts", "issue": "defects"},
        ]
        uncovered, unplanned = reasoning.decomposition_coverage(
            planned_list, answers
        )
        self.assertTrue(any("evidence" in u for u in uncovered))
        self.assertFalse(any("facts" in u for u in uncovered))
        self.assertEqual([], unplanned)
        answers.append({"id": "FQ9", "class": "deadlines", "issue": ""})
        _, unplanned = reasoning.decomposition_coverage(planned_list, answers)
        self.assertTrue(any("outside the validated plan" in u for u in unplanned))


class DecompositionRecordTest(unittest.TestCase):
    """CaseRecord + tool for decomposition validation."""

    def setUp(self) -> None:
        self.root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        self.run_id = new_run(self.root, instructions="i", objective="o")
        self.case = CaseRecord(self.root, self.run_id)
        self.fake = support.scripted(self.root, [])

    def test_validate_requires_identification(self) -> None:
        with self.assertRaises(CaseError):
            self.case.validate_decomposition(
                questions=investigation_plan(), waived=[]
            )

    def test_validate_records_pass_and_fail(self) -> None:
        self.case.identify_task(**identification(), **classification())
        record = self.case.validate_decomposition(
            questions=investigation_plan(), waived=[]
        )
        self.assertEqual("pass", record["verdict"])
        self.assertEqual(1, record["version"])
        bad = [q for q in investigation_plan() if q["class"] != "action"]
        record = self.case.validate_decomposition(questions=bad, waived=[])
        self.assertEqual("fail", record["verdict"])
        self.assertEqual(2, record["version"])
        self.assertTrue(record["problems"])

    def test_validate_tool_is_offered_and_reports(self) -> None:
        offered = {t["name"] for t in tools.definitions(self.root, self.run_id)}
        self.assertIn("validate_decomposition", offered)
        result = tools.run(
            self.fake.client, self.root, self.run_id,
            {"name": "validate_decomposition",
             "arguments": {"questions": investigation_plan()},
             "call_id": "call-1"},
        )
        self.assertIn("error", result)
        tools.run(
            self.fake.client, self.root, self.run_id,
            {"name": "identify_task",
             "arguments": {**identification(), **classification()},
             "call_id": "call-2"},
        )
        result = tools.run(
            self.fake.client, self.root, self.run_id,
            {"name": "validate_decomposition",
             "arguments": {"questions": investigation_plan()},
             "call_id": "call-3"},
        )
        self.assertEqual("pass", result["verdict"])


def authority(**kw):
    entry = {
        "proposition": "Breach requires duty and breach.",
        "document_id": "DOC-REPORT",
        "citation": "Fixture Code",
        "version": "2026-01-01",
        "effective_date": "2026-01-01",
        "level": "binding",
        "verification": "verified",
        "treatment_checked": True,
        "treatment": "No contrary treatment found.",
    }
    entry.update(kw)
    return entry


class AuthorityRegistryTest(unittest.TestCase):
    """Proposition-level authority registration and gating."""

    def test_validation_layers(self) -> None:
        self.assertEqual([], reasoning.validate_authority(authority()))
        self.assertTrue(
            reasoning.validate_authority(authority(proposition=""))
        )
        self.assertTrue(reasoning.validate_authority(authority(level="rumor")))
        self.assertTrue(
            reasoning.validate_authority(authority(effective_date="recently"))
        )
        self.assertTrue(
            reasoning.validate_authority(authority(verification="certain"))
        )
        self.assertTrue(
            reasoning.validate_authority(
                authority(verification="verified", treatment_checked=False)
            )
        )
        self.assertEqual(
            [],
            reasoning.validate_authority(
                authority(verification="provisional", treatment_checked=False)
            ),
        )

    def test_registration_needs_provenance_first(self) -> None:
        root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        run_id = new_run(root, instructions="i", objective="o")
        case = CaseRecord(root, run_id)
        with self.assertRaises(CaseError):
            case.register_authority(**authority())
        fake = support.scripted(root, [])
        tools.run(
            fake.client, root, run_id,
            {"name": "analyze_document",
             "arguments": {"document_ids": ["DOC-REPORT"], "request": "x"},
             "call_id": "call-1"},
        )
        tools.run(
            fake.client, root, run_id,
            {"name": "note_source",
             "arguments": {"document_id": "DOC-REPORT", "repo": "ca-leginfo",
                           "citation": "Fixture", "version": "2026-01-01"},
             "call_id": "call-2"},
        )
        entry = tools.run(
            fake.client, root, run_id,
            {"name": "register_authority", "arguments": authority(),
             "call_id": "call-3"},
        )
        self.assertEqual("A1", entry["registered"])
        offered = {t["name"] for t in tools.definitions(root, run_id)}
        self.assertIn("register_authority", offered)

    def test_gate_requires_registry_for_legal_rules(self) -> None:
        data = {
            "task_type": "research",
            "checklist": {"limit": {}},
            "facts": [
                {"id": "F1", "kind": "legal_rule", "document_id": "UA-LAW"}
            ],
            "sources": {"UA-LAW": {"repo": "ca-leginfo"}},
            "authorities": {},
            "foundational": [
                dict(answer(c)) for c in reasoning.REQUIRED_CLASSES["research"]
            ],
            "adversarial": [review()],
        }
        memo = {
            "conclusion": "Three years.",
            "findings": [
                {"item": "limit", "status": "asserted", "summary": "Three years."}
            ],
            "gaps": "None.",
        }
        result = reasoning.run_gate(data, memo)
        self.assertTrue(
            any("no registered authority" in p for p in result["problems"])
        )
        data["authorities"] = {
            "A1": dict(
                authority(document_id="UA-LAW"),
                id="A1", level="persuasive", verification="provisional",
                treatment_checked=False,
            )
        }
        result = reasoning.run_gate(data, memo)
        self.assertFalse(
            [p for p in result["problems"] if "registered authority" in p]
        )
        self.assertTrue(
            any("no binding verified authority" in a for a in result["advisory"])
        )

    def test_gate_resolves_authority_link(self) -> None:
        # Closeout §7/§11: the registry join is an explicit link.
        # A legal-rule fact names its A-id; the gate resolves it
        # instead of guessing from document membership (and instead
        # of text equality, which refuses sound filings whose claims
        # legitimately summarize rather than restate the rule).
        data = {
            "task_type": "research",
            "checklist": {"limit": {}},
            "facts": [
                {"id": "F1", "kind": "legal_rule",
                 "document_id": "UA-LAW",
                 "claim": "excerpt states the breach elements",
                 "authority": "A1"}
            ],
            "sources": {"UA-LAW": {"repo": "ca-leginfo"}},
            "authorities": {
                "A1": dict(
                    authority(document_id="UA-LAW"),
                    id="A1", level="persuasive",
                    verification="provisional",
                    treatment_checked=False,
                )
            },
            "foundational": [
                dict(answer(c)) for c in reasoning.REQUIRED_CLASSES["research"]
            ],
            "adversarial": [review()],
        }
        memo = {
            "conclusion": "Three years.",
            "findings": [
                {"item": "limit", "status": "asserted", "summary": "Three years."}
            ],
            "gaps": "None.",
        }
        result = reasoning.run_gate(data, memo)
        self.assertFalse(
            [p for p in result["problems"] if "registered authority" in p],
            result["problems"],
        )
        # A link to nothing registered fails closed.
        data["facts"][0]["authority"] = "A9"
        result = reasoning.run_gate(data, memo)
        self.assertTrue(
            any("not a registered authority" in p
                for p in result["problems"]),
            result["problems"],
        )
        # No link with a real claim fails closed too: registering P1
        # must not silently validate a P2 fact from the same doc.
        del data["facts"][0]["authority"]
        result = reasoning.run_gate(data, memo)
        self.assertTrue(
            any("names no registered authority" in p
                for p in result["problems"]),
            result["problems"],
        )


class NextActionTest(unittest.TestCase):
    """Ledger-aware next-action ranking (pure)."""

    def test_empty_run_acquires_sources_first(self) -> None:
        suggestions = reasoning.suggest_next_actions({})
        self.assertEqual("search_public_sources", suggestions[0]["tool"])

    def test_chain_gaps_rank_in_order(self) -> None:
        data = {
            "task_type": "investigation",
            "identification": identification(),
            "classification": classification(),
            "foundational": [
                dict(answer("objective"), id="FQ1"),
                dict(
                    answer("facts", status="unresolved", resolution="ask"),
                    id="FQ2", threshold=True,
                ),
            ],
        }
        tools_ranked = [
            s["tool"] for s in reasoning.suggest_next_actions(data)
        ]
        self.assertEqual("search_public_sources", tools_ranked[0])
        self.assertIn("triage_gap", tools_ranked)

    def test_stale_orchestration_comes_first(self) -> None:
        data = {
            "task_type": "investigation",
            "identification": identification(),
            "classification": classification(),
            "orchestration": {
                "questions": {
                    "Q1": {"status": "stale", "issue": "defects"},
                },
            },
        }
        suggestions = reasoning.suggest_next_actions(data)
        self.assertIn("stale", suggestions[0]["reason"])
        self.assertEqual("defects", suggestions[0]["target"])

    def test_complete_chain_suggests_filing(self) -> None:
        gate = IdentifiedGateTest()
        data = gate.chain()
        data["facts"] = [{"id": "F1", "document_id": "D1", "quote": "Source text"}]
        data["checklist"]["defects"]["status"] = "answered"
        data["adversarial"][-1]["at"] = "2026-01-02"
        suggestions = reasoning.suggest_next_actions(data)
        self.assertEqual("submit_memo", suggestions[-1]["tool"])

    def test_tool_reports_ranking(self) -> None:
        root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        run_id = new_run(root, instructions="i", objective="o")
        fake = support.scripted(root, [])
        result = tools.run(
            fake.client, root, run_id,
            {"name": "suggest_next_action", "arguments": {},
             "call_id": "call-1"},
        )
        self.assertEqual("search_public_sources", result["suggestions"][0]["tool"])


class OrchestrationSeamTest(unittest.TestCase):
    """Filing respects control-plane invalidation; ledger seeds the plane."""

    def memo(self):
        return {
            "conclusion": "Count is 4.",
            "findings": [
                {"item": "defects", "status": "asserted", "summary": "Count is 4."}
            ],
            "gaps": "None.",
        }

    def data(self):
        gate = IdentifiedGateTest()
        return gate.chain()

    def test_no_orchestration_state_is_vacuous(self) -> None:
        problems, advisory = reasoning.orchestration_staleness(
            self.data(), self.memo()
        )
        self.assertEqual(([], []), (problems, advisory))

    def test_stale_and_unsettled_block_filing(self) -> None:
        data = self.data()
        data["orchestration"] = {
            "questions": {
                "Q1": {"status": "stale", "issue": "defects"},
                "Q2": {"status": "blocked", "issue": "defects"},
                "Q3": {"status": "answered", "issue": "defects"},
                "Q4": {"status": "stale", "issue": ""},
            },
            "invalidations": [
                {"premise": "P1", "reason": "premise changed", "staled": ["Q1"]}
            ],
            "disclosures": [],
            "contradictions": [],
        }
        problems, _ = reasoning.orchestration_staleness(data, self.memo())
        self.assertTrue(any("Q1" in p and "stale" in p for p in problems))
        self.assertTrue(any("Q2" in p and "blocked" in p for p in problems))
        self.assertFalse(any("Q3" in p for p in problems))
        self.assertFalse(any("Q4" in p for p in problems))
        result = reasoning.run_gate(data, self.memo())
        self.assertFalse(result["passed"])

    def test_disclosed_questions_draw_advisories(self) -> None:
        data = self.data()
        data["orchestration"] = {
            "questions": {
                "Q1": {"status": "disclosed", "issue": "defects"},
                "Q2": {"status": "blocked", "issue": "defects"},
            },
            "invalidations": [],
            "disclosures": ["Q2 disclosed (stalled): thin record"],
            "contradictions": [
                {"id": "C1", "about": "Q1", "status": "open"}
            ],
        }
        problems, advisory = reasoning.orchestration_staleness(data, self.memo())
        self.assertEqual([], problems)
        self.assertTrue(any("Q1" in a for a in advisory))
        self.assertTrue(any("C1" in a for a in advisory))

    def test_malformed_state_advises_without_failing(self) -> None:
        data = self.data()
        data["orchestration"] = {"questions": ["not", "a", "dict"]}
        problems, advisory = reasoning.orchestration_staleness(data, self.memo())
        self.assertEqual([], problems)
        self.assertTrue(advisory)

    def test_seeds_feed_the_control_plane(self) -> None:
        from agent_kit.orchestration import contracts

        data = {
            "foundational": [
                dict(answer("objective"), id="FQ1"),
                dict(answer("facts"), id="FQ2", depends_on=["FQ1"],
                     issue="defects"),
                dict(answer("adverse"), id="FQ3"),
            ]
        }
        seeds = reasoning.orchestration_seeds(data)
        self.assertEqual(
            ["analysis", "evidence", "review"],
            [s["class"] for s in seeds],
        )
        self.assertEqual([], seeds[0]["deps"])
        self.assertEqual(["Q1"], seeds[1]["deps"])
        self.assertEqual("defects", seeds[1]["issue"])
        state = contracts.new_state()
        for seed in seeds:
            contracts.add_question(
                state, seed["text"], seed["class"], deps=seed["deps"],
                premises=seed["premises"], issue=seed["issue"],
            )
        self.assertEqual({"Q1", "Q2", "Q3"}, set(state["questions"]))


class AuthorizationTest(unittest.TestCase):
    """Approval ledger lifecycle and tool classification policy."""

    def test_every_tool_is_classified(self) -> None:
        from agent_kit.tools import MODULES

        registered = set(MODULES)
        classified = set(reasoning.TOOL_ACTION_CLASSES)
        self.assertEqual(set(), registered - classified)
        self.assertEqual(set(), classified - registered)
        for tool, klass in reasoning.TOOL_ACTION_CLASSES.items():
            with self.subTest(tool=tool):
                self.assertIn(klass, reasoning.ACTION_CLASSES)

    def test_no_execute_tools_exist_yet(self) -> None:
        from agent_kit.tools import MODULES

        self.assertEqual((), reasoning.EXECUTE_TOOLS)
        for tool in MODULES:
            self.assertFalse(reasoning.requires_approval(tool))

    def test_approval_lifecycle(self) -> None:
        root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        run_id = new_run(root, instructions="i", objective="o")
        case = CaseRecord(root, run_id)
        entry = case.request_approval(
            action="file the notice", scope="one filing", reason="deadline"
        )
        self.assertEqual("APR1", entry["id"])
        self.assertEqual(
            "pending", reasoning.approval_status(case.data, "file the notice")
        )
        self.assertEqual("none", reasoning.approval_status(case.data, "other"))
        decided = case.decide_approval(
            approval_id="APR1", decision="grant",
            decided_by="attorney", note="ok",
        )
        self.assertEqual("grant", decided["decision"])
        self.assertEqual(
            "granted", reasoning.approval_status(case.data, "file the notice")
        )
        with self.assertRaises(CaseError):
            case.decide_approval(
                approval_id="APR1", decision="deny", decided_by="attorney"
            )
        with self.assertRaises(CaseError):
            case.decide_approval(
                approval_id="APR9", decision="deny", decided_by="attorney"
            )

    def test_approval_tools_offered(self) -> None:
        root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        run_id = new_run(root, instructions="i", objective="o")
        fake = support.scripted(root, [])

        def call(name, args, call_id):
            return tools.run(
                fake.client, root, run_id,
                {"name": name, "arguments": args, "call_id": call_id},
            )

        # request_approval is model-offered; decide_approval is
        # operator-only (closeout §6): offered to no model path.
        offered = {t["name"] for t in tools.definitions(root, run_id)}
        self.assertIn("request_approval", offered)
        self.assertNotIn("decide_approval", offered)
        requested = call(
            "request_approval",
            {"action": "a", "scope": "s", "reason": "r"}, "call-1",
        )
        self.assertEqual("APR1", requested["requested"])
        refused = call(
            "decide_approval",
            {"approval_id": "APR1", "decision": "deny",
             "decided_by": "attorney"},
            "call-2",
        )
        self.assertIn("error", refused)
        self.assertEqual(
            "", CaseRecord(root, run_id).data["approvals"][0]["decision"]
        )

    def test_operator_path_decides_with_server_actor(self) -> None:
        from agent_kit.tools import decide_approval

        root, tmp = support.make_root()
        self.addCleanup(tmp.cleanup)
        run_id = new_run(root, instructions="i", objective="o")
        fake = support.scripted(root, [])
        case = CaseRecord(root, run_id)
        case.set_checklist(support.CHECKLIST_ITEMS)
        case.request_approval(action="a", scope="s", reason="r")

        def op_ctx(actor):
            return tools.Context(
                fake.client, root, run_id, case, True, actor=actor
            )

        # Model actor refused even on the direct module path.
        refused = decide_approval.run(
            op_ctx("model"),
            {"approval_id": "APR1", "decision": "grant",
             "decided_by": "reviewing-attorney"},
        )
        self.assertIn("error", refused)
        # Confused deputy refused: claimed name != actor.
        spoofed = decide_approval.run(
            op_ctx("operator"),
            {"approval_id": "APR1", "decision": "grant",
             "decided_by": "someone-else"},
        )
        self.assertIn("confused-deputy", spoofed["error"])
        # Matching operator actor records under the server identity.
        decided = decide_approval.run(
            op_ctx("attorney"),
            {"approval_id": "APR1", "decision": "deny",
             "decided_by": "attorney"},
        )
        self.assertEqual("deny", decided["decision"])
        entry = CaseRecord(root, run_id).data["approvals"][0]
        self.assertEqual("deny", entry["decision"])
        self.assertEqual("attorney", entry["decided_by"])

    def test_pending_approvals_surface_as_advisory(self) -> None:
        gate = IdentifiedGateTest()
        data = gate.chain()
        data["approvals"] = [
            {"id": "APR1", "action": "file the notice", "decision": ""}
        ]
        result = reasoning.run_gate(data, gate.memo())
        self.assertTrue(result["passed"])
        self.assertTrue(any("APR1" in a for a in result["advisory"]))


class ToolSchemaTest(unittest.TestCase):
    """Every tool DEFINITION honors the structural contract.

    Adapted from the Block agent-skills/ai-rules idea (canonical,
    validated instruction surfaces) as a suite lint instead of a
    new dependency: names match registration, parameters are
    closed objects, required names exist, and enums are
    non-empty. Per-parameter descriptions stay a review
    convention, not a gate: most tools predate it, and gating
    would churn twenty-plus tools across owners for no behavior
    change. New and touched tools carry descriptions anyway.
    """

    def test_definitions_honor_contract(self) -> None:
        from agent_kit.tools import MODULES

        self.assertGreaterEqual(len(MODULES), 31)
        for name, module in sorted(MODULES.items()):
            with self.subTest(tool=name):
                definition = module.DEFINITION
                self.assertEqual("function", definition.get("type"))
                self.assertEqual(name, definition.get("name"))
                self.assertTrue(
                    str(definition.get("description") or "").strip()
                )
                params = definition.get("parameters") or {}
                self.assertEqual("object", params.get("type"))
                self.assertIs(False, params.get("additionalProperties"))
                properties = params.get("properties") or {}
                self.assertIsInstance(properties, dict)
                required = params.get("required") or []
                for field in required:
                    self.assertIn(field, properties)
                for field, spec in properties.items():
                    self.assertIsInstance(spec, dict, field)
                    if "enum" in spec:
                        self.assertTrue(list(spec["enum"]), field)


class RegressionGateTest(unittest.TestCase):
    """The system-level regression gate passes on this tree."""

    def test_regression_passes(self) -> None:
        from eval import regression

        report = regression.run_all()
        self.assertEqual("PASS", report["verdict"], report)


if __name__ == "__main__":
    unittest.main()
