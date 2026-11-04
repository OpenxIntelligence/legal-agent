"""Counterevidence detection (deterministic): asserted findings that ignore
opposing evidence recorded for their item. Each objection names the finding
unit and the ignored fact; whether the omission is material is decided by
the objection-materiality module."""

from __future__ import annotations

from quality import records
from quality.registry import register
from verification.verify_memo import _matches_citation  # same match semantics as memo rules


def _opposing(ctx, item: str) -> list:
    return [
        f
        for f in ctx.case.data.get("facts", [])
        if isinstance(f, dict)
        and f.get("item") == item
        and f.get("stance", "supports") == "opposes"
    ]


def applies(ctx, memo: dict, prior: dict) -> tuple[bool, str]:
    if not any(
        isinstance(f, dict) and f.get("stance", "supports") == "opposes"
        for f in ctx.case.data.get("facts", [])
    ):
        return False, "no opposing evidence recorded"
    return True, ""


def run(ctx, memo: dict, review: dict) -> dict:
    findings = memo.get("findings") or []
    objections = []
    for finding in findings:
        if not isinstance(finding, dict) or finding.get("status") != "asserted":
            continue
        item = finding.get("item", "")
        citations = finding.get("citations") or []
        if not isinstance(citations, list):
            citations = []
        for fact in _opposing(ctx, item):
            if any(
                isinstance(c, dict) and _matches_citation(c, fact)
                for c in citations
            ):
                continue
            objections.append(
                {
                    "unit_id": f"finding:{item}",
                    "item": item,
                    "fact_id": fact.get("id", ""),
                    "quote": str(fact.get("quote", "")),
                    "reason": (
                        f"finding {item} is marked asserted but does not cite "
                        f"opposing evidence {fact.get('id', '')}"
                    ),
                }
            )
    return records.assessment(
        module=SPEC["name"],
        module_version=SPEC["version"],
        memo_version=review["memo_version"],
        evidence_version=review["evidence_version"],
        outcome="failed" if objections else "passed",
        summary=(
            f"{len(objections)} finding(s) ignore opposing evidence"
            if objections
            else "every asserted finding cites its opposing evidence"
        ),
        findings=objections,
    )


SPEC = register(
    {
        "name": "counterevidence",
        "version": "1",
        "kind": "deterministic",
        "purpose": "Find asserted findings that ignore opposing evidence recorded for their item.",
        "applies": applies,
        "inputs": "memo findings with citations; case facts with stance",
        "output_schema": {
            "objections": [
                {"unit_id": "str", "item": "str", "fact_id": "str", "quote": "str", "reason": "str"}
            ]
        },
        "dependencies": [],
        "budget": {"max_judge_calls_per_review": 0},
        "obligation": "required",
        "run": run,
    }
)
