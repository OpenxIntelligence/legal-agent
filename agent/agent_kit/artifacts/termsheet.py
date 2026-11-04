"""venture term-sheet comparison (one artifact slice; split from artifacts.py, behavior unchanged)."""

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


TS_STATUS = ("open", "aligned", "divergent", "resolved")
_TS_NEXT = {
    "open": "record positions, then mark aligned or divergent",
    "divergent": "resolve or reopen",
}
_TS_DECISIONS = {
    "open": ("aligned", "divergent"),
    "aligned": ("open",),
    "divergent": ("resolved", "open"),
    "resolved": ("open",),
}


def open_ts_instance(
    data: dict, run_id: str, title: str, source_id: str = ""
) -> dict:
    """Create a term-sheet comparison (pure; caller saves).

    No prerequisites: sheets and terms are reviewer
    records, so an empty matter can already log them.
    """
    instance = {
        "id": _next_instance_id(data),
        "template": TEMPLATE_TS,
        "template_version": TEMPLATE_TS_VERSION,
        "matter": run_id,
        "title": title or "Venture Term-Sheet Comparison",
        "owner": "local",
        "viewers": ["local"],
        "created_at": now(),
        "sheets": [],
        "terms": [],
        "versions": [],
        "review": _blank_review(),
        "history": [{"at": now(), "event": "opened", "detail": ""}],
    }
    instance["versions"].append(
        {"v": 1, "at": now(), "reason": "opened", "sheets": [], "terms": []}
    )
    return instance


def ts_get_sheet(instance: dict, ts_id: str) -> dict:
    sheet = next(
        (s for s in instance.get("sheets") or [] if s.get("ts_id") == ts_id),
        None,
    )
    if sheet is None:
        raise CaseError(f"unknown sheet {ts_id!r}")
    return sheet


def ts_get_term(instance: dict, tm_id: str) -> dict:
    term = next(
        (t for t in instance.get("terms") or [] if t.get("tm_id") == tm_id),
        None,
    )
    if term is None:
        raise CaseError(f"unknown term {tm_id!r}")
    return term


def _ts_version(instance: dict, reason: str) -> None:
    versions = instance.setdefault("versions", [])
    versions.append(
        {
            "v": len(versions) + 1,
            "at": now(),
            "reason": reason,
            "sheets": copy.deepcopy(instance.get("sheets")),
            "terms": copy.deepcopy(instance.get("terms")),
        }
    )


def _ts_touch(instance: dict, event: str, detail: str) -> None:
    instance["history"].append({"at": now(), "event": event, "detail": detail})
    instance["review"] = _blank_review()


def _next_ts_id(instance: dict) -> str:
    taken = {s.get("ts_id", "") for s in instance.get("sheets") or []}
    seq = 1
    while f"TS{seq}" in taken:
        seq += 1
    return f"TS{seq}"


def _next_tm_id(instance: dict) -> str:
    taken = {t.get("tm_id", "") for t in instance.get("terms") or []}
    seq = 1
    while f"TM{seq}" in taken:
        seq += 1
    return f"TM{seq}"


def _sheet_pin(sheet: dict) -> str:
    return _stable(
        {
            "label": sheet.get("label"),
            "party": sheet.get("party"),
            "date": sheet.get("date"),
            "notes": sheet.get("notes"),
        }
    )


def add_ts(
    instance: dict,
    label: str,
    party: str = "",
    date: str = "",
    notes: str = "",
) -> dict:
    """Log one term sheet (pure; caller saves).

    The label is required; the sheet date stays
    unknown until the reviewer states it.
    """
    if not str(label or "").strip():
        raise CaseError("a sheet needs its label")
    date = str(date or "").strip()
    if date and not _valid_date(date):
        raise CaseError(f"sheet date must be YYYY-MM-DD, got {date!r}")
    sheet = {
        "ts_id": _next_ts_id(instance),
        "label": str(label).strip(),
        "party": str(party or "").strip(),
        "date": date,
        "notes": str(notes or "").strip(),
        "history": [{"at": now(), "change": "logged", "detail": ""}],
    }
    instance.setdefault("sheets", []).append(sheet)
    _ts_version(instance, f"logged {sheet['ts_id']}")
    _ts_touch(instance, "sheet_logged", sheet["ts_id"])
    return sheet


def set_ts(
    instance: dict,
    ts_id: str,
    label: str | None = None,
    party: str | None = None,
    date: str | None = None,
    notes: str | None = None,
) -> dict:
    """Revise a sheet (pure; caller saves).

    Revisions do not rewrite history: positions
    pinned to the old sheet go stale with reasons
    until re-recorded.
    """
    sheet = ts_get_sheet(instance, ts_id)
    if label is not None:
        if not str(label).strip():
            raise CaseError("a sheet needs its label")
        sheet["label"] = str(label).strip()
    if party is not None:
        sheet["party"] = str(party).strip()
    if date is not None:
        date = str(date).strip()
        if date and not _valid_date(date):
            raise CaseError(f"sheet date must be YYYY-MM-DD, got {date!r}")
        sheet["date"] = date
    if notes is not None:
        sheet["notes"] = str(notes).strip()
    sheet["history"].append({"at": now(), "change": "revised", "detail": ts_id})
    _ts_version(instance, f"revised {ts_id}")
    _ts_touch(instance, "sheet_revised", ts_id)
    return sheet


def add_tm(instance: dict, topic: str, proposition: str) -> dict:
    """Open one comparison term (pure; caller saves)."""
    if not str(topic or "").strip():
        raise CaseError("a term needs its topic")
    if not str(proposition or "").strip():
        raise CaseError("a term needs its proposition")
    term = {
        "tm_id": _next_tm_id(instance),
        "topic": str(topic).strip(),
        "proposition": str(proposition).strip(),
        "positions": [],
        "status": "open",
        "resolution": "",
        "history": [{"at": now(), "change": "opened", "detail": ""}],
    }
    instance.setdefault("terms", []).append(term)
    _ts_version(instance, f"opened {term['tm_id']}")
    _ts_touch(instance, "term_opened", term["tm_id"])
    return term


def set_tm(
    instance: dict,
    tm_id: str,
    topic: str | None = None,
    proposition: str | None = None,
) -> dict:
    """Revise a term's wording (pure; caller saves).

    Decided terms are locked: reopen them first, so a
    verdict can never silently change meaning.
    """
    term = ts_get_term(instance, tm_id)
    if term.get("status") != "open":
        raise CaseError(f"{tm_id} is {term['status']}; reopen it first")
    if topic is not None:
        if not str(topic).strip():
            raise CaseError("a term needs its topic")
        term["topic"] = str(topic).strip()
    if proposition is not None:
        if not str(proposition).strip():
            raise CaseError("a term needs its proposition")
        term["proposition"] = str(proposition).strip()
    term["history"].append({"at": now(), "change": "revised", "detail": tm_id})
    _ts_version(instance, f"revised {tm_id}")
    _ts_touch(instance, "term_revised", tm_id)
    return term


def position_tm(instance: dict, tm_id: str, ts_id: str, text: str) -> dict:
    """Record one sheet's position on a term (pure; caller saves).

    Re-recording supersedes the old position with a
    fresh sheet pin, which also clears staleness from
    a sheet revision the reviewer has now confirmed.
    Decided terms are locked: reopen them first.
    """
    term = ts_get_term(instance, tm_id)
    if term.get("status") != "open":
        raise CaseError(f"{tm_id} is {term['status']}; reopen it first")
    sheet = ts_get_sheet(instance, ts_id)
    if not str(text or "").strip():
        raise CaseError("a position needs its text")
    positions = term.setdefault("positions", [])
    entry = {"ts_id": ts_id, "text": str(text).strip(), "pin": _sheet_pin(sheet)}
    for i, old in enumerate(positions):
        if old.get("ts_id") == ts_id:
            positions[i] = entry
            break
    else:
        positions.append(entry)
    term["history"].append(
        {"at": now(), "change": "positioned", "detail": ts_id}
    )
    _ts_version(instance, f"positioned {tm_id} {ts_id}")
    _ts_touch(instance, "term_positioned", f"{tm_id} {ts_id}")
    return term


def tm_missing(instance: dict, term: dict) -> list[str]:
    """Sheets with no recorded position on this term."""
    placed = {p.get("ts_id") for p in term.get("positions") or []}
    return sorted(
        s.get("ts_id", "")
        for s in instance.get("sheets") or []
        if s.get("ts_id") not in placed
    )


def decide_tm(
    instance: dict, tm_id: str, decision: str, reason: str = ""
) -> dict:
    """Record a term verdict (pure; caller saves).

    Every verdict except reopening needs a stated
    reason and a position from every logged sheet: a
    missing position never counts as agreement.
    """
    term = ts_get_term(instance, tm_id)
    status = term.get("status") or "open"
    if decision not in (_TS_DECISIONS.get(status) or ()):
        allowed = ", ".join(_TS_DECISIONS.get(status) or ())
        raise CaseError(f"{tm_id} is {status}; allowed next: {allowed or 'none'}")
    if decision != "open":
        if not str(reason or "").strip():
            raise CaseError(f"{decision} needs a stated reason")
        missing = tm_missing(instance, term)
        if missing:
            raise CaseError(
                f"{tm_id} still needs positions from: {', '.join(missing)}"
            )
    term["status"] = decision
    term["resolution"] = str(reason or "").strip() if decision != "open" else ""
    term["history"].append(
        {"at": now(), "change": decision, "detail": str(reason or "").strip()}
    )
    _ts_version(instance, f"{decision} {tm_id}")
    _ts_touch(instance, f"term_{decision}", tm_id)
    return term


def tm_status(instance: dict, term: dict) -> dict:
    """Current, stale, or orphaned for one term.

    A position whose sheet was revised stales its
    term; a position whose sheet is gone orphans it.
    Missing positions are limitations, not staleness.
    """
    reasons: list[str] = []
    state = "current"
    sheets = {s.get("ts_id"): s for s in instance.get("sheets") or []}
    for position in term.get("positions") or []:
        ts_id = position.get("ts_id") or ""
        sheet = sheets.get(ts_id)
        if sheet is None:
            state = "orphaned"
            reasons.append(f"sheet {ts_id or '?'} is gone")
        elif _sheet_pin(sheet) != position.get("pin"):
            state = "stale" if state == "current" else state
            reasons.append(f"sheet {ts_id} changed since this position")
    return {"state": state, "reasons": reasons}


def ts_queue(instance: dict) -> list[dict]:
    """Open terms with their link state and missing sheets."""
    queue = []
    for term in instance.get("terms") or []:
        if term.get("status") != "open":
            continue
        status = tm_status(instance, term)
        queue.append(
            {
                "tm_id": term.get("tm_id"),
                "topic": term.get("topic"),
                "missing": tm_missing(instance, term),
                "link_state": status["state"],
                "reasons": status["reasons"],
            }
        )
    return queue


def ts_summary(instance: dict) -> dict:
    """Counts, queue, incomplete terms, and stale terms."""
    terms = instance.get("terms") or []
    by_status: dict[str, int] = {}
    for term in terms:
        status = term.get("status") or "open"
        by_status[status] = by_status.get(status, 0) + 1
    incomplete = [
        {"tm_id": t.get("tm_id"), "missing": tm_missing(instance, t)}
        for t in terms
        if tm_missing(instance, t)
    ]
    stale = []
    for term in terms:
        status = tm_status(instance, term)
        if status["state"] != "current":
            stale.append(
                {
                    "tm_id": term.get("tm_id"),
                    "state": status["state"],
                    "reasons": status["reasons"],
                }
            )
    return {
        "counts": by_status,
        "sheets": len(instance.get("sheets") or []),
        "queue": ts_queue(instance),
        "incomplete": incomplete,
        "stale": stale,
    }


def ts_snapshot(instance: dict, data: dict) -> dict:
    """Content hash a termsheet review binds (recomputed, never stored)."""
    from quality.records import evidence_fingerprint

    return {
        "content": _stable(
            {
                "sheets": instance.get("sheets"),
                "terms": instance.get("terms"),
            }
        ),
        "evidence": evidence_fingerprint(data),
        "template_version": instance.get("template_version"),
    }


def ts_diff(instance: dict, old: int, new: int) -> dict:
    """Before/after comparison between two comparison versions."""
    versions = {v.get("v"): v for v in instance.get("versions") or []}
    if old not in versions or new not in versions:
        raise CaseError(f"versions {old}/{new} are not in this instance")
    changes = []
    before_ts = {s.get("ts_id"): s for s in versions[old].get("sheets") or []}
    after_ts = {s.get("ts_id"): s for s in versions[new].get("sheets") or []}
    for ts_id in sorted(set(before_ts) | set(after_ts)):
        if ts_id not in before_ts:
            changes.append({"ts_id": ts_id, "change": "added"})
            continue
        if ts_id not in after_ts:
            changes.append({"ts_id": ts_id, "change": "removed"})
            continue
        first, second = before_ts[ts_id], after_ts[ts_id]
        for key in ("label", "party", "date", "notes"):
            if first.get(key) != second.get(key):
                changes.append(
                    {
                        "ts_id": ts_id,
                        "change": key,
                        "before": first.get(key),
                        "after": second.get(key),
                    }
                )
    before_tm = {t.get("tm_id"): t for t in versions[old].get("terms") or []}
    after_tm = {t.get("tm_id"): t for t in versions[new].get("terms") or []}
    for tm_id in sorted(set(before_tm) | set(after_tm)):
        if tm_id not in before_tm:
            changes.append({"tm_id": tm_id, "change": "added"})
            continue
        if tm_id not in after_tm:
            changes.append({"tm_id": tm_id, "change": "removed"})
            continue
        first, second = before_tm[tm_id], after_tm[tm_id]
        for key in ("topic", "proposition", "status", "resolution"):
            if first.get(key) != second.get(key):
                changes.append(
                    {
                        "tm_id": tm_id,
                        "change": key,
                        "before": first.get(key),
                        "after": second.get(key),
                    }
                )
        before_pos = [
            (p.get("ts_id"), p.get("text")) for p in first.get("positions") or []
        ]
        after_pos = [
            (p.get("ts_id"), p.get("text")) for p in second.get("positions") or []
        ]
        if before_pos != after_pos:
            changes.append(
                {
                    "tm_id": tm_id,
                    "change": "positions",
                    "before": [
                        {"ts_id": ts, "text": text} for ts, text in before_pos
                    ],
                    "after": [
                        {"ts_id": ts, "text": text} for ts, text in after_pos
                    ],
                }
            )
    return {"old": old, "new": new, "changes": changes}


def ts_export(
    instance: dict,
    data: dict,
    version: int | None = None,
) -> dict:
    """Versioned JSON report; historical exports stay frozen."""
    from quality.records import evidence_fingerprint

    versions = instance.get("versions") or []
    if version is None:
        sheets = instance.get("sheets") or []
        terms = instance.get("terms") or []
        historical = False
    else:
        match = next((v for v in versions if v.get("v") == version), None)
        if match is None:
            raise CaseError(f"version {version} is not in this instance")
        sheets = match.get("sheets") or []
        terms = match.get("terms") or []
        historical = True
    out_terms = []
    for term in terms:
        limitations = []
        if not historical:
            limitations.extend(tm_status(instance, term)["reasons"])
        if term.get("status") in _TS_NEXT:
            limitations.append(
                f"status: {term.get('status')} "
                f"(next: {_TS_NEXT[term['status']]})"
            )
        if not historical:
            missing = tm_missing(instance, term)
            if missing:
                limitations.append(
                    f"missing positions from: {', '.join(missing)}"
                )
        out_terms.append(
            {
                "tm_id": term.get("tm_id"),
                "topic": term.get("topic"),
                "proposition": term.get("proposition"),
                "positions": [
                    {"ts_id": p.get("ts_id"), "text": p.get("text")}
                    for p in term.get("positions") or []
                ],
                "status": term.get("status"),
                "resolution": term.get("resolution"),
                "limitations": limitations,
            }
        )
    summary = {} if historical else ts_summary(instance)
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
        "sheets": [
            {
                "ts_id": s.get("ts_id"),
                "label": s.get("label"),
                "party": s.get("party"),
                "date": s.get("date"),
                "notes": s.get("notes"),
            }
            for s in sheets
        ],
        "terms": out_terms,
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
            "synthetic demonstration: sheets, positions, and "
            "verdicts are reviewer-logged records",
            "a missing position never counts as agreement",
            "verdicts compare recorded positions; they are not "
            "legal advice about either offer",
        ]
        + (["historical version: liveness was not recomputed"] if historical else []),
    }


register(
    TEMPLATE_TS,
    snapshot=ts_snapshot,
    open=open_ts_instance,
    diff=ts_diff,
    export=ts_export,
    export_takes_root=False,
    count_key="terms",
    review_keys=('sheets', 'terms'),
)

__all__ = ['TS_STATUS', '_TS_NEXT', '_TS_DECISIONS', 'open_ts_instance', 'ts_get_sheet', 'ts_get_term', '_ts_version', '_ts_touch', '_next_ts_id', '_next_tm_id', '_sheet_pin', 'add_ts', 'set_ts', 'add_tm', 'set_tm', 'position_tm', 'tm_missing', 'decide_tm', 'tm_status', 'ts_queue', 'ts_summary', 'ts_snapshot', 'ts_diff', 'ts_export']
