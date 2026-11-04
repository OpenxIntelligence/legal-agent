"""discovery request/response log (one artifact slice; split from artifacts.py, behavior unchanged)."""

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
from .matrix import _valid_date


DR_DIRECTIONS = ("outgoing", "incoming")
DR_CATEGORIES = ("documents", "interrogatory", "admission", "inspection", "other")
DR_STATUS = ("draft", "served", "responded", "complete", "objected", "withdrawn")
_DR_NEXT = {
    "draft": "serve",
    "served": "respond or object",
    "responded": "complete",
    "objected": "resolve objection",
}
_DR_DECISIONS = {
    "draft": ("withdrawn",),
    "served": ("objected", "withdrawn"),
    "responded": ("complete", "withdrawn"),
    "objected": ("served", "withdrawn"),
    "complete": ("responded",),
    "withdrawn": ("draft",),
}


def open_dr_instance(
    data: dict, run_id: str, title: str, source_id: str = ""
) -> dict:
    """Create a discovery log (pure; caller saves).

    No prerequisites: the log precedes production, so an
    empty matter can already draft requests.
    """
    instance = {
        "id": _next_instance_id(data),
        "template": TEMPLATE_DR,
        "template_version": TEMPLATE_DR_VERSION,
        "matter": run_id,
        "title": title or "Discovery Request / Response Log",
        "owner": "local",
        "viewers": ["local"],
        "created_at": now(),
        "requests": [],
        "responses": [],
        "versions": [],
        "review": _blank_review(),
        "history": [{"at": now(), "event": "opened", "detail": ""}],
    }
    instance["versions"].append(
        {"v": 1, "at": now(), "reason": "opened", "requests": [], "responses": []}
    )
    return instance


def dr_get_request(instance: dict, rq_id: str) -> dict:
    request = next(
        (r for r in instance.get("requests") or [] if r.get("rq_id") == rq_id),
        None,
    )
    if request is None:
        raise CaseError(f"unknown discovery request {rq_id!r}")
    return request


def dr_get_response(instance: dict, rs_id: str) -> dict:
    response = next(
        (r for r in instance.get("responses") or [] if r.get("rs_id") == rs_id),
        None,
    )
    if response is None:
        raise CaseError(f"unknown response {rs_id!r}")
    return response


def _dr_version(instance: dict, reason: str) -> None:
    versions = instance.setdefault("versions", [])
    versions.append(
        {
            "v": len(versions) + 1,
            "at": now(),
            "reason": reason,
            "requests": copy.deepcopy(instance.get("requests")),
            "responses": copy.deepcopy(instance.get("responses")),
        }
    )


def _dr_touch(instance: dict, event: str, detail: str) -> None:
    instance["history"].append({"at": now(), "event": event, "detail": detail})
    instance["review"] = _blank_review()


def _next_rq_id(instance: dict) -> str:
    taken = {r.get("rq_id", "") for r in instance.get("requests") or []}
    seq = 1
    while f"RQ{seq}" in taken:
        seq += 1
    return f"RQ{seq}"


def _next_rs_id(instance: dict) -> str:
    taken = {r.get("rs_id", "") for r in instance.get("responses") or []}
    seq = 1
    while f"RS{seq}" in taken:
        seq += 1
    return f"RS{seq}"


def add_rq(
    instance: dict,
    title: str,
    description: str,
    direction: str,
    category: str,
    due_date: str = "",
    counterpart: str = "",
) -> dict:
    """Draft one discovery request."""
    if not str(title or "").strip():
        raise CaseError("a request needs its title")
    if not str(description or "").strip():
        raise CaseError("a request needs its description")
    if direction not in DR_DIRECTIONS:
        raise CaseError(f"direction must be one of: {', '.join(DR_DIRECTIONS)}")
    if category not in DR_CATEGORIES:
        raise CaseError(f"category must be one of: {', '.join(DR_CATEGORIES)}")
    due = str(due_date or "").strip()
    if due and not _valid_date(due):
        raise CaseError(f"{due!r} is not a YYYY-MM-DD date")
    request = {
        "rq_id": _next_rq_id(instance),
        "title": str(title),
        "description": str(description),
        "direction": direction,
        "category": category,
        "due_date": due,
        "counterpart": str(counterpart or ""),
        "status": "draft",
        "rationale": "",
        "history": [{"at": now(), "change": "added", "detail": ""}],
    }
    instance.setdefault("requests", []).append(request)
    _dr_version(instance, f"added {request['rq_id']}")
    _dr_touch(instance, "request_added", request["rq_id"])
    return request


def set_rq(instance: dict, rq_id: str, **fields: Any) -> dict:
    """Revise a request that is not finished."""
    request = dr_get_request(instance, rq_id)
    if request.get("status") in ("complete", "withdrawn"):
        raise CaseError(f"{rq_id} is {request['status']}; reopen it first")
    changed = []
    for key in ("title", "description", "counterpart"):
        if key in fields and fields[key] is not None:
            value = str(fields[key])
            if key in ("title", "description") and not value.strip():
                raise CaseError(f"a request needs its {key}")
            if value != request.get(key):
                request[key] = value
                changed.append(key)
    if "direction" in fields and fields["direction"] is not None:
        value = str(fields["direction"])
        if value not in DR_DIRECTIONS:
            raise CaseError(f"direction must be one of: {', '.join(DR_DIRECTIONS)}")
        if value != request.get("direction"):
            request["direction"] = value
            changed.append("direction")
    if "category" in fields and fields["category"] is not None:
        value = str(fields["category"])
        if value not in DR_CATEGORIES:
            raise CaseError(f"category must be one of: {', '.join(DR_CATEGORIES)}")
        if value != request.get("category"):
            request["category"] = value
            changed.append("category")
    if "due_date" in fields and fields["due_date"] is not None:
        value = str(fields["due_date"]).strip()
        if value and not _valid_date(value):
            raise CaseError(f"{value!r} is not a YYYY-MM-DD date")
        if value != request.get("due_date"):
            request["due_date"] = value
            changed.append("due_date")
    if not changed:
        return request
    request["history"].append(
        {"at": now(), "change": "set", "detail": ", ".join(changed)}
    )
    _dr_version(instance, f"set {rq_id}")
    _dr_touch(instance, "request_set", rq_id)
    return request


def serve_rq(instance: dict, rq_id: str) -> dict:
    """Serve a drafted request."""
    request = dr_get_request(instance, rq_id)
    if request.get("status") != "draft":
        raise CaseError(f"{rq_id} is {request.get('status')}, not draft")
    request["status"] = "served"
    request["history"].append({"at": now(), "change": "served", "detail": ""})
    _dr_version(instance, f"served {rq_id}")
    _dr_touch(instance, "request_served", rq_id)
    return request


def respond_rq(
    instance: dict,
    data: dict,
    root: Path,
    rq_id: str,
    received_date: str,
    summary: str,
    documents: list[str] | None = None,
    notes: str = "",
) -> dict:
    """Log one response with its produced documents pinned.

    The request must be served or already responded; every
    produced document must exist in the inbox when logged.
    """
    request = dr_get_request(instance, rq_id)
    if request.get("status") not in ("served", "responded"):
        raise CaseError(
            f"{rq_id} is {request.get('status')}; only served or "
            "responded requests can take a response"
        )
    received = str(received_date or "").strip()
    if not received or not _valid_date(received):
        raise CaseError(f"{received or 'empty date'!r} is not a YYYY-MM-DD date")
    if not str(summary or "").strip():
        raise CaseError("a response needs its summary")
    docs = [str(d).strip() for d in (documents or []) if str(d).strip()]
    shas = _inbox_shas(root)
    missing = [d for d in docs if d not in shas]
    if missing:
        raise CaseError(
            "produced documents not in the inbox: " + ", ".join(sorted(missing))
        )
    response = {
        "rs_id": _next_rs_id(instance),
        "rq_id": rq_id,
        "received_date": received,
        "summary": str(summary),
        "documents": docs,
        "doc_pins": {d: shas[d] for d in docs},
        "notes": str(notes or ""),
        "history": [{"at": now(), "change": "logged", "detail": ""}],
    }
    instance.setdefault("responses", []).append(response)
    request["status"] = "responded"
    request["history"].append(
        {"at": now(), "change": "responded", "detail": response["rs_id"]}
    )
    _dr_version(instance, f"logged {response['rs_id']}")
    _dr_touch(instance, "response_logged", response["rs_id"])
    return response


def decide_dr(instance: dict, rq_id: str, status: str, reason: str) -> dict:
    """Complete, object, withdraw, or reopen a request with its reason.

    Reopening takes one step back: complete returns to
    responded, withdrawn returns to draft, an objection
    returns to served.
    """
    request = dr_get_request(instance, rq_id)
    current = request.get("status")
    allowed = _DR_DECISIONS.get(current, ())
    if status not in allowed:
        raise CaseError(
            f"{rq_id} is {current}; allowed from here: "
            + (", ".join(allowed) or "nothing")
        )
    if not str(reason or "").strip():
        raise CaseError(f"marking {status} needs its reason")
    request["status"] = status
    request["rationale"] = str(reason)
    request["history"].append(
        {"at": now(), "change": "decided", "detail": f"{status} {reason}".strip()}
    )
    _dr_version(instance, f"decided {rq_id} {status}")
    _dr_touch(instance, "request_decided", rq_id)
    return request


def rs_status(data: dict, root: Path, response: dict) -> dict:
    """Whether a response's produced documents changed or vanished."""
    shas = _inbox_shas(root)
    reasons = []
    for doc in response.get("documents") or []:
        if doc not in shas:
            reasons.append(f"produced document {doc} is gone")
        elif shas[doc] != (response.get("doc_pins") or {}).get(doc):
            reasons.append(f"produced document {doc} changed")
    return {"stale": bool(reasons), "reasons": reasons}


def rq_status(instance: dict, data: dict, root: Path, request: dict) -> dict:
    """Whether any of a request's responses needs a re-check."""
    reasons = []
    for response in instance.get("responses") or []:
        if response.get("rq_id") != request.get("rq_id"):
            continue
        status = rs_status(data, root, response)
        reasons.extend(
            f"{response.get('rs_id')}: {reason}" for reason in status["reasons"]
        )
    return {"stale": bool(reasons), "orphaned": False, "reasons": reasons}


def dr_summary(instance: dict, data: dict, root: Path) -> dict:
    """Counts plus the queue with each item's next action."""
    requests = instance.get("requests") or []
    queue = [
        {
            "rq_id": r.get("rq_id"),
            "title": r.get("title"),
            "direction": r.get("direction"),
            "status": r.get("status"),
            "due_date": r.get("due_date"),
            "next": _DR_NEXT.get(r.get("status"), ""),
        }
        for r in sorted(requests, key=lambda r: r.get("rq_id") or "")
        if r.get("status") in _DR_NEXT
    ]
    stale = sum(
        1 for r in requests if rq_status(instance, data, root, r)["stale"]
    )
    by_status = {name: 0 for name in DR_STATUS}
    for r in requests:
        if r.get("status") in by_status:
            by_status[r["status"]] += 1
    return {
        "total": len(requests),
        "responses": len(instance.get("responses") or []),
        "by_status": by_status,
        "stale": stale,
        "queue": queue,
    }


def dr_snapshot(instance: dict, data: dict) -> dict:
    """Content hash a discovery review binds (recomputed, never stored)."""
    from quality.records import evidence_fingerprint

    return {
        "content": _stable(
            {
                "requests": instance.get("requests"),
                "responses": instance.get("responses"),
            }
        ),
        "evidence": evidence_fingerprint(data),
        "template_version": instance.get("template_version"),
    }


def dr_diff(instance: dict, old: int, new: int) -> dict:
    """Before/after comparison between two log versions."""
    versions = {v.get("v"): v for v in instance.get("versions") or []}
    if old not in versions or new not in versions:
        raise CaseError(f"versions {old}/{new} are not in this instance")
    changes = []
    before_rq = {r.get("rq_id"): r for r in versions[old].get("requests") or []}
    after_rq = {r.get("rq_id"): r for r in versions[new].get("requests") or []}
    for rq_id in sorted(set(before_rq) | set(after_rq)):
        if rq_id not in before_rq:
            changes.append({"rq_id": rq_id, "change": "added"})
            continue
        if rq_id not in after_rq:
            changes.append({"rq_id": rq_id, "change": "removed"})
            continue
        first, second = before_rq[rq_id], after_rq[rq_id]
        for key in (
            "title", "description", "direction", "category", "due_date",
            "counterpart", "status", "rationale",
        ):
            if first.get(key) != second.get(key):
                changes.append(
                    {
                        "rq_id": rq_id,
                        "change": key,
                        "before": first.get(key),
                        "after": second.get(key),
                    }
                )
    before_rs = {r.get("rs_id"): r for r in versions[old].get("responses") or []}
    after_rs = {r.get("rs_id"): r for r in versions[new].get("responses") or []}
    for rs_id in sorted(set(before_rs) | set(after_rs)):
        if rs_id not in before_rs:
            changes.append({"rs_id": rs_id, "change": "added"})
            continue
        if rs_id not in after_rs:
            changes.append({"rs_id": rs_id, "change": "removed"})
    return {"old": old, "new": new, "changes": changes}


def dr_export(
    instance: dict, data: dict, root: Path, version: int | None = None
) -> dict:
    """Versioned discovery report with queue, limits, approval scope."""
    from quality.records import evidence_fingerprint

    versions = instance.get("versions") or []
    if version is None:
        requests = instance.get("requests") or []
        responses = instance.get("responses") or []
        historical = False
    else:
        match = next((v for v in versions if v.get("v") == version), None)
        if match is None:
            raise CaseError(f"version {version} is not in this instance")
        requests = match.get("requests") or []
        responses = match.get("responses") or []
        historical = True
    out_responses = []
    for response in responses:
        limitations = []
        if not historical:
            limitations.extend(rs_status(data, root, response)["reasons"])
        out_responses.append(
            {
                "rs_id": response.get("rs_id"),
                "rq_id": response.get("rq_id"),
                "received_date": response.get("received_date"),
                "summary": response.get("summary"),
                "documents": response.get("documents"),
                "notes": response.get("notes"),
                "limitations": limitations,
            }
        )
    out_requests = []
    for request in requests:
        limitations = []
        if not historical:
            limitations.extend(
                rq_status(instance, data, root, request)["reasons"]
            )
        if request.get("status") in _DR_NEXT:
            limitations.append(
                f"status: {request.get('status')} "
                f"(next: {_DR_NEXT[request['status']]})"
            )
        out_requests.append(
            {
                "rq_id": request.get("rq_id"),
                "title": request.get("title"),
                "description": request.get("description"),
                "direction": request.get("direction"),
                "category": request.get("category"),
                "due_date": request.get("due_date"),
                "counterpart": request.get("counterpart"),
                "status": request.get("status"),
                "rationale": request.get("rationale"),
                "limitations": limitations,
            }
        )
    summary = {} if historical else dr_summary(instance, data, root)
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
        "requests": out_requests,
        "responses": out_responses,
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
            "synthetic demonstration: requests, responses, and "
            "productions are fixture records",
            "a logged response records what was received and which "
            "files were pinned, never automatic completeness",
        ]
        + (["historical version: liveness was not recomputed"] if historical else []),
    }


register(
    TEMPLATE_DR,
    snapshot=dr_snapshot,
    open=open_dr_instance,
    diff=dr_diff,
    export=dr_export,
    export_takes_root=True,
    count_key="requests",
    review_keys=('requests', 'responses'),
)

__all__ = ['DR_DIRECTIONS', 'DR_CATEGORIES', 'DR_STATUS', '_DR_NEXT', '_DR_DECISIONS', 'open_dr_instance', 'dr_get_request', 'dr_get_response', '_dr_version', '_dr_touch', '_next_rq_id', '_next_rs_id', 'add_rq', 'set_rq', 'serve_rq', 'respond_rq', 'decide_dr', 'rs_status', 'rq_status', 'dr_summary', 'dr_snapshot', 'dr_diff', 'dr_export']
