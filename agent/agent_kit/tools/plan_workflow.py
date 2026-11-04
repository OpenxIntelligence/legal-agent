"""plan_workflow: select the workflow components and their order.

Each component names a catalog workflow, its purpose in this
matter, and which earlier components it runs after. Coordinates
multidisciplinary work in dependency order instead of one generic
flow.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..reasoning import WORKFLOWS

if TYPE_CHECKING:
    from . import Context

COMPONENT = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "workflow": {
            "type": "string",
            "enum": sorted(WORKFLOWS),
        },
        "purpose": {
            "type": "string",
            "description": "why this component is needed in this matter",
        },
        "after": {
            "type": "array",
            "items": {"type": "string"},
            "description": "components this runs after, if any",
        },
    },
    "required": ["workflow", "purpose"],
}

DEFINITION = {
    "type": "function",
    "name": "plan_workflow",
    "description": "Select the legal workflow for the identified task: which catalog "
    "components apply, why each is needed, and what runs after what. Planning "
    "again replaces the plan.",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "components": {
                "type": "array",
                "items": COMPONENT,
                "minItems": 1,
            },
            "rationale": {
                "type": "string",
                "description": "why this combination fits the identified task",
            },
        },
        "required": ["components"],
    },
}


def run(ctx: Context, args: dict) -> dict:
    plan = ctx.case.plan_workflow(
        components=args.get("components") or [],
        rationale=str(args.get("rationale", "") or ""),
    )
    return {
        "planned": [c["workflow"] for c in plan["components"]],
    }
