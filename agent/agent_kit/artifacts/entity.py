"""entity compliance calendar (one artifact slice; split from artifacts.py, behavior unchanged)."""

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


EC_KINDS = (
    "annual-report",
    "franchise-tax",
    "registered-agent",
    "statement-of-information",
    "license",
    "other",
)
EC_STATUS = ("pending", "filed", "waived")
_EC_NEXT = {
    "pending": "file or waive",
}
_EC_DECISIONS = {
    "pending": ("filed", "waived"),
    "filed": ("pending",),
    "waived": ("pending",),
}


def open_ec_instance(
    data: dict, run_id: str, title: str, source_id: str = ""
) -> dict:
    """Create an entity calendar (pure; caller saves).

    No prerequisites: entities and filings are reviewer
    records, so an empty matter can already log them.
    """
    instance = {
        "id": _next_instance_id(data),
        "template": TEMPLATE_EC,
        "template_version": TEMPLATE_EC_VERSION,
        "matter": run_id,
        "title": title or "Entity Compliance Calendar",
        "owner": "local",
        "viewers": ["local"],
        "created_at": now(),
        "entities": [],
        "filings": [],
        "versions": [],
        "review": _blank_review(),
        "history": [{"at": now(), "event": "opened", "detail": ""}],
    }
    instance["versions"].append(
        {"v": 1, "at": now(), "reason": "opened", "entities": [], "filings": []}
    )
    return instance


def ec_get_entity(instance: dict, en_id: str) -> dict:
    entity = next(
        (e for e in instance.get("entities") or [] if e.get("en_id") == en_id),
        None,
    )
    if entity is None:
        raise CaseError(f"unknown entity {en_id!r}")
    return entity


def ec_get_filing(instance: dict, cf_id: str) -> dict:
    filing = next(
        (f for f in instance.get("filings") or [] if f.get("cf_id") == cf_id),
        None,
    )
    if filing is None:
        raise CaseError(f"unknown filing {cf_id!r}")
    return filing


def _ec_version(instance: dict, reason: str) -> None:
    versions = instance.setdefault("versions", [])
    versions.append(
        {
            "v": len(versions) + 1,
            "at": now(),
            "reason": reason,
            "entities": copy.deepcopy(instance.get("entities")),
            "filings": copy.deepcopy(instance.get("filings")),
        }
    )


def _ec_touch(instance: dict, event: str, detail: str) -> None:
    instance["history"].append({"at": now(), "event": event, "detail": detail})
    instance["review"] = _blank_review()


def _next_en_id(instance: dict) -> str:
    taken = {e.get("en_id", "") for e in instance.get("entities") or []}
    seq = 1
    while f"EN{seq}" in taken:
        seq += 1
    return f"EN{seq}"


def _next_cf_id(instance: dict) -> str:
    taken = {f.get("cf_id", "") for f in instance.get("filings") or []}
    seq = 1
    while f"CF{seq}" in taken:
        seq += 1
    return f"CF{seq}"


def _entity_pin(entity: dict) -> str:
    return _stable(
        {
            "name": entity.get("name"),
            "regime": entity.get("regime"),
            "identifier": entity.get("identifier"),
        }
    )


def add_en(
    instance: dict, name: str, regime: str, identifier: str = ""
) -> dict:
    """Register one entity (pure; caller saves).

    Regimes are registry keys; anything else fails
    closed. The identifier is the reviewer's own
    reference, never a verified filing number.
    """
    from agent_kit import sources

    if not str(name or "").strip():
        raise CaseError("an entity needs its name")
    if regime not in sources.REGIONS:
        raise CaseError(
            f"regime {regime!r} is not registered "
            f"(one of: {', '.join(sorted(sources.REGIONS))})"
        )
    entity = {
        "en_id": _next_en_id(instance),
        "name": str(name).strip(),
        "regime": regime,
        "identifier": str(identifier or "").strip(),
        "history": [{"at": now(), "change": "registered", "detail": ""}],
    }
    instance.setdefault("entities", []).append(entity)
    _ec_version(instance, f"registered {entity['en_id']}")
    _ec_touch(instance, "entity_registered", entity["en_id"])
    return entity


def set_en(
    instance: dict,
    en_id: str,
    name: str | None = None,
    regime: str | None = None,
    identifier: str | None = None,
) -> dict:
    """Revise an entity (pure; caller saves).

    Revisions do not rewrite history: filings pinned
    to the old identity go stale with reasons until
    the reviewer re-pins them.
    """
    from agent_kit import sources

    entity = ec_get_entity(instance, en_id)
    if name is not None:
        if not str(name).strip():
            raise CaseError("an entity needs its name")
        entity["name"] = str(name).strip()
    if regime is not None:
        if regime not in sources.REGIONS:
            raise CaseError(
                f"regime {regime!r} is not registered "
                f"(one of: {', '.join(sorted(sources.REGIONS))})"
            )
        entity["regime"] = regime
    if identifier is not None:
        entity["identifier"] = str(identifier).strip()
    entity["history"].append(
        {"at": now(), "change": "revised", "detail": en_id}
    )
    _ec_version(instance, f"revised {en_id}")
    _ec_touch(instance, "entity_revised", en_id)
    return entity


def add_cf(
    instance: dict,
    en_id: str,
    title: str,
    kind: str,
    due_date: str = "",
    period: str = "",
    notes: str = "",
) -> dict:
    """Log one filing for an entity (pure; caller saves).

    The entity must already be registered. The due
    date stays unknown until the reviewer states it.
    """
    entity = ec_get_entity(instance, en_id)
    if not str(title or "").strip():
        raise CaseError("a filing needs its title")
    if kind not in EC_KINDS:
        raise CaseError(f"kind must be one of: {', '.join(EC_KINDS)}")
    due_date = str(due_date or "").strip()
    if due_date and not _valid_date(due_date):
        raise CaseError(f"due date must be YYYY-MM-DD, got {due_date!r}")
    filing = {
        "cf_id": _next_cf_id(instance),
        "en_id": en_id,
        "en_pin": _entity_pin(entity),
        "title": str(title).strip(),
        "kind": kind,
        "due_date": due_date,
        "period": str(period or "").strip(),
        "notes": str(notes or "").strip(),
        "status": "pending",
        "resolution": "",
        "history": [{"at": now(), "change": "logged", "detail": ""}],
    }
    instance.setdefault("filings", []).append(filing)
    _ec_version(instance, f"logged {filing['cf_id']}")
    _ec_touch(instance, "filing_logged", filing["cf_id"])
    return filing


def set_cf(
    instance: dict,
    cf_id: str,
    en_id: str | None = None,
    title: str | None = None,
    kind: str | None = None,
    due_date: str | None = None,
    period: str | None = None,
    notes: str | None = None,
    rebind: bool = False,
) -> dict:
    """Revise a filing (pure; caller saves).

    Filed and waived filings are locked: reopen them
    first. Moving a filing re-pins the new entity;
    rebind refreshes the pin after the reviewer
    confirms a stale entity revision.
    """
    filing = ec_get_filing(instance, cf_id)
    if filing.get("status") in ("filed", "waived"):
        raise CaseError(f"{cf_id} is {filing['status']}; reopen it first")
    if en_id is not None:
        entity = ec_get_entity(instance, en_id)
        filing["en_id"] = en_id
        filing["en_pin"] = _entity_pin(entity)
    if title is not None:
        if not str(title).strip():
            raise CaseError("a filing needs its title")
        filing["title"] = str(title).strip()
    if kind is not None:
        if kind not in EC_KINDS:
            raise CaseError(f"kind must be one of: {', '.join(EC_KINDS)}")
        filing["kind"] = kind
    if due_date is not None:
        due_date = str(due_date).strip()
        if due_date and not _valid_date(due_date):
            raise CaseError(f"due date must be YYYY-MM-DD, got {due_date!r}")
        filing["due_date"] = due_date
    if period is not None:
        filing["period"] = str(period).strip()
    if notes is not None:
        filing["notes"] = str(notes).strip()
    if rebind:
        filing["en_pin"] = _entity_pin(ec_get_entity(instance, filing["en_id"]))
    filing["history"].append(
        {"at": now(), "change": "revised", "detail": cf_id}
    )
    _ec_version(instance, f"revised {cf_id}")
    _ec_touch(instance, "filing_revised", cf_id)
    return filing


def decide_cf(
    instance: dict, cf_id: str, decision: str, reason: str = ""
) -> dict:
    """Record a filing outcome (pure; caller saves).

    Filing and waiving each need a stated reason on
    the record; reopening records the return without
    erasing history.
    """
    filing = ec_get_filing(instance, cf_id)
    status = filing.get("status") or "pending"
    if decision not in (_EC_DECISIONS.get(status) or ()):
        allowed = ", ".join(_EC_DECISIONS.get(status) or ())
        raise CaseError(f"{cf_id} is {status}; allowed next: {allowed or 'none'}")
    if decision != "pending" and not str(reason or "").strip():
        raise CaseError(f"{decision} needs a stated reason")
    filing["status"] = decision
    filing["resolution"] = str(reason or "").strip() if decision != "pending" else ""
    filing["history"].append(
        {"at": now(), "change": decision, "detail": str(reason or "").strip()}
    )
    _ec_version(instance, f"{decision} {cf_id}")
    _ec_touch(instance, f"filing_{decision}", cf_id)
    return filing


def cf_status(instance: dict, filing: dict) -> dict:
    """Current, stale, or orphaned for one filing.

    Only the entity link can stale a filing: revising
    the entity marks its filings stale, and a missing
    entity orphans them. Unknown due dates are
    limitations, not staleness.
    """
    reasons: list[str] = []
    state = "current"
    en_id = filing.get("en_id") or ""
    entity = next(
        (e for e in instance.get("entities") or [] if e.get("en_id") == en_id),
        None,
    )
    if entity is None:
        state = "orphaned"
        reasons.append(f"entity {en_id or '?'} is gone")
    elif _entity_pin(entity) != filing.get("en_pin"):
        state = "stale"
        reasons.append(f"entity {en_id} changed since this filing")
    return {"state": state, "reasons": reasons}


def ec_queue(instance: dict) -> list[dict]:
    """Pending filings with their link state."""
    queue = []
    for filing in instance.get("filings") or []:
        if filing.get("status") != "pending":
            continue
        status = cf_status(instance, filing)
        queue.append(
            {
                "cf_id": filing.get("cf_id"),
                "title": filing.get("title"),
                "en_id": filing.get("en_id"),
                "kind": filing.get("kind"),
                "due_date": filing.get("due_date"),
                "link_state": status["state"],
                "reasons": status["reasons"],
            }
        )
    return queue


def ec_summary(instance: dict, as_of: str = "") -> dict:
    """Counts, dated queue, unknowns, and stale filings.

    Overdue is relative to an explicit as-of date only;
    with none given, nothing is called overdue.
    """
    if as_of and not _valid_date(as_of):
        raise CaseError(f"{as_of!r} is not a YYYY-MM-DD date")
    filings = instance.get("filings") or []
    by_status: dict[str, int] = {}
    for filing in filings:
        status = filing.get("status") or "pending"
        by_status[status] = by_status.get(status, 0) + 1
    pending = [f for f in filings if f.get("status") == "pending"]
    dated = sorted(
        (f for f in pending if f.get("due_date")),
        key=lambda f: (f.get("due_date"), f.get("cf_id")),
    )
    upcoming = [
        {
            "cf_id": f.get("cf_id"),
            "title": f.get("title"),
            "en_id": f.get("en_id"),
            "kind": f.get("kind"),
            "due_date": f.get("due_date"),
            "overdue": bool(as_of and f["due_date"] < as_of),
        }
        for f in dated
    ]
    unknown_dated = [
        {"cf_id": f.get("cf_id"), "title": f.get("title")}
        for f in pending
        if not f.get("due_date")
    ]
    stale = []
    for filing in filings:
        status = cf_status(instance, filing)
        if status["state"] != "current":
            stale.append(
                {
                    "cf_id": filing.get("cf_id"),
                    "state": status["state"],
                    "reasons": status["reasons"],
                }
            )
    return {
        "counts": by_status,
        "entities": len(instance.get("entities") or []),
        "queue": ec_queue(instance),
        "upcoming": upcoming,
        "unknown_dated": unknown_dated,
        "overdue": sum(1 for u in upcoming if u["overdue"]) if as_of else None,
        "as_of": as_of or None,
        "stale": stale,
    }


def ec_snapshot(instance: dict, data: dict) -> dict:
    """Content hash an entity review binds (recomputed, never stored)."""
    from quality.records import evidence_fingerprint

    return {
        "content": _stable(
            {
                "entities": instance.get("entities"),
                "filings": instance.get("filings"),
            }
        ),
        "evidence": evidence_fingerprint(data),
        "template_version": instance.get("template_version"),
    }


def ec_diff(instance: dict, old: int, new: int) -> dict:
    """Before/after comparison between two calendar versions."""
    versions = {v.get("v"): v for v in instance.get("versions") or []}
    if old not in versions or new not in versions:
        raise CaseError(f"versions {old}/{new} are not in this instance")
    changes = []
    before_en = {e.get("en_id"): e for e in versions[old].get("entities") or []}
    after_en = {e.get("en_id"): e for e in versions[new].get("entities") or []}
    for en_id in sorted(set(before_en) | set(after_en)):
        if en_id not in before_en:
            changes.append({"en_id": en_id, "change": "added"})
            continue
        if en_id not in after_en:
            changes.append({"en_id": en_id, "change": "removed"})
            continue
        first, second = before_en[en_id], after_en[en_id]
        for key in ("name", "regime", "identifier"):
            if first.get(key) != second.get(key):
                changes.append(
                    {
                        "en_id": en_id,
                        "change": key,
                        "before": first.get(key),
                        "after": second.get(key),
                    }
                )
    before_cf = {f.get("cf_id"): f for f in versions[old].get("filings") or []}
    after_cf = {f.get("cf_id"): f for f in versions[new].get("filings") or []}
    for cf_id in sorted(set(before_cf) | set(after_cf)):
        if cf_id not in before_cf:
            changes.append({"cf_id": cf_id, "change": "added"})
            continue
        if cf_id not in after_cf:
            changes.append({"cf_id": cf_id, "change": "removed"})
            continue
        first, second = before_cf[cf_id], after_cf[cf_id]
        for key in (
            "en_id", "title", "kind", "due_date", "period",
            "notes", "status", "resolution",
        ):
            if first.get(key) != second.get(key):
                changes.append(
                    {
                        "cf_id": cf_id,
                        "change": key,
                        "before": first.get(key),
                        "after": second.get(key),
                    }
                )
    return {"old": old, "new": new, "changes": changes}


def ec_export(
    instance: dict,
    data: dict,
    version: int | None = None,
) -> dict:
    """Versioned JSON report; historical exports stay frozen."""
    from quality.records import evidence_fingerprint

    versions = instance.get("versions") or []
    if version is None:
        entities = instance.get("entities") or []
        filings = instance.get("filings") or []
        historical = False
    else:
        match = next((v for v in versions if v.get("v") == version), None)
        if match is None:
            raise CaseError(f"version {version} is not in this instance")
        entities = match.get("entities") or []
        filings = match.get("filings") or []
        historical = True
    out_filings = []
    for filing in filings:
        limitations = []
        if not historical:
            limitations.extend(cf_status(instance, filing)["reasons"])
        if filing.get("status") in _EC_NEXT:
            limitations.append(
                f"status: {filing.get('status')} "
                f"(next: {_EC_NEXT[filing['status']]})"
            )
        if not filing.get("due_date"):
            limitations.append("due date unknown")
        out_filings.append(
            {
                "cf_id": filing.get("cf_id"),
                "en_id": filing.get("en_id"),
                "title": filing.get("title"),
                "kind": filing.get("kind"),
                "due_date": filing.get("due_date"),
                "period": filing.get("period"),
                "notes": filing.get("notes"),
                "status": filing.get("status"),
                "resolution": filing.get("resolution"),
                "limitations": limitations,
            }
        )
    summary = {} if historical else ec_summary(instance)
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
        "entities": [
            {
                "en_id": e.get("en_id"),
                "name": e.get("name"),
                "regime": e.get("regime"),
                "identifier": e.get("identifier"),
            }
            for e in entities
        ],
        "filings": out_filings,
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
            "synthetic demonstration: entities and filings are "
            "reviewer-logged records",
            "dates are reviewer records, not docketed deadlines",
            "overdue is relative to an explicit as-of date only",
        ]
        + (["historical version: liveness was not recomputed"] if historical else []),
    }


register(
    TEMPLATE_EC,
    snapshot=ec_snapshot,
    open=open_ec_instance,
    diff=ec_diff,
    export=ec_export,
    export_takes_root=False,
    count_key="filings",
    review_keys=('entities', 'filings'),
)

__all__ = ['EC_KINDS', 'EC_STATUS', '_EC_NEXT', '_EC_DECISIONS', 'open_ec_instance', 'ec_get_entity', 'ec_get_filing', '_ec_version', '_ec_touch', '_next_en_id', '_next_cf_id', '_entity_pin', 'add_en', 'set_en', 'add_cf', 'set_cf', 'decide_cf', 'cf_status', 'ec_queue', 'ec_summary', 'ec_snapshot', 'ec_diff', 'ec_export']
