"""decide_approval: grant or deny a pending approval request.

Operator-only: one decision per request, recording who decided.
The model path is refused twice — the tool is not offered in
definitions() and tools.run() gates it out of names() — because
a model-supplied reviewer name is self-authorization. This run()
additionally refuses model actors for direct callers; the
trusted path is server-side case.decide_approval bound to the
recorded request.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..reasoning import APPROVAL_DECISIONS

if TYPE_CHECKING:
    from . import Context

DEFINITION = {
    "type": "function",
    "name": "decide_approval",
    "description": "Grant or deny a pending approval request, recording who "
    "decided. One decision per request.",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "approval_id": {"type": "string"},
            "decision": {
                "type": "string",
                "enum": list(APPROVAL_DECISIONS),
            },
            "decided_by": {
                "type": "string",
                "description": "who decided",
            },
            "note": {"type": "string"},
        },
        "required": ["approval_id", "decision", "decided_by"],
    },
}


def run(ctx: Context, args: dict) -> dict:
    actor = getattr(ctx, "actor", "model") or "model"
    if actor == "model":
        return {
            "error": "decide_approval is operator-only: a model call "
            "cannot grant or deny, because the reviewer name would be "
            "self-supplied. A human records the decision."
        }
    # The decider is the server-derived actor, never the caller's
    # claimed name: args cannot self-authorize through this path.
    claimed = str(args.get("decided_by", "") or "")
    if claimed and claimed != actor:
        return {
            "error": "decided_by does not match the authenticated "
            f"operator {actor!r}: refusing a confused-deputy decision"
        }
    entry = ctx.case.decide_approval(
        approval_id=str(args.get("approval_id", "")),
        decision=str(args.get("decision", "")),
        decided_by=actor,
        note=str(args.get("note", "") or ""),
    )
    return {"decided": entry["id"], "decision": entry["decision"]}
