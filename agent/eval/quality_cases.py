"""Frozen legal-quality benchmark: synthetic matters with planted truth.

Each case is a small invented matter ("Meridian" fixtures, unrelated
to the Harborview demo) covering the failure categories the quality
brief requires: unsupported assertions, citations without support,
altered quotations, missing required issues, contradictory evidence,
omitted counterarguments, jurisdiction/temporal applicability, stale
versions, overconfident conclusions, and missing-information handling.

Stale versions, wrong page, absent source, missing quote, revoked,
and untranscribed classes are already frozen in
`eval/artifact_cases.py`; this benchmark references those cases
rather than duplicating them, and freezes the remaining
categories here. Truth is planted in the fixture, never derived
from system output. The grader (`eval/quality_run.py`) compares
observed rows AND observed defect records against truth.

Frozen: 2026-10-29. Do not edit cases to match implementation
changes; add new cases instead.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

# Defect categories, shared with the artifact defect ledger:
#   unsupported_claim        semantic failed on a finding/gaps row
#   unsupported_conclusion   semantic failed on the conclusion row
#   citation_failed          structural failed (page/source/quote/version/permission)
#   citation_incomplete      structural incomplete (e.g. untranscribed)
#   contradictory_evidence   supporting + opposing facts recorded for the row's item
#   uncited_counterevidence  asserted finding ignores recorded opposing facts
#   uncovered_issue          checklist item with no finding in the memo
#   unstated_open_issues     gaps text empty while issues stay open/uncovered
DEFECT_CATEGORIES = frozenset(
    {
        "unsupported_claim",
        "unsupported_conclusion",
        "citation_failed",
        "citation_incomplete",
        "contradictory_evidence",
        "uncited_counterevidence",
        "uncovered_issue",
        "unstated_open_issues",
    }
)

CASES = {
    "quality-quotes": {
        "title": "Quotation and citation correctness",
        "brief": {
            "expected_findings": (
                "R1 clean; R2 fails structurally (altered quotation); "
                "R3 fails structurally (quote cited to the wrong page)."
            ),
            "relevant_passages": [
                "QV-LEDGER p1: Paid invoices total 41000.",
                "QV-LEDGER p2: Two invoices are overdue.",
                "QV-NOTE p1: The ledger was reconciled on April 2.",
            ],
            "required_issues": ["qv_totals", "qv_status", "qv_note"],
            "prohibited_conclusions": [
                "No invoices are overdue.",
            ],
            "limitations": (
                "Quote matching is normalized-substring only; paraphrase "
                "detection is out of scope."
            ),
        },
        "documents": {
            "QV-LEDGER": [
                "Meridian ledger page one. Paid invoices total 41000.",
                "Meridian ledger page two. Two invoices are overdue.",
            ],
            "QV-NOTE": [
                "Counsel note. The ledger was reconciled on April 2.",
                "Counsel note continued. No further entries this quarter.",
            ],
        },
        "checklist": [
            {"id": "qv_totals", "question": "What do paid invoices total?",
             "evidence_needed": "ledger records"},
            {"id": "qv_status", "question": "Are any invoices overdue?",
             "evidence_needed": "ledger records"},
            {"id": "qv_note", "question": "Was the ledger reconciled?",
             "evidence_needed": "counsel note"},
        ],
        "facts": [
            {"item": "qv_totals", "document_id": "QV-LEDGER", "page": 1,
             "quote": "Paid invoices total 41000.", "claim": "paid total 41000"},
            {"item": "qv_status", "document_id": "QV-LEDGER", "page": 2,
             "quote": "Two invoices are overdue.", "claim": "two overdue"},
            {"item": "qv_note", "document_id": "QV-NOTE", "page": 1,
             "quote": "The ledger was reconciled on April 2.",
             "claim": "reconciled April 2"},
        ],
        "memo": {
            "conclusion": "The ledger is reconciled and two invoices are overdue.",
            "findings": [
                {
                    "item": "qv_totals",
                    "status": "asserted",
                    "summary": "Paid invoices total 41000.",
                    "citations": [
                        {"document_id": "QV-LEDGER", "page": 1,
                         "quote": "Paid invoices total 41000."}
                    ],
                },
                {
                    "item": "qv_status",
                    "status": "asserted",
                    "summary": "No invoices are overdue.",
                    "citations": [
                        {"document_id": "QV-LEDGER", "page": 2,
                         "quote": "No invoices are overdue."}
                    ],
                },
                {
                    "item": "qv_note",
                    "status": "asserted",
                    "summary": "The ledger was reconciled on April 2.",
                    "citations": [
                        {"document_id": "QV-NOTE", "page": 2,
                         "quote": "The ledger was reconciled on April 2."}
                    ],
                },
            ],
            "gaps": "Whether the overdue invoices were later paid is unresolved.",
        },
        "unsupported": [],
        "truth": {
            "R1": {"class": "clean", "structural": "passed", "semantic": "passed"},
            "R2": {"class": "altered_quote", "structural": "failed",
                   "semantic": "not_run"},
            "R3": {"class": "wrong_page", "structural": "failed",
                   "semantic": "not_run"},
            "RC": {"class": "clean", "structural": "failed", "semantic": "passed"},
            "RG": {"class": "clean", "structural": "not_run", "semantic": "passed"},
        },
        "defects": [
            {"category": "citation_failed", "target": "R2"},
            {"category": "citation_failed", "target": "R3"},
        ],
    },
    "quality-counterevidence": {
        "title": "Contradiction and omitted counterargument",
        "brief": {
            "expected_findings": (
                "R1 cites a real passage but overstates it (unsupported "
                "inference), ignores recorded opposing maintenance evidence, "
                "and its item is contested; R2 quotes an allegation as an "
                "allegation and passes; the conclusion overclaims."
            ),
            "relevant_passages": [
                "QV-REPORT p1: The valve showed wear.",
                "QV-REPORT p1: The cause of the wear was not determined.",
                "QV-MAINT p1: Maintenance was performed on schedule in every quarter.",
                "QV-WITNESS p1: Rivera alleges the valve failed during testing.",
            ],
            "required_issues": ["qv_cause", "qv_testing"],
            "prohibited_conclusions": [
                "The valve failed because maintenance lapsed.",
                "The valve failure was caused solely by lapsed maintenance.",
            ],
            "limitations": (
                "Whether the omitted counterevidence is material needs "
                "human legal judgment; the system must surface it, not "
                "resolve it."
            ),
        },
        "documents": {
            "QV-REPORT": [
                "Inspection report. The valve showed wear. The cause "
                "of the wear was not determined.",
            ],
            "QV-MAINT": [
                "Maintenance log. Maintenance was performed on schedule "
                "in every quarter.",
            ],
            "QV-WITNESS": [
                "Witness statement. Rivera alleges the valve failed "
                "during testing.",
            ],
        },
        "checklist": [
            {"id": "qv_cause", "question": "What caused the valve wear?",
             "evidence_needed": "inspection and maintenance records"},
            {"id": "qv_testing", "question": "What does the witness state?",
             "evidence_needed": "witness statement"},
        ],
        "facts": [
            {"item": "qv_cause", "document_id": "QV-REPORT", "page": 1,
             "quote": "The valve showed wear.", "claim": "valve wear observed"},
            {"item": "qv_cause", "document_id": "QV-MAINT", "page": 1,
             "quote": "Maintenance was performed on schedule in every quarter.",
             "claim": "maintenance on schedule", "stance": "opposes"},
            {"item": "qv_testing", "document_id": "QV-WITNESS", "page": 1,
             "quote": "Rivera alleges the valve failed during testing.",
             "claim": "witness alleges failure"},
        ],
        "memo": {
            "conclusion": "The valve failure was caused solely by lapsed maintenance.",
            "findings": [
                {
                    "item": "qv_cause",
                    "status": "asserted",
                    "summary": "The valve failed because maintenance lapsed.",
                    "citations": [
                        {"document_id": "QV-REPORT", "page": 1,
                         "quote": "The valve showed wear."}
                    ],
                },
                {
                    "item": "qv_testing",
                    "status": "asserted",
                    "summary": "Rivera states the valve failed during testing.",
                    "citations": [
                        {"document_id": "QV-WITNESS", "page": 1,
                         "quote": "Rivera alleges the valve failed during testing."}
                    ],
                },
            ],
            "gaps": "Whether wear alone could cause the failure is unresolved.",
        },
        "unsupported": ["finding:R1", "conclusion"],
        "truth": {
            "R1": {"class": "unsupported_inference", "structural": "passed",
                   "semantic": "failed"},
            "R2": {"class": "clean", "structural": "passed", "semantic": "passed"},
            "RC": {"class": "unsupported_conclusion", "structural": "passed",
                   "semantic": "failed"},
            "RG": {"class": "clean", "structural": "not_run", "semantic": "passed"},
        },
        "defects": [
            {"category": "unsupported_claim", "target": "R1"},
            {"category": "contradictory_evidence", "target": "R1"},
            {"category": "uncited_counterevidence", "target": "R1"},
            {"category": "unsupported_conclusion", "target": "RC"},
        ],
    },
    "quality-coverage": {
        "title": "Required-issue coverage and missing information",
        "brief": {
            "expected_findings": (
                "R1 and R2 pass; qv_access has no finding (uncovered); "
                "gaps text is empty while issues stay open (unstated); "
                "the conclusion overclaims the unreviewed audit."
            ),
            "relevant_passages": [
                "QV-SCOPE-A p1: The delivery audit is complete.",
                "QV-SCOPE-B p1: The payment audit is complete.",
                "QV-SCOPE-C p1: The access review has not started.",
            ],
            "required_issues": ["qv_delivery", "qv_payment", "qv_access"],
            "prohibited_conclusions": [
                "All three audits are complete.",
            ],
            "limitations": (
                "Coverage is by checklist item id; a finding that names "
                "an item without addressing it needs human review."
            ),
        },
        "documents": {
            "QV-SCOPE-A": ["Scope memo. The delivery audit is complete."],
            "QV-SCOPE-B": ["Scope memo. The payment audit is complete."],
            "QV-SCOPE-C": ["Scope memo. The access review has not started."],
        },
        "checklist": [
            {"id": "qv_delivery", "question": "Is the delivery audit complete?",
             "evidence_needed": "scope memo"},
            {"id": "qv_payment", "question": "Is the payment audit complete?",
             "evidence_needed": "scope memo"},
            {"id": "qv_access", "question": "Has the access review started?",
             "evidence_needed": "scope memo"},
        ],
        "facts": [
            {"item": "qv_delivery", "document_id": "QV-SCOPE-A", "page": 1,
             "quote": "The delivery audit is complete.", "claim": "delivery done"},
            {"item": "qv_payment", "document_id": "QV-SCOPE-B", "page": 1,
             "quote": "The payment audit is complete.", "claim": "payment done"},
        ],
        "memo": {
            "conclusion": "All three audits are complete.",
            "findings": [
                {
                    "item": "qv_delivery",
                    "status": "asserted",
                    "summary": "The delivery audit is complete.",
                    "citations": [
                        {"document_id": "QV-SCOPE-A", "page": 1,
                         "quote": "The delivery audit is complete."}
                    ],
                },
                {
                    "item": "qv_payment",
                    "status": "asserted",
                    "summary": "The payment audit is complete.",
                    "citations": [
                        {"document_id": "QV-SCOPE-B", "page": 1,
                         "quote": "The payment audit is complete."}
                    ],
                },
            ],
            "gaps": "",
        },
        "unsupported": ["conclusion"],
        # NOTE (fixture fix, score-neutral): no RG truth — empty gaps
        # text correctly yields no gaps row, and the missing-information
        # signal is graded as the unstated_open_issues defect instead.
        "truth": {
            "R1": {"class": "clean", "structural": "passed", "semantic": "passed"},
            "R2": {"class": "clean", "structural": "passed", "semantic": "passed"},
            "RC": {"class": "unsupported_conclusion", "structural": "passed",
                   "semantic": "failed"},
        },
        "defects": [
            {"category": "uncovered_issue", "target": "qv_access"},
            {"category": "unstated_open_issues", "target": "RG"},
            {"category": "unsupported_conclusion", "target": "RC"},
        ],
    },
    "quality-jurisdiction": {
        "title": "Applicability unknowns stay explicit (matrix stages)",
        "brief": {
            "expected_findings": (
                "Stage 1: an unlinked, uncited, undated, unreviewed cell "
                "blocks readiness with four exact reasons. Stage 2: after "
                "linking a known branch, recording an illustrative "
                "citation, a known effective date, and confirmation, the "
                "cell resolves. Stage 3: disputing the branch restales "
                "the cell with the branch reason named."
            ),
            "relevant_passages": ["(no documents: applicability record only)"],
            "required_issues": ["qv_license"],
            "prohibited_conclusions": [
                "California law governs (before the branch is known).",
            ],
            "limitations": (
                "The stage-2 citation is explicitly illustrative ('no "
                "authority consulted'); the benchmark measures readiness "
                "and staleness mechanics, never legal correctness."
            ),
        },
        "kind": "matrix",
        "checklist": [
            {"id": "qv_license",
             "question": "Which regime governs the license renewal?",
             "evidence_needed": "operator connecting facts"},
        ],
        "stages": {
            "stage1_blockers": sorted(
                [
                    "no linked branch",
                    "no authority citation",
                    "effective date unknown",
                    "review: unreviewed",
                ]
            ),
            "stage2_resolved": 1,
            "stage2_provisional": False,
            "stage3_stale_reason": "the linked branch changed",
            "stage3_provisional": True,
        },
    },
}


def write_document(root: Path, doc_id: str, pages: list[str]) -> str:
    """Write one inbox file plus its extraction transcript; return sha."""
    inbox = root / "workspace/inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    blob = "\n".join(pages).encode()
    (inbox / f"{doc_id}.pdf").write_bytes(blob)
    sha = hashlib.sha256(blob).hexdigest()
    cache = root / "workspace/cache/text"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / f"{sha}.json").write_text(
        json.dumps(
            {"pages": [{"page": n + 1, "text": text}
                       for n, text in enumerate(pages)]}
        )
    )
    return sha


def materialize(root: Path, name: str):
    """Build the fixture matter; return (run_id, record, instance).

    Audit cases open an audit instance over the filed memo.
    The matrix case opens a matrix instance and seeds one cell;
    stage transitions are driven by the grader.
    """
    from agent_kit import artifacts
    from agent_kit.case_record import CaseRecord

    case = CASES[name]
    run_id = f"quality-{name}"
    for doc_id, pages in case.get("documents", {}).items():
        write_document(root, doc_id, pages)
    record = CaseRecord(root, run_id)
    record.set_checklist(case["checklist"])
    if case.get("documents"):
        record.mark_read(sorted(case["documents"]))
    for fact in case.get("facts", []):
        record.record_fact(
            fact["item"],
            fact["document_id"],
            fact["page"],
            fact["quote"],
            fact["claim"],
            stance=fact.get("stance", "supports"),
        )
    if case.get("kind") == "matrix":
        instance = record.artifact_open(case["title"], "jurisdiction-matrix")
        artifacts.add_cell(instance, record.data, "qv_license", "ca", "substantive")
        record.artifact_save(instance, "artifact_opened", "eval matrix seed")
        return run_id, record, instance
    record.file_memo(case["memo"], "unverified")
    instance = record.artifact_open(case["title"])
    return run_id, record, instance
