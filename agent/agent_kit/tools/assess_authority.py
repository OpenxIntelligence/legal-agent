"""assess_authority: record one verification act for an authority.

A "verified" status claims someone looked at the source and
confirmed the proposition there. This tool records that
look — page, exact quote, whether treatment was read — and
the quote is re-checked against the transcript rather than
trusted. On success the assessment links onto the authority
and flips it to verified. Register provisional first.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._arguments import boolean_arg

if TYPE_CHECKING:
    from . import Context

DEFINITION = {
    "type": "function",
    "name": "assess_authority",
    "description": "Verify a registered authority against its source: give "
    "the A-id, the page, and the exact quote confirming the "
    "proposition. Treatment sources must have been read "
    "(treatment_read true) or the assessment is refused. "
    "The quote is re-checked; success links the assessment "
    "and marks the authority verified.",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "authority_id": {
                "type": "string",
                "description": "registered A-id to verify",
            },
            "page": {
                "type": "integer",
                "description": "page where the proposition was confirmed",
            },
            "quote": {
                "type": "string",
                "description": "exact words confirming the proposition",
            },
            "treatment_read": {
                "type": "boolean",
                "description": "whether treatment sources were actually read",
            },
            "detail": {
                "type": "string",
                "description": "what was checked and what remains open",
            },
        },
        "required": ["authority_id", "page", "quote", "treatment_read"],
    },
}


def run(ctx: Context, args: dict) -> dict:
    record = ctx.case.assess_authority(
        str(args.get("authority_id", "")),
        page=args.get("page", 0),
        quote=str(args.get("quote", "")),
        treatment_read=boolean_arg("assess_authority", args, "treatment_read"),
        detail=str(args.get("detail", "")),
    )
    return {
        "assessed": record["id"],
        "authority": record["authority"],
        "verification": "verified",
    }
