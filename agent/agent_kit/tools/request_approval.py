"""request_approval: request human approval for a consequential action.

Records the proposed action, its scope, and why it is needed.
No execute tools exist today, so requests document intent and
wait; decide_approval records the human decision.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from . import Context

DEFINITION = {
    "type": "function",
    "name": "request_approval",
    "description": "Request human approval for an action with external or "
    "irreversible consequences: the action, its scope, and why it is needed.",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "action": {"type": "string"},
            "scope": {"type": "string"},
            "reason": {"type": "string"},
        },
        "required": ["action", "scope", "reason"],
    },
}


def run(ctx: Context, args: dict) -> dict:
    entry = ctx.case.request_approval(
        action=str(args.get("action", "")),
        scope=str(args.get("scope", "")),
        reason=str(args.get("reason", "")),
    )
    return {"requested": entry["id"], "action": entry["action"]}
