"""legal intake & routing (one artifact slice; split from artifacts.py, behavior unchanged)."""

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


IN_CATEGORIES = ("contract", "litigation", "regulatory", "employment", "other")
IN_URGENCY = ("routine", "expedited", "critical")
IN_STATUS = ("new", "triaged", "routed", "declined", "closed")
_IN_URGENCY_ORDER = {"critical": 0, "expedited": 1, "routine": 2}


def open_in_instance(
    data: dict, run_id: str, title: str, source_id: str = ""
) -> dict:
    """Create an intake queue (pure; caller saves).

    No prerequisites: intake precedes investigation, so an
    empty matter can already take requests.
    """
    instance = {
        "id": _next_instance_id(data),
        "template": TEMPLATE_IN,
        "template_version": TEMPLATE_IN_VERSION,
        "matter": run_id,
        "title": title or "Legal Intake & Routing",
        "owner": "local",
        "viewers": ["local"],
        "created_at": now(),
        "intakes": [],
        "versions": [],
        "review": _blank_review(),
        "history": [{"at": now(), "event": "opened", "detail": ""}],
    }
    instance["versions"].append(
        {"v": 1, "at": now(), "reason": "opened", "intakes": []}
    )
    return instance


def in_get_request(instance: dict, in_id: str) -> dict:
    request = next(
        (r for r in instance.get("intakes") or [] if r.get("in_id") == in_id),
        None,
    )
    if request is None:
        raise CaseError(f"unknown intake request {in_id!r}")
    return request


def _in_version(instance: dict, reason: str) -> None:
    versions = instance.setdefault("versions", [])
    versions.append(
        {
            "v": len(versions) + 1,
            "at": now(),
            "reason": reason,
            "intakes": copy.deepcopy(instance.get("intakes")),
        }
    )


def _in_touch(instance: dict, event: str, detail: str) -> None:
    instance["history"].append({"at": now(), "event": event, "detail": detail})
    instance["review"] = _blank_review()


def _next_in_id(instance: dict) -> str:
    taken = {r.get("in_id", "") for r in instance.get("intakes") or []}
    seq = 1
    while f"IN{seq}" in taken:
        seq += 1
    return f"IN{seq}"


def _checked_issue(data: dict, issue_id: str) -> str:
    issue_id = str(issue_id or "").strip()
    if not issue_id:
        return ""
    checklist = data.get("checklist", {}) or {}
    if issue_id not in checklist or not isinstance(checklist[issue_id], dict):
        raise CaseError(f"issue {issue_id!r} is not on the checklist")
    return issue_id


def _issue_pin(data: dict, issue_id: str) -> str | None:
    if not issue_id:
        return None
    entry = (data.get("checklist", {}) or {}).get(issue_id) or {}
    return _stable(
        {"question": entry.get("question"), "status": entry.get("status")}
    )


def add_in(
    instance: dict,
    data: dict,
    title: str,
    description: str,
    category: str,
    urgency: str,
    requester: str = "",
    issue_id: str = "",
) -> dict:
    """Add one intake request, optionally linked to an issue."""
    if not str(title or "").strip():
        raise CaseError("a request needs its title")
    if not str(description or "").strip():
        raise CaseError("a request needs its description")
    if category not in IN_CATEGORIES:
        raise CaseError(f"category must be one of: {', '.join(IN_CATEGORIES)}")
    if urgency not in IN_URGENCY:
        raise CaseError(f"urgency must be one of: {', '.join(IN_URGENCY)}")
    issue = _checked_issue(data, issue_id)
    request = {
        "in_id": _next_in_id(instance),
        "title": str(title),
        "description": str(description),
        "category": category,
        "urgency": urgency,
        "requester": str(requester or ""),
        "assignee": "",
        "issue_id": issue,
        "issue_pin": _issue_pin(data, issue),
        "route": "",
        "rationale": "",
        "status": "new",
        "history": [{"at": now(), "change": "added", "detail": ""}],
    }
    instance.setdefault("intakes", []).append(request)
    _in_version(instance, f"added {request['in_id']}")
    _in_touch(instance, "intake_added", request["in_id"])
    return request


def set_in(instance: dict, data: dict, in_id: str, **fields: Any) -> dict:
    """Revise a request's text, classification, owner, or issue link.

    Saving re-pins the linked issue: the reviewer looked
    again, so a prior revision staleness clears with its
    reason recorded in history.
    """
    request = in_get_request(instance, in_id)
    if request.get("status") in ("declined", "closed"):
        raise CaseError(f"{in_id} is {request['status']}; reopen it first")
    changed = []
    for key in ("title", "description", "requester", "assignee"):
        if key in fields and fields[key] is not None:
            value = str(fields[key])
            if key in ("title", "description") and not value.strip():
                raise CaseError(f"a request needs its {key}")
            if value != request.get(key):
                request[key] = value
                changed.append(key)
    if "category" in fields and fields["category"] is not None:
        value = str(fields["category"])
        if value not in IN_CATEGORIES:
            raise CaseError(f"category must be one of: {', '.join(IN_CATEGORIES)}")
        if value != request.get("category"):
            request["category"] = value
            changed.append("category")
    if "urgency" in fields and fields["urgency"] is not None:
        value = str(fields["urgency"])
        if value not in IN_URGENCY:
            raise CaseError(f"urgency must be one of: {', '.join(IN_URGENCY)}")
        if value != request.get("urgency"):
            request["urgency"] = value
            changed.append("urgency")
    if "issue_id" in fields and fields["issue_id"] is not None:
        value = _checked_issue(data, fields["issue_id"])
        if value != request.get("issue_id"):
            request["issue_id"] = value
            changed.append("issue_id")
    if not changed:
        return request
    request["issue_pin"] = _issue_pin(data, request.get("issue_id") or "")
    changed.append("rebound")
    request["history"].append(
        {"at": now(), "change": "set", "detail": ", ".join(changed)}
    )
    _in_version(instance, f"set {in_id}")
    _in_touch(instance, "intake_set", in_id)
    return request


def triage_in(instance: dict, in_id: str, assignee: str) -> dict:
    """Confirm a new request and give it an owner."""
    request = in_get_request(instance, in_id)
    if request.get("status") != "new":
        raise CaseError(f"{in_id} is {request.get('status')}, not new")
    if not str(assignee or "").strip():
        raise CaseError("triaging needs an assignee")
    request["assignee"] = str(assignee)
    request["status"] = "triaged"
    request["history"].append(
        {"at": now(), "change": "triaged", "detail": str(assignee)}
    )
    _in_version(instance, f"triaged {in_id}")
    _in_touch(instance, "intake_triaged", in_id)
    return request


def route_in(instance: dict, in_id: str, route: str, rationale: str) -> dict:
    """Route a new or triaged request with its rationale."""
    request = in_get_request(instance, in_id)
    if request.get("status") not in ("new", "triaged"):
        raise CaseError(
            f"{in_id} is {request.get('status')}; only new or triaged "
            "requests can be routed"
        )
    if not str(route or "").strip():
        raise CaseError("routing needs its destination")
    if not str(rationale or "").strip():
        raise CaseError("routing needs its rationale")
    request["route"] = str(route)
    request["rationale"] = str(rationale)
    request["status"] = "routed"
    request["history"].append(
        {"at": now(), "change": "routed", "detail": str(route)}
    )
    _in_version(instance, f"routed {in_id}")
    _in_touch(instance, "intake_routed", in_id)
    return request


_IN_DECISIONS = {
    "new": ("declined",),
    "triaged": ("declined",),
    "routed": ("closed",),
    "declined": ("new",),
    "closed": ("routed",),
}


def decide_in(instance: dict, in_id: str, status: str, reason: str) -> dict:
    """Decline, close, or reopen a request with its reason.

    Reopening returns a declined request to new or a closed
    one to routed — the smallest step back, never a jump.
    """
    request = in_get_request(instance, in_id)
    current = request.get("status")
    allowed = _IN_DECISIONS.get(current, ())
    if status not in allowed:
        raise CaseError(
            f"{in_id} is {current}; allowed from here: "
            + (", ".join(allowed) or "nothing")
        )
    if not str(reason or "").strip():
        raise CaseError(f"marking {status} needs its reason")
    request["status"] = status
    request["rationale"] = str(reason)
    request["history"].append(
        {"at": now(), "change": "decided", "detail": f"{status} {reason}".strip()}
    )
    _in_version(instance, f"decided {in_id} {status}")
    _in_touch(instance, "intake_decided", in_id)
    return request


def in_status(instance: dict, data: dict, request: dict) -> dict:
    """Whether a request's linked issue changed or vanished."""
    issue_id = request.get("issue_id") or ""
    if not issue_id:
        return {"stale": False, "orphaned": False, "reasons": []}
    entry = (data.get("checklist", {}) or {}).get(issue_id)
    if not isinstance(entry, dict):
        return {
            "stale": False,
            "orphaned": True,
            "reasons": ["the linked issue was removed"],
        }
    if _issue_pin(data, issue_id) != request.get("issue_pin"):
        return {
            "stale": True,
            "orphaned": False,
            "reasons": ["the linked issue was revised"],
        }
    return {"stale": False, "orphaned": False, "reasons": []}


def in_summary(instance: dict, data: dict) -> dict:
    """Counts plus the queue: new and triaged, urgent first."""
    requests = instance.get("intakes") or []
    queue = sorted(
        (r for r in requests if r.get("status") in ("new", "triaged")),
        key=lambda r: (
            _IN_URGENCY_ORDER.get(r.get("urgency"), 9),
            r.get("in_id"),
        ),
    )
    stale = sum(1 for r in requests if in_status(instance, data, r)["stale"])
    orphaned = sum(
        1 for r in requests if in_status(instance, data, r)["orphaned"]
    )
    by_status = {name: 0 for name in IN_STATUS}
    for r in requests:
        if r.get("status") in by_status:
            by_status[r["status"]] += 1
    return {
        "total": len(requests),
        "by_status": by_status,
        "stale": stale,
        "orphaned": orphaned,
        "unassigned": sum(
            1 for r in requests if not str(r.get("assignee") or "").strip()
        ),
        "queue": [
            {
                "in_id": r.get("in_id"),
                "title": r.get("title"),
                "category": r.get("category"),
                "urgency": r.get("urgency"),
                "status": r.get("status"),
            }
            for r in queue
        ],
    }


def in_snapshot(instance: dict, data: dict) -> dict:
    """Content hash an intake review binds (recomputed, never stored)."""
    from quality.records import evidence_fingerprint

    return {
        "content": _stable(instance.get("intakes")),
        "evidence": evidence_fingerprint(data),
        "template_version": instance.get("template_version"),
    }


def in_diff(instance: dict, old: int, new: int) -> dict:
    """Before/after comparison between two intake versions."""
    versions = {v.get("v"): v for v in instance.get("versions") or []}
    if old not in versions or new not in versions:
        raise CaseError(f"versions {old}/{new} are not in this instance")
    before = {r.get("in_id"): r for r in versions[old].get("intakes") or []}
    after = {r.get("in_id"): r for r in versions[new].get("intakes") or []}
    changes = []
    for in_id in sorted(set(before) | set(after)):
        if in_id not in before:
            changes.append({"in_id": in_id, "change": "added"})
            continue
        if in_id not in after:
            changes.append({"in_id": in_id, "change": "removed"})
            continue
        first, second = before[in_id], after[in_id]
        for key in (
            "title", "description", "category", "urgency", "requester",
            "assignee", "issue_id", "route", "rationale", "status",
        ):
            if first.get(key) != second.get(key):
                changes.append(
                    {
                        "in_id": in_id,
                        "change": key,
                        "before": first.get(key),
                        "after": second.get(key),
                    }
                )
    return {"old": old, "new": new, "changes": changes}


def in_export(
    instance: dict, data: dict, version: int | None = None
) -> dict:
    """Versioned intake report with queue, limits, approval scope."""
    from quality.records import evidence_fingerprint

    versions = instance.get("versions") or []
    if version is None:
        requests = instance.get("intakes") or []
        historical = False
    else:
        match = next((v for v in versions if v.get("v") == version), None)
        if match is None:
            raise CaseError(f"version {version} is not in this instance")
        requests = match.get("intakes") or []
        historical = True
    checklist = data.get("checklist", {}) or {}
    out_requests = []
    for request in requests:
        limitations = []
        if not historical:
            status = in_status(instance, data, request)
            limitations.extend(status["reasons"])
            if status["orphaned"]:
                limitations.append("orphaned: linked issue removed")
        if request.get("status") in ("new", "triaged"):
            limitations.append(f"status: {request.get('status')} (awaiting routing)")
        entry = checklist.get(request.get("issue_id") or "")
        out_requests.append(
            {
                "in_id": request.get("in_id"),
                "title": request.get("title"),
                "description": request.get("description"),
                "category": request.get("category"),
                "urgency": request.get("urgency"),
                "requester": request.get("requester"),
                "assignee": request.get("assignee"),
                "issue_id": request.get("issue_id"),
                "issue_question": (entry or {}).get("question", "")
                if isinstance(entry, dict)
                else "",
                "route": request.get("route"),
                "rationale": request.get("rationale"),
                "status": request.get("status"),
                "limitations": limitations,
            }
        )
    summary = {} if historical else in_summary(instance, data)
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
        "intakes": out_requests,
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
            "synthetic demonstration: requests, routes, and "
            "decisions are fixture records",
            "routing records the reviewer's call with its rationale, "
            "never automatic assignment",
        ]
        + (["historical version: liveness was not recomputed"] if historical else []),
    }


register(
    TEMPLATE_IN,
    snapshot=in_snapshot,
    open=open_in_instance,
    diff=in_diff,
    export=in_export,
    export_takes_root=False,
    count_key="intakes",
    review_keys=('intakes',),
)

__all__ = ['IN_CATEGORIES', 'IN_URGENCY', 'IN_STATUS', '_IN_URGENCY_ORDER', 'open_in_instance', 'in_get_request', '_in_version', '_in_touch', '_next_in_id', '_checked_issue', '_issue_pin', 'add_in', 'set_in', 'triage_in', 'route_in', '_IN_DECISIONS', 'decide_in', 'in_status', 'in_summary', 'in_snapshot', 'in_diff', 'in_export']
