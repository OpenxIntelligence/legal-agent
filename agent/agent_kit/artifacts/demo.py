"""synthetic demo matter (one artifact slice; split from artifacts.py, behavior unchanged)."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from agent_kit import document_text
from agent_kit.case_record import CaseError
from agent_kit.progress import now

from .kernel import (
    DEMO_RUN_ID,
    TEMPLATE_AUDIT,
    TEMPLATE_BOARD,
    TEMPLATE_CAL,
    TEMPLATE_CT,
    TEMPLATE_DL,
    TEMPLATE_DR,
    TEMPLATE_EC,
    TEMPLATE_GV,
    TEMPLATE_HZ,
    TEMPLATE_IN,
    TEMPLATE_JX,
    TEMPLATE_PB,
    TEMPLATE_PT,
    TEMPLATE_QA,
    TEMPLATE_SP,
    TEMPLATE_TS,
    TEMPLATE_AUDIT_VERSION,
    TEMPLATE_BOARD_VERSION,
    TEMPLATE_JX_VERSION,
    TEMPLATE_CT_VERSION,
    TEMPLATE_PB_VERSION,
    TEMPLATE_CAL_VERSION,
    TEMPLATE_IN_VERSION,
    TEMPLATE_DL_VERSION,
    TEMPLATE_DR_VERSION,
    TEMPLATE_HZ_VERSION,
    TEMPLATE_GV_VERSION,
    TEMPLATE_EC_VERSION,
    TEMPLATE_TS_VERSION,
    TEMPLATE_PT_VERSION,
    TEMPLATE_QA_VERSION,
    TEMPLATE_SP_VERSION,
    _blank_review,
    _excerpt,
    _flagged,
    _inbox_shas,
    _next_instance_id,
    _stable,
    _structural_outcome,
    audit_citation,
    register,
)
from .board import annotate_event, confirm_event, link_events
from .calendar import add_ob, resolve_ob
from .contract import add_clause, flag_clause, set_clause
from .deadlines import add_dl, resolve_dl
from .discovery import add_rq, decide_dr, respond_rq, serve_rq
from .entity import add_cf, add_en, decide_cf
from .governance import add_uc, decide_uc, set_uc
from .horizon import add_wh, decide_wh
from .intake import add_in, route_in, triage_in
from .matrix import add_cell, link_cell_branch, set_cell
from .patent import add_el, add_sj, decide_el, reading_el
from .playbook import add_rule, assess
from .qa import add_qa, answer_qa, decide_qa
from .spend import add_eg, add_iv, add_vn, decide_iv
from .termsheet import add_tm, add_ts, decide_tm, position_tm


DEMO_OBJECTIVE = (
    "DEMO (synthetic): audit a fictional readiness memo against "
    "fictional sources. No real matter, no live provider needed."
)
DEMO_DOCS: dict[str, list[str]] = {
    "DEMO-STATUS": [
        "Harborview rollout status. Overall status is yellow. "
        "Open Severity 1 defects: 4 at period end. "
        "Go-live is scheduled for March 15.",
        "Risks: the payments queue has not completed load testing. "
        "The review board meets March 1.",
    ],
    "DEMO-MINUTES": [
        "Steering minutes. Ms. Okafor confirmed the data migration "
        "finished on February 10. Mr. Lindgren confirmed subsystem "
        "load tests passed on February 18. Final sign-off is pending "
        "the payments queue."
    ],
    "DEMO-DRAFT": [
        "Draft note. Load testing passed on February 20 with no open defects."
    ],
    "DEMO-CONTRACT": [
        "Services agreement (fictional). Section 3, Acceptance. "
        "The client accepts each deliverable within ten business days of delivery. "
        "Silence is not acceptance. Section 5, Fees. "
        "Fees are due within thirty days of invoice. "
        "Late payment bears interest at one percent per month.",
        "Section 8, Liability. "
        "Neither party's liability exceeds the fees paid in the prior twelve months. "
        "Section 9, Termination. "
        "Either party may terminate with thirty days written notice. "
        "Termination does not excuse unpaid fees.",
    ],
}
DEMO_PLAN_V1 = (
    "Cutover plan revision A. The cutover window is March 14-15. "
    "Rollback requires board approval."
)
DEMO_PLAN_V2 = (
    "Cutover plan revision B. The cutover window is March 14-15. "
    "Rollback requires board approval and a filed notice."
)
DEMO_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
)


def _write_demo_doc(inbox: Path, doc_id: str, pages: list[str]) -> str:
    (inbox / f"{doc_id}.pdf").write_bytes(("\n".join(pages)).encode())
    pages_dir = inbox / "pages"
    pages_dir.mkdir(exist_ok=True)
    for number in range(1, len(pages) + 1):
        (pages_dir / f"{doc_id}-p{number}.png").write_bytes(
            DEMO_PNG + doc_id.encode() + bytes([number])
        )
    return hashlib.sha256((inbox / f"{doc_id}.pdf").read_bytes()).hexdigest()


def _seed_transcript(agent_root: Path, sha: str, pages: list[str]) -> None:
    cache_dir = agent_root / "workspace/cache/text"
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / f"{sha}.json").write_text(
        json.dumps(
            {"pages": [{"page": n + 1, "text": text} for n, text in enumerate(pages)]}
        )
    )


def build_demo(agent_root: Path) -> str:
    """Materialize the synthetic audit-demo matter (no model calls).

    Writes inbox documents with pre-seeded extraction transcripts,
    a checklist, recorded facts, one flagged source, a filed
    fictional memo, and a fresh audit instance. Idempotent: an
    existing demo run is returned unchanged, never rebuilt.
    """
    from agent_kit.case_record import CaseRecord

    agent_root = Path(agent_root)
    run_dir = agent_root / "workspace/runs" / DEMO_RUN_ID
    if run_dir.is_dir():
        return DEMO_RUN_ID
    inbox = agent_root / "workspace/inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    for doc_id, pages in DEMO_DOCS.items():
        sha = _write_demo_doc(inbox, doc_id, pages)
        _seed_transcript(agent_root, sha, pages)
    plan_v1_sha = _write_demo_doc(inbox, "DEMO-PLAN", [DEMO_PLAN_V1])
    _seed_transcript(agent_root, plan_v1_sha, [DEMO_PLAN_V1])
    _write_demo_doc(inbox, "DEMO-PLAN", [DEMO_PLAN_V2])
    plan_v2_sha = hashlib.sha256(
        (inbox / "DEMO-PLAN.pdf").read_bytes()
    ).hexdigest()
    _seed_transcript(agent_root, plan_v2_sha, [DEMO_PLAN_V2])

    run_dir.mkdir(parents=True)
    (run_dir / "instructions.md").write_text("DEMO: no agent instructions.")
    (run_dir / "objective.md").write_text(DEMO_OBJECTIVE)
    (run_dir / "progress.json").write_text(
        json.dumps(
            {
                "run_id": DEMO_RUN_ID,
                "verification": True,
                "model": "demo (no model calls)",
                "config": {
                    "config_version": 3,
                    "model": "demo (no model calls)",
                    "verification": True,
                    "run_token_budget": None,
                    "synthesis_reserve_tokens": 0,
                    "max_context_bytes": 0,
                    "errors": [],
                },
                "started": now(),
                "status": "answered",
                "turn": 0,
                "messages": [],
                "turns": [],
                "notes": {"demo": "synthetic audit demo matter"},
            }
        )
    )
    case = CaseRecord(agent_root, DEMO_RUN_ID)
    case.set_checklist(
        [
            {
                "id": "readiness",
                "question": "Was the rollout ready?",
                "evidence_needed": "status records",
            },
            {
                "id": "testing",
                "question": "Did load testing pass?",
                "evidence_needed": "test records",
            },
            {
                "id": "migration",
                "question": "Did the migration finish?",
                "evidence_needed": "migration records",
            },
            {
                "id": "cutover",
                "question": "When is the cutover window?",
                "evidence_needed": "cutover plan",
            },
        ]
    )
    case.mark_read(
        ["DEMO-STATUS", "DEMO-MINUTES", "DEMO-DRAFT", "DEMO-PLAN", "DEMO-CONTRACT"]
    )
    case.record_fact(
        "readiness",
        "DEMO-STATUS",
        1,
        "Open Severity 1 defects: 4 at period end",
        "four sev-1 defects open",
    )
    case.record_fact(
        "testing",
        "DEMO-DRAFT",
        1,
        "Load testing passed on February 20 with no open defects",
        "load testing passed",
    )
    case.record_fact(
        "testing",
        "DEMO-STATUS",
        2,
        "the payments queue has not completed load testing",
        "payments load testing incomplete",
        stance="opposes",
    )
    case.record_fact(
        "testing",
        "DEMO-MINUTES",
        1,
        "Mr. Lindgren confirmed subsystem load tests passed on February 18",
        "subsystem tests passed",
    )
    case.record_fact(
        "migration",
        "DEMO-MINUTES",
        1,
        "Ms. Okafor confirmed the data migration finished on February 10",
        "migration finished Feb 10",
    )
    case.record_fact(
        "cutover",
        "DEMO-PLAN",
        1,
        "The cutover window is March 14-15",
        "cutover mid-March",
    )
    case.record_fact(
        "readiness",
        "DEMO-STATUS",
        1,
        "Go-live is scheduled for March 15",
        "go-live mid-March",
    )
    case.record_fact(
        "readiness",
        "DEMO-STATUS",
        2,
        "The review board meets March 1",
        "board meets Mar 1",
    )
    case.flag_privileged("DEMO-DRAFT", "counsel restricted the draft after collection")
    memo = {
        "conclusion": (
            "The rollout was unconditionally ready and every test passed."
        ),
        "findings": [
            {
                "item": "readiness",
                "summary": "Four Severity 1 defects were open at period end.",
                "status": "asserted",
                "citations": [
                    {
                        "document_id": "DEMO-STATUS",
                        "page": 1,
                        "quote": "Open Severity 1 defects: 4 at period end",
                    }
                ],
            },
            {
                "item": "testing",
                "summary": "Load testing passed on February 20.",
                "status": "asserted",
                "citations": [
                    {
                        "document_id": "DEMO-DRAFT",
                        "page": 1,
                        "quote": "Load testing passed on February 20 with no open defects",
                    }
                ],
            },
            {
                "item": "migration",
                "summary": (
                    "The migration finished February 10 and all downstream "
                    "systems were certified."
                ),
                "status": "asserted",
                "citations": [
                    {
                        "document_id": "DEMO-MINUTES",
                        "page": 1,
                        "quote": "Ms. Okafor confirmed the data migration finished on February 10",
                    }
                ],
            },
            {
                "item": "cutover",
                "summary": "The cutover window is March 14-15.",
                "status": "asserted",
                "citations": [
                    {
                        "document_id": "DEMO-PLAN",
                        "page": 1,
                        "quote": "The cutover window is March 14-15",
                    }
                ],
            },
            {
                "item": "readiness",
                "summary": "Go-live is scheduled.",
                "status": "asserted",
                "citations": [
                    {
                        "document_id": "DEMO-STATUS",
                        "page": 2,
                        "quote": "Go-live is scheduled for March 15",
                    }
                ],
            },
            {
                "item": "readiness",
                "summary": "The vendor vanished without a trace.",
                "status": "asserted",
                "citations": [
                    {"document_id": "DOC-VANISHED", "page": 1, "quote": "gone"}
                ],
            },
            {
                "item": "migration",
                "summary": "Someone confirmed something.",
                "status": "asserted",
                "citations": [
                    {"document_id": "DEMO-MINUTES", "page": 1, "quote": ""}
                ],
            },
        ],
        "gaps": (
            "Whether the March 1 board meeting occurred is unresolved "
            "in the reviewed record."
        ),
    }
    case.file_memo(memo, "unverified")
    instance = case.artifact_open("Harborview readiness audit")
    cutover = next(r for r in instance["rows"] if r["item"] == "cutover")
    cutover["citations"][0]["pin_sha"] = plan_v1_sha
    cutover["history"].append(
        {"at": now(), "change": "pinned", "detail": "pinned to plan revision A (superseded)"}
    )
    case.artifact_save(instance, "artifact_demo_pinned")
    board = case.artifact_open("Harborview chronology", TEMPLATE_BOARD)
    confirm_event(board, case.data, "F5", "February 10", "February 10", "Migration finished")
    confirm_event(board, case.data, "F7", "March 15", "March 15", "Go-live scheduled")
    confirm_event(board, case.data, "F6", "March 14-15", "March 14-15", "Cutover window")
    confirm_event(board, case.data, "F8", "March 1", "March 1", "Board review")
    annotate_event(
        board,
        "E2",
        owner="A. Rivera",
        uncertainty="Go-live assumes the board clears the payments queue.",
    )
    link_events(board, "E2", "E1", "depends_on", "go-live needs the migration record")
    link_events(board, "E3", "E1", "depends_on", "cutover needs the migration record")
    link_events(
        board,
        "E2",
        "E4",
        "disputed_order",
        "reviewer: sign-off is pending the payments queue, which may move go-live",
    )
    case.artifact_save(board, "artifact_demo_seeded")
    case.note_context("purpose", "synthetic demo matter", "known", "fixture")
    case.note_context("governing_law", "", "unknown", "")
    case.open_branch(
        "readiness", "ca", "unknown", "performance location unclear (fixture)"
    )
    case.open_branch(
        "cutover", "ca", "known", "contract clause 12 names California law (fictional)"
    )
    jx = case.artifact_open("Harborview jurisdiction survey", TEMPLATE_JX)
    add_cell(jx, case.data, "readiness", "ca", "substantive")
    set_cell(
        jx, case.data, "C1",
        proposition="Defect remedies follow the contract's acceptance clause",
        citation="Demo fixture — not a legal authority",
        caveat="governing law unresolved for readiness",
    )
    link_cell_branch(jx, case.data, "C1", "B1")
    add_cell(jx, case.data, "cutover", "ca", "substantive")
    set_cell(
        jx, case.data, "C2",
        proposition="Cutover needs board approval",
        citation="DEMO-PLAN rev B passage (fictional)",
        caveat="fixture passage, not legal research",
    )
    link_cell_branch(jx, case.data, "C2", "B2")
    add_cell(jx, case.data, "testing", "ew", "substantive")
    set_cell(
        jx, case.data, "C3",
        proposition="Testing sign-off standards (fixture question)",
        caveat="no branch opened for testing under ew",
    )
    case.artifact_save(jx, "artifact_demo_seeded")
    ct = case.artifact_open("Harborview contract review", TEMPLATE_CT)
    add_clause(
        ct, case.data, agent_root, "DEMO-CONTRACT", 1,
        "The client accepts each deliverable within ten business days of delivery.",
        "Acceptance",
    )
    set_clause(
        ct, "K1",
        effect="Client must accept or reject within ten business days; "
        "silence is not acceptance (reviewer reading).",
        risk="low",
        risk_note="Short clock; confirm business days in negotiation.",
    )
    flag_clause(ct, "K1", "confirmed", "fixture reviewed")
    add_clause(
        ct, case.data, agent_root, "DEMO-CONTRACT", 2,
        "Neither party's liability exceeds the fees paid in the prior twelve months.",
        "Liability cap",
    )
    set_clause(
        ct, "K2",
        effect="Liability capped at trailing-twelve-month fees (reviewer reading).",
        risk="high",
        risk_note="Cap may be too low for the rollout scope; needs counsel decision.",
    )
    flag_clause(ct, "K2", "flagged", "cap level needs a decision")
    add_clause(
        ct, case.data, agent_root, "DEMO-PLAN", 1,
        "Rollback requires board approval and a filed notice.",
        "Rollback gate",
    )
    set_clause(
        ct, "K3",
        effect="Rollback needs board approval plus a filed notice (reviewer reading).",
    )
    case.artifact_save(ct, "artifact_demo_seeded")
    pb = case.artifact_open("Harborview playbook review", TEMPLATE_PB, ct["id"])
    add_rule(
        pb,
        "Acceptance clocks",
        "Acceptance periods must be at least ten business days; "
        "silence is never acceptance (fixture playbook).",
        "standard",
    )
    add_rule(
        pb,
        "Liability caps",
        "Liability caps at or below twelve-month fees need counsel "
        "approval for rollouts this size (fixture playbook).",
        "critical",
    )
    assess(
        pb, case.data, "K1", "R1", "conforms",
        "Ten business days meets the floor (reviewer call).",
    )
    assess(
        pb, case.data, "K2", "R2", "deviates",
        "Cap sits at the floor while the scope exceeds it; needs a decision.",
    )
    assess(pb, case.data, "K3", "R1", "undecided")
    case.artifact_save(pb, "artifact_demo_seeded")
    cal = case.artifact_open("Harborview obligation calendar", TEMPLATE_CAL, ct["id"])
    add_ob(
        cal, case.data, "K1", "renewal", "Services agreement renewal",
        due_date="2027-03-01", notice_days=60,
        notes="Start renewal review ahead of the window (fixture).",
    )
    add_ob(
        cal, case.data, "K2", "notice", "Liability cap review",
        notes="Date depends on the counsel decision (fixture).",
    )
    add_ob(
        cal, case.data, "K3", "notice", "Rollback notice filed",
        due_date="2026-02-20",
        notes="Notice obligation from the cutover plan (fixture).",
    )
    resolve_ob(cal, "OB3", "satisfied", "Filed per plan rev B (fixture).")
    case.artifact_save(cal, "artifact_demo_seeded")
    intake = case.artifact_open("Harborview intake queue", TEMPLATE_IN)
    add_in(
        intake, case.data,
        "Harborview renewal review",
        "Review the services agreement before the renewal window (fixture).",
        "contract", "expedited",
        requester="Procurement (fixture)", issue_id="readiness",
    )
    triage_in(intake, "IN1", "R. Alvarez")
    add_in(
        intake, case.data,
        "Payments queue complaint",
        "A customer alleges the payments queue lost a transfer (fixture).",
        "litigation", "critical",
        requester="Support desk (fixture)",
    )
    add_in(
        intake, case.data,
        "Filing deadline question",
        "When is the next compliance filing due (fixture)?",
        "regulatory", "routine",
        requester="Ops (fixture)", issue_id="cutover",
    )
    route_in(
        intake, "IN3", "Regulatory desk",
        "Standard deadline lookup; no investigation needed (fixture).",
    )
    case.artifact_save(intake, "artifact_demo_seeded")
    board = case.artifact_open("Harborview matter board", TEMPLATE_DL, cal["id"])
    add_dl(
        board, case.data, "Renewal window opens", "renewal",
        owner="R. Alvarez", due_date="2027-01-01", ob_id="OB1",
        notes="Tracks the services renewal (fixture).",
    )
    add_dl(
        board, case.data, "Cap review meeting", "meeting",
        ob_id="OB2",
        notes="Date once counsel decides (fixture).",
    )
    add_dl(
        board, case.data, "Rollback notice filed", "filing",
        owner="J. Osei", due_date="2026-02-20", ob_id="OB3",
    )
    resolve_dl(board, "DL3", "done", "Filed per plan rev B (fixture).")
    case.artifact_save(board, "artifact_demo_seeded")
    disc = case.artifact_open("Harborview discovery log", TEMPLATE_DR)
    add_rq(
        disc,
        "Payments queue logs",
        "All transfer logs for the disputed window (fixture).",
        "outgoing", "documents",
        due_date="2027-02-15", counterpart="Vendor (fixture)",
    )
    serve_rq(disc, "RQ1")
    respond_rq(
        disc, case.data, agent_root, "RQ1", "2027-01-20",
        "Partial production: status report; logs to follow (fixture).",
        ["DEMO-STATUS"],
    )
    add_rq(
        disc,
        "Cutover timeline",
        "When was each cutover step approved (fixture)?",
        "incoming", "interrogatory",
        counterpart="Counterparty (fixture)",
    )
    add_rq(
        disc,
        "Admit the outage date",
        "Admit the payments queue was down on Feb 12 (fixture).",
        "incoming", "admission",
        due_date="2027-03-01", counterpart="Counterparty (fixture)",
    )
    serve_rq(disc, "RQ3")
    decide_dr(disc, "RQ3", "objected", "Vague as to which outage (fixture).")
    case.artifact_save(disc, "artifact_demo_seeded")
    hz = case.artifact_open("Harborview horizon scan", TEMPLATE_HZ, jx["id"])
    add_wh(
        hz, case.data,
        "Acceptance-clock guidance (fixture)",
        "A fictional bulletin discusses acceptance timing; "
        "watch whether it affects the readiness cell.",
        "ca", "Fixture bulletin 2027-03 (fictional)", "2027-01-10",
        cell_id="C1", assignee="R. Osei",
    )
    add_wh(
        hz, case.data,
        "Liability-cap proposal (fixture)",
        "A fictional proposal would cap vendor liability; "
        "assess against the cutover cell.",
        "ca", "Fixture proposal packet (fictional)", "2027-01-12",
        effective_date="2027-06-01", last_checked="2027-01-15",
        relevance="Could change the cutover approval analysis (fixture).",
        cell_id="C2", assignee="R. Osei",
    )
    decide_wh(hz, "WH2", "assessing", "Reading the proposal text (fixture).")
    add_wh(
        hz, case.data,
        "Testing standards note (fixture)",
        "A fictional note on sign-off standards; out of scope "
        "for this matter.",
        "ew", "Fixture standards note (fictional)", "2027-01-08",
    )
    decide_wh(hz, "WH3", "closed", "Out of scope for Harborview (fixture).")
    case.artifact_save(hz, "artifact_demo_seeded")
    gv = case.artifact_open("Harborview AI inventory", TEMPLATE_GV, intake["id"])
    add_uc(
        gv, case.data,
        "Queue-triage assistant (fixture)",
        "Draft replies to payments-queue complaints for staff review.",
        "Customer complaint text (fixture).",
        system="Fictional triage assistant",
        owner="J. Osei", in_id="IN2",
    )
    add_uc(
        gv, case.data,
        "Renewal-date extractor (fixture)",
        "Pull renewal dates from service agreements into the calendar.",
        "Agreement text and renewal clauses (fixture).",
        system="Fictional extractor",
        owner="J. Osei", in_id="IN1",
    )
    set_uc(gv, case.data, "UC2", risk="medium")
    decide_uc(gv, "UC2", "assessed", "Scoped to extraction review (fixture).")
    add_uc(
        gv, case.data,
        "Filing-calendar reminder (fixture)",
        "Remind staff of upcoming compliance filings.",
        "Filing calendar entries (fixture).",
        system="Fictional reminder",
    )
    set_uc(
        gv, case.data, "UC3", risk="low",
        conditions="A human confirms every filing date (fixture).",
    )
    decide_uc(gv, "UC3", "assessed", "Reminder only; no advice (fixture).")
    decide_uc(gv, "UC3", "approved", "Low risk with the condition (fixture).")
    case.artifact_save(gv, "artifact_demo_seeded")
    ec = case.artifact_open("Harborview entity calendar", TEMPLATE_EC)
    add_en(ec, "Harborview LLC (fixture)", "ca", "CA-0001 (fixture)")
    add_en(ec, "Harborview Ltd (fixture)", "ew")
    add_cf(
        ec, "EN1", "Annual report (fixture)", "annual-report",
        due_date="2027-04-01", period="FY2026",
    )
    add_cf(
        ec, "EN1", "Franchise tax (fixture)", "franchise-tax",
        period="FY2026", notes="Amount unknown until assessed (fixture).",
    )
    add_cf(
        ec, "EN2", "Registered agent renewal (fixture)", "registered-agent",
        due_date="2027-01-15",
    )
    decide_cf(ec, "CF3", "filed", "Filed with confirmation (fixture).")
    case.artifact_save(ec, "artifact_demo_seeded")
    tc = case.artifact_open("Harborview term-sheet comparison", TEMPLATE_TS)
    add_ts(tc, "Offer A (fixture)", "Fund A (fixture)", "2027-01-20")
    add_ts(tc, "Offer B (fixture)", "Fund B (fixture)", "2027-01-25")
    add_tm(tc, "Valuation (fixture)", "Pre-money valuation offered.")
    position_tm(tc, "TM1", "TS1", "$8M pre-money (fixture).")
    position_tm(tc, "TM1", "TS2", "$10M pre-money (fixture).")
    decide_tm(tc, "TM1", "divergent", "Two-point gap (fixture).")
    add_tm(tc, "Board (fixture)", "Investor board seat.")
    position_tm(tc, "TM2", "TS1", "One investor seat (fixture).")
    position_tm(tc, "TM2", "TS2", "One investor seat (fixture).")
    decide_tm(tc, "TM2", "aligned", "Same seat (fixture).")
    add_tm(tc, "Option pool (fixture)", "Post-money option pool.")
    position_tm(tc, "TM3", "TS1", "12% post-money (fixture).")
    case.artifact_save(tc, "artifact_demo_seeded")
    pt = case.artifact_open("Harborview patent comparison", TEMPLATE_PT)
    add_sj(pt, "Patent X claim 1 (fixture)", "patent-claim", "US-0000001 (fictional)")
    add_sj(pt, "Widget Y (fixture)", "product", "Accused product (fixture)")
    add_el(pt, "claim 1, element [a]", "A wireless module (transcribed fixture).")
    reading_el(pt, "EL1", "SJ1", "Element [a] as claimed (fixture).")
    reading_el(pt, "EL1", "SJ2", "Module present in the widget (fixture).")
    decide_el(pt, "EL1", "mapped", "Module present (fixture).")
    add_el(pt, "claim 1, element [b]", "A solar cell (transcribed fixture).")
    reading_el(pt, "EL2", "SJ1", "Element [b] as claimed (fixture).")
    reading_el(pt, "EL2", "SJ2", "No solar cell described (fixture).")
    decide_el(pt, "EL2", "not-mapped", "No solar cell described (fixture).")
    add_el(pt, "claim 1, element [c]", "An encrypted channel (transcribed fixture).")
    reading_el(pt, "EL3", "SJ1", "Element [c] as claimed (fixture).")
    case.artifact_save(pt, "artifact_demo_seeded")
    qa = case.artifact_open("Harborview sell-side Q&A", TEMPLATE_QA)
    add_qa(
        qa, "What is the revenue recognition policy (fixture)?",
        asker="Buyer counsel (fixture)", topic="financials",
        assignee="R. Osei", due_date="2027-02-15",
    )
    answer_qa(
        qa, agent_root, "QA1",
        "Revenue is recognized on acceptance (fixture).",
        ["DEMO-STATUS"],
    )
    decide_qa(qa, "QA1", "answered", "Supported by the status memo (fixture).")
    add_qa(
        qa, "Is any litigation pending (fixture)?",
        asker="Buyer counsel (fixture)", topic="litigation",
        assignee="R. Osei",
    )
    add_qa(
        qa, "How is staff retained through close (fixture)?",
        asker="Buyer HR (fixture)", topic="employment",
        assignee="R. Osei", due_date="2027-02-20",
    )
    answer_qa(
        qa, agent_root, "QA3",
        "Retention bonuses were minuted (fixture).",
        ["DEMO-MINUTES"],
    )
    decide_qa(qa, "QA3", "answered", "Minuted bonuses (fixture).")
    decide_qa(qa, "QA3", "clarify", "Buyer wants the figures (fixture).")
    case.artifact_save(qa, "artifact_demo_seeded")
    sp = case.artifact_open("Harborview counsel spend", TEMPLATE_SP)
    add_vn(sp, "Fictional Firm LLP", "Partner P (fixture)")
    add_eg(sp, "VN1", "Harborview acquisition (fixture)", "50000.00", "USD")
    add_eg(sp, "VN1", "Harborview employment (fixture)", currency="USD")
    add_iv(sp, "EG1", "January invoice (fixture)", "12000.00")
    decide_iv(sp, "IV1", "approved", "Within budget (fixture).")
    add_iv(sp, "EG1", "February invoice (fixture)", "8000.00")
    add_iv(sp, "EG2", "Review invoice (fixture)", "3000.00")
    case.artifact_save(sp, "artifact_demo_seeded")
    return DEMO_RUN_ID

__all__ = ['DEMO_RUN_ID', 'DEMO_OBJECTIVE', 'DEMO_DOCS', 'DEMO_PLAN_V1', 'DEMO_PLAN_V2', 'DEMO_PNG', '_write_demo_doc', '_seed_transcript', 'build_demo']
