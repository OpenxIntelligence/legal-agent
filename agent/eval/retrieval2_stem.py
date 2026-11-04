"""Stemming-eval fixtures: suffix-mismatch queries (sealed experiment).

SIX queries where the morphed term is the SOLE bridge to the
required passage (other terms match elsewhere or nowhere):
employment/employed, disputed/disputes, rated/rating,
completed/completion, warns/warning, extends/extend. Truths
authored SOLELY from the corpus TEXT in retrieval2_cases,
frozen before any candidate ranker ran. Split "stem".

First draft (slip/safety/record forms) discarded before any
candidate ran: substring or sibling terms already carried
those, so they could not isolate morphology.

Tuple shape matches retrieval2_cases.QUERIES:
(id, matter, query, category, expected, required, prohibited,
abstain, split).
"""

from __future__ import annotations

STEM_VERSION = "retrieval2-stem/v1"

QUERIES = (
    ("g-employ", "meridian-bonus",
     "employment at year end for payout",
     "morph", [("MB-PLAN-V2", 2)], [("MB-PLAN-V2", 2)],
     [("MB-PLAN-V1", 2)], False, "stem"),
    ("g-dispute", "harbor-bridge",
     "disputed delay size",
     "morph", [("HB-MINUTES", 2)], [("HB-MINUTES", 2)],
     [], False, "stem"),
    ("g-rated", "meridian-bonus",
     "Reyes rated score",
     "morph", [("MB-REVIEW", 1)], [("MB-REVIEW", 1)],
     [], False, "stem"),
    ("g-complete", "harbor-bridge",
     "bridge completed date",
     "morph", [("HB-CONTRACT", 2)], [("HB-CONTRACT", 2)],
     [], False, "stem"),
    ("g-warns", "meridian-bonus",
     "warns about absence",
     "morph", [("MB-REVIEW", 2)], [("MB-REVIEW", 2)],
     [], False, "stem"),
    ("g-extends", "harbor-bridge",
     "rain extends deadline",
     "morph", [("HB-ENGINEER", 2)], [("HB-ENGINEER", 2)],
     [], False, "stem"),
)
