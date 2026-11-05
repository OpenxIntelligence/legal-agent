"""adversarial_review: challenge the preliminary conclusion before filing.

The strongest argument against, fact risks, adverse authority,
overlooked exceptions and procedure, opposing counsel's case, what
a skeptical decision-maker would require, alternative theories, and
the fact most likely to reverse the result — then whether revision
followed and what changed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._arguments import boolean_arg

from ..case_record import ADVERSARIAL_LENSES

if TYPE_CHECKING:
    from . import Context

DEFINITION = {
    "type": "function",
    "name": "adversarial_review",
    "description": "Challenge the preliminary conclusion before filing: strongest "
    "argument against, fact risks, adverse authority, overlooked issues, opposing "
    "counsel's case, skeptical decision-maker needs, alternative theories, the "
    "most likely reversing fact, and what revision followed. Re-review after new "
    "evidence arrives.",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "strongest_against": {"type": "string"},
            "fact_risks": {"type": "string"},
            "adverse_authority": {"type": "string"},
            "overlooked": {"type": "string"},
            "opposing_counsel": {"type": "string"},
            "judge_needs": {"type": "string"},
            "alternative_theory": {"type": "string"},
            "reversing_fact": {"type": "string"},
            "revision_needed": {"type": "boolean"},
            "revision": {
                "type": "string",
                "description": "what changed, or why no change was needed",
            },
            "lens": {
                "type": "string",
                "description": "panel role this challenge speaks from "
                "(evidence, law, procedure, strategy); omit for a "
                "general challenge",
                "enum": list(ADVERSARIAL_LENSES),
            },
            "dropped": {
                "type": "array",
                "description": "objections raised and dismissed, each with "
                "the counter-evidence explaining why (proposed, unassessed)",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "objection": {"type": "string"},
                        "counter_evidence": {"type": "string"},
                    },
                    "required": ["objection", "counter_evidence"],
                },
            },
        },
        "required": [
            "strongest_against",
            "fact_risks",
            "adverse_authority",
            "overlooked",
            "opposing_counsel",
            "judge_needs",
            "alternative_theory",
            "reversing_fact",
            "revision_needed",
            "revision",
        ],
    },
}


def run(ctx: Context, args: dict) -> dict:
    entry = ctx.case.record_adversarial(
        strongest_against=str(args.get("strongest_against", "")),
        fact_risks=str(args.get("fact_risks", "")),
        adverse_authority=str(args.get("adverse_authority", "")),
        overlooked=str(args.get("overlooked", "")),
        opposing_counsel=str(args.get("opposing_counsel", "")),
        judge_needs=str(args.get("judge_needs", "")),
        alternative_theory=str(args.get("alternative_theory", "")),
        reversing_fact=str(args.get("reversing_fact", "")),
        revision_needed=boolean_arg("adversarial_review", args, "revision_needed"),
        revision=str(args.get("revision", "")),
        dropped=args.get("dropped"),
        lens=str(args.get("lens", "") or ""),
    )
    result = {
        "reviewed": entry["id"],
        "revision_needed": entry["revision_needed"],
    }
    if ctx.case.adversarial_lenses():
        # Panel mode: at least one lensed challenge exists, so nudge the
        # next empty seat. Lens-less runs see the legacy shape exactly.
        status = ctx.case.panel_status()
        result["panel"] = {
            "covered": status["covered"],
            "missing": status["missing"],
        }
    return result
