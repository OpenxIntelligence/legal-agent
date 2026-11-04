"""The acceptance policy: which checks are required, and what the outcomes mean.

One authoritative interpretation shared by submission, manual
verification, saved records, CLI, UI, and exports. Vocabulary:

- Assessment outcome: passed / failed / unavailable.
- Execution: applicable / inapplicable / unexecuted (with reason).
- Obligation: required / advisory, per module, under POLICY_VERSION.
- Review aggregate: repair / incomplete / deferred / clean.
- Memo aggregate: revision-required / assessment-incomplete /
  ready-for-review.

A missing required review never reads as success. Advisory limits
are preserved alongside progression, never implied as passed.
JavaScript in ui/index.html mirrors this vocabulary; see
qcReviewHtml and memoVerdict.
"""

from __future__ import annotations

POLICY_VERSION = "qc-policy-1"

REVIEW_REPAIR = "repair"
REVIEW_INCOMPLETE = "incomplete"
REVIEW_DEFERRED = "deferred"
REVIEW_CLEAN = "clean"

MEMO_REVISION_REQUIRED = "revision-required"
MEMO_ASSESSMENT_INCOMPLETE = "assessment-incomplete"
MEMO_READY_FOR_REVIEW = "ready-for-review"


def obligation_of(spec: dict) -> str:
    """A module's obligation under this policy (registration validates it)."""
    return spec.get("obligation", "required")


def aggregate_review(
    assessments: list, demanded: dict, tasks: dict, skipped: bool = False
) -> tuple[str, list]:
    """Aggregate one QC review. Returns (verdict, state_reasons).

    repair: required defects demand revision.
    incomplete: a required assessment could not complete.
    deferred: review skipped while required obligations stand open.
    clean: required work complete, nothing demanded (advisory limits
    and retained unresolved tasks ride along, explicitly listed).
    """
    from quality import registry

    if demanded:
        return REVIEW_REPAIR, [f"repair demanded: {key}" for key in sorted(demanded)]
    if skipped:
        opens = sorted(k for k, t in tasks.items() if t["status"] == "open")
        if opens:
            return REVIEW_DEFERRED, [f"obligation open at skip: {k}" for k in opens]
        return REVIEW_CLEAN, ["review skipped; no open obligations"]
    reasons = []
    for assessment in assessments:
        if not isinstance(assessment, dict):
            continue
        spec = registry.MODULES.get(assessment.get("module", ""), {})
        if obligation_of(spec) != "required":
            continue
        if assessment.get("outcome") in ("unavailable", "unexecuted"):
            reasons.append(
                f"{assessment.get('module')}: {assessment.get('outcome')} "
                f"(required) — {assessment.get('summary', '')}"
            )
    if reasons:
        return REVIEW_INCOMPLETE, reasons
    return REVIEW_CLEAN, []


def aggregate_memo(rules_passed: bool, claims_outcome: str | None) -> str:
    """Aggregate memo rules + claims into one memo state."""
    if not rules_passed:
        return MEMO_REVISION_REQUIRED
    if claims_outcome == "failed":
        return MEMO_REVISION_REQUIRED
    if claims_outcome == "passed":
        return MEMO_READY_FOR_REVIEW
    return MEMO_ASSESSMENT_INCOMPLETE


def primary_memo_label(passed: bool, rules: list, claims_outcome: str | None) -> str:
    """The primary human-readable memo line. Incomplete is not failure."""
    if passed and claims_outcome == "passed":
        return "MEMO passes"
    if rules:
        return "MEMO fails"
    if claims_outcome == "failed":
        return "MEMO fails"
    if claims_outcome == "unavailable":
        return "MEMO incomplete: claims check unavailable"
    return "MEMO fails"
