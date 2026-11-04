"""declare_work_product: declare the deliverable and its sections.

Names the work product for the identified task, the sections it
will contain, and how its conclusions trace to legal authorities,
facts, and assumptions. Declaring again replaces the declaration.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..reasoning import WORK_PRODUCTS

if TYPE_CHECKING:
    from . import Context

DEFINITION = {
    "type": "function",
    "name": "declare_work_product",
    "description": "Declare the deliverable for the identified task: which work "
    "product, its sections, and how conclusions trace to authorities, facts, and "
    "assumptions. Declaring again replaces the declaration.",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "product": {
                "type": "string",
                "enum": sorted(WORK_PRODUCTS),
            },
            "sections": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
            },
            "traceability": {
                "type": "string",
                "description": "how conclusions trace to authorities, facts, assumptions",
            },
        },
        "required": ["product", "sections", "traceability"],
    },
}


def run(ctx: Context, args: dict) -> dict:
    entry = ctx.case.declare_work_product(
        product=str(args.get("product", "")),
        sections=args.get("sections") or [],
        traceability=str(args.get("traceability", "")),
    )
    return {"declared": entry["product"], "sections": entry["sections"]}
