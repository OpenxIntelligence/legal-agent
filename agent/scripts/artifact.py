#!/usr/bin/env python3
"""Citation & Claim Audit instances: open, check, revise, review, export.

./helper artifact demo                                  # build the synthetic demo matter
./helper artifact open RUN_ID [--title ...]             # audit the filed memo
./helper artifact show RUN_ID A1                        # rows, checks, staleness, review
./helper artifact check RUN_ID A1 [--rows R1,R2]        # structural + support checks
./helper artifact revise RUN_ID A1 --row R1 [--assertion ... | --cite DOC,PAGE,QUOTE] [--index N] [--feedback ...]
./helper artifact board RUN_ID A2 --op confirm|add|annotate|link|dismiss [...]
./helper artifact matrix RUN_ID A3 --op add|set|link|flag|retract [...]
./helper artifact contract RUN_ID A4 --op add|set|flag|retract [...]
./helper artifact playbook RUN_ID A5 --op add-rule|set-rule|retract-rule|assess [...]
./helper artifact calendar RUN_ID A6 --op add|set|resolve [...]
./helper artifact intake RUN_ID A7 --op add|set|triage|route|decide [...]
./helper artifact deadlines RUN_ID A8 --op add|set|resolve [...]
./helper artifact discovery RUN_ID A9 --op add|set|serve|respond|decide [...]
./helper artifact review RUN_ID A1 (--approve | --request-changes) --snapshot JSON [--notes ...] [--qualify ...]
./helper artifact diff RUN_ID A1 OLD NEW                # before/after row comparison
./helper artifact export RUN_ID A1 [--version N] [--format json|md]
                                                    # versioned JSON or Markdown report

Deterministic citation checks always run. The support check needs a
model client; without one it is recorded unavailable, never failed.

Set LEGAL_AGENT_ROOT to run against an isolated synthetic data root
(a directory holding workspace/); otherwise the agent folder this
script lives in is used. Code always loads from this script's folder.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(CODE_ROOT))


def _resolve_root() -> Path:
    """Data root: LEGAL_AGENT_ROOT when set, else this script's agent folder."""
    override = (os.environ.get("LEGAL_AGENT_ROOT") or "").strip()
    if not override:
        return CODE_ROOT
    root = Path(override).expanduser().resolve()
    if not root.is_dir():
        from agent_kit.case_record import CaseError

        raise CaseError(f"LEGAL_AGENT_ROOT is not a directory: {override}")
    return root


ROOT = _resolve_root()

from agent_kit import artifacts, review_labels  # noqa: E402
from agent_kit.secrets import MissingSecretError
from agent_kit.case_record import CaseError, CaseRecord  # noqa: E402
from verification import verify_claims  # noqa: E402


def _case(run_id: str) -> CaseRecord:
    return CaseRecord(ROOT, run_id)


def cmd_demo(_args: argparse.Namespace) -> dict:
    run_id = artifacts.build_demo(ROOT)
    return {"run_id": run_id, "demo": True}


def cmd_gallery(_args: argparse.Namespace) -> dict:
    return {"cards": artifacts.gallery_cards()}


def cmd_open(args: argparse.Namespace) -> dict:
    case = _case(args.run_id)
    instance = case.artifact_open(
        args.title or "", args.template or "", args.source or ""
    )
    count = artifacts.content_count(instance)
    return {"instance": instance["id"], "template": instance["template"], "rows": count}


def _board_view(case, instance: dict) -> dict:
    from agent_kit import scheduler

    events = []
    for event in instance.get("events") or []:
        event = dict(event)
        event["stale"] = artifacts.event_staleness(case.data, event)
        events.append(event)
    checklist = case.data.get("checklist", {}) or {}
    facts = case.data.get("facts", []) or []
    questions = [
        {
            "id": item_id,
            "question": entry.get("question", ""),
            "evidence_needed": entry.get("evidence_needed", ""),
            "facts": sum(1 for f in facts if f.get("item") == item_id),
            "contested": item_id in case.contested_items(),
        }
        for item_id, entry in sorted(checklist.items())
        if isinstance(entry, dict)
    ]
    tasks = case.data.get("tasks", {}) or {}
    rows = [
        {
            "id": task.get("id", ""),
            "kind": task.get("kind", ""),
            "question": task.get("question", ""),
            "status": task.get("status", ""),
            "blocked_on": task.get("blocked_on") or "",
            "waiting_on": scheduler.waiting_on(case, task),
            "attempts": len(task.get("attempts") or []),
        }
        for task in sorted(tasks.values(), key=lambda t: t.get("seq", 0))
        if isinstance(task, dict)
    ]
    return {
        "instance": {**instance, "events": events},
        "candidates": artifacts.board_candidates(case.data, instance),
        "facts": [
            {
                "id": fact.get("id"),
                "item": fact.get("item"),
                "document_id": fact.get("document_id"),
                "page": fact.get("page"),
                "quote": fact.get("quote", ""),
                "stance": fact.get("stance", "supports"),
            }
            for fact in facts
            if isinstance(fact, dict)
        ],
        "questions": questions,
        "tasks": rows,
        "next": scheduler.select_next(case),
        "snapshot": artifacts.current_snapshot(instance, case.data),
    }


def cmd_show(args: argparse.Namespace) -> dict:
    case = _case(args.run_id)
    instance = case.artifact_get(args.instance)
    view = _VIEWS.get(instance.get("template") or "")
    if view is not None:
        result = view(case, instance)
    else:
        rows = []
        for row in instance.get("rows") or []:
            row = dict(row)
            row["citations"] = artifacts.scope_citations(
                row.get("citations") or [], case.data, ROOT)
            row["stale"] = artifacts.row_staleness(ROOT, case.data, row)
            row["contested"] = artifacts.contested_info(
                case.data, str(row.get("item") or ""), ROOT)
            rows.append(row)
        result = {
            "instance": {**instance, "rows": rows},
            "status": artifacts.instance_status(instance, case.data, ROOT),
            "snapshot": artifacts.current_snapshot(instance, case.data),
        }
    result["data_mode"] = artifacts.data_mode(instance)
    return result


def cmd_board(args: argparse.Namespace) -> dict:
    case = _case(args.run_id)
    instance = case.artifact_get(args.instance)
    if instance.get("template") != artifacts.TEMPLATE_BOARD:
        raise CaseError("board operations need a chronology instance")
    op = args.op
    if op == "confirm":
        event = artifacts.confirm_event(
            instance, case.data, args.fact, args.span, args.date or "", args.label or ""
        )
    elif op == "add":
        event = artifacts.add_event(
            instance,
            case.data,
            args.label or "",
            date=args.date or "",
            issue_ids=[i.strip() for i in (args.issues or "").split(",") if i.strip()],
            fact_ids=[f.strip() for f in (args.facts or "").split(",") if f.strip()],
            uncertainty=args.uncertainty or "",
        )
    elif op == "annotate":
        event = artifacts.annotate_event(
            instance,
            args.event,
            label=args.label,
            date=args.date,
            owner=args.owner,
            uncertainty=args.uncertainty,
        )
    elif op == "link":
        link = artifacts.link_events(
            instance, args.event, args.with_, args.kind or "", args.reason or ""
        )
        case.artifact_save(instance, "artifact_board", f"linked {args.event}")
        return {"linked": [link["a"], link["b"], link["kind"]]}
    elif op == "dismiss":
        artifacts.dismiss_candidate(instance, args.fact, args.span)
        case.artifact_save(instance, "artifact_board", f"dismissed {args.fact}")
        return {"dismissed": [args.fact, args.span]}
    else:
        raise CaseError(f"unknown board operation {op!r}")
    case.artifact_save(instance, "artifact_board", event["event_id"])
    return {"event": event["event_id"]}


def _jx_view(case, instance: dict) -> dict:
    cells = []
    for cell in instance.get("cells") or []:
        cell = dict(cell)
        cell["stale"] = artifacts.cell_staleness(case.data, cell)
        branch = (case.data.get("branches", {}) or {}).get(cell.get("branch_id") or "")
        cell["branch"] = (
            {
                "id": cell.get("branch_id"),
                "status": branch.get("status"),
                "basis": branch.get("basis"),
            }
            if isinstance(branch, dict)
            else None
        )
        cells.append(cell)
    branches = case.data.get("branches", {}) or {}
    matter = case.data.get("matter", {}) or {}
    from agent_kit import sources

    return {
        "instance": {**instance, "cells": cells},
        "readiness": artifacts.jx_readiness(case.data, instance),
        "regimes": sorted(sources.REGIONS),
        "branches": [
            {
                "id": branch.get("id", bid),
                "issue": branch.get("issue", ""),
                "regime": branch.get("regime", ""),
                "status": branch.get("status", ""),
                "basis": branch.get("basis", ""),
            }
            for bid, branch in sorted(branches.items())
            if isinstance(branch, dict)
        ],
        "matter": {
            "version": matter.get("version"),
            "fields": [
                {
                    "field": name,
                    "value": entry.get("value", ""),
                    "status": entry.get("status", "unknown"),
                }
                for name, entry in sorted((matter.get("fields", {}) or {}).items())
                if isinstance(entry, dict)
            ],
        },
        "issues": sorted(
            key
            for key, entry in (case.data.get("checklist", {}) or {}).items()
            if isinstance(entry, dict)
        ),
        "snapshot": artifacts.current_snapshot(instance, case.data),
    }


def _ct_view(case, instance: dict) -> dict:
    clauses = []
    for clause in instance.get("clauses") or []:
        clause = dict(clause)
        clause["status"] = artifacts.clause_status(case.data, ROOT, clause)
        clauses.append(clause)
    return {
        "instance": {**instance, "clauses": clauses},
        "summary": artifacts.ct_summary(case.data, ROOT, instance),
        "read_documents": sorted(d for d in case.data.get("read_ids", []) or [] if d),
        "snapshot": artifacts.current_snapshot(instance, case.data),
    }


def _pb_view(case, instance: dict) -> dict:
    deviations = []
    for deviation in instance.get("deviations") or []:
        deviation = dict(deviation)
        deviation["status"] = artifacts.deviation_status(
            instance, case.data, deviation
        )
        deviations.append(deviation)
    source = (case.data.get("artifacts", {}) or {}).get(
        instance.get("source_instance") or ""
    ) or {}
    return {
        "instance": {**instance, "deviations": deviations},
        "summary": artifacts.pb_summary(instance, case.data),
        "clauses": [
            {
                "clause_id": c.get("clause_id"),
                "heading": c.get("heading"),
                "document_id": c.get("document_id"),
                "page": c.get("page"),
                "risk": c.get("risk"),
                "review_status": c.get("review_status"),
                "retracted": bool(c.get("retracted")),
            }
            for c in source.get("clauses") or []
            if isinstance(c, dict)
        ],
        "snapshot": artifacts.current_snapshot(instance, case.data),
    }


def _cal_view(case, instance: dict) -> dict:
    obligations = []
    for obligation in instance.get("obligations") or []:
        obligation = dict(obligation)
        obligation["live"] = artifacts.ob_status(
            instance, case.data, obligation
        )
        obligations.append(obligation)
    source = (case.data.get("artifacts", {}) or {}).get(
        instance.get("source_instance") or ""
    ) or {}
    return {
        "instance": {**instance, "obligations": obligations},
        "summary": artifacts.cal_summary(instance, case.data),
        "clauses": [
            {
                "clause_id": c.get("clause_id"),
                "heading": c.get("heading"),
                "document_id": c.get("document_id"),
                "page": c.get("page"),
                "retracted": bool(c.get("retracted")),
            }
            for c in source.get("clauses") or []
            if isinstance(c, dict)
        ],
        "snapshot": artifacts.current_snapshot(instance, case.data),
    }


def _in_view(case, instance: dict) -> dict:
    intakes = []
    for request in instance.get("intakes") or []:
        request = dict(request)
        request["live"] = artifacts.in_status(
            instance, case.data, request
        )
        intakes.append(request)
    checklist = case.data.get("checklist", {}) or {}
    return {
        "instance": {**instance, "intakes": intakes},
        "summary": artifacts.in_summary(instance, case.data),
        "issues": [
            {"id": key, "question": entry.get("question", "")}
            for key, entry in sorted(checklist.items())
            if isinstance(entry, dict)
        ],
        "snapshot": artifacts.current_snapshot(instance, case.data),
    }


def _dl_view(case, instance: dict) -> dict:
    deadlines = []
    for item in instance.get("deadlines") or []:
        item = dict(item)
        item["live"] = artifacts.dl_status(
            instance, case.data, item
        )
        deadlines.append(item)
    source = (case.data.get("artifacts", {}) or {}).get(
        instance.get("source_instance") or ""
    ) or {}
    return {
        "instance": {**instance, "deadlines": deadlines},
        "summary": artifacts.dl_summary(instance, case.data),
        "obligations": [
            {
                "ob_id": o.get("ob_id"),
                "title": o.get("title"),
                "due_date": o.get("due_date"),
                "status": o.get("status"),
            }
            for o in source.get("obligations") or []
            if isinstance(o, dict)
        ],
        "snapshot": artifacts.current_snapshot(instance, case.data),
    }


def _dr_view(case, instance: dict) -> dict:
    requests = []
    for request in instance.get("requests") or []:
        request = dict(request)
        request["live"] = artifacts.rq_status(
            instance, case.data, ROOT, request
        )
        requests.append(request)
    responses = []
    for response in instance.get("responses") or []:
        response = dict(response)
        response["live"] = artifacts.rs_status(case.data, ROOT, response)
        responses.append(response)
    return {
        "instance": {**instance, "requests": requests, "responses": responses},
        "summary": artifacts.dr_summary(instance, case.data, ROOT),
        "inbox": sorted(artifacts._inbox_shas(ROOT)),
        "snapshot": artifacts.current_snapshot(instance, case.data),
    }


def _hz_view(case, instance: dict) -> dict:
    watches = []
    for watch in instance.get("watches") or []:
        watch = dict(watch)
        watch["live"] = artifacts.wh_status(instance, case.data, watch)
        watches.append(watch)
    source = (case.data.get("artifacts", {}) or {}).get(
        instance.get("source_instance") or ""
    ) or {}
    from agent_kit import sources

    return {
        "instance": {**instance, "watches": watches},
        "summary": artifacts.hz_summary(instance, case.data),
        "regimes": sorted(sources.REGIONS),
        "cells": [
            {
                "cell_id": c.get("cell_id"),
                "issue_id": c.get("issue_id"),
                "regime": c.get("regime"),
                "retracted": c.get("retracted"),
            }
            for c in source.get("cells") or []
            if isinstance(c, dict)
        ],
        "snapshot": artifacts.current_snapshot(instance, case.data),
    }


def cmd_horizon(args: argparse.Namespace) -> dict:
    case = _case(args.run_id)
    instance = case.artifact_get(args.instance)
    if instance.get("template") != artifacts.TEMPLATE_HZ:
        raise CaseError("horizon operations need a regulatory-horizon scan")
    op = args.op
    if op == "add":
        watch = artifacts.add_wh(
            instance,
            case.data,
            args.title or "", args.summary or "",
            args.regime or "", args.source_desc or "",
            args.observed or "",
            effective_date=args.effective or "",
            last_checked=args.checked or "",
            relevance=args.relevance or "",
            assignee=args.assignee or "",
            cell_id=args.cell or "",
        )
    elif op == "set":
        watch = artifacts.set_wh(
            instance,
            case.data,
            args.watch,
            title=args.title,
            summary=args.summary,
            regime=args.regime,
            source_desc=args.source_desc,
            observed_date=args.observed,
            effective_date=args.effective,
            last_checked=args.checked,
            relevance=args.relevance,
            assignee=args.assignee,
            cell_id=args.cell,
        )
    elif op == "decide":
        watch = artifacts.decide_wh(
            instance, args.watch, args.status or "", args.reason or ""
        )
    else:
        raise CaseError(f"unknown horizon operation {op!r}")
    case.artifact_save(instance, "artifact_horizon", watch["wh_id"])
    return {"watch": watch["wh_id"]}


def _gv_view(case, instance: dict) -> dict:
    uses = []
    for use in instance.get("uses") or []:
        use = dict(use)
        use["live"] = artifacts.uc_status(instance, case.data, use)
        uses.append(use)
    source = (case.data.get("artifacts", {}) or {}).get(
        instance.get("source_instance") or ""
    ) or {}
    return {
        "instance": {**instance, "uses": uses},
        "summary": artifacts.gv_summary(instance, case.data),
        "intakes": [
            {
                "in_id": r.get("in_id"),
                "title": r.get("title"),
                "status": r.get("status"),
            }
            for r in source.get("intakes") or []
            if isinstance(r, dict)
        ],
        "snapshot": artifacts.current_snapshot(instance, case.data),
    }


def cmd_governance(args: argparse.Namespace) -> dict:
    case = _case(args.run_id)
    instance = case.artifact_get(args.instance)
    if instance.get("template") != artifacts.TEMPLATE_GV:
        raise CaseError("governance operations need an AI-governance inventory")
    op = args.op
    if op == "add":
        use = artifacts.add_uc(
            instance,
            case.data,
            args.title or "", args.description or "", args.data or "",
            system=args.system or "",
            owner=args.owner or "",
            in_id=args.intake or "",
        )
    elif op == "set":
        use = artifacts.set_uc(
            instance,
            case.data,
            args.use,
            title=args.title,
            description=args.description,
            data_types=args.data,
            system=args.system,
            risk=args.risk,
            owner=args.owner,
            conditions=args.conditions,
            in_id=args.intake,
        )
    elif op == "decide":
        use = artifacts.decide_uc(
            instance, args.use, args.status or "", args.reason or ""
        )
    else:
        raise CaseError(f"unknown governance operation {op!r}")
    case.artifact_save(instance, "artifact_governance", use["uc_id"])
    return {"use": use["uc_id"]}


def _ec_view(case, instance: dict) -> dict:
    filings = []
    for filing in instance.get("filings") or []:
        filing = dict(filing)
        filing["live"] = artifacts.cf_status(instance, filing)
        filings.append(filing)
    from agent_kit import sources

    return {
        "instance": {**instance, "filings": filings},
        "summary": artifacts.ec_summary(instance),
        "regimes": sorted(sources.REGIONS),
        "snapshot": artifacts.current_snapshot(instance, case.data),
    }


def cmd_entity(args: argparse.Namespace) -> dict:
    case = _case(args.run_id)
    instance = case.artifact_get(args.instance)
    if instance.get("template") != artifacts.TEMPLATE_EC:
        raise CaseError("entity operations need an entity-compliance calendar")
    op = args.op
    if op == "add-en":
        entity = artifacts.add_en(
            instance,
            args.name or "", args.regime or "",
            identifier=args.identifier or "",
        )
        saved = entity["en_id"]
    elif op == "set-en":
        entity = artifacts.set_en(
            instance,
            args.entity,
            name=args.name,
            regime=args.regime,
            identifier=args.identifier,
        )
        saved = entity["en_id"]
    elif op == "add":
        filing = artifacts.add_cf(
            instance,
            args.entity or "", args.title or "", args.kind or "",
            due_date=args.due_date or "",
            period=args.period or "",
            notes=args.notes or "",
        )
        saved = filing["cf_id"]
    elif op == "set":
        filing = artifacts.set_cf(
            instance,
            args.filing,
            en_id=args.entity,
            title=args.title,
            kind=args.kind,
            due_date=args.due_date,
            period=args.period,
            notes=args.notes,
        )
        saved = filing["cf_id"]
    elif op == "rebind":
        filing = artifacts.set_cf(instance, args.filing, rebind=True)
        saved = filing["cf_id"]
    elif op == "decide":
        filing = artifacts.decide_cf(
            instance, args.filing, args.status or "", args.reason or ""
        )
        saved = filing["cf_id"]
    else:
        raise CaseError(f"unknown entity operation {op!r}")
    case.artifact_save(instance, "artifact_entity", saved)
    return {"saved": saved}


def _ts_view(case, instance: dict) -> dict:
    terms = []
    for term in instance.get("terms") or []:
        term = dict(term)
        term["live"] = artifacts.tm_status(instance, term)
        term["missing"] = artifacts.tm_missing(instance, term)
        terms.append(term)
    return {
        "instance": {**instance, "terms": terms},
        "summary": artifacts.ts_summary(instance),
        "snapshot": artifacts.current_snapshot(instance, case.data),
    }


def cmd_termsheet(args: argparse.Namespace) -> dict:
    case = _case(args.run_id)
    instance = case.artifact_get(args.instance)
    if instance.get("template") != artifacts.TEMPLATE_TS:
        raise CaseError("termsheet operations need a term-sheet comparison")
    op = args.op
    if op == "add-sheet":
        sheet = artifacts.add_ts(
            instance,
            args.label or "",
            party=args.party or "",
            date=args.date or "",
            notes=args.notes or "",
        )
        saved = sheet["ts_id"]
    elif op == "set-sheet":
        sheet = artifacts.set_ts(
            instance,
            args.sheet,
            label=args.label,
            party=args.party,
            date=args.date,
            notes=args.notes,
        )
        saved = sheet["ts_id"]
    elif op == "add":
        term = artifacts.add_tm(
            instance, args.topic or "", args.proposition or ""
        )
        saved = term["tm_id"]
    elif op == "set":
        term = artifacts.set_tm(
            instance, args.term, topic=args.topic, proposition=args.proposition
        )
        saved = term["tm_id"]
    elif op == "position":
        term = artifacts.position_tm(
            instance, args.term, args.sheet or "", args.text or ""
        )
        saved = term["tm_id"]
    elif op == "decide":
        term = artifacts.decide_tm(
            instance, args.term, args.status or "", args.reason or ""
        )
        saved = term["tm_id"]
    else:
        raise CaseError(f"unknown termsheet operation {op!r}")
    case.artifact_save(instance, "artifact_termsheet", saved)
    return {"saved": saved}


def _pt_view(case, instance: dict) -> dict:
    elements = []
    for element in instance.get("elements") or []:
        element = dict(element)
        element["live"] = artifacts.el_status(instance, element)
        element["missing"] = artifacts.el_missing(instance, element)
        elements.append(element)
    return {
        "instance": {**instance, "elements": elements},
        "summary": artifacts.pt_summary(instance),
        "snapshot": artifacts.current_snapshot(instance, case.data),
    }


def cmd_patent(args: argparse.Namespace) -> dict:
    case = _case(args.run_id)
    instance = case.artifact_get(args.instance)
    if instance.get("template") != artifacts.TEMPLATE_PT:
        raise CaseError("patent operations need a patent-claim comparison")
    op = args.op
    if op == "add-subject":
        subject = artifacts.add_sj(
            instance,
            args.label or "", args.kind or "",
            ref=args.ref or "",
            notes=args.notes or "",
        )
        saved = subject["sj_id"]
    elif op == "set-subject":
        subject = artifacts.set_sj(
            instance,
            args.subject,
            label=args.label,
            kind=args.kind,
            ref=args.ref,
            notes=args.notes,
        )
        saved = subject["sj_id"]
    elif op == "add":
        element = artifacts.add_el(
            instance, args.claim_ref or "", args.text or ""
        )
        saved = element["el_id"]
    elif op == "set":
        element = artifacts.set_el(
            instance, args.element, claim_ref=args.claim_ref, text=args.text
        )
        saved = element["el_id"]
    elif op == "reading":
        element = artifacts.reading_el(
            instance, args.element, args.subject or "", args.reading or ""
        )
        saved = element["el_id"]
    elif op == "decide":
        element = artifacts.decide_el(
            instance, args.element, args.status or "", args.reason or ""
        )
        saved = element["el_id"]
    else:
        raise CaseError(f"unknown patent operation {op!r}")
    case.artifact_save(instance, "artifact_patent", saved)
    return {"saved": saved}


def _qa_view(case, instance: dict) -> dict:
    questions = []
    for entry in instance.get("questions") or []:
        entry = dict(entry)
        entry["live"] = artifacts.qa_status(instance, ROOT, entry)
        questions.append(entry)
    return {
        "instance": {**instance, "questions": questions},
        "summary": artifacts.qa_summary(instance, ROOT),
        "inbox": sorted(artifacts._inbox_shas(ROOT)),
        "snapshot": artifacts.current_snapshot(instance, case.data),
    }


def cmd_qa(args: argparse.Namespace) -> dict:
    case = _case(args.run_id)
    instance = case.artifact_get(args.instance)
    if instance.get("template") != artifacts.TEMPLATE_QA:
        raise CaseError("qa operations need a sell-side Q&A tracker")
    op = args.op
    if op == "add":
        entry = artifacts.add_qa(
            instance,
            args.question or "",
            asker=args.asker or "",
            topic=args.topic or "",
            assignee=args.assignee or "",
            due_date=args.due_date or "",
        )
        saved = entry["qa_id"]
    elif op == "set":
        entry = artifacts.set_qa(
            instance,
            args.question_id,
            question=args.question,
            asker=args.asker,
            topic=args.topic,
            assignee=args.assignee,
            due_date=args.due_date,
        )
        saved = entry["qa_id"]
    elif op == "answer":
        docs = [
            d.strip() for d in str(args.documents or "").split(",") if d.strip()
        ]
        entry = artifacts.answer_qa(
            instance, ROOT, args.question_id, args.answer or "",
            documents=docs,
        )
        saved = entry["qa_id"]
    elif op == "decide":
        entry = artifacts.decide_qa(
            instance, args.question_id, args.status or "", args.reason or ""
        )
        saved = entry["qa_id"]
    else:
        raise CaseError(f"unknown qa operation {op!r}")
    case.artifact_save(instance, "artifact_qa", saved)
    return {"saved": saved}


def _sp_view(case, instance: dict) -> dict:
    engagements = []
    for engagement in instance.get("engagements") or []:
        engagement = dict(engagement)
        engagement["live"] = artifacts.eg_status(instance, engagement)
        engagement["spend"] = artifacts.sp_spend(
            instance, engagement.get("eg_id") or ""
        )
        engagements.append(engagement)
    invoices = []
    for invoice in instance.get("invoices") or []:
        invoice = dict(invoice)
        invoice["live"] = artifacts.iv_status(instance, invoice)
        invoices.append(invoice)
    return {
        "instance": {
            **instance, "engagements": engagements, "invoices": invoices
        },
        "summary": artifacts.sp_summary(instance),
        "snapshot": artifacts.current_snapshot(instance, case.data),
    }


def cmd_spend(args: argparse.Namespace) -> dict:
    case = _case(args.run_id)
    instance = case.artifact_get(args.instance)
    if instance.get("template") != artifacts.TEMPLATE_SP:
        raise CaseError("spend operations need a counsel-spend tracker")
    op = args.op
    if op == "add-vendor":
        vendor = artifacts.add_vn(
            instance, args.name or "", contact=args.contact or ""
        )
        saved = vendor["vn_id"]
    elif op == "set-vendor":
        vendor = artifacts.set_vn(
            instance, args.vendor, name=args.name, contact=args.contact
        )
        saved = vendor["vn_id"]
    elif op == "add-eng":
        engagement = artifacts.add_eg(
            instance,
            args.vendor or "", args.title or "",
            budget=args.budget or "", currency=args.currency or "USD",
        )
        saved = engagement["eg_id"]
    elif op == "set-eng":
        engagement = artifacts.set_eg(
            instance,
            args.engagement,
            vn_id=args.vendor,
            title=args.title,
            budget=args.budget,
            currency=args.currency,
            rebind=bool(args.rebind),
        )
        saved = engagement["eg_id"]
    elif op == "add":
        invoice = artifacts.add_iv(
            instance,
            args.engagement or "", args.label or "", args.amount or "",
            notes=args.notes or "",
        )
        saved = invoice["iv_id"]
    elif op == "set":
        invoice = artifacts.set_iv(
            instance,
            args.invoice,
            eg_id=args.engagement,
            label=args.label,
            amount=args.amount,
            notes=args.notes,
            rebind=bool(args.rebind),
        )
        saved = invoice["iv_id"]
    elif op == "decide":
        invoice = artifacts.decide_iv(
            instance, args.invoice, args.status or "", args.reason or ""
        )
        saved = invoice["iv_id"]
    else:
        raise CaseError(f"unknown spend operation {op!r}")
    case.artifact_save(instance, "artifact_spend", saved)
    return {"saved": saved}


_VIEWS = {
    artifacts.TEMPLATE_BOARD: _board_view,
    artifacts.TEMPLATE_JX: _jx_view,
    artifacts.TEMPLATE_CT: _ct_view,
    artifacts.TEMPLATE_PB: _pb_view,
    artifacts.TEMPLATE_CAL: _cal_view,
    artifacts.TEMPLATE_IN: _in_view,
    artifacts.TEMPLATE_DL: _dl_view,
    artifacts.TEMPLATE_DR: _dr_view,
    artifacts.TEMPLATE_HZ: _hz_view,
    artifacts.TEMPLATE_GV: _gv_view,
    artifacts.TEMPLATE_EC: _ec_view,
    artifacts.TEMPLATE_TS: _ts_view,
    artifacts.TEMPLATE_PT: _pt_view,
    artifacts.TEMPLATE_QA: _qa_view,
    artifacts.TEMPLATE_SP: _sp_view,
}


def cmd_discovery(args: argparse.Namespace) -> dict:
    case = _case(args.run_id)
    instance = case.artifact_get(args.instance)
    if instance.get("template") != artifacts.TEMPLATE_DR:
        raise CaseError("discovery operations need a discovery log")
    op = args.op
    if op == "add":
        request = artifacts.add_rq(
            instance,
            args.title or "", args.description or "",
            args.direction or "", args.category or "",
            due_date=args.due_date or "",
            counterpart=args.counterpart or "",
        )
        saved = request["rq_id"]
    elif op == "set":
        request = artifacts.set_rq(
            instance,
            args.request,
            title=args.title,
            description=args.description,
            direction=args.direction,
            category=args.category,
            due_date=args.due_date,
            counterpart=args.counterpart,
        )
        saved = request["rq_id"]
    elif op == "serve":
        saved = artifacts.serve_rq(instance, args.request)["rq_id"]
    elif op == "respond":
        docs = [
            d.strip() for d in str(args.documents or "").split(",") if d.strip()
        ]
        saved = artifacts.respond_rq(
            instance, case.data, ROOT,
            args.request or "", args.received or "", args.summary or "",
            documents=docs, notes=args.notes or "",
        )["rs_id"]
    elif op == "decide":
        saved = artifacts.decide_dr(
            instance, args.request, args.status or "", args.reason or ""
        )["rq_id"]
    else:
        raise CaseError(f"unknown discovery operation {op!r}")
    case.artifact_save(instance, "artifact_discovery", saved)
    return {"saved": saved}


def cmd_deadlines(args: argparse.Namespace) -> dict:
    case = _case(args.run_id)
    instance = case.artifact_get(args.instance)
    if instance.get("template") != artifacts.TEMPLATE_DL:
        raise CaseError("deadline operations need a matter-deadlines board")
    op = args.op
    if op == "add":
        item = artifacts.add_dl(
            instance, case.data,
            args.title or "", args.kind or "",
            owner=args.owner or "",
            due_date=args.due_date or "",
            ob_id=args.ob or "",
            notes=args.notes or "",
        )
    elif op == "set":
        item = artifacts.set_dl(
            instance,
            case.data,
            args.deadline,
            title=args.title,
            kind=args.kind,
            owner=args.owner,
            due_date=args.due_date,
            ob_id=args.ob,
            notes=args.notes,
        )
    elif op == "resolve":
        item = artifacts.resolve_dl(
            instance, args.deadline, args.status or "", args.reason or ""
        )
    else:
        raise CaseError(f"unknown deadline operation {op!r}")
    case.artifact_save(instance, "artifact_deadlines", item["dl_id"])
    return {"dl": item["dl_id"]}


def cmd_intake(args: argparse.Namespace) -> dict:
    case = _case(args.run_id)
    instance = case.artifact_get(args.instance)
    if instance.get("template") != artifacts.TEMPLATE_IN:
        raise CaseError("intake operations need a legal-intake instance")
    op = args.op
    if op == "add":
        request = artifacts.add_in(
            instance, case.data,
            args.title or "", args.description or "",
            args.category or "", args.urgency or "",
            requester=args.requester or "",
            issue_id=args.issue or "",
        )
    elif op == "set":
        request = artifacts.set_in(
            instance,
            case.data,
            args.intake,
            title=args.title,
            description=args.description,
            category=args.category,
            urgency=args.urgency,
            requester=args.requester,
            assignee=args.assignee,
            issue_id=args.issue,
        )
    elif op == "triage":
        request = artifacts.triage_in(
            instance, args.intake, args.assignee or ""
        )
    elif op == "route":
        request = artifacts.route_in(
            instance, args.intake, args.route or "", args.rationale or ""
        )
    elif op == "decide":
        request = artifacts.decide_in(
            instance, args.intake, args.status or "", args.reason or ""
        )
    else:
        raise CaseError(f"unknown intake operation {op!r}")
    case.artifact_save(instance, "artifact_intake", request["in_id"])
    return {"in": request["in_id"]}


def cmd_calendar(args: argparse.Namespace) -> dict:
    case = _case(args.run_id)
    instance = case.artifact_get(args.instance)
    if instance.get("template") != artifacts.TEMPLATE_CAL:
        raise CaseError("calendar operations need an obligation calendar")
    op = args.op
    if op == "add":
        obligation = artifacts.add_ob(
            instance, case.data,
            args.clause or "", args.kind or "", args.title or "",
            due_date=args.due_date or "",
            notice_days=args.notice_days,
            notes=args.notes or "",
        )
    elif op == "set":
        obligation = artifacts.set_ob(
            instance,
            case.data,
            args.ob,
            title=args.title,
            kind=args.kind,
            due_date=args.due_date,
            notice_days=args.notice_days,
            notes=args.notes,
        )
    elif op == "resolve":
        obligation = artifacts.resolve_ob(
            instance, args.ob, args.status or "", args.reason or ""
        )
    else:
        raise CaseError(f"unknown calendar operation {op!r}")
    case.artifact_save(instance, "artifact_calendar", obligation["ob_id"])
    return {"ob": obligation["ob_id"]}


def cmd_playbook(args: argparse.Namespace) -> dict:
    case = _case(args.run_id)
    instance = case.artifact_get(args.instance)
    if instance.get("template") != artifacts.TEMPLATE_PB:
        raise CaseError("playbook operations need a playbook instance")
    op = args.op
    if op == "add-rule":
        rule = artifacts.add_rule(
            instance, args.topic or "", args.position or "", args.severity or ""
        )
        saved = rule["rule_id"]
    elif op == "set-rule":
        rule = artifacts.set_rule(
            instance,
            args.rule,
            topic=args.topic,
            position=args.position,
            severity=args.severity,
        )
        saved = rule["rule_id"]
    elif op == "retract-rule":
        rule = artifacts.retract_rule(instance, args.rule, args.reason or "")
        saved = rule["rule_id"]
    elif op == "assess":
        deviation = artifacts.assess(
            instance, case.data,
            args.clause or "", args.rule or "",
            args.verdict or "", args.rationale or "",
        )
        saved = deviation["dev_id"]
    else:
        raise CaseError(f"unknown playbook operation {op!r}")
    case.artifact_save(instance, "artifact_playbook", saved)
    return {"saved": saved}


def cmd_contract(args: argparse.Namespace) -> dict:
    case = _case(args.run_id)
    instance = case.artifact_get(args.instance)
    if instance.get("template") != artifacts.TEMPLATE_CT:
        raise CaseError("contract operations need a contract-review instance")
    op = args.op
    if op == "add":
        clause = artifacts.add_clause(
            instance, case.data, ROOT,
            args.document or "", args.page or "", args.quote or "",
            args.heading or "",
        )
    elif op == "set":
        clause = artifacts.set_clause(
            instance,
            args.clause,
            heading=args.heading,
            effect=args.effect,
            risk=args.risk,
            risk_note=args.risk_note,
        )
    elif op == "flag":
        clause = artifacts.flag_clause(
            instance, args.clause, args.status or "", args.note or ""
        )
    elif op == "retract":
        clause = artifacts.retract_clause(instance, args.clause, args.reason or "")
    else:
        raise CaseError(f"unknown contract operation {op!r}")
    case.artifact_save(instance, "artifact_contract", clause["clause_id"])
    return {"clause": clause["clause_id"]}


def cmd_matrix(args: argparse.Namespace) -> dict:
    case = _case(args.run_id)
    instance = case.artifact_get(args.instance)
    if instance.get("template") != artifacts.TEMPLATE_JX:
        raise CaseError("matrix operations need a jurisdiction instance")
    op = args.op
    if op == "add":
        cell = artifacts.add_cell(
            instance, case.data, args.issue or "", args.regime or "", args.layer or ""
        )
    elif op == "set":
        cell = artifacts.set_cell(
            instance,
            case.data,
            args.cell,
            proposition=args.proposition,
            citation=args.citation,
            effective_date=args.effective_date,
            last_checked=args.last_checked,
            caveat=args.caveat,
        )
    elif op == "link":
        cell = artifacts.link_cell_branch(instance, case.data, args.cell, args.branch)
    elif op == "flag":
        cell = artifacts.flag_cell(
            instance, args.cell, args.status or "", args.note or ""
        )
    elif op == "retract":
        cell = artifacts.retract_cell(instance, args.cell, args.reason or "")
    else:
        raise CaseError(f"unknown matrix operation {op!r}")
    case.artifact_save(instance, "artifact_matrix", cell["cell_id"])
    return {"cell": cell["cell_id"]}


def cmd_check(args: argparse.Namespace) -> dict:
    case = _case(args.run_id)
    instance = case.artifact_get(args.instance)
    wanted = [r.strip() for r in (args.rows or "").split(",") if r.strip()]
    rows = instance.get("rows") or []
    if wanted:
        unknown = [r for r in wanted if r not in {row["row_id"] for row in rows}]
        if unknown:
            raise CaseError(f"unknown audit rows: {', '.join(unknown)}")
        scope = set(wanted)
        if any(r != "RC" for r in wanted):
            scope.add("RC")  # the conclusion depends on every finding
        rows = [row for row in rows if row["row_id"] in scope]
    structural = artifacts.audit_structural(ROOT, case.data, rows)
    fake = os.environ.get("LEGAL_AGENT_FAKE_MODEL", "").strip() == "1"
    ctx = None
    if not fake:
        # Provider dependencies are needed only for an actual support assessment.
        from agent_kit import tools
        from agent_kit.client import MissingKeyError, make_client
        try:
            ctx = tools.Context(make_client(), ROOT, args.run_id, case, True)
        except MissingKeyError:
            pass
    if ctx is None:
        allowed = {row["row_id"] for row in artifacts.eligible_rows(rows, structural)}
        reason = ("synthetic demo transport: no live model calls"
                  if fake
                  else "no model client configured")
        semantic = {}
        for row in rows:
            if row["row_id"] in allowed:
                problems = [f"{reason}: support not assessed"]
                outcome = "unavailable"
            else:
                problems = ["citation check did not pass: nothing valid to assess"]
                outcome = "not_run"
            semantic[row["row_id"]] = {
                "outcome": outcome,
                "verdict": None,
                "problems": problems,
                "support": [],
                "verifier": verify_claims.VERIFIER_VERSION,
            }
    else:
        semantic = artifacts.audit_support(ctx, rows, structural)
    changed = artifacts.record_checks(instance, case.data, ROOT, structural, semantic)
    if changed:
        from agent_kit.progress import now

        instance["history"].append(
            {"at": now(), "event": "checked", "detail": ", ".join(changed)}
        )
    defect_events = []
    if instance.get("template") == artifacts.TEMPLATE_AUDIT:
        defect_events = artifacts.refresh_defects(instance, case.data, ROOT)
    case.artifact_save(instance, "artifact_checked", f"changed: {', '.join(changed) or 'none'}")
    return {
        "checked": [row["row_id"] for row in rows],
        "changed": changed,
        "defects": defect_events,
    }


def cmd_revise(args: argparse.Namespace) -> dict:
    case = _case(args.run_id)
    instance = case.artifact_get(args.instance)
    citation = None
    if args.cite is not None:
        parts = args.cite.split(",", 2)
        if len(parts) != 3:
            raise CaseError("--cite needs DOCUMENT,PAGE,QUOTE")
        page: object = parts[1].strip()
        try:
            page = int(page)
        except ValueError:
            pass
        citation = {
            "document_id": parts[0].strip(),
            "page": page,
            "quote": parts[2],
        }
    row = artifacts.revise_row(
        instance,
        case.data,
        ROOT,
        args.row,
        assertion=args.assertion,
        citation=citation,
        index=args.index,
        feedback=args.feedback or "",
    )
    if instance.get("template") == artifacts.TEMPLATE_AUDIT:
        artifacts.refresh_defects(instance, case.data, ROOT)
    case.artifact_save(instance, "artifact_revised", row["row_id"])
    return {"row": row["row_id"], "versions": len(instance["versions"])}


def cmd_mark(args: argparse.Namespace) -> dict:
    case = _case(args.run_id)
    instance = case.artifact_get(args.instance)
    row = artifacts.mark_citation(
        instance, args.row, args.index, args.category,
        note=args.note or "",
    )
    case.artifact_save(instance, "artifact_marked", row["row_id"])
    return {"row": row["row_id"], "marks": len(row.get("marks") or [])}


def cmd_eval_receipt(args: argparse.Namespace) -> dict:
    case = _case(args.run_id)
    instance = case.artifact_get(args.instance)
    try:
        receipt = json.loads(Path(args.receipt).read_text())
    except (OSError, ValueError):
        raise CaseError("--receipt must be a readable JSON file")
    entry = artifacts.record_eval_receipt(instance, receipt)
    case.artifact_save(instance, "artifact_eval_receipt", entry["suite"])
    return {"suite": entry["suite"], "client": entry["client"]}


def cmd_assign(args: argparse.Namespace) -> dict:
    case = _case(args.run_id)
    instance = case.artifact_get(args.instance)
    assignment = review_labels.issue_assignment(
        instance, case.data, args.row)
    case.artifact_save(instance, "artifact_assigned", args.row)
    return assignment


def cmd_label(args: argparse.Namespace) -> dict:
    case = _case(args.run_id)
    instance = case.artifact_get(args.instance)
    label = review_labels.submit_assignment_label(
        instance, token=args.token, verdict=args.verdict,
        rationale=args.rationale or "", actor=args.actor or "",
        origin=args.origin,
        missing_evidence=bool(args.missing_evidence))
    case.artifact_save(instance, "artifact_labeled", label["id"])
    return {"label": label["id"], "row": label["row_id"],
            "verdict": label["verdict"]}


def cmd_adjudicate(args: argparse.Namespace) -> dict:
    case = _case(args.run_id)
    instance = case.artifact_get(args.instance)
    entry = review_labels.adjudicate(
        instance, row_id=args.row, decision=args.decision,
        rationale=args.rationale or "", actor=args.actor or "")
    case.artifact_save(instance, "artifact_adjudicated", args.row)
    return {"row": args.row, "decision": entry["decision"],
            "labels": entry["labels"]}


def _promoted_dir(case: CaseRecord, run_id: str) -> Path:
    dest = case.root / "workspace" / "runs" / run_id / "promoted"
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def _example_path(case: CaseRecord, run_id: str, example: str) -> Path:
    ident = str(example or "").strip()
    if not ident or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-"
                        for c in ident):
        raise CaseError(f"unknown example {example!r}")
    path = _promoted_dir(case, run_id) / f"{ident}.json"
    if not path.is_file():
        raise CaseError(f"unknown example {ident!r}")
    return path


def cmd_promote(args: argparse.Namespace) -> dict:
    from agent_kit import promotion

    case = _case(args.run_id)
    instance = case.artifact_get(args.instance)
    try:
        classes = json.loads(args.classes or "{}")
    except ValueError:
        raise CaseError("classes must be a JSON object") from None
    truth = promotion.preservation_truth(instance, classes)
    record = promotion.promote_example(
        instance, case.data, case.root,
        _promoted_dir(case, args.run_id),
        example_id=args.example_id, role=args.role, truth=truth,
        failure=args.failure or "", behavior=args.behavior or "",
        actor=args.actor or "")
    return {"example": record["id"], "role": record["role"],
            "rows": len(record["truth"])}


def cmd_examples(args: argparse.Namespace) -> dict:
    case = _case(args.run_id)
    case.artifact_get(args.instance)
    dest = _promoted_dir(case, args.run_id)
    examples = []
    for path in sorted(dest.glob("*.json")):
        try:
            record = json.loads(path.read_text())
        except ValueError:
            continue
        if not isinstance(record, dict):
            continue
        examples.append({
            "id": record.get("id"), "role": record.get("role"),
            "failure": record.get("failure"),
            "behavior": record.get("behavior"),
            "rows": len(record.get("truth") or {}),
            "exposure": record.get("exposure") or [],
            "promoted_from": record.get("promoted_from") or {},
        })
    return {"examples": examples}


def cmd_replay(args: argparse.Namespace) -> dict:
    from eval import artifact_run, review_run

    case = _case(args.run_id)
    case.artifact_get(args.instance)
    path = _example_path(case, args.run_id, args.example)
    record = json.loads(path.read_text())
    catalogue = {record["id"]: record}
    failed = sorted(
        f"finding:{row_id}"
        for row_id, expected in (record.get("truth") or {}).items()
        if expected.get("semantic") == "failed")

    def mark(_text: str) -> list:
        return [{"unit": unit, "reason": "review replay: stored truth"}
                for unit in failed]

    maker = lambda root, name: review_run.materialize(  # noqa: E731
        root, name, catalogue)
    report = artifact_run.run_case(
        record["id"], cases=catalogue, materialize=maker,
        unsupported=mark)
    record.setdefault("exposure", []).append({
        "use": "replayed", "complete": bool(report.get("complete")),
    })
    path.write_text(json.dumps(record, indent=1) + "\n")
    return {
        "example": record["id"],
        "complete": bool(report.get("complete")),
        "rows": report.get("rows") or {},
        "classes": report.get("classes") or {},
    }


def cmd_note(args: argparse.Namespace) -> dict:
    case = _case(args.run_id)
    instance = case.artifact_get(args.instance)
    entry = artifacts.add_note(
        instance, args.text or "", args.author or "")
    case.artifact_save(instance, "artifact_noted", entry["id"])
    return {"note": entry["id"], "author": entry["author"]}


def cmd_review(args: argparse.Namespace) -> dict:
    case = _case(args.run_id)
    instance = case.artifact_get(args.instance)
    try:
        snapshot = json.loads(args.snapshot)
    except ValueError:
        raise CaseError("--snapshot must be the JSON snapshot the reviewer inspected")
    review = artifacts.review_instance(
        instance,
        case.data,
        "approved" if args.approve else "changes_requested",
        args.notes or "",
        args.qualify or [],
        snapshot,
    )
    case.artifact_save(instance, "artifact_reviewed", review["decision"])
    return {"decision": review["decision"]}


def cmd_diff(args: argparse.Namespace) -> dict:
    case = _case(args.run_id)
    instance = case.artifact_get(args.instance)
    template = instance.get("template")
    if template != artifacts.TEMPLATE_AUDIT:
        diff = artifacts.handlers(template)["diff"]
        return diff(instance, args.old, args.new)
    return artifacts.diff_versions(instance, args.old, args.new)


def cmd_export(args: argparse.Namespace) -> dict | str:
    case = _case(args.run_id)
    instance = case.artifact_get(args.instance)
    template = instance.get("template")
    if template != artifacts.TEMPLATE_AUDIT:
        export = artifacts.handlers(template)
        if export["export_takes_root"]:
            report = export["export"](
                instance, case.data, ROOT, args.version
            )
        else:
            report = export["export"](instance, case.data, args.version)
    else:
        report = artifacts.export_instance(instance, case.data, ROOT, args.version)
    report.setdefault("data_mode", artifacts.data_mode(instance))
    if getattr(args, "format", "json") == "md":
        return artifacts.export_markdown(report)
    return report


# Option specs per op verb, in --help order: ((name, default), ...),
# (store_true flags ...), {name: dest} overrides. Generated from the
# hand-written parsers; behavior identical.
_VERB_OPTIONS = {
    "discovery": ((("request", ''), ("title", None), ("description", None), ("direction", None), ("category", None), ("due_date", None), ("counterpart", None), ("received", ''), ("summary", ''), ("documents", ''), ("notes", ''), ("status", ''), ("reason", '')), (), {}),
    "horizon": ((("watch", ''), ("title", None), ("summary", None), ("regime", None), ("source_desc", None), ("observed", None), ("effective", None), ("checked", None), ("relevance", None), ("assignee", None), ("cell", None), ("status", ''), ("reason", '')), (), {}),
    "governance": ((("use", ''), ("title", None), ("description", None), ("data", None), ("system", None), ("risk", None), ("owner", None), ("conditions", None), ("intake", None), ("status", ''), ("reason", '')), (), {}),
    "entity": ((("entity", None), ("filing", ''), ("name", None), ("regime", None), ("identifier", None), ("title", None), ("kind", None), ("due_date", None), ("period", None), ("notes", None), ("status", ''), ("reason", '')), (), {}),
    "termsheet": ((("sheet", None), ("term", ''), ("label", None), ("party", None), ("date", None), ("notes", None), ("topic", None), ("proposition", None), ("text", ''), ("status", ''), ("reason", '')), (), {}),
    "patent": ((("subject", None), ("element", ''), ("label", None), ("kind", None), ("ref", None), ("notes", None), ("claim_ref", None), ("text", None), ("reading", ''), ("status", ''), ("reason", '')), (), {}),
    "qa": ((("question_id", ''), ("question", None), ("asker", None), ("topic", None), ("assignee", None), ("due_date", None), ("answer", ''), ("documents", ''), ("status", ''), ("reason", '')), (), {}),
    "spend": ((("vendor", None), ("engagement", None), ("invoice", ''), ("name", None), ("contact", None), ("title", None), ("budget", None), ("currency", None), ("label", None), ("amount", None), ("notes", None), ("status", ''), ("reason", '')), ("rebind",), {}),
    "deadlines": ((("deadline", ''), ("title", None), ("kind", None), ("owner", None), ("due_date", None), ("ob", None), ("notes", None), ("status", ''), ("reason", '')), (), {}),
    "intake": ((("intake", ''), ("title", None), ("description", None), ("category", None), ("urgency", None), ("requester", None), ("assignee", None), ("issue", None), ("route", ''), ("rationale", ''), ("status", ''), ("reason", '')), (), {}),
    "calendar": ((("ob", ''), ("clause", ''), ("kind", None), ("title", None), ("due_date", None), ("notice_days", None), ("notes", None), ("status", ''), ("reason", '')), (), {}),
    "playbook": ((("rule", ''), ("topic", None), ("position", None), ("severity", None), ("clause", ''), ("verdict", ''), ("rationale", ''), ("reason", '')), (), {}),
    "contract": ((("clause", ''), ("document", ''), ("page", ''), ("quote", ''), ("heading", None), ("effect", None), ("risk", None), ("risk_note", None), ("status", ''), ("note", ''), ("reason", '')), (), {}),
    "matrix": ((("cell", ''), ("issue", ''), ("regime", ''), ("layer", ''), ("proposition", None), ("citation", None), ("effective_date", None), ("last_checked", None), ("caveat", None), ("branch", ''), ("status", ''), ("note", ''), ("reason", '')), (), {}),
    "board": ((("event", ''), ("fact", ''), ("span", ''), ("date", None), ("label", None), ("issues", ''), ("facts", ''), ("uncertainty", None), ("owner", None), ("with", ''), ("kind", ''), ("reason", '')), (), {"with": "with_"}),
}


def _verb_parser(sub, verb: str):
    """One op-verb parser: run_id + instance + --op + its options."""
    p = sub.add_parser(verb)
    p.add_argument("run_id")
    p.add_argument("instance")
    p.add_argument("--op", required=True)
    opts, flags, dests = _VERB_OPTIONS[verb]
    for name, default in opts:
        kw = {"dest": dests[name]} if name in dests else {}
        p.add_argument(f"--{name}", default=default, **kw)
    for name in flags:
        p.add_argument(f"--{name}", action="store_true")
    return p


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="verb", required=True)
    sub.add_parser("demo")
    sub.add_parser("gallery")
    p = sub.add_parser("open")
    p.add_argument("run_id")
    p.add_argument("--title", default="")
    p.add_argument("--template", default="")
    p.add_argument("--source", default="")
    _verb_parser(sub, "discovery")
    _verb_parser(sub, "horizon")
    _verb_parser(sub, "governance")
    _verb_parser(sub, "entity")
    _verb_parser(sub, "termsheet")
    _verb_parser(sub, "patent")
    _verb_parser(sub, "qa")
    _verb_parser(sub, "spend")
    _verb_parser(sub, "deadlines")
    _verb_parser(sub, "intake")
    _verb_parser(sub, "calendar")
    _verb_parser(sub, "playbook")
    _verb_parser(sub, "contract")
    _verb_parser(sub, "matrix")
    _verb_parser(sub, "board")
    p = sub.add_parser("show")
    p.add_argument("run_id")
    p.add_argument("instance")
    p = sub.add_parser("check")
    p.add_argument("run_id")
    p.add_argument("instance")
    p.add_argument("--rows", default="")
    p = sub.add_parser("revise")
    p.add_argument("run_id")
    p.add_argument("instance")
    p.add_argument("--row", required=True)
    p.add_argument("--assertion", default=None)
    p.add_argument("--cite", default=None)
    p.add_argument("--index", type=int, default=0)
    p.add_argument("--feedback", default="")
    p = sub.add_parser("mark")
    p.add_argument("run_id")
    p.add_argument("instance")
    p.add_argument("--row", required=True)
    p.add_argument("--index", type=int, default=0)
    p.add_argument("--category", required=True,
                   choices=("wrong", "incomplete", "irrelevant"))
    p.add_argument("--note", default="")
    p = sub.add_parser("assign")
    p.add_argument("run_id")
    p.add_argument("instance")
    p.add_argument("--row", required=True)
    p = sub.add_parser("label")
    p.add_argument("run_id")
    p.add_argument("instance")
    p.add_argument("--token", required=True)
    p.add_argument("--verdict", required=True,
                   choices=("supported", "contradicted", "insufficient"))
    p.add_argument("--rationale", default="")
    p.add_argument("--actor", default="")
    p.add_argument("--origin", required=True,
                   choices=("human", "simulated"))
    p.add_argument("--missing-evidence", action="store_true")
    p = sub.add_parser("note")
    p.add_argument("run_id")
    p.add_argument("instance")
    p.add_argument("--text", default="")
    p.add_argument("--author", default="")
    p = sub.add_parser("promote")
    p.add_argument("run_id")
    p.add_argument("instance")
    p.add_argument("--example-id", required=True)
    p.add_argument("--actor", default="")
    p.add_argument("--role", required=True,
                   choices=("development", "regression", "sealed"))
    p.add_argument("--failure", default="")
    p.add_argument("--behavior", default="")
    p.add_argument("--classes", default="{}")
    p = sub.add_parser("examples")
    p.add_argument("run_id")
    p.add_argument("instance")
    p = sub.add_parser("replay")
    p.add_argument("run_id")
    p.add_argument("instance")
    p.add_argument("--example", required=True)
    p = sub.add_parser("adjudicate")
    p.add_argument("run_id")
    p.add_argument("instance")
    p.add_argument("--row", required=True)
    p.add_argument("--decision", required=True,
                   choices=("upheld", "corrected", "unresolved"))
    p.add_argument("--rationale", default="")
    p.add_argument("--actor", default="")
    p = sub.add_parser("eval-receipt")
    p.add_argument("run_id")
    p.add_argument("instance")
    p.add_argument("--receipt", required=True)
    p = sub.add_parser("review")
    p.add_argument("run_id")
    p.add_argument("instance")
    p.add_argument("--approve", action="store_true")
    p.add_argument("--request-changes", action="store_true")
    p.add_argument("--snapshot", required=True)
    p.add_argument("--notes", default="")
    p.add_argument("--qualify", action="append", default=[])
    p = sub.add_parser("diff")
    p.add_argument("run_id")
    p.add_argument("instance")
    p.add_argument("old", type=int)
    p.add_argument("new", type=int)
    p = sub.add_parser("export")
    p.add_argument("run_id")
    p.add_argument("instance")
    p.add_argument("--version", type=int, default=None)
    p.add_argument("--format", choices=("json", "md"), default="json")
    args = parser.parse_args(argv)
    try:
        if args.verb == "review" and args.approve == args.request_changes:
            raise CaseError("review needs exactly one of --approve or --request-changes")
        result = {
            "demo": cmd_demo,
            "gallery": cmd_gallery,
            "open": cmd_open,
            "board": cmd_board,
            "matrix": cmd_matrix,
            "contract": cmd_contract,
            "playbook": cmd_playbook,
            "calendar": cmd_calendar,
            "intake": cmd_intake,
            "deadlines": cmd_deadlines,
            "discovery": cmd_discovery,
            "horizon": cmd_horizon,
            "governance": cmd_governance,
            "entity": cmd_entity,
            "termsheet": cmd_termsheet,
            "patent": cmd_patent,
            "qa": cmd_qa,
            "spend": cmd_spend,
            "show": cmd_show,
            "check": cmd_check,
            "revise": cmd_revise,
            "mark": cmd_mark,
            "note": cmd_note,
            "promote": cmd_promote,
            "examples": cmd_examples,
            "replay": cmd_replay,
            "adjudicate": cmd_adjudicate,
            "assign": cmd_assign,
            "label": cmd_label,
            "eval-receipt": cmd_eval_receipt,
            "review": cmd_review,
            "diff": cmd_diff,
            "export": cmd_export,
        }[args.verb](args)
    except (CaseError, MissingSecretError, RuntimeError, FileNotFoundError) as error:
        sys.exit(str(error))
    if isinstance(result, str):
        print(result, end="" if result.endswith("\n") else "\n")
    else:
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
