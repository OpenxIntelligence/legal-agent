"""chronology & issue board (one artifact slice; split from artifacts.py, behavior unchanged)."""

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
    _sealed_docs,
    _next_instance_id,
    _stable,
    _structural_outcome,
    audit_citation,
    data_mode,
    register,
)


_DATE_SPAN = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+\d{1,2}(?:\s*[-\u2013]\s*\d{1,2})?\b"
)
LINK_KINDS = ("disputed_order", "depends_on")


def open_board_instance(
    data: dict, run_id: str, title: str, source_id: str = ""
) -> dict:
    """Create a board instance over recorded evidence (pure; caller saves).

    Needs facts, not a memo: the board can open before any report
    exists. Events are confirmed later from detected date spans or
    added by the reviewer; nothing is invented at open time.
    """
    live = [f for f in data.get("facts", []) or [] if isinstance(f, dict)]
    if not live:
        raise CaseError("the board needs recorded evidence")
    instance = {
        "id": _next_instance_id(data),
        "template": TEMPLATE_BOARD,
        "template_version": TEMPLATE_BOARD_VERSION,
        "matter": run_id,
        "title": title or "Case Chronology & Issue Board",
        "owner": "local",
        "viewers": ["local"],
        "created_at": now(),
        "events": [],
        "links": [],
        "dismissed": [],
        "versions": [],
        "review": _blank_review(),
        "history": [{"at": now(), "event": "opened", "detail": ""}],
    }
    instance["versions"].append(
        {"v": 1, "at": now(), "reason": "opened", "events": [], "links": []}
    )
    return instance


def _live_facts(data: dict) -> dict[str, dict]:
    return {
        fact["id"]: fact
        for fact in data.get("facts", []) or []
        if isinstance(fact, dict) and fact.get("id")
    }


def board_candidates(data: dict, instance: dict) -> list[dict]:
    """Date spans detected in live fact quotes, minus used/dismissed.

    Deterministic text heuristic (month + day); the reviewer
    confirms or discards each span, so a wrong guess costs one
    click, not a fabricated event. Withdrawn facts never surface.
    """
    used = {
        (event.get("source") or {}).get("fact_id", "")
        + "\x00"
        + (event.get("source") or {}).get("span", "")
        for event in instance.get("events") or []
    }
    dismissed = {
        str(entry.get("fact_id", "")) + "\x00" + str(entry.get("span", ""))
        for entry in instance.get("dismissed") or []
    }
    candidates = []
    for fact in sorted(
        _live_facts(data).values(), key=lambda f: str(f.get("id"))
    ):
        quote = str(fact.get("quote") or "")
        for match in _DATE_SPAN.finditer(quote):
            key = f"{fact['id']}\x00{match.group(0)}"
            if key in used or key in dismissed:
                continue
            start = max(0, match.start() - 60)
            end = min(len(quote), match.end() + 60)
            candidates.append(
                {
                    "fact_id": fact["id"],
                    "item": fact.get("item", ""),
                    "document_id": fact.get("document_id", ""),
                    "page": fact.get("page"),
                    "span": match.group(0),
                    "context": quote[start:end].strip(),
                }
            )
    return candidates


def _event_inputs(data: dict, fact_ids: list[str]) -> dict:
    from quality.records import evidence_fingerprint

    live = _live_facts(data)
    return {
        "fact_ids": sorted(fact_ids),
        "quotes": _stable(
            {fid: (live[fid].get("quote") if fid in live else None) for fid in fact_ids}
        ),
        "evidence": evidence_fingerprint(data),
    }


def _board_version(instance: dict, reason: str) -> None:
    versions = instance.setdefault("versions", [])
    versions.append(
        {
            "v": len(versions) + 1,
            "at": now(),
            "reason": reason,
            "events": copy.deepcopy(instance.get("events")),
            "links": copy.deepcopy(instance.get("links")),
        }
    )


def _board_touch(instance: dict, event: str, detail: str) -> None:
    instance["history"].append({"at": now(), "event": event, "detail": detail})
    instance["review"] = _blank_review()


def _next_event_id(instance: dict) -> str:
    taken = {
        event.get("event_id", "")
        for event in instance.get("events") or []
    }
    seq = 1
    while f"E{seq}" in taken:
        seq += 1
    return f"E{seq}"


def board_get_event(instance: dict, event_id: str) -> dict:
    event = next(
        (
            e
            for e in instance.get("events") or []
            if e.get("event_id") == event_id
        ),
        None,
    )
    if event is None:
        raise CaseError(f"unknown board event {event_id!r}")
    return event


def confirm_event(
    instance: dict,
    data: dict,
    fact_id: str,
    span: str,
    date: str,
    label: str,
) -> dict:
    """Promote a detected date span to a sourced event (pure; caller saves)."""
    live = _live_facts(data)
    fact = live.get(fact_id)
    if fact is None:
        raise CaseError(f"fact {fact_id!r} is not live evidence")
    if span not in str(fact.get("quote") or ""):
        raise CaseError(f"{span!r} is not in {fact_id}'s quote")
    if not str(label or "").strip():
        raise CaseError("a confirmed event needs a label")
    event = {
        "event_id": _next_event_id(instance),
        "date": str(date or span),
        "date_source": "span" if str(date or span) == span else "reviewer",
        "label": str(label),
        "issue_ids": [str(fact.get("item") or "")],
        "fact_ids": [fact_id],
        "source": {"fact_id": fact_id, "span": span},
        "uncertainty": "",
        "owner": "",
        "inputs": _event_inputs(data, [fact_id]),
        "history": [{"at": now(), "change": "confirmed", "detail": span}],
    }
    instance.setdefault("events", []).append(event)
    _board_version(instance, f"confirmed {event['event_id']}")
    _board_touch(instance, "event_confirmed", event["event_id"])
    return event


def add_event(
    instance: dict,
    data: dict,
    label: str,
    date: str = "",
    issue_ids: list | None = None,
    fact_ids: list | None = None,
    uncertainty: str = "",
) -> dict:
    """Add a reviewer-authored event: sources or explicit uncertainty.

    Linked facts may include withdrawn ones; those stay visible as
    stale redacted links rather than vanishing silently.
    """
    if not str(label or "").strip():
        raise CaseError("an event needs a label")
    fact_ids = [str(fid) for fid in fact_ids or []]
    if not fact_ids and not str(uncertainty or "").strip():
        raise CaseError("an unsourced event needs explicit uncertainty")
    known = set(_live_facts(data))
    known.update(
        str(fact.get("id"))
        for fact in data.get("withdrawn_facts", []) or []
        if isinstance(fact, dict) and fact.get("id")
    )
    unknown = [fid for fid in fact_ids if fid not in known]
    if unknown:
        raise CaseError(f"unknown facts: {', '.join(unknown)}")
    event = {
        "event_id": _next_event_id(instance),
        "date": str(date or ""),
        "date_source": "reviewer" if date else "",
        "label": str(label),
        "issue_ids": [str(i) for i in issue_ids or []],
        "fact_ids": fact_ids,
        "source": {},
        "uncertainty": str(uncertainty or ""),
        "owner": "",
        "inputs": _event_inputs(data, fact_ids),
        "history": [{"at": now(), "change": "added", "detail": ""}],
    }
    instance.setdefault("events", []).append(event)
    _board_version(instance, f"added {event['event_id']}")
    _board_touch(instance, "event_added", event["event_id"])
    return event


def annotate_event(instance: dict, event_id: str, **fields: Any) -> dict:
    """Revise an event's label, date, owner, or uncertainty."""
    event = board_get_event(instance, event_id)
    changed = []
    for key in ("label", "date", "owner", "uncertainty"):
        if key in fields and fields[key] is not None:
            value = str(fields[key])
            if value != event.get(key):
                event[key] = value
                if key == "date":
                    event["date_source"] = "reviewer" if value else ""
                changed.append(key)
    if not changed:
        return event
    event["history"].append(
        {"at": now(), "change": "annotated", "detail": ", ".join(changed)}
    )
    _board_version(instance, f"annotated {event_id}")
    _board_touch(instance, "event_annotated", event_id)
    return event


def link_events(
    instance: dict, first: str, second: str, kind: str, reason: str
) -> dict:
    """Record a dependency or a reviewer-flagged disputed ordering."""
    if kind not in LINK_KINDS:
        raise CaseError(f"link kind must be one of: {', '.join(LINK_KINDS)}")
    board_get_event(instance, first)
    board_get_event(instance, second)
    if first == second:
        raise CaseError("an event cannot link to itself")
    if not str(reason or "").strip():
        raise CaseError("a link needs its reason")
    for link in instance.get("links") or []:
        if {link.get("a"), link.get("b")} == {first, second} and link.get(
            "kind"
        ) == kind:
            raise CaseError(f"{first} and {second} already link as {kind}")
    link = {
        "a": first,
        "b": second,
        "kind": kind,
        "reason": str(reason),
        "by": "reviewer",
        "at": now(),
    }
    instance.setdefault("links", []).append(link)
    _board_version(instance, f"linked {first} {kind} {second}")
    _board_touch(instance, "events_linked", f"{first} {kind} {second}")
    return link


def dismiss_candidate(instance: dict, fact_id: str, span: str) -> dict:
    """Discard a detected span; it stops being suggested."""
    entry = {"fact_id": str(fact_id), "span": str(span)}
    if entry not in (instance.get("dismissed") or []):
        instance.setdefault("dismissed", []).append(entry)
        _board_version(instance, f"dismissed {fact_id} {span!r}")
        _board_touch(instance, "candidate_dismissed", fact_id)
    return entry


def event_staleness(data: dict, event: dict) -> dict:
    """Whether an event's linked evidence still supports it.

    Read-time, like audit-row staleness: withdrawn links, changed
    quotes, and new evidence each stale with their own reason.
    """
    stored = event.get("inputs") or {}
    if not stored:
        return {"stale": None, "reasons": []}
    current = _event_inputs(data, event.get("fact_ids") or [])
    reasons = []
    live = _live_facts(data)
    for fid in event.get("fact_ids") or []:
        if fid not in live:
            reasons.append(f"linked evidence {fid} was withdrawn")
    if stored.get("quotes") != current["quotes"] and not reasons:
        reasons.append("a linked passage changed")
    if stored.get("evidence") != current["evidence"]:
        reasons.append("evidence changed since the event was set")
    return {"stale": bool(reasons), "reasons": reasons}


def board_snapshot(instance: dict, data: dict) -> dict:
    """Content hash a board review binds (recomputed, never stored)."""
    from quality.records import evidence_fingerprint

    return {
        "content": _stable(
            {
                "events": instance.get("events"),
                "links": instance.get("links"),
                "dismissed": instance.get("dismissed"),
            }
        ),
        "evidence": evidence_fingerprint(data),
        "template_version": instance.get("template_version"),
    }


def board_diff(instance: dict, old: int, new: int) -> dict:
    """Before/after comparison between two board versions."""
    versions = {v.get("v"): v for v in instance.get("versions") or []}
    if old not in versions or new not in versions:
        raise CaseError(f"versions {old}/{new} are not in this instance")
    before = {e.get("event_id"): e for e in versions[old].get("events") or []}
    after = {e.get("event_id"): e for e in versions[new].get("events") or []}
    changes = []
    for event_id in sorted(set(before) | set(after)):
        if event_id not in before:
            changes.append({"event_id": event_id, "change": "added"})
            continue
        if event_id not in after:
            changes.append({"event_id": event_id, "change": "removed"})
            continue
        first, second = before[event_id], after[event_id]
        for key in ("label", "date", "owner", "uncertainty", "fact_ids"):
            if first.get(key) != second.get(key):
                changes.append(
                    {
                        "event_id": event_id,
                        "change": key,
                        "before": first.get(key),
                        "after": second.get(key),
                    }
                )
    old_links = {
        (l.get("a"), l.get("b"), l.get("kind")) for l in versions[old].get("links") or []
    }
    new_links = {
        (l.get("a"), l.get("b"), l.get("kind")) for l in versions[new].get("links") or []
    }
    for link in sorted(new_links - old_links):
        changes.append({"event_id": f"{link[0]} {link[2]} {link[1]}", "change": "linked"})
    for link in sorted(old_links - new_links):
        changes.append({"event_id": f"{link[0]} {link[2]} {link[1]}", "change": "unlinked"})
    return {"old": old, "new": new, "changes": changes}


def board_export(
    instance: dict, data: dict, root: Path, version: int | None = None
) -> dict:
    """Versioned board report with provenance, limits, approval scope.

    Same scope filter as the audit export: passages from
    privileged sources are redacted, quarantined sources are
    sealed; withdrawn facts stay named but unreadable.
    """
    from quality.records import evidence_fingerprint

    versions = instance.get("versions") or []
    if version is None:
        events = instance.get("events") or []
        links = instance.get("links") or []
        historical = False
    else:
        match = next((v for v in versions if v.get("v") == version), None)
        if match is None:
            raise CaseError(f"version {version} is not in this instance")
        events = match.get("events") or []
        links = match.get("links") or []
        historical = True
    flagged = _flagged(data)
    live = _live_facts(data)
    sealed = _sealed_docs(
        root,
        {str(fact.get("document_id") or "") for fact in live.values()},
    )
    withdrawn = {
        str(fact.get("id")): fact
        for fact in data.get("withdrawn_facts", []) or []
        if isinstance(fact, dict) and fact.get("id")
    }
    out_events = []
    for event in events:
        sources = []
        for fid in event.get("fact_ids") or []:
            fact = live.get(fid)
            if fact is None:
                sources.append(
                    {
                        "fact_id": fid,
                        "status": "withdrawn",
                        "quote": "[redacted: withdrawn]",
                        "passage": "[redacted: withdrawn]",
                    }
                )
                continue
            document_id = str(fact.get("document_id") or "")
            redacted = document_id in flagged
            shut = document_id in sealed
            quote = fact.get("quote", "")
            if redacted:
                quote = "[redacted: privileged]"
            elif shut:
                quote = "[sealed: quarantined]"
            passage = _excerpt(
                root, document_id, fact.get("page"), str(fact.get("quote") or "")
            )
            if redacted:
                passage = "[redacted: privileged]"
            elif shut:
                passage = "[sealed: quarantined]"
            sources.append(
                {
                    "fact_id": fid,
                    "status": "live",
                    "document_id": document_id,
                    "page": fact.get("page"),
                    "quote": quote,
                    "passage": passage,
                }
            )
        limitations = []
        if not historical:
            stale = event_staleness(data, event)
            if stale["stale"]:
                limitations.append(f"stale: {'; '.join(stale['reasons'])}")
        if event.get("uncertainty"):
            limitations.append(f"uncertain: {event['uncertainty']}")
        if not event.get("fact_ids"):
            limitations.append("unsourced: reviewer judgment only")
        out_events.append(
            {
                "event_id": event.get("event_id"),
                "date": event.get("date"),
                "date_source": event.get("date_source"),
                "label": event.get("label"),
                "issue_ids": event.get("issue_ids"),
                "owner": event.get("owner") or "unassigned",
                "uncertainty": event.get("uncertainty"),
                "sources": sources,
                "limitations": limitations,
            }
        )
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
        "events": out_events,
        "links": links,
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
            "detected dates are text spans until a reviewer confirms them",
            "stale events keep their last values with reasons; they are not refreshed silently",
            "disputed orderings are reviewer flags, not judge verdicts",
        ]
        + (["historical version: liveness was not recomputed"] if historical else []),
    }


register(
    TEMPLATE_BOARD,
    snapshot=board_snapshot,
    open=open_board_instance,
    diff=board_diff,
    export=board_export,
    export_takes_root=True,
    count_key="events",
    review_keys=('events', 'links'),
)

__all__ = ['_DATE_SPAN', 'LINK_KINDS', 'open_board_instance', '_live_facts', 'board_candidates', '_event_inputs', '_board_version', '_board_touch', '_next_event_id', 'board_get_event', 'confirm_event', 'add_event', 'annotate_event', 'link_events', 'dismiss_candidate', 'event_staleness', 'board_snapshot', 'board_diff', 'board_export']
