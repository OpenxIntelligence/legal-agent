"""jurisdiction matrix (synthetic demonstration) (one artifact slice; split from artifacts.py, behavior unchanged)."""

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
    data_mode,
    register,
)


JX_LAYERS = ("substantive", "procedure", "evidence")
JX_REVIEW = ("unreviewed", "confirmed", "flagged")


def open_jx_instance(
    data: dict, run_id: str, title: str, source_id: str = ""
) -> dict:
    """Create a matrix instance over checklist issues (pure; caller saves).

    Needs issues, not branches or authority: cells are added and
    resolved by the reviewer. Nothing about applicability is
    assumed at open time.
    """
    issues = [
        key for key, entry in (data.get("checklist", {}) or {}).items()
        if isinstance(entry, dict)
    ]
    if not issues:
        raise CaseError("the matrix needs checklist issues")
    instance = {
        "id": _next_instance_id(data),
        "template": TEMPLATE_JX,
        "template_version": TEMPLATE_JX_VERSION,
        "matter": run_id,
        "title": title or "Jurisdiction Survey Matrix",
        "owner": "local",
        "viewers": ["local"],
        "created_at": now(),
        "cells": [],
        "versions": [],
        "review": _blank_review(),
        "history": [{"at": now(), "event": "opened", "detail": ""}],
    }
    instance["versions"].append(
        {"v": 1, "at": now(), "reason": "opened", "cells": []}
    )
    return instance


def jx_get_cell(instance: dict, cell_id: str) -> dict:
    cell = next(
        (c for c in instance.get("cells") or [] if c.get("cell_id") == cell_id),
        None,
    )
    if cell is None:
        raise CaseError(f"unknown matrix cell {cell_id!r}")
    return cell


def _jx_version(instance: dict, reason: str) -> None:
    versions = instance.setdefault("versions", [])
    versions.append(
        {
            "v": len(versions) + 1,
            "at": now(),
            "reason": reason,
            "cells": copy.deepcopy(instance.get("cells")),
        }
    )


def _jx_touch(instance: dict, event: str, detail: str) -> None:
    instance["history"].append({"at": now(), "event": event, "detail": detail})
    instance["review"] = _blank_review()


def _next_cell_id(instance: dict) -> str:
    taken = {cell.get("cell_id", "") for cell in instance.get("cells") or []}
    seq = 1
    while f"C{seq}" in taken:
        seq += 1
    return f"C{seq}"


def add_cell(
    instance: dict, data: dict, issue_id: str, regime: str, layer: str
) -> dict:
    """Add one issue × regime × layer cell (pure; caller saves).

    Regimes are registry keys; anything else fails closed.
    """
    from agent_kit import sources

    checklist = data.get("checklist", {}) or {}
    if issue_id not in checklist:
        raise CaseError(f"issue {issue_id!r} is not on the checklist")
    if regime not in sources.REGIONS:
        raise CaseError(
            f"regime {regime!r} is not registered "
            f"(one of: {', '.join(sorted(sources.REGIONS))})"
        )
    if layer not in JX_LAYERS:
        raise CaseError(f"layer must be one of: {', '.join(JX_LAYERS)}")
    for cell in instance.get("cells") or []:
        if (
            not cell.get("retracted")
            and cell.get("issue_id") == issue_id
            and cell.get("regime") == regime
            and cell.get("layer") == layer
        ):
            raise CaseError(f"{issue_id} × {regime} × {layer} already has a cell")
    cell = {
        "cell_id": _next_cell_id(instance),
        "issue_id": issue_id,
        "regime": regime,
        "layer": layer,
        "proposition": "",
        "authority": {"citation": "", "effective_date": "", "last_checked": ""},
        "caveat": "",
        "branch_id": "",
        "review_status": "unreviewed",
        "retracted": False,
        "inputs": _cell_inputs(data, ""),
        "history": [{"at": now(), "change": "added", "detail": ""}],
    }
    instance.setdefault("cells", []).append(cell)
    _jx_version(instance, f"added {cell['cell_id']}")
    _jx_touch(instance, "cell_added", cell["cell_id"])
    return cell


def _valid_date(value: str) -> bool:
    import datetime as _datetime

    try:
        _datetime.date.fromisoformat(value)
    except (TypeError, ValueError):
        return False
    return True


def set_cell(instance: dict, data: dict, cell_id: str, **fields: Any) -> dict:
    """Set a cell's proposition, authority, or caveat.

    Authority fields stay individually optional: an empty
    effective date is unknown, never today. Malformed dates are
    refused rather than stored.
    """
    cell = jx_get_cell(instance, cell_id)
    if cell.get("retracted"):
        raise CaseError(f"{cell_id} is retracted")
    changed = []
    if "proposition" in fields and fields["proposition"] is not None:
        value = str(fields["proposition"])
        if value != cell.get("proposition"):
            cell["proposition"] = value
            changed.append("proposition")
    authority = cell.setdefault("authority", {})
    for key in ("citation", "effective_date", "last_checked"):
        if key in fields and fields[key] is not None:
            value = str(fields[key])
            if key in ("effective_date", "last_checked") and value and not _valid_date(value):
                raise CaseError(f"{value!r} is not a YYYY-MM-DD date")
            if value != authority.get(key):
                authority[key] = value
                changed.append(key)
    if "caveat" in fields and fields["caveat"] is not None:
        value = str(fields["caveat"])
        if value != cell.get("caveat"):
            cell["caveat"] = value
            changed.append("caveat")
    if not changed:
        return cell
    cell["inputs"] = _cell_inputs(data, cell.get("branch_id") or "")
    cell["history"].append(
        {"at": now(), "change": "set", "detail": ", ".join(changed)}
    )
    _jx_version(instance, f"set {cell_id}")
    _jx_touch(instance, "cell_set", cell_id)
    return cell


def link_cell_branch(instance: dict, data: dict, cell_id: str, branch_id: str) -> dict:
    """Bind a cell to the legal branch deciding its applicability.

    The branch must govern the cell's own issue: a readiness
    branch cannot resolve a cutover cell.
    """
    cell = jx_get_cell(instance, cell_id)
    branches = data.get("branches", {}) or {}
    branch = branches.get(branch_id)
    if not isinstance(branch, dict):
        raise CaseError(f"unknown branch {branch_id!r}")
    if branch.get("issue") != cell.get("issue_id"):
        raise CaseError(
            f"branch {branch_id} governs {branch.get('issue')}, "
            f"not {cell.get('issue_id')}"
        )
    cell["branch_id"] = branch_id
    cell["inputs"] = _cell_inputs(data, branch_id)
    cell["history"].append(
        {"at": now(), "change": "linked", "detail": branch_id}
    )
    _jx_version(instance, f"linked {cell_id} to {branch_id}")
    _jx_touch(instance, "cell_linked", cell_id)
    return cell


def flag_cell(
    instance: dict, cell_id: str, review_status: str, note: str = ""
) -> dict:
    """Mark a cell confirmed or flagged (unreviewed is the default)."""
    cell = jx_get_cell(instance, cell_id)
    if review_status not in JX_REVIEW:
        raise CaseError(f"review status must be one of: {', '.join(JX_REVIEW)}")
    cell["review_status"] = review_status
    cell["history"].append(
        {"at": now(), "change": "flagged", "detail": f"{review_status} {note}".strip()}
    )
    _jx_version(instance, f"flagged {cell_id} {review_status}")
    _jx_touch(instance, "cell_flagged", cell_id)
    return cell


def retract_cell(instance: dict, cell_id: str, reason: str) -> dict:
    """Retire a cell with its reason; history keeps it visible."""
    cell = jx_get_cell(instance, cell_id)
    if not str(reason or "").strip():
        raise CaseError("retracting a cell needs its reason")
    cell["retracted"] = True
    cell["history"].append({"at": now(), "change": "retracted", "detail": str(reason)})
    _jx_version(instance, f"retracted {cell_id}")
    _jx_touch(instance, "cell_retracted", cell_id)
    return cell


def _cell_inputs(data: dict, branch_id: str) -> dict:
    branch = (data.get("branches", {}) or {}).get(branch_id)
    noted = data.get("sources", {}) or {}
    return {
        "branch": _stable(
            {
                "status": (branch or {}).get("status"),
                "basis": (branch or {}).get("basis"),
                "basis_kind": (branch or {}).get("basis_kind"),
            }
        )
        if branch_id
        else None,
        "matter": _stable((data.get("matter", {}) or {}).get("fields", {})),
        "sources": _stable(
            {
                doc: (entry or {}).get("version")
                for doc, entry in sorted(noted.items())
                if isinstance(entry, dict)
            }
        ),
    }


def cell_staleness(data: dict, cell: dict) -> dict:
    """Whether a cell's applicability inputs changed since it was set."""
    stored = cell.get("inputs") or {}
    if not stored:
        return {"stale": None, "reasons": []}
    current = _cell_inputs(data, cell.get("branch_id") or "")
    reasons = []
    if stored.get("branch") != current["branch"]:
        reasons.append("the linked branch changed")
    if stored.get("matter") != current["matter"]:
        reasons.append("matter context changed")
    if stored.get("sources") != current["sources"]:
        reasons.append("a noted source version changed")
    return {"stale": bool(reasons), "reasons": reasons}


def jx_readiness(data: dict, instance: dict) -> dict:
    """Which cells block a definitive comparison, and why.

    A cell is resolved only with a linked known branch, a
    citation, a known effective date, a confirmation, and
    current inputs. Anything less keeps the matrix provisional
    with the exact gaps listed — never a quiet pass.
    """
    branches = data.get("branches", {}) or {}
    blockers = []
    resolved = 0
    live = [c for c in instance.get("cells") or [] if not c.get("retracted")]
    for cell in live:
        reasons = []
        branch = branches.get(cell.get("branch_id") or "")
        if not cell.get("branch_id") or not isinstance(branch, dict):
            reasons.append("no linked branch")
        elif branch.get("status") != "known":
            reasons.append(f"branch {cell['branch_id']} is {branch.get('status')}")
        authority = cell.get("authority") or {}
        if not str(authority.get("citation") or "").strip():
            reasons.append("no authority citation")
        if not str(authority.get("effective_date") or "").strip():
            reasons.append("effective date unknown")
        if cell.get("review_status") != "confirmed":
            reasons.append(f"review: {cell.get('review_status', 'unreviewed')}")
        stale = cell_staleness(data, cell)
        if stale["stale"]:
            reasons.append(f"stale: {'; '.join(stale['reasons'])}")
        if reasons:
            blockers.append({"cell_id": cell.get("cell_id"), "reasons": reasons})
        else:
            resolved += 1
    return {
        "resolved": resolved,
        "total": len(live),
        "provisional": bool(blockers),
        "blockers": blockers,
    }


def jx_snapshot(instance: dict, data: dict) -> dict:
    """Content hash a matrix review binds (recomputed, never stored)."""
    from quality.records import evidence_fingerprint

    return {
        "content": _stable(instance.get("cells")),
        "evidence": evidence_fingerprint(data),
        "template_version": instance.get("template_version"),
    }


def jx_diff(instance: dict, old: int, new: int) -> dict:
    """Before/after comparison between two matrix versions."""
    versions = {v.get("v"): v for v in instance.get("versions") or []}
    if old not in versions or new not in versions:
        raise CaseError(f"versions {old}/{new} are not in this instance")
    before = {c.get("cell_id"): c for c in versions[old].get("cells") or []}
    after = {c.get("cell_id"): c for c in versions[new].get("cells") or []}
    changes = []
    for cell_id in sorted(set(before) | set(after)):
        if cell_id not in before:
            changes.append({"cell_id": cell_id, "change": "added"})
            continue
        if cell_id not in after:
            changes.append({"cell_id": cell_id, "change": "removed"})
            continue
        first, second = before[cell_id], after[cell_id]
        for key in (
            "proposition", "authority", "caveat", "branch_id",
            "review_status", "retracted",
        ):
            if first.get(key) != second.get(key):
                changes.append(
                    {
                        "cell_id": cell_id,
                        "change": key,
                        "before": first.get(key),
                        "after": second.get(key),
                    }
                )
    return {"old": old, "new": new, "changes": changes}


def jx_export(
    instance: dict, data: dict, version: int | None = None
) -> dict:
    """Versioned matrix report with readiness, limits, approval scope."""
    from quality.records import evidence_fingerprint

    versions = instance.get("versions") or []
    if version is None:
        cells = instance.get("cells") or []
        historical = False
    else:
        match = next((v for v in versions if v.get("v") == version), None)
        if match is None:
            raise CaseError(f"version {version} is not in this instance")
        cells = match.get("cells") or []
        historical = True
    branches = data.get("branches", {}) or {}
    out_cells = []
    for cell in cells:
        branch = branches.get(cell.get("branch_id") or "")
        authority = cell.get("authority") or {}
        limitations = []
        if not historical:
            stale = cell_staleness(data, cell)
            if stale["stale"]:
                limitations.append(f"stale: {'; '.join(stale['reasons'])}")
        if cell.get("retracted"):
            limitations.append("retracted")
        else:
            if not isinstance(branch, dict):
                limitations.append("no linked branch")
            elif branch.get("status") != "known":
                limitations.append(f"applicability {branch.get('status')}")
            if not str(authority.get("effective_date") or "").strip():
                limitations.append("effective date unknown")
            if cell.get("review_status") != "confirmed":
                limitations.append(f"review: {cell.get('review_status', 'unreviewed')}")
        if cell.get("caveat"):
            limitations.append(f"caveat: {cell['caveat']}")
        out_cells.append(
            {
                "cell_id": cell.get("cell_id"),
                "issue_id": cell.get("issue_id"),
                "regime": cell.get("regime"),
                "layer": cell.get("layer"),
                "proposition": cell.get("proposition"),
                "authority": authority,
                "branch": {
                    "id": cell.get("branch_id"),
                    "status": (branch or {}).get("status"),
                    "basis": (branch or {}).get("basis"),
                }
                if cell.get("branch_id")
                else None,
                "review_status": cell.get("review_status"),
                "retracted": bool(cell.get("retracted")),
                "limitations": limitations,
            }
        )
    readiness = {} if historical else jx_readiness(data, instance)
    review = instance.get("review") or {}
    return {
        "template": instance.get("template"),
        "template_version": instance.get("template_version"),
        "instance": instance.get("id"),
        "matter": instance.get("matter"),
        "data_mode": data_mode(instance),
        "title": instance.get("title"),
        "exported_version": version or (versions[-1].get("v") if versions else 1),
        "historical": historical,
        "evidence_fingerprint": evidence_fingerprint(data) if not historical else None,
        "readiness": readiness,
        "cells": out_cells,
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
            "synthetic demonstration: propositions and citations are fixture "
            "text, not legal research",
            "a regime key is not a holding that the regime governs",
            "unknown effective dates and applicability gate definitive comparison",
        ]
        + (["historical version: liveness was not recomputed"] if historical else []),
    }


register(
    TEMPLATE_JX,
    snapshot=jx_snapshot,
    open=open_jx_instance,
    diff=jx_diff,
    export=jx_export,
    export_takes_root=False,
    count_key="cells",
    review_keys=('cells',),
)

__all__ = ['JX_LAYERS', 'JX_REVIEW', 'open_jx_instance', 'jx_get_cell', '_jx_version', '_jx_touch', '_next_cell_id', 'add_cell', '_valid_date', 'set_cell', 'link_cell_branch', 'flag_cell', 'retract_cell', '_cell_inputs', 'cell_staleness', 'jx_readiness', 'jx_snapshot', 'jx_diff', 'jx_export']
