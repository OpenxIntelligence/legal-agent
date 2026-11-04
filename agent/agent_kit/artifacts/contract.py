"""contract review (clause/term tabular review) (one artifact slice; split from artifacts.py, behavior unchanged)."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from agent_kit import document_text
from agent_kit.case_record import CaseError
from agent_kit.progress import now

from .kernel import (
    TEMPLATE_AUDIT,
    TEMPLATE_BOARD,
    TEMPLATE_CAL,
    TEMPLATE_CT,
    TEMPLATE_DL,
    TEMPLATE_DR,
    TEMPLATE_EC,
    TEMPLATE_GV,
    TEMPLATE_HZ,
    TEMPLATE_IN,
    TEMPLATE_JX,
    TEMPLATE_PB,
    TEMPLATE_PT,
    TEMPLATE_QA,
    TEMPLATE_SP,
    TEMPLATE_TS,
    TEMPLATE_AUDIT_VERSION,
    TEMPLATE_BOARD_VERSION,
    TEMPLATE_JX_VERSION,
    TEMPLATE_CT_VERSION,
    TEMPLATE_PB_VERSION,
    TEMPLATE_CAL_VERSION,
    TEMPLATE_IN_VERSION,
    TEMPLATE_DL_VERSION,
    TEMPLATE_DR_VERSION,
    TEMPLATE_HZ_VERSION,
    TEMPLATE_GV_VERSION,
    TEMPLATE_EC_VERSION,
    TEMPLATE_TS_VERSION,
    TEMPLATE_PT_VERSION,
    TEMPLATE_QA_VERSION,
    TEMPLATE_SP_VERSION,
    _blank_review,
    _excerpt,
    _flagged,
    _inbox_shas,
    _next_instance_id,
    _stable,
    _structural_outcome,
    audit_citation,
    register,
)


CT_RISKS = ("none", "low", "high")
CT_REVIEW = ("unreviewed", "confirmed", "flagged")


def open_ct_instance(
    data: dict, run_id: str, title: str, source_id: str = ""
) -> dict:
    """Create a contract review over read documents (pure; caller saves).

    Needs read documents, not a memo: clauses bind quoted
    passages, and only successfully read documents count.
    """
    read = [d for d in data.get("read_ids", []) or [] if d]
    if not read:
        raise CaseError("the review needs read documents")
    instance = {
        "id": _next_instance_id(data),
        "template": TEMPLATE_CT,
        "template_version": TEMPLATE_CT_VERSION,
        "matter": run_id,
        "title": title or "Clause/Term Tabular Review",
        "owner": "local",
        "viewers": ["local"],
        "created_at": now(),
        "clauses": [],
        "versions": [],
        "review": _blank_review(),
        "history": [{"at": now(), "event": "opened", "detail": ""}],
    }
    instance["versions"].append(
        {"v": 1, "at": now(), "reason": "opened", "clauses": []}
    )
    return instance


def ct_get_clause(instance: dict, clause_id: str) -> dict:
    clause = next(
        (c for c in instance.get("clauses") or [] if c.get("clause_id") == clause_id),
        None,
    )
    if clause is None:
        raise CaseError(f"unknown clause {clause_id!r}")
    return clause


def _ct_version(instance: dict, reason: str) -> None:
    versions = instance.setdefault("versions", [])
    versions.append(
        {
            "v": len(versions) + 1,
            "at": now(),
            "reason": reason,
            "clauses": copy.deepcopy(instance.get("clauses")),
        }
    )


def _ct_touch(instance: dict, event: str, detail: str) -> None:
    instance["history"].append({"at": now(), "event": event, "detail": detail})
    instance["review"] = _blank_review()


def _next_clause_id(instance: dict) -> str:
    taken = {clause.get("clause_id", "") for clause in instance.get("clauses") or []}
    seq = 1
    while f"K{seq}" in taken:
        seq += 1
    return f"K{seq}"


def add_clause(
    instance: dict,
    data: dict,
    root: Path,
    document_id: str,
    page: Any,
    quote: str,
    heading: str,
) -> dict:
    """Add one clause bound to an exact quoted passage.

    The quote must pass the same deterministic citation check
    the audit uses: existing source, valid page, exact words on
    the page, current version, permitted access. Anything less
    is refused with the check's reasons — a clause never opens
    on an unverified passage.
    """
    if document_id not in (data.get("read_ids", []) or []):
        raise CaseError(
            f"{document_id or 'empty source'} has not been read; "
            "read it before quoting it"
        )
    try:
        page_number = int(page)
    except (TypeError, ValueError):
        raise CaseError(f"page {page!r} is not a valid page number") from None
    shas = _inbox_shas(root)
    citation = {
        "document_id": document_id,
        "page": page_number,
        "quote": str(quote or ""),
        "pin_sha": shas.get(document_id),
    }
    checked = audit_citation(root, citation, shas, _flagged(data))
    if _structural_outcome([checked]) != "passed":
        raise CaseError("; ".join(checked["problems"]) or "quote check failed")
    clause = {
        "clause_id": _next_clause_id(instance),
        "document_id": document_id,
        "page": page_number,
        "quote": str(quote),
        "pin_sha": shas.get(document_id),
        "heading": str(heading or ""),
        "effect": "",
        "risk": "none",
        "risk_note": "",
        "review_status": "unreviewed",
        "retracted": False,
        "history": [{"at": now(), "change": "added", "detail": ""}],
    }
    instance.setdefault("clauses", []).append(clause)
    _ct_version(instance, f"added {clause['clause_id']}")
    _ct_touch(instance, "clause_added", clause["clause_id"])
    return clause


def set_clause(instance: dict, clause_id: str, **fields: Any) -> dict:
    """Set a clause's heading, effect, or risk assessment.

    Effect and risk are reviewer judgment recorded as such;
    setting them never re-verifies the quote — the passage
    binding is immutable once added.
    """
    clause = ct_get_clause(instance, clause_id)
    if clause.get("retracted"):
        raise CaseError(f"{clause_id} is retracted")
    changed = []
    for key in ("heading", "effect", "risk_note"):
        if key in fields and fields[key] is not None:
            value = str(fields[key])
            if value != clause.get(key):
                clause[key] = value
                changed.append(key)
    if "risk" in fields and fields["risk"] is not None:
        value = str(fields["risk"])
        if value not in CT_RISKS:
            raise CaseError(f"risk must be one of: {', '.join(CT_RISKS)}")
        if value != clause.get("risk"):
            clause["risk"] = value
            changed.append("risk")
    if not changed:
        return clause
    clause["history"].append(
        {"at": now(), "change": "set", "detail": ", ".join(changed)}
    )
    _ct_version(instance, f"set {clause_id}")
    _ct_touch(instance, "clause_set", clause_id)
    return clause


def flag_clause(
    instance: dict, clause_id: str, review_status: str, note: str = ""
) -> dict:
    """Mark a clause confirmed or flagged (unreviewed is the default)."""
    clause = ct_get_clause(instance, clause_id)
    if review_status not in CT_REVIEW:
        raise CaseError(f"review status must be one of: {', '.join(CT_REVIEW)}")
    clause["review_status"] = review_status
    clause["history"].append(
        {"at": now(), "change": "flagged", "detail": f"{review_status} {note}".strip()}
    )
    _ct_version(instance, f"flagged {clause_id} {review_status}")
    _ct_touch(instance, "clause_flagged", clause_id)
    return clause


def retract_clause(instance: dict, clause_id: str, reason: str) -> dict:
    """Retire a clause with its reason; history keeps it visible."""
    clause = ct_get_clause(instance, clause_id)
    if not str(reason or "").strip():
        raise CaseError("retracting a clause needs its reason")
    clause["retracted"] = True
    clause["history"].append({"at": now(), "change": "retracted", "detail": str(reason)})
    _ct_version(instance, f"retracted {clause_id}")
    _ct_touch(instance, "clause_retracted", clause_id)
    return clause


def clause_status(data: dict, root: Path, clause: dict) -> dict:
    """Live quote/version/permission verdict for one clause.

    Re-runs the deterministic citation check against the pinned
    version: a changed or revoked source stales the clause with
    the check's own reasons.
    """
    checked = audit_citation(
        root,
        {
            "document_id": clause.get("document_id"),
            "page": clause.get("page"),
            "quote": clause.get("quote"),
            "pin_sha": clause.get("pin_sha"),
        },
        _inbox_shas(root),
        _flagged(data),
    )
    outcome = _structural_outcome([checked])
    reasons = list(checked["problems"])
    if outcome == "passed":
        return {"stale": False, "outcome": outcome, "reasons": []}
    return {"stale": True, "outcome": outcome, "reasons": reasons}


def ct_summary(data: dict, root: Path, instance: dict) -> dict:
    """Counts plus the exception queue: flagged or high-risk clauses."""
    live = [c for c in instance.get("clauses") or [] if not c.get("retracted")]
    stale = sum(
        1 for c in live if clause_status(data, root, c)["stale"]
    )
    exceptions = [
        {
            "clause_id": c.get("clause_id"),
            "heading": c.get("heading"),
            "risk": c.get("risk"),
            "review_status": c.get("review_status"),
        }
        for c in live
        if c.get("review_status") == "flagged" or c.get("risk") == "high"
    ]
    return {
        "total": len(live),
        "stale": stale,
        "confirmed": sum(1 for c in live if c.get("review_status") == "confirmed"),
        "exceptions": exceptions,
    }


def ct_snapshot(instance: dict, data: dict) -> dict:
    """Content hash a contract review binds (recomputed, never stored)."""
    from quality.records import evidence_fingerprint

    return {
        "content": _stable(instance.get("clauses")),
        "evidence": evidence_fingerprint(data),
        "template_version": instance.get("template_version"),
    }


def ct_diff(instance: dict, old: int, new: int) -> dict:
    """Before/after comparison between two review versions."""
    versions = {v.get("v"): v for v in instance.get("versions") or []}
    if old not in versions or new not in versions:
        raise CaseError(f"versions {old}/{new} are not in this instance")
    before = {c.get("clause_id"): c for c in versions[old].get("clauses") or []}
    after = {c.get("clause_id"): c for c in versions[new].get("clauses") or []}
    changes = []
    for clause_id in sorted(set(before) | set(after)):
        if clause_id not in before:
            changes.append({"clause_id": clause_id, "change": "added"})
            continue
        if clause_id not in after:
            changes.append({"clause_id": clause_id, "change": "removed"})
            continue
        first, second = before[clause_id], after[clause_id]
        for key in (
            "heading", "effect", "risk", "risk_note",
            "review_status", "retracted",
        ):
            if first.get(key) != second.get(key):
                changes.append(
                    {
                        "clause_id": clause_id,
                        "change": key,
                        "before": first.get(key),
                        "after": second.get(key),
                    }
                )
    return {"old": old, "new": new, "changes": changes}


def ct_export(
    instance: dict, data: dict, root: Path, version: int | None = None
) -> dict:
    """Versioned clause report with exceptions, limits, approval scope."""
    from quality.records import evidence_fingerprint

    versions = instance.get("versions") or []
    if version is None:
        clauses = instance.get("clauses") or []
        historical = False
    else:
        match = next((v for v in versions if v.get("v") == version), None)
        if match is None:
            raise CaseError(f"version {version} is not in this instance")
        clauses = match.get("clauses") or []
        historical = True
    out_clauses = []
    for clause in clauses:
        limitations = []
        if not historical:
            status = clause_status(data, root, clause)
            limitations.extend(status["reasons"])
        if clause.get("retracted"):
            limitations.append("retracted")
        elif clause.get("review_status") != "confirmed":
            limitations.append(f"review: {clause.get('review_status', 'unreviewed')}")
        if clause.get("risk") == "high":
            limitations.append(f"high risk: {clause.get('risk_note') or 'no note recorded'}")
        out_clauses.append(
            {
                "clause_id": clause.get("clause_id"),
                "heading": clause.get("heading"),
                "document_id": clause.get("document_id"),
                "page": clause.get("page"),
                "quote": clause.get("quote"),
                "effect": clause.get("effect"),
                "risk": clause.get("risk"),
                "risk_note": clause.get("risk_note"),
                "review_status": clause.get("review_status"),
                "retracted": bool(clause.get("retracted")),
                "limitations": limitations,
            }
        )
    summary = {} if historical else ct_summary(data, root, instance)
    review = instance.get("review") or {}
    return {
        "template": instance.get("template"),
        "template_version": instance.get("template_version"),
        "instance": instance.get("id"),
        "matter": instance.get("matter"),
        "title": instance.get("title"),
        "exported_version": version or (versions[-1].get("v") if versions else 1),
        "historical": historical,
        "evidence_fingerprint": evidence_fingerprint(data) if not historical else None,
        "summary": summary,
        "clauses": out_clauses,
        "review": {
            "decision": review.get("decision"),
            "notes": review.get("notes"),
            "qualifications": review.get("qualifications"),
            "at": review.get("at"),
            "snapshot": review.get("snapshot"),
            "scope": (
                "the reviewed snapshot only; later revisions need a new review"
                if review.get("decision") == "approved"
                else "no approval recorded"
            ),
        },
        "limits": [
            "synthetic demonstration: effects and risks are reviewer "
            "judgment, not legal advice",
            "a matching quote establishes the words, never that the "
            "reviewer's reading follows from them",
        ]
        + (["historical version: liveness was not recomputed"] if historical else []),
    }


register(
    TEMPLATE_CT,
    snapshot=ct_snapshot,
    open=open_ct_instance,
    diff=ct_diff,
    export=ct_export,
    export_takes_root=True,
    count_key="clauses",
    review_keys=('clauses',),
)

__all__ = ['CT_RISKS', 'CT_REVIEW', 'open_ct_instance', 'ct_get_clause', '_ct_version', '_ct_touch', '_next_clause_id', 'add_clause', 'set_clause', 'flag_clause', 'retract_clause', 'clause_status', 'ct_summary', 'ct_snapshot', 'ct_diff', 'ct_export']
