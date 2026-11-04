"""Reusable legal artifacts: versioned views over shared case records.

A template defines one task-oriented workflow (its inputs, checks,
permissions, and view contract). An instance binds a template to one
matter's records: it holds versioned reviewer-facing values plus
provenance links, never copies of the underlying facts. Execution,
assessment, and storage stay with the existing agent loop, quality
controller, and case record; this module adds no competing store,
no per-template agent, and no new database.

The first template is the Citation & Claim Audit: a row per memo
assertion with deterministic citation checks (source, page, quote,
version, permission) and semantic support checks through the
existing claims verifier. Quote matching and assertion support are
separate verdicts on every row; they are never merged into one
"established" label.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from agent_kit import document_text, redactions
from agent_kit.case_record import CaseError, collaboration_export, effective_status
from agent_kit.panel_export import panel_export
from agent_kit.review_labels import label_status
from agent_kit.progress import now

_MD_SEALED = "[sealed: quarantined]"

TEMPLATE_AUDIT = "citation-claim-audit"
TEMPLATE_AUDIT_VERSION = "1.0.0"
TEMPLATE_BOARD = "case-chronology-board"
TEMPLATE_BOARD_VERSION = "1.0.0"
TEMPLATE_JX = "jurisdiction-matrix"
TEMPLATE_JX_VERSION = "1.0.0"
TEMPLATE_CT = "contract-review"
TEMPLATE_CT_VERSION = "1.0.0"
TEMPLATE_PB = "playbook-deviation"
TEMPLATE_PB_VERSION = "1.0.0"
TEMPLATE_CAL = "obligation-calendar"
TEMPLATE_CAL_VERSION = "1.0.0"
TEMPLATE_IN = "legal-intake"
TEMPLATE_IN_VERSION = "1.0.0"
TEMPLATE_DL = "matter-deadlines"
TEMPLATE_DL_VERSION = "1.0.0"
TEMPLATE_DR = "discovery-log"
TEMPLATE_DR_VERSION = "1.0.0"
TEMPLATE_HZ = "regulatory-horizon"
TEMPLATE_HZ_VERSION = "1.0.0"
TEMPLATE_GV = "ai-governance"
TEMPLATE_GV_VERSION = "1.0.0"
TEMPLATE_EC = "entity-compliance"
TEMPLATE_EC_VERSION = "1.0.0"
TEMPLATE_TS = "term-sheet"
TEMPLATE_TS_VERSION = "1.0.0"
TEMPLATE_PT = "patent-claims"
TEMPLATE_PT_VERSION = "1.0.0"
DEMO_RUN_ID = "run-demo-audit"
DEMO_MATTER_NOTE = (
    "synthetic demonstration: fictional parties, documents, and memo; "
    "no real matter, no live provider needed"
)


def data_mode(instance: dict) -> str:
    """Declared data mode for one artifact instance.

    "synthetic-demo" when the instance is bound to the synthetic
    demo matter, "matter-workspace" otherwise. Views and exports
    carry this so demo output is never mistaken for real work.
    """
    if isinstance(instance, dict) and instance.get("matter") == DEMO_RUN_ID:
        return "synthetic-demo"
    return "matter-workspace"


TEMPLATE_QA = "sellside-qa"
TEMPLATE_QA_VERSION = "1.0.0"
TEMPLATE_SP = "counsel-spend"
TEMPLATE_SP_VERSION = "1.0.0"

TEMPLATES: dict[str, dict] = {
    TEMPLATE_AUDIT: {
        "id": TEMPLATE_AUDIT,
        "version": TEMPLATE_AUDIT_VERSION,
        "name": "Citation & Claim Audit",
        "purpose": (
            "Check every quotation, page, source version, and permission "
            "behind a memo's assertions, then assess whether the assertions "
            "follow from their evidence. Reviewers correct rows and recheck "
            "only what changed."
        ),
        "audience": "Reviewing attorneys and legal analysts",
        "review_role": "Confirm or correct each flagged row; approve only the inspected snapshot",
        "inputs": [
            {"name": "memo", "type": "filed_memo", "required": True},
            {"name": "facts", "type": "evidence_ledger", "required": True},
            {"name": "documents", "type": "inbox_documents", "required": True},
        ],
        "prerequisites": [
            "a filed memo with findings",
            "analyzed source documents for quoted passages",
        ],
        "cannot_continue_when": [
            "no memo has been filed",
            "no findings carry citations",
        ],
        "capabilities": [
            "checks:citation-structural",
            "checks:claim-support",
            "tools:search_corpus",
        ],
        "evidence_requirements": [
            "quotations checked against saved extraction text",
            "source versions pinned by content hash",
            "privileged sources excluded from display and export",
        ],
        "outputs": {
            "rows": "one per finding, conclusion, and gaps section",
            "row_view": ["assertion", "citations", "structural", "semantic", "contested", "review"],
            "versions": "immutable snapshots on every revision or review",
            "export": "json report with provenance, limitations, and approval scope",
        },
        "qa": {
            "required": ["citation-structural", "claim-support"],
            "advisory": [],
            "qualified_states": [
                "structural incomplete while a source is untranscribed",
                "semantic unavailable while no judge responds",
            ],
        },
        "permissions": {
            "revise": "reviewer",
            "recheck": "reviewer",
            "approve": "reviewer",
            "export": "viewer",
            "sharing_default": "private",
        },
        "budgets": {
            "structural": "deterministic; no model calls",
            "semantic": "one claims-judge call per recheck, ledgered under the run",
        },
        "triggers": [
            "row revised",
            "source file changed",
            "source access revoked",
            "new evidence recorded",
        ],
        "safe_failure": (
            "unrunnable checks stay explicit (incomplete/unavailable); "
            "nothing structurally invalid reaches semantic assessment"
        ),
        "demo": "demo-audit-matter",
    },
    TEMPLATE_BOARD: {
        "id": TEMPLATE_BOARD,
        "version": TEMPLATE_BOARD_VERSION,
        "name": "Case Chronology & Issue Board",
        "purpose": (
            "Turn shared records into dated events, open questions, "
            "action dependencies, and next steps. Events link to "
            "evidence or carry explicit uncertainty; disputed "
            "orderings stay flagged until resolved."
        ),
        "audience": "Case teams tracking events, questions, and owners",
        "review_role": "Confirm event dates and links; flag disputes; assign owners",
        "inputs": [
            {"name": "facts", "type": "evidence_ledger", "required": True},
            {"name": "checklist", "type": "issue_list", "required": True},
            {"name": "tasks", "type": "task_ledger", "required": True},
        ],
        "prerequisites": ["recorded evidence to date events from"],
        "cannot_continue_when": ["no facts are recorded"],
        "capabilities": [
            "checks:event-staleness",
            "scheduler:select_next",
            "tools:search_corpus",
        ],
        "evidence_requirements": [
            "events link recorded fact ids, never copied passages",
            "withdrawn facts stay linked but redacted and stale",
            "dates from text carry their source span; reviewer dates say so",
        ],
        "outputs": {
            "events": "dated, sourced-or-uncertain, owned",
            "candidates": "date spans detected in live fact quotes",
            "board": "live questions, tasks, dependencies, next step",
            "versions": "immutable snapshots on every change or review",
            "export": "json report with provenance, limitations, and approval scope",
        },
        "qa": {
            "required": ["event-staleness"],
            "advisory": [],
            "qualified_states": [
                "events with explicit uncertainty instead of sources",
                "reviewer-flagged disputed orderings",
            ],
        },
        "permissions": {
            "revise": "reviewer",
            "recheck": "reviewer",
            "approve": "reviewer",
            "export": "viewer",
            "sharing_default": "private",
        },
        "budgets": {
            "staleness": "deterministic; no model calls",
            "scheduling": "deterministic rubric over record state",
        },
        "triggers": [
            "event revised",
            "linked evidence withdrawn or changed",
            "new evidence recorded",
        ],
        "safe_failure": (
            "unresolvable dates stay explicit uncertainty; stale "
            "events keep their last values with reasons"
        ),
        "demo": "demo-audit-matter",
    },
    TEMPLATE_JX: {
        "id": TEMPLATE_JX,
        "version": TEMPLATE_JX_VERSION,
        "name": "Jurisdiction Survey Matrix",
        "purpose": (
            "Compare issues across candidate regimes with explicit "
            "authority, effective dates, applicability caveats, and "
            "review states. Unresolved applicability stays visible; "
            "nothing here is legal research until authoritative "
            "sources and legal review exist."
        ),
        "audience": "Researchers comparing regimes across issues",
        "review_role": "Confirm or flag each cell; resolve applicability per issue",
        "inputs": [
            {"name": "checklist", "type": "issue_list", "required": True},
            {"name": "branches", "type": "legal_branches", "required": False},
            {"name": "matter", "type": "matter_context", "required": False},
        ],
        "prerequisites": ["checklist issues to compare"],
        "cannot_continue_when": ["no checklist issues exist"],
        "capabilities": [
            "checks:cell-staleness",
            "sources:registry",
        ],
        "evidence_requirements": [
            "regimes are registry keys, never inferred from addresses",
            "authority citations are reviewer-recorded fixture text until "
            "authoritative sources are configured",
            "missing effective dates stay unknown; publication is not effectiveness",
        ],
        "outputs": {
            "cells": "one per issue × regime × layer with proposition, "
            "authority, effective date, caveat, branch link, review state",
            "readiness": "which cells block a definitive comparison, and why",
            "versions": "immutable snapshots on every change or review",
            "export": "json report with provenance, limitations, and approval scope",
        },
        "qa": {
            "required": ["cell-staleness"],
            "advisory": [],
            "qualified_states": [
                "provisional comparison with unresolved cells listed",
            ],
        },
        "permissions": {
            "revise": "reviewer",
            "recheck": "reviewer",
            "approve": "reviewer",
            "export": "viewer",
            "sharing_default": "private",
        },
        "budgets": {
            "staleness": "deterministic; no model calls",
        },
        "triggers": [
            "cell revised",
            "linked branch changed",
            "matter context changed",
            "noted source version changed",
        ],
        "safe_failure": (
            "unknown applicability gates definitive comparison; "
            "stale cells keep their last values with reasons"
        ),
        "demo": "demo-audit-matter",
    },
    TEMPLATE_CT: {
        "id": TEMPLATE_CT,
        "version": TEMPLATE_CT_VERSION,
        "name": "Clause/Term Tabular Review",
        "purpose": (
            "Turn contract passages into a source-linked table: exact "
            "quote, document version, reviewer restatement, risk flag, "
            "and review state per clause. The quote check is "
            "deterministic; the effect and risk stay reviewer "
            "judgment, never a model verdict presented as law."
        ),
        "audience": "Commercial counsel reviewing clauses and terms",
        "review_role": "Restate each clause's effect, flag risks, confirm or retract",
        "inputs": [
            {"name": "documents", "type": "inbox_documents", "required": True},
        ],
        "prerequisites": ["read documents with saved extraction"],
        "cannot_continue_when": ["no documents have been read"],
        "capabilities": [
            "checks:quote-fidelity",
            "checks:clause-staleness",
        ],
        "evidence_requirements": [
            "every clause binds an exact quote on a read page",
            "quotes are checked against saved extraction, not memory",
            "privileged or unread documents refuse new clauses",
        ],
        "outputs": {
            "clauses": "one per reviewed passage with quote, effect, "
            "risk, and review state",
            "exceptions": "flagged or high-risk clauses awaiting decision",
            "versions": "immutable snapshots on every change or review",
            "export": "json report with provenance, limitations, and approval scope",
        },
        "qa": {
            "required": ["quote-fidelity", "clause-staleness"],
            "advisory": [],
            "qualified_states": [
                "review with flagged risks listed as open exceptions",
            ],
        },
        "permissions": {
            "revise": "reviewer",
            "recheck": "reviewer",
            "approve": "reviewer",
            "export": "viewer",
            "sharing_default": "private",
        },
        "budgets": {
            "checks": "deterministic; no model calls",
        },
        "triggers": [
            "clause revised",
            "source document changed",
            "source access revoked",
        ],
        "safe_failure": (
            "changed or revoked sources stale their clauses with "
            "reasons; quotes never pass on a different version"
        ),
        "demo": "demo-audit-matter",
    },
    TEMPLATE_PB: {
        "id": TEMPLATE_PB,
        "version": TEMPLATE_PB_VERSION,
        "name": "Playbook Deviation & Exception Queue",
        "purpose": (
            "Compare reviewed clauses against a reviewer-written "
            "playbook: expected positions, severity, and per-clause "
            "verdicts with rationale. Deviations and undecided "
            "items queue for decision; conforms and waivers stay "
            "out of the queue. Verdicts are reviewer judgment, "
            "never automatic legal conclusions."
        ),
        "audience": "Counsel routing contract exceptions for approval",
        "review_role": "Write positions, assess each clause, clear the queue",
        "inputs": [
            {"name": "contract_review", "type": "contract_instance", "required": True},
        ],
        "prerequisites": ["a contract-review instance in the same matter"],
        "cannot_continue_when": ["no contract-review instance exists"],
        "capabilities": [
            "checks:deviation-staleness",
        ],
        "evidence_requirements": [
            "every deviation binds one clause and one rule version",
            "clause or rule revisions stale their deviations with reasons",
            "retracted clauses and rules orphan their deviations visibly",
        ],
        "outputs": {
            "rules": "reviewer-written positions with severity",
            "deviations": "per-clause verdicts with rationale and pins",
            "exceptions": "deviating or undecided items awaiting decision",
            "versions": "immutable snapshots on every change or review",
            "export": "json report with provenance, limitations, and approval scope",
        },
        "qa": {
            "required": ["deviation-staleness"],
            "advisory": [],
            "qualified_states": [
                "review with open exceptions listed as undecided",
            ],
        },
        "permissions": {
            "revise": "reviewer",
            "recheck": "reviewer",
            "approve": "reviewer",
            "export": "viewer",
            "sharing_default": "private",
        },
        "budgets": {
            "checks": "deterministic; no model calls",
        },
        "triggers": [
            "deviation assessed",
            "clause revised or retracted",
            "rule revised or retracted",
        ],
        "safe_failure": (
            "stale deviations keep their last verdict with reasons "
            "and stay queued until re-assessed"
        ),
        "demo": "demo-audit-matter",
    },
    TEMPLATE_CAL: {
        "id": TEMPLATE_CAL,
        "version": TEMPLATE_CAL_VERSION,
        "name": "Renewal & Obligation Calendar",
        "purpose": (
            "Track dated obligations from reviewed clauses: renewals, "
            "notices, payments, termination windows. Each obligation "
            "binds its clause; unknown dates stay unknown and sort "
            "apart. Overdue is always relative to a stated as-of "
            "date, never a silent wall clock."
        ),
        "audience": "Counsel and operators tracking contract dates",
        "review_role": "Date each obligation, resolve or waive with reasons",
        "inputs": [
            {"name": "contract_review", "type": "contract_instance", "required": True},
        ],
        "prerequisites": ["a contract-review instance in the same matter"],
        "cannot_continue_when": ["no contract-review instance exists"],
        "capabilities": [
            "checks:obligation-staleness",
        ],
        "evidence_requirements": [
            "every obligation binds one clause in the source review",
            "due dates are reviewer-recorded; malformed dates are refused",
            "clause revisions stale their obligations with reasons",
        ],
        "outputs": {
            "obligations": "dated items with kind, status, and clause pins",
            "upcoming": "open dated items sorted earliest-first, unknowns apart",
            "versions": "immutable snapshots on every change or review",
            "export": "json report with provenance, limitations, and approval scope",
        },
        "qa": {
            "required": ["obligation-staleness"],
            "advisory": [],
            "qualified_states": [
                "review with unknown-dated obligations listed",
            ],
        },
        "permissions": {
            "revise": "reviewer",
            "recheck": "reviewer",
            "approve": "reviewer",
            "export": "viewer",
            "sharing_default": "private",
        },
        "budgets": {
            "checks": "deterministic; no model calls",
        },
        "triggers": [
            "obligation revised or resolved",
            "clause revised or retracted",
        ],
        "safe_failure": (
            "stale obligations keep their last values with reasons; "
            "unknown dates never default to today"
        ),
        "demo": "demo-audit-matter",
    },
    TEMPLATE_IN: {
        "id": TEMPLATE_IN,
        "version": TEMPLATE_IN_VERSION,
        "name": "Legal Intake & Routing",
        "purpose": (
            "Take in legal requests, triage them with an owner and "
            "priority, and route each to a named destination with a "
            "recorded rationale — or decline it with a reason. The "
            "queue shows exactly what still needs a decision."
        ),
        "audience": "Intake coordinators and supervising counsel",
        "review_role": "Triage, assign, route, or decline each request",
        "inputs": [
            {"name": "checklist", "type": "issue_list", "required": False},
        ],
        "prerequisites": ["none: intake precedes investigation"],
        "cannot_continue_when": [],
        "capabilities": [
            "checks:intake-staleness",
        ],
        "evidence_requirements": [
            "optional issue links are validated against the checklist",
            "linked issue revisions stale their requests with reasons",
            "every routing or decline carries its rationale",
        ],
        "outputs": {
            "intakes": "requests with category, urgency, owner, and status",
            "queue": "new and triaged items awaiting routing, urgent first",
            "versions": "immutable snapshots on every change or review",
            "export": "json report with provenance, limitations, and approval scope",
        },
        "qa": {
            "required": ["intake-staleness"],
            "advisory": [],
            "qualified_states": [
                "review with unrouted items listed as pending",
            ],
        },
        "permissions": {
            "revise": "reviewer",
            "recheck": "reviewer",
            "approve": "reviewer",
            "export": "viewer",
            "sharing_default": "private",
        },
        "budgets": {
            "checks": "deterministic; no model calls",
        },
        "triggers": [
            "request revised, routed, or decided",
            "linked issue revised or removed",
        ],
        "safe_failure": (
            "stale requests keep their last values with reasons; "
            "the queue never silently drops an item"
        ),
        "demo": "demo-audit-matter",
    },
    TEMPLATE_DL: {
        "id": TEMPLATE_DL,
        "version": TEMPLATE_DL_VERSION,
        "name": "Matter & Deadlines Board",
        "purpose": (
            "One board for matter deadlines: dated items with "
            "owners, optionally tracking obligations from the "
            "calendar. Linked items stale when their obligation "
            "changes, so the board never silently disagrees with "
            "its source."
        ),
        "audience": "Matter owners tracking deadlines and owners",
        "review_role": "Date each deadline, assign owners, resolve with reasons",
        "inputs": [
            {"name": "calendar", "type": "calendar_instance", "required": True},
        ],
        "prerequisites": ["an obligation-calendar instance in the same matter"],
        "cannot_continue_when": ["no obligation-calendar instance exists"],
        "capabilities": [
            "checks:deadline-staleness",
        ],
        "evidence_requirements": [
            "obligation links are validated against the source calendar",
            "linked obligation revisions stale their deadlines",
            "due dates are reviewer-recorded; malformed dates are refused",
        ],
        "outputs": {
            "deadlines": "dated items with kind, owner, status, links",
            "upcoming": "open dated items sorted earliest-first, unknowns apart",
            "versions": "immutable snapshots on every change or review",
            "export": "json report with provenance, limitations, and approval scope",
        },
        "qa": {
            "required": ["deadline-staleness"],
            "advisory": [],
            "qualified_states": [
                "review with unknown-dated or unowned items listed",
            ],
        },
        "permissions": {
            "revise": "reviewer",
            "recheck": "reviewer",
            "approve": "reviewer",
            "export": "viewer",
            "sharing_default": "private",
        },
        "budgets": {
            "checks": "deterministic; no model calls",
        },
        "triggers": [
            "deadline revised or resolved",
            "linked obligation revised or resolved",
        ],
        "safe_failure": (
            "stale deadlines keep their last values with reasons; "
            "unknown dates never default to today"
        ),
        "demo": "demo-audit-matter",
    },
    TEMPLATE_DR: {
        "id": TEMPLATE_DR,
        "version": TEMPLATE_DR_VERSION,
        "name": "Discovery Request / Response Log",
        "purpose": (
            "Log discovery requests in both directions with due "
            "dates, record each response with its received date "
            "and produced documents, and track what still needs "
            "an answer. Produced documents are pinned, so a "
            "changed file stales its response."
        ),
        "audience": "Litigators tracking requests and productions",
        "review_role": "Serve requests, log responses, complete or object",
        "inputs": [
            {"name": "inbox_documents", "type": "inbox_documents", "required": False},
        ],
        "prerequisites": ["none: the log precedes production"],
        "cannot_continue_when": [],
        "capabilities": [
            "checks:response-staleness",
        ],
        "evidence_requirements": [
            "produced documents must exist in the inbox when logged",
            "document changes stale their responses with reasons",
            "every objection, withdrawal, and completion carries a reason",
        ],
        "outputs": {
            "requests": "propounded and received requests with status",
            "responses": "dated answers with produced-document pins",
            "queue": "draft, served, responded, and objected items awaiting action",
            "versions": "immutable snapshots on every change or review",
            "export": "json report with provenance, limitations, and approval scope",
        },
        "qa": {
            "required": ["response-staleness"],
            "advisory": [],
            "qualified_states": [
                "review with pending requests listed",
            ],
        },
        "permissions": {
            "revise": "reviewer",
            "recheck": "reviewer",
            "approve": "reviewer",
            "export": "viewer",
            "sharing_default": "private",
        },
        "budgets": {
            "checks": "deterministic; no model calls",
        },
        "triggers": [
            "request revised, served, answered, or decided",
            "produced document changed",
        ],
        "safe_failure": (
            "stale responses keep their last values with reasons; "
            "the queue never silently drops an item"
        ),
        "demo": "demo-audit-matter",
    },
    TEMPLATE_HZ: {
        "id": TEMPLATE_HZ,
        "version": TEMPLATE_HZ_VERSION,
        "name": "Regulatory Horizon Scan",
        "purpose": (
            "Log regulatory developments a reviewer is watching, "
            "with provenance, observed and effective dates, and "
            "relevance to the matter. Items are reviewer records "
            "only: nothing here is live monitoring, and nothing "
            "claims to be current law."
        ),
        "audience": "Counsel tracking regulatory change",
        "review_role": "Log developments, assess relevance, act or close",
        "inputs": [
            {"name": "matrix", "type": "matrix_instance", "required": True},
        ],
        "prerequisites": ["a jurisdiction-matrix instance in the same matter"],
        "cannot_continue_when": ["no jurisdiction-matrix instance exists"],
        "capabilities": [
            "checks:horizon-staleness",
        ],
        "evidence_requirements": [
            "every item names the source where it was observed",
            "cell links are validated against the source matrix",
            "linked cell revisions stale their items with reasons",
        ],
        "outputs": {
            "watches": "logged developments with dates and relevance",
            "queue": "watching and assessing items awaiting action",
            "versions": "immutable snapshots on every change or review",
            "export": "json report with provenance, limitations, and approval scope",
        },
        "qa": {
            "required": ["horizon-staleness"],
            "advisory": [],
            "qualified_states": [
                "review with unknown effective dates listed",
            ],
        },
        "permissions": {
            "revise": "reviewer",
            "recheck": "reviewer",
            "approve": "reviewer",
            "export": "viewer",
            "sharing_default": "private",
        },
        "budgets": {
            "checks": "deterministic; no model calls",
        },
        "triggers": [
            "watch revised or decided",
            "linked cell revised or retracted",
        ],
        "safe_failure": (
            "stale watches keep their last values with reasons; "
            "unknown effective dates never default to observed dates"
        ),
        "demo": "demo-audit-matter",
    },
    TEMPLATE_GV: {
        "id": TEMPLATE_GV,
        "version": TEMPLATE_GV_VERSION,
        "name": "AI Governance / Use-case Inventory",
        "purpose": (
            "Inventory AI use cases a reviewer is governing, "
            "with purpose, data, risk tier, and approval state. "
            "Risk tiers are reviewer judgments recorded here, "
            "never measured evaluations."
        ),
        "audience": "Counsel governing AI use",
        "review_role": "Log use cases, assess risk, approve with conditions",
        "inputs": [
            {"name": "intake", "type": "intake_instance", "required": True},
        ],
        "prerequisites": ["a legal-intake instance in the same matter"],
        "cannot_continue_when": ["no legal-intake instance exists"],
        "capabilities": [
            "checks:governance-staleness",
        ],
        "evidence_requirements": [
            "every use case states its purpose and data",
            "intake links are validated against the source queue",
            "linked request revisions stale their use cases with reasons",
        ],
        "outputs": {
            "uses": "inventoried use cases with risk and approval state",
            "queue": "proposed and assessed cases awaiting action",
            "versions": "immutable snapshots on every change or review",
            "export": "json report with conditions, limitations, and approval scope",
        },
        "qa": {
            "required": ["governance-staleness"],
            "advisory": [],
            "qualified_states": [
                "approval with recorded conditions",
            ],
        },
        "permissions": {
            "revise": "reviewer",
            "recheck": "reviewer",
            "approve": "reviewer",
            "export": "viewer",
            "sharing_default": "private",
        },
        "budgets": {
            "checks": "deterministic; no model calls",
        },
        "triggers": [
            "use case revised or decided",
            "linked intake request revised, routed, or decided",
        ],
        "safe_failure": (
            "stale use cases keep their last values with reasons; "
            "unassessed risk never defaults to low"
        ),
        "demo": "demo-audit-matter",
    },
    TEMPLATE_EC: {
        "id": TEMPLATE_EC,
        "version": TEMPLATE_EC_VERSION,
        "name": "Entity Compliance Calendar",
        "purpose": (
            "Track compliance filings per entity, with "
            "reviewer-recorded due dates and filing state. "
            "Unknown dates stay unknown; overdue is relative "
            "to an explicit as-of date only."
        ),
        "audience": "Counsel tracking entity filings",
        "review_role": "Log entities and filings, record filing outcomes",
        "inputs": [],
        "prerequisites": [],
        "cannot_continue_when": ["a filing names no known entity"],
        "capabilities": [
            "checks:entity-staleness",
        ],
        "evidence_requirements": [
            "every filing names its entity, kind, and period or unknown",
            "filings pin their entity's identity at log time",
            "entity revisions stale their filings with reasons",
        ],
        "outputs": {
            "entities": "registered entities with regime and identifier",
            "filings": "filings with due dates and filing state",
            "queue": "pending filings awaiting action",
            "versions": "immutable snapshots on every change or review",
            "export": "json report with limitations and approval scope",
        },
        "qa": {
            "required": ["entity-staleness"],
            "advisory": [],
            "qualified_states": [
                "review with unknown due dates listed",
            ],
        },
        "permissions": {
            "revise": "reviewer",
            "recheck": "reviewer",
            "approve": "reviewer",
            "export": "viewer",
            "sharing_default": "private",
        },
        "budgets": {
            "checks": "deterministic; no model calls",
        },
        "triggers": [
            "entity or filing revised or decided",
        ],
        "safe_failure": (
            "stale filings keep their last values with reasons; "
            "unknown due dates never default to today"
        ),
        "demo": "demo-audit-matter",
    },
    TEMPLATE_TS: {
        "id": TEMPLATE_TS,
        "version": TEMPLATE_TS_VERSION,
        "name": "Venture Term-Sheet Comparison",
        "purpose": (
            "Compare term sheets term by term, with each "
            "sheet's recorded position and a reviewer "
            "verdict on whether the term diverges."
        ),
        "audience": "Counsel comparing financing offers",
        "review_role": "Log sheets and terms, record positions, mark divergence",
        "inputs": [],
        "prerequisites": [],
        "cannot_continue_when": ["fewer than two sheets are logged"],
        "capabilities": [
            "checks:termsheet-staleness",
        ],
        "evidence_requirements": [
            "every position names its sheet and states its terms",
            "positions pin their sheet's identity at record time",
            "sheet revisions stale their positions with reasons",
        ],
        "outputs": {
            "sheets": "term sheets under comparison",
            "terms": "comparison rows with per-sheet positions and verdicts",
            "queue": "open terms awaiting positions or verdicts",
            "versions": "immutable snapshots on every change or review",
            "export": "json report with limitations and approval scope",
        },
        "qa": {
            "required": ["termsheet-staleness"],
            "advisory": [],
            "qualified_states": [
                "review with incomplete positions listed",
            ],
        },
        "permissions": {
            "revise": "reviewer",
            "recheck": "reviewer",
            "approve": "reviewer",
            "export": "viewer",
            "sharing_default": "private",
        },
        "budgets": {
            "checks": "deterministic; no model calls",
        },
        "triggers": [
            "sheet, term, or position revised or decided",
        ],
        "safe_failure": (
            "stale terms keep their last positions with reasons; "
            "a missing position never counts as agreement"
        ),
        "demo": "demo-audit-matter",
    },
    TEMPLATE_PT: {
        "id": TEMPLATE_PT,
        "version": TEMPLATE_PT_VERSION,
        "name": "Patent Claim Comparison",
        "purpose": (
            "Compare claim elements against products or "
            "prior art, element by element, with a "
            "recorded reading per subject and a reviewer "
            "mapping verdict. Unclear is a verdict, not a "
            "missing verdict."
        ),
        "audience": "Counsel comparing claims to products or art",
        "review_role": "Log subjects and elements, record readings, mark mapping",
        "inputs": [],
        "prerequisites": [],
        "cannot_continue_when": ["fewer than two subjects are logged"],
        "capabilities": [
            "checks:patent-staleness",
        ],
        "evidence_requirements": [
            "every reading names its subject and states its basis",
            "readings pin their subject's identity at record time",
            "subject revisions stale their readings with reasons",
        ],
        "outputs": {
            "subjects": "claims, products, and art under comparison",
            "elements": "claim-chart rows with per-subject readings and verdicts",
            "queue": "open elements awaiting readings or verdicts",
            "versions": "immutable snapshots on every change or review",
            "export": "json report with limitations and approval scope",
        },
        "qa": {
            "required": ["patent-staleness"],
            "advisory": [],
            "qualified_states": [
                "review with incomplete readings listed",
            ],
        },
        "permissions": {
            "revise": "reviewer",
            "recheck": "reviewer",
            "approve": "reviewer",
            "export": "viewer",
            "sharing_default": "private",
        },
        "budgets": {
            "checks": "deterministic; no model calls",
        },
        "triggers": [
            "subject, element, or reading revised or decided",
        ],
        "safe_failure": (
            "stale elements keep their last readings with reasons; "
            "a missing reading never counts as mapped"
        ),
        "demo": "demo-audit-matter",
    },
    TEMPLATE_QA: {
        "id": TEMPLATE_QA,
        "version": TEMPLATE_QA_VERSION,
        "name": "Sell-side Q&A Tracker",
        "purpose": (
            "Track buyer questions and sell-side answers, "
            "with supporting documents pinned so later "
            "file changes stale the answers they support."
        ),
        "audience": "Sell-side counsel running diligence Q&A",
        "review_role": "Log questions, record answers, mark clarification needs",
        "inputs": [
            {"name": "documents", "type": "inbox_documents", "required": False},
        ],
        "prerequisites": [],
        "cannot_continue_when": ["a cited support document is not in the inbox"],
        "capabilities": [
            "checks:qa-support-staleness",
        ],
        "evidence_requirements": [
            "support documents must exist in the inbox when cited",
            "answers pin their support documents' hashes",
            "changed or missing documents stale their answers with reasons",
        ],
        "outputs": {
            "questions": "buyer questions with answers and support",
            "queue": "asked and clarify questions awaiting action",
            "versions": "immutable snapshots on every change or review",
            "export": "json report with limitations and approval scope",
        },
        "qa": {
            "required": ["qa-support-staleness"],
            "advisory": [],
            "qualified_states": [
                "review with unknown due dates listed",
            ],
        },
        "permissions": {
            "revise": "reviewer",
            "recheck": "reviewer",
            "approve": "reviewer",
            "export": "viewer",
            "sharing_default": "private",
        },
        "budgets": {
            "checks": "deterministic; no model calls",
        },
        "triggers": [
            "question revised, answered, or decided",
            "support document changed or removed",
        ],
        "safe_failure": (
            "stale answers keep their last text with reasons; "
            "unknown due dates never default to today"
        ),
        "demo": "demo-audit-matter",
    },
    TEMPLATE_SP: {
        "id": TEMPLATE_SP,
        "version": TEMPLATE_SP_VERSION,
        "name": "Outside Counsel Spend",
        "purpose": (
            "Track outside counsel engagements and "
            "invoices against reviewer-recorded budgets, "
            "with exact decimal totals per currency. "
            "Currencies are never converted."
        ),
        "audience": "Counsel managing outside spend",
        "review_role": "Log vendors, engagements, and invoices; approve spend",
        "inputs": [],
        "prerequisites": [],
        "cannot_continue_when": ["an invoice names no known engagement"],
        "capabilities": [
            "checks:spend-staleness",
        ],
        "evidence_requirements": [
            "every invoice names its engagement and states its amount",
            "invoices pin their engagement; engagements pin their vendor",
            "vendor and engagement revisions stale dependents with reasons",
        ],
        "outputs": {
            "vendors": "registered outside vendors",
            "engagements": "engagements with budgets and spend totals",
            "invoices": "invoices with amounts and approval state",
            "queue": "received and disputed invoices awaiting action",
            "versions": "immutable snapshots on every change or review",
            "export": "json report with limitations and approval scope",
        },
        "qa": {
            "required": ["spend-staleness"],
            "advisory": [],
            "qualified_states": [
                "review with unbudgeted engagements listed",
            ],
        },
        "permissions": {
            "revise": "reviewer",
            "recheck": "reviewer",
            "approve": "reviewer",
            "export": "viewer",
            "sharing_default": "private",
        },
        "budgets": {
            "checks": "deterministic; no model calls",
        },
        "triggers": [
            "vendor, engagement, or invoice revised or decided",
        ],
        "safe_failure": (
            "stale rows keep their last values with reasons; "
            "amounts that do not parse are refused, never rounded "
            "into meaning"
        ),
        "demo": "demo-audit-matter",
    },
}

# Honest catalog entries for slices not yet built. A Planned card
# carries no workspace, no demo, and no action button.
PLANNED: list[dict] = []


def gallery_cards() -> list[dict]:
    """Template catalog for the gallery landing page.

    Implemented templates are Demo with working actions; every
    other card is honestly Planned with its missing dependency
    named.
    """
    categories = {
        TEMPLATE_AUDIT: "Evidence",
        TEMPLATE_BOARD: "Matters",
        TEMPLATE_JX: "Research",
        TEMPLATE_CT: "Contracts",
        TEMPLATE_PB: "Contracts",
        TEMPLATE_CAL: "Contracts",
        TEMPLATE_IN: "Operations",
        TEMPLATE_DL: "Matters",
        TEMPLATE_DR: "Litigation",
        TEMPLATE_HZ: "Research",
        TEMPLATE_GV: "Governance",
        TEMPLATE_EC: "Corporate",
        TEMPLATE_TS: "Corporate",
        TEMPLATE_PT: "Litigation",
        TEMPLATE_QA: "Transaction",
        TEMPLATE_SP: "Finance",
    }
    cards = [
        {
            "id": template["id"],
            "name": template["name"],
            "category": categories.get(template["id"], ""),
            "status": "Demo",
            "purpose": template["purpose"],
            "audience": template["audience"],
            "inputs": [i["name"] for i in template["inputs"]],
            "actions": ["open_demo", "open_in_matter"],
        }
        for template in TEMPLATES.values()
    ]
    for planned in PLANNED:
        cards.append({**planned, "actions": []})
    return cards


def _stable(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode()
    ).hexdigest()


def _inbox_shas(root: Path) -> dict[str, str]:
    return {
        document["id"]: document["sha256"]
        for document in document_text.list_documents(root)
        if isinstance(document, dict) and document.get("id")
    }


def _fact_by_id(data: dict, fact_id: str) -> dict | None:
    return next(
        (f for f in data.get("facts", []) if isinstance(f, dict) and f.get("id") == fact_id),
        None,
    )


def _row_citations(finding: dict) -> list[dict]:
    citations = []
    for citation in finding.get("citations") or []:
        if not isinstance(citation, dict):
            continue
        citations.append(
            {
                "document_id": str(citation.get("document_id") or ""),
                "page": citation.get("page"),
                "quote": str(citation.get("quote") or ""),
            }
        )
    return citations


def _linked_facts(data: dict, item: str, citations: list[dict]) -> list[str]:
    """Recorded facts backing a row: same issue, same document and page."""
    linked = []
    for fact in data.get("facts", []) or []:
        if not isinstance(fact, dict) or fact.get("item") != item:
            continue
        for citation in citations:
            if (
                fact.get("document_id") == citation["document_id"]
                and str(fact.get("page")) == str(citation["page"])
            ):
                linked.append(fact["id"])
                break
    return sorted(set(linked))


def build_rows(memo: dict, data: dict, shas: dict[str, str]) -> list[dict]:
    """One audit row per finding, plus conclusion and gaps rows.

    Rows carry reviewer-editable values (assertion text, citations)
    and provenance links (fact ids, pinned source hashes) — never
    copies of the fact bodies.
    """
    rows = []
    findings = memo.get("findings") or []
    if not isinstance(findings, list):
        findings = []
    for index, finding in enumerate([f for f in findings if isinstance(f, dict)]):
        citations = _row_citations(finding)
        item = str(finding.get("item") or f"finding-{index + 1}")
        for citation in citations:
            citation["pin_sha"] = shas.get(citation["document_id"])
        rows.append(
            {
                "row_id": f"R{index + 1}",
                "kind": "finding",
                "item": item,
                "finding_status": str(finding.get("status") or ""),
                "assertion": str(finding.get("summary") or ""),
                "assertion_version": 1,
                "citations": citations,
                "fact_ids": _linked_facts(data, item, citations),
                "checks": {},
                "contested": {},
                "feedback": [],
                "history": [{"at": now(), "change": "opened", "detail": ""}],
            }
        )
    rows.append(
        {
            "row_id": "RC",
            "kind": "conclusion",
            "item": "",
            "finding_status": "",
            "assertion": str(memo.get("conclusion") or ""),
            "assertion_version": 1,
            "citations": [
                {
                    **citation,
                    "pin_sha": shas.get(citation["document_id"]),
                    "inherited": True,
                }
                for row in rows
                for citation in row["citations"]
            ],
            "fact_ids": sorted({fid for row in rows for fid in row["fact_ids"]}),
            "checks": {},
            "contested": {},
            "feedback": [],
            "history": [{"at": now(), "change": "opened", "detail": ""}],
        }
    )
    gaps = memo.get("gaps", "")
    if isinstance(gaps, list):
        gaps = "; ".join(str(g) for g in gaps)
    if str(gaps or "").strip():
        rows.append(
            {
                "row_id": "RG",
                "kind": "gaps",
                "item": "",
                "finding_status": "",
                "assertion": str(gaps),
                "assertion_version": 1,
                "citations": [],
                "fact_ids": [],
                "checks": {},
                "contested": {},
                "feedback": [],
                "history": [{"at": now(), "change": "opened", "detail": ""}],
            }
        )
    return rows


def _next_instance_id(data: dict) -> str:
    existing = data.get("artifacts") or {}
    seq = 1 + sum(
        1 for key in existing if isinstance(key, str) and key.startswith("A")
    )
    instance_id = f"A{seq}"
    while instance_id in existing:
        seq += 1
        instance_id = f"A{seq}"
    return instance_id


def _blank_review() -> dict:
    return {
        "decision": "pending",
        "notes": "",
        "qualifications": [],
        "snapshot": None,
        "at": None,
    }


def open_instance(
    data: dict, root: Path, memo: dict, title: str, run_id: str
) -> dict:
    """Create an audit instance from a filed memo (pure; caller saves).

    Raises CaseError when the memo cannot support an audit. The
    instance pins current source hashes so later file changes are
    detectable, and records template + policy versions.
    """
    from quality.records import evidence_fingerprint

    findings = [f for f in (memo.get("findings") or []) if isinstance(f, dict)]
    if not findings:
        raise CaseError("the audit needs a filed memo with findings")
    if not any(f.get("citations") for f in findings):
        raise CaseError("the audit needs findings that carry citations")
    instance_id = _next_instance_id(data)
    rows = build_rows(memo, data, _inbox_shas(root))
    instance = {
        "id": instance_id,
        "template": TEMPLATE_AUDIT,
        "template_version": TEMPLATE_AUDIT_VERSION,
        "matter": run_id,
        "title": title or "Citation & Claim Audit",
        "owner": "local",
        "viewers": ["local"],
        "created_at": now(),
        "memo_source": "filed",
        "rows": rows,
        "versions": [],
        "review": {
            "decision": "pending",
            "notes": "",
            "qualifications": [],
            "snapshot": None,
            "at": None,
        },
        "history": [{"at": now(), "event": "opened", "detail": f"{len(rows)} rows"}],
    }
    instance["versions"].append(
        {
            "v": 1,
            "at": now(),
            "reason": "opened",
            "rows": copy.deepcopy(rows),
            "evidence": evidence_fingerprint(data),
        }
    )
    return instance


def _flagged(data: dict) -> set[str]:
    return {
        flag.get("document_id")
        for flag in data.get("flags", []) or []
        if isinstance(flag, dict)
        and flag.get("kind") == "privileged"
        and flag.get("document_id")
    }


def audit_citation(
    root: Path, citation: dict, shas: dict[str, str], flagged: set[str]
) -> dict:
    """Deterministic checks for one citation (no model calls).

    Verdicts per aspect; quote_match/version_match are None when
    unrunnable rather than guessed. A matching quote establishes
    only that the words occur in the extraction — never that any
    interpretation of them is supported.
    """
    document_id = str(citation.get("document_id") or "")
    page = citation.get("page")
    quote = str(citation.get("quote") or "")
    result: dict[str, Any] = {
        "document_id": document_id,
        "page": page,
        "quote_present": bool(quote.strip()),
        "source_exists": document_id in shas,
        "page_valid": None,
        "quote_match": None,
        "version_match": None,
        "permitted": document_id not in flagged,
        "problems": [],
    }
    if not result["quote_present"]:
        result["problems"].append("citation carries no quotation")
    if not result["source_exists"]:
        result["problems"].append(f"{document_id or 'empty source'} is not in the inbox")
        return result
    try:
        pages = int(page)
        result["page_valid"] = pages >= 1
    except (TypeError, ValueError):
        result["page_valid"] = False
    if not result["page_valid"]:
        result["problems"].append(f"page {page!r} is not a valid page number")
        return result
    text = document_text.page_text(root, document_id, pages)
    if text is None:
        result["problems"].append(
            f"{document_id} has no saved extraction; analyze it before judging the quote"
        )
    elif text == "":
        result["problems"].append(f"{document_id} page {pages} is not transcribed")
    elif quote.strip():
        result["quote_match"] = document_text.quote_on_page(quote, text)
        if not result["quote_match"]:
            result["problems"].append(
                f"quote not found on {document_id} page {pages}"
            )
    pin = citation.get("pin_sha")
    if pin is not None:
        result["version_match"] = shas.get(document_id) == pin
        if not result["version_match"]:
            result["problems"].append(
                f"{document_id} changed since the citation was pinned"
            )
    if not result["permitted"]:
        result["problems"].append(f"{document_id} is privileged: access revoked")
    return result


def _structural_outcome(citations: list[dict]) -> str:
    if not citations:
        return "not_run"
    for citation in citations:
        if (
            not citation["quote_present"]
            or not citation["source_exists"]
            or citation["page_valid"] is False
            or citation["quote_match"] is False
            or citation["version_match"] is False
            or not citation["permitted"]
        ):
            return "failed"
    if any(citation["problems"] for citation in citations):
        return "incomplete"
    return "passed"


def audit_structural(
    root: Path, data: dict, rows: list[dict]
) -> dict[str, dict]:
    """Citation verdicts for rows, keyed by row id."""
    shas = _inbox_shas(root)
    flagged = _flagged(data)
    verdicts = {}
    for row in rows:
        citations = [
            audit_citation(root, citation, shas, flagged)
            for citation in row.get("citations") or []
        ]
        verdicts[row["row_id"]] = {
            "outcome": _structural_outcome(citations),
            "citations": citations,
            "at": now(),
            "inputs": {
                "assertion_version": row.get("assertion_version", 1),
                "citations_hash": _stable(row.get("citations") or []),
                "shas": {c["document_id"]: shas.get(c["document_id"]) for c in citations},
                "flags": sorted(flagged),
            },
        }
    return verdicts


def _adapted_memo(rows: list[dict]) -> dict:
    """Audit rows reshaped as a memo for the existing claims judge."""
    findings = []
    conclusion = ""
    gaps = ""
    for row in rows:
        if row.get("kind") == "conclusion":
            conclusion = row.get("assertion", "")
        elif row.get("kind") == "gaps":
            gaps = row.get("assertion", "")
        else:
            findings.append(
                {
                    # The row is the unit: several rows may audit one
                    # checklist item, and unit ids must stay unique.
                    "item": row.get("row_id"),
                    "summary": row.get("assertion", ""),
                    "status": row.get("finding_status") or "asserted",
                    "citations": [
                        {
                            "document_id": c.get("document_id"),
                            "page": c.get("page"),
                            "quote": c.get("quote"),
                        }
                        for c in row.get("citations") or []
                    ],
                }
            )
    return {"conclusion": conclusion, "findings": findings, "gaps": gaps}


def _unit_row(rows: list[dict]) -> dict[str, str]:
    mapping = {}
    for row in rows:
        if row.get("kind") == "conclusion":
            mapping["conclusion"] = row["row_id"]
        elif row.get("kind") == "gaps":
            mapping["gaps"] = row["row_id"]
        else:
            mapping[f"finding:{row.get('row_id')}"] = row["row_id"]
    return mapping


def eligible_rows(rows: list[dict], structural: dict[str, dict]) -> list[dict]:
    """Rows the judge may assess: findings with valid citations, plus
    the conclusion and gaps rows when at least one finding qualifies."""
    passed = {
        row["row_id"]
        for row in rows
        if row.get("kind") == "finding"
        and structural.get(row["row_id"], {}).get("outcome") == "passed"
    }
    eligible = []
    for row in rows:
        if not str(row.get("assertion") or "").strip():
            continue
        if row.get("kind") == "finding" and row["row_id"] not in passed:
            continue
        if row.get("kind") in ("conclusion", "gaps") and not passed:
            continue
        eligible.append(row)
    return eligible


def audit_support(
    ctx: Any, rows: list[dict], structural: dict[str, dict]
) -> dict[str, dict]:
    """Semantic support verdicts through the existing claims verifier.

    Finding rows whose citations are structurally invalid never
    reach the judge (not_run, with the prerequisite named) —
    mirroring how memo rules gate semantic verification. The
    conclusion and gaps rows aggregate across the memo, so they
    are eligible when at least one finding offers valid evidence;
    their own inherited citation problems stay visible on the
    structural layer. Judge failures and malformed responses come
    back unavailable, never failed.
    """
    from verification import verify_claims

    eligible = eligible_rows(rows, structural)
    verdicts: dict[str, dict] = {}
    for row in rows:
        if row not in eligible:
            if not str(row.get("assertion") or "").strip():
                problems = ["no assertion to assess"]
            elif row.get("kind") in ("conclusion", "gaps"):
                problems = ["no finding offers valid evidence to assess against"]
            else:
                problems = ["citation check did not pass: nothing valid to assess"]
            verdicts[row["row_id"]] = {
                "outcome": "not_run",
                "verdict": None,
                "problems": problems,
                "support": [],
                "at": now(),
                "verifier": verify_claims.VERIFIER_VERSION,
            }
    if not eligible:
        return verdicts
    result = verify_claims.run_verification(ctx, _adapted_memo(eligible))
    mapping = _unit_row(eligible)
    by_unit = result.get("verdicts") or {}
    support = result.get("support") or {}
    feedback = [str(p) for p in result.get("feedback") or []]
    for unit_id, row_id in mapping.items():
        if result.get("outcome") == verify_claims.OUTCOME_UNAVAILABLE:
            verdicts[row_id] = {
                "outcome": "unavailable",
                "verdict": None,
                "problems": feedback or ["judge gave no usable verdict"],
                "support": [],
                "at": now(),
                "verifier": result.get("verifier"),
            }
            continue
        if unit_id not in by_unit:
            verdicts[row_id] = {
                "outcome": "not_run",
                "verdict": None,
                "problems": ["the judge did not assess this unit"],
                "support": [],
                "at": now(),
                "verifier": result.get("verifier"),
            }
            continue
        verdict = by_unit[unit_id]
        unit_problems = [p for p in feedback if unit_id in p] or (
            feedback if verdict == "unsupported" else []
        )
        verdicts[row_id] = {
            "outcome": "failed" if verdict == "unsupported" else "passed",
            "verdict": verdict,
            "problems": unit_problems,
            "support": list(support.get(unit_id) or []),
            "at": now(),
            "verifier": result.get("verifier"),
        }
    return verdicts


def _sealed_docs(root: Path, doc_ids) -> set[str]:
    """Document ids currently quarantined (fail-closed seal set)."""
    return {
        doc_id for doc_id in doc_ids
        if doc_id and redactions.is_quarantined(root, doc_id)
    }


def scope_citations(citations: list, data: dict, root: Path) -> list:
    """Redact privileged and seal quarantined quotes (copies).

    Views and exports share this filter: privileged sources show
    [redacted: privileged], quarantined sources show
    [sealed: quarantined], applied to quote and passage alike.
    Input citations are never mutated.
    """
    items = [c for c in citations or [] if isinstance(c, dict)]
    flagged = _flagged(data)
    sealed = _sealed_docs(
        root, {str(c.get("document_id") or "") for c in items})
    out = []
    for citation in citations or []:
        if not isinstance(citation, dict):
            out.append(citation)
            continue
        citation = dict(citation)
        doc = str(citation.get("document_id") or "")
        if doc in flagged:
            citation["quote"] = "[redacted: privileged]"
            if "passage" in citation:
                citation["passage"] = "[redacted: privileged]"
        elif doc in sealed:
            citation["quote"] = _MD_SEALED
            if "passage" in citation:
                citation["passage"] = _MD_SEALED
        out.append(citation)
    return out


def contested_info(data: dict, item: str, root: Path | None = None) -> dict:
    """Whether recorded evidence disputes an issue, with the opposing passages.

    When root is given, quotes from quarantined documents are
    sealed: their text may have been meant hidden, so views and
    exports show the marker instead of the passage.
    """
    opposing = []
    for fact in data.get("facts", []) or []:
        if not (
            isinstance(fact, dict)
            and fact.get("item") == item
            and fact.get("stance") == "opposes"
        ):
            continue
        quote = fact.get("quote", "")
        if root is not None and redactions.is_quarantined(
            root, str(fact.get("document_id") or "")
        ):
            quote = _MD_SEALED
        opposing.append(
            {
                "fact_id": fact.get("id"),
                "document_id": fact.get("document_id"),
                "page": fact.get("page"),
                "quote": quote,
            }
        )
    supporting = any(
        isinstance(fact, dict)
        and fact.get("item") == item
        and fact.get("stance") == "supports"
        for fact in data.get("facts", []) or []
    )
    return {"contested": supporting and bool(opposing), "opposing": opposing}


def adversarial_summary(data: dict) -> dict | None:
    """Latest recorded challenge to the preliminary conclusion, if any.

    Read-only view over the case adversarial records (the
    argument stage): id, lens, time, whether revision was
    needed, and the brief fields. The case record stays the
    source of truth; this never re-judges the challenge.
    """
    entries = [
        entry for entry in data.get("adversarial", []) or []
        if isinstance(entry, dict)
    ]
    if not entries:
        return None
    latest = entries[-1]
    return {
        "id": latest.get("id"),
        "at": latest.get("at"),
        "lens": str(latest.get("lens", "") or ""),
        "revision_needed": bool(latest.get("revision_needed")),
        "revision": str(latest.get("revision", "") or ""),
        "strongest_against": str(latest.get("strongest_against", "") or ""),
        "alternative_theory": str(latest.get("alternative_theory", "") or ""),
        "reversing_fact": str(latest.get("reversing_fact", "") or ""),
        "n": len(entries),
    }


def _current_inputs(
    data: dict, root: Path, row: dict, evidence: str
) -> dict:
    shas = _inbox_shas(root)
    return {
        "assertion_version": row.get("assertion_version", 1),
        "citations_hash": _stable(row.get("citations") or []),
        "shas": {
            str(c.get("document_id")): shas.get(str(c.get("document_id")))
            for c in row.get("citations") or []
        },
        "flags": sorted(_flagged(data)),
        "evidence": evidence,
    }


def row_staleness(root: Path, data: dict, row: dict) -> dict:
    """Whether each check layer still covers the row's current inputs.

    Computed on read from stored input versions — no write hooks in
    the evidence paths. A revised claim, changed source file, new
    flag, or new evidence each stales the layers that depend on it.
    """
    from quality.records import evidence_fingerprint

    checks = row.get("checks") or {}
    if not checks:
        return {"structural": None, "semantic": None, "reasons": []}
    current = _current_inputs(data, root, row, evidence_fingerprint(data))
    reasons: list[str] = []
    stale: dict[str, bool | None] = {}
    stored = (checks.get("structural") or {}).get("inputs") or {}
    if not stored:
        stale["structural"] = None
    else:
        out = (
            stored.get("assertion_version") != current["assertion_version"]
            or stored.get("citations_hash") != current["citations_hash"]
            or stored.get("shas") != current["shas"]
            or stored.get("flags") != current["flags"]
        )
        stale["structural"] = out
        if out:
            if stored.get("assertion_version") != current["assertion_version"]:
                reasons.append("assertion revised since the citation check")
            if stored.get("citations_hash") != current["citations_hash"]:
                reasons.append("citations revised since the citation check")
            if stored.get("shas") != current["shas"]:
                reasons.append("a cited source file changed")
            if stored.get("flags") != current["flags"]:
                reasons.append("source access changed")
    stored = (checks.get("semantic") or {}).get("inputs") or {}
    if not stored:
        stale["semantic"] = None
    else:
        out = (
            stored.get("assertion_version") != current["assertion_version"]
            or stored.get("citations_hash") != current["citations_hash"]
            or stored.get("evidence") != current["evidence"]
        )
        stale["semantic"] = out
        if out and "assertion revised since the citation check" not in reasons:
            if stored.get("assertion_version") != current["assertion_version"]:
                reasons.append("assertion revised since the support check")
            if stored.get("citations_hash") != current["citations_hash"]:
                reasons.append("citations revised since the support check")
            if stored.get("evidence") != current["evidence"]:
                reasons.append("evidence changed since the support check")
    return {**stale, "reasons": reasons}


def record_checks(
    instance: dict,
    data: dict,
    root: Path,
    structural: dict[str, dict],
    semantic: dict[str, dict],
) -> list[str]:
    """Store fresh check verdicts on rows; return ids whose outcomes changed.

    Pure over the instance dict (caller saves). Rerunning identical
    checks changes nothing, so no new version or history entry is
    owed for an unchanged rerun.
    """
    from quality.records import evidence_fingerprint

    evidence = evidence_fingerprint(data)
    changed = []
    for row in instance.get("rows") or []:
        row_id = row["row_id"]
        struct = structural.get(row_id)
        sem = semantic.get(row_id)
        if struct is None or sem is None:
            continue
        sem = {
            **sem,
            "inputs": {
                "assertion_version": row.get("assertion_version", 1),
                "citations_hash": _stable(row.get("citations") or []),
                "evidence": evidence,
            },
        }
        before = row.get("checks") or {}
        after = {"structural": struct, "semantic": sem}
        same = _stable(
            {
                k: {kk: vv for kk, vv in v.items() if kk != "at"}
                for k, v in before.items()
            }
        ) == _stable(
            {
                k: {kk: vv for kk, vv in v.items() if kk != "at"}
                for k, v in after.items()
            }
        )
        if not same:
            changed.append(row_id)
            row["history"].append(
                {"at": now(), "change": "rechecked", "detail": ""}
            )
        row["checks"] = after
    return changed


DEFECT_CATEGORIES = (
    "unsupported_claim",
    "unsupported_conclusion",
    "citation_failed",
    "citation_incomplete",
    "contradictory_evidence",
    "uncited_counterevidence",
    "uncovered_issue",
    "unstated_open_issues",
)

DEFECT_SEVERITY = {
    "unsupported_claim": "medium",
    "unsupported_conclusion": "high",
    "citation_failed": "high",
    "citation_incomplete": "low",
    "contradictory_evidence": "medium",
    "uncited_counterevidence": "medium",
    "uncovered_issue": "high",
    "unstated_open_issues": "medium",
}

DEFECT_ACTION = {
    "unsupported_claim": (
        "narrow the assertion to what the cited passages state, cite "
        "stronger evidence, or qualify the uncertainty; then recheck"
    ),
    "unsupported_conclusion": (
        "narrow the conclusion to the supported findings or qualify it; "
        "then recheck the conclusion and its dependent findings"
    ),
    "citation_failed": (
        "correct the document, page, or quotation to the supplied "
        "source (re-pinning is explicit); then recheck"
    ),
    "citation_incomplete": (
        "analyze the source so its extraction exists, or replace the "
        "citation; then recheck"
    ),
    "contradictory_evidence": (
        "address the opposing passages in the finding or qualify the "
        "claim; contradiction needs reviewer judgment, not silence"
    ),
    "uncited_counterevidence": (
        "cite the opposing fact in the finding or record why it does "
        "not bear on the claim; then recheck"
    ),
    "uncovered_issue": (
        "amend the memo with a finding for this checklist item "
        "(asserted, unresolved, or escalated)"
    ),
    "unstated_open_issues": (
        "amend the memo gaps text to name the open issues, or close "
        "them with evidence; then recheck"
    ),
}


def _defect(
    category: str,
    target: str,
    kind: str,
    rationale: str,
    evidence: list,
    sources: list,
    discriminator: str = "",
) -> dict:
    return {
        "category": category,
        "severity": DEFECT_SEVERITY[category],
        "target": target,
        "kind": kind,
        "rationale": rationale,
        "evidence": [e for e in evidence if e],
        "sources": sources,
        "action": DEFECT_ACTION[category],
        "key": f"{category}:{target}:{discriminator}" if discriminator else f"{category}:{target}",
    }


def _conclusion_failure_inherited(row: dict, rows: list[dict]) -> bool:
    """RC structural failure fully explained by finding citations.

    The conclusion aggregates finding citations, so its citation
    failure usually duplicates finding defects the reviewer must
    fix first. It earns its own defect only when a failing
    (document, page) is unique to the conclusion (e.g. a citation
    added directly to the conclusion row). Granularity is
    document+page because recorded verdicts do not retain quotes.
    """
    own = {
        (str(verdict.get("document_id") or ""), verdict.get("page"))
        for verdict in (row.get("checks") or {}).get("structural", {}).get("citations") or []
        if isinstance(verdict, dict) and verdict.get("problems")
    }
    if not own:
        return True
    finding_keys = set()
    for other in rows:
        if other.get("kind") != "finding":
            continue
        for citation in other.get("citations") or []:
            if isinstance(citation, dict):
                finding_keys.add(
                    (str(citation.get("document_id") or ""), citation.get("page"))
                )
    return own <= finding_keys


def _row_sources(row: dict) -> list[dict]:
    return [
        {"document_id": str(c.get("document_id") or ""), "page": c.get("page")}
        for c in row.get("citations") or []
        if isinstance(c, dict)
    ]


def _counterevidence_objections(data: dict) -> list[dict]:
    """QC counterevidence objections for the filed memo (reused, not rebuilt).

    Runs the existing quality module against the memo the audit
    opened from; returns its objection findings unfiltered. An
    absent memo yields no objections rather than an error.
    """
    memo = data.get("memo")
    if not isinstance(memo, dict):
        return []
    from quality.modules import counterevidence
    from quality.records import evidence_fingerprint, memo_version

    class _Shim:
        pass

    case_shim = _Shim()
    case_shim.data = data
    ctx = _Shim()
    ctx.case = case_shim
    applies, _ = counterevidence.applies(ctx, memo, {})
    if not applies:
        return []
    review = {
        "memo_version": memo_version(data, memo),
        "evidence_version": evidence_fingerprint(data),
    }
    return counterevidence.run(ctx, memo, review).get("findings") or []


def _fact_ref(data: dict, fact_id: str) -> dict:
    fact = _fact_by_id(data, fact_id) or {}
    return {
        "fact_id": fact_id,
        "document_id": str(fact.get("document_id") or ""),
        "page": fact.get("page"),
    }


def synthesize_defects(instance: dict, data: dict, root: Path) -> list[dict]:
    """Current defect conditions for an audit instance (pure; no ids).

    Unifies the existing signals — recorded row checks, contested
    flags, the QC counterevidence module, memo coverage rules, and
    the gaps text — into actionable defect records. Rows without
    recorded checks, and unavailable/not_run outcomes, yield no
    defects: unassessed work stays explicitly unassessed, never a
    quiet pass and never a fabricated failure. `root` is accepted
    for signature symmetry with refresh_defects; all inputs here
    are recorded state, so partial rechecks cannot retire defects
    whose rows were not rechecked.
    """
    del root
    defects = []
    rows = [r for r in instance.get("rows") or [] if isinstance(r, dict)]
    for row in rows:
        row_id = row.get("row_id") or ""
        checks = row.get("checks") or {}
        structural = checks.get("structural") or {}
        semantic = checks.get("semantic") or {}
        citation_problems = [
            str(p)
            for citation in structural.get("citations") or []
            if isinstance(citation, dict)
            for p in citation.get("problems") or []
        ]
        inherited = row.get("kind") == "conclusion" and _conclusion_failure_inherited(
            row, rows
        )
        if inherited:
            pass
        elif structural.get("outcome") == "failed":
            defects.append(
                _defect(
                    "citation_failed", row_id, "row",
                    f"citation check failed for row {row_id}: "
                    + (
                        "; ".join(citation_problems)
                        if citation_problems
                        else "see citation detail"
                    ),
                    citation_problems,
                    _row_sources(row),
                )
            )
        elif structural.get("outcome") == "incomplete":
            defects.append(
                _defect(
                    "citation_incomplete", row_id, "row",
                    f"citation check incomplete for row {row_id}: "
                    + (
                        "; ".join(citation_problems)
                        if citation_problems
                        else "see citation detail"
                    ),
                    citation_problems,
                    _row_sources(row),
                )
            )
        if semantic.get("outcome") == "failed":
            problems = [str(p) for p in semantic.get("problems") or []]
            kind = row.get("kind")
            category = (
                "unsupported_conclusion" if kind == "conclusion" else "unsupported_claim"
            )
            defects.append(
                _defect(
                    category, row_id, "row",
                    f"support check failed for row {row_id}"
                    + (f" ({'; '.join(problems)})" if problems else ""),
                    problems,
                    _row_sources(row),
                )
            )
    for row in rows:
        item = row.get("item") or ""
        if row.get("kind") != "finding" or not item:
            continue
        contested = contested_info(data, str(item))
        if contested.get("contested"):
            opposing = [
                _fact_ref(data, str(e.get("id") or e.get("fact_id") or ""))
                for e in contested.get("opposing") or []
                if isinstance(e, dict)
            ]
            defects.append(
                _defect(
                    "contradictory_evidence", row.get("row_id") or "", "row",
                    f"row {row.get('row_id')} item {item} has supporting and "
                    "opposing recorded evidence",
                    [f"{r['fact_id']} ({r['document_id']} p{r['page']})" for r in opposing],
                    [
                        {"document_id": r["document_id"], "page": r["page"]}
                        for r in opposing
                    ],
                )
            )
    rows_by_item: dict[str, list] = {}
    for row in rows:
        if row.get("kind") == "finding" and row.get("item"):
            rows_by_item.setdefault(str(row["item"]), []).append(row)
    for objection in _counterevidence_objections(data):
        if not isinstance(objection, dict):
            continue
        for row in rows_by_item.get(str(objection.get("item") or ""), []):
            ref = _fact_ref(data, str(objection.get("fact_id") or ""))
            defects.append(
                _defect(
                    "uncited_counterevidence", row.get("row_id") or "", "row",
                    f"row {row.get('row_id')} is asserted but does not cite "
                    f"opposing evidence {ref['fact_id']}",
                    [f"{ref['fact_id']} ({ref['document_id']} p{ref['page']})"],
                    [{"document_id": ref["document_id"], "page": ref["page"]}],
                    discriminator=ref["fact_id"],
                )
            )
    memo = data.get("memo") if isinstance(data.get("memo"), dict) else {}
    from verification.verify_memo import coverage_problems

    for problem in coverage_problems(data.get("checklist") or {}, memo.get("findings") or []):
        if problem.get("kind") != "uncovered_issue":
            continue
        defects.append(
            _defect(
                "uncovered_issue", str(problem.get("item") or ""), "issue",
                str(problem.get("detail") or ""),
                [str(problem.get("detail") or "")],
                [],
            )
        )
    gaps_rows = [r for r in rows if r.get("kind") == "gaps"]
    if gaps_rows:
        gaps_text = str(gaps_rows[0].get("assertion") or "")
    else:
        filed_gaps = memo.get("gaps") if isinstance(memo, dict) else ""
        if isinstance(filed_gaps, list):
            filed_gaps = "; ".join(str(g) for g in filed_gaps)
        gaps_text = str(filed_gaps or "")
    if not gaps_text.strip():
        checks = data.get("memo_checks") or []
        open_items = sorted(
            item_id
            for item_id, entry in (data.get("checklist") or {}).items()
            if effective_status(entry if isinstance(entry, dict) else {}, checks) == "open"
        )
        uncovered = [d["target"] for d in defects if d["category"] == "uncovered_issue"]
        if open_items or uncovered:
            defects.append(
                _defect(
                    "unstated_open_issues", "RG", "row",
                    "gaps text is empty while issues stay open: "
                    + ", ".join(sorted(set(open_items) | set(uncovered))),
                    sorted(set(open_items) | set(uncovered)),
                    [],
                )
            )
    ordered = {name: index for index, name in enumerate(DEFECT_CATEGORIES)}
    defects.sort(key=lambda d: (ordered.get(d["category"], 99), d["target"], d["key"]))
    return defects


def _check_current(row: dict, data: dict, layer: str, root=None) -> bool:
    """Recorded verdict still covers the row's current inputs."""
    from quality.records import evidence_fingerprint

    recorded = (row.get("checks") or {}).get(layer) or {}
    inputs = recorded.get("inputs") or {}
    if not inputs:
        return False
    if inputs.get("assertion_version") != row.get("assertion_version", 1):
        return False
    if inputs.get("citations_hash") != _stable(row.get("citations") or []):
        return False
    evidence = inputs.get("evidence")
    if evidence is not None and evidence != evidence_fingerprint(data):
        return False
    if layer == "structural" and root is not None:
        shas = _inbox_shas(root)
        for citation in row.get("citations") or []:
            if not isinstance(citation, dict):
                continue
            if citation.get("pin_sha") != shas.get(
                str(citation.get("document_id") or "")
            ):
                return False
    return True


def _defect_resolved(defect: dict, instance: dict, data: dict, root) -> str:
    """Why an open defect's condition vanished: repaired or unresolved.

    Repaired needs a positive current verdict (or the missing input
    supplied); anything unassessable — cleared, unavailable, stale,
    withdrawn evidence — is unresolved, never silently closed.
    """
    category = defect.get("category")
    target = defect.get("target")
    rows = {r.get("row_id"): r for r in instance.get("rows") or []}
    memo = data.get("memo") if isinstance(data.get("memo"), dict) else {}
    if defect.get("kind") == "issue":
        items = {
            f.get("item")
            for f in memo.get("findings") or []
            if isinstance(f, dict)
        }
        if target in items:
            return "repaired: a finding now covers this issue"
        if target not in (data.get("checklist") or {}):
            return "unresolved: the issue left the checklist; re-scope"
        return "unresolved: recheck to confirm"
    if category == "unstated_open_issues":
        filed_gaps = memo.get("gaps") if isinstance(memo, dict) else ""
        if isinstance(filed_gaps, list):
            filed_gaps = "; ".join(str(g) for g in filed_gaps)
        row = rows.get(target)
        current = (
            str(row.get("assertion") or "")
            if isinstance(row, dict)
            else str(filed_gaps or "")
        )
        if current.strip():
            return "repaired: gaps text now names the open work"
        checks = data.get("memo_checks") or []
        still_open = any(
            effective_status(e if isinstance(e, dict) else {}, checks) == "open"
            for e in (data.get("checklist") or {}).values()
        )
        if not still_open:
            return "repaired: no open issues remain"
        return "unresolved: gaps row is absent; amend the memo and reopen"
    row = rows.get(target)
    if not isinstance(row, dict):
        return "unresolved: target row is gone; recheck"
    if category in ("citation_failed", "citation_incomplete"):
        outcome = (row.get("checks") or {}).get("structural", {}).get("outcome")
        if outcome == "passed" and _check_current(row, data, "structural", root):
            return "repaired: citation check passes on current inputs"
        return "unresolved: citation check is not currently passing"
    if category in ("unsupported_claim", "unsupported_conclusion"):
        outcome = (row.get("checks") or {}).get("semantic", {}).get("outcome")
        if outcome == "passed" and _check_current(row, data, "semantic", root):
            return "repaired: support check passes on current inputs"
        return "unresolved: support is not currently established"
    if category == "contradictory_evidence":
        return "unresolved: evidence changed; contradiction needs reviewer judgment"
    if category == "uncited_counterevidence":
        fact_id = (defect.get("key") or "").split(":")[-1]
        if _fact_by_id(data, fact_id) is None:
            return "unresolved: the opposing fact was withdrawn"
        return "unresolved: recheck to confirm"
    return "unresolved: recheck to confirm"


def refresh_defects(instance: dict, data: dict, root: Path) -> list[str]:
    """Reconcile the stored defect ledger with current conditions.

    Pure over the instance dict (caller saves). New conditions open
    defects with stable never-recycled ids; vanished conditions
    become repaired (positive current verdict) or unresolved
    (unassessable); repaired conditions that return reopen with a
    regression note. Accepted defects stay accepted until a new
    revision supersedes the review. Returns transition summaries;
    an unchanged ledger writes nothing.
    """
    current = {d["key"]: d for d in synthesize_defects(instance, data, root)}
    stored = instance.setdefault("defects", [])
    if not isinstance(stored, list):
        instance["defects"] = stored = []
    seq = int(instance.get("defect_seq") or 0)
    events = []
    by_key = {}
    for defect in stored:
        if isinstance(defect, dict) and defect.get("key"):
            by_key.setdefault(defect["key"], defect)
    version = len(instance.get("versions") or [])
    for key in sorted(current):
        fresh = current[key]
        prior = by_key.get(key)
        if prior is None:
            seq += 1
            stored.append(
                {
                    "id": f"D{seq}",
                    **fresh,
                    "status": "open",
                    "first_seen_version": version,
                    "last_seen_version": version,
                    "disposition": None,
                    "history": [
                        {"at": now(), "change": "opened", "detail": fresh["rationale"]}
                    ],
                }
            )
            events.append(f"opened {fresh['category']} on {fresh['target']}")
        elif prior.get("status") == "repaired":
            prior["status"] = "open"
            prior["last_seen_version"] = version
            prior["rationale"] = fresh["rationale"]
            prior["evidence"] = fresh["evidence"]
            prior["sources"] = fresh["sources"]
            prior["history"].append(
                {"at": now(), "change": "regressed", "detail": fresh["rationale"]}
            )
            events.append(f"regressed {prior.get('id')} {fresh['category']} on {fresh['target']}")
        elif prior.get("status") in ("open", "unresolved"):
            prior["status"] = "open"
            prior["last_seen_version"] = version
            prior["rationale"] = fresh["rationale"]
            prior["evidence"] = fresh["evidence"]
            prior["sources"] = fresh["sources"]
    for defect in stored:
        if not isinstance(defect, dict):
            continue
        if defect.get("status") not in ("open", "unresolved"):
            continue
        if defect.get("key") in current:
            continue
        verdict = _defect_resolved(defect, instance, data, root)
        kind, _, detail = verdict.partition(": ")
        defect["status"] = "repaired" if kind == "repaired" else "unresolved"
        defect["history"].append({"at": now(), "change": defect["status"], "detail": detail})
        events.append(f"{defect['status']} {defect.get('id')} {defect.get('category')}")
    if events:
        instance["defect_seq"] = seq
        instance.setdefault("history", []).append(
            {"at": now(), "event": "defects_refreshed", "detail": "; ".join(events)}
        )
    return events


def _refresh_conclusion_citations(instance: dict, root: Path) -> bool:
    """Re-derive the conclusion's inherited citations from findings.

    The conclusion aggregates finding citations, so a finding
    revision re-derives that union: entries matching an existing
    conclusion citation keep their pins (source-change detection
    is preserved); new entries pin current hashes explicitly.
    Citations the reviewer added directly to the conclusion (not
    from any finding) are preserved, not silently dropped.
    Returns whether the conclusion changed.
    """
    conclusions = [
        r for r in instance.get("rows") or []
        if isinstance(r, dict) and r.get("kind") == "conclusion"
    ]
    if not conclusions:
        return False
    shas = _inbox_shas(root)
    union = []
    for row in instance.get("rows") or []:
        if not isinstance(row, dict) or row.get("kind") != "finding":
            continue
        for citation in row.get("citations") or []:
            if isinstance(citation, dict):
                union.append(citation)
    changed = False
    for conclusion in conclusions:
        existing = {
            (
                str(c.get("document_id") or ""),
                c.get("page"),
                str(c.get("quote") or ""),
            ): c.get("pin_sha")
            for c in conclusion.get("citations") or []
            if isinstance(c, dict)
        }
        def _entry(citation: dict) -> dict:
            triple = (
                str(citation.get("document_id") or ""),
                citation.get("page"),
                str(citation.get("quote") or ""),
            )
            return {
                "document_id": triple[0],
                "page": triple[1],
                "quote": triple[2],
                "pin_sha": existing.get(
                    triple, shas.get(str(citation.get("document_id") or ""))
                ),
                "inherited": True,
            }

        rebuilt = [_entry(c) for c in union]
        for citation in conclusion.get("citations") or []:
            if isinstance(citation, dict) and citation.get("inherited") is False:
                rebuilt.append(dict(citation))
        if rebuilt != (conclusion.get("citations") or []):
            conclusion["citations"] = rebuilt
            conclusion["history"].append(
                {"at": now(), "change": "inherited-citations", "detail": ""}
            )
            changed = True
    return changed


def revise_row(
    instance: dict,
    data: dict,
    root: Path,
    row_id: str,
    assertion: str | None = None,
    citation: dict | None = None,
    index: int = 0,
    feedback: str = "",
) -> dict:
    """Revise one row's assertion or citation (pure; caller saves).

    A revised citation re-pins the source's current hash — the new
    pin is explicit, not inherited. Every value change appends an
    immutable version snapshot; feedback alone is annotation.
    """
    row = next(
        (r for r in instance.get("rows") or [] if r.get("row_id") == row_id), None
    )
    if row is None:
        raise CaseError(f"unknown audit row {row_id!r}")
    if feedback and str(feedback).strip():
        row["feedback"].append({"at": now(), "text": str(feedback)})
        row["history"].append({"at": now(), "change": "feedback", "detail": ""})
    changed = ""
    if assertion is not None and str(assertion) != row.get("assertion"):
        row["assertion"] = str(assertion)
        row["assertion_version"] = int(row.get("assertion_version", 1)) + 1
        row["history"].append(
            {"at": now(), "change": "revised-assertion", "detail": ""}
        )
        changed = "assertion"
    if citation is not None:
        citations = row.get("citations") or []
        if not (0 <= index < len(citations)):
            raise CaseError(f"row {row_id} has no citation {index}")
        shas = _inbox_shas(root)
        target = citations[index]
        for key in ("document_id", "page", "quote"):
            if key in citation:
                target[key] = citation[key]
        target["pin_sha"] = shas.get(str(target.get("document_id")))
        if row.get("kind") == "conclusion":
            target["inherited"] = False
        row["history"].append(
            {"at": now(), "change": "revised-citation", "detail": f"citation {index}"}
        )
        changed = f"citation {index}" if not changed else f"{changed} + citation {index}"
        if row.get("kind") == "finding":
            _refresh_conclusion_citations(instance, root)
    if changed:
        versions = instance.setdefault("versions", [])
        versions.append(
            {
                "v": len(versions) + 1,
                "at": now(),
                "reason": f"revised {row_id} {changed}",
                "rows": copy.deepcopy(instance["rows"]),
            }
        )
        instance["history"].append(
            {"at": now(), "event": "revised", "detail": f"{row_id} {changed}"}
        )
        instance["review"] = {
            "decision": "pending",
            "notes": "",
            "qualifications": [],
            "snapshot": None,
            "at": None,
        }
        for defect in instance.get("defects") or []:
            if isinstance(defect, dict) and defect.get("status") == "accepted":
                defect["status"] = "open"
                defect["disposition"] = None
                defect["history"].append(
                    {
                        "at": now(),
                        "change": "reopened",
                        "detail": "a new revision supersedes the prior acceptance",
                    }
                )
    return row


MARK_CATEGORIES = ("wrong", "incomplete", "irrelevant")


def mark_citation(
    instance: dict,
    row_id: str,
    index: int,
    category: str,
    note: str = "",
) -> dict:
    """Mark one row citation as wrong/incomplete/irrelevant (pure; caller saves).

    A structured reviewer marking over a retrieved result: which
    citation, what is wrong with it, and an optional note. Like
    feedback, a mark is annotation — no new version, no review
    reset. Recheck via the normal checker afterwards.
    """
    row = next(
        (r for r in instance.get("rows") or [] if r.get("row_id") == row_id), None
    )
    if row is None:
        raise CaseError(f"unknown audit row {row_id!r}")
    citations = row.get("citations") or []
    if not isinstance(index, int) or not (0 <= index < len(citations)):
        raise CaseError(f"row {row_id} has no citation {index}")
    if category not in MARK_CATEGORIES:
        raise CaseError(
            f"category must be one of {', '.join(MARK_CATEGORIES)}")
    target = citations[index]
    row.setdefault("marks", []).append(
        {
            "at": now(),
            "index": index,
            "document_id": target.get("document_id"),
            "page": target.get("page"),
            "category": category,
            "note": str(note or ""),
        }
    )
    row.setdefault("history", []).append(
        {"at": now(), "change": "marked",
         "detail": f"citation {index} {category}"}
    )
    return row


def add_note(instance: dict, text: str, author: str = "") -> dict:
    """Append one freehand note (pure; caller saves).

    A lawyer's hand note on the whole artifact: anything worth
    remembering that fits no row, check, or review field. Like
    feedback, a note is annotation — no new version, no review
    reset. Blank notes are refused.
    """
    if not str(text or "").strip():
        raise CaseError("note text is required")
    notes = instance.setdefault("notes", [])
    entry = {
        "id": f"N{len(notes) + 1}",
        "text": str(text).strip(),
        "author": str(author or "").strip(),
        "at": now(),
    }
    notes.append(entry)
    return entry


_HANDLERS: dict[str, dict] = {}


def register(template_id: str, **handlers: Any) -> None:
    """One template's handlers (snapshot, diff, ...); called on slice import."""
    _HANDLERS[template_id] = handlers


def handlers(template_id: str) -> dict:
    """A template's registered handlers; unknown templates fail closed."""
    try:
        return _HANDLERS[template_id]
    except KeyError:
        raise CaseError(f"unknown template {template_id!r}") from None


def content_count(instance: dict) -> int:
    """Primary row count for list views (first content key per template)."""
    template = instance.get("template") or ""
    if template in _HANDLERS:
        keys = [_HANDLERS[template].get("count_key") or "rows"]
    else:
        keys = [h.get("count_key") or "rows" for h in _HANDLERS.values()]
    for key in keys:
        rows = instance.get(key)
        if rows:
            return len(rows)
    return 0


def current_snapshot(instance: dict, data: dict) -> dict:
    """Content + verdict hash a review binds (recomputed, never stored)."""
    from quality.records import evidence_fingerprint

    template = instance.get("template")
    if template != TEMPLATE_AUDIT:
        snapshot = _HANDLERS.get(template or "", {}).get("snapshot")
        if snapshot is not None:
            return snapshot(instance, data)
    rows = instance.get("rows") or []
    return {
        "content": _stable(
            [
                {
                    "row_id": row.get("row_id"),
                    "assertion": row.get("assertion"),
                    "assertion_version": row.get("assertion_version"),
                    "citations": row.get("citations"),
                }
                for row in rows
            ]
        ),
        "checks": _stable(
            {
                row.get("row_id"): {
                    layer: {
                        "outcome": (row.get("checks") or {}).get(layer, {}).get("outcome"),
                        "problems": (row.get("checks") or {})
                        .get(layer, {})
                        .get("problems"),
                    }
                    for layer in ("structural", "semantic")
                }
                for row in rows
            }
        ),
        "evidence": evidence_fingerprint(data),
        # The defect SET, not its statuses: dispositions are the
        # review's output, so binding them would invalidate the very
        # review that records them. Status transitions that matter
        # (repaired, regressed) always move the checks digest too.
        "defects": _stable(
            [
                {
                    "id": d.get("id"),
                    "category": d.get("category"),
                    "target": d.get("target"),
                }
                for d in instance.get("defects") or []
                if isinstance(d, dict)
            ]
        ),
        "template_version": instance.get("template_version"),
    }


def review_instance(
    instance: dict,
    data: dict,
    decision: str,
    notes: str,
    qualifications: list,
    snapshot: dict,
) -> dict:
    """Record a review decision bound to the inspected snapshot.

    Refused when the presented snapshot differs from the current
    one: a reviewer cannot approve a version they did not inspect.
    """
    if decision not in ("approved", "changes_requested"):
        raise CaseError("decision must be approved or changes_requested")
    current = current_snapshot(instance, data)
    if snapshot != current:
        raise CaseError(
            "the artifact changed since this snapshot was inspected; "
            "review the current version instead"
        )
    instance["review"] = {
        "decision": decision,
        "notes": str(notes or ""),
        "qualifications": [str(q) for q in qualifications or [] if str(q).strip()],
        "snapshot": current,
        "at": now(),
    }
    if decision == "approved" and instance.get("template") == TEMPLATE_AUDIT:
        for defect in instance.get("defects") or []:
            if not isinstance(defect, dict):
                continue
            if defect.get("status") not in ("open", "unresolved"):
                continue
            defect["status"] = "accepted"
            defect["disposition"] = {
                "decision": "accepted",
                "at": instance["review"]["at"],
                "qualifications": list(instance["review"]["qualifications"]),
            }
            defect["history"].append(
                {
                    "at": now(),
                    "change": "accepted",
                    "detail": "reviewer accepted the risk under the recorded qualifications",
                }
            )
    versions = instance.setdefault("versions", [])
    entry: dict[str, Any] = {
        "v": len(versions) + 1,
        "at": now(),
        "reason": f"reviewed: {decision}",
    }
    keys = _HANDLERS.get(instance.get("template") or "", {}).get(
        "review_keys", ("rows",)
    )
    for key in keys:
        entry[key] = copy.deepcopy(instance.get(key))
    versions.append(entry)
    instance["history"].append(
        {"at": now(), "event": "reviewed", "detail": decision}
    )
    return instance["review"]


def diff_versions(instance: dict, old: int, new: int) -> dict:
    """Before/after row comparison between two version snapshots."""
    versions = {v.get("v"): v for v in instance.get("versions") or []}
    if old not in versions or new not in versions:
        raise CaseError(f"versions {old}/{new} are not in this instance")
    before = {r.get("row_id"): r for r in versions[old].get("rows") or []}
    after = {r.get("row_id"): r for r in versions[new].get("rows") or []}
    changes = []
    for row_id in sorted(set(before) | set(after)):
        if row_id not in before:
            changes.append({"row_id": row_id, "change": "added"})
            continue
        if row_id not in after:
            changes.append({"row_id": row_id, "change": "removed"})
            continue
        first, second = before[row_id], after[row_id]
        if first.get("assertion") != second.get("assertion"):
            changes.append(
                {
                    "row_id": row_id,
                    "change": "assertion",
                    "before": first.get("assertion"),
                    "after": second.get("assertion"),
                }
            )
        if first.get("citations") != second.get("citations"):
            changes.append(
                {
                    "row_id": row_id,
                    "change": "citations",
                    "before": first.get("citations"),
                    "after": second.get("citations"),
                }
            )
        for layer in ("structural", "semantic"):
            was = (first.get("checks") or {}).get(layer, {}).get("outcome")
            is_now = (second.get("checks") or {}).get(layer, {}).get("outcome")
            if was != is_now:
                changes.append(
                    {
                        "row_id": row_id,
                        "change": f"{layer} {was or 'never checked'} → {is_now or 'never checked'}",
                    }
                )
    return {"old": old, "new": new, "changes": changes}


def quality_report(instance: dict, data: dict, root: Path) -> dict:
    """Work-product quality: checks, scope, unresolved, freshness, review.

    Read-only roll-up of already-recorded state — never a
    system-accuracy score. Open objections, failed/stale rows,
    stored open defects, non-pending label states, and the
    current review decision with its snapshot currency.
    """
    rows = [row for row in instance.get("rows") or []
            if isinstance(row, dict)]
    failed: list[str] = []
    stale: list[str] = []
    checked = 0
    for row in rows:
        checks = row.get("checks") or {}
        if not checks:
            continue
        checked += 1
        if any((checks.get(layer) or {}).get("outcome") == "failed"
               for layer in ("structural", "semantic")):
            failed.append(str(row.get("row_id")))
        flag = row_staleness(root, data, row)
        if flag.get("structural") or flag.get("semantic"):
            stale.append(str(row.get("row_id")))
    objections = [
        str(entry.get("id")) for entry in data.get("objections") or []
        if isinstance(entry, dict) and entry.get("status") == "open"
    ]
    defects = [
        str(entry.get("id")) for entry in instance.get("defects") or []
        if isinstance(entry, dict) and entry.get("status") == "open"
    ]
    states = {}
    for row in rows:
        state = label_status(instance, str(row.get("row_id")))["state"]
        if state != "pending":
            states[str(row.get("row_id"))] = state
    review = instance.get("review") or {}
    snapshot = review.get("snapshot")
    return {
        "rows": len(rows),
        "checked": checked,
        "failed_rows": failed,
        "stale_rows": stale,
        "open_objections": objections,
        "defects_open": defects,
        "label_states": states,
        "review": {
            "decision": review.get("decision", "pending"),
            "qualifications": list(review.get("qualifications") or []),
            "current": (snapshot == current_snapshot(instance, data)
                        if snapshot else None),
        },
    }


def record_eval_receipt(instance: dict, receipt: dict) -> dict:
    """Attach one evaluated-configuration receipt (trusted tooling).

    A receipt names the suite, client, and configuration measured;
    it never invents results. Absent receipts mean the work has
    no evaluated configuration — shown explicitly, never as a
    passing score.
    """
    if not isinstance(receipt, dict):
        raise CaseError("eval receipt must be a mapping")
    suite = str(receipt.get("suite") or "").strip()
    client = str(receipt.get("client") or "").strip()
    if not suite or not client:
        raise CaseError("eval receipt needs a suite and a client")
    entry = {
        "suite": suite,
        "client": client,
        "config": dict(receipt.get("config") or {}),
        "limits": str(receipt.get("limits") or ""),
        "summary": str(receipt.get("summary") or ""),
        "at": now(),
    }
    instance.setdefault("eval_receipts", []).append(entry)
    return entry


def _eval_version(instance: dict) -> dict | None:
    receipts = instance.get("eval_receipts") or []
    if not receipts:
        return None
    return receipts[-1]


def instance_status(instance: dict, data: dict, root: Path) -> dict:
    """Headline counts per check layer plus review state (never merged).

    Callers must render each layer separately: a passed citation
    layer with a failed support layer is not an approved row.
    """
    counts = {
        "structural": {},
        "semantic": {},
        "stale": 0,
        "contested": 0,
        "unchecked": 0,
    }
    for row in instance.get("rows") or []:
        checks = row.get("checks") or {}
        if not checks:
            counts["unchecked"] += 1
            continue
        for layer in ("structural", "semantic"):
            outcome = checks.get(layer, {}).get("outcome", "not_run")
            layer_counts = counts[layer]
            layer_counts[outcome] = layer_counts.get(outcome, 0) + 1
        stale = row_staleness(root, data, row)
        if stale.get("structural") or stale.get("semantic"):
            counts["stale"] += 1
        if (row.get("contested") or {}).get("contested"):
            counts["contested"] += 1
    review = instance.get("review") or {}
    return {
        "rows": len(instance.get("rows") or []),
        "counts": counts,
        "adversarial": adversarial_summary(data),
        "panel": panel_export(data),
        "quality": quality_report(instance, data, root),
        "eval_version": _eval_version(instance),
        "review": review.get("decision", "pending"),
        "review_current": review_is_current(
            instance, data, root, stale_rows=counts["stale"])
        if review.get("snapshot")
        else None,
    }


def review_is_current(
    instance: dict, data: dict, root: Path, stale_rows: int | None = None
) -> bool:
    """One approval-currency rule for status, export, and UI.

    An approval binds the inspected snapshot AND live inputs: a
    changed source, new evidence, or revoked access stales the
    approval even when the recorded snapshot still matches.
    Callers that already counted stale rows pass the count in.
    """
    if stale_rows is None:
        stale_rows = sum(
            1 for row in instance.get("rows") or []
            if (row_staleness(root, data, row).get("structural")
                or row_staleness(root, data, row).get("semantic"))
        )
    return (
        instance.get("review", {}).get("snapshot")
        == current_snapshot(instance, data)
        and not stale_rows
    )


def _excerpt(root: Path, document_id: str, page: Any, quote: str) -> str:
    try:
        page_no = int(page)
    except (TypeError, ValueError):
        return ""
    try:
        text = document_text.page_text(root, document_id, page_no)
    except document_text.DocumentError:
        return ""
    if not text:
        return ""
    at = text.lower().find((quote or "").strip().lower()[:60])
    if at < 0:
        at = 0
    start = max(0, at - 100)
    end = min(len(text), at + 200)
    passage = text[start:end].strip()
    if start > 0:
        passage = "… " + passage
    if end < len(text):
        passage = passage + " …"
    return passage


def export_instance(
    instance: dict, data: dict, root: Path, version: int | None = None
) -> dict:
    """Versioned report: rows, provenance, limitations, approval scope.

    Applies the same scope filter as the interactive view: quotes
    and passages from privileged sources are redacted, never
    leaked. Historical versions export their frozen rows with a
    note that liveness was not recomputed.
    """
    from quality.records import evidence_fingerprint

    versions = instance.get("versions") or []
    if version is None:
        rows = instance.get("rows") or []
        historical = False
    else:
        match = next((v for v in versions if v.get("v") == version), None)
        if match is None:
            raise CaseError(f"version {version} is not in this instance")
        rows = match.get("rows") or []
        historical = True
    shas = _inbox_shas(root)
    out_rows = []
    for row in rows:
        citations = []
        for citation in row.get("citations") or []:
            document_id = str(citation.get("document_id") or "")
            citations.append(
                {
                    "document_id": document_id,
                    "page": citation.get("page"),
                    "quote": citation.get("quote"),
                    "pin_sha": (citation.get("pin_sha") or "")[:12],
                    "current_sha": (shas.get(document_id) or "")[:12],
                    "passage": _excerpt(root, document_id, citation.get("page"), str(citation.get("quote") or "")),
                }
            )
        citations = scope_citations(citations, data, root)
        contested = contested_info(data, str(row.get("item") or ""), root) if not historical else row.get("contested", {})
        if contested.get("opposing"):
            # Frozen historical rows predate the root-aware seal;
            # re-apply the current scope filter either way.
            cleaned = scope_citations(
                list(contested["opposing"]), data, root)
            contested = {**contested, "opposing": cleaned}
        checks = row.get("checks") or {}
        limitations = []
        for layer in ("structural", "semantic"):
            outcome = checks.get(layer, {}).get("outcome")
            if outcome in ("failed", "incomplete", "unavailable", "not_run"):
                limitations.append(
                    f"{layer}: {outcome}"
                    + (
                        f" ({'; '.join(checks[layer].get('problems') or [])})"
                        if checks.get(layer, {}).get("problems")
                        else ""
                    )
                )
        stale = {} if historical else row_staleness(root, data, row)
        if stale.get("structural") or stale.get("semantic"):
            limitations.append(f"stale: {'; '.join(stale.get('reasons') or [])}")
        out_rows.append(
            {
                "row_id": row.get("row_id"),
                "kind": row.get("kind"),
                "item": row.get("item"),
                "assertion": row.get("assertion"),
                "assertion_version": row.get("assertion_version"),
                "citations": citations,
                "fact_ids": row.get("fact_ids"),
                "structural": checks.get("structural"),
                "semantic": checks.get("semantic"),
                "contested": contested,
                "limitations": limitations,
                "feedback": row.get("feedback"),
                "marks": row.get("marks"),
            }
        )
    review = instance.get("review") or {}
    return {
        "template": instance.get("template"),
        "template_version": instance.get("template_version"),
        "instance": instance.get("id"),
        "matter": instance.get("matter"),
        "data_mode": data_mode(instance),
        "title": instance.get("title"),
        "exported_version": version or (versions[-1].get("v") if versions else 1),
        "historical": historical,
        "evidence_fingerprint": evidence_fingerprint(data) if not historical else None,
        "rows": out_rows,
        "defects": [
            {k: v for k, v in d.items() if k != "key"}
            for d in instance.get("defects") or []
            if isinstance(d, dict)
        ]
        if not historical
        else [],
        "adversarial": adversarial_summary(data) if not historical else None,
        "panel": panel_export(data) if not historical else None,
        "quality": quality_report(instance, data, root)
        if not historical else None,
        "collaboration": collaboration_export(data)
        if not historical else None,
        "eval_version": _eval_version(instance)
        if not historical else None,
        "review": {
            "decision": review.get("decision"),
            "notes": review.get("notes"),
            "qualifications": review.get("qualifications"),
            "at": review.get("at"),
            "snapshot": review.get("snapshot"),
            # Historical rows are frozen, so their liveness is
            # unknown, never asserted current.
            "current": review_is_current(instance, data, root)
            if (review.get("snapshot") and not historical)
            else None,
            "scope": (
                "the reviewed snapshot only; later revisions need a new review"
                if review.get("decision") == "approved"
                else "no approval recorded"
            ),
        },
        "limits": [
            "quotations are checked against saved extraction text, not the original page image",
            "a matching quote never establishes that an interpretation is supported",
            "unavailable checks are not passes; stale checks do not cover current inputs",
        ]
        + (
            [
                "historical version: liveness was not recomputed",
                "historical version: the defect ledger is current-state only; "
                "see the live instance",
            ]
            if historical
            else []
        ),
    }


# Markup-breaking characters only: -, +, ., ! are literal mid-line
# (list markers cannot start inside our prefixed lines), so they
# stay readable. Brackets are escaped, which also breaks links/images.
_MD_SPECIAL = ("\\", "`", "*", "_", "{", "}", "[", "]", "(", ")", "#", "|", "<", ">")

# Trusted scope-filter marker: emitted verbatim so it stays greppable
# in both formats; only surrounding untrusted text is escaped.
_MD_LITERAL = "[redacted: privileged]"


def _escape_raw(text: str) -> str:
    for char in _MD_SPECIAL:
        text = text.replace(char, "\\" + char)
    return text


def _escape_keep_marker(text: str) -> str:
    out: list[str] = []
    rest = text
    while rest:
        hits = [
            (rest.find(marker), marker)
            for marker in (_MD_LITERAL, _MD_SEALED)
        ]
        hits = [(pos, marker) for pos, marker in hits if pos >= 0]
        if not hits:
            out.append(_escape_raw(rest))
            break
        pos, marker = min(hits)
        out.append(_escape_raw(rest[:pos]))
        out.append(marker)
        rest = rest[pos + len(marker):]
    return "".join(out)


def _md_code(text: object) -> str:
    """System value for a code span: backticks cannot survive in one.

    Callers pass generated/validated values only (ids, shas, fixed
    outcomes); backslash escapes would render literally here, so
    backticks are stripped instead of escaped.
    """
    return " ".join(("" if text is None else str(text)).replace("`", "").split())


def _md_inline(text: object) -> str:
    """One line of untrusted text, safe for Markdown headings, cells, and bullets."""
    return _escape_keep_marker(" ".join(("" if text is None else str(text)).split()))


def _md_quote(text: object) -> list[str]:
    """Untrusted passage as Markdown blockquote lines (blank-safe)."""
    lines = ("" if text is None else str(text)).splitlines() or [""]
    out = []
    for line in lines:
        escaped = _escape_keep_marker(line.strip())
        out.append(f"> {escaped}" if escaped else ">")
    return out


def export_markdown(report: dict) -> str:
    """Readable Markdown rendering of one redacted audit export.

    Takes the JSON report dict (already scope-filtered and
    redacted) so both formats share one redaction source; it
    never reads case records itself. Raises CaseError for any
    other template's report shape.
    """
    if not isinstance(report, dict) or report.get("template") != TEMPLATE_AUDIT:
        raise CaseError("markdown export needs a citation-audit report")
    mode = report.get("data_mode") or "matter-workspace"
    lines = [
        f"# {_md_inline(report.get('title') or 'Citation & Claim Audit')}",
        "",
    ]
    if mode == "synthetic-demo":
        lines += [f"> {DEMO_MATTER_NOTE}.", ""]
    lines += [
        f"- template: `{_md_code(report.get('template'))}` v{_md_inline(report.get('template_version'))}",
        f"- instance: `{_md_code(report.get('instance'))}` · matter: `{_md_code(report.get('matter'))}`",
        f"- data mode: `{_md_code(mode)}`",
        f"- exported version: {_md_inline(report.get('exported_version'))}"
        + (" (historical: liveness was not recomputed)" if report.get("historical") else ""),
        f"- evidence fingerprint: `{_md_code(report.get('evidence_fingerprint') or 'n/a')}`",
        "",
        "## Review",
        "",
    ]
    review = report.get("review") or {}
    lines += [
        f"- decision: {_md_inline(review.get('decision') or 'pending')}",
        f"- scope: {_md_inline(review.get('scope') or '')}",
        f"- qualifications: {_md_inline('; '.join(review.get('qualifications') or []) or 'none')}",
        f"- notes: {_md_inline(review.get('notes') or 'none')}",
        f"- at: {_md_inline(review.get('at') or 'n/a')}",
        "",
        "## Defects",
        "",
    ]
    defects = report.get("defects") or []
    if not defects:
        lines += ["- none open or recorded", ""]
    for defect in defects:
        if not isinstance(defect, dict):
            continue
        lines += [
            f"### {_md_code(defect.get('id'))} · {_md_inline(defect.get('category'))} "
            f"· {_md_inline(defect.get('severity'))} · `{_md_code(defect.get('target'))}`",
            "",
            f"- status: {_md_inline(defect.get('status'))}",
            f"- rationale: {_md_inline(defect.get('rationale'))}",
            f"- action: {_md_inline(defect.get('action'))}",
        ]
        for bit in defect.get("evidence") or []:
            lines.append(f"- evidence: {_md_inline(bit)}")
        disposition = defect.get("disposition") or {}
        if isinstance(disposition, dict) and disposition.get("decision"):
            lines.append(
                f"- disposition: {_md_inline(disposition.get('decision'))} "
                f"({_md_inline('; '.join(disposition.get('qualifications') or []))})"
            )
        lines.append("")
    challenge = report.get("adversarial") or {}
    lines += ["## Adversarial review", ""]
    if challenge.get("id"):
        lines += [
            f"- latest: `{_md_code(challenge.get('id'))}`"
            + (f" · lens {_md_inline(challenge.get('lens'))}" if challenge.get("lens") else "")
            + f" · {_md_inline(challenge.get('n'))} recorded",
            f"- revision needed: {_md_inline(challenge.get('revision_needed'))}",
            f"- strongest against: {_md_inline(challenge.get('strongest_against') or 'n/a')}",
            f"- alternative theory: {_md_inline(challenge.get('alternative_theory') or 'n/a')}",
            f"- reversing fact: {_md_inline(challenge.get('reversing_fact') or 'n/a')}",
            "",
        ]
    else:
        lines += ["- none recorded", ""]
    panel = report.get("panel") or {}
    lines += ["## Review panel", ""]
    if panel:
        counts = (panel.get("objections") or {}).get("counts") or {}
        lines += [
            f"- reviews: {_md_inline(panel.get('reviews'))}"
            f" · lenses {_md_inline(', '.join(panel.get('lenses') or []))}",
            f"- objections open: {_md_inline(counts.get('open'))}"
            f" · disproven: {_md_inline(counts.get('disproven'))}",
            "",
        ]
        for entry in (panel.get("objections") or {}).get("open") or []:
            lines.append(
                f"- open `{_md_code(entry.get('id'))}`: "
                f"{_md_inline(entry.get('objection') or 'n/a')}")
        if ((panel.get("objections") or {}).get("open")):
            lines.append("")
    else:
        lines += ["- none recorded", ""]
    quality = report.get("quality") or {}
    lines += ["## Quality", ""]
    if quality:
        failed = quality.get("failed_rows") or []
        stale = quality.get("stale_rows") or []
        states = quality.get("label_states") or {}
        review = quality.get("review") or {}
        lines += [
            f"- checks: {_md_inline(quality.get('checked'))} of "
            f"{_md_inline(quality.get('rows'))} rows checked",
            f"- failed rows: "
            f"{_md_inline(', '.join(failed)) if failed else 'none'}",
            f"- stale rows: "
            f"{_md_inline(', '.join(stale)) if stale else 'none'}",
            f"- open objections: "
            f"{_md_inline(len(quality.get('open_objections') or []))}"
            f" · open defects: "
            f"{_md_inline(len(quality.get('defects_open') or []))}",
            f"- labels: "
            f"{_md_inline(', '.join(f'{k} {v}' for k, v in states.items())) if states else 'none'}",
            f"- review: {_md_inline(review.get('decision'))}",
            "",
        ]
    else:
        lines += ["- not computed for this export", ""]
    evaluated = report.get("eval_version") or {}
    if evaluated:
        lines += [
            f"- evaluated: {_md_inline(evaluated.get('suite'))} "
            f"({_md_inline(evaluated.get('client'))})",
            "",
        ]
    else:
        lines += ["- evaluated configuration: none attached", ""]
    collab = report.get("collaboration") or {}
    lines += ["## Collaboration", ""]
    if collab:
        drafts = collab.get("drafts") or []
        threads = collab.get("threads") or []
        directives = collab.get("directives") or []
        context = collab.get("context") or []
        lines += [
            f"- drafts: {_md_inline(len(drafts))}"
            f" · threads: {_md_inline(len(threads))}"
            f" · directives: {_md_inline(len(directives))}"
            f" · context: {_md_inline(len(context))}"
            f" · edges: {_md_inline(len(collab.get('edges') or []))}",
            "",
        ]
        for draft in drafts:
            if not isinstance(draft, dict):
                continue
            lines.append(
                f"- draft `{_md_code(draft.get('id'))}` "
                f"v{_md_inline(draft.get('version'))} "
                f"{_md_inline(draft.get('state'))} / "
                f"{_md_inline(draft.get('verification_status'))}: "
                f"{_md_inline(draft.get('title') or 'untitled')}"
            )
        for thread in threads:
            if not isinstance(thread, dict):
                continue
            flags = []
            if thread.get("version_current") is False:
                flags.append("old version")
            if thread.get("anchor_match") is False:
                flags.append("anchor drifted")
            lines.append(
                f"- thread `{_md_code(thread.get('id'))}` "
                f"({_md_inline(thread.get('state'))}) on "
                f"`{_md_code(thread.get('object_id'))}` · "
                f"{_md_inline(len(thread.get('messages') or []))} messages"
                + (f" [{'; '.join(flags)}]" if flags else "")
            )
        for directive in directives:
            if not isinstance(directive, dict):
                continue
            lines.append(
                f"- directive `{_md_code(directive.get('id'))}` "
                f"{_md_inline(directive.get('kind'))} "
                f"({_md_inline(directive.get('state'))}): "
                f"{_md_inline((directive.get('body_md') or '')[:160])}"
            )
        for item in context:
            if not isinstance(item, dict):
                continue
            lines.append(
                f"- context `{_md_code(item.get('id'))}` "
                f"{_md_inline(item.get('source'))} "
                f"({_md_inline(item.get('state'))})"
            )
        lines.append("")
    else:
        lines += ["- none recorded", ""]
    lines += ["## Limitations", ""]
    limits = report.get("limits") or []
    lines += [(f"- {_md_inline(limit)}" if limit else "- (none)") for limit in limits] or ["- (none)"]
    lines += ["", "## Rows", ""]
    for row in report.get("rows") or []:
        if not isinstance(row, dict):
            continue
        structural = (row.get("structural") or {}).get("outcome", "not_run")
        semantic = (row.get("semantic") or {}).get("outcome", "not_run")
        lines += [
            f"### {_md_inline(row.get('row_id'))} · {_md_inline(row.get('kind'))}",
            "",
            f"**Assertion (v{_md_inline(row.get('assertion_version') or '?')}):** "
            f"{_md_inline(row.get('assertion'))}",
            "",
            f"- quote check: `{_md_code(structural)}` · support: `{_md_code(semantic)}`",
        ]
        row_limits = row.get("limitations") or []
        for limit in row_limits:
            lines.append(f"- limitation: {_md_inline(limit)}")
        citations = row.get("citations") or []
        if citations:
            lines += ["", "**Citations:**", ""]
            for citation in citations:
                if not isinstance(citation, dict):
                    continue
                lines.append(
                    f"- `{_md_code(citation.get('document_id'))}` p{_md_inline(citation.get('page'))} "
                    f"(pinned `{_md_code(citation.get('pin_sha'))}`, current "
                    f"`{_md_code(citation.get('current_sha'))}`)"
                )
                lines += _md_quote(citation.get("quote"))
        feedback = row.get("feedback")
        if feedback:
            lines += ["", f"Reviewer feedback: {_md_inline(feedback)}"]
        marks = [m for m in row.get("marks") or [] if isinstance(m, dict)]
        for mark in marks:
            lines.append(
                f"- marked `{_md_code(mark.get('category'))}` "
                f"(citation {_md_inline(mark.get('index'))}"
                f"{': ' + _md_inline(mark.get('note')) if mark.get('note') else ''})")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# The audit binds the filed memo and the case root, so it keeps its
# explicit open path in case_record; every other template opens
# through the registry with (data, run_id, title, source_id).
register(
    TEMPLATE_AUDIT,
    diff=diff_versions,
    export=export_instance,
    export_takes_root=True,
    review_keys=("rows",),
    count_key="rows",
)



__all__ = ['DEMO_RUN_ID', 'data_mode', 'export_markdown', 'DEFECT_CATEGORIES', 'DEFECT_SEVERITY', 'DEFECT_ACTION', 'synthesize_defects', 'refresh_defects', 'TEMPLATE_AUDIT', 'TEMPLATE_AUDIT_VERSION', 'TEMPLATE_BOARD', 'TEMPLATE_BOARD_VERSION', 'TEMPLATE_JX', 'TEMPLATE_JX_VERSION', 'TEMPLATE_CT', 'TEMPLATE_CT_VERSION', 'TEMPLATE_PB', 'TEMPLATE_PB_VERSION', 'TEMPLATE_CAL', 'TEMPLATE_CAL_VERSION', 'TEMPLATE_IN', 'TEMPLATE_IN_VERSION', 'TEMPLATE_DL', 'TEMPLATE_DL_VERSION', 'TEMPLATE_DR', 'TEMPLATE_DR_VERSION', 'TEMPLATE_HZ', 'TEMPLATE_HZ_VERSION', 'TEMPLATE_GV', 'TEMPLATE_GV_VERSION', 'TEMPLATE_EC', 'TEMPLATE_EC_VERSION', 'TEMPLATE_TS', 'TEMPLATE_TS_VERSION', 'TEMPLATE_PT', 'TEMPLATE_PT_VERSION', 'TEMPLATE_QA', 'TEMPLATE_QA_VERSION', 'TEMPLATE_SP', 'TEMPLATE_SP_VERSION', 'TEMPLATES', 'PLANNED', 'gallery_cards', '_stable', '_inbox_shas', '_fact_by_id', '_row_citations', '_linked_facts', 'build_rows', '_next_instance_id', '_blank_review', 'open_instance', '_flagged', 'audit_citation', '_structural_outcome', 'audit_structural', '_adapted_memo', '_unit_row', 'eligible_rows', 'audit_support', 'contested_info', 'adversarial_summary', '_sealed_docs', 'scope_citations', 'mark_citation', 'MARK_CATEGORIES', '_current_inputs', 'row_staleness', 'record_checks', 'revise_row', 'current_snapshot', 'review_instance', 'review_is_current', 'diff_versions', 'quality_report', 'record_eval_receipt', 'add_note', 'instance_status', '_excerpt', 'export_instance', '_HANDLERS', 'register', 'handlers', 'content_count']
