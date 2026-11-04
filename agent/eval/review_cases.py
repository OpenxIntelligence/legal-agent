"""NV-2 review-to-repair fixtures: temporal support + irrelevant objection.

Two small invented matters for the native review chain. Both run
through the real audit path (structural checks, scripted semantic
judge, record) like eval/artifact_cases, but their purpose is the
review lifecycle: label -> grounded issue -> repair ->
reassessment, with the original obligation preserved.

review-temporal-support: a finding asserts knowledge BEFORE an
approval; its only source is dated AFTER the approval. The quote
matches (structural pass) but the inference overreaches
(scripted semantic fail until a qualified revision narrows it).

review-irrelevant-objection: a clean, fully supported finding.
An unrelated objection must be disprovable without revising the
correct claim — the chain must not reward excessive refusal.
"""

from __future__ import annotations

from pathlib import Path

from eval.artifact_cases import write_document

CASES = {
    "review-temporal-support": {
        "title": "Knowledge timing vs approval date (temporal support)",
        "documents": {
            "REVIEW-EMAIL": [
                "Status email. Reyes learned of the shortfall on May 6.",
            ],
        },
        "memo": {
            "conclusion": "The approval of May 5 stands; knowledge "
            "timing needs care.",
            "findings": [
                {
                    "item": "knowledge-timing",
                    "summary": "Reyes knew of the shortfall before "
                    "the May 5 approval.",
                    "citations": [
                        {
                            "document_id": "REVIEW-EMAIL",
                            "page": 1,
                            "quote": "Reyes learned of the shortfall "
                            "on May 6.",
                        }
                    ],
                },
            ],
            "gaps": "Whether Reyes knew before May 5 is unresolved "
            "in the reviewed record.",
        },
        # Scripted judge: the overreaching temporal unit is
        # unsupported; the qualified revision is supported.
        "unsupported_before": ["finding:R1"],
        "unsupported_after": [],
        "repaired_assertion": "The email establishes knowledge by "
        "May 6. The reviewed record does not establish whether "
        "knowledge preceded the May 5 approval.",
    },
    "review-irrelevant-objection": {
        "title": "Clean totals with an unrelated challenge",
        "documents": {
            "REVIEW-LEDGER": [
                "Harborview ledger page one. Paid invoices total 20000.",
            ],
        },
        "memo": {
            "conclusion": "The ledger totals are established.",
            "findings": [
                {
                    "item": "ledger-totals",
                    "summary": "Paid invoices total 20000.",
                    "citations": [
                        {
                            "document_id": "REVIEW-LEDGER",
                            "page": 1,
                            "quote": "Paid invoices total 20000.",
                        }
                    ],
                },
            ],
            "gaps": "No open items remain in the reviewed ledger.",
        },
        "unsupported_before": [],
        "unsupported_after": [],
        "repaired_assertion": None,
    },
}


def materialize(root: Path, name: str):
    """Build the fixture matter; return (run_id, record, instance)."""
    from agent_kit.case_record import CaseRecord

    case = CASES[name]
    run_id = f"eval-{name}"
    for doc_id, pages in case["documents"].items():
        write_document(root, doc_id, pages)
    record = CaseRecord(root, run_id)
    record.file_memo(case["memo"], "unverified")
    instance = record.artifact_open(case["title"])
    return run_id, record, instance
