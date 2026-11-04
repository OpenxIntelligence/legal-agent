"""Self-contained synthetic evaluation cases and their on-disk layout.

These fixtures are not the workshop case: small synthetic matters with their own
documents and ground-truth checklists, each with a scripted action list that
drives a run. `materialize()` writes a runnable case layout into a temp root.
"""

from __future__ import annotations

import json
from pathlib import Path

from tests import support


def _checklist(*ids) -> list:
    return [
        {
            "id": item,
            "question": f"Question for {item}",
            "evidence_needed": f"A document showing {item}",
            "needs_signature_check": False,
        }
        for item in ids
    ]


CASES = {
    "harbor-readiness": {
        "objective": "Did the vendor know the harbor module was not ready, and say it was?",
        "documents": {
            "DOC-STATUS": [
                "Harbor weekly status. Open Severity 1 defects: 4 at period end. Overall status is amber."
            ],
            "DOC-NOTE": [
                "Harbor steering note. Ms. Perera confirmed the harbor module is ready for release. Ana Perera, Program Lead."
            ],
        },
        "reference": [
            {"id": "defects", "question": "Do internal records show open defects?"},
            {"id": "statement", "question": "Did the vendor say it was ready?"},
        ],
        "script": [
            ("set_checklist", {"items": _checklist("defects", "statement")}),
            (
                "analyze_document",
                {
                    "document_ids": ["DOC-STATUS", "DOC-NOTE"],
                    "request": "defects and readiness",
                },
            ),
            (
                "record_fact",
                {
                    "item": "defects",
                    "document_id": "DOC-STATUS",
                    "page": 1,
                    "quote": "Open Severity 1 defects: 4",
                    "claim": "internal records show open defects",
                },
            ),
            (
                "record_fact",
                {
                    "item": "statement",
                    "document_id": "DOC-NOTE",
                    "page": 1,
                    "quote": "Ms. Perera confirmed the harbor module is ready for release",
                    "claim": "the vendor said it was ready",
                },
            ),
            # Verified filings require task identity (closeout
            # §7.1): identify, plan, answer, challenge, then file.
            (
                "identify_task",
                {
                    "requested": "Did the vendor know the harbor module was not ready, and say it was?",
                    "objective": "Establish the factual record on defects and the readiness statement.",
                    "deliverable": "A verified memo answering both checklist questions.",
                    "underlying_problem": "Whether internal records contradict the readiness claim.",
                    "context_available": "Harbor status report and steering note, both supplied.",
                    "missing_or_ambiguous": "Whether the steering note postdates the defect record.",
                    "constraints": "Supplied documents only; no external law needed.",
                    "categories": [
                        {
                            "category": "evidence",
                            "rationale": "The work turns on extracting and weighing documentary evidence.",
                        }
                    ],
                    "primary": "evidence",
                    "gate_task_type": "investigation",
                    "jurisdiction": "not applicable: internal records review",
                    "law_basis": "supplied-documents",
                },
            ),
            (
                "validate_decomposition",
                {
                    "questions": [
                        {
                            "id": "p1",
                            "class": "objective",
                            "question": "What outcome must this investigation establish?",
                            "criterion": "The deliverable and its questions are named.",
                            "depends_on": [],
                        },
                        {
                            "id": "p2",
                            "class": "facts",
                            "question": "What do the supplied records state about defects?",
                            "criterion": "Each material fact traces to a recorded quote.",
                            "depends_on": [],
                        },
                        {
                            "id": "p3",
                            "class": "evidence",
                            "question": "Does the evidence support each material fact?",
                            "criterion": "Every finding cites its supporting evidence.",
                            "depends_on": [],
                        },
                        {
                            "id": "p4",
                            "class": "adverse",
                            "question": "What contrary evidence could change the result?",
                            "criterion": "Counterevidence is surfaced, not skipped.",
                            "depends_on": [],
                        },
                        {
                            "id": "p5",
                            "class": "unknowns",
                            "question": "What material facts remain unknown?",
                            "criterion": "Gaps are named or ruled out.",
                            "depends_on": [],
                        },
                        {
                            "id": "p6",
                            "class": "action",
                            "question": "What action does the record support?",
                            "criterion": "The conclusion follows from the findings.",
                            "depends_on": [],
                        },
                    ],
                },
            ),
            (
                "answer_foundational",
                {
                    "class": "objective",
                    "question": "What outcome must this investigation establish?",
                    "rule_or_source": "checklist questions defects and statement",
                    "evidence": "DOC-STATUS and DOC-NOTE supplied",
                    "answer": "Whether defects were open when readiness was declared.",
                    "basis": "The two checklist questions frame the memo.",
                    "contrary": "No contrary framing offered.",
                    "effect": "Sets the investigation's scope.",
                    "issue": "defects",
                },
            ),
            (
                "answer_foundational",
                {
                    "class": "facts",
                    "question": "What do the supplied records state about defects?",
                    "rule_or_source": "DOC-STATUS p1",
                    "evidence": "Open Severity 1 defects: 4",
                    "answer": "Four Sev-1 defects were open at period end.",
                    "basis": "Recorded fact F1.",
                    "contrary": "Overall status is amber, not red.",
                    "effect": "Supports the defects finding.",
                    "issue": "defects",
                },
            ),
            (
                "answer_foundational",
                {
                    "class": "evidence",
                    "question": "Does the evidence support each material fact?",
                    "rule_or_source": "DOC-NOTE p1",
                    "evidence": "Ms. Perera confirmed the harbor module is ready for release",
                    "answer": "The vendor declared readiness for release.",
                    "basis": "Recorded fact F2.",
                    "contrary": "The note gives no defect count.",
                    "effect": "Supports the statement finding.",
                    "issue": "statement",
                },
            ),
            (
                "answer_foundational",
                {
                    "class": "adverse",
                    "question": "What contrary evidence could change the result?",
                    "rule_or_source": "both supplied documents",
                    "evidence": "Amber status alongside the readiness claim.",
                    "answer": "Amber status softens but does not erase the defect count.",
                    "basis": "Severity-1 count is explicit.",
                    "contrary": "A later all-clear would reverse this.",
                    "effect": "Recorded as a limitation, not a blocker.",
                    "issue": "statement",
                },
            ),
            (
                "answer_foundational",
                {
                    "class": "unknowns",
                    "question": "What material facts remain unknown?",
                    "rule_or_source": "supplied record",
                    "evidence": "No certification document supplied.",
                    "answer": "Whether a formal certification exists is unknown.",
                    "basis": "No supplied document certifies readiness.",
                    "contrary": "None in the record.",
                    "effect": "Noted in gaps; the memo stays conditional on it.",
                    "issue": "defects",
                },
            ),
            (
                "answer_foundational",
                {
                    "class": "action",
                    "question": "What action does the record support?",
                    "rule_or_source": "the established findings",
                    "evidence": "Defects open when readiness was declared.",
                    "answer": "File the memo; escalate the contradiction.",
                    "basis": "Both findings are supported.",
                    "contrary": "No exonerating record supplied.",
                    "effect": "Authorizes this filing.",
                    "issue": "statement",
                },
            ),
            (
                "adversarial_review",
                {
                    "strongest_against": "Amber status suggests watchfulness, not failure.",
                    "fact_risks": "Defect count could predate a fix.",
                    "adverse_authority": "No contrary authority in a records review.",
                    "overlooked": "The note's date relative to the status report.",
                    "opposing_counsel": "Readiness is a judgment call, not a fact.",
                    "judge_needs": "Dates pinning knowledge before declaration.",
                    "alternative_theory": "Defects found after the note was written.",
                    "reversing_fact": "A dated all-clear before the note.",
                    "revision_needed": False,
                    "revision": "Challenge recorded; findings stand on the quotes.",
                },
            ),
            (
                "plan_workflow",
                {
                    "components": [
                        {
                            "workflow": "fact_evidence",
                            "purpose": "Extract and weigh the documentary record.",
                        },
                        {
                            "workflow": "adversarial_review",
                            "purpose": "Challenge the preliminary conclusion.",
                            "after": ["fact_evidence"],
                        },
                        {
                            "workflow": "quality_control",
                            "purpose": "Verify the memo before filing.",
                            "after": ["adversarial_review"],
                        },
                    ],
                },
            ),
            (
                "declare_work_product",
                {
                    "product": "direct_answer",
                    "sections": ["conclusion", "findings", "gaps"],
                    "traceability": "Each finding cites its checklist item and recorded facts.",
                },
            ),
            (
                "submit_memo",
                {
                    "conclusion": "Yes: the module was not ready when readiness was declared.",
                    "findings": [
                        {
                            "item": "defects",
                            "status": "asserted",
                            "summary": "Internal records show 4 open Sev-1 defects.",
                            "citations": [
                                {
                                    "document_id": "DOC-STATUS",
                                    "page": 1,
                                    "quote": "Open Severity 1 defects: 4",
                                }
                            ],
                        },
                        {
                            "item": "statement",
                            "status": "asserted",
                            "summary": "The vendor declared the module ready for release.",
                            "citations": [
                                {
                                    "document_id": "DOC-NOTE",
                                    "page": 1,
                                    "quote": "Ms. Perera confirmed the harbor module is ready for release",
                                }
                            ],
                        },
                    ],
                    "gaps": "None.",
                },
            ),
        ],
    },
    "harbor-gap": {
        "objective": "Is the harbor module certified, and was it announced?",
        "documents": {
            "DOC-STATUS": ["Harbor weekly status. Overall status is amber."],
        },
        "reference": [
            {"id": "defects", "question": "Do internal records show open defects?"},
            {"id": "statement", "question": "Did the vendor say it was ready?"},
        ],
        "script": [
            ("set_checklist", {"items": _checklist("defects", "statement")}),
            (
                "analyze_document",
                {"document_ids": ["DOC-STATUS"], "request": "defects and readiness"},
            ),
            (
                "record_fact",
                {
                    "item": "defects",
                    "document_id": "DOC-STATUS",
                    "page": 1,
                    "quote": "Overall status is amber",
                    "claim": "internal records show amber status",
                },
            ),
            (
                "request_evidence",
                {"item": "statement", "need": "a dated readiness note"},
            ),
        ],
    },
}


def materialize(root: Path, case: dict) -> None:
    """Write a runnable case layout (case, ground truth, inbox) under root."""
    (root / "case").mkdir(parents=True, exist_ok=True)
    (root / "case/objective.md").write_text(case["objective"])
    (root / "case/instructions.md").write_text(support.instructions_file())
    (root / "ground_truth").mkdir(exist_ok=True)
    (root / "ground_truth/case.json").write_text(
        json.dumps({"items": case["reference"]})
    )
    inbox = root / "workspace/inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "pages").mkdir(exist_ok=True)
    for doc_id, pages in case["documents"].items():
        (inbox / f"{doc_id}.pdf").write_bytes(("\n".join(pages)).encode())
        for n in range(1, len(pages) + 1):
            (inbox / "pages" / f"{doc_id}-p{n}.png").write_bytes(
                support.PNG + doc_id.encode() + bytes([n])
            )


def transcripts(case: dict) -> dict:
    """Scripted extraction transcripts matching the fixture documents."""
    return {
        doc_id: [{"page": n + 1, "text": text} for n, text in enumerate(pages)]
        for doc_id, pages in case["documents"].items()
    }
