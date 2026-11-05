"""update_plan: the investigation plan. Every revision is persisted to the case record."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..case_record import CaseError

if TYPE_CHECKING:
    from . import Context

DEFINITION = {
    "type": "function",
    "name": "update_plan",
    "description": "Replace the investigation plan. Cover every checklist item; say what you will check next for each.",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {"plan": {"type": "array", "items": {"type": "string"}}},
        "required": ["plan"],
    },
}


def run(ctx: Context, args: dict) -> dict:
    steps = args.get("plan")
    if not isinstance(steps, list) or not all(isinstance(step, str) for step in steps):
        raise CaseError("update_plan plan must be a list of strings")
    ctx.case.record_plan(steps)
    return {"plan": steps}
