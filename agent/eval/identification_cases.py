"""Task-identification benchmark: synthetic matters with planted truth.

Each fixture exercises the identification chain: task identification,
multi-label classification with dependencies, threshold-first
question decomposition, clarification triage, workflow selection,
and work-product declaration. Sound fixtures file in identified
mode; defective fixtures are refused there while the baseline
(reasoning mode without identification) files them. Truth is
planted in the fixture, never derived from system output. The
grader (`eval/identification_run.py`) runs both modes on every
fixture and compares them.

Triage and dependency references use positional foundational ids
(FQ1, FQ2, ... in fixture order) and matter:field refs; the
materializer records answers in order so the ids are deterministic.
The cyclic fixture injects a back-edge after recording (labeled),
since record-time validation otherwise makes cycles unrecordable —
the gate must still catch a corrupted record.

Fixture data stays synthetic and self-contained, as in
`eval/reasoning_cases.py`.
"""

from __future__ import annotations

from pathlib import Path

from eval.reasoning_cases import A, AR, C, E, F, Q, write_document


def ID(categories, primary, gate_task_type, *, dependencies=None, **kw):
    """One identification block with compact defaults (overrides win)."""
    block = {
        "requested": kw.pop("requested", "Fixture request."),
        "objective": kw.pop("objective", "Fixture objective."),
        "deliverable": kw.pop("deliverable", "Fixture deliverable."),
        "underlying_problem": kw.pop("underlying_problem", "Fixture legal problem."),
        "context_available": kw.pop("context_available", "Fixture context."),
        "missing_or_ambiguous": kw.pop("missing_or_ambiguous", "Fixture gaps."),
        "constraints": kw.pop("constraints", "Fixture constraints: none material."),
        "categories": categories,
        "dependencies": dependencies or [],
        "primary": primary,
        "gate_task_type": gate_task_type,
    }
    block.update(kw)
    return block


def CAT(category, rationale, subcategory=""):
    return {"category": category, "subcategory": subcategory, "rationale": rationale}


def WF(workflow, purpose, after=None):
    return {"workflow": workflow, "purpose": purpose, "after": after or []}


def TG(ref, gap, needed_for, sources_checked, decision, detail):
    return {
        "ref": ref, "gap": gap, "needed_for": needed_for,
        "sources_checked": sources_checked, "decision": decision,
        "detail": detail,
    }


def WP(product, sections, traceability):
    return {"product": product, "sections": sections, "traceability": traceability}


def P(qid, qclass, question, criterion="Evidence and reasoning recorded.",
      issue="", depends_on=None, threshold=False):
    """One planned question (mirrors recorded answers by class+issue)."""
    return {
        "id": qid, "class": qclass, "question": question,
        "criterion": criterion, "issue": issue,
        "depends_on": list(depends_on or []), "threshold": bool(threshold),
    }


CASES = {
    # ---- sound: mixed contract + litigation work --------------------------
    "multi-category-matter": {
        "title": "Demand letter over a delivery dispute",
        "sound": True,
        "objective": "Draft a demand letter for the late delivery.",
        "documents": {
            "MC-AGREE": [
                "Services agreement. Vendor shall deliver the module by March 1. "
                "Delivery occurred on March 20."
            ],
        },
        "checklist": [
            {"id": "delay", "question": "Was delivery late?",
             "evidence_needed": "agreement and delivery records"},
        ],
        "facts": [
            F("delay", "MC-AGREE", 1,
              "Vendor shall deliver the module by March 1.",
              "March 1 duty stated"),
            F("delay", "MC-AGREE", 1, "Delivery occurred on March 20.",
              "delivery March 20"),
        ],
        "identification": ID(
            [CAT("contract", "The dispute turns on delivery duties.",
                 "interpretation"),
             CAT("litigation", "A demand letter threatens proceedings.",
                 "claim analysis")],
            "litigation", "litigation",
            dependencies=[{"from": "litigation", "to": "contract"}],
            requested="Draft a demand letter for the late delivery.",
            objective="Secure compensation or prompt cure.",
            deliverable="A demand letter stating rights and a deadline.",
            underlying_problem="Whether delay breached the agreement and what follows.",
            context_available="Agreement and delivery records in the inbox.",
            missing_or_ambiguous="Damages amount; counterparty position.",
            constraints="No filing yet; letter only.",
        ),
        "workflow": {
            "components": [
                WF("fact_evidence", "Establish delay from the records."),
                WF("contract_interpretation", "Construe the delivery duty.",
                   after=["fact_evidence"]),
                WF("element_analysis", "Analyze breach elements.",
                   after=["contract_interpretation"]),
                WF("drafting_verification", "Draft and verify the letter.",
                   after=["element_analysis"]),
                WF("quality_control", "Final gate.", after=["drafting_verification"]),
            ],
            "rationale": "Facts, then meaning, then breach, then the letter.",
        },
        "decomposition": {
            "questions": [
                P("p1", "objective", "What must the letter achieve?"),
                P("p2", "jurisdiction", "Which forum looms?"),
                P("p3", "governing_law", "Which law governs the duty?",
                  issue="delay", threshold=True),
                P("p4", "claim", "What claim does delay state?"),
                P("p5", "elements", "What must breach show?", issue="delay",
                  depends_on=["p3"]),
                P("p6", "facts", "Was delivery late?", issue="delay"),
                P("p7", "evidence", "What proves the dates?", issue="delay"),
                P("p8", "deadlines", "Any deadlines?"),
                P("p9", "exceptions", "Any excuses?"),
                P("p10", "adverse", "Any adverse material?"),
                P("p11", "unknowns", "What is unknown?"),
                P("p12", "action", "What action?", depends_on=["p5"]),
            ],
            "waived": [],
        },
        "foundational": [
            A("objective", "What must the letter achieve?"),
            A("jurisdiction", "Which forum looms?",
              answer="Fixture assumes one forum."),
            A("governing_law", "Which law governs?", issue="delay",
              answer="Fixture contract law.", threshold=True),
            A("claim", "What claim?", answer="Breach by late delivery."),
            A("elements", "What elements?", issue="delay",
              answer="Duty, breach, causation, damages."),
            A("facts", "What happened?", issue="delay",
              answer="Duty March 1; delivery March 20."),
            A("evidence", "What supports it?", issue="delay",
              answer="Agreement passages recorded verbatim."),
            A("deadlines", "Deadlines?",
              answer="None in the fixture record."),
            A("exceptions", "Exceptions?",
              answer="None in the fixture record."),
            A("adverse", "Adverse material?",
              answer="None in the fixture record."),
            A("unknowns", "Unknowns?",
              status="unresolved", resolution="Ask the client for the loss figure.",
              answer="", basis="Damages unquantified.",
              effect="The letter states liability, not quantum."),
            A("action", "Action?",
              answer="Send the letter; quantify damages next."),
        ],
        "triages": [
            TG("FQ11", "Damages amount.", "Quantum in the letter.",
               "Inbox has no loss records.", "scenarios",
               "Analyze nominal and documented-loss scenarios; letter omits quantum."),
        ],
        "work_product": WP(
            "litigation_analysis",
            ["claims", "elements", "evidence", "options"],
            "Breach conclusion traces to the agreement passages F1-F2; "
            "quantum stays provisional on client figures.",
        ),
        "adversarial": [AR(
            strongest_against="Delay may be excused on unknown facts.",
            revision_needed=True,
            revision="Added an excuse caveat to the letter draft.",
        )],
        "memo": {
            "conclusion": "Delivery was 19 days late against a March 1 duty; "
                         "a demand letter stating breach with an excuse caveat is next.",
            "findings": [
                C("delay", "asserted",
                  "The March 1 duty was delivered March 20.",
                  [Q("MC-AGREE", 1, "Vendor shall deliver the module by March 1."),
                   Q("MC-AGREE", 1, "Delivery occurred on March 20.")]),
            ],
            "gaps": "Damages unquantified; counterparty position unknown.",
        },
        "unsupported": [],
        "truth": {"identified_gate": True, "baseline_gate": True,
                  "rules": True, "claims": "passed"},
    },
    # ---- sound: ambiguous request resolved by asking -----------------------
    "ambiguous-request-ask": {
        "title": "Ambiguous filing question resolved by asking",
        "sound": True,
        "objective": "Can we file?",
        "documents": {
            "AQ-NOTE": ["Intake note. The client asks whether to file."],
        },
        "checklist": [
            {"id": "filing", "question": "Should the client file?",
             "evidence_needed": "forum, claims, and deadlines"},
        ],
        "matter": [
            {"field": "forum", "value": "", "status": "unknown",
             "provenance": "intake note names no forum"},
        ],
        "facts": [],
        "identification": ID(
            [CAT("advice", "The client seeks a filing decision.", "options analysis")],
            "advice", "investigation",
            requested="Can we file?",
            objective="Decide whether filing serves the client.",
            deliverable="A yes/no with reasons, or a needs list.",
            underlying_problem="Forum, claim, and deadline unknowns block any answer.",
            context_available="One intake line only.",
            missing_or_ambiguous="Forum, claim, deadlines, objectives.",
            constraints="Do not advise filing blind.",
        ),
        "workflow": {
            "components": [
                WF("fact_evidence", "Gather what the record holds."),
                WF("element_analysis", "Test what can be analyzed.",
                   after=["fact_evidence"]),
                WF("quality_control", "Final gate.", after=["element_analysis"]),
            ],
            "rationale": "Thin record: establish limits, then stop.",
        },
        "decomposition": {
            "questions": [
                P("p1", "objective", "Should the client file?"),
                P("p2", "facts", "What is known?", issue="filing"),
                P("p3", "evidence", "What evidence exists?", issue="filing"),
                P("p4", "adverse", "Adverse considerations?"),
                P("p5", "unknowns", "What is unknown?"),
                P("p6", "action", "What action first?", depends_on=["p5"]),
            ],
            "waived": [
                {"area": "governing law",
                 "reason": "Forum unknown; governing law unknowable until triaged."},
            ],
        },
        "foundational": [
            A("objective", "Should the client file?"),
            A("facts", "What is known?", issue="filing",
              status="unresolved", resolution="Ask the client for forum and claim.",
              answer="", basis="Only an intake line exists.",
              effect="No filing analysis is supportable."),
            A("evidence", "What evidence?", issue="filing",
              status="unresolved", resolution="Ask the client for documents.",
              answer="", basis="No documents beyond the note.",
              effect="Nothing can be cited."),
            A("adverse", "Adverse considerations?",
              answer="Filing blind risks forum, deadline, and cost errors."),
            A("unknowns", "Unknowns?",
              answer="Forum, claim, deadlines, objectives."),
            A("action", "Action?",
              answer="Ask for forum, claim, deadlines, documents first."),
        ],
        "triages": [
            TG("FQ2", "Forum and claim.", "Any filing analysis.",
               "Intake note only.", "ask_user",
               "Where would you file, and what claim?"),
            TG("FQ3", "Supporting documents.", "Evidence for any claim.",
               "Inbox holds only the note.", "ask_user",
               "Share the key documents and dates."),
            TG("matter:forum", "Forum unknown.", "Jurisdiction analysis.",
               "Intake note only.", "ask_user", "Where would you file?"),
        ],
        "work_product": WP(
            "direct_answer",
            ["answer", "needs"],
            "The needs list traces to the recorded unknowns FQ2-FQ3 and forum.",
        ),
        "adversarial": [AR(
            strongest_against="Delay in asking could itself cost time.",
        )],
        "memo": {
            "conclusion": "No filing answer is supportable; forum, claim, "
                         "deadlines, and documents are needed first.",
            "findings": [
                C("filing", "unresolved",
                  "Filing waits for forum, claim, and documents.", []),
            ],
            "gaps": "Forum, claim, deadlines, documents outstanding.",
        },
        "unsupported": [],
        "truth": {"identified_gate": True, "baseline_gate": True,
                  "rules": True, "claims": "passed"},
    },
    # ---- sound: emerging work under other ----------------------------------
    "emerging-task-other": {
        "title": "Model audit as emerging work",
        "sound": True,
        "objective": "Is the model audit complete?",
        "documents": {
            "EO-REPORT": ["Audit memo. The benchmark suite passed on April 4."],
        },
        "checklist": [
            {"id": "audit", "question": "Is the model audit complete?",
             "evidence_needed": "audit records"},
        ],
        "facts": [
            F("audit", "EO-REPORT", 1,
              "The benchmark suite passed on April 4.",
              "benchmarks passed April 4"),
        ],
        "identification": ID(
            [CAT("other", "No listed category covers model audits.",
                 "AI model audit")],
            "other", "investigation",
            requested="Is the model audit complete?",
            objective="Confirm audit completion.",
            deliverable="A yes/no with the audit basis.",
            underlying_problem="Whether the recorded audit suffices.",
            context_available="One audit memo.",
            missing_or_ambiguous="Whether benchmarks equal completion.",
            constraints="Fixture scope only.",
        ),
        "workflow": {
            "components": [
                WF("fact_evidence", "Establish the audit record."),
                WF("quality_control", "Final gate.", after=["fact_evidence"]),
            ],
            "rationale": "Single-question factual check.",
        },
        "decomposition": {
            "questions": [
                P("p1", "objective", "Is the model audit complete?"),
                P("p2", "facts", "What does the memo show?", issue="audit"),
                P("p3", "evidence", "What supports it?", issue="audit"),
                P("p4", "adverse", "Adverse material?"),
                P("p5", "unknowns", "What is unknown?"),
                P("p6", "action", "What action?"),
            ],
            "waived": [],
        },
        "foundational": [
            A("objective", "Is the model audit complete?"),
            A("facts", "What does the memo show?", issue="audit",
              answer="Benchmarks passed April 4."),
            A("evidence", "What supports it?", issue="audit",
              answer="The audit passage recorded verbatim."),
            A("adverse", "Adverse material?",
              answer="None in the fixture record."),
            A("unknowns", "Unknowns?",
              answer="Whether benchmarks alone complete an audit.",
              effect="The conclusion stays provisional on scope."),
            A("action", "Action?",
              answer="Report provisional completion with the scope caveat."),
        ],
        "work_product": WP(
            "direct_answer",
            ["answer", "caveat"],
            "Completion traces to the audit passage F1; scope stays provisional.",
        ),
        "adversarial": [AR()],
        "memo": {
            "conclusion": "The benchmarks passed April 4; completion holds "
                         "provisionally on audit scope.",
            "findings": [
                C("audit", "asserted", "Benchmarks passed April 4.",
                  [Q("EO-REPORT", 1, "The benchmark suite passed on April 4.")]),
            ],
            "gaps": "Audit scope beyond benchmarks unresolved.",
        },
        "unsupported": [],
        "truth": {"identified_gate": True, "baseline_gate": True,
                  "rules": True, "claims": "passed"},
    },
    # ---- sound: threshold-first dependency order ----------------------------
    "threshold-first-ordering": {
        "title": "Appeal with jurisdiction decided first",
        "sound": True,
        "objective": "Can the judgment be appealed?",
        "documents": {
            "TO-ORDER": [
                "Court order. Judgment entered May 2. Appeal lies within 30 days."
            ],
        },
        "checklist": [
            {"id": "appeal", "question": "Can the judgment be appealed?",
             "evidence_needed": "order and appeal rules"},
        ],
        "facts": [
            F("appeal", "TO-ORDER", 1, "Judgment entered May 2.",
              "judgment May 2"),
            F("appeal", "TO-ORDER", 1, "Appeal lies within 30 days.",
              "30-day appeal window"),
        ],
        "identification": ID(
            [CAT("enforcement", "An appealability question.", "appeals")],
            "enforcement", "litigation",
            requested="Can the judgment be appealed?",
            objective="Decide whether to appeal.",
            deliverable="Appealability with the deadline.",
            underlying_problem="Forum, standing, and the appeal window.",
            context_available="The court order.",
            missing_or_ambiguous="Standing facts.",
            constraints="30-day window runs from May 2.",
        ),
        "workflow": {
            "components": [
                WF("procedure_deadlines", "Settle forum and window first."),
                WF("fact_evidence", "Establish order facts.",
                   after=["procedure_deadlines"]),
                WF("element_analysis", "Analyze appeal elements.",
                   after=["fact_evidence"]),
                WF("quality_control", "Final gate.", after=["element_analysis"]),
            ],
            "rationale": "Threshold procedure first, then merits.",
        },
        "decomposition": {
            "questions": [
                P("p1", "objective", "Can the judgment be appealed?"),
                P("p2", "jurisdiction", "Which forum?", threshold=True,
                  depends_on=["p1"]),
                P("p3", "governing_law", "Which appeal law?",
                  depends_on=["p2"]),
                P("p4", "claim", "Standing?", issue="appeal",
                  depends_on=["p2"]),
                P("p5", "elements", "Appeal elements?", issue="appeal",
                  depends_on=["p4"]),
                P("p6", "facts", "What happened?", issue="appeal"),
                P("p7", "evidence", "What supports it?", issue="appeal"),
                P("p8", "deadlines", "The window?", threshold=True,
                  depends_on=["p2"]),
                P("p9", "exceptions", "Exceptions?"),
                P("p10", "adverse", "Adverse material?"),
                P("p11", "unknowns", "Unknowns?"),
                P("p12", "action", "Action?", depends_on=["p4", "p8"]),
            ],
            "waived": [],
        },
        "foundational": [
            A("objective", "Can the judgment be appealed?"),
            A("jurisdiction", "Which forum?", threshold=True,
              answer="Fixture assumes the ordering court.", depends_on=["FQ1"]),
            A("governing_law", "Which appeal law?", depends_on=["FQ2"],
              answer="Fixture appeal rules."),
            A("claim", "Standing?", issue="appeal", depends_on=["FQ2"],
              status="unresolved", resolution="Establish appellant standing.",
              answer="", basis="No standing facts in the order.",
              effect="Appealability stays conditional."),
            A("elements", "Appeal elements?", issue="appeal", depends_on=["FQ4"],
              answer="Order, standing, timeliness."),
            A("facts", "What happened?", issue="appeal",
              answer="Judgment May 2; 30-day window."),
            A("evidence", "What supports it?", issue="appeal",
              answer="Order passages recorded verbatim."),
            A("deadlines", "The window?", threshold=True,
              answer="30 days from May 2.", depends_on=["FQ2"]),
            A("exceptions", "Exceptions?",
              answer="None in the fixture record."),
            A("adverse", "Adverse material?",
              answer="None in the fixture record."),
            A("unknowns", "Unknowns?",
              answer="Standing facts.",
              effect="Conditional conclusion."),
            A("action", "Action?", depends_on=["FQ4", "FQ8"],
              answer="Appeal by June 1 if standing holds."),
        ],
        "triages": [
            TG("FQ4", "Standing facts.", "Appealability.",
               "Order holds none.", "accept",
               "Proceed conditionally; the memo states the condition."),
        ],
        "work_product": WP(
            "action_plan",
            ["answer", "deadline", "condition"],
            "Window traces to the order passages; standing stays conditional.",
        ),
        "adversarial": [AR(
            strongest_against="Standing may fail, mooting the window.",
        )],
        "memo": {
            "conclusion": "Appeal lies within 30 days of May 2, conditional "
                         "on standing.",
            "findings": [
                C("appeal", "asserted",
                  "Judgment May 2 with a 30-day appeal window.",
                  [Q("TO-ORDER", 1, "Judgment entered May 2."),
                   Q("TO-ORDER", 1, "Appeal lies within 30 days.")]),
            ],
            "gaps": "Standing facts outstanding; conclusion conditional.",
        },
        "unsupported": [],
        "truth": {"identified_gate": True, "baseline_gate": True,
                  "rules": True, "claims": "passed"},
    },
    # ---- defective: cyclic question dependencies ----------------------------
    "cyclic-dependencies": {
        "title": "Cyclic question dependencies (injected)",
        "sound": False,
        "objective": "What happened and what supports it?",
        "documents": {
            "CD-REC": ["Status report. The module failed acceptance testing."],
        },
        "checklist": [
            {"id": "failure", "question": "Did the module fail testing?",
             "evidence_needed": "test records"},
        ],
        "facts": [
            F("failure", "CD-REC", 1,
              "The module failed acceptance testing.", "testing failed"),
        ],
        "identification": ID(
            [CAT("evidence", "A test-outcome question.", "document review")],
            "evidence", "investigation",
            requested="What happened and what supports it?",
            objective="Establish the test outcome.",
            deliverable="Finding with support.",
            underlying_problem="Test outcome and its basis.",
            context_available="One status report.",
            missing_or_ambiguous="Nothing material.",
            constraints="Fixture scope only.",
        ),
        "workflow": {
            "components": [
                WF("fact_evidence", "Establish the outcome."),
                WF("quality_control", "Final gate.", after=["fact_evidence"]),
            ],
            "rationale": "Single-question factual check.",
        },
        "decomposition": {
            "questions": [
                P("p1", "objective", "Did the module fail testing?"),
                P("p2", "facts", "What happened?", issue="failure"),
                P("p3", "evidence", "What supports it?", issue="failure",
                  depends_on=["p2"]),
                P("p4", "adverse", "Adverse material?"),
                P("p5", "unknowns", "Unknowns?"),
                P("p6", "action", "Action?"),
            ],
            "waived": [],
        },
        "foundational": [
            A("objective", "Did the module fail testing?"),
            A("facts", "What happened?", issue="failure",
              answer="Testing failed."),
            A("evidence", "What supports it?", issue="failure",
              answer="The status passage.", depends_on=["FQ2"]),
            A("adverse", "Adverse material?",
              answer="None in the fixture record."),
            A("unknowns", "Unknowns?",
              answer="None material."),
            A("action", "Action?",
              answer="Report the failure."),
        ],
        # Injected after recording: FQ2 depends back on FQ3, closing a
        # cycle record-time validation would otherwise refuse to create.
        "inject_cycle": ["FQ2", "FQ3"],
        "work_product": WP(
            "direct_answer",
            ["answer"],
            "Outcome traces to the status passage F1.",
        ),
        "adversarial": [AR()],
        "memo": {
            "conclusion": "The module failed acceptance testing.",
            "findings": [
                C("failure", "asserted", "Testing failed.",
                  [Q("CD-REC", 1, "The module failed acceptance testing.")]),
            ],
            "gaps": "None.",
        },
        "unsupported": [],
        "truth": {"identified_gate": False,
                  "identified_problems": ["cyclic question dependencies"],
                  "baseline_gate": True, "rules": True, "claims": "passed"},
    },
    # ---- defective: unresolved question never triaged -----------------------
    "untriaged-unknown": {
        "title": "Unresolved deadline question never triaged",
        "sound": False,
        "objective": "Is the claim timely?",
        "documents": {
            "UU-REC": ["Filing memo. The claim was filed on June 1."],
        },
        "checklist": [
            {"id": "timely", "question": "Is the filed claim timely?",
             "evidence_needed": "filing and limitation records"},
        ],
        "facts": [
            F("timely", "UU-REC", 1, "The claim was filed on June 1.",
              "filing June 1"),
        ],
        "identification": ID(
            [CAT("litigation", "A timeliness question.", "claim analysis")],
            "litigation", "litigation",
            requested="Is the claim timely?",
            objective="Decide timeliness.",
            deliverable="Timeliness with the period.",
            underlying_problem="Filing date against the limitation period.",
            context_available="Filing memo only.",
            missing_or_ambiguous="The limitation period.",
            constraints="Fixture scope only.",
        ),
        "workflow": {
            "components": [
                WF("fact_evidence", "Establish the filing date."),
                WF("procedure_deadlines", "Examine the period.",
                   after=["fact_evidence"]),
                WF("quality_control", "Final gate.", after=["procedure_deadlines"]),
            ],
            "rationale": "Date first, then the period.",
        },
        "decomposition": {
            "questions": [
                P("p1", "objective", "Is the claim timely?"),
                P("p2", "jurisdiction", "Which forum?"),
                P("p3", "governing_law", "Which law?", issue="timely",
                  threshold=True),
                P("p4", "claim", "What claim?"),
                P("p5", "elements", "What elements?", issue="timely"),
                P("p6", "facts", "What happened?", issue="timely"),
                P("p7", "evidence", "What supports it?", issue="timely"),
                P("p8", "deadlines", "The period?", threshold=True),
                P("p9", "exceptions", "Exceptions?"),
                P("p10", "adverse", "Adverse material?"),
                P("p11", "unknowns", "Unknowns?"),
                P("p12", "action", "Action?"),
            ],
            "waived": [],
        },
        "foundational": [
            A("objective", "Is the claim timely?"),
            A("jurisdiction", "Which forum?",
              answer="Fixture assumes one forum."),
            A("governing_law", "Which law?", issue="timely",
              answer="Fixture limitation law."),
            A("claim", "What claim?", answer="The filed June 1 claim."),
            A("elements", "What elements?", issue="timely",
              answer="Timeliness only, for this fixture."),
            A("facts", "What happened?", issue="timely",
              answer="Filed June 1."),
            A("evidence", "What supports it?", issue="timely",
              answer="The filing passage."),
            A("deadlines", "The period?",
              status="unresolved", resolution="Retrieve the limitation period.",
              answer="", basis="No period in the record.",
              effect="Timeliness cannot be concluded."),
            A("exceptions", "Exceptions?", answer="None in the record."),
            A("adverse", "Adverse material?", answer="None in the record."),
            A("unknowns", "Unknowns?",
              answer="The limitation period itself.",
              effect="Timeliness cannot be concluded."),
            A("action", "Action?", answer="Retrieve the period first."),
        ],
        "triages": [],
        "work_product": WP(
            "direct_answer",
            ["answer"],
            "Filing date traces to the memo passage F1.",
        ),
        "adversarial": [AR()],
        "memo": {
            "conclusion": "The claim was filed June 1; timeliness awaits the period.",
            "findings": [
                C("timely", "asserted", "The claim was filed June 1.",
                  [Q("UU-REC", 1, "The claim was filed on June 1.")]),
            ],
            "gaps": "Limitation period outstanding.",
        },
        "unsupported": [],
        "truth": {"identified_gate": False,
                  "identified_problems": ["no triage decision"],
                  "baseline_gate": True, "rules": True, "claims": "passed"},
    },
    # ---- defective: identification without a workflow plan ------------------
    "missing-workflow": {
        "title": "Identified matter with no workflow plan",
        "sound": False,
        "objective": "Do records show open defects?",
        "documents": {
            "MW-STATUS": ["Weekly status. Open Severity 1 defects: 2."],
        },
        "checklist": [
            {"id": "defects", "question": "Do records show open defects?",
             "evidence_needed": "status report"},
        ],
        "facts": [
            F("defects", "MW-STATUS", 1, "Open Severity 1 defects: 2.",
              "2 open sev-1 defects"),
        ],
        "identification": ID(
            [CAT("evidence", "A defect-count question.", "document review")],
            "evidence", "investigation",
            requested="Do records show open defects?",
            objective="Establish the count.",
            deliverable="Count with support.",
            underlying_problem="Period-end defect count.",
            context_available="One status page.",
            missing_or_ambiguous="Nothing material.",
            constraints="Fixture scope only.",
        ),
        "workflow": None,
        "decomposition": {
            "questions": [
                P("p1", "objective", "Do records show open defects?"),
                P("p2", "facts", "What?", issue="defects"),
                P("p3", "evidence", "Support?", issue="defects"),
                P("p4", "adverse", "Adverse?"),
                P("p5", "unknowns", "Unknowns?"),
                P("p6", "action", "Action?"),
            ],
            "waived": [],
        },
        "foundational": [
            A("objective", "Do records show open defects?"),
            A("facts", "What?", issue="defects", answer="2 open sev-1 defects."),
            A("evidence", "Support?", issue="defects",
              answer="The status passage."),
            A("adverse", "Adverse?", answer="None in the record."),
            A("unknowns", "Unknowns?", answer="None material."),
            A("action", "Action?", answer="Report the count."),
        ],
        "work_product": WP(
            "direct_answer",
            ["answer"],
            "Count traces to the status passage F1.",
        ),
        "adversarial": [AR()],
        "memo": {
            "conclusion": "Records show 2 open Severity 1 defects.",
            "findings": [
                C("defects", "asserted", "2 open Sev-1 defects.",
                  [Q("MW-STATUS", 1, "Open Severity 1 defects: 2.")]),
            ],
            "gaps": "None.",
        },
        "unsupported": [],
        "truth": {"identified_gate": False,
                  "identified_problems": ["no workflow plan"],
                  "baseline_gate": True, "rules": True, "claims": "passed"},
    },
    # ---- defective: full chain except the work product ----------------------
    "missing-work-product": {
        "title": "Full chain with no declared work product",
        "sound": False,
        "objective": "What limitation period applies?",
        "documents": {
            "MP-LAW": [
                "Supplied excerpt (fixture text, not real law). "
                "The limitation period is three years."
            ],
        },
        "checklist": [
            {"id": "limit", "question": "What limitation period applies?",
             "evidence_needed": "supplied excerpt"},
        ],
        "facts": [
            F("limit", "MP-LAW", 1, "The limitation period is three years.",
              "three-year period", kind="legal_rule", authority="A1"),
        ],
        "sources": [
            {"document_id": "MP-LAW", "repo": "ca-leginfo",
             "citation": "Fixture Code (not real law)", "version": "2026-01-01"},
        ],
        "authorities": [
            {"proposition": "The limitation period is three years.",
             "document_id": "MP-LAW",
             "citation": "Fixture Code (not real law)",
             "version": "2026-01-01", "effective_date": "2026-01-01",
             "level": "binding", "verification": "verified",
             "treatment_checked": True,
             "treatment": "No contrary treatment in the fixture record."},
        ],
        "identification": ID(
            [CAT("research", "A limitation-period question.",
                 "statutory interpretation")],
            "research", "research",
            requested="What limitation period applies?",
            objective="State the period.",
            deliverable="Period with authority.",
            underlying_problem="Applicable limitation period.",
            context_available="Supplied excerpt.",
            missing_or_ambiguous="Nothing material.",
            constraints="Fixture scope only.",
        ),
        "workflow": {
            "components": [
                WF("legal_research", "Locate the period."),
                WF("quality_control", "Final gate.", after=["legal_research"]),
            ],
            "rationale": "Single-question lookup.",
        },
        "decomposition": {
            "questions": [
                P("p1", "objective", "What limitation period applies?"),
                P("p2", "jurisdiction", "Which system?"),
                P("p3", "governing_law", "Which law?", issue="limit"),
                P("p4", "law_status", "Current?"),
                P("p5", "claim", "At issue?"),
                P("p6", "adverse", "Adverse?"),
                P("p7", "unknowns", "Unknowns?"),
            ],
            "waived": [],
        },
        "foundational": [
            A("objective", "What limitation period applies?"),
            A("jurisdiction", "Which system?",
              answer="Fixture assumes one system."),
            A("governing_law", "Which law?", issue="limit",
              answer="The supplied excerpt."),
            A("law_status", "Current?",
              answer="Version recorded as 2026-01-01."),
            A("claim", "At issue?", answer="The limitation period."),
            A("adverse", "Adverse?", answer="None in the record."),
            A("unknowns", "Unknowns?", answer="None material."),
        ],
        "elements": [
            E("limit", test="limitation", element="period",
              rule="The limitation period is three years.",
              source="MP-LAW via ca-leginfo (fixture excerpt)",
              facts_evidence="Excerpt recorded verbatim.",
              application="The excerpt states three years.",
              supporting="Express period language.",
              opposing="No opposing reading.",
              finding="Three years.",
              effect="Answers the question."),
        ],
        "work_product": None,
        "adversarial": [AR()],
        "memo": {
            "conclusion": "A three-year limitation period applies.",
            "findings": [
                C("limit", "asserted", "The limitation period is three years.",
                  [Q("MP-LAW", 1, "The limitation period is three years.")],
                  kind="legal_rule"),
            ],
            "gaps": "None.",
        },
        "unsupported": [],
        "truth": {"identified_gate": False,
                  "identified_problems": ["no work product declared"],
                  "baseline_gate": True, "rules": True, "claims": "passed"},
    },
    # ---- sound: validated decomposition with a threshold extra -------------
    "decomposition-first": {
        "title": "Support term with a validated question plan",
        "sound": True,
        "objective": "How long does support run?",
        "documents": {
            "DC-AGREE": [
                "Support agreement. Vendor provides support for 12 months."
            ],
        },
        "checklist": [
            {"id": "term", "question": "How long does support run?",
             "evidence_needed": "agreement terms"},
        ],
        "facts": [
            F("term", "DC-AGREE", 1,
              "Vendor provides support for 12 months.",
              "12-month support term"),
        ],
        "identification": ID(
            [CAT("contract", "A support-term question.", "interpretation")],
            "contract", "contract",
            requested="How long does support run?",
            objective="State the support term.",
            deliverable="Term with support.",
            underlying_problem="Support duration under the agreement.",
            context_available="The agreement excerpt.",
            missing_or_ambiguous="Nothing material.",
            constraints="Fixture scope only.",
        ),
        "workflow": {
            "components": [
                WF("contract_interpretation", "Construe the term."),
                WF("quality_control", "Final gate.",
                   after=["contract_interpretation"]),
            ],
            "rationale": "Single-term interpretation.",
        },
        "decomposition": {
            "questions": [
                P("p1", "objective", "How long does support run?"),
                P("p2", "governing_law", "Which law governs?", threshold=True),
                P("p3", "claim", "What is at issue?"),
                P("p4", "elements", "What settles it?", issue="term"),
                P("p5", "facts", "What do the terms say?", issue="term"),
                P("p6", "evidence", "What supports it?", issue="term"),
                P("p7", "exceptions", "Any exceptions?"),
                P("p8", "unknowns", "Unknowns?"),
                P("p9", "action", "Action?"),
            ],
            "waived": [],
        },
        "foundational": [
            A("objective", "How long does support run?"),
            A("governing_law", "Which law governs?",
              answer="Fixture contract law.", threshold=True),
            A("claim", "What is at issue?",
              answer="The support duration."),
            A("elements", "What settles it?", issue="term",
              answer="The express 12-month term."),
            A("facts", "What do the terms say?", issue="term",
              answer="12 months of support."),
            A("evidence", "What supports it?", issue="term",
              answer="The agreement passage recorded verbatim."),
            A("exceptions", "Any exceptions?",
              answer="None in the fixture record."),
            A("unknowns", "Unknowns?", answer="None material."),
            A("action", "Action?", answer="Report the term."),
        ],
        "work_product": WP(
            "direct_answer",
            ["answer"],
            "Term traces to the agreement passage F1.",
        ),
        "adversarial": [AR()],
        "memo": {
            "conclusion": "Support runs 12 months under the agreement.",
            "findings": [
                C("term", "asserted", "Support runs 12 months.",
                  [Q("DC-AGREE", 1,
                     "Vendor provides support for 12 months.")]),
            ],
            "gaps": "None.",
        },
        "unsupported": [],
        "truth": {"identified_gate": True, "baseline_gate": True,
                  "rules": True, "claims": "passed"},
    },
    # ---- defective: full chain without any decomposition -------------------
    "unvalidated-decomposition": {
        "title": "Identified matter with no validated decomposition",
        "sound": False,
        "objective": "Was the claim filed on time?",
        "documents": {
            "DU-REC": ["Filing memo. The claim was filed on June 1."],
        },
        "checklist": [
            {"id": "filed", "question": "Was the claim filed?",
             "evidence_needed": "filing records"},
        ],
        "facts": [
            F("filed", "DU-REC", 1, "The claim was filed on June 1.",
              "filing June 1"),
        ],
        "identification": ID(
            [CAT("evidence", "A filing-date question.", "document review")],
            "evidence", "investigation",
            requested="Was the claim filed on time?",
            objective="Establish filing.",
            deliverable="Filing finding.",
            underlying_problem="Whether filing occurred.",
            context_available="Filing memo only.",
            missing_or_ambiguous="Nothing material.",
            constraints="Fixture scope only.",
        ),
        "workflow": {
            "components": [
                WF("fact_evidence", "Establish the filing date."),
                WF("quality_control", "Final gate.", after=["fact_evidence"]),
            ],
            "rationale": "Date check.",
        },
        "foundational": [
            A("objective", "Was the claim filed?"),
            A("facts", "What happened?", issue="filed",
              answer="Filed June 1."),
            A("evidence", "What supports it?", issue="filed",
              answer="The filing passage."),
            A("adverse", "Adverse?", answer="None in the record."),
            A("unknowns", "Unknowns?", answer="None material."),
            A("action", "Action?", answer="Report filing."),
        ],
        "work_product": WP(
            "direct_answer",
            ["answer"],
            "Filing traces to the memo passage F1.",
        ),
        "adversarial": [AR()],
        "memo": {
            "conclusion": "The claim was filed on June 1.",
            "findings": [
                C("filed", "asserted", "Filed June 1.",
                  [Q("DU-REC", 1, "The claim was filed on June 1.")]),
            ],
            "gaps": "None.",
        },
        "unsupported": [],
        "truth": {"identified_gate": False,
                  "identified_problems": ["no validated decomposition"],
                  "baseline_gate": True, "rules": True, "claims": "passed"},
    },
    # ---- defective: validated plan with an unanswered question --------------
    "uncovered-planned-question": {
        "title": "Validated plan with an unanswered deadlines question",
        "sound": False,
        "objective": "Do records show open defects?",
        "documents": {
            "DB-REC": ["Weekly status. Open Severity 1 defects: 2."],
        },
        "checklist": [
            {"id": "defects", "question": "Do records show open defects?",
             "evidence_needed": "status report"},
        ],
        "facts": [
            F("defects", "DB-REC", 1, "Open Severity 1 defects: 2.",
              "2 open sev-1 defects"),
        ],
        "identification": ID(
            [CAT("evidence", "A defect-count question.", "document review")],
            "evidence", "investigation",
            requested="Do records show open defects?",
            objective="Establish the count.",
            deliverable="Count with support.",
            underlying_problem="Period-end defect count.",
            context_available="One status page.",
            missing_or_ambiguous="Nothing material.",
            constraints="Fixture scope only.",
        ),
        "workflow": {
            "components": [
                WF("fact_evidence", "Establish the count."),
                WF("quality_control", "Final gate.", after=["fact_evidence"]),
            ],
            "rationale": "Count check.",
        },
        "decomposition": {
            "questions": [
                P("p1", "objective", "Do records show open defects?"),
                P("p2", "facts", "What?", issue="defects"),
                P("p3", "evidence", "Support?", issue="defects"),
                P("p4", "adverse", "Adverse?"),
                P("p5", "unknowns", "Unknowns?"),
                P("p6", "action", "Action?"),
                P("p7", "deadlines", "Any reporting deadline?"),
            ],
            "waived": [],
        },
        "foundational": [
            A("objective", "Do records show open defects?"),
            A("facts", "What?", issue="defects", answer="2 open sev-1 defects."),
            A("evidence", "Support?", issue="defects",
              answer="The status passage."),
            A("adverse", "Adverse?", answer="None in the record."),
            A("unknowns", "Unknowns?", answer="None material."),
            A("action", "Action?", answer="Report the count."),
        ],
        "work_product": WP(
            "direct_answer",
            ["answer"],
            "Count traces to the status passage F1.",
        ),
        "adversarial": [AR()],
        "memo": {
            "conclusion": "Records show 2 open Severity 1 defects.",
            "findings": [
                C("defects", "asserted", "2 open Sev-1 defects.",
                  [Q("DB-REC", 1, "Open Severity 1 defects: 2.")]),
            ],
            "gaps": "None.",
        },
        "unsupported": [],
        "truth": {"identified_gate": False,
                  "identified_problems": ["has no recorded answer"],
                  "baseline_gate": True, "rules": True, "claims": "passed"},
    },
}


def materialize(root: Path, name: str, *, with_identification: bool):
    """Build the fixture matter; return (run_id, record).

    with_identification=True records the identification chain
    (identification, classification, workflow, triages, work
    product); False records only the reasoning artifacts (the
    baseline path). Documents, checklist, facts, and memo are
    identical either way.
    """
    from agent_kit.case_record import CaseRecord

    case = CASES[name]
    run_id = f"identification-{name}"
    for doc_id, pages in case.get("documents", {}).items():
        write_document(root, doc_id, pages)
    record = CaseRecord(root, run_id)
    record.set_checklist(case["checklist"])
    if case.get("documents"):
        record.mark_read(sorted(case["documents"]))
    for field in case.get("matter", []):
        record.note_context(
            field["field"], field.get("value", ""),
            field.get("status", "known"), field.get("provenance", ""),
        )
    for fact in case.get("facts", []):
        record.record_fact(
            fact["item"],
            fact["document_id"],
            fact["page"],
            fact["quote"],
            fact["claim"],
            stance=fact.get("stance", "supports"),
            kind=fact.get("kind", "fact"),
            basis=fact.get("basis", ""),
            authority=fact.get("authority", ""),
        )
    for source in case.get("sources", []):
        record.note_source(
            source["document_id"], source["repo"],
            source["citation"], source["version"],
            source.get("language", ""),
        )
    for authority in case.get("authorities", []):
        record.register_authority(
            proposition=authority["proposition"],
            document_id=authority["document_id"],
            citation=authority["citation"], version=authority["version"],
            effective_date=authority["effective_date"],
            level=authority["level"],
            verification=authority["verification"],
            treatment_checked=authority["treatment_checked"],
            treatment=authority["treatment"],
        )
    for branch in case.get("branches", []):
        record.open_branch(
            branch["issue"], branch["regime"],
            status=branch.get("status", "unknown"),
            basis=branch.get("basis", ""),
        )
    if with_identification:
        ident = case["identification"]
        record.identify_task(
            requested=ident["requested"], objective=ident["objective"],
            deliverable=ident["deliverable"],
            underlying_problem=ident["underlying_problem"],
            context_available=ident["context_available"],
            missing_or_ambiguous=ident["missing_or_ambiguous"],
            constraints=ident["constraints"],
            categories=ident["categories"],
            dependencies=ident.get("dependencies", []),
            primary=ident["primary"],
            gate_task_type=ident["gate_task_type"],
        )
    else:
        record.set_task_type(case["identification"]["gate_task_type"])
    if with_identification and case.get("decomposition"):
        record.validate_decomposition(
            questions=case["decomposition"]["questions"],
            waived=case["decomposition"].get("waived", []),
        )
    for answer in case.get("foundational", []):
        record.answer_foundational(
            qclass=answer["class"], question=answer["question"],
            rule_or_source=answer["rule_or_source"],
            evidence=answer["evidence"], answer=answer["answer"],
            basis=answer["basis"], contrary=answer["contrary"],
            effect=answer["effect"], status=answer.get("status", "answered"),
            resolution=answer.get("resolution", ""),
            issue=answer.get("issue", ""),
            depends_on=answer.get("depends_on", []),
            threshold=answer.get("threshold", False),
        )
    for element in case.get("elements", []):
        record.analyze_element(
            issue=element["issue"], test=element["test"],
            element=element["element"], rule=element["rule"],
            source=element["source"],
            facts_evidence=element["facts_evidence"],
            application=element["application"],
            supporting=element["supporting"],
            opposing=element["opposing"], missing=element["missing"],
            finding=element["finding"], effect=element["effect"],
        )
    for review in case.get("adversarial", []):
        record.record_adversarial(
            strongest_against=review["strongest_against"],
            fact_risks=review["fact_risks"],
            adverse_authority=review["adverse_authority"],
            overlooked=review["overlooked"],
            opposing_counsel=review["opposing_counsel"],
            judge_needs=review["judge_needs"],
            alternative_theory=review["alternative_theory"],
            reversing_fact=review["reversing_fact"],
            revision_needed=review["revision_needed"],
            revision=review["revision"],
        )
    if with_identification:
        if case.get("workflow"):
            record.plan_workflow(
                components=case["workflow"]["components"],
                rationale=case["workflow"].get("rationale", ""),
            )
        for triage in case.get("triages", []):
            record.triage_gap(
                ref=triage["ref"], gap=triage["gap"],
                needed_for=triage["needed_for"],
                sources_checked=triage["sources_checked"],
                decision=triage["decision"], detail=triage["detail"],
            )
        if case.get("work_product"):
            record.declare_work_product(
                product=case["work_product"]["product"],
                sections=case["work_product"]["sections"],
                traceability=case["work_product"]["traceability"],
            )
    if case.get("inject_cycle"):
        first, second = case["inject_cycle"]
        for entry in record.data.get("foundational", []):
            if entry.get("id") == first:
                entry["depends_on"] = [second]
    return run_id, record
