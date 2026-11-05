"""answer_foundational: answer one foundational question before concluding.

Each material question gets its governing rule or source, the
evidence the tools returned, the supported answer, its basis, the
contrary evidence or uncertainty, and how the answer affects the
conclusion. Unanswerable questions stay explicitly unresolved with
what would resolve them — never silent assumptions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ._arguments import boolean_arg

from ..reasoning import QUESTION_CLASSES, TASK_TYPES

if TYPE_CHECKING:
    from . import Context

DEFINITION = {
    "type": "function",
    "name": "answer_foundational",
    "description": "Answer one foundational question the conclusion rests on: its "
    "governing rule or source, the evidence found, the supported answer, its basis, "
    "contrary evidence or uncertainty, and its effect on the conclusion. Mark "
    "unanswerable questions unresolved with what would resolve them. Sets the "
    "matter's task type on first use.",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "class": {
                "type": "string",
                "enum": list(QUESTION_CLASSES),
                "description": "which foundational question this answers",
            },
            "question": {"type": "string"},
            "rule_or_source": {
                "type": "string",
                "description": "governing rule, evidence, or document needed",
            },
            "evidence": {
                "type": "string",
                "description": "what the sources and tools returned",
            },
            "answer": {
                "type": "string",
                "description": "answer the available information supports",
            },
            "basis": {
                "type": "string",
                "description": "legal or factual basis for the answer",
            },
            "contrary": {
                "type": "string",
                "description": "contrary evidence, exceptions, uncertainty, missing proof",
            },
            "effect": {
                "type": "string",
                "description": "how the answer affects the ultimate conclusion",
            },
            "status": {
                "type": "string",
                "enum": ["answered", "unresolved"],
                "description": "answered, or unresolved when it cannot be answered reliably",
            },
            "resolution": {
                "type": "string",
                "description": "required when unresolved: what would resolve it",
            },
            "issue": {
                "type": "string",
                "description": "checklist item this answer supports, if any",
            },
            "task_type": {
                "type": "string",
                "enum": list(TASK_TYPES),
                "description": "matter task type, set on first use",
            },
            "depends_on": {
                "type": "array",
                "items": {"type": "string"},
                "description": "FQ ids this answer rests on, recorded first",
            },
            "threshold": {
                "type": "boolean",
                "description": "true for a potentially dispositive question",
            },
        },
        "required": [
            "class",
            "question",
            "rule_or_source",
            "evidence",
            "answer",
            "basis",
            "contrary",
            "effect",
        ],
    },
}


def run(ctx: Context, args: dict) -> dict:
    entry = ctx.case.answer_foundational(
        qclass=str(args.get("class", "")),
        question=str(args.get("question", "")),
        rule_or_source=str(args.get("rule_or_source", "")),
        evidence=str(args.get("evidence", "")),
        answer=str(args.get("answer", "")),
        basis=str(args.get("basis", "")),
        contrary=str(args.get("contrary", "")),
        effect=str(args.get("effect", "")),
        status=str(args.get("status", "answered")),
        resolution=str(args.get("resolution", "") or ""),
        issue=str(args.get("issue", "") or ""),
        task_type=str(args.get("task_type", "") or ""),
        depends_on=args.get("depends_on") or [],
        threshold=boolean_arg("answer_foundational", args, "threshold"),
    )
    return {
        "answered": entry["id"],
        "class": entry["class"],
        # "answer_status", never "status": the loop reads a result's
        # "status" as the run's status (answered/paused/...).
        "answer_status": entry["status"],
        "task_type": ctx.case.data.get("task_type", ""),
    }
