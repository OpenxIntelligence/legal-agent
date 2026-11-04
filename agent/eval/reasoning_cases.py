"""First-principles reasoning benchmark: synthetic matters with planted truth.

Each fixture is a small invented matter exercising one failure or
success category the upgrade targets: simple and complex questions,
missing or contradictory facts, multiple jurisdictions, unverified
authorities, adverse precedent, exceptions and deadlines, contractual
ambiguity, unsupported assumptions, drafting, and cases where the
correct answer is that more information is needed.

Sound fixtures must file: the reasoning gate, the memo rules, and
the scripted claims check all pass. Defective fixtures must be
caught: the upgraded path refuses at the reasoning gate while the
legacy path (memo rules + scripted claims only) would file the
same memo. Truth is planted in the fixture, never derived from
system output. The grader (`eval/reasoning_run.py`) runs both
modes on every fixture and compares them.

Fixture data must stay synthetic and self-contained: documents are
invented, authority excerpts are explicitly marked as fixture text,
and no fixture asserts real law.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def A(qclass, question, *, issue="", status="answered", resolution="",
      rule_or_source="the assignment and the inbox documents",
      evidence="documents read; passages recorded as facts",
      answer="", basis="", contrary="", effect="",
      depends_on=None, threshold=False):
    """One foundational answer with compact defaults (overrides win)."""
    return {
        "class": qclass,
        "question": question,
        "rule_or_source": rule_or_source,
        "evidence": evidence,
        "answer": answer or f"Resolved for this fixture: {question}",
        "basis": basis or "Fixture record as stated in the documents.",
        "contrary": contrary or "No contrary evidence in the fixture record.",
        "effect": effect or "Supports proceeding to a conclusion.",
        "status": status,
        "resolution": resolution,
        "issue": issue,
        "depends_on": list(depends_on or []),
        "threshold": bool(threshold),
    }


def E(issue, *, test="", element="", rule="", source="",
      facts_evidence="", application="", supporting="", opposing="",
      missing="None identified.", finding="", effect=""):
    """One element analysis with compact defaults (overrides win)."""
    return {
        "issue": issue,
        "test": test or "Fixture test",
        "element": element or "Fixture element",
        "rule": rule or "Fixture rule",
        "source": source or "Fixture source",
        "facts_evidence": facts_evidence or "Fixture facts as recorded.",
        "application": application or "The rule applied to the fixture facts.",
        "supporting": supporting or "The recorded evidence supports the finding.",
        "opposing": opposing or "No opposing argument in the fixture record.",
        "missing": missing,
        "finding": finding or "Provisionally found for this fixture.",
        "effect": effect or "Supports the overall result as stated.",
    }


def AR(**kw):
    """One adversarial review with compact defaults (overrides win)."""
    review = {
        "strongest_against": "Fixture challenge: the conclusion overreads thin evidence.",
        "fact_risks": "Fixture facts are few; a new document could revise them.",
        "adverse_authority": "No adverse authority in the fixture record.",
        "overlooked": "No further exceptions or procedure in the fixture record.",
        "opposing_counsel": "Would dispute the weight given to the thin record.",
        "judge_needs": "A skeptical reader would want corroboration.",
        "alternative_theory": "No alternative theory fits the fixture record.",
        "reversing_fact": "A contradicting document would reverse the conclusion.",
        "revision_needed": False,
        "revision": "No change needed: the challenge found no material weakness.",
    }
    review.update(kw)
    return review


def F(item, document_id, page, quote, claim, **kw):
    """One fact to record (stance/kind/basis via kw)."""
    fact = {
        "item": item, "document_id": document_id, "page": page,
        "quote": quote, "claim": claim,
    }
    fact.update(kw)
    return fact


def C(item, status, summary, citations, **kw):
    """One memo finding (kind/branch via kw)."""
    finding = {
        "item": item, "status": status, "summary": summary,
        "citations": citations,
    }
    finding.update(kw)
    return finding


def Q(doc, page, quote):
    return {"document_id": doc, "page": page, "quote": quote}


CASES = {
    # ---- sound: simple question -------------------------------------------
    "simple-fact": {
        "title": "Simple factual question",
        "task_type": "investigation",
        "sound": True,
        "uncertainty": False,
        "objective": "Do internal records show open defects?",
        "documents": {
            "SF-STATUS": [
                "Harbor weekly status. Open Severity 1 defects: 4 at period end."
            ],
        },
        "checklist": [
            {"id": "defects", "question": "Do internal records show open defects?",
             "evidence_needed": "status report"},
        ],
        "facts": [
            F("defects", "SF-STATUS", 1, "Open Severity 1 defects: 4",
              "records show 4 open sev-1 defects"),
        ],
        "foundational": [
            A("objective", "Do internal records show open defects?",
              answer="Yes-or-no question about the period-end defect count."),
            A("facts", "What does the status report show?", issue="defects",
              answer="4 open Severity 1 defects at period end."),
            A("evidence", "What supports the defect count?", issue="defects",
              answer="The status report passage, recorded verbatim."),
            A("adverse", "Is there evidence against open defects?",
              answer="None in the one-page fixture record."),
            A("unknowns", "What could change the answer?",
              answer="A later status could revise the count; none is in the record."),
            A("action", "What action is available?",
              answer="Report the count; proportionate to the thin record."),
        ],
        "adversarial": [AR()],
        "memo": {
            "conclusion": "Internal records show 4 open Severity 1 defects at period end.",
            "findings": [
                C("defects", "asserted", "Internal records show 4 open Sev-1 defects.",
                  [Q("SF-STATUS", 1, "Open Severity 1 defects: 4")]),
            ],
            "gaps": "None.",
        },
        "unsupported": [],
        "truth": {"gate": True, "rules": True, "claims": "passed"},
    },
    # ---- sound: complex litigation ----------------------------------------
    "complex-litigation": {
        "title": "Litigation with governing law and elements",
        "task_type": "litigation",
        "sound": True,
        "uncertainty": True,
        "objective": "Did the vendor breach a California delivery duty?",
        "documents": {
            "CL-CONTRACT": [
                "Services agreement. Vendor shall deliver the module by March 1."
            ],
            "CL-PERFORM": [
                "Performance memo. The vendor delivered the module on March 1 in Sacramento."
            ],
            "CL-LAW": [
                "Operator-supplied code excerpt (fixture text, not real law). "
                "A breach requires duty, breach, causation, and damages."
            ],
        },
        "checklist": [
            {"id": "breach", "question": "Was the delivery duty breached?",
             "evidence_needed": "agreement and performance records"},
            {"id": "governing", "question": "Which regime governs the duty?",
             "evidence_needed": "connecting facts"},
            {"id": "duty", "question": "What does a breach require?",
             "evidence_needed": "supplied authority excerpt"},
        ],
        "facts": [
            F("breach", "CL-CONTRACT", 1,
              "Vendor shall deliver the module by March 1.",
              "the agreement states a March 1 delivery duty"),
            F("governing", "CL-PERFORM", 1,
              "The vendor delivered the module on March 1 in Sacramento.",
              "performance occurred in Sacramento"),
            F("duty", "CL-LAW", 1,
              "A breach requires duty, breach, causation, and damages.",
              "fixture excerpt states breach elements", kind="legal_rule",
              authority="A1"),
        ],
        "sources": [
            {"document_id": "CL-LAW", "repo": "ca-leginfo",
             "citation": "Cal. Civ. Code (fixture excerpt)", "version": "2026-01-01"},
        ],
        "authorities": [
            {"proposition": "Breach requires duty, breach, causation, damages.",
             "document_id": "CL-LAW",
             "citation": "Cal. Civ. Code (fixture excerpt)",
             "version": "2026-01-01", "effective_date": "2026-01-01",
             "level": "binding", "verification": "verified",
             "treatment_checked": True,
             "treatment": "No contrary treatment in the fixture record."},
        ],
        "assessments": [
            {"authority": "A1", "page": 1,
             "quote": "A breach requires duty, breach, causation, and damages.",
             "treatment_read": True,
             "detail": "No contrary treatment in the fixture record."},
        ],
        "branches": [
            {"issue": "governing", "regime": "ca", "status": "known",
             "basis": "operator connecting facts: California performance"},
        ],
        "foundational": [
            A("objective", "Did the vendor breach a California delivery duty?"),
            A("jurisdiction", "Which forum is relevant?",
              answer="Fixture assumes a California forum; forum choice is out of scope."),
            A("governing_law", "Which law governs the duty?", issue="governing",
              answer="California law, on the supplied connecting facts."),
            A("claim", "What claim is at issue?",
              answer="Breach of the March 1 delivery duty."),
            A("elements", "What must a breach show?", issue="duty",
              answer="Duty, breach, causation, damages per the fixture excerpt."),
            A("facts", "What happened?", issue="breach",
              answer="A March 1 duty was stated; delivery occurred March 1."),
            A("evidence", "What supports each fact?", issue="breach",
              answer="Agreement and performance passages, recorded verbatim."),
            A("deadlines", "Do any deadlines apply?",
              answer="None in the fixture record."),
            A("exceptions", "Any exceptions or defenses?",
              answer="Timely performance would defeat breach; delivery date matches."),
            A("adverse", "Any adverse authority?",
              answer="None in the fixture record."),
            A("unknowns", "What is still unknown?",
              answer="Causation and damages are not in the record.",
              effect="The conclusion stays provisional on those elements."),
            A("action", "What action is available?",
              answer="Report provisional findings; gather causation and damages proof."),
        ],
        "elements": [
            E("governing", test="governing law", element="connecting facts",
              rule="Performance-centered duties follow the place of performance (fixture rule).",
              source="operator connecting facts with branch B1",
              facts_evidence="Delivery in Sacramento, recorded verbatim.",
              application="Sacramento performance points to California law.",
              supporting="The only connecting facts are Californian.",
              opposing="The agreement names no governing law.",
              finding="California law provisionally governs.",
              effect="Selects the breach test applied to the duty."),
            E("duty", test="breach", element="duty",
              rule="A breach requires duty, breach, causation, and damages.",
              source="CL-LAW via ca-leginfo (fixture excerpt)",
              facts_evidence="The agreement states a March 1 delivery duty.",
              application="The stated duty satisfies the duty element.",
              supporting="Express written duty with a fixed date.",
              opposing="No opposing reading of the duty language.",
              finding="Duty element provisionally met.",
              effect="One of four elements; breach, causation, damages remain."),
        ],
        "adversarial": [AR(
            strongest_against="Timely delivery on March 1 defeats breach.",
            revision_needed=True,
            revision="Narrowed the conclusion: duty owed, breach not shown.",
        )],
        "memo": {
            "conclusion": "The vendor owed March 1 delivery under California law; "
                         "timely delivery on that date means breach is not shown.",
            "findings": [
                C("breach", "asserted",
                  "The agreement imposed March 1 delivery; delivery occurred that day.",
                  [Q("CL-CONTRACT", 1, "Vendor shall deliver the module by March 1.")]),
                C("governing", "asserted", "California law governs the duty.",
                  [Q("CL-PERFORM", 1,
                     "The vendor delivered the module on March 1 in Sacramento.")],
                  kind="applicability", branch="B1"),
                C("duty", "asserted",
                  "Breach requires duty, breach, causation, and damages.",
                  [Q("CL-LAW", 1,
                     "A breach requires duty, breach, causation, and damages.")],
                  kind="legal_rule"),
            ],
            "gaps": "Causation and damages not examined; forum choice assumed.",
            "depends_on": ["B1"],
        },
        "unsupported": [],
        "truth": {"gate": True, "rules": True, "claims": "passed"},
    },
    # ---- sound: missing facts handled as unresolved ------------------------
    "missing-facts": {
        "title": "Missing certification handled as unresolved",
        "task_type": "investigation",
        "sound": True,
        "uncertainty": True,
        "objective": "Is the module certified?",
        "documents": {
            "MF-STATUS": ["Harbor weekly status. Overall status is amber."],
        },
        "checklist": [
            {"id": "cert", "question": "Is the module certified?",
             "evidence_needed": "signed certificate"},
        ],
        "facts": [],
        "foundational": [
            A("objective", "Is the module certified?"),
            A("facts", "What shows certification?", issue="cert",
              status="unresolved",
              resolution="Release the signed certificate into the inbox.",
              answer="", basis="No certification passage in the record.",
              contrary="Absence of evidence is not evidence of absence.",
              effect="Certification cannot be asserted."),
            A("evidence", "What evidence exists?", issue="cert",
              status="unresolved",
              resolution="Release the signed certificate into the inbox.",
              answer="", basis="Only a status page; nothing about certification.",
              effect="No citation can support a certification finding."),
            A("adverse", "Any adverse evidence?",
              answer="None; the question is unanswerable either way."),
            A("unknowns", "What could change the answer?",
              answer="The signed certificate would settle it.",
              effect="The memo reports the item as unresolved."),
            A("action", "What action is available?",
              answer="Request the certificate; report the item unresolved meanwhile."),
        ],
        "adversarial": [AR(
            strongest_against="Amber status might imply not certified — but implication is not proof.",
        )],
        "memo": {
            "conclusion": "Certification cannot be determined: no certificate is in the record.",
            "findings": [
                C("cert", "unresolved",
                  "No certification evidence in the record.", []),
            ],
            "gaps": "Certification status unknown; the signed certificate is outstanding.",
        },
        "unsupported": [],
        "truth": {"gate": True, "rules": True, "claims": "passed"},
    },
    # ---- defective: contradiction with omitted counterargument -------------
    "omitted-counterargument": {
        "title": "Contradiction with omitted counterargument",
        "task_type": "investigation",
        "sound": False,
        "uncertainty": False,
        "objective": "Did lapsed maintenance cause the valve failure?",
        "documents": {
            "OC-REPORT": ["Inspection report. The valve showed wear."],
            "OC-MAINT": [
                "Maintenance log. Maintenance was performed on schedule in every quarter."
            ],
        },
        "checklist": [
            {"id": "cause", "question": "What caused the valve wear?",
             "evidence_needed": "inspection and maintenance records"},
        ],
        "facts": [
            F("cause", "OC-REPORT", 1, "The valve showed wear.",
              "wear observed"),
            F("cause", "OC-MAINT", 1,
              "Maintenance was performed on schedule in every quarter.",
              "maintenance on schedule", stance="opposes"),
        ],
        "foundational": [
            A("objective", "Did lapsed maintenance cause the valve failure?"),
            A("facts", "What happened?", issue="cause",
              answer="Wear observed; maintenance records show on-schedule work."),
            A("evidence", "What supports each account?", issue="cause",
              answer="Inspection shows wear; the log cuts against a neglect theory."),
            A("adverse", "Any adverse evidence?",
              answer="Yes: the on-schedule maintenance log.", issue="cause"),
            A("unknowns", "What is unknown?",
              answer="The actual cause of the wear."),
            A("action", "What action is available?",
              answer="Report the conflict; do not assert causation."),
        ],
        "adversarial": [],
        "memo": {
            "conclusion": "The valve failed because maintenance lapsed.",
            "findings": [
                C("cause", "asserted", "The valve failed because maintenance lapsed.",
                  [Q("OC-REPORT", 1, "The valve showed wear.")]),
            ],
            "gaps": "None.",
        },
        "unsupported": ["finding:cause", "conclusion"],
        "truth": {"gate": False, "gate_problems": ["no adversarial review"],
                  "rules": True, "claims": "failed"},
    },
    # ---- sound: multiple jurisdictions --------------------------------------
    "multi-jurisdiction": {
        "title": "Renewal across two candidate regimes",
        "task_type": "research",
        "sound": True,
        "uncertainty": True,
        "objective": "Which regime governs the license renewal?",
        "documents": {
            "MJ-NOTE": [
                "Counsel note. The license renews in California and in England."
            ],
        },
        "checklist": [
            {"id": "scope", "question": "Which regime governs the renewal?",
             "evidence_needed": "connecting facts"},
        ],
        "facts": [
            F("scope", "MJ-NOTE", 1,
              "The license renews in California and in England.",
              "renewal touches two systems"),
        ],
        "branches": [
            {"issue": "scope", "regime": "ca", "status": "unknown",
             "basis": "operator note: California renewal"},
            {"issue": "scope", "regime": "ew", "status": "unknown",
             "basis": "operator note: England renewal"},
        ],
        "foundational": [
            A("objective", "Which regime governs the license renewal?"),
            A("jurisdiction", "Which systems are candidates?",
              answer="California and England and Wales; neither is defaulted."),
            A("governing_law", "Which law governs?", issue="scope",
              status="unresolved",
              resolution="Operator connecting facts selecting one regime.",
              answer="", basis="Two candidate branches are open.",
              effect="No substantive conclusion under either regime."),
            A("law_status", "What is the law's status?",
              status="unresolved",
              resolution="Retrieve the version in force at the event date.",
              answer="", basis="No in-force version retrieved.",
              effect="Today's text cannot be assumed to govern."),
            A("claim", "What is at issue?",
              answer="The renewal's governing regime."),
            A("adverse", "Any adverse authority?",
              answer="None assessable before the regime is known."),
            A("unknowns", "What is unknown?",
              answer="Connecting facts and in-force versions.",
              effect="The memo reports the scope as unresolved."),
        ],
        "adversarial": [AR(
            strongest_against="Assuming either regime without connecting facts.",
        )],
        "memo": {
            "conclusion": "The governing regime cannot be determined yet: two "
                         "candidates are open and connecting facts are outstanding.",
            "findings": [
                C("scope", "unresolved",
                  "Which regime governs the renewal is unresolved.", []),
            ],
            "gaps": "Connecting facts and in-force versions outstanding.",
        },
        "unsupported": [],
        "truth": {"gate": True, "rules": True, "claims": "passed"},
    },
    # ---- defective: legal rule without provenance ---------------------------
    "unverified-authority": {
        "title": "Legal rule asserted without provenance",
        "task_type": "research",
        "sound": False,
        "uncertainty": False,
        "objective": "What limitation period applies?",
        "documents": {
            "UA-LAW": [
                "Supplied excerpt (fixture text, not real law). "
                "The limitation period is three years."
            ],
        },
        "checklist": [
            {"id": "limit", "question": "What limitation period applies?",
             "evidence_needed": "supplied authority excerpt"},
        ],
        "facts": [
            F("limit", "UA-LAW", 1, "The limitation period is three years.",
              "fixture excerpt states a three-year period", kind="legal_rule"),
        ],
        "sources": [],
        "foundational": [
            A("objective", "What limitation period applies?"),
            A("jurisdiction", "Which system?",
              answer="Fixture assumes one system; scope is out of scope."),
            A("governing_law", "Which law governs?", issue="limit",
              answer="The supplied excerpt, provisionally."),
            A("law_status", "Is it current?",
              answer="Unverified: no version recorded.",
              contrary="Without a version the excerpt cannot be relied on.",
              effect="Undermines any substantive conclusion."),
            A("claim", "What is at issue?", answer="The limitation period."),
            A("adverse", "Any adverse authority?", answer="None in the record."),
            A("unknowns", "What is unknown?",
              answer="Whether the excerpt is current and authentic."),
        ],
        "adversarial": [AR()],
        "memo": {
            "conclusion": "A three-year limitation period applies.",
            "findings": [
                C("limit", "asserted", "The limitation period is three years.",
                  [Q("UA-LAW", 1, "The limitation period is three years.")],
                  kind="legal_rule"),
            ],
            "gaps": "Authority currency unverified.",
        },
        "unsupported": [],
        "truth": {"gate": False, "gate_problems": ["no recorded provenance"],
                  "rules": True, "claims": "passed"},
    },
    # ---- defective: provenance without proposition registration ------------
    "unregistered-authority": {
        "title": "Legal rule with provenance but no registered authority",
        "task_type": "research",
        "sound": False,
        "uncertainty": False,
        "objective": "What limitation period applies?",
        "documents": {
            "RA-LAW": [
                "Supplied excerpt (fixture text, not real law). "
                "The limitation period is three years."
            ],
        },
        "checklist": [
            {"id": "limit", "question": "What limitation period applies?",
             "evidence_needed": "supplied authority excerpt"},
        ],
        "facts": [
            F("limit", "RA-LAW", 1, "The limitation period is three years.",
              "fixture excerpt states a three-year period", kind="legal_rule"),
        ],
        "sources": [
            {"document_id": "RA-LAW", "repo": "ca-leginfo",
             "citation": "Fixture Code (not real law)", "version": "2026-01-01"},
        ],
        "foundational": [
            A("objective", "What limitation period applies?"),
            A("jurisdiction", "Which system?",
              answer="Fixture assumes one system."),
            A("governing_law", "Which law governs?", issue="limit",
              answer="The supplied excerpt, provisionally."),
            A("law_status", "Is it current?",
              answer="Version 2026-01-01 recorded.",
              effect="Currency established; proposition still unregistered."),
            A("claim", "What is at issue?", answer="The limitation period."),
            A("adverse", "Any adverse authority?", answer="None in the record."),
            A("unknowns", "What is unknown?",
              answer="Authority level and subsequent treatment.",
              effect="The proposition cannot be relied on yet."),
        ],
        "elements": [
            E("limit", test="limitation", element="period",
              rule="The limitation period is three years.",
              source="RA-LAW via ca-leginfo (fixture excerpt)",
              facts_evidence="Excerpt recorded verbatim.",
              application="The excerpt states three years.",
              supporting="Express period language.",
              opposing="No opposing reading.",
              finding="Three years.",
              effect="Answers the question, pending registration."),
        ],
        "adversarial": [AR()],
        "memo": {
            "conclusion": "A three-year limitation period applies.",
            "findings": [
                C("limit", "asserted", "The limitation period is three years.",
                  [Q("RA-LAW", 1, "The limitation period is three years.")],
                  kind="legal_rule"),
            ],
            "gaps": "Authority level and treatment outstanding.",
        },
        "unsupported": [],
        "truth": {"gate": False, "gate_problems": ["no registered authority"],
                  "rules": True, "claims": "passed"},
    },
    # ---- defective: adverse precedent unaddressed ----------------------------
    "adverse-unaddressed": {
        "title": "Adverse precedent recorded but unaddressed",
        "task_type": "litigation",
        "sound": False,
        "uncertainty": False,
        "objective": "Does the acceptance test support the claim?",
        "documents": {
            "AU-REC": ["Status report. The module failed acceptance testing."],
            "AU-OP": [
                "Opposing decision excerpt (fixture text, not real law). "
                "Courts have rejected this reading."
            ],
        },
        "checklist": [
            {"id": "reading", "question": "Does the test failure support the claim?",
             "evidence_needed": "test records and decisional law"},
        ],
        "facts": [
            F("reading", "AU-REC", 1, "The module failed acceptance testing.",
              "acceptance testing failed"),
            F("reading", "AU-OP", 1, "Courts have rejected this reading.",
              "fixture excerpt rejects the reading",
              stance="opposes", kind="legal_rule", authority="A1"),
        ],
        "sources": [
            {"document_id": "AU-OP", "repo": "ca-opinions",
             "citation": "Fixture Op. (not real law)", "version": "2026-01-01"},
        ],
        "authorities": [
            {"proposition": "Courts have rejected this reading.",
             "document_id": "AU-OP",
             "citation": "Fixture Op. (not real law)",
             "version": "2026-01-01", "effective_date": "2026-01-01",
             "level": "persuasive", "verification": "provisional",
             "treatment_checked": False,
             "treatment": "Not checked in the fixture record."},
        ],
        "foundational": [
            A("objective", "Does the acceptance test support the claim?"),
            A("jurisdiction", "Which forum?",
              answer="Fixture assumes one forum."),
            A("governing_law", "Which law?", issue="reading",
              answer="Fixture decisional law, provisionally."),
            A("claim", "What claim?", answer="The acceptance-test claim."),
            A("elements", "What elements?", issue="reading",
              answer="Fixture test with one element: test outcome."),
            A("facts", "What happened?", issue="reading",
              answer="Acceptance testing failed."),
            A("evidence", "What supports it?", issue="reading",
              answer="The status report; an adverse excerpt cuts against the reading."),
            A("deadlines", "Deadlines?", answer="None in the fixture record."),
            A("exceptions", "Exceptions?", answer="None in the fixture record."),
            A("adverse", "Adverse authority?", issue="reading",
              answer="A fixture decision excerpt rejects the reading.",
              contrary="The excerpt is recorded but not addressed in the memo.",
              effect="Must be addressed before any conclusion."),
            A("unknowns", "Unknowns?", answer="None material in the fixture."),
            A("action", "Action?",
              answer="Address the adverse excerpt before concluding."),
        ],
        "adversarial": [],
        "memo": {
            "conclusion": "The test failure supports the claim.",
            "findings": [
                C("reading", "asserted", "The test failure supports the claim.",
                  [Q("AU-REC", 1, "The module failed acceptance testing.")]),
            ],
            "gaps": "None.",
        },
        "unsupported": ["finding:reading"],
        "truth": {"gate": False, "gate_problems": ["no adversarial review"],
                  "rules": True, "claims": "failed"},
    },
    # ---- defective: deadlines question never answered ------------------------
    "unanswered-deadlines": {
        "title": "Litigation filed without the deadlines question",
        "task_type": "litigation",
        "sound": False,
        "uncertainty": False,
        "objective": "Is the filed claim timely?",
        "documents": {
            "UD-REC": ["Filing memo. The claim was filed on June 1."],
        },
        "checklist": [
            {"id": "timely", "question": "Is the filed claim timely?",
             "evidence_needed": "filing records and limitation rules"},
        ],
        "facts": [
            F("timely", "UD-REC", 1, "The claim was filed on June 1.",
              "filing occurred June 1"),
        ],
        "foundational": [
            A("objective", "Is the filed claim timely?"),
            A("jurisdiction", "Which forum?", answer="Fixture assumes one forum."),
            A("governing_law", "Which law?", issue="timely",
              answer="Fixture limitation law, provisionally."),
            A("claim", "What claim?", answer="The filed June 1 claim."),
            A("elements", "What elements?", issue="timely",
              answer="Timeliness only, for this fixture."),
            A("facts", "What happened?", issue="timely",
              answer="The claim was filed June 1."),
            A("evidence", "What supports it?", issue="timely",
              answer="The filing memo passage."),
            A("exceptions", "Exceptions?", answer="None in the fixture record."),
            A("adverse", "Adverse authority?", answer="None in the record."),
            A("unknowns", "Unknowns?",
              answer="The limitation period itself was never examined.",
              effect="Timeliness cannot be concluded."),
            A("action", "Action?", answer="Examine the limitation period first."),
        ],
        "adversarial": [AR()],
        "memo": {
            "conclusion": "The claim is timely.",
            "findings": [
                C("timely", "asserted", "The claim was filed on June 1.",
                  [Q("UD-REC", 1, "The claim was filed on June 1.")]),
            ],
            "gaps": "None.",
        },
        "unsupported": ["conclusion"],
        "truth": {"gate": False, "gate_problems": ["'deadlines' is unanswered"],
                  "rules": True, "claims": "failed"},
    },
    # ---- sound: contractual ambiguity held open -------------------------------
    "contractual-ambiguity": {
        "title": "Ambiguous support term held provisionally",
        "task_type": "contract",
        "sound": True,
        "uncertainty": True,
        "objective": "When did support end?",
        "documents": {
            "CA-AGREE": [
                "Services agreement. Support ends 90 days after delivery. "
                "Delivery occurred on January 10."
            ],
        },
        "checklist": [
            {"id": "support_end", "question": "When did support end?",
             "evidence_needed": "agreement terms and delivery records"},
        ],
        "facts": [
            F("support_end", "CA-AGREE", 1, "Support ends 90 days after delivery.",
              "support term stated"),
            F("support_end", "CA-AGREE", 1, "Delivery occurred on January 10.",
              "delivery date stated"),
        ],
        "foundational": [
            A("objective", "When did support end?"),
            A("claim", "What is at issue?",
              answer="The support end date under the agreement."),
            A("elements", "What must be settled?", issue="support_end",
              answer="The meaning of delivery and the resulting date."),
            A("facts", "What do the terms say?", issue="support_end",
              answer="90 days after delivery; delivery January 10."),
            A("evidence", "What supports each reading?", issue="support_end",
              answer="Both passages recorded; acceptance date unknown.",
              contrary="Delivery could mean acceptance rather than shipment."),
            A("exceptions", "Any exceptions?",
              answer="An acceptance-based reading would move the date."),
            A("unknowns", "What is unknown?",
              answer="The acceptance date.",
              effect="The conclusion stays provisional on the shipment reading."),
            A("action", "What action?",
              answer="Report the provisional date; seek the acceptance date."),
        ],
        "elements": [
            E("support_end", test="contract interpretation",
              element="support end date",
              rule="Give the parties' words their plain meaning.",
              source="CA-AGREE operative language",
              facts_evidence="90-day term; January 10 delivery.",
              application="Ninety days after January 10 is April 10 on a shipment reading.",
              supporting="Express term with an express trigger date.",
              opposing="Delivery could mean acceptance, whose date is unknown.",
              missing="Acceptance date.",
              finding="Provisionally April 10 on the shipment reading.",
              effect="Dates the support obligation subject to the ambiguity."),
        ],
        "adversarial": [AR(
            alternative_theory="Acceptance-based reading moves the end date.",
            judge_needs="The acceptance date or evidence delivery means shipment.",
        )],
        "memo": {
            "conclusion": "Support presumptively ended April 10 on a shipment "
                         "reading; an acceptance reading stays open.",
            "findings": [
                C("support_end", "asserted",
                  "Support ends 90 days after delivery; delivery was January 10.",
                  [Q("CA-AGREE", 1, "Support ends 90 days after delivery."),
                   Q("CA-AGREE", 1, "Delivery occurred on January 10.")]),
            ],
            "gaps": "Acceptance date unknown; acceptance reading unresolved.",
        },
        "unsupported": [],
        "truth": {"gate": True, "rules": True, "claims": "passed"},
    },
    # ---- defective: numeric confidence and baseless inference -----------------
    "overconfident-conclusion": {
        "title": "Overconfident conclusion on a baseless inference",
        "task_type": "risk",
        "sound": False,
        "uncertainty": False,
        "objective": "Were all invoices properly paid?",
        "documents": {
            "OV-LEDGER": ["Ledger page. Paid invoices total 41000."],
        },
        "checklist": [
            {"id": "paid", "question": "Were all invoices properly paid?",
             "evidence_needed": "ledger and payment records"},
        ],
        "facts": [
            F("paid", "OV-LEDGER", 1, "Paid invoices total 41000.",
              "paid total 41000"),
            F("paid", "OV-LEDGER", 1, "Paid invoices total 41000.",
              "all invoices were properly paid", kind="inference"),
        ],
        "foundational": [
            A("objective", "Were all invoices properly paid?"),
            A("governing_law", "Which obligations apply?",
              answer="Fixture payment obligations only."),
            A("claim", "What is at issue?", answer="Payment propriety."),
            A("exceptions", "Exceptions?",
              answer="A total alone proves no invoice was proper."),
            A("unknowns", "Unknowns?",
              answer="Per-invoice propriety is entirely unknown.",
              effect="No propriety conclusion is supportable."),
            A("action", "Action?",
              answer="Report the total; do not assert propriety."),
        ],
        "adversarial": [AR(
            strongest_against="A total is not per-invoice proof.",
            revision_needed=True,
            revision="Narrowed the finding to the total; kept the conclusion worded as recorded.",
        )],
        "memo": {
            "conclusion": "We are 85% confident all invoices were properly paid.",
            "findings": [
                C("paid", "asserted", "Paid invoices total 41000.",
                  [Q("OV-LEDGER", 1, "Paid invoices total 41000.")]),
            ],
            "gaps": "Per-invoice propriety unknown.",
        },
        "unsupported": ["conclusion"],
        "truth": {"gate": False,
                  "gate_problems": ["numeric confidence", "no basis"],
                  "rules": True, "claims": "failed"},
    },
    # ---- sound: drafting with recorded revision -------------------------------
    "drafting-request": {
        "title": "Term clause drafted with ambiguity removed",
        "task_type": "drafting",
        "sound": True,
        "uncertainty": True,
        "objective": "Draft the term clause.",
        "documents": {
            "DR-TERMS": ["Term sheet. The parties agree to a 12-month term."],
        },
        "checklist": [
            {"id": "term_clause", "question": "What term should the clause state?",
             "evidence_needed": "agreed term sheet"},
        ],
        "facts": [
            F("term_clause", "DR-TERMS", 1,
              "The parties agree to a 12-month term.", "12-month term agreed"),
        ],
        "foundational": [
            A("objective", "What should the clause achieve?",
              answer="State a 12-month term with no automatic renewal."),
            A("jurisdiction", "Which system?",
              answer="Fixture assumes one system."),
            A("governing_law", "Which law?", answer="Fixture contract law."),
            A("claim", "What is at issue?", answer="The operative term language."),
            A("elements", "What must the wording settle?", issue="term_clause",
              answer="Duration, start, and renewal."),
            A("exceptions", "Adverse readings?",
              answer="Silence on renewal invites an auto-renewal reading.",
              effect="The draft states no automatic renewal expressly."),
            A("unknowns", "Unknowns?",
              answer="Counterparty acceptance of the no-renewal language.",
              effect="Flagged as a gap for human review."),
            A("action", "Action?",
              answer="Propose the clause; route to human review."),
        ],
        "elements": [
            E("term_clause", test="operative clarity", element="renewal",
              rule="Operative terms must settle renewal expressly.",
              source="Fixture drafting standard",
              facts_evidence="Term sheet agrees a 12-month term; silent on renewal.",
              application="Silence is ambiguous, so the draft negates renewal.",
              supporting="Express negation removes the ambiguity.",
              opposing="The counterparty may have expected renewal.",
              missing="Counterparty acceptance.",
              finding="Draft states 12 months with no automatic renewal.",
              effect="The clause is unambiguous as proposed."),
        ],
        "adversarial": [AR(
            alternative_theory="Auto-renewal reading of a silent clause.",
            revision_needed=True,
            revision="Added 'with no automatic renewal' to the draft.",
        )],
        "memo": {
            "conclusion": "Proposed clause: a 12-month term with no automatic "
                         "renewal, for human review.",
            "findings": [
                C("term_clause", "asserted",
                  "The parties agreed a 12-month term.",
                  [Q("DR-TERMS", 1, "The parties agree to a 12-month term.")]),
            ],
            "gaps": "Counterparty acceptance of the no-renewal language outstanding; "
                   "human review required.",
        },
        "unsupported": [],
        "truth": {"gate": True, "rules": True, "claims": "passed"},
    },
    # ---- sound: the right answer is more information ---------------------------
    "more-info-needed": {
        "title": "Filing advice correctly withheld",
        "task_type": "strategy",
        "sound": True,
        "uncertainty": True,
        "objective": "Should the client file now?",
        "documents": {
            "MI-NOTE": ["Intake note. The client seeks advice on filing."],
        },
        "checklist": [
            {"id": "filing", "question": "Should the client file now?",
             "evidence_needed": "forum, deadlines, and key documents"},
        ],
        "facts": [],
        "foundational": [
            A("objective", "Should the client file now?"),
            A("jurisdiction", "Which forum?",
              status="unresolved", resolution="Ask the client where to file.",
              answer="", basis="The intake note names no forum.",
              effect="No forum-specific advice is supportable."),
            A("governing_law", "Which law?",
              status="unresolved", resolution="Identify the forum first.",
              answer="", basis="Law follows forum and claims, both unknown.",
              effect="No substantive advice is supportable."),
            A("claim", "What claim?",
              status="unresolved", resolution="Ask what the client wants to file.",
              answer="", basis="The intake note states no claim.",
              effect="No strategy comparison is supportable."),
            A("adverse", "Adverse considerations?",
              answer="Filing blind risks the wrong forum, missed deadlines, and costs.",
              effect="Reinforces withholding advice until facts exist."),
            A("unknowns", "What is unknown?",
              answer="Forum, deadlines, documents, objectives.",
              effect="The memo withholds advice and lists the needs."),
            A("action", "What action?",
              answer="Gather forum, dates, documents, and objectives first.",
              effect="Filing advice waits for that record."),
        ],
        "adversarial": [AR(
            strongest_against="Delay could itself miss a deadline — which is why the memo seeks dates urgently, not silently.",
        )],
        "memo": {
            "conclusion": "No filing advice is supportable until the forum, "
                         "deadlines, and key documents are established.",
            "findings": [
                C("filing", "unresolved",
                  "Filing advice waits for forum, deadlines, and documents.", []),
            ],
            "gaps": "Forum, governing law, deadlines, documents, objectives outstanding.",
        },
        "unsupported": [],
        "truth": {"gate": True, "rules": True, "claims": "passed"},
    },
}


def write_document(root: Path, doc_id: str, pages: list[str]) -> str:
    """Write one inbox file plus its extraction transcript; return sha."""
    inbox = root / "workspace/inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    blob = "\n".join(pages).encode()
    (inbox / f"{doc_id}.pdf").write_bytes(blob)
    sha = hashlib.sha256(blob).hexdigest()
    cache = root / "workspace/cache/text"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / f"{sha}.json").write_text(
        json.dumps(
            {"pages": [{"page": n + 1, "text": text}
                       for n, text in enumerate(pages)]}
        )
    )
    return sha


def materialize(root: Path, name: str, *, with_reasoning: bool):
    """Build the fixture matter; return (run_id, record).

    with_reasoning=True records the fixture's foundational answers,
    element analyses, and adversarial reviews (the upgraded path);
    False leaves them out (the legacy path). Documents, checklist,
    facts, sources, and branches are identical either way.
    """
    from agent_kit.case_record import CaseRecord

    case = CASES[name]
    run_id = f"reasoning-{name}"
    for doc_id, pages in case.get("documents", {}).items():
        write_document(root, doc_id, pages)
    record = CaseRecord(root, run_id)
    record.set_checklist(case["checklist"])
    if case.get("documents"):
        record.mark_read(sorted(case["documents"]))
    for fact in case.get("facts", []):
        record.record_fact(
            fact["item"],
            fact["document_id"],
            fact["page"],
            fact["quote"],
            fact["claim"],
            stance=fact.get("stance", "supports"),
            kind=fact.get("kind", "fact"),
            basis=fact.get("basis", ""),
            authority=fact.get("authority", ""),
        )
    for source in case.get("sources", []):
        record.note_source(
            source["document_id"], source["repo"],
            source["citation"], source["version"],
            source.get("language", ""),
        )
    for authority in case.get("authorities", []):
        record.register_authority(
            proposition=authority["proposition"],
            document_id=authority["document_id"],
            citation=authority["citation"], version=authority["version"],
            effective_date=authority["effective_date"],
            level=authority["level"],
            verification=authority["verification"],
            treatment_checked=authority["treatment_checked"],
            treatment=authority["treatment"],
        )
    for assessment in case.get("assessments", []):
        record.assess_authority(
            assessment["authority"], page=assessment["page"],
            quote=assessment["quote"],
            treatment_read=assessment.get("treatment_read", False),
            detail=assessment.get("detail", ""),
        )
    for branch in case.get("branches", []):
        record.open_branch(
            branch["issue"], branch["regime"],
            status=branch.get("status", "unknown"),
            basis=branch.get("basis", ""),
        )
    if with_reasoning:
        record.set_task_type(case["task_type"])
        for answer in case.get("foundational", []):
            record.answer_foundational(
                qclass=answer["class"], question=answer["question"],
                rule_or_source=answer["rule_or_source"],
                evidence=answer["evidence"], answer=answer["answer"],
                basis=answer["basis"], contrary=answer["contrary"],
                effect=answer["effect"], status=answer.get("status", "answered"),
                resolution=answer.get("resolution", ""),
                issue=answer.get("issue", ""),
            )
        for element in case.get("elements", []):
            record.analyze_element(
                issue=element["issue"], test=element["test"],
                element=element["element"], rule=element["rule"],
                source=element["source"],
                facts_evidence=element["facts_evidence"],
                application=element["application"],
                supporting=element["supporting"],
                opposing=element["opposing"], missing=element["missing"],
                finding=element["finding"], effect=element["effect"],
            )
        for review in case.get("adversarial", []):
            record.record_adversarial(
                strongest_against=review["strongest_against"],
                fact_risks=review["fact_risks"],
                adverse_authority=review["adverse_authority"],
                overlooked=review["overlooked"],
                opposing_counsel=review["opposing_counsel"],
                judge_needs=review["judge_needs"],
                alternative_theory=review["alternative_theory"],
                reversing_fact=review["reversing_fact"],
                revision_needed=review["revision_needed"],
                revision=review["revision"],
            )
    return run_id, record
