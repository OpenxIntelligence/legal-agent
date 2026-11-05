"""submit_memo: file the case memo.

Verification off: the memo is filed unverified and the run ends (answered).
Verification on: the memo rules (verification/verify_memo.py) run first, then the
reasoning gate (verification/verify_reasoning.py), then the quality review
(quality/controller.py), then the claims check
(verification/verify_claims.py). A memo that fails is refused with the problems; after
MAX_REFUSALS refused memos the run escalates to a person. A memo returned for
repair is not a refusal: the agent revises and resubmits. A memo whose quality
review cannot complete pauses the run for a later retry (qc_incomplete); a memo
whose review budget is exhausted while obligations stand open escalates to a
person (qc_exhausted). A memo that passes waits for attorney sign-off
(awaiting_signoff), carrying its retained QC obligations. A claims check that
cannot be completed (unavailable) pauses the run for a later retry instead: the
memo is not filed and the attempt does not count as a refusal.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

from quality.staleness import fact_index as _snapshot_facts

from .request_evidence import escalate

if TYPE_CHECKING:
    from . import Context

MAX_REFUSALS = 3

ITEM = {
    "type": "string",
    "description": "id of an item in your checklist (set_checklist)",
}
CITATION = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "document_id": {"type": "string"},
        "page": {"type": "integer"},
        "quote": {"type": "string"},
    },
    "required": ["document_id", "page", "quote"],
}
FINDING = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "item": ITEM,
        "status": {"type": "string", "enum": ["asserted", "unresolved", "escalated"]},
        "summary": {"type": "string"},
        "citations": {"type": "array", "items": CITATION, "maxItems": 3},
        "kind": {
            "type": "string",
            "enum": ["fact", "legal_rule", "applicability", "inference"],
            "description": "what the finding claims (default fact)",
        },
        "branch": {
            "type": "string",
            "description": "branch id, when the finding depends on a governing regime",
        },
    },
    "required": ["item", "status", "summary", "citations"],
}

DEFINITION = {
    "type": "function",
    "name": "submit_memo",
    "description": "File the case memo: the answer to the question in 2-3 sentences, one finding per checklist item "
    "in one sentence citing its 1-3 strongest recorded facts, and the remaining gaps as a short list.",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "conclusion": {"type": "string"},
            "findings": {"type": "array", "items": FINDING},
            "gaps": {"type": "string"},
            "depends_on": {
                "type": "array",
                "items": {"type": "string"},
                "description": "branch ids the conclusion depends on, if any",
            },
            "addresses": {
                "type": "array",
                "items": {"type": "string"},
                "description": "reviewer comment targets this revision addresses, if any",
            },
        },
        "required": ["conclusion", "findings", "gaps"],
    },
}


def run(ctx: Context, args: dict) -> dict:
    from .. import task_understanding
    if task_understanding.refresh(ctx.case)['stale']:
        return {'filed': False, 'error': 'Task interpretation needs reassessment after new user direction.',
                'next_action': 'Compare original request and delivered directions, reassess affected findings, and revise update_task_understanding before submitting.'}
    depends = args.get("depends_on") or []
    if not isinstance(depends, list):
        depends = []
    memo = {
        "conclusion": str(args.get("conclusion", "")),
        "findings": args.get("findings") or [],
        "gaps": str(args.get("gaps", "")),
        "depends_on": [d for d in depends if isinstance(d, str)],
    }
    addresses = args.get("addresses") or []
    if not isinstance(addresses, list):
        addresses = []
    addresses = [str(a) for a in addresses if isinstance(a, str) and a.strip()]
    if not ctx.verification:
        ctx.case.file_memo(memo, "unverified", addresses=addresses)
        return {"filed": True, "memo": "unverified", "status": "answered"}
    from verification import verify_claims

    verify_memo = importlib.import_module(
        "verification.verify_memo"
    )  # yours, built in Session 2
    verify_reasoning = importlib.import_module("verification.verify_reasoning")
    check, result = "memo", verify_memo.run_verification(ctx, memo)
    qc_off = False
    reasoning_advisories: list = []
    if result.get("passed"):
        ctx.case.record_memo_check("memo", [])
        check, result = "reasoning", verify_reasoning.run_verification(ctx, memo)
    if result.get("passed") and check == "reasoning":
        reasoning_advisories = [
            str(a) for a in result.get("advisory") or []
        ]
        if result.get("mode") in ("reasoning", "identified"):
            # Engaged passes stay on the audit record; legacy runs
            # (gate not engaged) keep the memo/claims sequence they had.
            ctx.case.record_memo_check(
                "reasoning", [], advisories=reasoning_advisories
            )
        from agent_kit.settings import qc_enabled
        from quality import controller as qc

        if not qc_enabled():
            qc_off = True
            ctx.case._save("qc_skipped", "system", {"reason": "QC_ENABLED=off"})
            check, result = "claims", verify_claims.run_verification(ctx, memo)
        else:
            qc_outcome = qc.review(ctx, memo)
            if qc_outcome["verdict"] == "repair":
                return {
                    "error": "memo returned for repair",
                    "check": "qc",
                    "problems": qc_outcome["repair_feedback"],
                    "qc_review": qc_outcome["review"]["id"],
                }
            if qc_outcome["verdict"] == "incomplete":
                reasons = "; ".join(qc_outcome["review"].get("state_reasons", []))
                problems = (
                    [f"quality review incomplete: {reasons}"]
                    if reasons
                    else ["quality review incomplete: a required check did not run"]
                )
                opens = qc_outcome["review"].get("open_tasks", [])
                if opens:
                    problems.append(f"obligations retained: {', '.join(opens)}")
                return {
                    "status": "paused",
                    "reason": "qc_incomplete",
                    "check": "qc",
                    "outcome": "incomplete",
                    "problems": problems,
                    "qc_review": qc_outcome["review"]["id"],
                    "error": (
                        "quality review incomplete: "
                        + problems[0][:600]
                        + " The memo was not filed; retry submit_memo "
                        "with the same memo once the check can run."
                    ),
                }
            if qc_outcome["verdict"] == "deferred" and not _apply_waiver(
                ctx, qc_outcome["review"]
            ):
                return _escalate_exhaustion(ctx, qc_outcome["review"])
            check, result = "claims", verify_claims.run_verification(ctx, memo)
    outcome = result.get("outcome")
    if check != "claims" or not outcome:
        outcome = "passed" if result.get("passed") else "failed"
    problems = (
        []
        if result.get("passed")
        else [str(p) for p in result.get("feedback") or ["refused"]]
    )
    regressions: list = []
    if check == "claims":
        regressions = _claim_regressions(
            ctx.case.data.get("memo_checks", []), result.get("verdicts") or {}
        )
        ctx.case.record_memo_check(
            check,
            problems,
            outcome=outcome,
            scope=result.get("scope"),
            verifier=result.get("verifier"),
            support=result.get("support"),
            units=result.get("verdicts"),
            regressions=regressions,
            fact_index=_snapshot_facts(ctx.case.data.get("facts", [])),
            # The claims record is the last word before sign-off, so
            # it carries the gate's advisories even when no separate
            # reasoning record was written (legacy mode).
            advisories=reasoning_advisories,
        )
    else:
        ctx.case.record_memo_check(check, problems)
    if check == "claims" and outcome == verify_claims.OUTCOME_UNAVAILABLE:
        detail = "; ".join(problems) if problems else "no reason given"
        return {
            "status": "paused",
            "reason": "verification_unavailable",
            "check": "claims",
            "outcome": outcome,
            "scope": result.get("scope"),
            "verifier": result.get("verifier"),
            "support": result.get("support"),
            "problems": problems,
            "error": (
                f"claims check unavailable: {detail[:600]}. The memo was not "
                "filed and this does not count as a refusal; retry submit_memo "
                "with the same memo."
            ),
        }
    if problems:
        if ctx.case.memo_refusals() >= MAX_REFUSALS:
            checklist = ctx.case.data["checklist"]
            item = next(
                (k for k, v in checklist.items() if v["status"] == "open"),
                next(iter(checklist), ""),
            )
            need = (
                f"The memo was refused {ctx.case.memo_refusals()} times. Last problems: "
                + "; ".join(problems)
            )
            return escalate(ctx, item, need, "memo_refused")
        refused: dict = {
            "error": "memo refused",
            "check": check,
            "problems": problems,
            "conditions": ctx.case.memo_branch_conditions(memo),
        }
        if check == "claims":
            refused.update(
                {
                    "outcome": outcome,
                    "scope": result.get("scope"),
                    "verifier": result.get("verifier"),
                    "support": result.get("support"),
                    "regressions": regressions,
                }
            )
        return refused
    verdicts = result.get("verdicts") or {}
    supported = [
        finding
        for finding in memo.get("findings") or []
        if isinstance(finding, dict)
        and finding.get("status") in ("asserted", "unresolved")
        and verdicts.get(f"finding:{finding.get('item')}") == "supported"
    ]
    ctx.case.mark_answered(
        [finding.get("item") for finding in supported],
        {finding.get("item"): finding.get("status") for finding in supported},
    )
    ctx.case.file_memo(memo, "awaiting_signoff", addresses=addresses)
    tasks = ctx.case.data.get("repair_tasks", {})
    return {
        "filed": True,
        "memo": "awaiting_signoff",
        "status": "awaiting_signoff",
        "check": "claims",
        "advisories": reasoning_advisories,
        "outcome": outcome,
        "scope": result.get("scope"),
        "verifier": result.get("verifier"),
        "support": result.get("support"),
        "regressions": regressions,
        "conditions": ctx.case.memo_branch_conditions(memo),
        "qc": {
            "enabled": not qc_off,
            "reviews": len(ctx.case.data.get("qc_reviews", [])),
            "open": sorted(k for k, t in tasks.items() if t["status"] == "open"),
            "unresolved": sorted(
                k for k, t in tasks.items() if t["status"] == "unresolved"
            ),
        },
    }


def _claim_regressions(prior_checks: list, verdicts: dict) -> list:
    """Units supported at the last claims check that are unsupported now.

    A repair that breaks a previously correct unit is damage, not just a
    fresh defect: the revision still has to satisfy the claims gate, and
    the regression stays on the record either way.
    """
    previous: dict = {}
    for record in prior_checks:
        if (
            isinstance(record, dict)
            and record.get("check") == "claims"
            and isinstance(record.get("units"), dict)
        ):
            previous = record["units"]
    return [
        {"unit": unit, "was": "supported", "now": "unsupported"}
        for unit, verdict in verdicts.items()
        if previous.get(unit) == "supported" and verdict == "unsupported"
    ]


def _apply_waiver(ctx: Context, review: dict) -> bool:
    """Record a human disposition of exhausted-review obligations.

    A qc_exhausted request the reviewer answered disposes the open
    obligations: they stay visible as unresolved with disposition
    "waived" (never silently dropped), and submission proceeds to the
    claims check, which still gates the memo. Returns True when a
    waiver applied.
    """
    answered = [
        r
        for r in ctx.case.data.get("requests", [])
        if r.get("rule") == "qc_exhausted" and r.get("status") == "answered"
    ]
    if not answered:
        return False
    request = answered[-1]
    tasks = ctx.case.data.get("repair_tasks", {})
    waived = []
    for key in review.get("open_tasks", []):
        task = tasks.get(key)
        if task is None or task["status"] != "open":
            continue
        task["status"] = "unresolved"
        task["disposition"] = "waived"
        task["history"].append(
            f"waived by {request['id']}: {str(request.get('response', ''))[:200]}"
        )
        waived.append(key)
    ctx.case._save(
        "qc_waived", "human", {"request": request["id"], "tasks": waived}
    )
    return True


def _escalate_exhaustion(ctx: Context, review: dict) -> dict:
    """Escalate review-budget exhaustion with the retained obligations."""
    opens = review.get("open_tasks", [])
    item = _obligation_item(ctx, opens)
    need = (
        "The quality-review budget is exhausted with open obligations: "
        + "; ".join(opens)
        + ". Advise how to proceed: release evidence, accept a stated "
        "limitation, or direct the next revision."
    )
    return escalate(ctx, item, need, "qc_exhausted")


def _obligation_item(ctx: Context, opens: list) -> str:
    """A checklist item to anchor the qc_exhausted request."""
    checklist = ctx.case.data.get("checklist", {})
    for key in opens:
        if key.startswith("checklist-scope:"):
            item = key.split(":", 1)[1]
            if item in checklist:
                return item
        if key.startswith("counterevidence:finding:"):
            item = key.split(":")[2]
            if item in checklist:
                return item
    if checklist:
        return next(iter(checklist))
    return ""
