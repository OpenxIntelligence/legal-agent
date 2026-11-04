"""Checklist scope guard (deterministic): original questions stay answerable.

The scope baseline is captured when the first checklist is accepted,
before investigation. Removing a baselined question, or rewording it
without a recorded amendment, is a defect: the investigation may not
narrow its own scope to dodge a hard question. Legitimate evolution is
explicit: a reworded question carries an `amendment` note explaining
how the original obligation is preserved, and a split/combined item
lists the baselined ids it continues in `amends`.
"""

from __future__ import annotations

from quality import records
from quality.registry import register


def baseline_of(case_data: dict) -> dict:
    """The baselined {id: question}; legacy first-review snapshot as fallback."""
    baseline = case_data.get("scope_baseline")
    if isinstance(baseline, dict) and baseline:
        return {str(k): str(v) for k, v in baseline.items()}
    legacy = (case_data.get("qc_state") or {}).get("checklist")
    if isinstance(legacy, dict):
        return {str(k): str(v) for k, v in legacy.items()}
    return {}


def applies(ctx, memo: dict, prior: dict) -> tuple[bool, str]:
    if not baseline_of(ctx.case.data):
        return False, "no scope baseline yet"
    return True, ""


def run(ctx, memo: dict, review: dict) -> dict:
    baseline = baseline_of(ctx.case.data)
    current = ctx.case.data.get("checklist") or {}
    continued = {
        old
        for entry in current.values()
        if isinstance(entry, dict)
        for old in (entry.get("amends") or [])
    }
    findings = []
    for item in sorted(baseline):
        entry = current.get(item)
        if not isinstance(entry, dict):
            if item in continued:
                continue
            findings.append({"item": item, "kind": "removed"})
        elif (
            str(entry.get("question", "")) != baseline[item]
            and not str(entry.get("amendment", "") or "").strip()
        ):
            findings.append({"item": item, "kind": "changed"})
    kinds = sorted({f["kind"] for f in findings})
    return records.assessment(
        module=SPEC["name"],
        module_version=SPEC["version"],
        memo_version=review["memo_version"],
        evidence_version=review["evidence_version"],
        outcome="failed" if findings else "passed",
        summary=(
            f"baselined question(s) {', '.join(kinds)}: "
            + ", ".join(f["item"] for f in findings)
            if findings
            else "every baselined question is still on the checklist"
        ),
        findings=findings,
    )


SPEC = register(
    {
        "name": "checklist-scope",
        "version": "2",
        "kind": "deterministic",
        "purpose": (
            "Flag baselined checklist questions removed or reworded "
            "without a recorded amendment before filing."
        ),
        "applies": applies,
        "inputs": "scope baseline + current checklist ids, questions, amendments",
        "output_schema": {"findings": [{"item": "str", "kind": "removed|changed"}]},
        "dependencies": [],
        "budget": {"max_judge_calls_per_review": 0},
        "obligation": "required",
        "run": run,
    }
)
