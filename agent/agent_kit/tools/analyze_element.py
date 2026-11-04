"""analyze_element: analyze one element of a controlling legal test.

Rule-based and element-by-element: the test, the element, its rule
and source, the facts and evidence, the application, the strongest
arguments each way, missing proof or exceptions, the provisional
finding, and its effect on the overall result.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from . import Context

DEFINITION = {
    "type": "function",
    "name": "analyze_element",
    "description": "Analyze one element of the controlling legal test against the "
    "facts and evidence: rule and source, application, strongest arguments each "
    "way, missing proof or exceptions, provisional finding, and effect on the "
    "result. Required for findings that assert law or applicability.",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "issue": {
                "type": "string",
                "description": "checklist item this analysis supports",
            },
            "test": {"type": "string"},
            "element": {"type": "string"},
            "rule": {"type": "string"},
            "source": {
                "type": "string",
                "description": "authoritative source for the rule",
            },
            "facts_evidence": {
                "type": "string",
                "description": "relevant facts and evidence",
            },
            "application": {
                "type": "string",
                "description": "the rule applied to those facts",
            },
            "supporting": {
                "type": "string",
                "description": "strongest supporting argument",
            },
            "opposing": {
                "type": "string",
                "description": "strongest opposing argument",
            },
            "missing": {
                "type": "string",
                "description": "missing proof, exceptions, unresolved issues",
            },
            "finding": {
                "type": "string",
                "description": "provisional finding on this element",
            },
            "effect": {
                "type": "string",
                "description": "effect on the overall result",
            },
        },
        "required": [
            "issue",
            "test",
            "element",
            "rule",
            "source",
            "facts_evidence",
            "application",
            "supporting",
            "opposing",
            "missing",
            "finding",
            "effect",
        ],
    },
}


def run(ctx: Context, args: dict) -> dict:
    entry = ctx.case.analyze_element(
        issue=str(args.get("issue", "")),
        test=str(args.get("test", "")),
        element=str(args.get("element", "")),
        rule=str(args.get("rule", "")),
        source=str(args.get("source", "")),
        facts_evidence=str(args.get("facts_evidence", "")),
        application=str(args.get("application", "")),
        supporting=str(args.get("supporting", "")),
        opposing=str(args.get("opposing", "")),
        missing=str(args.get("missing", "")),
        finding=str(args.get("finding", "")),
        effect=str(args.get("effect", "")),
    )
    return {"analyzed": entry["id"], "issue": entry["issue"]}
