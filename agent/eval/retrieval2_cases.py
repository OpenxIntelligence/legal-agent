"""Frozen retrieval benchmark v1: multi-matter legal evidence search.

Three fictional matters (20 documents, ~35 pages) exercise the
real search path (`document_text.search_corpus` via the same
builder the runner uses): exact quotes, normalized variants,
paraphrases, multi-passage obligations, exceptions and adverse
authority, jurisdiction/temporal discrimination, conflicting
evidence, superseded versions, abstention, restricted and
unindexed handling, cross-matter isolation, noisy text, and
citation location.

Schema:
  CORPORA[matter] = {"docs": {doc_id: [page, ...]},
                     "meta": {doc_id: {...}}, "restricted": [...],
                     "unindexed": [...], "supersedes": {new: old}}
  QUERIES = (id, matter, text, category, expected [(doc, page)],
             required [(doc, page)] (all must appear),
             prohibited [(doc, page)] (none may appear),
             abstain (bool: nothing relevant exists),
             split ("dev" | "heldout"))
Expected pairs are (document_id, page) passage identities.
Relevance is binary and frozen here; the runner grades ranks
deterministically. Held-out judgments were fixed before any
retriever tuning: tune on dev, report on heldout.

Frozen: 2026-10-30. Do not edit judgments to match code; bump
CASES_VERSION on any corpus/query/truth change.

All parties, places, laws, and citations are fictional.
"""

from __future__ import annotations

CASES_VERSION = "retrieval2-cases/v1.2"

CORPORA = {
    "harbor-bridge": {
        "docs": {
            "HB-CONTRACT": [
                "Harbor Bridge Works Agreement. Section 1: Novak Builders "
                "shall widen the Harbor Bridge deck and repave approaches.",
                "Section 4: Substantial completion is due June 1. "
                "Liquidated damages are $2,500 per calendar day of delay.",
                "Section 9: No change order is effective unless written "
                "and signed by both the owner and the contractor.",
            ],
            "HB-CO-03": [
                "Change Order 3. The completion date moves by fourteen "
                "days on account of pier redesign.",
                "Revised substantial completion: June 15. Signed: Port "
                "Authority and Novak Builders.",
            ],
            "HB-NOTICE": [
                "Delay notice dated May 20. Novak claims nine rain days "
                "in April excused the slow start.",
                "Owner response: April rainfall was foreseeable and the "
                "contract allows no weather extension.",
            ],
            "HB-MINUTES": [
                "Progress meeting May 28. Novak states the project slipped "
                "twenty-one days through May.",
                "The owner disputes the figure and asks for a critical "
                "path analysis before any extension.",
            ],
            "HB-ENGINEER": [
                "Independent engineer report. Late steel delivery pushed "
                "the critical path by eighteen days.",
                "Rain days affected only non-critical grading work and "
                "do not extend the completion date.",
            ],
            "HB-PRIV": [
                "PRIVILEGED AND CONFIDENTIAL. Counsel advises settlement "
                "exposure near $45,000 if the June date holds.",
            ],
            "HB-DRAFT": [
                "Draft punch-list walkthrough notes, not yet transcribed.",
            ],
        },
        "meta": {
            "HB-CONTRACT": {"jurisdiction": "Port Authority",
                            "effective": "2025-09-01"},
            "HB-CO-03": {"jurisdiction": "Port Authority",
                         "effective": "2026-02-10"},
            "HB-NOTICE": {"jurisdiction": "Port Authority",
                          "effective": "2026-05-20"},
            "HB-MINUTES": {"jurisdiction": "Port Authority",
                           "effective": "2026-05-28"},
            "HB-ENGINEER": {"jurisdiction": "Port Authority",
                            "effective": "2026-06-02"},
            "HB-PRIV": {"jurisdiction": "Port Authority",
                        "effective": "2026-06-05",
                        "privileged": True},
            "HB-DRAFT": {"jurisdiction": "Port Authority",
                         "effective": "2026-06-06"},
        },
        "restricted": ["HB-PRIV"],
        "unindexed": ["HB-DRAFT"],
        "supersedes": {},
    },
    "meridian-bonus": {
        "docs": {
            "MB-OFFER": [
                "Offer letter to Dana Reyes. Bonus eligibility begins "
                "after twelve months of continuous service.",
                "Start date March 1, 2024. Reports to the operations "
                "manager in Meridian.",
            ],
            "MB-PLAN-V1": [
                "2024 bonus plan. Eligible staff earn ten percent of "
                "salary with a rating of 4 or higher.",
                "Payout occurs each December without further conditions.",
            ],
            "MB-PLAN-V2": [
                "2025 bonus plan. Eligible staff earn eight percent of "
                "salary with a rating of 4 or higher.",
                "Staff must be employed on the December 31 payout date "
                "to receive anything.",
            ],
            "MB-REVIEW": [
                "Annual review: Dana Reyes earned a 4.2 rating and an "
                "exceeds-expectations mark.",
                "Appendix: written warning for repeated absences during "
                "the review period.",
            ],
            "MB-HRNOTE": [
                "HR note: Reyes employment end date recorded as "
                "November 15, currently under audit.",
            ],
            "MB-EMAIL": [
                "Email from the operations manager: your bonus is safe "
                "no matter what happens this year.",
                "Reply from Reyes thanking him and asking for that "
                "promise in writing.",
            ],
            "MB-TERM": [
                "Termination notice effective November 15 for repeated "
                "absence.",
            ],
        },
        "meta": {
            "MB-OFFER": {"jurisdiction": "Meridian", "effective": "2024-03-01"},
            "MB-PLAN-V1": {"jurisdiction": "Meridian",
                           "effective": "2024-01-01", "superseded": True},
            "MB-PLAN-V2": {"jurisdiction": "Meridian",
                           "effective": "2025-01-01"},
            "MB-REVIEW": {"jurisdiction": "Meridian", "effective": "2025-11-01"},
            "MB-HRNOTE": {"jurisdiction": "Meridian",
                          "effective": "2025-11-16"},
            "MB-EMAIL": {"jurisdiction": "Meridian", "effective": "2025-10-02"},
            "MB-TERM": {"jurisdiction": "Meridian", "effective": "2025-11-15"},
        },
        "restricted": [],
        "unindexed": [],
        "supersedes": {"MB-PLAN-V2": "MB-PLAN-V1"},
    },
    "quarry-title": {
        "docs": {
            "QT-AGREEMENT": [
                "Parcel purchase agreement. Section 3: the seller "
                "conveys clear title except recorded easements.",
                "Section 7: closing occurs on August 1 at the title "
                "company office.",
                "Section 12: this agreement is governed by the law of "
                "the State of Caldera.",
            ],
            "QT-TITLE": [
                "Title report: an access-road easement recorded in "
                "1987 burdens the parcel.",
                "No liens, judgments, or tax claims were found of "
                "record.",
            ],
            "QT-SURVEY": [
                "SURVEY  plat  shows   the  access ROAD crossing "
                "parcel B from the north gate.",
            ],
            "QT-STATUTE": [
                "Caldera Recording Act section 44-210: an unrecorded "
                "conveyance is void against a bona fide purchaser.",
                "Section 44-212: short-term leases under one year need "
                "not be recorded.",
            ],
            "QT-CLOSING": [
                "Closing statement dated August 1. Funds disbursed per "
                "the signed settlement sheet.",
            ],
        },
        "meta": {
            "QT-AGREEMENT": {"jurisdiction": "Caldera",
                             "effective": "2026-06-20"},
            "QT-TITLE": {"jurisdiction": "Caldera", "effective": "2026-07-11"},
            "QT-SURVEY": {"jurisdiction": "Caldera", "effective": "2026-07-02"},
            "QT-STATUTE": {"jurisdiction": "Caldera",
                           "effective": "2019-01-01"},
            "QT-CLOSING": {"jurisdiction": "Caldera", "effective": "2026-08-01"},
        },
        "restricted": [],
        "unindexed": [],
        "supersedes": {},
    },
}

# (id, matter, text, category, expected, required, prohibited,
#  abstain, split). expected/required/prohibited hold (doc, page).
QUERIES = (
    # Exact quotes.
    ("e-liquidated", "harbor-bridge",
     "Liquidated damages are $2,500 per calendar day of delay.",
     "exact", [("HB-CONTRACT", 2)], [("HB-CONTRACT", 2)], [], False, "dev"),
    ("e-change15", "harbor-bridge",
     "Revised substantial completion: June 15.",
     "exact", [("HB-CO-03", 2)], [("HB-CO-03", 2)], [], False, "dev"),
    ("e-bonus12", "meridian-bonus",
     "Bonus eligibility begins after twelve months of continuous service.",
     "exact", [("MB-OFFER", 1)], [("MB-OFFER", 1)], [], False, "heldout"),
    ("e-employed-dec", "meridian-bonus",
     "Staff must be employed on the December 31 payout date to receive anything.",
     "exact", [("MB-PLAN-V2", 2)], [("MB-PLAN-V2", 2)],
     [("MB-PLAN-V1", 1), ("MB-PLAN-V1", 2)], False, "dev"),
    ("e-easement", "quarry-title",
     "an access-road easement recorded in 1987 burdens the parcel.",
     "exact", [("QT-TITLE", 1)], [("QT-TITLE", 1)], [], False, "dev"),
    ("e-bfp", "quarry-title",
     "an unrecorded conveyance is void against a bona fide purchaser.",
     "exact", [("QT-STATUTE", 1)], [("QT-STATUTE", 1)], [], False, "heldout"),
    ("e-slipped", "harbor-bridge",
     "Novak states the project slipped twenty-one days through May.",
     "exact", [("HB-MINUTES", 1)], [("HB-MINUTES", 1)], [], False, "heldout"),
    ("e-rating", "meridian-bonus",
     "Dana Reyes earned a 4.2 rating and an exceeds-expectations mark.",
     "exact", [("MB-REVIEW", 1)], [("MB-REVIEW", 1)], [], False, "dev"),
    # Normalized variants (case/space/punctuation/curly).
    ("n-lower", "harbor-bridge",
     "liquidated DAMAGES are $2,500  per   calendar day of delay",
     "norm", [("HB-CONTRACT", 2)], [("HB-CONTRACT", 2)], [], False, "dev"),
    ("n-nbsp", "quarry-title",
     "access-road\u00a0easement\u00a0recorded\u00a0in\u00a01987",
     "norm", [("QT-TITLE", 1)], [("QT-TITLE", 1)], [], False, "dev"),
    ("n-email-nbsp", "meridian-bonus",
     "YOUR\u00a0BONUS\u00a0IS\u00a0SAFE\u00a0no\u00a0matter\u00a0what",
     "norm", [("MB-EMAIL", 1)], [("MB-EMAIL", 1)], [], False, "heldout"),
    ("n-ws", "harbor-bridge",
     "Section\t9:\nNo change order is effective unless written",
     "norm", [("HB-CONTRACT", 3)], [("HB-CONTRACT", 3)], [], False, "heldout"),
    ("n-case", "quarry-title",
     "SHORT-TERM LEASES UNDER ONE YEAR NEED NOT BE RECORDED",
     "norm", [("QT-STATUTE", 2)], [("QT-STATUTE", 2)], [], False, "dev"),
    ("n-mixed", "meridian-bonus",
     "termination NOTICE  effective\u00a0November 15   for repeated absence",
     "norm", [("MB-TERM", 1)], [("MB-TERM", 1)], [], False, "dev"),
    # Paraphrases (different words, same meaning).
    ("p-delay-cost", "harbor-bridge",
     "how much per day does the builder owe for finishing late",
     "para", [("HB-CONTRACT", 2)], [], [], False, "dev"),
    ("p-new-deadline", "harbor-bridge",
     "what is the pushed-back finish date after the pier redesign",
     "para", [("HB-CO-03", 1), ("HB-CO-03", 2)],
     [("HB-CO-03", 2)], [], False, "dev"),
    ("p-slip", "harbor-bridge",
     "how many days behind schedule did Novak admit to in May",
     "para", [("HB-MINUTES", 1)], [], [], False, "heldout"),
    ("p-steel", "harbor-bridge",
     "did late materials affect the critical path and by how long",
     "para", [("HB-ENGINEER", 1)], [], [], False, "heldout"),
    ("p-bonus-wait", "meridian-bonus",
     "how long must Reyes work before becoming bonus eligible",
     "para", [("MB-OFFER", 1)], [], [], False, "dev"),
    ("p-payout-job", "meridian-bonus",
     "does the current plan require staying employed through year end",
     "para", [("MB-PLAN-V2", 2)], [("MB-PLAN-V2", 2)],
     [("MB-PLAN-V1", 2)], False, "dev"),
    ("p-rating-num", "meridian-bonus",
     "what score did Reyes get on her annual review",
     "para", [("MB-REVIEW", 1)], [], [], False, "heldout"),
    ("p-end-date", "meridian-bonus",
     "when did Reyes stop working there and is the date final",
     "para", [("MB-HRNOTE", 1), ("MB-TERM", 1)],
     [("MB-HRNOTE", 1)], [], False, "heldout"),
    ("p-easement", "quarry-title",
     "what burden on the land did the title search turn up",
     "para", [("QT-TITLE", 1)], [], [], False, "dev"),
    ("p-liens", "quarry-title",
     "were any mortgages or tax debts found on the property",
     "para", [("QT-TITLE", 2)], [], [], False, "heldout"),
    ("p-recording", "quarry-title",
     "what happens to a deed that never gets filed with the county",
     "para", [("QT-STATUTE", 1)], [], [], False, "dev"),
    ("p-closing", "quarry-title",
     "when did the purchase funds change hands",
     "para", [("QT-CLOSING", 1)], [], [], False, "dev"),
    # Multi-passage obligations (all required).
    ("m-completion", "harbor-bridge",
     "original deadline, any revision, and the revision formalities",
     "multi", [("HB-CONTRACT", 2), ("HB-CO-03", 2), ("HB-CONTRACT", 3)],
     [("HB-CONTRACT", 2), ("HB-CO-03", 2), ("HB-CONTRACT", 3)],
     [], False, "dev"),
    ("m-rain", "harbor-bridge",
     "rain delay claim, owner response, and engineer view on weather",
     "multi", [("HB-NOTICE", 1), ("HB-NOTICE", 2), ("HB-ENGINEER", 2)],
     [("HB-NOTICE", 1), ("HB-NOTICE", 2), ("HB-ENGINEER", 2)],
     [], False, "heldout"),
    ("m-bonus-rules", "meridian-bonus",
     "current bonus rate, rating bar, and payout-day employment rule",
     "multi", [("MB-PLAN-V2", 1), ("MB-PLAN-V2", 2)],
     [("MB-PLAN-V2", 1), ("MB-PLAN-V2", 2)],
     [("MB-PLAN-V1", 1), ("MB-PLAN-V1", 2)], False, "dev"),
    ("m-reyes-case", "meridian-bonus",
     "Reyes rating, absence record, and employment end date",
     "multi", [("MB-REVIEW", 1), ("MB-REVIEW", 2), ("MB-HRNOTE", 1)],
     [("MB-REVIEW", 1), ("MB-REVIEW", 2), ("MB-HRNOTE", 1)],
     [], False, "heldout"),
    ("m-title", "quarry-title",
     "title promise, found easement, and where the road runs",
     "multi", [("QT-AGREEMENT", 1), ("QT-TITLE", 1), ("QT-SURVEY", 1)],
     [("QT-AGREEMENT", 1), ("QT-TITLE", 1), ("QT-SURVEY", 1)],
     [], False, "dev"),
    ("m-closing", "quarry-title",
     "closing date term and proof closing occurred",
     "multi", [("QT-AGREEMENT", 2), ("QT-CLOSING", 1)],
     [("QT-AGREEMENT", 2), ("QT-CLOSING", 1)], [], False, "heldout"),
    # Exceptions and adverse authority (must surface, not just support).
    ("a-weather-exc", "harbor-bridge",
     "does the contract allow any weather extension at all",
     "adverse", [("HB-NOTICE", 2)], [("HB-NOTICE", 2)], [], False, "dev"),
    ("a-change-form", "harbor-bridge",
     "what kills an unsigned oral change order",
     "adverse", [("HB-CONTRACT", 3)], [], [], False, "heldout"),
    ("a-promise", "meridian-bonus",
     "manager assurance contradicting the written plan terms",
     "adverse", [("MB-EMAIL", 1)], [("MB-EMAIL", 1)], [], False, "dev"),
    ("a-warning", "meridian-bonus",
     "evidence against Reyes being a strong performer",
     "adverse", [("MB-REVIEW", 2)], [], [], False, "heldout"),
    ("a-lease-exc", "quarry-title",
     "which conveyances escape the recording requirement",
     "adverse", [("QT-STATUTE", 2)], [("QT-STATUTE", 2)], [], False, "dev"),
    # Jurisdiction and temporal discrimination.
    ("j-caldera-law", "quarry-title",
     "which state law governs the parcel agreement",
     "juris", [("QT-AGREEMENT", 3)], [("QT-AGREEMENT", 3)], [], False, "dev"),
    ("j-recording-act", "quarry-title",
     "Caldera Recording Act bona fide purchaser rule",
     "juris", [("QT-STATUTE", 1)], [("QT-STATUTE", 1)], [], False, "heldout"),
    ("j-plan-year", "meridian-bonus",
     "2025 plan employment condition for payout",
     "juris", [("MB-PLAN-V2", 2)], [("MB-PLAN-V2", 2)],
     [("MB-PLAN-V1", 2)], False, "dev"),
    ("j-start-date", "meridian-bonus",
     "when did Reyes start work in Meridian",
     "juris", [("MB-OFFER", 2)], [], [], False, "heldout"),
    ("j-notice-date", "harbor-bridge",
     "date of the delay notice about April rain",
     "juris", [("HB-NOTICE", 1)], [], [], False, "dev"),
    # Conflicting evidence (both sides required).
    ("c-rain", "harbor-bridge",
     "was the April rain excusable or foreseeable",
     "conflict", [("HB-NOTICE", 1), ("HB-NOTICE", 2)],
     [("HB-NOTICE", 1), ("HB-NOTICE", 2)], [], False, "dev"),
    ("c-slip-size", "harbor-bridge",
     "how large was the delay: admitted figure versus demanded proof",
     "conflict", [("HB-MINUTES", 1), ("HB-MINUTES", 2)],
     [("HB-MINUTES", 1), ("HB-MINUTES", 2)], [], False, "heldout"),
    ("c-bonus-safe", "meridian-bonus",
     "is the bonus guaranteed: manager promise versus plan conditions",
     "conflict", [("MB-EMAIL", 1), ("MB-PLAN-V2", 2)],
     [("MB-EMAIL", 1), ("MB-PLAN-V2", 2)], [], False, "dev"),
    ("c-reyes-date", "meridian-bonus",
     "is the November 15 end date settled or still disputed",
     "conflict", [("MB-HRNOTE", 1), ("MB-TERM", 1)],
     [("MB-HRNOTE", 1), ("MB-TERM", 1)], [], False, "heldout"),
    # Superseded versions (current wins; history stays findable).
    ("s-rate-now", "meridian-bonus",
     "current bonus percentage for eligible staff",
     "stale", [("MB-PLAN-V2", 1)], [("MB-PLAN-V2", 1)],
     [("MB-PLAN-V1", 1)], False, "dev"),
    ("s-ten-pct", "meridian-bonus",
     "ten percent bonus historical 2024 plan term",
     "stale", [("MB-PLAN-V1", 1)], [("MB-PLAN-V1", 1)], [], False, "heldout"),
    ("s-no-conditions", "meridian-bonus",
     "payout each December without further conditions",
     "stale", [("MB-PLAN-V1", 2)], [("MB-PLAN-V1", 2)], [], False, "dev"),
    # Abstention (nothing relevant exists; empty is correct).
    ("x-bankruptcy", "harbor-bridge",
     "bankruptcy proceedings against Novak Builders",
     "abstain", [], [], [], True, "dev"),
    ("x-arbitration", "meridian-bonus",
     "arbitration clause in the Reyes offer letter",
     "abstain", [], [], [], True, "heldout"),
    ("x-lienholder", "quarry-title",
     "name of the mortgage lienholder on the parcel",
     "abstain", [], [], [], True, "dev"),
    ("x-ld-amount", "harbor-bridge",
     "total liquidated damages assessed to date",
     "abstain", [], [], [], True, "heldout"),
    ("x-bonus-amount", "meridian-bonus",
     "exact dollar bonus paid to Reyes for 2024",
     "abstain", [], [], [], True, "dev"),
    # Restricted and unindexed (empty + honest listing).
    ("r-counsel", "harbor-bridge",
     "counsel settlement exposure forty-five thousand",
     "restricted", [], [], [("HB-PRIV", 1)], True, "dev"),
    ("r-privileged", "harbor-bridge",
     "privileged work product review team advice",
     "restricted", [], [], [("HB-PRIV", 1)], True, "heldout"),
    ("u-punchlist", "harbor-bridge",
     "punch-list walkthrough notes",
     "unindexed", [], [], [("HB-DRAFT", 1)], True, "dev"),
    # Cross-matter isolation (run against the WRONG root: must be empty).
    ("i-harbor-on-meridian", "meridian-bonus",
     "liquidated damages $2,500 per calendar day of delay",
     "isolation", [], [], [], True, "dev"),
    ("i-meridian-on-quarry", "quarry-title",
     "Reyes 4.2 rating exceeds expectations bonus",
     "isolation", [], [], [], True, "heldout"),
    ("i-quarry-on-harbor", "harbor-bridge",
     "bona fide purchaser Caldera Recording Act",
     "isolation", [], [], [], True, "dev"),
    # Noisy text (spacing/case artifacts).
    ("t-survey", "quarry-title",
     "survey plat access road parcel B north gate",
     "noisy", [("QT-SURVEY", 1)], [("QT-SURVEY", 1)], [], False, "dev"),
    ("t-crossing", "quarry-title",
     "where does the access road cross the parcel",
     "noisy", [("QT-SURVEY", 1)], [], [], False, "heldout"),
    # Citation location.
    ("c-44210", "quarry-title",
     "44-210",
     "cite", [("QT-STATUTE", 1)], [("QT-STATUTE", 1)], [], False, "dev"),
    ("c-44212", "quarry-title",
     "44-212",
     "cite", [("QT-STATUTE", 2)], [("QT-STATUTE", 2)], [], False, "heldout"),
    ("c-sec9", "harbor-bridge",
     "Section 9 change order",
     "cite", [("HB-CONTRACT", 3)], [("HB-CONTRACT", 3)], [], False, "dev"),
    ("c-sec3", "quarry-title",
     "Section 3 clear title",
     "cite", [("QT-AGREEMENT", 1)], [("QT-AGREEMENT", 1)], [], False, "heldout"),
    # Misleadingly similar numerical claims (v1.1: truth written
    # from the corpus text before any retriever run; the wrong
    # number always lives on a prohibited page).
    ("v-extension-days", "harbor-bridge",
     "how many days did the change order add",
     "numeric", [("HB-CO-03", 1)], [("HB-CO-03", 1)],
     [("HB-MINUTES", 1), ("HB-ENGINEER", 1), ("HB-NOTICE", 1)],
     False, "dev"),
    ("v-rating-bar", "meridian-bonus",
     "minimum rating number for bonus eligibility",
     "numeric", [("MB-PLAN-V2", 1)], [("MB-PLAN-V2", 1)],
     [("MB-REVIEW", 1)], False, "dev"),
    ("v-rain-days", "harbor-bridge",
     "how many rain days did Novak claim for April",
     "numeric", [("HB-NOTICE", 1)], [("HB-NOTICE", 1)],
     [("HB-MINUTES", 1), ("HB-ENGINEER", 1)], False, "heldout"),
    ("v-steel-days", "harbor-bridge",
     "critical path delay length in days",
     "numeric", [("HB-ENGINEER", 1)], [("HB-ENGINEER", 1)],
     [("HB-MINUTES", 1), ("HB-CO-03", 1)], False, "heldout"),
    # Sealed supplement v1.2: truth written from the corpus
    # text above; the retriever was not run on these queries
    # before they were frozen. Run once, report, never tune.
    ("s-deadline-ext", "harbor-bridge",
     "when must the bridge finish and what extension was granted",
     "multi", [("HB-CONTRACT", 2), ("HB-CO-03", 1)],
     [("HB-CONTRACT", 2), ("HB-CO-03", 1)], [], False, "heldout"),
    ("s-rain-both", "harbor-bridge",
     "rain delay claim and the owner response to it",
     "multi", [("HB-NOTICE", 1), ("HB-NOTICE", 2)],
     [("HB-NOTICE", 1), ("HB-NOTICE", 2)], [], False, "heldout"),
    ("s-insurance", "harbor-bridge",
     "what insurance policy covers the construction delay",
     "abstain", [], [], [], True, "heldout"),
    ("s-against-ext", "harbor-bridge",
     "evidence against the full fourteen day extension claim",
     "adverse", [("HB-NOTICE", 2), ("HB-ENGINEER", 2)],
     [("HB-ENGINEER", 2)], [], False, "heldout"),
    ("s-rate-up-down", "meridian-bonus",
     "did the bonus rate go up or down in plan version 2",
     "numeric", [("MB-PLAN-V1", 1), ("MB-PLAN-V2", 1)],
     [("MB-PLAN-V1", 1), ("MB-PLAN-V2", 1)], [], False, "heldout"),
    ("s-no-liens", "quarry-title",
     "which report found no liens judgments or tax claims",
     "exact", [("QT-TITLE", 2)], [("QT-TITLE", 2)], [], False,
     "heldout"),
)