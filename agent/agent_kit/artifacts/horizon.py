"""regulatory horizon scan (one artifact slice; split from artifacts.py, behavior unchanged)."""

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


HZ_STATUS = ("watching", "assessing", "acted", "closed")
_HZ_NEXT = {
    "watching": "assess or close",
    "assessing": "act or close",
}
_HZ_DECISIONS = {
    "watching": ("assessing", "closed"),
    "assessing": ("acted", "closed", "watching"),
    "acted": ("watching",),
    "closed": ("watching",),
}


def _hz_source(data: dict, source_id: str) -> dict:
    source = (data.get("artifacts", {}) or {}).get(source_id)
    if not isinstance(source, dict):
        raise CaseError(f"unknown source instance {source_id!r}")
    if source.get("template") != TEMPLATE_JX:
        raise CaseError(
            f"{source_id} is a {source.get('template')}, not a jurisdiction matrix"
        )
    return source


def open_hz_instance(
    data: dict, run_id: str, title: str, source_id: str = ""
) -> dict:
    """Create a horizon scan over one matrix (pure; caller saves).

    Needs a jurisdiction-matrix instance in the same matter. An
    explicit source wins; otherwise the single matrix is used,
    and several require naming one.
    """
    matrices = [
        key
        for key, entry in (data.get("artifacts", {}) or {}).items()
        if isinstance(entry, dict) and entry.get("template") == TEMPLATE_JX
    ]
    if source_id:
        _hz_source(data, source_id)
    elif len(matrices) == 1:
        source_id = matrices[0]
    elif not matrices:
        raise CaseError("the horizon scan needs a jurisdiction-matrix instance")
    else:
        raise CaseError("several matrices exist; name one with --source")
    instance = {
        "id": _next_instance_id(data),
        "template": TEMPLATE_HZ,
        "template_version": TEMPLATE_HZ_VERSION,
        "matter": run_id,
        "title": title or "Regulatory Horizon Scan",
        "owner": "local",
        "viewers": ["local"],
        "created_at": now(),
        "source_instance": source_id,
        "watches": [],
        "versions": [],
        "review": _blank_review(),
        "history": [{"at": now(), "event": "opened", "detail": source_id}],
    }
    instance["versions"].append(
        {"v": 1, "at": now(), "reason": "opened", "watches": []}
    )
    return instance


def hz_get_watch(instance: dict, wh_id: str) -> dict:
    watch = next(
        (w for w in instance.get("watches") or [] if w.get("wh_id") == wh_id),
        None,
    )
    if watch is None:
        raise CaseError(f"unknown watch {wh_id!r}")
    return watch


def _hz_version(instance: dict, reason: str) -> None:
    versions = instance.setdefault("versions", [])
    versions.append(
        {
            "v": len(versions) + 1,
            "at": now(),
            "reason": reason,
            "watches": copy.deepcopy(instance.get("watches")),
        }
    )


def _hz_touch(instance: dict, event: str, detail: str) -> None:
    instance["history"].append({"at": now(), "event": event, "detail": detail})
    instance["review"] = _blank_review()


def _next_wh_id(instance: dict) -> str:
    taken = {w.get("wh_id", "") for w in instance.get("watches") or []}
    seq = 1
    while f"WH{seq}" in taken:
        seq += 1
    return f"WH{seq}"


def _hz_cell(data: dict, instance: dict, cell_id: str) -> dict | None:
    if not cell_id:
        return None
    source = _hz_source(data, instance.get("source_instance") or "")
    return next(
        (c for c in source.get("cells") or [] if c.get("cell_id") == cell_id),
        None,
    )


def _cell_watch_pin(cell: dict) -> str:
    authority = cell.get("authority") or {}
    return _stable(
        {
            "proposition": cell.get("proposition"),
            "citation": authority.get("citation"),
            "effective_date": authority.get("effective_date"),
            "caveat": cell.get("caveat"),
            "review_status": cell.get("review_status"),
        }
    )


def _hz_check_date(value: str, field: str, required: bool) -> str:
    value = str(value or "").strip()
    if not value:
        if required:
            raise CaseError(f"a watch needs its {field}")
        return ""
    if not _valid_date(value):
        raise CaseError(f"{field} must be YYYY-MM-DD, got {value!r}")
    return value


def add_wh(
    instance: dict,
    data: dict,
    title: str,
    summary: str,
    regime: str,
    source_desc: str,
    observed_date: str,
    effective_date: str = "",
    last_checked: str = "",
    relevance: str = "",
    assignee: str = "",
    cell_id: str = "",
) -> dict:
    """Log one regulatory development (pure; caller saves).

    Regimes are registry keys; anything else fails closed.
    The observed date is required; the effective date stays
    unknown until the reviewer states it. A cell link must
    name a live cell in the source matrix.
    """
    from agent_kit import sources

    if not str(title or "").strip():
        raise CaseError("a watch needs its title")
    if not str(summary or "").strip():
        raise CaseError("a watch needs its summary")
    if regime not in sources.REGIONS:
        raise CaseError(
            f"regime {regime!r} is not registered "
            f"(one of: {', '.join(sorted(sources.REGIONS))})"
        )
    if not str(source_desc or "").strip():
        raise CaseError("a watch must name where it was observed")
    observed = _hz_check_date(observed_date, "observed date", True)
    effective = _hz_check_date(effective_date, "effective date", False)
    checked = _hz_check_date(last_checked, "last-checked date", False)
    cell_id = str(cell_id or "").strip()
    cell = _hz_cell(data, instance, cell_id)
    if cell_id and cell is None:
        raise CaseError(f"cell {cell_id!r} is not in the source matrix")
    if cell is not None and cell.get("retracted"):
        raise CaseError(f"cell {cell_id!r} was retracted")
    watch = {
        "wh_id": _next_wh_id(instance),
        "title": str(title).strip(),
        "summary": str(summary).strip(),
        "regime": regime,
        "source_desc": str(source_desc).strip(),
        "observed_date": observed,
        "effective_date": effective,
        "last_checked": checked,
        "relevance": str(relevance or "").strip(),
        "assignee": str(assignee or "").strip(),
        "cell_id": cell_id,
        "cell_pin": _cell_watch_pin(cell) if cell is not None else None,
        "status": "watching",
        "resolution": "",
        "history": [{"at": now(), "change": "logged", "detail": ""}],
    }
    instance.setdefault("watches", []).append(watch)
    _hz_version(instance, f"logged {watch['wh_id']}")
    _hz_touch(instance, "watch_logged", watch["wh_id"])
    return watch


def set_wh(
    instance: dict,
    data: dict,
    wh_id: str,
    title: str | None = None,
    summary: str | None = None,
    regime: str | None = None,
    source_desc: str | None = None,
    observed_date: str | None = None,
    effective_date: str | None = None,
    last_checked: str | None = None,
    relevance: str | None = None,
    assignee: str | None = None,
    cell_id: str | None = None,
) -> dict:
    """Revise a watch (pure; caller saves).

    Acted and closed watches are locked: reopen them first.
    Changing the cell link re-pins the new cell; clearing it
    drops the link. Every field is re-validated.
    """
    from agent_kit import sources

    watch = hz_get_watch(instance, wh_id)
    if watch.get("status") in ("acted", "closed"):
        raise CaseError(f"{wh_id} is {watch['status']}; reopen it first")
    if title is not None:
        if not str(title).strip():
            raise CaseError("a watch needs its title")
        watch["title"] = str(title).strip()
    if summary is not None:
        if not str(summary).strip():
            raise CaseError("a watch needs its summary")
        watch["summary"] = str(summary).strip()
    if regime is not None:
        if regime not in sources.REGIONS:
            raise CaseError(
                f"regime {regime!r} is not registered "
                f"(one of: {', '.join(sorted(sources.REGIONS))})"
            )
        watch["regime"] = regime
    if source_desc is not None:
        if not str(source_desc).strip():
            raise CaseError("a watch must name where it was observed")
        watch["source_desc"] = str(source_desc).strip()
    if observed_date is not None:
        watch["observed_date"] = _hz_check_date(
            observed_date, "observed date", True
        )
    if effective_date is not None:
        watch["effective_date"] = _hz_check_date(
            effective_date, "effective date", False
        )
    if last_checked is not None:
        watch["last_checked"] = _hz_check_date(
            last_checked, "last-checked date", False
        )
    if relevance is not None:
        watch["relevance"] = str(relevance).strip()
    if assignee is not None:
        watch["assignee"] = str(assignee).strip()
    if cell_id is not None:
        cell_id = str(cell_id).strip()
        cell = _hz_cell(data, instance, cell_id)
        if cell_id and cell is None:
            raise CaseError(f"cell {cell_id!r} is not in the source matrix")
        if cell is not None and cell.get("retracted"):
            raise CaseError(f"cell {cell_id!r} was retracted")
        watch["cell_id"] = cell_id
        watch["cell_pin"] = _cell_watch_pin(cell) if cell is not None else None
    watch["history"].append({"at": now(), "change": "revised", "detail": wh_id})
    _hz_version(instance, f"revised {wh_id}")
    _hz_touch(instance, "watch_revised", wh_id)
    return watch


def decide_wh(
    instance: dict, wh_id: str, decision: str, reason: str = ""
) -> dict:
    """Move a watch through its lifecycle (pure; caller saves).

    Assessing, acting, and closing each need a stated reason;
    acting also needs a stated relevance to the matter.
    """
    watch = hz_get_watch(instance, wh_id)
    status = watch.get("status") or "watching"
    if decision not in (_HZ_DECISIONS.get(status) or ()):
        allowed = ", ".join(_HZ_DECISIONS.get(status) or ())
        raise CaseError(f"{wh_id} is {status}; allowed next: {allowed or 'none'}")
    if decision != "watching" and not str(reason or "").strip():
        raise CaseError(f"{decision} needs a stated reason")
    if decision == "acted" and not watch.get("relevance"):
        raise CaseError(f"{wh_id} needs a stated relevance before acting")
    watch["status"] = decision
    watch["resolution"] = str(reason or "").strip()
    watch["history"].append(
        {"at": now(), "change": decision, "detail": str(reason or "").strip()}
    )
    _hz_version(instance, f"{decision} {wh_id}")
    _hz_touch(instance, f"watch_{decision}", wh_id)
    return watch


def wh_status(instance: dict, data: dict, watch: dict) -> dict:
    """Current, stale, or orphaned for one watch.

    Only the cell link can stale a watch: revising the
    linked cell marks it stale, retracting it orphans it.
    Unknown dates are limitations, not staleness.
    """
    reasons: list[str] = []
    state = "current"
    cell_id = watch.get("cell_id") or ""
    if cell_id:
        cell = _hz_cell(data, instance, cell_id)
        if cell is None or cell.get("retracted"):
            state = "orphaned"
            reasons.append(f"linked cell {cell_id} is gone or retracted")
        elif _cell_watch_pin(cell) != watch.get("cell_pin"):
            state = "stale"
            reasons.append(f"linked cell {cell_id} changed since this watch")
    return {"state": state, "reasons": reasons}


def hz_queue(instance: dict, data: dict) -> list[dict]:
    """Watching and assessing items with their link state."""
    queue = []
    for watch in instance.get("watches") or []:
        if watch.get("status") not in ("watching", "assessing"):
            continue
        status = wh_status(instance, data, watch)
        queue.append(
            {
                "wh_id": watch.get("wh_id"),
                "title": watch.get("title"),
                "status": watch.get("status"),
                "regime": watch.get("regime"),
                "assignee": watch.get("assignee"),
                "link_state": status["state"],
                "reasons": status["reasons"],
            }
        )
    return queue


def hz_summary(instance: dict, data: dict) -> dict:
    """Counts, queue, unknowns, and stale watches."""
    watches = instance.get("watches") or []
    by_status: dict[str, int] = {}
    for watch in watches:
        status = watch.get("status") or "watching"
        by_status[status] = by_status.get(status, 0) + 1
    unknown_effective = [
        w.get("wh_id") for w in watches if not w.get("effective_date")
    ]
    unchecked = [w.get("wh_id") for w in watches if not w.get("last_checked")]
    stale = []
    for watch in watches:
        status = wh_status(instance, data, watch)
        if status["state"] != "current":
            stale.append(
                {
                    "wh_id": watch.get("wh_id"),
                    "state": status["state"],
                    "reasons": status["reasons"],
                }
            )
    return {
        "counts": by_status,
        "queue": hz_queue(instance, data),
        "unknown_effective": unknown_effective,
        "unchecked": unchecked,
        "stale": stale,
    }


def hz_snapshot(instance: dict, data: dict) -> dict:
    """Content hash a horizon review binds (recomputed, never stored)."""
    from quality.records import evidence_fingerprint

    return {
        "content": _stable({"watches": instance.get("watches")}),
        "evidence": evidence_fingerprint(data),
        "template_version": instance.get("template_version"),
    }


def hz_diff(instance: dict, old: int, new: int) -> dict:
    """Before/after comparison between two scan versions."""
    versions = {v.get("v"): v for v in instance.get("versions") or []}
    if old not in versions or new not in versions:
        raise CaseError(f"versions {old}/{new} are not in this instance")
    before = {w.get("wh_id"): w for w in versions[old].get("watches") or []}
    after = {w.get("wh_id"): w for w in versions[new].get("watches") or []}
    changes = []
    for wh_id in sorted(set(before) | set(after)):
        if wh_id not in before:
            changes.append({"wh_id": wh_id, "change": "added"})
            continue
        if wh_id not in after:
            changes.append({"wh_id": wh_id, "change": "removed"})
            continue
        first, second = before[wh_id], after[wh_id]
        for key in (
            "title", "summary", "regime", "source_desc",
            "observed_date", "effective_date", "last_checked",
            "relevance", "assignee", "cell_id", "status", "resolution",
        ):
            if first.get(key) != second.get(key):
                changes.append(
                    {
                        "wh_id": wh_id,
                        "change": key,
                        "before": first.get(key),
                        "after": second.get(key),
                    }
                )
    return {"old": old, "new": new, "changes": changes}


def hz_export(
    instance: dict,
    data: dict,
    version: int | None = None,
) -> dict:
    """Versioned JSON report; historical exports stay frozen."""
    from quality.records import evidence_fingerprint

    versions = instance.get("versions") or []
    if version is None:
        watches = instance.get("watches") or []
        historical = False
    else:
        match = next((v for v in versions if v.get("v") == version), None)
        if match is None:
            raise CaseError(f"version {version} is not in this instance")
        watches = match.get("watches") or []
        historical = True
    out_watches = []
    for watch in watches:
        limitations = []
        if not historical:
            limitations.extend(wh_status(instance, data, watch)["reasons"])
        if watch.get("status") in _HZ_NEXT:
            limitations.append(
                f"status: {watch.get('status')} "
                f"(next: {_HZ_NEXT[watch['status']]})"
            )
        if not watch.get("effective_date"):
            limitations.append("effective date unknown")
        if not watch.get("last_checked"):
            limitations.append("never re-checked")
        out_watches.append(
            {
                "wh_id": watch.get("wh_id"),
                "title": watch.get("title"),
                "summary": watch.get("summary"),
                "regime": watch.get("regime"),
                "source_desc": watch.get("source_desc"),
                "observed_date": watch.get("observed_date"),
                "effective_date": watch.get("effective_date"),
                "last_checked": watch.get("last_checked"),
                "relevance": watch.get("relevance"),
                "assignee": watch.get("assignee"),
                "cell_id": watch.get("cell_id"),
                "status": watch.get("status"),
                "resolution": watch.get("resolution"),
                "limitations": limitations,
            }
        )
    summary = {} if historical else hz_summary(instance, data)
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
        "watches": out_watches,
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
            "synthetic demonstration: watches are reviewer-logged "
            "records, never live monitoring",
            "unknown effective dates never default to observed dates",
            "this export states what was watched and assessed, "
            "not what the law currently is",
        ]
        + (["historical version: liveness was not recomputed"] if historical else []),
    }


register(
    TEMPLATE_HZ,
    snapshot=hz_snapshot,
    open=open_hz_instance,
    diff=hz_diff,
    export=hz_export,
    export_takes_root=False,
    count_key="watches",
    review_keys=('watches',),
)

__all__ = ['HZ_STATUS', '_HZ_NEXT', '_HZ_DECISIONS', '_hz_source', 'open_hz_instance', 'hz_get_watch', '_hz_version', '_hz_touch', '_next_wh_id', '_hz_cell', '_cell_watch_pin', '_hz_check_date', 'add_wh', 'set_wh', 'decide_wh', 'wh_status', 'hz_queue', 'hz_summary', 'hz_snapshot', 'hz_diff', 'hz_export']
