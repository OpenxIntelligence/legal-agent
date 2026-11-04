"""Frozen orchestration benchmark: multidisciplinary matters.

Two invented matters exercise the control plane: changing
assumptions (premise invalidation + selective recompute),
conflicting authorities (contradiction integration),
missing evidence (disclosure, not fabrication), incorrect
initial classification (mid-run reclassification), driver
failure (escalation), and cost discipline. Truth is planted
here; the runner (`eval/orchestration_run.py`) grades final
state per arm. Scripted drivers prove loop mechanics only.

Frozen: 2026-10-29. Add cases; do not edit to match code.

Amendment 2026-10-29 (plant only; truth untouched): the
lighthouse archive lookup is meant to fail persistently and
escalate, but two scripted failures then succeed under the
documented max_failures=3 contract, so the plant could never
produce the frozen "escalated" truth. failures raised 2 -> 3
so the scenario the truth describes actually occurs.

Amendment 2026-10-29 (contract rename): the machine outcome
"approved" is renamed "ready_for_review" (approval record
decision "machine_ready") because the old word implied human
sign-off the machine cannot give. Expected final states are
otherwise unchanged.

Addition 2026-10-29 (new case; prior truths untouched):
"meridian-bonus" plants a preferred-but-wrong file conclusion
(Reyes bonus-eligible) against records that contradict it
without settling the matter (end date under audit). Truth
requires disclosure of the eligibility question, a
conditional-only downstream answer carrying the unsettled
dep, and an integrated contradiction — never confirmation
of the preferred claim.
"""

from __future__ import annotations

# Fixture + truth version (closeout §9: version benchmark
# cases and expected classifications). Bump on any case,
# script, or truth change; every report carries this value.
CASES_VERSION = "orchestration-cases/v1"

CASES = {
    "harborlight-renewal": {
        "title": "Renewal that turns out disputed and multi-regime",
        "context": {"purpose": "license renewal transaction",
                    "realms": ["ca"]},
        "initial_type": "transaction_review",
        "final_type": "dispute_investigation",
        "transcripts": {
            "VR-CONTRACT": {
                1: "Section 4. Renewal requires written notice by March 1.",
            },
            "VR-DISPUTE": {
                1: "Rivera disputes the renewal and alleges the notice was late.",
            },
            "VR-FEDERAL": {
                1: "Federal notice 88-B sets a February 15 renewal cutoff.",
            },
        },
        "premises": [
            {"id": "P-governing",
             "statement": "California law governs the renewal"},
            {"id": "P-scope",
             "statement": "single counterparty, no dispute"},
        ],
        "seeds": [
            {"text": "What is the renewal deadline?", "class": "evidence",
             "premises": ["P-governing"]},
            {"text": "Is the renewal disputed?", "class": "evidence",
             "premises": ["P-scope"]},
            {"text": "Does the federal notice change the deadline?",
             "class": "analysis", "deps": ["Q1", "Q2"],
             "premises": ["P-governing"]},
            {"text": "Is there a waiver letter?", "class": "evidence",
             "premises": ["P-scope"]},
            {"text": "Verify the position.", "class": "verify",
             "deps": ["Q1", "Q2", "Q3", "Q4"]},
            {"text": "Integrate and disclose.", "class": "review",
             "deps": ["Q5"]},
        ],
        "scripts": {
            "Q1": [{"facts": [{
                "document_id": "VR-CONTRACT", "page": 1,
                "quote": "Renewal requires written notice by March 1.",
            }], "coverage": "complete"}],
            "Q2": [{"facts": [{
                "document_id": "VR-DISPUTE", "page": 1,
                "quote": "Rivera disputes the renewal and alleges the notice was late.",
                "flags": {"dispute": True},
            }], "coverage": "complete"}],
            # First pass misses the federal side; the engine asks
            # for more (gap), and the second pass addresses both
            # authorities and proposes the premise change.
            "Q3": [
                {"elements": [{"test": "deadline", "element": "cutoff",
                               "finding": "March 1 controls",
                               "for_side": "VR-CONTRACT",
                               "against_side": ""}]},
                {"elements": [{"test": "deadline", "element": "cutoff",
                               "finding": "February 15 may control",
                               "for_side": "Federal notice 88-B sets a "
                                           "February 15 renewal cutoff.",
                               "against_side": "VR-CONTRACT March 1",
                               "rule": "the earliest controlling cutoff governs",
                               "source": "VR-FEDERAL notice 88-B",
                               "facts_evidence": "VR-CONTRACT March 1; "
                                                 "VR-FEDERAL February 15",
                               "application": "the federal notice may "
                                              "displace March 1",
                               "supporting": "federal notice sets "
                                             "February 15",
                               "opposing": "contract states March 1",
                               "missing": "whether the notice binds "
                                          "this license",
                               "effect": "deadline uncertain pending "
                                         "governing-law answer"}],
                 "premise_updates": [{
                     "premise": "P-governing",
                     "statement": "federal notice may control the deadline",
                     "reason": "VR-FEDERAL conflicts with VR-CONTRACT",
                 }]},
            ],
            "Q4": [{"facts": [], "coverage": "partial",
                    "gaps": ["no waiver letter in the record"]}],
            "Q5": [{"passed": True, "problems": []}],
        },
        "evidence": {
            "Q3": [{"document_id": "VR-FEDERAL", "page": 1,
                    "quote": "Federal notice 88-B sets a February 15 renewal cutoff.",
                    "stance": "opposes"}],
        },
        "truth": {
            "outcome": "ready_for_review",
            "statuses": {"Q1": "answered", "Q2": "answered", "Q3": "answered",
                         "Q4": "disclosed", "Q5": "answered", "Q6": "answered",
                         "Q7": "answered"},
            "reclassified": ["transaction_review", "dispute_investigation"],
            "contradictions_integrated": 1,
            "invalidations": [{"premise": "P-governing",
                               "staled": ["Q1", "Q3"]}],
            "preserved": ["Q2"],
            "disclosed_keys": ["waiver"],
            "min_support": {"Q1": 1, "Q2": 1, "Q3": 1},
        },
        "baseline_truth": {
            "outcome": "disclosed: quality bar unmet",
            "open_contradictions": 1,
            "classification": ["transaction_review"],
        },
    },
    "lighthouse-claim": {
        "title": "Conflicting fee sources with a failing lookup",
        "context": {"purpose": "fee dispute", "realms": ["ca", "ew"]},
        "initial_type": "dispute_investigation",
        "final_type": "dispute_investigation",
        "transcripts": {
            "LH-INVOICE": {1: "Invoice total: 42000 due on receipt."},
            "LH-LEDGER": {1: "Ledger shows 41000 received in full settlement."},
        },
        "premises": [
            {"id": "P-fee", "statement": "the invoice states the final fee"},
        ],
        "seeds": [
            {"text": "What fee was invoiced?", "class": "evidence",
             "premises": ["P-fee"]},
            {"text": "Does the ledger settle the fee?", "class": "analysis",
             "deps": ["Q1"], "premises": ["P-fee"]},
            {"text": "What does the archive hold?", "class": "evidence"},
            {"text": "Verify the position.", "class": "verify",
             "deps": ["Q1", "Q2"]},
            {"text": "Repair open defects.", "class": "repair",
             "deps": ["Q4"]},
            {"text": "Integrate and disclose.", "class": "review",
             "deps": ["Q4", "Q5"]},
        ],
        "scripts": {
            "Q1": [
                {"facts": [
                    {"document_id": "LH-INVOICE", "page": 1,
                     "quote": "Invoice total: 42000 due on receipt."},
                    {"document_id": "LH-LEDGER", "page": 1,
                     "quote": "Ledger shows 41000 received in full settlement.",
                     "stance": "opposes"},
                ], "coverage": "complete"},
                {"facts": [
                    {"document_id": "LH-INVOICE", "page": 1,
                     "quote": "Invoice total: 42000 due on receipt."},
                    {"document_id": "LH-LEDGER", "page": 1,
                     "quote": "Ledger shows 41000 received in full settlement.",
                     "stance": "opposes"},
                ], "coverage": "complete"},
            ],
            "Q2": [{"elements": [{"test": "settlement", "element": "accord",
                                  "finding": "amount genuinely disputed",
                                  "for_side": "ledger settlement entry",
                                  "against_side": "invoice demands 42000",
                                  "rule": "accord requires agreement on the sum",
                                  "source": "LH-LEDGER settlement entry",
                                  "facts_evidence": "invoice 42000; ledger "
                                                    "41000 in full settlement",
                                  "application": "the two sums cannot both "
                                               "state the final fee",
                                  "supporting": "ledger marked received in "
                                                "full settlement",
                                  "opposing": "invoice demands 42000",
                                  "missing": "archive record of any payment",
                                  "effect": "fee stays disputed"}],
                    "premise_updates": [{
                        "premise": "P-fee",
                        "statement": "invoiced amount disputed",
                        "reason": "ledger contradicts the invoice",
                    }]}],
            "Q4": [{"passed": True, "problems": []}],
        },
        "failures": {"Q3": 3},
        "evidence": {},
        "truth": {
            "outcome": "ready_for_review",
            "statuses": {"Q1": "answered", "Q2": "answered",
                         "Q3": "escalated", "Q4": "answered",
                         "Q5": "answered", "Q6": "answered"},
            "reclassified": ["dispute_investigation"],
            "contradictions_integrated": 1,
            "invalidations": [{"premise": "P-fee", "staled": ["Q1", "Q2"]}],
            "preserved": ["Q4", "Q5", "Q6"],
            "disclosed_keys": [],
            "escalated": ["Q3"],
            "min_support": {"Q1": 1, "Q2": 1},
        },
        "baseline_truth": {
            "outcome": "disclosed: quality bar unmet",
            "open_contradictions": 1,
            "classification": ["dispute_investigation"],
        },
    },
    "westhaven-amendment": {
        "title": "Conflicting fee provisions with a disclosed standoff",
        "context": {"purpose": "fee vesting lookup",
                    "realms": ["ca"]},
        "initial_type": "single_issue_research",
        "final_type": "single_issue_research",
        "transcripts": {
            "WV-CODE": {
                1: "Section 12. Fees vest on filing.",
            },
            "WV-AMEND": {
                1: "Amendment 3 revises Section 12 fees.",
            },
        },
        "premises": [],
        "seeds": [
            {"text": "When do the fees vest?", "class": "evidence"},
            {"text": "Does the amendment apply here?", "class": "analysis",
             "deps": ["Q1"]},
            {"text": "Verify the position.", "class": "verify",
             "deps": ["Q1", "Q2"]},
            {"text": "Integrate and disclose.", "class": "review",
             "deps": ["Q3"]},
        ],
        "scripts": {
            "Q1": [{"facts": [
                {"document_id": "WV-CODE", "page": 1,
                 "quote": "Fees vest on filing."},
                {"document_id": "WV-AMEND", "page": 1,
                 "quote": "Amendment 3 revises Section 12 fees.",
                 "stance": "opposes"},
            ], "coverage": "complete"}],
            # A brand-new interpretive premise (no premise key):
            # recorded, nothing staled.
            "Q2": [{"elements": [{"test": "vesting", "element": "timing",
                                  "finding": "vesting genuinely unclear",
                                  "for_side": "code says filing",
                                  "against_side": "amendment revises",
                                  "rule": "amendments apply as written",
                                  "source": "WV-AMEND amendment 3",
                                  "facts_evidence": "code vesting; "
                                                    "amendment revision",
                                  "application": "the two cannot both "
                                               "control timing",
                                  "supporting": "code text is explicit",
                                  "opposing": "amendment revises it",
                                  "missing": "prospectivity ruling",
                                  "effect": "timing stays open"}],
                    "premise_updates": [{
                        "statement": "amendment 3 applies prospectively",
                        "reason": "Q2 reading of the amendment",
                    }]}],
            "Q3": [{"passed": True, "problems": []}],
        },
        "evidence": {},
        "truth": {
            "outcome": "ready_for_review",
            "statuses": {"Q1": "answered", "Q2": "answered",
                         "Q3": "answered", "Q4": "disclosed"},
            "reclassified": ["single_issue_research"],
            "contradictions_integrated": 1,
            "invalidations": [{"premise": "P3", "staled": []}],
            "preserved": [],
            "disclosed_keys": ["C1"],
            "min_support": {"Q1": 1, "Q2": 1, "Q3": 1},
        },
        "baseline_truth": {
            "outcome": "disclosed: quality bar unmet",
            "open_contradictions": 1,
            "classification": ["single_issue_research"],
        },
    },
    "meridian-bonus": {
        "title": "Preferred eligibility claim contradicted, unsettled",
        "context": {"purpose": "bonus eligibility lookup",
                    "realms": ["ca"]},
        "initial_type": "single_issue_research",
        "final_type": "single_issue_research",
        "transcripts": {
            "MB-PLAN": {
                1: "Bonus plan: continuous service through March 1 "
                   "is required.",
            },
            "MB-RECORD": {
                1: "Service ended February 10; end date under audit.",
            },
            "MB-FILE": {
                1: "File note: Reyes is expected to be bonus-eligible.",
            },
        },
        "premises": [],
        "seeds": [
            {"text": "What do the records state?", "class": "evidence"},
            {"text": "Is Reyes bonus-eligible?", "class": "analysis",
             "deps": ["Q1"]},
            {"text": "What follows for the bonus letter?",
             "class": "analysis", "deps": ["Q2"]},
            {"text": "Verify the position.", "class": "verify",
             "deps": ["Q1", "Q2", "Q3"]},
            {"text": "Integrate and disclose.", "class": "review",
             "deps": ["Q4"]},
        ],
        "scripts": {
            "Q1": [{"facts": [
                {"document_id": "MB-PLAN", "page": 1,
                 "quote": "continuous service through March 1 "
                          "is required."},
                {"document_id": "MB-RECORD", "page": 1,
                 "quote": "Service ended February 10; end date "
                          "under audit."},
                {"document_id": "MB-FILE", "page": 1,
                 "quote": "Reyes is expected to be bonus-eligible."},
            ], "coverage": "complete"}],
            "Q2": [{"elements": [{"test": "eligibility",
                                  "element": "service rule",
                                  "finding": "preferred eligibility claim "
                                             "unsupported; end date disputed",
                                  "for_side": "file expects eligibility",
                                  "against_side": "record shows February "
                                                  "10 end",
                                  "rule": "plan requires service through "
                                          "March 1",
                                  "source": "MB-PLAN service rule",
                                  "facts_evidence": "plan rule; February "
                                                    "record; file note",
                                  "application": "records do not establish "
                                                 "eligibility",
                                  "supporting": "file expectation only",
                                  "opposing": "February 10 record",
                                  "missing": "audited end date",
                                  "effect": "eligibility stays open"}],
                    "missing": ["audited service end date"]}],
            "Q3": [{"elements": [{"test": "bonus-letter",
                                  "element": "issuance",
                                  "finding": "no unconditional letter can "
                                             "issue",
                                  "for_side": "plan rule is explicit",
                                  "against_side": "end date disputed, not "
                                                  "established",
                                  "rule": "letters follow established "
                                          "eligibility",
                                  "source": "MB-PLAN service rule",
                                  "facts_evidence": "plan March 1 rule; "
                                                    "February record under "
                                                    "audit",
                                  "application": "conditional draft only "
                                                 "while eligibility is open",
                                  "supporting": "plan text is explicit",
                                  "opposing": "record under audit",
                                  "missing": "nothing further",
                                  "effect": "letter stays conditional"}]}],
            "Q4": [{"passed": True, "problems": []}],
        },
        "evidence": {
            "Q3": [{"document_id": "MB-RECORD", "page": 1,
                    "quote": "Service ended February 10; end date "
                             "under audit.",
                    "stance": "opposes"}],
        },
        "truth": {
            "outcome": "ready_for_review",
            "statuses": {"Q1": "answered", "Q2": "disclosed",
                         "Q3": "answered", "Q4": "answered",
                         "Q5": "disclosed"},
            "reclassified": ["single_issue_research"],
            "contradictions_integrated": 1,
            "invalidations": [],
            "preserved": ["Q1"],
            "disclosed_keys": ["C1", "Q2"],
            "min_support": {"Q1": 1, "Q3": 1},
            "conditions": {"Q3": ["Q2 is disclosed"]},
        },
        "baseline_truth": {
            "outcome": "disclosed: quality bar unmet",
            "open_contradictions": 0,
            "classification": ["single_issue_research"],
        },
    },
}
