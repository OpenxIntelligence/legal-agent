"""validate_decomposition: validate the planned question set.

Checks class coverage, threshold coverage (or waivers with
reasons), dependency validity, redundancy, and resolution
criteria against the run's classification. Every attempt is
recorded with its verdict; fix failures and re-validate. The
filing gate reads the latest record, so validate again when the
plan changes. Requires identification first.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..reasoning import QUESTION_CLASSES

if TYPE_CHECKING:
    from . import Context

QUESTION = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "id": {"type": "string", "description": "caller-chosen temp id"},
        "class": {"type": "string", "enum": list(QUESTION_CLASSES)},
        "question": {"type": "string"},
        "criterion": {
            "type": "string",
            "description": "what would resolve this question",
        },
        "issue": {
            "type": "string",
            "description": "checklist item it supports, if any",
        },
        "depends_on": {
            "type": "array",
            "items": {"type": "string"},
            "description": "temp ids this question rests on",
        },
        "threshold": {
            "type": "boolean",
            "description": "true for a potentially dispositive question",
        },
    },
    "required": ["id", "class", "question", "criterion"],
}

WAIVER = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "area": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["area", "reason"],
}

DEFINITION = {
    "type": "function",
    "name": "validate_decomposition",
    "description": "Validate the planned foundational-question set before "
    "investigating: class and threshold coverage, dependencies, redundancy, "
    "and resolution criteria. Fix failures and re-validate; the filing gate "
    "reads the latest verdict.",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "questions": {
                "type": "array",
                "items": QUESTION,
                "minItems": 1,
            },
            "waived": {
                "type": "array",
                "items": WAIVER,
                "description": "threshold areas set aside with reasons",
            },
        },
        "required": ["questions"],
    },
}


def run(ctx: Context, args: dict) -> dict:
    record = ctx.case.validate_decomposition(
        questions=args.get("questions") or [],
        waived=args.get("waived") or [],
    )
    return {
        "version": record["version"],
        "verdict": record["verdict"],
        "problems": record["problems"],
        "advisory": record["advisory"],
    }
