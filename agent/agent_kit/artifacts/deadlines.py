"""matter & deadlines board (one artifact slice; split from artifacts.py, behavior unchanged)."""

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


DL_KINDS = ("filing", "renewal", "meeting", "payment", "other")
DL_STATUS = ("open", "done", "waived")


def _cal_source(data: dict, source_id: str) -> dict:
    source = (data.get("artifacts", {}) or {}).get(source_id)
    if not isinstance(source, dict):
        raise CaseError(f"unknown source instance {source_id!r}")
    if source.get("template") != TEMPLATE_CAL:
        raise CaseError(
            f"{source_id} is a {source.get('template')}, not an obligation calendar"
        )
    return source


def open_dl_instance(
    data: dict, run_id: str, title: str, source_id: str = ""
) -> dict:
    """Create a deadlines board over one calendar (pure; caller saves).

    Needs an obligation-calendar instance in the same matter. An
    explicit source wins; otherwise the single calendar is used,
    and several require naming one.
    """
    calendars = [
        key
        for key, entry in (data.get("artifacts", {}) or {}).items()
        if isinstance(entry, dict) and entry.get("template") == TEMPLATE_CAL
    ]
    if source_id:
        _cal_source(data, source_id)
    elif len(calendars) == 1:
        source_id = calendars[0]
    elif not calendars:
        raise CaseError("the board needs an obligation calendar")
    else:
        raise CaseError(
            "several obligation calendars exist; name one with --source"
        )
    instance = {
        "id": _next_instance_id(data),
        "template": TEMPLATE_DL,
        "template_version": TEMPLATE_DL_VERSION,
        "matter": run_id,
        "title": title or "Matter & Deadlines Board",
        "owner": "local",
        "viewers": ["local"],
        "created_at": now(),
        "source_instance": source_id,
        "deadlines": [],
        "versions": [],
        "review": _blank_review(),
        "history": [{"at": now(), "event": "opened", "detail": source_id}],
    }
    instance["versions"].append(
        {"v": 1, "at": now(), "reason": "opened", "deadlines": []}
    )
    return instance


def dl_get_item(instance: dict, dl_id: str) -> dict:
    item = next(
        (d for d in instance.get("deadlines") or [] if d.get("dl_id") == dl_id),
        None,
    )
    if item is None:
        raise CaseError(f"unknown deadline {dl_id!r}")
    return item


def _dl_version(instance: dict, reason: str) -> None:
    versions = instance.setdefault("versions", [])
    versions.append(
        {
            "v": len(versions) + 1,
            "at": now(),
            "reason": reason,
            "deadlines": copy.deepcopy(instance.get("deadlines")),
        }
    )


def _dl_touch(instance: dict, event: str, detail: str) -> None:
    instance["history"].append({"at": now(), "event": event, "detail": detail})
    instance["review"] = _blank_review()


def _next_dl_id(instance: dict) -> str:
    taken = {d.get("dl_id", "") for d in instance.get("deadlines") or []}
    seq = 1
    while f"DL{seq}" in taken:
        seq += 1
    return f"DL{seq}"


def _dl_obligation(data: dict, instance: dict, ob_id: str) -> dict | None:
    source = _cal_source(data, instance.get("source_instance") or "")
    return next(
        (o for o in source.get("obligations") or [] if o.get("ob_id") == ob_id),
        None,
    )


def _ob_pin(obligation: dict) -> str:
    return _stable(
        {
            "title": obligation.get("title"),
            "kind": obligation.get("kind"),
            "due_date": obligation.get("due_date"),
            "status": obligation.get("status"),
        }
    )


def _checked_ob(data: dict, instance: dict, ob_id: str) -> str:
    ob_id = str(ob_id or "").strip()
    if not ob_id:
        return ""
    if _dl_obligation(data, instance, ob_id) is None:
        raise CaseError(
            f"obligation {ob_id!r} is not in {instance.get('source_instance')}"
        )
    return ob_id


def add_dl(
    instance: dict,
    data: dict,
    title: str,
    kind: str,
    owner: str = "",
    due_date: str = "",
    ob_id: str = "",
    notes: str = "",
) -> dict:
    """Add one deadline, optionally tracking a calendar obligation."""
    if not str(title or "").strip():
        raise CaseError("a deadline needs its title")
    if kind not in DL_KINDS:
        raise CaseError(f"kind must be one of: {', '.join(DL_KINDS)}")
    due = str(due_date or "").strip()
    if due and not _valid_date(due):
        raise CaseError(f"{due!r} is not a YYYY-MM-DD date")
    link = _checked_ob(data, instance, ob_id)
    obligation = _dl_obligation(data, instance, link) if link else None
    item = {
        "dl_id": _next_dl_id(instance),
        "title": str(title),
        "kind": kind,
        "owner": str(owner or ""),
        "due_date": due,
        "ob_id": link,
        "ob_pin": _ob_pin(obligation) if obligation else None,
        "notes": str(notes or ""),
        "status": "open",
        "resolution": "",
        "history": [{"at": now(), "change": "added", "detail": ""}],
    }
    instance.setdefault("deadlines", []).append(item)
    _dl_version(instance, f"added {item['dl_id']}")
    _dl_touch(instance, "deadline_added", item["dl_id"])
    return item


def set_dl(instance: dict, data: dict, dl_id: str, **fields: Any) -> dict:
    """Revise a deadline's text, dates, owner, or obligation link.

    Saving re-pins the linked obligation: the reviewer looked
    again, so a prior revision staleness clears with its
    reason recorded in history.
    """
    item = dl_get_item(instance, dl_id)
    changed = []
    for key in ("title", "owner", "notes"):
        if key in fields and fields[key] is not None:
            value = str(fields[key])
            if key == "title" and not value.strip():
                raise CaseError("a deadline needs its title")
            if value != item.get(key):
                item[key] = value
                changed.append(key)
    if "kind" in fields and fields["kind"] is not None:
        value = str(fields["kind"])
        if value not in DL_KINDS:
            raise CaseError(f"kind must be one of: {', '.join(DL_KINDS)}")
        if value != item.get("kind"):
            item["kind"] = value
            changed.append("kind")
    if "due_date" in fields and fields["due_date"] is not None:
        value = str(fields["due_date"]).strip()
        if value and not _valid_date(value):
            raise CaseError(f"{value!r} is not a YYYY-MM-DD date")
        if value != item.get("due_date"):
            item["due_date"] = value
            changed.append("due_date")
    if "ob_id" in fields and fields["ob_id"] is not None:
        value = _checked_ob(data, instance, fields["ob_id"])
        if value != item.get("ob_id"):
            item["ob_id"] = value
            changed.append("ob_id")
    if not changed:
        return item
    obligation = (
        _dl_obligation(data, instance, item.get("ob_id") or "")
        if item.get("ob_id")
        else None
    )
    item["ob_pin"] = _ob_pin(obligation) if obligation else None
    changed.append("rebound")
    item["history"].append(
        {"at": now(), "change": "set", "detail": ", ".join(changed)}
    )
    _dl_version(instance, f"set {dl_id}")
    _dl_touch(instance, "deadline_set", dl_id)
    return item


def resolve_dl(instance: dict, dl_id: str, status: str, reason: str = "") -> dict:
    """Mark a deadline done, waived, or back open.

    Done and waived need their reason on the record;
    reopening records the return without erasing history.
    """
    item = dl_get_item(instance, dl_id)
    if status not in DL_STATUS:
        raise CaseError(f"status must be one of: {', '.join(DL_STATUS)}")
    if status in ("done", "waived") and not str(reason or "").strip():
        raise CaseError(f"marking {status} needs its reason")
    item["status"] = status
    item["resolution"] = str(reason or "") if status != "open" else ""
    item["history"].append(
        {"at": now(), "change": "resolved", "detail": f"{status} {reason}".strip()}
    )
    _dl_version(instance, f"resolved {dl_id} {status}")
    _dl_touch(instance, "deadline_resolved", dl_id)
    return item


def dl_status(instance: dict, data: dict, item: dict) -> dict:
    """Whether a deadline's linked obligation changed or vanished."""
    ob_id = item.get("ob_id") or ""
    if not ob_id:
        return {"stale": False, "orphaned": False, "reasons": []}
    obligation = _dl_obligation(data, instance, ob_id)
    if obligation is None:
        return {
            "stale": False,
            "orphaned": True,
            "reasons": ["the linked obligation is gone"],
        }
    if _ob_pin(obligation) != item.get("ob_pin"):
        return {
            "stale": True,
            "orphaned": False,
            "reasons": ["the linked obligation changed"],
        }
    return {"stale": False, "orphaned": False, "reasons": []}


def dl_summary(instance: dict, data: dict, as_of: str = "") -> dict:
    """Counts plus the upcoming list, unknowns kept apart.

    Overdue is relative to an explicit as-of date only; with
    none given, overdue stays null instead of guessing today.
    """
    items = instance.get("deadlines") or []
    if as_of and not _valid_date(as_of):
        raise CaseError(f"{as_of!r} is not a YYYY-MM-DD date")
    open_items = [d for d in items if d.get("status") == "open"]
    dated = sorted(
        (d for d in open_items if d.get("due_date")),
        key=lambda d: (d.get("due_date"), d.get("dl_id")),
    )
    upcoming = [
        {
            "dl_id": d.get("dl_id"),
            "title": d.get("title"),
            "kind": d.get("kind"),
            "due_date": d.get("due_date"),
            "overdue": bool(as_of and d["due_date"] < as_of),
        }
        for d in dated
    ]
    unknown = [
        {"dl_id": d.get("dl_id"), "title": d.get("title"), "kind": d.get("kind")}
        for d in open_items
        if not d.get("due_date")
    ]
    stale = sum(1 for d in items if dl_status(instance, data, d)["stale"])
    orphaned = sum(
        1 for d in items if dl_status(instance, data, d)["orphaned"]
    )
    by_status = {name: 0 for name in DL_STATUS}
    for d in items:
        if d.get("status") in by_status:
            by_status[d["status"]] += 1
    return {
        "total": len(items),
        "by_status": by_status,
        "stale": stale,
        "orphaned": orphaned,
        "unowned": sum(
            1 for d in items if not str(d.get("owner") or "").strip()
        ),
        "as_of": as_of or None,
        "overdue": (
            [u["dl_id"] for u in upcoming if u["overdue"]] if as_of else None
        ),
        "upcoming": upcoming,
        "unknown_dated": unknown,
    }


def dl_snapshot(instance: dict, data: dict) -> dict:
    """Content hash a board review binds (recomputed, never stored)."""
    from quality.records import evidence_fingerprint

    return {
        "content": _stable(
            {
                "deadlines": instance.get("deadlines"),
                "source": instance.get("source_instance"),
            }
        ),
        "evidence": evidence_fingerprint(data),
        "template_version": instance.get("template_version"),
    }


def dl_diff(instance: dict, old: int, new: int) -> dict:
    """Before/after comparison between two board versions."""
    versions = {v.get("v"): v for v in instance.get("versions") or []}
    if old not in versions or new not in versions:
        raise CaseError(f"versions {old}/{new} are not in this instance")
    before = {d.get("dl_id"): d for d in versions[old].get("deadlines") or []}
    after = {d.get("dl_id"): d for d in versions[new].get("deadlines") or []}
    changes = []
    for dl_id in sorted(set(before) | set(after)):
        if dl_id not in before:
            changes.append({"dl_id": dl_id, "change": "added"})
            continue
        if dl_id not in after:
            changes.append({"dl_id": dl_id, "change": "removed"})
            continue
        first, second = before[dl_id], after[dl_id]
        for key in (
            "title", "kind", "owner", "due_date", "ob_id", "notes",
            "status", "resolution",
        ):
            if first.get(key) != second.get(key):
                changes.append(
                    {
                        "dl_id": dl_id,
                        "change": key,
                        "before": first.get(key),
                        "after": second.get(key),
                    }
                )
    return {"old": old, "new": new, "changes": changes}


def dl_export(
    instance: dict, data: dict, version: int | None = None
) -> dict:
    """Versioned board report with queue, limits, approval scope."""
    from quality.records import evidence_fingerprint

    versions = instance.get("versions") or []
    if version is None:
        items = instance.get("deadlines") or []
        historical = False
    else:
        match = next((v for v in versions if v.get("v") == version), None)
        if match is None:
            raise CaseError(f"version {version} is not in this instance")
        items = match.get("deadlines") or []
        historical = True
    out_items = []
    for item in items:
        limitations = []
        if not historical:
            status = dl_status(instance, data, item)
            limitations.extend(status["reasons"])
            if status["orphaned"]:
                limitations.append("orphaned: linked obligation gone")
        if not item.get("due_date"):
            limitations.append("due date unknown")
        if not str(item.get("owner") or "").strip():
            limitations.append("no owner assigned")
        if item.get("status") == "waived":
            limitations.append(f"waived: {item.get('resolution') or '?'}")
        out_items.append(
            {
                "dl_id": item.get("dl_id"),
                "title": item.get("title"),
                "kind": item.get("kind"),
                "owner": item.get("owner"),
                "due_date": item.get("due_date"),
                "ob_id": item.get("ob_id"),
                "notes": item.get("notes"),
                "status": item.get("status"),
                "resolution": item.get("resolution"),
                "limitations": limitations,
            }
        )
    summary = {} if historical else dl_summary(instance, data)
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
        "deadlines": out_items,
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
    TEMPLATE_DL,
    snapshot=dl_snapshot,
    open=open_dl_instance,
    diff=dl_diff,
    export=dl_export,
    export_takes_root=False,
    count_key="deadlines",
    review_keys=('deadlines',),
)

__all__ = ['DL_KINDS', 'DL_STATUS', '_cal_source', 'open_dl_instance', 'dl_get_item', '_dl_version', '_dl_touch', '_next_dl_id', '_dl_obligation', '_ob_pin', '_checked_ob', 'add_dl', 'set_dl', 'resolve_dl', 'dl_status', 'dl_summary', 'dl_snapshot', 'dl_diff', 'dl_export']
