"""Evidence sufficiency assessment over retrieved passages (Slice 4).

Pure function: given an issue's required passage sets (each set
is alternatives — every set needs at least one hit), adverse
passages that should surface, and superseded passages that must
not decide, judge the retrieved ranks. Reports sufficiency,
missing sets, adverse coverage, abstention advice, and
version-confusion flags. Advisory: policy decides what a
shortfall blocks. No retrieval dependency, no model calls.
"""

from __future__ import annotations


def assess(
    required: list,
    returned: list,
    *,
    adverse: list | None = None,
    superseded: list | None = None,
) -> dict:
    """Judge retrieved ranks against an issue's evidence needs.

    required: [{label, passages: [(doc, page), ...]}]; an empty
    passages list is a known-absent element (unanswerable part).
    returned: [(doc, page), ...] in rank order.
    adverse: [(doc, page), ...] that should surface.
    superseded: [(doc, page), ...] that must not decide.

    Returns {sufficient, missing, adverse_covered,
    adverse_recall, abstain, version_confusion, reasons}.

    Malformed items (non-dict needs, unusable pairs, bad pages)
    never raise: unusable evidence cannot satisfy a need, a
    malformed need stays open, malformed adverse counts as
    uncovered, and malformed superseded voids sufficiency —
    each with a reason naming the problem.
    """
    adverse = list(adverse or [])
    superseded = list(superseded or [])
    hit = set()
    for item in returned or []:
        pair = _pair(item)
        if pair is not None:
            hit.add(pair)
    missing = []
    malformed_needs = 0
    for need in required or []:
        if not isinstance(need, dict):
            malformed_needs += 1
            missing.append({"label": "?", "reason": "malformed need entry"})
            continue
        label = need.get("label", "?")
        wants = set()
        for passage in need.get("passages", []) or []:
            pair = _pair(passage)
            if pair is not None:
                wants.add(pair)
        if not wants:
            if need.get("passages"):
                missing.append({"label": label,
                                "reason": "no usable passage listed"})
            else:
                missing.append({"label": label,
                                "reason": "no supporting passage exists"})
        elif wants.isdisjoint(hit):
            missing.append({"label": label, "reason": "required passage not returned"})
    adverse_set = set()
    malformed_adverse = 0
    for item in adverse:
        pair = _pair(item)
        if pair is None:
            malformed_adverse += 1
        else:
            adverse_set.add(pair)
    adverse_hit = adverse_set & hit
    adverse_total = len(adverse_set) + malformed_adverse
    adverse_recall = len(adverse_hit) / adverse_total if adverse_total else 1.0
    superseded_set = set()
    malformed_superseded = 0
    for item in superseded:
        pair = _pair(item)
        if pair is None:
            malformed_superseded += 1
        else:
            superseded_set.add(pair)
    confused = [
        {"document_id": doc, "page": page}
        for doc, page in sorted(superseded_set & hit)
    ]
    reasons = []
    if missing:
        reasons += [
            f"missing {m['label']}: {m['reason']}" for m in missing
        ]
    if adverse_total and (adverse_hit != adverse_set or malformed_adverse):
        reasons.append(
            f"adverse coverage {len(adverse_hit)}/{adverse_total}")
    if malformed_adverse:
        reasons.append(
            f"malformed adverse entry skipped ({malformed_adverse})")
    if confused:
        reasons.append(
            "superseded passage returned: "
            + ", ".join(f"{c['document_id']} p{c['page']}" for c in confused))
    if malformed_superseded:
        reasons.append(
            f"malformed superseded entry skipped ({malformed_superseded})")
    sufficient = (not missing and not confused
                  and not malformed_superseded)
    # Abstain only when the issue is unanswerable in truth: every
    # required element is known-absent. Unretrieved-but-existing
    # evidence is a retrieval gap (missing), not abstention; and a
    # malformed need is unknown, never a ground to abstain.
    abstain = (
        bool(required)
        and not malformed_needs
        and all(
            not (need.get("passages", []) or [])
            for need in (required or [])
            if isinstance(need, dict)
        )
    )
    return {
        "sufficient": sufficient,
        "missing": missing,
        "adverse_covered": (adverse_hit == adverse_set
                             and not malformed_adverse),
        "adverse_recall": adverse_recall,
        "abstain": abstain,
        "version_confusion": confused,
        "reasons": reasons,
    }


def _pair(item) -> tuple | None:
    """One normalized (document, page) pair, or None when unusable.

    Corrupt entries never raise here: callers skip them and keep
    the affected need open, matching the fail-closed convention.
    """
    if not isinstance(item, (list, tuple)) or len(item) != 2:
        return None
    doc, page = item
    try:
        number = int(page)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    name = str(doc or "").strip()
    if not name:
        return None
    return (name, number)
