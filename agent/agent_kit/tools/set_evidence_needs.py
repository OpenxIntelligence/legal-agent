"""set_evidence_needs: freeze what an issue needs before it can conclude."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from . import Context

DEFINITION = {
    "type": "function",
    "name": "set_evidence_needs",
    "description": "Declare what a checklist item needs before a finding "
    "on it can be asserted: required passage sets (each needs at "
    "least one recorded), adverse passages that should surface, "
    "and superseded passages that must not decide. The filing "
    "gate holds asserted findings to these needs.",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "item": {"type": "string", "description": "checklist item id"},
            "required": {
                "type": "array",
                "description": "each {label, passages: [[doc, page]]}",
                "items": {"type": "object"},
            },
            "adverse": {
                "type": "array",
                "description": "passages that should surface",
                "items": {"type": "array"},
            },
            "superseded": {
                "type": "array",
                "description": "passages that must not decide",
                "items": {"type": "array"},
            },
        },
        "required": ["item", "required"],
    },
}


def run(ctx: Context, args: dict) -> dict:
    from ..case_record import CaseError

    body = args if isinstance(args, dict) else {}
    required = body.get("required", [])
    adverse = body.get("adverse")
    superseded = body.get("superseded")
    if not isinstance(required, list):
        raise CaseError("set_evidence_needs needs a required list")
    for name, value in (("adverse", adverse), ("superseded", superseded)):
        if value is not None and not isinstance(value, list):
            raise CaseError(f"set_evidence_needs needs a {name} list")
    needs = ctx.case.set_evidence_needs(
        str(body.get("item", "")),
        list(required),
        adverse=list(adverse) if adverse is not None else None,
        superseded=list(superseded) if superseded is not None else None,
    )
    return {"needs": needs}
