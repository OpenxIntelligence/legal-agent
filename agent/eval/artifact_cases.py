"""Phase F stage 1: synthetic audit-eval fixtures (mechanics only).

Each case is a small invented matter with planted per-row defect
truth: the runner pushes it through the real audit path
(structural checks, scripted semantic judge, record) and grades
the observed outcomes against truth. Nothing here measures live
model quality: semantic verdicts come from the scripted judge,
so the semantic arm proves plumbing (eligibility gating, unit
mapping, record shape), not judgment.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

CASES = {
    "eval-clean-ledger": {
        "title": "Clean ledger (all rows should pass)",
        "documents": {
            "EVAL-LEDGER": [
                "Harborview ledger page one. Paid invoices total 20000.",
                "Harborview ledger page two. No invoices are overdue.",
            ],
            "EVAL-NOTE": [
                "Counsel note. The ledger was reconciled on March 1.",
            ],
        },
        "memo": {
            "conclusion": "The ledger is reconciled and nothing is overdue.",
            "findings": [
                {
                    "item": "ledger-totals",
                    "summary": "Paid invoices total 20000.",
                    "citations": [
                        {
                            "document_id": "EVAL-LEDGER",
                            "page": 1,
                            "quote": "Paid invoices total 20000.",
                        }
                    ],
                },
                {
                    "item": "ledger-status",
                    "summary": "No invoices are overdue.",
                    "citations": [
                        {
                            "document_id": "EVAL-LEDGER",
                            "page": 2,
                            "quote": "No invoices are overdue.",
                        },
                        {
                            "document_id": "EVAL-NOTE",
                            "page": 1,
                            "quote": "ledger was reconciled on March 1",
                        },
                    ],
                },
            ],
            "gaps": "No open items remain in the reviewed ledger.",
        },
        # Scripted judge: every unit supported.
        "unsupported": [],
        "truth": {
            "R1": {"class": "clean", "structural": "passed",
                   "semantic": "passed"},
            "R2": {"class": "clean", "structural": "passed",
                   "semantic": "passed"},
            "RC": {"class": "clean", "structural": "passed",
                   "semantic": "passed"},
            "RG": {"class": "clean", "structural": "not_run",
                   "semantic": "passed"},
        },
    },
    "eval-citation-defects": {
        "title": "Citation defects (structural layer must catch each)",
        "documents": {
            "EVAL-CONTRACT": [
                "Contract page one. Delivery is due on June 1.",
                "Contract page two. Late delivery accrues a fee.",
            ],
            "EVAL-EMAIL": [
                "Email thread. The parties confirmed the June 1 date.",
            ],
            "EVAL-DRAFT": [
                "Draft terms. This draft was never signed.",
            ],
            "EVAL-SCAN": [
                "Scanned schedule. Mobilization starts in week three.",
            ],
        },
        # Inbox file with no extraction transcript.
        "untranscribed": ["EVAL-SCAN"],
        # Access revoked after filing.
        "revoked": ["EVAL-DRAFT"],
        # Rewritten after the audit pins its sha (quote kept, so
        # the defect is purely the version mismatch).
        "rewritten": {
            "EVAL-EMAIL": [
                "Email thread. The parties confirmed the June 1 date. "
                "Amended thread with a new paragraph.",
            ],
        },
        "memo": {
            "conclusion": "Delivery is due June 1 with a late fee.",
            "findings": [
                {
                    "item": "clean-anchor",
                    "summary": "Delivery is due on June 1.",
                    "citations": [
                        {
                            "document_id": "EVAL-CONTRACT",
                            "page": 1,
                            "quote": "Delivery is due on June 1.",
                        }
                    ],
                },
                {
                    "item": "wrong-page",
                    "summary": "Late delivery accrues a fee.",
                    "citations": [
                        {
                            "document_id": "EVAL-CONTRACT",
                            "page": 1,
                            "quote": "Late delivery accrues a fee.",
                        }
                    ],
                },
                {
                    "item": "absent-source",
                    "summary": "The schedule was approved.",
                    "citations": [
                        {
                            "document_id": "EVAL-MISSING",
                            "page": 1,
                            "quote": "The schedule was approved.",
                        }
                    ],
                },
                {
                    "item": "missing-quote",
                    "summary": "The parties confirmed the date.",
                    "citations": [
                        {
                            "document_id": "EVAL-EMAIL",
                            "page": 1,
                            "quote": "",
                        }
                    ],
                },
                {
                    "item": "revoked-source",
                    "summary": "The draft was never signed.",
                    "citations": [
                        {
                            "document_id": "EVAL-DRAFT",
                            "page": 1,
                            "quote": "This draft was never signed.",
                        }
                    ],
                },
                {
                    "item": "untranscribed-source",
                    "summary": "Mobilization starts in week three.",
                    "citations": [
                        {
                            "document_id": "EVAL-SCAN",
                            "page": 1,
                            "quote": "Mobilization starts in week three.",
                        }
                    ],
                },
                {
                    "item": "changed-source",
                    "summary": "The parties confirmed the June 1 date.",
                    "citations": [
                        {
                            "document_id": "EVAL-EMAIL",
                            "page": 1,
                            "quote": "The parties confirmed the June 1 date.",
                        }
                    ],
                },
            ],
            "gaps": "No further delivery terms were reviewed.",
        },
        # Scripted judge: every eligible unit supported.
        "unsupported": [],
        "truth": {
            "R1": {"class": "clean", "structural": "passed",
                   "semantic": "passed"},
            "R2": {"class": "wrong_page", "structural": "failed",
                   "semantic": "not_run"},
            "R3": {"class": "absent_source", "structural": "failed",
                   "semantic": "not_run"},
            "R4": {"class": "missing_quote", "structural": "failed",
                   "semantic": "not_run"},
            "R5": {"class": "revoked_source", "structural": "failed",
                   "semantic": "not_run"},
            "R6": {"class": "untranscribed", "structural": "incomplete",
                   "semantic": "not_run"},
            "R7": {"class": "changed_source", "structural": "failed",
                   "semantic": "not_run"},
            # The conclusion inherits every finding citation, so it
            # is structurally failed; it stays semantically eligible
            # because R1 offers valid evidence.
            "RC": {"class": "clean", "structural": "failed",
                   "semantic": "passed"},
            "RG": {"class": "clean", "structural": "not_run",
                   "semantic": "passed"},
        },
    },
    "eval-inference-defects": {
        "title": "Inference defects (judge plumbing must route each)",
        "documents": {
            "EVAL-ALLEGATION": [
                "Witness statement. Rivera alleges the valve failed.",
            ],
            "EVAL-REPORT": [
                "Inspection report. The valve showed wear. The cause "
                "of the wear was not determined.",
            ],
        },
        "memo": {
            "conclusion": "The valve failed because maintenance lapsed.",
            "findings": [
                {
                    "item": "unsupported-inference",
                    "summary": "The valve failed because maintenance lapsed.",
                    "citations": [
                        {
                            "document_id": "EVAL-REPORT",
                            "page": 1,
                            "quote": "The valve showed wear.",
                        }
                    ],
                },
                {
                    "item": "qualified-uncertainty",
                    "summary": "The cause of the wear was not determined "
                    "in the reviewed report.",
                    "citations": [
                        {
                            "document_id": "EVAL-REPORT",
                            "page": 1,
                            "quote": "The cause of the wear was not "
                            "determined.",
                        }
                    ],
                },
                {
                    "item": "allegation-as-fact",
                    "summary": "Rivera states the valve failed.",
                    "citations": [
                        {
                            "document_id": "EVAL-ALLEGATION",
                            "page": 1,
                            "quote": "Rivera alleges the valve failed.",
                        }
                    ],
                },
            ],
            "gaps": "No maintenance log was found in the reviewed record.",
        },
        # Scripted judge marks the overreaching units unsupported.
        "unsupported": ["finding:R1", "conclusion"],
        "truth": {
            "R1": {"class": "unsupported_inference",
                   "structural": "passed", "semantic": "failed"},
            "R2": {"class": "qualified_uncertainty",
                   "structural": "passed", "semantic": "passed"},
            "R3": {"class": "clean", "structural": "passed",
                   "semantic": "passed"},
            "RC": {"class": "unsupported_conclusion",
                   "structural": "passed", "semantic": "failed"},
            "RG": {"class": "clean", "structural": "not_run",
                   "semantic": "passed"},
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
    """Build the fixture matter; return (run_id, case, instance).

    Order matters: the audit opens (pinning shas) before any
    post-open mutation, so changed-source defects stale exactly
    the rows that pinned the old file.
    """
    from agent_kit.case_record import CaseRecord

    case = CASES[name]
    run_id = f"eval-{name}"
    for doc_id, pages in case["documents"].items():
        write_document(root, doc_id, pages)
    for doc_id in case.get("untranscribed", []):
        blob = "\n".join(case["documents"][doc_id]).encode()
        sha = hashlib.sha256(blob).hexdigest()
        (root / "workspace/cache/text" / f"{sha}.json").unlink()
    record = CaseRecord(root, run_id)
    record.file_memo(case["memo"], "unverified")
    for doc_id in case.get("revoked", []):
        record.flag_privileged(doc_id, "eval fixture: access revoked")
    instance = record.artifact_open(case["title"])
    for doc_id, pages in case.get("rewritten", {}).items():
        write_document(root, doc_id, pages)
    return run_id, record, instance
