"""renewal & obligation calendar (one artifact slice; split from artifacts.py, behavior unchanged)."""

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
from .playbook import _clause_pin, _pb_source


CAL_KINDS = ("renewal", "notice", "payment", "termination", "other")
CAL_STATUS = ("open", "satisfied", "waived")


def open_cal_instance(
    data: dict, run_id: str, title: str, source_id: str = ""
) -> dict:
    """Create a calendar over one contract review (pure; caller saves).

    Needs a contract-review instance in the same matter. An
    explicit source wins; otherwise the single contract review
    is used, and several require naming one.
    """
    contracts = [
        key
        for key, entry in (data.get("artifacts", {}) or {}).items()
        if isinstance(entry, dict) and entry.get("template") == TEMPLATE_CT
    ]
    if source_id:
        _pb_source(data, source_id)
    elif len(contracts) == 1:
        source_id = contracts[0]
    elif not contracts:
        raise CaseError("the calendar needs a contract-review instance")
    else:
        raise CaseError(
            "several contract reviews exist; name one with --source"
        )
    instance = {
        "id": _next_instance_id(data),
        "template": TEMPLATE_CAL,
        "template_version": TEMPLATE_CAL_VERSION,
        "matter": run_id,
        "title": title or "Renewal & Obligation Calendar",
        "owner": "local",
        "viewers": ["local"],
        "created_at": now(),
        "source_instance": source_id,
        "obligations": [],
        "versions": [],
        "review": _blank_review(),
        "history": [{"at": now(), "event": "opened", "detail": source_id}],
    }
    instance["versions"].append(
        {"v": 1, "at": now(), "reason": "opened", "obligations": []}
    )
    return instance


def cal_get_ob(instance: dict, ob_id: str) -> dict:
    obligation = next(
        (o for o in instance.get("obligations") or [] if o.get("ob_id") == ob_id),
        None,
    )
    if obligation is None:
        raise CaseError(f"unknown obligation {ob_id!r}")
    return obligation


def _cal_version(instance: dict, reason: str) -> None:
    versions = instance.setdefault("versions", [])
    versions.append(
        {
            "v": len(versions) + 1,
            "at": now(),
            "reason": reason,
            "obligations": copy.deepcopy(instance.get("obligations")),
        }
    )


def _cal_touch(instance: dict, event: str, detail: str) -> None:
    instance["history"].append({"at": now(), "event": event, "detail": detail})
    instance["review"] = _blank_review()


def _next_ob_id(instance: dict) -> str:
    taken = {o.get("ob_id", "") for o in instance.get("obligations") or []}
    seq = 1
    while f"OB{seq}" in taken:
        seq += 1
    return f"OB{seq}"


def _cal_clause(data: dict, instance: dict, clause_id: str) -> dict | None:
    source = _pb_source(data, instance.get("source_instance") or "")
    return next(
        (
            c
            for c in source.get("clauses") or []
            if c.get("clause_id") == clause_id
        ),
        None,
    )


def _checked_notice_days(value: Any) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        days = int(value)
    except (TypeError, ValueError):
        raise CaseError(f"notice days {value!r} is not a whole number") from None
    if days < 0:
        raise CaseError("notice days cannot be negative")
    return days


def add_ob(
    instance: dict,
    data: dict,
    clause_id: str,
    kind: str,
    title: str,
    due_date: str = "",
    notice_days: Any = None,
    notes: str = "",
) -> dict:
    """Add one obligation bound to a clause in the source review.

    The due date is reviewer-recorded: empty stays unknown,
    malformed is refused. The clause pin records exactly what
    was assessed, so later clause revisions stale the item.
    """
    clause = _cal_clause(data, instance, clause_id)
    if clause is None:
        raise CaseError(
            f"clause {clause_id!r} is not in {instance.get('source_instance')}"
        )
    if kind not in CAL_KINDS:
        raise CaseError(f"kind must be one of: {', '.join(CAL_KINDS)}")
    if not str(title or "").strip():
        raise CaseError("an obligation needs its title")
    due = str(due_date or "").strip()
    if due and not _valid_date(due):
        raise CaseError(f"{due!r} is not a YYYY-MM-DD date")
    obligation = {
        "ob_id": _next_ob_id(instance),
        "clause_id": clause_id,
        "kind": kind,
        "title": str(title),
        "due_date": due,
        "notice_days": _checked_notice_days(notice_days),
        "notes": str(notes or ""),
        "status": "open",
        "resolution": "",
        "clause_pin": _clause_pin(clause),
        "history": [{"at": now(), "change": "added", "detail": ""}],
    }
    instance.setdefault("obligations", []).append(obligation)
    _cal_version(instance, f"added {obligation['ob_id']}")
    _cal_touch(instance, "obligation_added", obligation["ob_id"])
    return obligation


def set_ob(instance: dict, data: dict, ob_id: str, **fields: Any) -> dict:
    """Revise an obligation's title, kind, dates, or notes.

    Saving re-examines the clause and refreshes the pin: the
    reviewer looked again, so a prior revision staleness
    clears with its reason recorded in history.
    """
    obligation = cal_get_ob(instance, ob_id)
    changed = []
    if "title" in fields and fields["title"] is not None:
        value = str(fields["title"])
        if not value.strip():
            raise CaseError("an obligation needs its title")
        if value != obligation.get("title"):
            obligation["title"] = value
            changed.append("title")
    if "kind" in fields and fields["kind"] is not None:
        value = str(fields["kind"])
        if value not in CAL_KINDS:
            raise CaseError(f"kind must be one of: {', '.join(CAL_KINDS)}")
        if value != obligation.get("kind"):
            obligation["kind"] = value
            changed.append("kind")
    if "due_date" in fields and fields["due_date"] is not None:
        value = str(fields["due_date"]).strip()
        if value and not _valid_date(value):
            raise CaseError(f"{value!r} is not a YYYY-MM-DD date")
        if value != obligation.get("due_date"):
            obligation["due_date"] = value
            changed.append("due_date")
    if "notice_days" in fields and fields["notice_days"] is not None:
        value = _checked_notice_days(fields["notice_days"])
        if value != obligation.get("notice_days"):
            obligation["notice_days"] = value
            changed.append("notice_days")
    if "notes" in fields and fields["notes"] is not None:
        value = str(fields["notes"])
        if value != obligation.get("notes"):
            obligation["notes"] = value
            changed.append("notes")
    if not changed:
        return obligation
    clause = _cal_clause(data, instance, obligation.get("clause_id") or "")
    if clause is not None:
        obligation["clause_pin"] = _clause_pin(clause)
        changed.append("rebound")
    obligation["history"].append(
        {"at": now(), "change": "set", "detail": ", ".join(changed)}
    )
    _cal_version(instance, f"set {ob_id}")
    _cal_touch(instance, "obligation_set", ob_id)
    return obligation


def resolve_ob(instance: dict, ob_id: str, status: str, reason: str = "") -> dict:
    """Mark an obligation satisfied, waived, or back open.

    Satisfied and waived need their reason on the record;
    reopening records the return without erasing history.
    """
    obligation = cal_get_ob(instance, ob_id)
    if status not in CAL_STATUS:
        raise CaseError(f"status must be one of: {', '.join(CAL_STATUS)}")
    if status in ("satisfied", "waived") and not str(reason or "").strip():
        raise CaseError(f"marking {status} needs its reason")
    obligation["status"] = status
    obligation["resolution"] = str(reason or "") if status != "open" else ""
    obligation["history"].append(
        {"at": now(), "change": "resolved", "detail": f"{status} {reason}".strip()}
    )
    _cal_version(instance, f"resolved {ob_id} {status}")
    _cal_touch(instance, "obligation_resolved", ob_id)
    return obligation


def ob_status(instance: dict, data: dict, obligation: dict) -> dict:
    """Whether an obligation's clause changed or vanished."""
    clause = _cal_clause(data, instance, obligation.get("clause_id") or "")
    if clause is None or clause.get("retracted"):
        return {
            "stale": False,
            "orphaned": True,
            "reasons": ["the clause was retracted"],
        }
    if _clause_pin(clause) != obligation.get("clause_pin"):
        return {
            "stale": True,
            "orphaned": False,
            "reasons": ["the clause was revised since the obligation was set"],
        }
    return {"stale": False, "orphaned": False, "reasons": []}


def cal_summary(instance: dict, data: dict, as_of: str = "") -> dict:
    """Counts plus the upcoming list, unknowns kept apart.

    Overdue is relative to an explicit as-of date only; with
    none given, overdue stays null instead of guessing today.
    """
    import datetime as _datetime

    obligations = instance.get("obligations") or []
    if as_of:
        if not _valid_date(as_of):
            raise CaseError(f"{as_of!r} is not a YYYY-MM-DD date")
        today = _datetime.date.fromisoformat(as_of)
    else:
        today = None
    open_obs = [o for o in obligations if o.get("status") == "open"]
    dated = sorted(
        (o for o in open_obs if o.get("due_date")),
        key=lambda o: (o.get("due_date"), o.get("ob_id")),
    )
    upcoming = [
        {
            "ob_id": o.get("ob_id"),
            "title": o.get("title"),
            "kind": o.get("kind"),
            "due_date": o.get("due_date"),
            "overdue": bool(today and o["due_date"] < as_of),
        }
        for o in dated
    ]
    unknown = [
        {"ob_id": o.get("ob_id"), "title": o.get("title"), "kind": o.get("kind")}
        for o in open_obs
        if not o.get("due_date")
    ]
    stale = sum(
        1 for o in obligations if ob_status(instance, data, o)["stale"]
    )
    orphaned = sum(
        1 for o in obligations if ob_status(instance, data, o)["orphaned"]
    )
    by_status = {name: 0 for name in CAL_STATUS}
    for o in obligations:
        if o.get("status") in by_status:
            by_status[o["status"]] += 1
    return {
        "total": len(obligations),
        "by_status": by_status,
        "stale": stale,
        "orphaned": orphaned,
        "as_of": as_of or None,
        "overdue": (
            [u["ob_id"] for u in upcoming if u["overdue"]] if today else None
        ),
        "upcoming": upcoming,
        "unknown_dated": unknown,
    }


def cal_snapshot(instance: dict, data: dict) -> dict:
    """Content hash a calendar review binds (recomputed, never stored)."""
    from quality.records import evidence_fingerprint

    return {
        "content": _stable(
            {
                "obligations": instance.get("obligations"),
                "source": instance.get("source_instance"),
            }
        ),
        "evidence": evidence_fingerprint(data),
        "template_version": instance.get("template_version"),
    }


def cal_diff(instance: dict, old: int, new: int) -> dict:
    """Before/after comparison between two calendar versions."""
    versions = {v.get("v"): v for v in instance.get("versions") or []}
    if old not in versions or new not in versions:
        raise CaseError(f"versions {old}/{new} are not in this instance")
    before = {o.get("ob_id"): o for o in versions[old].get("obligations") or []}
    after = {o.get("ob_id"): o for o in versions[new].get("obligations") or []}
    changes = []
    for ob_id in sorted(set(before) | set(after)):
        if ob_id not in before:
            changes.append({"ob_id": ob_id, "change": "added"})
            continue
        if ob_id not in after:
            changes.append({"ob_id": ob_id, "change": "removed"})
            continue
        first, second = before[ob_id], after[ob_id]
        for key in (
            "title", "kind", "due_date", "notice_days", "notes",
            "status", "resolution",
        ):
            if first.get(key) != second.get(key):
                changes.append(
                    {
                        "ob_id": ob_id,
                        "change": key,
                        "before": first.get(key),
                        "after": second.get(key),
                    }
                )
    return {"old": old, "new": new, "changes": changes}


def cal_export(
    instance: dict, data: dict, version: int | None = None
) -> dict:
    """Versioned calendar report with queue, limits, approval scope."""
    from quality.records import evidence_fingerprint

    versions = instance.get("versions") or []
    if version is None:
        obligations = instance.get("obligations") or []
        historical = False
    else:
        match = next((v for v in versions if v.get("v") == version), None)
        if match is None:
            raise CaseError(f"version {version} is not in this instance")
        obligations = match.get("obligations") or []
        historical = True
    out_obs = []
    for obligation in obligations:
        limitations = []
        if not historical:
            status = ob_status(instance, data, obligation)
            limitations.extend(status["reasons"])
            if status["orphaned"]:
                limitations.append("orphaned: clause retracted")
        if not obligation.get("due_date"):
            limitations.append("due date unknown")
        if obligation.get("status") == "waived":
            limitations.append(f"waived: {obligation.get('resolution') or '?'}")
        out_obs.append(
            {
                "ob_id": obligation.get("ob_id"),
                "clause_id": obligation.get("clause_id"),
                "kind": obligation.get("kind"),
                "title": obligation.get("title"),
                "due_date": obligation.get("due_date"),
                "notice_days": obligation.get("notice_days"),
                "notes": obligation.get("notes"),
                "status": obligation.get("status"),
                "resolution": obligation.get("resolution"),
                "limitations": limitations,
            }
        )
    summary = {} if historical else cal_summary(instance, data)
    review = instance.get("review") or {}
    return {
        "template": instance.get("template"),
        "template_version": instance.get("template_version"),
        "instance": instance.get("id"),
        "matter": instance.get("matter"),
        "title": instance.get("title"),
        "source_instance": instance.get("source_instance"),
        "exported_version": version or (versions[-1].get("v") if versions else 1),
        "historical": historical,
        "evidence_fingerprint": evidence_fingerprint(data) if not historical else None,
        "summary": summary,
        "obligations": out_obs,
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
            "synthetic demonstration: dates and resolutions are "
            "reviewer records, not docketed deadlines",
            "overdue is relative to the stated as-of date; unknown "
            "dates never default to today",
        ]
        + (["historical version: liveness was not recomputed"] if historical else []),
    }


register(
    TEMPLATE_CAL,
    snapshot=cal_snapshot,
    open=open_cal_instance,
    diff=cal_diff,
    export=cal_export,
    export_takes_root=False,
    count_key="obligations",
    review_keys=('obligations',),
)

__all__ = ['CAL_KINDS', 'CAL_STATUS', 'open_cal_instance', 'cal_get_ob', '_cal_version', '_cal_touch', '_next_ob_id', '_cal_clause', '_checked_notice_days', 'add_ob', 'set_ob', 'resolve_ob', 'ob_status', 'cal_summary', 'cal_snapshot', 'cal_diff', 'cal_export']
