"""suggest_next_action: rank the next useful steps from recorded state.

Reads the ledger the run maintains — identification, validated
decomposition, answers, triages, adversarial reviews, workflow,
work product, and orchestration staleness — and returns the most
urgent actions first with reasons. Reports only; it never acts.

The steering wrapper adds the stop signal: investigation_complete
means no further investigation is justified (filing still passes
the gates). Resource budgets stay enforced by the run loop, not
here, so this tool passes no budgets and never halts.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .. import steering

if TYPE_CHECKING:
    from . import Context

DEFINITION = {
    "type": "function",
    "name": "suggest_next_action",
    "description": "Rank the next useful steps from recorded state: what to do, "
    "on what, and why. Consult it when unsure what to do next. Read-only.",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {},
    },
}


def run(ctx: Context, args: dict) -> dict:
    advice = steering.recommend(ctx.case.data)
    return {
        "suggestions": advice["actions"],
        "halt": advice["halt"],
        "investigation_complete": advice["investigation_complete"],
    }
