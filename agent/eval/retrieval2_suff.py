"""Sealed sufficiency set v1: issue-level evidence needs over frozen corpora.

EIGHT queries with truths authored from the corpus TEXT in
retrieval2_cases (never from observed ranks). Elements with no
supporting passage carry passages=[] (known-absent). Run once via
`python -m eval.retrieval2_suff`, report, never tune. Corpora are
fictional (harbor bridge / meridian bonus / quarry title).
"""

from __future__ import annotations

SUFF_VERSION = "retrieval2-suff/v1"

# id, matter, query, required[{label, passages}], adverse, superseded
QUERIES = (
    {
        "id": "f-bonus-full",
        "matter": "meridian-bonus",
        "query": "what bonus does Reyes get for 2025 and was she employed at payout",
        "required": [
            {"label": "2025 rate",
             "passages": [("MB-PLAN-V2", 1)]},
            {"label": "payout-date employment",
             "passages": [("MB-HRNOTE", 1), ("MB-TERM", 1)]},
        ],
        "adverse": [("MB-EMAIL", 1)],
        "superseded": [("MB-PLAN-V1", 1), ("MB-PLAN-V1", 2)],
    },
    {
        "id": "f-rating-record",
        "matter": "meridian-bonus",
        "query": "Dana Reyes performance rating and any disciplinary record",
        "required": [
            {"label": "rating", "passages": [("MB-REVIEW", 1)]},
            {"label": "discipline", "passages": [("MB-REVIEW", 2)]},
        ],
        "adverse": [],
        "superseded": [],
    },
    {
        "id": "f-plan-current",
        "matter": "meridian-bonus",
        "query": "current bonus plan rate and payout conditions",
        "required": [
            {"label": "rate", "passages": [("MB-PLAN-V2", 1)]},
            {"label": "conditions", "passages": [("MB-PLAN-V2", 2)]},
        ],
        "adverse": [],
        "superseded": [("MB-PLAN-V1", 1), ("MB-PLAN-V1", 2)],
    },
    {
        "id": "f-easement-deal",
        "matter": "quarry-title",
        "query": "what easement burdens the parcel and does the agreement permit it",
        "required": [
            {"label": "burden", "passages": [("QT-TITLE", 1)]},
            {"label": "permission", "passages": [("QT-AGREEMENT", 1)]},
        ],
        "adverse": [],
        "superseded": [],
    },
    {
        "id": "f-mineral-rights",
        "matter": "quarry-title",
        "query": "who owns the mineral rights under the parcel",
        "required": [
            {"label": "mineral owner", "passages": []},
        ],
        "adverse": [],
        "superseded": [],
    },
    {
        "id": "f-bridge-insurer",
        "matter": "harbor-bridge",
        "query": "name the insurer and policy number for the bridge works",
        "required": [
            {"label": "insurer identity", "passages": []},
        ],
        "adverse": [],
        "superseded": [],
    },
    {
        "id": "f-weather-excuse",
        "matter": "harbor-bridge",
        "query": "do rain days extend the bridge completion date",
        "required": [
            {"label": "owner response",
             "passages": [("HB-NOTICE", 2)]},
            {"label": "engineer view",
             "passages": [("HB-ENGINEER", 2)]},
        ],
        "adverse": [("HB-NOTICE", 1)],
        "superseded": [],
    },
    {
        "id": "f-punch-signoff",
        "matter": "harbor-bridge",
        "query": "is the punch list complete and who signed off",
        "required": [
            {"label": "punch-list state",
             "passages": [("HB-DRAFT", 1)]},
            {"label": "signoff identity", "passages": []},
        ],
        "adverse": [],
        "superseded": [],
    },
)
