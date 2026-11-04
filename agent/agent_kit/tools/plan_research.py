"""plan_research: journal one research query with its routed plan.

The query class, scope, branch, and target map to an explicit plan:
repositories, ordered steps, blocking needs, and warnings. Queries log
open, or closed in one call when the work is quick. Observed
contribution is recorded at resolution.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..case_record import QUERY_OUTCOMES, CaseError
from ..sources import QUERY_CLASSES, route

if TYPE_CHECKING:
    from . import Context

DEFINITION = {
    "type": "function",
    "name": "plan_research",
    "description": "Journal one research query with its routed plan: what remains "
    "uncertain, what could answer it, which repositories or flows apply, and "
    "what is still missing. Log it open, or closed with its outcome when the "
    "work is quick.",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "query_class": {
                "type": "string",
                "enum": list(QUERY_CLASSES),
            },
            "target": {
                "type": "string",
                "description": "what would answer it",
            },
            "issue": {
                "type": "string",
                "description": "checklist item it could affect, if any",
            },
            "scope": {
                "type": "string",
                "description": "legal scope, e.g. California (legal classes only)",
            },
            "branch": {
                "type": "string",
                "description": "branch id, for branch-dependent research",
            },
            "events_date": {
                "type": "string",
                "description": "event date as YYYY-MM-DD, when version matters",
            },
            "outcome": {
                "type": "string",
                "enum": list(QUERY_OUTCOMES),
                "description": "close in one call: answered, unanswered, blocked",
            },
            "summary": {
                "type": "string",
                "description": "required when closing: what it contributed",
            },
            "coverage": {
                "type": "string",
                "description": "what was searched and what was inaccessible",
            },
        },
        "required": ["query_class", "target"],
    },
}


def run(ctx: Context, args: dict) -> dict:
    case = ctx.case
    branch_id = str(args.get("branch", "") or "")
    branch = None
    if branch_id:
        branches = case.data.get("branches", {})
        if branch_id not in branches:
            raise CaseError(f"unknown branch {branch_id!r}")
        branch = branches[branch_id]
    fields = (case.data.get("matter", {}) or {}).get("fields", {})
    values = {
        name: entry.get("value", "")
        for name, entry in fields.items()
        if isinstance(entry, dict)
    }
    transaction = values.get("transaction_type", "") or values.get(
        "party_capacity", ""
    )
    plan = route(
        str(args.get("query_class", "")),
        {
            "scope": str(args.get("scope", "") or ""),
            "regime": (branch or {}).get("regime", ""),
            "transaction": transaction,
            "events_date": str(args.get("events_date", "") or ""),
            "connecting": bool(values),
            "clause": (branch or {}).get("basis_kind") == "clause",
        },
    )
    entry = case.log_query(
        query_class=str(args.get("query_class", "")),
        target=str(args.get("target", "")),
        plan=plan,
        issue=str(args.get("issue", "") or ""),
        scope=str(args.get("scope", "") or ""),
        regime=(branch or {}).get("regime", ""),
        branch=branch_id,
        outcome=str(args.get("outcome", "") or ""),
        summary=str(args.get("summary", "") or ""),
        coverage=str(args.get("coverage", "") or ""),
    )
    return {"query": entry["id"], "plan": plan}
