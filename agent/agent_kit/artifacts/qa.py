"""sell-side Q&A tracker (one artifact slice; split from artifacts.py, behavior unchanged)."""

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


QA_STATUS = ("asked", "answered", "clarify", "closed")
_QA_NEXT = {
    "asked": "answer or close",
    "clarify": "answer or close",
}
_QA_DECISIONS = {
    "asked": ("answered", "closed"),
    "answered": ("clarify", "asked"),
    "clarify": ("answered", "closed"),
    "closed": ("asked",),
}


def open_qa_instance(
    data: dict, run_id: str, title: str, source_id: str = ""
) -> dict:
    """Create a Q&A tracker (pure; caller saves).

    No prerequisites: questions are reviewer records,
    and support documents are validated per answer.
    """
    instance = {
        "id": _next_instance_id(data),
        "template": TEMPLATE_QA,
        "template_version": TEMPLATE_QA_VERSION,
        "matter": run_id,
        "title": title or "Sell-side Q&A Tracker",
        "owner": "local",
        "viewers": ["local"],
        "created_at": now(),
        "questions": [],
        "versions": [],
        "review": _blank_review(),
        "history": [{"at": now(), "event": "opened", "detail": ""}],
    }
    instance["versions"].append(
        {"v": 1, "at": now(), "reason": "opened", "questions": []}
    )
    return instance


def qa_get_question(instance: dict, qa_id: str) -> dict:
    question = next(
        (q for q in instance.get("questions") or [] if q.get("qa_id") == qa_id),
        None,
    )
    if question is None:
        raise CaseError(f"unknown question {qa_id!r}")
    return question


def _qa_version(instance: dict, reason: str) -> None:
    versions = instance.setdefault("versions", [])
    versions.append(
        {
            "v": len(versions) + 1,
            "at": now(),
            "reason": reason,
            "questions": copy.deepcopy(instance.get("questions")),
        }
    )


def _qa_touch(instance: dict, event: str, detail: str) -> None:
    instance["history"].append({"at": now(), "event": event, "detail": detail})
    instance["review"] = _blank_review()


def _next_qa_id(instance: dict) -> str:
    taken = {q.get("qa_id", "") for q in instance.get("questions") or []}
    seq = 1
    while f"QA{seq}" in taken:
        seq += 1
    return f"QA{seq}"


def add_qa(
    instance: dict,
    question: str,
    asker: str = "",
    topic: str = "",
    assignee: str = "",
    due_date: str = "",
) -> dict:
    """Log one buyer question (pure; caller saves).

    The question text is required; the due date stays
    unknown until the reviewer states it.
    """
    if not str(question or "").strip():
        raise CaseError("a question needs its text")
    due_date = str(due_date or "").strip()
    if due_date and not _valid_date(due_date):
        raise CaseError(f"due date must be YYYY-MM-DD, got {due_date!r}")
    entry = {
        "qa_id": _next_qa_id(instance),
        "question": str(question).strip(),
        "asker": str(asker or "").strip(),
        "topic": str(topic or "").strip(),
        "assignee": str(assignee or "").strip(),
        "due_date": due_date,
        "answer": "",
        "support": [],
        "doc_pins": {},
        "status": "asked",
        "resolution": "",
        "history": [{"at": now(), "change": "logged", "detail": ""}],
    }
    instance.setdefault("questions", []).append(entry)
    _qa_version(instance, f"logged {entry['qa_id']}")
    _qa_touch(instance, "question_logged", entry["qa_id"])
    return entry


def set_qa(
    instance: dict,
    qa_id: str,
    question: str | None = None,
    asker: str | None = None,
    topic: str | None = None,
    assignee: str | None = None,
    due_date: str | None = None,
) -> dict:
    """Revise a question (pure; caller saves).

    Answered and closed questions are locked: reopen
    them first, so an answer can never silently
    change its question.
    """
    entry = qa_get_question(instance, qa_id)
    if entry.get("status") in ("answered", "closed"):
        raise CaseError(f"{qa_id} is {entry['status']}; reopen it first")
    if question is not None:
        if not str(question).strip():
            raise CaseError("a question needs its text")
        entry["question"] = str(question).strip()
    if asker is not None:
        entry["asker"] = str(asker).strip()
    if topic is not None:
        entry["topic"] = str(topic).strip()
    if assignee is not None:
        entry["assignee"] = str(assignee).strip()
    if due_date is not None:
        due_date = str(due_date).strip()
        if due_date and not _valid_date(due_date):
            raise CaseError(f"due date must be YYYY-MM-DD, got {due_date!r}")
        entry["due_date"] = due_date
    entry["history"].append({"at": now(), "change": "revised", "detail": qa_id})
    _qa_version(instance, f"revised {qa_id}")
    _qa_touch(instance, "question_revised", qa_id)
    return entry


def answer_qa(
    instance: dict,
    root: Path,
    qa_id: str,
    answer: str,
    documents: list[str] | None = None,
) -> dict:
    """Record an answer with pinned support (pure; caller saves).

    Allowed on asked, clarify, and answered questions;
    re-answering supersedes the old text and pins.
    Every support document must exist in the inbox
    when cited.
    """
    entry = qa_get_question(instance, qa_id)
    if entry.get("status") not in ("asked", "clarify", "answered"):
        raise CaseError(f"{qa_id} is {entry['status']}; reopen it first")
    if not str(answer or "").strip():
        raise CaseError("an answer needs its text")
    docs = [str(d).strip() for d in (documents or []) if str(d).strip()]
    shas = _inbox_shas(root)
    missing = [d for d in docs if d not in shas]
    if missing:
        raise CaseError(
            "support documents not in the inbox: " + ", ".join(sorted(missing))
        )
    entry["answer"] = str(answer).strip()
    entry["support"] = docs
    entry["doc_pins"] = {d: shas[d] for d in docs}
    entry["history"].append(
        {"at": now(), "change": "answered", "detail": qa_id}
    )
    _qa_version(instance, f"answered {qa_id}")
    _qa_touch(instance, "question_answered", qa_id)
    return entry


def decide_qa(
    instance: dict, qa_id: str, decision: str, reason: str = ""
) -> dict:
    """Move a question through its lifecycle (pure; caller saves).

    Every decision except reopening needs a stated
    reason; answering additionally needs recorded
    answer text.
    """
    entry = qa_get_question(instance, qa_id)
    status = entry.get("status") or "asked"
    if decision not in (_QA_DECISIONS.get(status) or ()):
        allowed = ", ".join(_QA_DECISIONS.get(status) or ())
        raise CaseError(f"{qa_id} is {status}; allowed next: {allowed or 'none'}")
    if decision != "asked" and not str(reason or "").strip():
        raise CaseError(f"{decision} needs a stated reason")
    if decision == "answered" and not entry.get("answer"):
        raise CaseError(f"{qa_id} needs recorded answer text first")
    entry["status"] = decision
    entry["resolution"] = str(reason or "").strip() if decision != "asked" else ""
    entry["history"].append(
        {"at": now(), "change": decision, "detail": str(reason or "").strip()}
    )
    _qa_version(instance, f"{decision} {qa_id}")
    _qa_touch(instance, f"question_{decision}", qa_id)
    return entry


def qa_status(instance: dict, root: Path, entry: dict) -> dict:
    """Current, stale, or orphaned for one question.

    A support document that changed stales the
    answer; one that vanished orphans it. Unknown
    due dates and missing support are limitations,
    not staleness.
    """
    reasons: list[str] = []
    state = "current"
    shas = _inbox_shas(root)
    for doc in entry.get("support") or []:
        if doc not in shas:
            state = "orphaned"
            reasons.append(f"support document {doc} is gone")
        elif shas[doc] != (entry.get("doc_pins") or {}).get(doc):
            state = "stale" if state == "current" else state
            reasons.append(f"support document {doc} changed")
    return {"state": state, "reasons": reasons}


def qa_queue(instance: dict, root: Path) -> list[dict]:
    """Asked and clarify questions with their link state."""
    queue = []
    for entry in instance.get("questions") or []:
        if entry.get("status") not in ("asked", "clarify"):
            continue
        status = qa_status(instance, root, entry)
        queue.append(
            {
                "qa_id": entry.get("qa_id"),
                "question": entry.get("question"),
                "status": entry.get("status"),
                "assignee": entry.get("assignee"),
                "due_date": entry.get("due_date"),
                "link_state": status["state"],
                "reasons": status["reasons"],
            }
        )
    return queue


def qa_summary(instance: dict, root: Path, as_of: str = "") -> dict:
    """Counts, dated queue, unknowns, and stale answers.

    Overdue is relative to an explicit as-of date only;
    with none given, nothing is called overdue.
    """
    if as_of and not _valid_date(as_of):
        raise CaseError(f"{as_of!r} is not a YYYY-MM-DD date")
    questions = instance.get("questions") or []
    by_status: dict[str, int] = {}
    for entry in questions:
        status = entry.get("status") or "asked"
        by_status[status] = by_status.get(status, 0) + 1
    waiting = [q for q in questions if q.get("status") in ("asked", "clarify")]
    dated = sorted(
        (q for q in waiting if q.get("due_date")),
        key=lambda q: (q.get("due_date"), q.get("qa_id")),
    )
    upcoming = [
        {
            "qa_id": q.get("qa_id"),
            "question": q.get("question"),
            "due_date": q.get("due_date"),
            "overdue": bool(as_of and q["due_date"] < as_of),
        }
        for q in dated
    ]
    unknown_dated = [
        {"qa_id": q.get("qa_id"), "question": q.get("question")}
        for q in waiting
        if not q.get("due_date")
    ]
    stale = []
    for entry in questions:
        status = qa_status(instance, root, entry)
        if status["state"] != "current":
            stale.append(
                {
                    "qa_id": entry.get("qa_id"),
                    "state": status["state"],
                    "reasons": status["reasons"],
                }
            )
    return {
        "counts": by_status,
        "queue": qa_queue(instance, root),
        "upcoming": upcoming,
        "unknown_dated": unknown_dated,
        "overdue": sum(1 for u in upcoming if u["overdue"]) if as_of else None,
        "as_of": as_of or None,
        "stale": stale,
    }


def qa_snapshot(instance: dict, data: dict) -> dict:
    """Content hash a Q&A review binds (recomputed, never stored)."""
    from quality.records import evidence_fingerprint

    return {
        "content": _stable({"questions": instance.get("questions")}),
        "evidence": evidence_fingerprint(data),
        "template_version": instance.get("template_version"),
    }


def qa_diff(instance: dict, old: int, new: int) -> dict:
    """Before/after comparison between two tracker versions."""
    versions = {v.get("v"): v for v in instance.get("versions") or []}
    if old not in versions or new not in versions:
        raise CaseError(f"versions {old}/{new} are not in this instance")
    before = {q.get("qa_id"): q for q in versions[old].get("questions") or []}
    after = {q.get("qa_id"): q for q in versions[new].get("questions") or []}
    changes = []
    for qa_id in sorted(set(before) | set(after)):
        if qa_id not in before:
            changes.append({"qa_id": qa_id, "change": "added"})
            continue
        if qa_id not in after:
            changes.append({"qa_id": qa_id, "change": "removed"})
            continue
        first, second = before[qa_id], after[qa_id]
        for key in (
            "question", "asker", "topic", "assignee", "due_date",
            "answer", "support", "status", "resolution",
        ):
            if first.get(key) != second.get(key):
                changes.append(
                    {
                        "qa_id": qa_id,
                        "change": key,
                        "before": first.get(key),
                        "after": second.get(key),
                    }
                )
    return {"old": old, "new": new, "changes": changes}


def qa_export(
    instance: dict,
    data: dict,
    root: Path,
    version: int | None = None,
) -> dict:
    """Versioned JSON report; historical exports stay frozen."""
    from quality.records import evidence_fingerprint

    versions = instance.get("versions") or []
    if version is None:
        questions = instance.get("questions") or []
        historical = False
    else:
        match = next((v for v in versions if v.get("v") == version), None)
        if match is None:
            raise CaseError(f"version {version} is not in this instance")
        questions = match.get("questions") or []
        historical = True
    out_questions = []
    for entry in questions:
        limitations = []
        if not historical:
            limitations.extend(qa_status(instance, root, entry)["reasons"])
        if entry.get("status") in _QA_NEXT:
            limitations.append(
                f"status: {entry.get('status')} "
                f"(next: {_QA_NEXT[entry['status']]})"
            )
        if not entry.get("due_date"):
            limitations.append("due date unknown")
        if entry.get("answer") and not entry.get("support"):
            limitations.append("no supporting documents cited")
        out_questions.append(
            {
                "qa_id": entry.get("qa_id"),
                "question": entry.get("question"),
                "asker": entry.get("asker"),
                "topic": entry.get("topic"),
                "assignee": entry.get("assignee"),
                "due_date": entry.get("due_date"),
                "answer": entry.get("answer"),
                "support": entry.get("support"),
                "status": entry.get("status"),
                "resolution": entry.get("resolution"),
                "limitations": limitations,
            }
        )
    summary = {} if historical else qa_summary(instance, root)
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
        "questions": out_questions,
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
            "synthetic demonstration: questions and answers "
            "are reviewer-logged records",
            "answers cite inbox documents; changed or missing "
            "documents stale them",
            "overdue is relative to an explicit as-of date only",
        ]
        + (["historical version: liveness was not recomputed"] if historical else []),
    }


register(
    TEMPLATE_QA,
    snapshot=qa_snapshot,
    open=open_qa_instance,
    diff=qa_diff,
    export=qa_export,
    export_takes_root=True,
    count_key="questions",
    review_keys=('questions',),
)

__all__ = ['QA_STATUS', '_QA_NEXT', '_QA_DECISIONS', 'open_qa_instance', 'qa_get_question', '_qa_version', '_qa_touch', '_next_qa_id', 'add_qa', 'set_qa', 'answer_qa', 'decide_qa', 'qa_status', 'qa_queue', 'qa_summary', 'qa_snapshot', 'qa_diff', 'qa_export']
