"""triage_gap: decide how to close one information gap.

The gap's reference (an unresolved foundational question or an
unknown matter field) must already be recorded. Decisions:
investigate a named source, ask the user the smallest high-value
questions, analyze explicit alternative scenarios, or accept the
gap with disclosure. Never invent the missing fact.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..reasoning import TRIAGE_DECISIONS

if TYPE_CHECKING:
    from . import Context

DEFINITION = {
    "type": "function",
    "name": "triage_gap",
    "description": "Decide how to close one recorded information gap: investigate a "
    "source, ask the user, analyze scenarios, or accept with disclosure. The "
    "unresolved question or unknown field must be recorded first.",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "ref": {
                "type": "string",
                "description": "FQ id or matter:name of the recorded gap",
            },
            "gap": {"type": "string"},
            "needed_for": {
                "type": "string",
                "description": "what analysis this gap blocks or qualifies",
            },
            "sources_checked": {
                "type": "string",
                "description": "documents, tools, and research already tried",
            },
            "decision": {
                "type": "string",
                "enum": list(TRIAGE_DECISIONS),
            },
            "detail": {
                "type": "string",
                "description": "source, questions, scenarios, or acceptance reason",
            },
        },
        "required": ["ref", "gap", "needed_for", "sources_checked", "decision", "detail"],
    },
}


def run(ctx: Context, args: dict) -> dict:
    entry = ctx.case.triage_gap(
        ref=str(args.get("ref", "")),
        gap=str(args.get("gap", "")),
        needed_for=str(args.get("needed_for", "")),
        sources_checked=str(args.get("sources_checked", "")),
        decision=str(args.get("decision", "")),
        detail=str(args.get("detail", "")),
    )
    return {"triaged": entry["id"], "ref": entry["ref"], "decision": entry["decision"]}
