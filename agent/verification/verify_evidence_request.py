"""is an evidence request allowed? Called by the request_evidence tool.

    run_verification(ctx, request) -> {"passed": bool, "feedback": str}

request = {"item", "need", "purpose"?, "target"?, "rationale"?, "read_exception"?}.
The item must be on the checklist, with no request for it still open.
Purpose and target may be supplied structurally; when absent, purpose is
inferred from the need text as a fallback (deprecated: wording must not
decide investigative meaning, so the fallback only ever grants the
same permissions the text markers describe).

Duplicates are decided by need identity (issue + purpose + target),
not wording: a paraphrase of the same need is still a duplicate, and
near-identical wording with a different material target is a new need.
Word-set similarity is reported as a hint only.

Read policy: unexamined documents block with an explanation, unless a
documented read exception is supplied. Failed reads recover by class:
transient failures retry (3 attempts), corrupt documents go straight
to replacement, restricted documents need an authorized decision, and
internal errors need diagnosis — never a guess-around.
"""

from __future__ import annotations

import re

from agent_kit import document_text
from agent_kit.case_record import (
    MAX_READ_ATTEMPTS_PER_DOC,
    REPLACEMENT_AFTER,
    REQUEST_PURPOSES,
    effective_status,
)

PURPOSE_MARKERS = (
    ("contradiction", ("contradict",)),
    ("corroboration", ("corroborat",)),
    ("replacement", (
        "readable cop",
        "replacement",
        "unreadable",
        "could not be read",
        "cannot be read",
        "failed to read",
        "failed read",
    )),
    ("suspected_discrepancy", (
        "discrepancy",
        "discrepant",
        "inconsisten",
        "does not add up",
        "do not add up",
    )),
    ("missing_primary", (
        "attachment",
        "exhibit",
        "appendix",
        "missing primary",
        "referenced document",
        "referenced file",
    )),
)

DIFFERENT_NEED_SIMILARITY = 0.5


def purpose_of(need: str) -> str:
    """Fallback purpose inference from need text (deprecated).

    Prefer the structured `purpose` argument. When markers overlap,
    the most specific purpose wins; text with no markers is a
    follow-up.
    """
    text = (need or "").lower()
    for purpose, markers in PURPOSE_MARKERS:
        if any(marker in text for marker in markers):
            return purpose
    return "follow_up"


def needs_differ(first: str, second: str) -> bool:
    """Word-set Jaccard similarity below 0.5. A repetition hint only."""
    left, right = _words(first), _words(second)
    if not left or not right:
        return False
    return len(left & right) / len(left | right) < DIFFERENT_NEED_SIMILARITY


def need_identity(purpose: str, target: str) -> tuple:
    """The underlying need: purpose plus normalized material target."""
    return (purpose, re.sub(r"\s+", " ", (target or "").strip().lower()))


def run_verification(ctx, request: dict) -> dict:
    case, item = ctx.case, str(request.get("item", ""))
    need = str(request.get("need", ""))
    target = str(request.get("target", "") or "")
    rationale = str(request.get("rationale", "") or "")
    exception = str(request.get("read_exception", "") or "")
    declared = request.get("purpose")
    if declared is not None and declared not in REQUEST_PURPOSES:
        return _refuse(
            f"purpose {declared!r} must be one of: {', '.join(REQUEST_PURPOSES)}"
        )
    entry = case.data["checklist"].get(item)
    if entry is None:
        return _refuse(f"unknown checklist item {item!r}")
    prior = [r for r in case.data["requests"] if r.get("item") == item]
    purpose = declared or purpose_of(need)
    if not prior and purpose == "follow_up":
        purpose = "initial"
    if any(r.get("status") == "open" for r in prior):
        rid = next(r.get("id") for r in prior if r.get("status") == "open")
        return _refuse(
            f"request {rid} for {item} is still open; "
            "wait for the reply instead of asking again"
        )
    status = effective_status(entry, case.data.get("memo_checks", []))
    if status == "answered" and purpose not in (
        "corroboration",
        "contradiction",
        "suspected_discrepancy",
    ):
        return _refuse(
            f"{item} is already answered; do not escalate it unless you need "
            "corroboration from an independent source, must resolve a "
            "contradiction, or have a grounded new discrepancy to check"
        )
    if purpose == "contradiction" and item not in case.contested_items():
        return _refuse(
            f"no collected contradiction for {item}; record the opposing "
            "evidence, or file a suspected_discrepancy request with a "
            "grounded rationale, before asking for contradiction resolution"
        )
    if purpose == "suspected_discrepancy":
        grounded = _grounding(ctx, rationale or need)
        if grounded is None:
            return _refuse(
                "a suspected discrepancy needs a grounded rationale naming "
                "collected evidence (a document or fact id); "
                "suspicion alone cannot open a request"
            )
    if purpose == "missing_primary" and not target.strip():
        return _refuse(
            "a missing-primary request must name its target: which document, "
            "attachment, page, or version is missing"
        )
    if prior:
        latest = prior[-1]
        visible_now = [d["id"] for d in document_text.list_documents(ctx.root)]
        fresh = [d for d in visible_now if d not in (latest.get("visible_ids") or [])]
        if not fresh and not _legitimate_repeat(prior, purpose, target, need):
            hint = ""
            if not needs_differ(need, str(latest.get("need", ""))):
                hint = " It reads like a paraphrase of the same need."
            return _refuse(
                f"already asked for {item} in {latest.get('id')}; "
                "no documents were released since that request, and this "
                f"asks the same {purpose} need"
                + (f" for {target!r}" if target.strip() else "")
                + "."
                + hint
                + " A repeat needs a different purpose, a different "
                "material target, or newly released documents."
            )
    return _read_gate(ctx, case, item, purpose, target, exception)


def _legitimate_repeat(prior: list, purpose: str, target: str, need: str) -> bool:
    """A repeat without fresh releases is legitimate only as a new need."""
    latest = prior[-1]
    latest_identity = need_identity(
        str(latest.get("purpose") or "follow_up"), str(latest.get("target") or "")
    )
    return need_identity(purpose, target) != latest_identity


def _grounding(ctx, text: str) -> str | None:
    """A collected-evidence id named in the text, or None when ungrounded."""
    lowered = (text or "").lower()
    known = {d["id"] for d in document_text.list_documents(ctx.root)}
    known |= {
        f.get("id", "")
        for f in ctx.case.data.get("facts", [])
        if isinstance(f, dict)
    }
    for identifier in sorted(known):
        if identifier and str(identifier).lower() in lowered:
            return str(identifier)
    return None


def _read_gate(ctx, case, item: str, purpose: str, target: str, exception: str) -> dict:
    read = case.data.get("read_ids", [])
    failed = case.data.get("failed_reads", {})
    visible = [d["id"] for d in document_text.list_documents(ctx.root)]
    unread = [d for d in visible if d not in read and not case.flagged(d)]
    failed_unread = [d for d in unread if d in failed]
    fresh_unread = [d for d in unread if d not in failed]
    blocking_classes = {
        d: case.failure_class(d) for d in failed_unread
    }
    restricted = [d for d, c in blocking_classes.items() if c == "restricted"]
    internal = [d for d, c in blocking_classes.items() if c == "internal"]
    if restricted:
        return _refuse(
            f"these documents are restricted: {', '.join(restricted)}. Request "
            "an authorized decision about access; re-reading cannot help and "
            "no read exception overrides a restriction"
        )
    if internal:
        details = "; ".join(f"{d} ({_note(failed[d])})" for d in internal)
        return _refuse(
            f"these reads failed with internal errors: {details}. Diagnose "
            "the failure before proceeding; do not guess around it"
        )
    if fresh_unread and not exception:
        return _refuse(
            "read every visible document before escalating; unread: "
            + ", ".join(fresh_unread)
            + ". Their relevance is unknown until read."
            + (
                f" Also unread after failures: {', '.join(failed_unread)}."
                if failed_unread
                else ""
            )
            + " If an unread document cannot matter to this request, "
            "explain why in read_exception."
        )
    recoverable = {
        d: c for d, c in blocking_classes.items() if c not in ("restricted", "internal")
    }
    if not recoverable:
        return {"passed": True, "feedback": ""}
    # A read exception covers unexamined documents, never failed recoveries.
    unexhausted = [
        d
        for d, c in recoverable.items()
        if case.read_attempts(d) < REPLACEMENT_AFTER.get(c, MAX_READ_ATTEMPTS_PER_DOC)
    ]
    if unexhausted:
        details = "; ".join(
            f"{d} ({_note(failed[d])}, {case.read_attempts(d)} attempts)"
            for d in unexhausted
        )
        return _refuse(
            f"these documents could not be read: {details}. "
            "Re-read them before escalating."
        )
    if purpose == "replacement":
        return {"passed": True, "feedback": ""}
    details = "; ".join(
        f"{d} ({_note(failed[d])})" for d in recoverable if d not in unexhausted
    ) or "; ".join(failed_unread)
    return _refuse(
        f"these documents could not be read: {details}. Request a readable "
        "replacement to proceed; re-reading exhausted documents will not help"
    )


def _words(text: str) -> set:
    return set(re.findall(r"[a-z0-9]+", (text or "").lower()))


def _note(entry) -> str:
    if isinstance(entry, dict):
        return str(entry.get("note", ""))
    return str(entry)


def _refuse(feedback: str) -> dict:
    return {"passed": False, "feedback": feedback}
