"""resolve_query: close an open research query with what it contributed."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..case_record import QUERY_OUTCOMES

if TYPE_CHECKING:
    from . import Context

DEFINITION = {
    "type": "function",
    "name": "resolve_query",
    "description": "Close an open research query: whether it was answered, "
    "unanswered, or blocked, what it contributed, and what coverage it had.",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "query": {"type": "string", "description": "query id, e.g. Q1"},
            "outcome": {
                "type": "string",
                "enum": list(QUERY_OUTCOMES),
            },
            "summary": {"type": "string"},
            "coverage": {
                "type": "string",
                "description": "what was searched and what was inaccessible",
            },
        },
        "required": ["query", "outcome", "summary"],
    },
}


def run(ctx: Context, args: dict) -> dict:
    entry = ctx.case.resolve_query(
        str(args.get("query", "")),
        str(args.get("outcome", "")),
        str(args.get("summary", "")),
        str(args.get("coverage", "") or ""),
    )
    return {"resolved": entry}
