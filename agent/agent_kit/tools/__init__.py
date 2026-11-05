"""The agent's tools, one file per tool: its DEFINITION (what the model sees) and
run(ctx, args) (what happens when the model calls it).

    tools.definitions(root, run_id)        # the tools to offer the model in this run
    result = tools.run(client, root, run_id, call)   # run the tool the model chose
    result.get("status", "running")        # "running", or why the run must stop

BASE_TOOLS are the thirty tools the agent always has. VERIFIER_TOOLS are added for runs
started with VERIFICATION=on, and with it on, set_checklist, analyze_document and
submit_memo also run the checks in agent/verification/ at their fixed points.

A result is what the model sees next. A refused call comes back as {"error": ...} so the
model can correct it. A result carries "status" only when the run must stop:
"answered" (memo filed, verification off), "needs_input" (a person must act),
"awaiting_signoff" (memo passed the checks), or "paused" (a check is not built yet).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openai import OpenAI

from ..case_record import CaseRecord
from ..progress import load_progress
from . import (
    adversarial_review,
    analyze_document,
    assess_authority,
    analyze_element,
    answer_foundational,
    calculate,
    check_signatures,
    decide_approval,
    declare_work_product,
    flag_privileged,
    identify_task,
    list_documents,
    note_context,
    note_source,
    open_branch,
    plan_research,
    plan_workflow,
    record_fact,
    record_facts,
    reclassify_task,
    register_authority,
    request_approval,
    request_evidence,
    resolve_branch,
    resolve_query,
    review_draft,
    scan_document,
    scan_redactions,
    search_corpus,
    search_public_sources,
    fetch_public_source,
    set_checklist,
    set_evidence_needs,
    submit_memo,
    suggest_next_action,
    sweep_issues,
    triage_gap,
    update_plan,
    validate_decomposition,
    work_status,
)

BASE_TOOLS = [
    "set_checklist",
    "update_plan",
    "list_documents",
    "analyze_document",
    "record_fact",
    "record_facts",
    "note_context",
    "note_source",
    "register_authority",
    "assess_authority",
    "open_branch",
    "resolve_branch",
    "plan_research",
    "resolve_query",
    "scan_document",
    "scan_redactions",
    "search_corpus",
    "search_public_sources",
    "fetch_public_source",
    "set_evidence_needs",
    "work_status",
    "sweep_issues",
    "answer_foundational",
    "analyze_element",
    "calculate",
    "adversarial_review",
    "identify_task",
    "reclassify_task",
    "plan_workflow",
    "triage_gap",
    "declare_work_product",
    "validate_decomposition",
    "suggest_next_action",
    "request_approval",
    # decide_approval is implemented but never model-offered: grants
    # and denials come from the operator path only (closeout §6).
    # It stays in MODULES so dispatch exists; names() gates it out
    # of both definitions() and tools.run().
    "submit_memo",
]
VERIFIER_TOOLS = [
    "flag_privileged",
    "request_evidence",
    "check_signatures",
    "review_draft",
]

MODULES: dict[str, Any] = {
    str(m.DEFINITION["name"]): m
    for m in (
        set_checklist,
        update_plan,
        list_documents,
        analyze_document,
        record_fact,
        record_facts,
        note_context,
        note_source,
        reclassify_task,
        register_authority,
        assess_authority,
        open_branch,
        resolve_branch,
        plan_research,
        resolve_query,
        scan_document,
        scan_redactions,
        search_corpus,
        search_public_sources,
        fetch_public_source,
        set_evidence_needs,
        work_status,
        sweep_issues,
        answer_foundational,
        analyze_element,
        calculate,
        adversarial_review,
        identify_task,
        plan_workflow,
        triage_gap,
        declare_work_product,
        validate_decomposition,
        suggest_next_action,
        request_approval,
        decide_approval,
        submit_memo,
        flag_privileged,
        request_evidence,
        check_signatures,
        review_draft,
    )
}
DEFINITIONS: dict[str, dict] = {
    name: module.DEFINITION for name, module in MODULES.items()
}


@dataclass
class Context:
    """What a tool gets besides its arguments.

    actor is server-derived, never model-supplied: tools.run (the
    model path) always carries "model". Operator-only tools refuse
    model actors; the trusted operator path (server-side code
    calling case methods directly, or an explicit operator
    Context) carries the human identity instead.
    """

    client: OpenAI
    root: Path
    run_id: str
    case: CaseRecord
    verification: bool
    actor: str = "model"


def names(verification: bool) -> list:
    return BASE_TOOLS + VERIFIER_TOOLS if verification else list(BASE_TOOLS)


def definitions(root: Path, run_id: str) -> list:
    """The tool definitions to offer the model in this run."""
    verification = bool(load_progress(root, run_id).get("verification"))
    return [DEFINITIONS[name] for name in names(verification)]


def page_arg(tool: str, field: str, value: Any) -> int:
    """One integer page from model args, or a clean refusal.

    Accepts ints and digit strings; bools, floats, and anything
    else refuse with CaseError instead of silently coercing to
    the wrong page (True → 1) or leaking int()'s ValueError.
    """
    from ..case_record import CaseError

    if isinstance(value, bool):
        raise CaseError(f"{tool} needs an integer page for {field}")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    raise CaseError(f"{tool} needs an integer page for {field}")


def run(
    client: OpenAI, root: Path, run_id: str, call: Any, op_seq: int | None = None
) -> dict:
    """Run the tool the model chose (an item of response.output, or the same as a dict).

    op_seq is the 1-based tool-call sequence number within the run's
    conversation. When given, a committed non-error result is recorded
    in a pending-operation slot before returning; if the run crashed
    after the commit but before its conversation checkpoint, the
    resumed run's identical call at the same sequence number gets the
    recorded result instead of executing twice. Error results are
    never recorded: transient failures must stay retryable, so tools
    must validate before committing.
    """
    name, raw = _field(call, "name"), _field(call, "arguments") or "{}"
    try:
        args = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except (TypeError, ValueError) as error:
        return {"error": f"tool arguments are not valid JSON: {error}"}
    if not isinstance(args, dict):
        return {"error": "tool arguments must be a JSON object"}
    root = Path(root)
    if op_seq is not None:
        recorded = _pending_result(root, run_id, op_seq, name, args)
        if recorded is not None:
            return recorded
    verification = bool(load_progress(root, run_id).get("verification"))
    if name not in names(verification):
        return {"error": f"unknown tool: {name!r}"}
    ctx = Context(client, root, run_id, CaseRecord(root, run_id), verification)
    try:
        result = MODULES[name].run(ctx, args)
    except NotImplementedError as error:  # a check that is not built yet: stop the run
        return {"error": str(error), "status": "paused"}
    except Exception as error:  # the model reads the error and tries again
        return {"error": str(error) or type(error).__name__}
    result = result if isinstance(result, dict) else {"result": result}
    if op_seq is not None and "error" not in result:
        _record_pending(root, run_id, op_seq, name, args, result)
    return result


def _pending_path(root: Path, run_id: str) -> Path:
    return Path(root) / "workspace" / "runs" / run_id / "pending_op.json"


def pending_op_path(root: Path, run_id: str) -> Path:
    """Where this run's half-applied-operation record lives (audit reads it)."""
    return _pending_path(root, run_id)


def _stable_args(args: dict) -> str:
    try:
        return json.dumps(args, sort_keys=True, default=str)
    except TypeError:
        return str(args)


def _pending_result(
    root: Path, run_id: str, op_seq: int, name: str, args: dict
) -> dict | None:
    """The recorded result when this exact call already committed, else None."""
    try:
        slot = json.loads(_pending_path(root, run_id).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(slot, dict):
        return None
    if (
        slot.get("seq") == op_seq
        and slot.get("name") == name
        and slot.get("args") == _stable_args(args)
        and isinstance(slot.get("result"), dict)
    ):
        return dict(slot["result"])
    return None


def _record_pending(
    root: Path, run_id: str, op_seq: int, name: str, args: dict, result: dict
) -> None:
    """Durably record one committed result (atomic write; single slot).

    Stale slots are inert: the sequence number only matches a call the
    conversation checkpoint has not absorbed yet, so no clearing pass
    is needed and a crash between writes cannot confuse recovery.
    """
    path = _pending_path(root, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(
            {
                "seq": op_seq,
                "name": name,
                "args": _stable_args(args),
                "result": result,
            }
        )
    )
    tmp.replace(path)


def _field(call: Any, key: str) -> Any:
    return call.get(key) if isinstance(call, dict) else getattr(call, key, None)
