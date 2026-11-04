"""Structured review labels: version-bound judgments over audit rows.

A citation mark is an annotation; a label is a reviewed judgment.
Labels bind to the exact row assertion, citation snapshot, and
instance version seen by the reviewer, carry a typed origin and
exposure, and are append-only: assisted work is never reworded
into independent evidence after the fact.

Origins: deterministic / model / human / simulated. Simulated
actors are test fixtures and must be named ``sim:*``; a human
origin with a ``sim:`` actor (or vice versa) is refused, so
simulated records can never pass as genuine human review.

Exposure: blinded (system verdict, candidate identity, prior
labels, and adjudicated answer hidden server-side until the
initial judgment is submitted), assisted (reviewer saw the
system's assessment — valuable feedback, not an independent
label), or unknown (legacy/unrecorded).

This module is storage-agnostic: it reads and writes plain
instance dicts. Only trusted paths (CLI scripts, UI handlers)
may call it — runtime tools must never submit labels or
adjudications; see the boundary test.
"""

from __future__ import annotations

import secrets

from agent_kit.case_record import CaseError
from agent_kit.progress import now

VERDICTS = ("supported", "contradicted", "insufficient")
ORIGINS = ("deterministic", "model", "human", "simulated")
EXPOSURES = ("blinded", "assisted", "unknown")
DECISIONS = ("upheld", "corrected", "unresolved")

__all__ = [
    "VERDICTS",
    "ORIGINS",
    "EXPOSURES",
    "DECISIONS",
    "record_label",
    "adjudicate",
    "label_status",
    "label_fresh",
    "blinded_assignment",
    "issue_assignment",
    "submit_assignment_label",
]


def _row(instance: dict, row_id: str) -> dict:
    row = next(
        (r for r in instance.get("rows") or []
         if isinstance(r, dict) and r.get("row_id") == row_id),
        None,
    )
    if row is None:
        raise CaseError(f"unknown audit row {row_id!r}")
    return row


def _snapshot_citations(row: dict) -> list:
    out = []
    for cite in row.get("citations") or []:
        if not isinstance(cite, dict):
            continue
        out.append({
            "document_id": cite.get("document_id"),
            "page": cite.get("page"),
            "quote": cite.get("quote"),
        })
    return out


def _check_actor(origin: str, actor: str) -> str:
    name = str(actor or "").strip()
    if not name:
        raise CaseError("reviewer actor is required")
    simulated = name.startswith("sim:")
    if origin == "simulated" and not simulated:
        raise CaseError("simulated origin requires a sim: actor")
    if origin == "human" and simulated:
        raise CaseError("human origin forbids a sim: actor")
    return name


def record_label(
    instance: dict,
    *,
    row_id: str,
    verdict: str,
    rationale: str,
    actor: str,
    origin: str,
    exposure: str,
    missing_evidence: bool = False,
    memo_use: dict | None = None,
) -> dict:
    """Append one reviewed judgment bound to the current row state.

    Raises CaseError on unknown rows, bad enums, empty rationale
    or actor, origin/actor mismatch, or a malformed memo-use
    decision. Never edits an existing label.
    """
    if verdict not in VERDICTS:
        raise CaseError(
            f"verdict must be one of {', '.join(VERDICTS)}")
    if origin not in ORIGINS:
        raise CaseError(
            f"origin must be one of {', '.join(ORIGINS)}")
    if exposure not in EXPOSURES:
        raise CaseError(
            f"exposure must be one of {', '.join(EXPOSURES)}")
    if not str(rationale or "").strip():
        raise CaseError("label rationale is required")
    name = _check_actor(origin, actor)
    use = None
    if memo_use is not None:
        if not isinstance(memo_use, dict):
            raise CaseError("memo_use must be a mapping")
        decision = memo_use.get("decision")
        if decision not in ("accept", "conditional", "reject"):
            raise CaseError(
                "memo_use decision must be accept, conditional, or reject")
        use = {
            "decision": decision,
            "qualifications": str(memo_use.get("qualifications") or ""),
        }
    row = _row(instance, row_id)
    labels = instance.setdefault("review_labels", [])
    label = {
        "id": f"L{len(labels) + 1}",
        "row_id": row_id,
        "verdict": verdict,
        "rationale": str(rationale).strip(),
        "actor": name,
        "origin": origin,
        "exposure": exposure,
        "missing_evidence": bool(missing_evidence),
        "memo_use": use,
        "claim": str(row.get("assertion") or ""),
        "citations": _snapshot_citations(row),
        "instance_version": len(instance.get("versions") or []),
        "at": now(),
    }
    labels.append(label)
    return label


def adjudicate(
    instance: dict,
    *,
    row_id: str,
    decision: str,
    rationale: str,
    actor: str,
) -> dict:
    """Record the adjudication state for one row's labels.

    Conflicting labels stay unresolved until this is called; the
    call references the labels it considered and never rewrites
    them. Adjudication itself is a trusted-path action.
    """
    if decision not in DECISIONS:
        raise CaseError(
            f"decision must be one of {', '.join(DECISIONS)}")
    if not str(rationale or "").strip():
        raise CaseError("adjudication rationale is required")
    name = str(actor or "").strip()
    if not name:
        raise CaseError("adjudicator actor is required")
    _row(instance, row_id)
    considered = [
        label["id"] for label in instance.get("review_labels") or []
        if isinstance(label, dict) and label.get("row_id") == row_id
    ]
    if not considered:
        raise CaseError(f"no labels to adjudicate for {row_id!r}")
    entries = instance.setdefault("adjudications", [])
    entry = {
        "row_id": row_id,
        "decision": decision,
        "rationale": str(rationale).strip(),
        "actor": name,
        "labels": considered,
        "at": now(),
    }
    entries.append(entry)
    return entry


def label_status(instance: dict, row_id: str) -> dict:
    """Labels, verdicts, adjudication, and aggregate state for a row."""
    labels = [
        label for label in instance.get("review_labels") or []
        if isinstance(label, dict) and label.get("row_id") == row_id
    ]
    verdicts = sorted({label.get("verdict") for label in labels})
    adjudication = next(
        (entry for entry in reversed(instance.get("adjudications") or [])
         if isinstance(entry, dict) and entry.get("row_id") == row_id),
        None,
    )
    if not labels:
        state = "pending"
    elif adjudication is not None:
        state = "adjudicated"
    elif len(verdicts) > 1:
        state = "unresolved"
    else:
        state = "submitted"
    return {
        "labels": labels,
        "verdicts": verdicts,
        "adjudication": adjudication,
        "state": state,
    }


def label_fresh(label: dict, instance: dict, row: dict) -> bool:
    """True when the row still matches what the label bound to."""
    if not isinstance(label, dict) or not isinstance(row, dict):
        return False
    if label.get("row_id") != row.get("row_id"):
        return False
    if label.get("claim") != str(row.get("assertion") or ""):
        return False
    if label.get("citations") != _snapshot_citations(row):
        return False
    return label.get("instance_version") == len(instance.get("versions") or [])


def blinded_assignment(instance: dict, data: dict, row_id: str) -> dict:
    """Claim plus allowed evidence for blinded review, nothing else.

    Server-side strip: no system verdicts, check outcomes, prior
    labels, adjudications, panel state, or candidate identity.
    The reviewer judges the claim against the evidence shown.
    """
    row = _row(instance, row_id)
    cites = []
    for cite in _snapshot_citations(row):
        cites.append({
            "document_id": cite["document_id"],
            "page": cite["page"],
            "quote": cite["quote"],
        })
    void = not isinstance(data, dict)
    return {
        "row_id": row_id,
        "claim": str(row.get("assertion") or ""),
        "citations": cites,
        "evidence_snapshot": None if void else "bound",
    }


def issue_assignment(instance: dict, data: dict, row_id: str) -> dict:
    """One single-use blinded assignment for a row (caller saves).

    Returns the stripped assignment plus its token. The token
    binds the row and instance version; submitting after any
    revision is refused, so the label always matches the state
    the reviewer actually saw.
    """
    _row(instance, row_id)
    token = secrets.token_hex(16)
    store = instance.setdefault("review_assignments", {})
    store[token] = {
        "row_id": row_id,
        "instance_version": len(instance.get("versions") or []),
        "at": now(),
        "used": False,
    }
    assignment = blinded_assignment(instance, data, row_id)
    assignment["token"] = token
    return assignment


def submit_assignment_label(
    instance: dict,
    *,
    token: str,
    verdict: str,
    rationale: str,
    actor: str,
    origin: str,
    missing_evidence: bool = False,
) -> dict:
    """Record the initial judgment for an assignment (caller saves).

    Single-use: unknown, reused, or version-stale tokens are
    refused. The label is always exposure blinded; origin and
    actor follow the usual simulated/human rule.
    """
    entry = (instance.get("review_assignments") or {}).get(token or "")
    if not isinstance(entry, dict) or entry.get("used"):
        raise CaseError("assignment token is unknown or already used")
    if entry.get("instance_version") != len(instance.get("versions") or []):
        raise CaseError(
            "the row changed since this assignment was issued; "
            "request a new one")
    label = record_label(
        instance, row_id=entry["row_id"], verdict=verdict,
        rationale=rationale, actor=actor, origin=origin,
        exposure="blinded", missing_evidence=missing_evidence)
    entry["used"] = True
    entry["label"] = label["id"]
    return label
