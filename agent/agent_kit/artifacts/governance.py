"""AI governance / use-case inventory (one artifact slice; split from artifacts.py, behavior unchanged)."""

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


UC_RISK = ("unassessed", "low", "medium", "high")
UC_STATUS = ("proposed", "assessed", "approved", "rejected")
_UC_NEXT = {
    "proposed": "assess",
    "assessed": "approve or reject",
}
_UC_DECISIONS = {
    "proposed": ("assessed",),
    "assessed": ("approved", "rejected", "proposed"),
    "approved": ("proposed",),
    "rejected": ("proposed",),
}


def _gv_source(data: dict, source_id: str) -> dict:
    source = (data.get("artifacts", {}) or {}).get(source_id)
    if not isinstance(source, dict):
        raise CaseError(f"unknown source instance {source_id!r}")
    if source.get("template") != TEMPLATE_IN:
        raise CaseError(
            f"{source_id} is a {source.get('template')}, not a legal intake"
        )
    return source


def open_gv_instance(
    data: dict, run_id: str, title: str, source_id: str = ""
) -> dict:
    """Create a governance inventory over one intake (pure; caller saves).

    Needs a legal-intake instance in the same matter. An
    explicit source wins; otherwise the single intake is
    used, and several require naming one.
    """
    intakes = [
        key
        for key, entry in (data.get("artifacts", {}) or {}).items()
        if isinstance(entry, dict) and entry.get("template") == TEMPLATE_IN
    ]
    if source_id:
        _gv_source(data, source_id)
    elif len(intakes) == 1:
        source_id = intakes[0]
    elif not intakes:
        raise CaseError("the governance inventory needs a legal-intake instance")
    else:
        raise CaseError("several intakes exist; name one with --source")
    instance = {
        "id": _next_instance_id(data),
        "template": TEMPLATE_GV,
        "template_version": TEMPLATE_GV_VERSION,
        "matter": run_id,
        "title": title or "AI Governance / Use-case Inventory",
        "owner": "local",
        "viewers": ["local"],
        "created_at": now(),
        "source_instance": source_id,
        "uses": [],
        "versions": [],
        "review": _blank_review(),
        "history": [{"at": now(), "event": "opened", "detail": source_id}],
    }
    instance["versions"].append(
        {"v": 1, "at": now(), "reason": "opened", "uses": []}
    )
    return instance


def gv_get_use(instance: dict, uc_id: str) -> dict:
    use = next(
        (u for u in instance.get("uses") or [] if u.get("uc_id") == uc_id),
        None,
    )
    if use is None:
        raise CaseError(f"unknown use case {uc_id!r}")
    return use


def _gv_version(instance: dict, reason: str) -> None:
    versions = instance.setdefault("versions", [])
    versions.append(
        {
            "v": len(versions) + 1,
            "at": now(),
            "reason": reason,
            "uses": copy.deepcopy(instance.get("uses")),
        }
    )


def _gv_touch(instance: dict, event: str, detail: str) -> None:
    instance["history"].append({"at": now(), "event": event, "detail": detail})
    instance["review"] = _blank_review()


def _next_uc_id(instance: dict) -> str:
    taken = {u.get("uc_id", "") for u in instance.get("uses") or []}
    seq = 1
    while f"UC{seq}" in taken:
        seq += 1
    return f"UC{seq}"


def _gv_intake(data: dict, instance: dict, in_id: str) -> dict | None:
    if not in_id:
        return None
    source = _gv_source(data, instance.get("source_instance") or "")
    return next(
        (r for r in source.get("intakes") or [] if r.get("in_id") == in_id),
        None,
    )


def _intake_use_pin(request: dict) -> str:
    return _stable(
        {
            "title": request.get("title"),
            "description": request.get("description"),
            "category": request.get("category"),
            "urgency": request.get("urgency"),
            "status": request.get("status"),
            "route": request.get("route"),
        }
    )


def add_uc(
    instance: dict,
    data: dict,
    title: str,
    description: str,
    data_types: str,
    system: str = "",
    owner: str = "",
    in_id: str = "",
) -> dict:
    """Log one AI use case (pure; caller saves).

    Purpose and data are required; risk starts
    unassessed and never defaults. An intake link must
    name a request in the source queue.
    """
    if not str(title or "").strip():
        raise CaseError("a use case needs its title")
    if not str(description or "").strip():
        raise CaseError("a use case needs its purpose")
    if not str(data_types or "").strip():
        raise CaseError("a use case needs its data")
    in_id = str(in_id or "").strip()
    request = _gv_intake(data, instance, in_id)
    if in_id and request is None:
        raise CaseError(f"intake request {in_id!r} is not in the source queue")
    use = {
        "uc_id": _next_uc_id(instance),
        "title": str(title).strip(),
        "description": str(description).strip(),
        "data_types": str(data_types).strip(),
        "system": str(system or "").strip(),
        "risk": "unassessed",
        "owner": str(owner or "").strip(),
        "in_id": in_id,
        "in_pin": _intake_use_pin(request) if request is not None else None,
        "status": "proposed",
        "conditions": "",
        "resolution": "",
        "history": [{"at": now(), "change": "logged", "detail": ""}],
    }
    instance.setdefault("uses", []).append(use)
    _gv_version(instance, f"logged {use['uc_id']}")
    _gv_touch(instance, "use_logged", use["uc_id"])
    return use


def set_uc(
    instance: dict,
    data: dict,
    uc_id: str,
    title: str | None = None,
    description: str | None = None,
    data_types: str | None = None,
    system: str | None = None,
    risk: str | None = None,
    owner: str | None = None,
    conditions: str | None = None,
    in_id: str | None = None,
) -> dict:
    """Revise a use case (pure; caller saves).

    Approved and rejected cases are locked: reopen them
    first. Risk is a reviewer judgment from the fixed
    tier list. Changing the intake link re-pins the new
    request; clearing it drops the link.
    """
    use = gv_get_use(instance, uc_id)
    if use.get("status") in ("approved", "rejected"):
        raise CaseError(f"{uc_id} is {use['status']}; reopen it first")
    if title is not None:
        if not str(title).strip():
            raise CaseError("a use case needs its title")
        use["title"] = str(title).strip()
    if description is not None:
        if not str(description).strip():
            raise CaseError("a use case needs its purpose")
        use["description"] = str(description).strip()
    if data_types is not None:
        if not str(data_types).strip():
            raise CaseError("a use case needs its data")
        use["data_types"] = str(data_types).strip()
    if system is not None:
        use["system"] = str(system).strip()
    if risk is not None:
        if risk not in UC_RISK:
            raise CaseError(f"risk must be one of: {', '.join(UC_RISK)}")
        use["risk"] = risk
    if owner is not None:
        use["owner"] = str(owner).strip()
    if conditions is not None:
        use["conditions"] = str(conditions).strip()
    if in_id is not None:
        in_id = str(in_id).strip()
        request = _gv_intake(data, instance, in_id)
        if in_id and request is None:
            raise CaseError(
                f"intake request {in_id!r} is not in the source queue"
            )
        use["in_id"] = in_id
        use["in_pin"] = (
            _intake_use_pin(request) if request is not None else None
        )
    use["history"].append({"at": now(), "change": "revised", "detail": uc_id})
    _gv_version(instance, f"revised {uc_id}")
    _gv_touch(instance, "use_revised", uc_id)
    return use


def decide_uc(
    instance: dict, uc_id: str, decision: str, reason: str = ""
) -> dict:
    """Move a use case through its lifecycle (pure; caller saves).

    Assessing, approving, and rejecting each need a
    stated reason; approving also needs an assessed
    risk tier. Conditions are recorded with set before
    the approval that carries them.
    """
    use = gv_get_use(instance, uc_id)
    status = use.get("status") or "proposed"
    if decision not in (_UC_DECISIONS.get(status) or ()):
        allowed = ", ".join(_UC_DECISIONS.get(status) or ())
        raise CaseError(f"{uc_id} is {status}; allowed next: {allowed or 'none'}")
    if decision != "proposed" and not str(reason or "").strip():
        raise CaseError(f"{decision} needs a stated reason")
    if decision == "approved" and use.get("risk") == "unassessed":
        raise CaseError(f"{uc_id} needs an assessed risk before approval")
    use["status"] = decision
    use["resolution"] = str(reason or "").strip()
    use["history"].append(
        {"at": now(), "change": decision, "detail": str(reason or "").strip()}
    )
    _gv_version(instance, f"{decision} {uc_id}")
    _gv_touch(instance, f"use_{decision}", uc_id)
    return use


def uc_status(instance: dict, data: dict, use: dict) -> dict:
    """Current, stale, or orphaned for one use case.

    Only the intake link can stale a case: revising,
    routing, or deciding the linked request marks it
    stale. Unassessed risk is a limitation, not
    staleness.
    """
    reasons: list[str] = []
    state = "current"
    in_id = use.get("in_id") or ""
    if in_id:
        request = _gv_intake(data, instance, in_id)
        if request is None:
            state = "orphaned"
            reasons.append(f"linked intake {in_id} is gone")
        elif _intake_use_pin(request) != use.get("in_pin"):
            state = "stale"
            reasons.append(f"linked intake {in_id} changed since this case")
    return {"state": state, "reasons": reasons}


def gv_queue(instance: dict, data: dict) -> list[dict]:
    """Proposed and assessed cases with their link state."""
    queue = []
    for use in instance.get("uses") or []:
        if use.get("status") not in ("proposed", "assessed"):
            continue
        status = uc_status(instance, data, use)
        queue.append(
            {
                "uc_id": use.get("uc_id"),
                "title": use.get("title"),
                "status": use.get("status"),
                "risk": use.get("risk"),
                "owner": use.get("owner"),
                "link_state": status["state"],
                "reasons": status["reasons"],
            }
        )
    return queue


def gv_summary(instance: dict, data: dict) -> dict:
    """Counts, risk spread, queue, unknowns, and stale cases."""
    uses = instance.get("uses") or []
    by_status: dict[str, int] = {}
    by_risk: dict[str, int] = {}
    for use in uses:
        status = use.get("status") or "proposed"
        by_status[status] = by_status.get(status, 0) + 1
        risk = use.get("risk") or "unassessed"
        by_risk[risk] = by_risk.get(risk, 0) + 1
    unassessed = [u.get("uc_id") for u in uses if u.get("risk") == "unassessed"]
    stale = []
    for use in uses:
        status = uc_status(instance, data, use)
        if status["state"] != "current":
            stale.append(
                {
                    "uc_id": use.get("uc_id"),
                    "state": status["state"],
                    "reasons": status["reasons"],
                }
            )
    return {
        "counts": by_status,
        "risk": by_risk,
        "queue": gv_queue(instance, data),
        "unassessed": unassessed,
        "stale": stale,
    }


def gv_snapshot(instance: dict, data: dict) -> dict:
    """Content hash a governance review binds (recomputed, never stored)."""
    from quality.records import evidence_fingerprint

    return {
        "content": _stable({"uses": instance.get("uses")}),
        "evidence": evidence_fingerprint(data),
        "template_version": instance.get("template_version"),
    }


def gv_diff(instance: dict, old: int, new: int) -> dict:
    """Before/after comparison between two inventory versions."""
    versions = {v.get("v"): v for v in instance.get("versions") or []}
    if old not in versions or new not in versions:
        raise CaseError(f"versions {old}/{new} are not in this instance")
    before = {u.get("uc_id"): u for u in versions[old].get("uses") or []}
    after = {u.get("uc_id"): u for u in versions[new].get("uses") or []}
    changes = []
    for uc_id in sorted(set(before) | set(after)):
        if uc_id not in before:
            changes.append({"uc_id": uc_id, "change": "added"})
            continue
        if uc_id not in after:
            changes.append({"uc_id": uc_id, "change": "removed"})
            continue
        first, second = before[uc_id], after[uc_id]
        for key in (
            "title", "description", "data_types", "system", "risk",
            "owner", "conditions", "in_id", "status", "resolution",
        ):
            if first.get(key) != second.get(key):
                changes.append(
                    {
                        "uc_id": uc_id,
                        "change": key,
                        "before": first.get(key),
                        "after": second.get(key),
                    }
                )
    return {"old": old, "new": new, "changes": changes}


def gv_export(
    instance: dict,
    data: dict,
    version: int | None = None,
) -> dict:
    """Versioned JSON report; historical exports stay frozen."""
    from quality.records import evidence_fingerprint

    versions = instance.get("versions") or []
    if version is None:
        uses = instance.get("uses") or []
        historical = False
    else:
        match = next((v for v in versions if v.get("v") == version), None)
        if match is None:
            raise CaseError(f"version {version} is not in this instance")
        uses = match.get("uses") or []
        historical = True
    out_uses = []
    for use in uses:
        limitations = []
        if not historical:
            limitations.extend(uc_status(instance, data, use)["reasons"])
        if use.get("status") in _UC_NEXT:
            limitations.append(
                f"status: {use.get('status')} "
                f"(next: {_UC_NEXT[use['status']]})"
            )
        if use.get("risk") == "unassessed":
            limitations.append("risk unassessed")
        if use.get("conditions"):
            limitations.append(f"conditions: {use['conditions']}")
        out_uses.append(
            {
                "uc_id": use.get("uc_id"),
                "title": use.get("title"),
                "description": use.get("description"),
                "data_types": use.get("data_types"),
                "system": use.get("system"),
                "risk": use.get("risk"),
                "owner": use.get("owner"),
                "in_id": use.get("in_id"),
                "status": use.get("status"),
                "conditions": use.get("conditions"),
                "resolution": use.get("resolution"),
                "limitations": limitations,
            }
        )
    summary = {} if historical else gv_summary(instance, data)
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
        "source_instance": instance.get("source_instance"),
        "summary": summary,
        "uses": out_uses,
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
            "synthetic demonstration: use cases are reviewer-logged "
            "records",
            "risk tiers are reviewer judgments, not measured "
            "evaluations",
            "approval covers the reviewed snapshot and its recorded "
            "conditions only",
        ]
        + (["historical version: liveness was not recomputed"] if historical else []),
    }


register(
    TEMPLATE_GV,
    snapshot=gv_snapshot,
    open=open_gv_instance,
    diff=gv_diff,
    export=gv_export,
    export_takes_root=False,
    count_key="uses",
    review_keys=('uses',),
)

__all__ = ['UC_RISK', 'UC_STATUS', '_UC_NEXT', '_UC_DECISIONS', '_gv_source', 'open_gv_instance', 'gv_get_use', '_gv_version', '_gv_touch', '_next_uc_id', '_gv_intake', '_intake_use_pin', 'add_uc', 'set_uc', 'decide_uc', 'uc_status', 'gv_queue', 'gv_summary', 'gv_snapshot', 'gv_diff', 'gv_export']
