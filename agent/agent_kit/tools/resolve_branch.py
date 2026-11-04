"""resolve_branch: move a branch to a new status with an explicit note."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..case_record import MATTER_STATUSES

if TYPE_CHECKING:
    from . import Context

DEFINITION = {
    "type": "function",
    "name": "resolve_branch",
    "description": "Move a governing-regime branch to a new status (known, inferred, "
    "disputed, unknown) with a note saying what established it.",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "branch": {"type": "string", "description": "branch id, e.g. B1"},
            "status": {
                "type": "string",
                "enum": list(MATTER_STATUSES),
            },
            "note": {"type": "string"},
        },
        "required": ["branch", "status", "note"],
    },
}


def run(ctx: Context, args: dict) -> dict:
    branch = ctx.case.resolve_branch(
        str(args.get("branch", "")),
        str(args.get("status", "")),
        str(args.get("note", "")),
    )
    return {"resolved": branch}
