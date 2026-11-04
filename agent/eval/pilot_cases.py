"""Human-label pilot: synthetic matters with DRAFT keys (offline prep).

SIX matters over the frozen corpora in retrieval2_cases, one per
failure family plus a clean baseline. Each issue carries draft
evidence needs and a draft key verdict authored from the corpus
TEXT (never from observed ranks).

Keys are DRAFT-SYNTHETIC: an attorney must review, correct, and
sign every key before this set measures anything about legal
correctness. Until then it proves plumbing only: matters run
end to end, needs assess deterministically, verdicts compare.

Matter shape:
  {id, title, issues: [{id, matter, query,
    required: [{label, passages: [(doc, page)]}],
    adverse: [(doc, page)], superseded: [(doc, page)],
    key_verdict: "sufficient" | "insufficient" | "abstain",
    key_gaps: [labels or notes the key expects disclosed]}]}
"""

from __future__ import annotations

PILOT_VERSION = "pilot-cases/v1-draft"
KEY_STATUS = "DRAFT-SYNTHETIC: attorney review required"

MATTERS = (
    {
        "id": "pm-clean-breach",
        "title": "Clean two-element breach question (baseline)",
        "issues": (
            {
                "id": "duty",
                "matter": "harbor-bridge",
                "query": "delivery duty and substantial completion date",
                "required": (
                    {"label": "duty",
                     "passages": (("HB-CONTRACT", 1),)},
                    {"label": "date",
                     "passages": (("HB-CONTRACT", 2),)},
                ),
                "adverse": (),
                "superseded": (),
                "key_verdict": "sufficient",
                "key_gaps": (),
            },
            {
                "id": "formality",
                "matter": "harbor-bridge",
                "query": "are unsigned change orders effective",
                "required": (
                    {"label": "formality",
                     "passages": (("HB-CONTRACT", 3),)},
                ),
                "adverse": (),
                "superseded": (),
                "key_verdict": "sufficient",
                "key_gaps": (),
            },
        ),
    },
    {
        "id": "pm-multi-bonus",
        "title": "Multi-element bonus entitlement (completeness)",
        "issues": (
            {
                "id": "entitlement",
                "matter": "meridian-bonus",
                "query": "2025 bonus rate, payout conditions, Reyes rating",
                "required": (
                    {"label": "rate",
                     "passages": (("MB-PLAN-V2", 1),)},
                    {"label": "conditions",
                     "passages": (("MB-PLAN-V2", 2),)},
                    {"label": "rating",
                     "passages": (("MB-REVIEW", 1),)},
                ),
                "adverse": (),
                "superseded": (
                    ("MB-PLAN-V1", 1), ("MB-PLAN-V1", 2)),
                "key_verdict": "insufficient",
                "key_gaps": (
                    "single-shot retrieval drops at least one element; "
                    "per-obligation search or disclosure required",
                ),
            },
        ),
    },
    {
        "id": "pm-version-rate",
        "title": "Which rate governs (temporal applicability)",
        "issues": (
            {
                "id": "current-rate",
                "matter": "meridian-bonus",
                "query": "current bonus percentage for eligible staff",
                "required": (
                    {"label": "rate",
                     "passages": (("MB-PLAN-V2", 1),)},
                ),
                "adverse": (),
                "superseded": (("MB-PLAN-V1", 1),),
                "key_verdict": "insufficient",
                "key_gaps": (
                    "superseded V1 outranks current V2; version must be "
                    "resolved by effective date, not rank",
                ),
            },
            {
                "id": "historic-rate",
                "matter": "meridian-bonus",
                "query": "rate paid under the old 2024 bonus plan",
                "required": (
                    {"label": "rate",
                     "passages": (("MB-PLAN-V1", 1),)},
                ),
                "adverse": (),
                "superseded": (),
                "key_verdict": "sufficient",
                "key_gaps": (),
            },
        ),
    },
    {
        "id": "pm-adverse-promise",
        "title": "Manager promise vs plan terms (adverse coverage)",
        "issues": (
            {
                "id": "guarantee",
                "matter": "meridian-bonus",
                "query": "is the Reyes bonus guaranteed",
                "required": (
                    {"label": "promise",
                     "passages": (("MB-EMAIL", 1),)},
                    {"label": "conditions",
                     "passages": (("MB-PLAN-V2", 2),)},
                ),
                "adverse": (("MB-EMAIL", 1),),
                "superseded": (),
                "key_verdict": "insufficient",
                "key_gaps": (
                    "adverse email must surface alongside plan terms; "
                    "concluding from one side is one-sided",
                ),
            },
        ),
    },
    {
        "id": "pm-abstain-insurer",
        "title": "Unanswerable from the record (abstention)",
        "issues": (
            {
                "id": "insurer",
                "matter": "harbor-bridge",
                "query": "name of the builder all-risk insurer",
                "required": (
                    {"label": "insurer identity", "passages": ()},
                ),
                "adverse": (),
                "superseded": (),
                "key_verdict": "abstain",
                "key_gaps": ("no supporting passage exists",),
            },
            {
                "id": "severance",
                "matter": "meridian-bonus",
                "query": "severance multiple paid to Reyes at termination",
                "required": (
                    {"label": "severance multiple", "passages": ()},
                ),
                "adverse": (),
                "superseded": (),
                "key_verdict": "abstain",
                "key_gaps": ("no supporting passage exists",),
            },
        ),
    },
    {
        "id": "pm-conflict-delay",
        "title": "Conflicting delay accounts (contradiction)",
        "issues": (
            {
                "id": "delay-size",
                "matter": "harbor-bridge",
                "query": "how large was the delay: admitted figure versus demanded proof",
                "required": (
                    {"label": "admitted",
                     "passages": (("HB-MINUTES", 1),)},
                    {"label": "proof-demand",
                     "passages": (("HB-MINUTES", 2),)},
                ),
                "adverse": (),
                "superseded": (),
                "key_verdict": "insufficient",
                "key_gaps": (
                    "both sides of the conflict must be represented; "
                    "one figure alone misleads",
                ),
            },
        ),
    },
)
