"""identify_task: identify the user's actual task and classify it.

Distinguishes the requested output from the underlying legal
problem, records the deliverable, available context, gaps, and
constraints, and classifies the work across one or more taxonomy
categories with dependencies and a primary category. Recording an
identification engages the identified filing gate.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..reasoning import LAW_BASES, TASK_ALIASES, TASK_TAXONOMY, TASK_TYPES

if TYPE_CHECKING:
    from . import Context

CATEGORY = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "category": {
            "type": "string",
            "enum": sorted(set(TASK_TAXONOMY) | set(TASK_ALIASES)),
            "description": "taxonomy category, stored canonical "
            "(investigation is evidence; use other for emerging work)",
        },
        "subcategory": {
            "type": "string",
            "description": "free-text subcategory, e.g. motion practice",
        },
        "rationale": {
            "type": "string",
            "description": "why this category applies to the request",
        },
    },
    "required": ["category", "rationale"],
}

EDGE = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "from": {"type": "string"},
        "to": {
            "type": "string",
            "description": "category this work depends on first",
        },
    },
    "required": ["from", "to"],
}

DEFINITION = {
    "type": "function",
    "name": "identify_task",
    "description": "Identify the user's actual task before analyzing: what was asked, "
    "the objective and deliverable, the underlying legal problem, available and "
    "missing context, constraints, and the task categories with dependencies. "
    "Call it first on substantive legal work; it sets the gate task type.",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "requested": {"type": "string"},
            "objective": {"type": "string"},
            "deliverable": {"type": "string"},
            "underlying_problem": {"type": "string"},
            "context_available": {"type": "string"},
            "missing_or_ambiguous": {"type": "string"},
            "constraints": {"type": "string"},
            "categories": {
                "type": "array",
                "items": CATEGORY,
                "minItems": 1,
                "description": "every task category that applies, each with rationale",
            },
            "dependencies": {
                "type": "array",
                "items": EDGE,
                "description": "which categories depend on which, for mixed work",
            },
            "primary": {
                "type": "string",
                "description": "the primary category",
            },
            "gate_task_type": {
                "type": "string",
                "enum": list(TASK_TYPES),
                "description": "gate task type the filing checks run under",
            },
            "jurisdiction": {
                "type": "string",
                "description": "governing jurisdiction, only if the user or "
                "supplied sources establish one; leave blank if unknown — "
                "never infer it from language or assumed location",
            },
            "law_basis": {
                "type": "string",
                "enum": list(LAW_BASES),
                "description": "where the law comes from: supplied-documents "
                "when every legal proposition traces to supplied sources via "
                "registered authorities; background-needed when the work needs "
                "law beyond the record; mixed when both. Background reliance "
                "refuses filing unless narrowed or human-qualified.",
            },
        },
        "required": [
            "requested",
            "objective",
            "deliverable",
            "underlying_problem",
            "context_available",
            "missing_or_ambiguous",
            "constraints",
            "categories",
            "primary",
            "gate_task_type",
        ],
    },
}


def run(ctx: Context, args: dict) -> dict:
    entry = ctx.case.identify_task(
        requested=str(args.get("requested", "")),
        objective=str(args.get("objective", "")),
        deliverable=str(args.get("deliverable", "")),
        underlying_problem=str(args.get("underlying_problem", "")),
        context_available=str(args.get("context_available", "")),
        missing_or_ambiguous=str(args.get("missing_or_ambiguous", "")),
        constraints=str(args.get("constraints", "")),
        categories=args.get("categories") or [],
        dependencies=args.get("dependencies") or [],
        primary=str(args.get("primary", "")),
        gate_task_type=str(args.get("gate_task_type", "")),
        jurisdiction=str(args.get("jurisdiction", "") or ""),
        law_basis=str(args.get("law_basis", "") or ""),
    )
    return {
        "identified": True,
        "categories": [
            c["category"]
            for c in entry["classification"]["categories"]
        ],
        "primary": entry["classification"]["primary"],
        "task_type": ctx.case.data.get("task_type", ""),
        "policy": ctx.case.data.get("assessment_policy", {}),
    }
