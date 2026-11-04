"""open_branch: open one candidate governing regime for an issue.

Different issues may depend on different regimes. A branch needs its
basis — the connecting facts, clause, authority, or operator statement
that makes the regime a candidate — because location alone never
implies governing law.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..case_record import BRANCH_BASIS_KINDS, MATTER_STATUSES
from ..sources import REGIONS

if TYPE_CHECKING:
    from . import Context

DEFINITION = {
    "type": "function",
    "name": "open_branch",
    "description": "Open one candidate governing regime for an issue. Say what makes "
    "it a candidate (connecting facts, a clause, an authority, an operator "
    "statement) and whether it is known, inferred, disputed, or unknown. "
    "Location alone never implies governing law.",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "issue": {
                "type": "string",
                "description": "id of an item in your checklist (set_checklist)",
            },
            "regime": {
                "type": "string",
                "enum": sorted(REGIONS),
                "description": "candidate governing regime",
            },
            "status": {
                "type": "string",
                "enum": list(MATTER_STATUSES),
                "description": "whether the regime is established (default unknown)",
            },
            "basis": {
                "type": "string",
                "description": "what makes this regime a candidate",
            },
            "basis_kind": {
                "type": "string",
                "enum": list(BRANCH_BASIS_KINDS),
                "description": "what kind of basis (default connecting_facts)",
            },
        },
        "required": ["issue", "regime", "basis"],
    },
}


def run(ctx: Context, args: dict) -> dict:
    branch = ctx.case.open_branch(
        str(args.get("issue", "")),
        str(args.get("regime", "")),
        str(args.get("status", "unknown")),
        str(args.get("basis", "")),
        str(args.get("basis_kind", "connecting_facts")),
    )
    return {"opened": branch}
