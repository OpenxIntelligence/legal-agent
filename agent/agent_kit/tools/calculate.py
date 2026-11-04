"""calculate: deterministic date and money arithmetic.

Runs one computation from agent_kit.compute and records it with
its inputs, result, governing rule, and assumptions. Use this for
every deadline, day count, and money figure — never compute dates
or money in prose. The filing gate re-runs each recorded
computation; a record that does not replay blocks filing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..compute import OPS

if TYPE_CHECKING:
    from . import Context

DEFINITION = {
    "type": "function",
    "name": "calculate",
    "description": "Compute a date or money figure deterministically and record it "
    "with its inputs, result, rule, and assumptions. Use for every deadline, "
    "day count, and money figure — never arithmetic in prose. Dates are "
    "YYYY-MM-DD; business days exclude the start date and skip weekends plus "
    "your explicit holidays list; money is decimal strings.",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "op": {
                "type": "string",
                "enum": list(OPS),
                "description": "which computation to run",
            },
            "start": {
                "type": "string",
                "description": "start date, YYYY-MM-DD",
            },
            "end": {
                "type": "string",
                "description": "end date for days_between, YYYY-MM-DD",
            },
            "days": {
                "description": "whole days to add, or to charge interest on",
            },
            "holidays": {
                "type": "array",
                "items": {"type": "string"},
                "description": "explicit YYYY-MM-DD closures for "
                "add_business_days (pass [] when there are none)",
            },
            "operands": {
                "type": "array",
                "items": {"type": "string"},
                "description": "decimal amounts for money_add",
            },
            "amount": {
                "type": "string",
                "description": "decimal amount for money_subtract/multiply",
            },
            "subtrahend": {
                "type": "string",
                "description": "decimal amount to subtract",
            },
            "factor": {
                "type": "string",
                "description": "decimal factor for money_multiply",
            },
            "principal": {
                "type": "string",
                "description": "decimal principal for interest",
            },
            "rate_pct": {
                "type": "string",
                "description": "annual rate percent for interest, e.g. '5'",
            },
            "basis": {
                "description": "day-count basis for interest: 360 or 365",
            },
            "rule": {
                "type": "string",
                "description": "statute, clause, or instruction the inputs "
                "come from, e.g. 'contract section 3'",
            },
            "assumptions": {
                "type": "string",
                "description": "what was supplied or assumed, e.g. which "
                "holiday calendar the list came from",
            },
        },
        "required": ["op"],
    },
}

INPUT_KEYS = (
    "start",
    "end",
    "days",
    "holidays",
    "operands",
    "amount",
    "subtrahend",
    "factor",
    "principal",
    "rate_pct",
    "basis",
)


def run(ctx: Context, args: dict) -> dict:
    inputs = {key: args[key] for key in INPUT_KEYS if key in args}
    entry = ctx.case.record_computation(
        op=str(args.get("op", "")),
        inputs=inputs,
        rule=str(args.get("rule", "") or ""),
        assumptions=str(args.get("assumptions", "") or ""),
    )
    return {"computed": entry}
