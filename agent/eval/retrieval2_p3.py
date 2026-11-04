"""Phase-3 independent fixtures: multi-element, version, abstention.

THIRTEEN queries with truths authored SOLELY from the corpus TEXT
in retrieval2_cases (never from observed ranks). Frozen before
any candidate code ran; split "phase3" marks them independent of
the 76-case set, which stays preserved as previously observed.

Tuple shape matches retrieval2_cases.QUERIES:
(id, matter, query, category, expected, required, prohibited,
abstain, split).
"""

from __future__ import annotations

P3_VERSION = "retrieval2-phase3/v1"

QUERIES = (
    ("w-delay-full", "harbor-bridge",
     "slip admitted, engineer critical path cause, and owner proof demand",
     "multi",
     [("HB-MINUTES", 1), ("HB-ENGINEER", 1), ("HB-MINUTES", 2)],
     [("HB-MINUTES", 1), ("HB-ENGINEER", 1), ("HB-MINUTES", 2)],
     [], False, "phase3"),
    ("w-bonus-2025", "meridian-bonus",
     "2025 rate, employment-at-payout rule, and Reyes end date",
     "multi",
     [("MB-PLAN-V2", 1), ("MB-PLAN-V2", 2), ("MB-HRNOTE", 1)],
     [("MB-PLAN-V2", 1), ("MB-PLAN-V2", 2), ("MB-HRNOTE", 1)],
     [("MB-PLAN-V1", 1), ("MB-PLAN-V1", 2)], False, "phase3"),
    ("w-title-deal", "quarry-title",
     "governing law, closing date term, and closing proof",
     "multi",
     [("QT-AGREEMENT", 3), ("QT-AGREEMENT", 2), ("QT-CLOSING", 1)],
     [("QT-AGREEMENT", 3), ("QT-AGREEMENT", 2), ("QT-CLOSING", 1)],
     [], False, "phase3"),
    ("w-change-valid", "harbor-bridge",
     "change order formalities and the signed extension",
     "multi",
     [("HB-CONTRACT", 3), ("HB-CO-03", 2)],
     [("HB-CONTRACT", 3), ("HB-CO-03", 2)],
     [], False, "phase3"),
    ("w-reyes-record", "meridian-bonus",
     "rating mark, absence warning, and termination ground",
     "multi",
     [("MB-REVIEW", 1), ("MB-REVIEW", 2), ("MB-TERM", 1)],
     [("MB-REVIEW", 1), ("MB-REVIEW", 2), ("MB-TERM", 1)],
     [], False, "phase3"),
    ("w-notice-fight", "harbor-bridge",
     "rain-day claim and owner foreseeability reply",
     "multi",
     [("HB-NOTICE", 1), ("HB-NOTICE", 2)],
     [("HB-NOTICE", 1), ("HB-NOTICE", 2)],
     [], False, "phase3"),
    ("w-rate-2024", "meridian-bonus",
     "old 2024 plan payout rate",
     "stale",
     [("MB-PLAN-V1", 1)],
     [("MB-PLAN-V1", 1)],
     [("MB-PLAN-V2", 1)], False, "phase3"),
    ("w-payout-now", "meridian-bonus",
     "must staff stay through December 31 under the current plan",
     "stale",
     [("MB-PLAN-V2", 2)],
     [("MB-PLAN-V2", 2)],
     [("MB-PLAN-V1", 2)], False, "phase3"),
    ("w-rain-excusable", "harbor-bridge",
     "engineer view: do rain days extend completion",
     "adverse",
     [("HB-ENGINEER", 2)],
     [("HB-ENGINEER", 2)],
     [], False, "phase3"),
    ("w-promise-fight", "meridian-bonus",
     "oral bonus safety assurance versus written payout conditions",
     "adverse",
     [("MB-EMAIL", 1), ("MB-PLAN-V2", 2)],
     [("MB-EMAIL", 1), ("MB-PLAN-V2", 2)],
     [], False, "phase3"),
    ("w-insurer", "harbor-bridge",
     "name of the builder all-risk insurer",
     "abstain", [], [], [], True, "phase3"),
    ("w-severance", "meridian-bonus",
     "severance multiple paid to Reyes at termination",
     "abstain", [], [], [], True, "phase3"),
    ("w-mortgage", "quarry-title",
     "mortgage balance owed on the parcel",
     "abstain", [], [], [], True, "phase3"),
)
