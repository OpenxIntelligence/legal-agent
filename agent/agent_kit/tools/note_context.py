"""note_context: record one matter fact the assignment establishes.

Each fact carries a status — known, inferred, disputed, or unknown —
with provenance. Unknown fields carry no value and stay explicitly
listed until established. Noting context bumps the matter version;
completed tasks go stale, because conclusions may rest on it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..case_record import MATTER_STATUSES

if TYPE_CHECKING:
    from . import Context

DEFINITION = {
    "type": "function",
    "name": "note_context",
    "description": "Record one matter fact the assignment establishes: purpose, parties "
    "and roles, forum, key dates, agreements, or anything else the conclusions may "
    "depend on. Say whether it is known, inferred, disputed, or unknown, and where it "
    "came from. Unknown fields carry no value. Repeat notes are harmless.",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "field": {
                "type": "string",
                "description": "snake_case name, e.g. purpose, parties, forum",
            },
            "value": {"type": "string"},
            "status": {
                "type": "string",
                "enum": list(MATTER_STATUSES),
                "description": "known, inferred, disputed, or unknown (default known)",
            },
            "provenance": {
                "type": "string",
                "description": "where this came from: assignment text, a document, a reply",
            },
        },
        "required": ["field", "value"],
    },
}


def run(ctx: Context, args: dict) -> dict:
    noted = ctx.case.note_context(
        str(args.get("field", "")),
        str(args.get("value", "")),
        str(args.get("status", "known")),
        str(args.get("provenance", "") or ""),
    )
    return {
        "noted": {
            "field": noted["field"],
            "status": noted["status"],
            "matter_version": noted["matter_version"],
        },
        "unknown": ctx.case.matter_unknowns(),
    }
