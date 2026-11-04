"""patent claim comparison (one artifact slice; split from artifacts.py, behavior unchanged)."""

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


PT_KINDS = ("patent-claim", "product", "prior-art", "other")
PT_STATUS = ("open", "mapped", "not-mapped", "unclear")
_PT_NEXT = {
    "open": "record readings, then mark the mapping",
}
_PT_DECISIONS = {
    "open": ("mapped", "not-mapped", "unclear"),
    "mapped": ("open",),
    "not-mapped": ("open",),
    "unclear": ("open",),
}


def open_pt_instance(
    data: dict, run_id: str, title: str, source_id: str = ""
) -> dict:
    """Create a patent claim comparison (pure; caller saves).

    No prerequisites: subjects and elements are
    reviewer records, so an empty matter can already
    log them.
    """
    instance = {
        "id": _next_instance_id(data),
        "template": TEMPLATE_PT,
        "template_version": TEMPLATE_PT_VERSION,
        "matter": run_id,
        "title": title or "Patent Claim Comparison",
        "owner": "local",
        "viewers": ["local"],
        "created_at": now(),
        "subjects": [],
        "elements": [],
        "versions": [],
        "review": _blank_review(),
        "history": [{"at": now(), "event": "opened", "detail": ""}],
    }
    instance["versions"].append(
        {"v": 1, "at": now(), "reason": "opened", "subjects": [], "elements": []}
    )
    return instance


def pt_get_subject(instance: dict, sj_id: str) -> dict:
    subject = next(
        (s for s in instance.get("subjects") or [] if s.get("sj_id") == sj_id),
        None,
    )
    if subject is None:
        raise CaseError(f"unknown subject {sj_id!r}")
    return subject


def pt_get_element(instance: dict, el_id: str) -> dict:
    element = next(
        (e for e in instance.get("elements") or [] if e.get("el_id") == el_id),
        None,
    )
    if element is None:
        raise CaseError(f"unknown element {el_id!r}")
    return element


def _pt_version(instance: dict, reason: str) -> None:
    versions = instance.setdefault("versions", [])
    versions.append(
        {
            "v": len(versions) + 1,
            "at": now(),
            "reason": reason,
            "subjects": copy.deepcopy(instance.get("subjects")),
            "elements": copy.deepcopy(instance.get("elements")),
        }
    )


def _pt_touch(instance: dict, event: str, detail: str) -> None:
    instance["history"].append({"at": now(), "event": event, "detail": detail})
    instance["review"] = _blank_review()


def _next_sj_id(instance: dict) -> str:
    taken = {s.get("sj_id", "") for s in instance.get("subjects") or []}
    seq = 1
    while f"SJ{seq}" in taken:
        seq += 1
    return f"SJ{seq}"


def _next_el_id(instance: dict) -> str:
    taken = {e.get("el_id", "") for e in instance.get("elements") or []}
    seq = 1
    while f"EL{seq}" in taken:
        seq += 1
    return f"EL{seq}"


def _subject_pin(subject: dict) -> str:
    return _stable(
        {
            "label": subject.get("label"),
            "kind": subject.get("kind"),
            "ref": subject.get("ref"),
            "notes": subject.get("notes"),
        }
    )


def add_sj(
    instance: dict,
    label: str,
    kind: str,
    ref: str = "",
    notes: str = "",
) -> dict:
    """Log one comparison subject (pure; caller saves).

    The kind declares what the subject is: a patent
    claim, a product, prior art, or another recorded
    reference.
    """
    if not str(label or "").strip():
        raise CaseError("a subject needs its label")
    if kind not in PT_KINDS:
        raise CaseError(f"kind must be one of: {', '.join(PT_KINDS)}")
    subject = {
        "sj_id": _next_sj_id(instance),
        "label": str(label).strip(),
        "kind": kind,
        "ref": str(ref or "").strip(),
        "notes": str(notes or "").strip(),
        "history": [{"at": now(), "change": "logged", "detail": ""}],
    }
    instance.setdefault("subjects", []).append(subject)
    _pt_version(instance, f"logged {subject['sj_id']}")
    _pt_touch(instance, "subject_logged", subject["sj_id"])
    return subject


def set_sj(
    instance: dict,
    sj_id: str,
    label: str | None = None,
    kind: str | None = None,
    ref: str | None = None,
    notes: str | None = None,
) -> dict:
    """Revise a subject (pure; caller saves).

    Revisions do not rewrite history: readings pinned
    to the old subject go stale with reasons until
    re-recorded.
    """
    subject = pt_get_subject(instance, sj_id)
    if label is not None:
        if not str(label).strip():
            raise CaseError("a subject needs its label")
        subject["label"] = str(label).strip()
    if kind is not None:
        if kind not in PT_KINDS:
            raise CaseError(f"kind must be one of: {', '.join(PT_KINDS)}")
        subject["kind"] = kind
    if ref is not None:
        subject["ref"] = str(ref).strip()
    if notes is not None:
        subject["notes"] = str(notes).strip()
    subject["history"].append(
        {"at": now(), "change": "revised", "detail": sj_id}
    )
    _pt_version(instance, f"revised {sj_id}")
    _pt_touch(instance, "subject_revised", sj_id)
    return subject


def add_el(instance: dict, claim_ref: str, text: str) -> dict:
    """Open one claim element (pure; caller saves).

    The text records the element's wording as the
    reviewer transcribed it; it is not the patent.
    """
    if not str(claim_ref or "").strip():
        raise CaseError("an element needs its claim reference")
    if not str(text or "").strip():
        raise CaseError("an element needs its text")
    element = {
        "el_id": _next_el_id(instance),
        "claim_ref": str(claim_ref).strip(),
        "text": str(text).strip(),
        "readings": [],
        "status": "open",
        "resolution": "",
        "history": [{"at": now(), "change": "opened", "detail": ""}],
    }
    instance.setdefault("elements", []).append(element)
    _pt_version(instance, f"opened {element['el_id']}")
    _pt_touch(instance, "element_opened", element["el_id"])
    return element


def set_el(
    instance: dict,
    el_id: str,
    claim_ref: str | None = None,
    text: str | None = None,
) -> dict:
    """Revise an element's wording (pure; caller saves).

    Decided elements are locked: reopen them first,
    so a verdict can never silently change meaning.
    """
    element = pt_get_element(instance, el_id)
    if element.get("status") != "open":
        raise CaseError(f"{el_id} is {element['status']}; reopen it first")
    if claim_ref is not None:
        if not str(claim_ref).strip():
            raise CaseError("an element needs its claim reference")
        element["claim_ref"] = str(claim_ref).strip()
    if text is not None:
        if not str(text).strip():
            raise CaseError("an element needs its text")
        element["text"] = str(text).strip()
    element["history"].append(
        {"at": now(), "change": "revised", "detail": el_id}
    )
    _pt_version(instance, f"revised {el_id}")
    _pt_touch(instance, "element_revised", el_id)
    return element


def reading_el(instance: dict, el_id: str, sj_id: str, text: str) -> dict:
    """Record one subject's reading of an element (pure; caller saves).

    Re-recording supersedes the old reading with a
    fresh subject pin, which also clears staleness
    from a subject revision the reviewer has now
    confirmed. Decided elements are locked: reopen
    them first.
    """
    element = pt_get_element(instance, el_id)
    if element.get("status") != "open":
        raise CaseError(f"{el_id} is {element['status']}; reopen it first")
    subject = pt_get_subject(instance, sj_id)
    if not str(text or "").strip():
        raise CaseError("a reading needs its text")
    readings = element.setdefault("readings", [])
    entry = {
        "sj_id": sj_id,
        "text": str(text).strip(),
        "pin": _subject_pin(subject),
    }
    for i, old in enumerate(readings):
        if old.get("sj_id") == sj_id:
            readings[i] = entry
            break
    else:
        readings.append(entry)
    element["history"].append(
        {"at": now(), "change": "reading", "detail": sj_id}
    )
    _pt_version(instance, f"reading {el_id} {sj_id}")
    _pt_touch(instance, "element_reading", f"{el_id} {sj_id}")
    return element


def el_missing(instance: dict, element: dict) -> list[str]:
    """Subjects with no recorded reading on this element."""
    placed = {r.get("sj_id") for r in element.get("readings") or []}
    return sorted(
        s.get("sj_id", "")
        for s in instance.get("subjects") or []
        if s.get("sj_id") not in placed
    )


def decide_el(
    instance: dict, el_id: str, decision: str, reason: str = ""
) -> dict:
    """Record an element mapping verdict (pure; caller saves).

    Every verdict except reopening needs a stated
    reason and a reading from every logged subject:
    a missing reading never counts as mapped.
    Unclear is a verdict for genuinely ambiguous
    mappings, not a way to skip the readings.
    """
    element = pt_get_element(instance, el_id)
    status = element.get("status") or "open"
    if decision not in (_PT_DECISIONS.get(status) or ()):
        allowed = ", ".join(_PT_DECISIONS.get(status) or ())
        raise CaseError(f"{el_id} is {status}; allowed next: {allowed or 'none'}")
    if decision != "open":
        if not str(reason or "").strip():
            raise CaseError(f"{decision} needs a stated reason")
        missing = el_missing(instance, element)
        if missing:
            raise CaseError(
                f"{el_id} still needs readings from: {', '.join(missing)}"
            )
    element["status"] = decision
    element["resolution"] = str(reason or "").strip() if decision != "open" else ""
    element["history"].append(
        {"at": now(), "change": decision, "detail": str(reason or "").strip()}
    )
    _pt_version(instance, f"{decision} {el_id}")
    _pt_touch(instance, f"element_{decision}", el_id)
    return element


def el_status(instance: dict, element: dict) -> dict:
    """Current, stale, or orphaned for one element.

    A reading whose subject was revised stales its
    element; a reading whose subject is gone orphans
    it. Missing readings are limitations, not
    staleness.
    """
    reasons: list[str] = []
    state = "current"
    subjects = {s.get("sj_id"): s for s in instance.get("subjects") or []}
    for reading in element.get("readings") or []:
        sj_id = reading.get("sj_id") or ""
        subject = subjects.get(sj_id)
        if subject is None:
            state = "orphaned"
            reasons.append(f"subject {sj_id or '?'} is gone")
        elif _subject_pin(subject) != reading.get("pin"):
            state = "stale" if state == "current" else state
            reasons.append(f"subject {sj_id} changed since this reading")
    return {"state": state, "reasons": reasons}


def pt_queue(instance: dict) -> list[dict]:
    """Open elements with their link state and missing subjects."""
    queue = []
    for element in instance.get("elements") or []:
        if element.get("status") != "open":
            continue
        status = el_status(instance, element)
        queue.append(
            {
                "el_id": element.get("el_id"),
                "claim_ref": element.get("claim_ref"),
                "missing": el_missing(instance, element),
                "link_state": status["state"],
                "reasons": status["reasons"],
            }
        )
    return queue


def pt_summary(instance: dict) -> dict:
    """Counts, queue, incomplete elements, and stale elements."""
    elements = instance.get("elements") or []
    by_status: dict[str, int] = {}
    for element in elements:
        status = element.get("status") or "open"
        by_status[status] = by_status.get(status, 0) + 1
    incomplete = [
        {"el_id": e.get("el_id"), "missing": el_missing(instance, e)}
        for e in elements
        if el_missing(instance, e)
    ]
    stale = []
    for element in elements:
        status = el_status(instance, element)
        if status["state"] != "current":
            stale.append(
                {
                    "el_id": element.get("el_id"),
                    "state": status["state"],
                    "reasons": status["reasons"],
                }
            )
    return {
        "counts": by_status,
        "subjects": len(instance.get("subjects") or []),
        "queue": pt_queue(instance),
        "incomplete": incomplete,
        "stale": stale,
    }


def pt_snapshot(instance: dict, data: dict) -> dict:
    """Content hash a patent review binds (recomputed, never stored)."""
    from quality.records import evidence_fingerprint

    return {
        "content": _stable(
            {
                "subjects": instance.get("subjects"),
                "elements": instance.get("elements"),
            }
        ),
        "evidence": evidence_fingerprint(data),
        "template_version": instance.get("template_version"),
    }


def pt_diff(instance: dict, old: int, new: int) -> dict:
    """Before/after comparison between two chart versions."""
    versions = {v.get("v"): v for v in instance.get("versions") or []}
    if old not in versions or new not in versions:
        raise CaseError(f"versions {old}/{new} are not in this instance")
    changes = []
    before_sj = {s.get("sj_id"): s for s in versions[old].get("subjects") or []}
    after_sj = {s.get("sj_id"): s for s in versions[new].get("subjects") or []}
    for sj_id in sorted(set(before_sj) | set(after_sj)):
        if sj_id not in before_sj:
            changes.append({"sj_id": sj_id, "change": "added"})
            continue
        if sj_id not in after_sj:
            changes.append({"sj_id": sj_id, "change": "removed"})
            continue
        first, second = before_sj[sj_id], after_sj[sj_id]
        for key in ("label", "kind", "ref", "notes"):
            if first.get(key) != second.get(key):
                changes.append(
                    {
                        "sj_id": sj_id,
                        "change": key,
                        "before": first.get(key),
                        "after": second.get(key),
                    }
                )
    before_el = {e.get("el_id"): e for e in versions[old].get("elements") or []}
    after_el = {e.get("el_id"): e for e in versions[new].get("elements") or []}
    for el_id in sorted(set(before_el) | set(after_el)):
        if el_id not in before_el:
            changes.append({"el_id": el_id, "change": "added"})
            continue
        if el_id not in after_el:
            changes.append({"el_id": el_id, "change": "removed"})
            continue
        first, second = before_el[el_id], after_el[el_id]
        for key in ("claim_ref", "text", "status", "resolution"):
            if first.get(key) != second.get(key):
                changes.append(
                    {
                        "el_id": el_id,
                        "change": key,
                        "before": first.get(key),
                        "after": second.get(key),
                    }
                )
        before_rd = [
            (r.get("sj_id"), r.get("text")) for r in first.get("readings") or []
        ]
        after_rd = [
            (r.get("sj_id"), r.get("text")) for r in second.get("readings") or []
        ]
        if before_rd != after_rd:
            changes.append(
                {
                    "el_id": el_id,
                    "change": "readings",
                    "before": [
                        {"sj_id": sj, "text": text} for sj, text in before_rd
                    ],
                    "after": [
                        {"sj_id": sj, "text": text} for sj, text in after_rd
                    ],
                }
            )
    return {"old": old, "new": new, "changes": changes}


def pt_export(
    instance: dict,
    data: dict,
    version: int | None = None,
) -> dict:
    """Versioned JSON report; historical exports stay frozen."""
    from quality.records import evidence_fingerprint

    versions = instance.get("versions") or []
    if version is None:
        subjects = instance.get("subjects") or []
        elements = instance.get("elements") or []
        historical = False
    else:
        match = next((v for v in versions if v.get("v") == version), None)
        if match is None:
            raise CaseError(f"version {version} is not in this instance")
        subjects = match.get("subjects") or []
        elements = match.get("elements") or []
        historical = True
    out_elements = []
    for element in elements:
        limitations = []
        if not historical:
            limitations.extend(el_status(instance, element)["reasons"])
        if element.get("status") in _PT_NEXT:
            limitations.append(
                f"status: {element.get('status')} "
                f"(next: {_PT_NEXT[element['status']]})"
            )
        if not historical:
            missing = el_missing(instance, element)
            if missing:
                limitations.append(
                    f"missing readings from: {', '.join(missing)}"
                )
        out_elements.append(
            {
                "el_id": element.get("el_id"),
                "claim_ref": element.get("claim_ref"),
                "text": element.get("text"),
                "readings": [
                    {"sj_id": r.get("sj_id"), "text": r.get("text")}
                    for r in element.get("readings") or []
                ],
                "status": element.get("status"),
                "resolution": element.get("resolution"),
                "limitations": limitations,
            }
        )
    summary = {} if historical else pt_summary(instance)
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
        "subjects": [
            {
                "sj_id": s.get("sj_id"),
                "label": s.get("label"),
                "kind": s.get("kind"),
                "ref": s.get("ref"),
                "notes": s.get("notes"),
            }
            for s in subjects
        ],
        "elements": out_elements,
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
            "synthetic demonstration: subjects, readings, and "
            "verdicts are reviewer-logged records",
            "a missing reading never counts as mapped",
            "verdicts compare recorded readings; they are not "
            "infringement or validity opinions",
        ]
        + (["historical version: liveness was not recomputed"] if historical else []),
    }


register(
    TEMPLATE_PT,
    snapshot=pt_snapshot,
    open=open_pt_instance,
    diff=pt_diff,
    export=pt_export,
    export_takes_root=False,
    count_key="elements",
    review_keys=('subjects', 'elements'),
)

__all__ = ['PT_KINDS', 'PT_STATUS', '_PT_NEXT', '_PT_DECISIONS', 'open_pt_instance', 'pt_get_subject', 'pt_get_element', '_pt_version', '_pt_touch', '_next_sj_id', '_next_el_id', '_subject_pin', 'add_sj', 'set_sj', 'add_el', 'set_el', 'reading_el', 'el_missing', 'decide_el', 'el_status', 'pt_queue', 'pt_summary', 'pt_snapshot', 'pt_diff', 'pt_export']
