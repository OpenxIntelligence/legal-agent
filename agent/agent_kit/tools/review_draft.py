"""review_draft: check a draft memo without filing it.

Runs the memo rules, then the reasoning gate, then the claims
check, on a draft: early feedback while evidence is still being
gathered. A draft never files, never counts as a refusal, never
settles an item, and never ends the run; the filing gate still
runs its own checks at submit_memo. Each draft assessment is
recorded with the draft and evidence versions it saw.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

from .submit_memo import FINDING

if TYPE_CHECKING:
    from . import Context

DEFINITION = {
    "type": "function",
    "name": "review_draft",
    "description": "Check a draft memo the way submit_memo would — memo rules first, "
    "then the reasoning gate, then the claims check — without filing it. Use after gathering evidence for "
    "several items, to find unsupported assertions while turns remain to fix them. "
    "A draft never files, never counts as a refusal, and never settles an item; "
    "filing still runs its own checks.",
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
                "description": "branch ids the draft conclusion depends on, if any",
            },
        },
        "required": ["conclusion", "findings", "gaps"],
    },
}


def run(ctx: Context, args: dict) -> dict:
    depends = args.get("depends_on") or []
    if not isinstance(depends, list):
        depends = []
    memo = {
        "conclusion": str(args.get("conclusion", "")),
        "findings": args.get("findings") or [],
        "gaps": str(args.get("gaps", "")),
        "depends_on": [d for d in depends if isinstance(d, str)],
    }
    from verification import verify_claims

    verify_memo = importlib.import_module("verification.verify_memo")
    verify_reasoning = importlib.import_module("verification.verify_reasoning")
    rules = verify_memo.run_verification(ctx, memo)
    if not rules.get("passed"):
        problems = [str(p) for p in rules.get("feedback") or ["refused"]]
        draft = ctx.case.record_draft_check(
            check="memo",
            passed=False,
            outcome="failed",
            problems=problems,
            memo=memo,
        )
        return {
            "draft": draft["n"],
            "check": "memo",
            "passed": False,
            "outcome": "failed",
            "problems": problems,
            "conditions": ctx.case.memo_branch_conditions(memo),
            "facts_since_assessment": ctx.case.data.get(
                "facts_since_assessment", 0
            ),
        }
    reasoning = verify_reasoning.run_verification(ctx, memo)
    if not reasoning.get("passed"):
        problems = [str(p) for p in reasoning.get("feedback") or ["refused"]]
        draft = ctx.case.record_draft_check(
            check="reasoning",
            passed=False,
            outcome="failed",
            problems=problems,
            memo=memo,
        )
        return {
            "draft": draft["n"],
            "check": "reasoning",
            "passed": False,
            "outcome": "failed",
            "problems": problems,
            "conditions": ctx.case.memo_branch_conditions(memo),
            "facts_since_assessment": ctx.case.data.get(
                "facts_since_assessment", 0
            ),
        }
    advisories = [str(a) for a in reasoning.get("advisory") or []]
    result = verify_claims.run_verification(ctx, memo)
    passed = bool(result.get("passed"))
    outcome = result.get("outcome") or ("passed" if passed else "failed")
    problems = (
        [] if passed else [str(p) for p in result.get("feedback") or ["refused"]]
    )
    draft = ctx.case.record_draft_check(
        check="claims",
        passed=passed,
        outcome=outcome,
        problems=problems,
        memo=memo,
        scope=result.get("scope"),
        verifier=result.get("verifier"),
        advisories=advisories,
    )
    return {
        "draft": draft["n"],
        "check": "claims",
        "passed": passed,
        "outcome": outcome,
        "problems": problems,
        "advisories": advisories,
        "scope": result.get("scope"),
        "verifier": result.get("verifier"),
        "conditions": ctx.case.memo_branch_conditions(memo),
        "facts_since_assessment": ctx.case.data.get("facts_since_assessment", 0),
    }
