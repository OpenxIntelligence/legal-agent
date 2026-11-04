"""Shared assessment records: versioned, content-bound review evidence.

Every QC assessment binds to the exact memo draft and evidence state it
reviewed: a memo version (filed revisions plus content hash) and an
evidence version (fingerprint over recorded facts). Outcomes reuse the
M1 vocabulary and add two explicit non-verdicts: inapplicable (the
module does not apply) and unexecuted (the check was honestly skipped).
"""

from __future__ import annotations

import datetime
import hashlib
import json

OUTCOMES = ("passed", "failed", "unavailable", "inapplicable", "unexecuted")


def memo_hash(memo: dict) -> str:
    """Short content hash identifying one memo draft."""
    canonical = json.dumps(memo or {}, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:12]


def memo_version(case_data: dict, memo: dict) -> dict:
    """The draft under review: filed revisions plus content hash."""
    history = case_data.get("memo_history") or []
    return {"filed": len(history), "hash": memo_hash(memo)}


def evidence_fingerprint(case_data: dict) -> str:
    """Short hash over recorded fact (id, document, page, quote, stance)."""
    tuples = sorted(
        (
            str(f.get("id", "")),
            str(f.get("document_id", "")),
            str(f.get("page", "")),
            str(f.get("quote", "")),
            str(f.get("stance", "supports")),
        )
        for f in (case_data.get("facts") or [])
        if isinstance(f, dict)
    )
    return hashlib.sha256(json.dumps(tuples).encode()).hexdigest()[:12]


def assessment(
    *,
    module: str,
    module_version: str,
    memo_version: dict,
    evidence_version: str,
    outcome: str,
    unit_id: str | None = None,
    summary: str = "",
    findings: list | None = None,
) -> dict:
    """One module's assessment of one draft. Unknown outcomes fail loud."""
    if outcome not in OUTCOMES:
        raise ValueError(f"unknown assessment outcome {outcome!r}")
    return {
        "module": module,
        "module_version": module_version,
        "target": {
            "memo": memo_version,
            "evidence": evidence_version,
            "unit": unit_id,
        },
        "outcome": outcome,
        "summary": summary,
        "findings": findings or [],
        "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
