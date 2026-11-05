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
import os
import tempfile
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
    update_research_tree,
    validate_decomposition,
    work_status,
)

BASE_TOOLS = [
    "set_checklist",
    "update_plan",
    "update_research_tree",
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
        update_research_tree,
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
    from ..argument_validation import ArgumentError, parse, validate

    name, raw = _field(call, "name"), _field(call, "arguments")
    try:
        args = parse(raw)
    except ArgumentError as error:
        return error.result()
    root = Path(root)
    verification = bool(load_progress(root, run_id).get("verification"))
    if name not in names(verification):
        return {"error": f"unknown tool: {name!r}"}
    if op_seq is not None:
        recorded = _pending_result(root, run_id, op_seq, name, args)
        if recorded is not None:
            return recorded
    try:
        validate(args, MODULES[name].DEFINITION.get("parameters", {}), tool=name)
    except ArgumentError as error:
        return error.result()
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
    from ..runtime_ownership import CheckpointError, run_folder

    path = run_folder(root, run_id) / "pending_op.json"
    if path.is_symlink():
        raise CheckpointError("Pending operation must not be a symlink.")
    return path


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
    from ..runtime_ownership import CheckpointError

    _validate_operation_seq(op_seq)
    path = _pending_path(root, run_id)
    try:
        slot = json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError, UnicodeError) as error:
        raise CheckpointError("Pending operation is unreadable; refusing to replay it.") from error
    if (not isinstance(slot, dict) or
            ("run_id" in slot and slot["run_id"] != run_id) or
            isinstance(slot.get("seq"), bool) or not isinstance(slot.get("seq"), int) or slot["seq"] <= 0 or
            not isinstance(slot.get("name"), str) or not isinstance(slot.get("args"), str) or
            not isinstance(slot.get("result"), dict)):
        raise CheckpointError("Pending operation has invalid identity or structure.")
    if slot["seq"] == op_seq and (slot["name"] != name or slot["args"] != _stable_args(args)):
        raise CheckpointError("Pending operation conflicts with this call at the same sequence.")
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
    _validate_operation_seq(op_seq)
    path = _pending_path(root, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"run_id": run_id, "seq": op_seq, "name": name,
                          "args": _stable_args(args), "result": result}, allow_nan=False)
    fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _validate_operation_seq(op_seq: int) -> None:
    from ..runtime_ownership import CheckpointError

    if isinstance(op_seq, bool) or not isinstance(op_seq, int) or op_seq <= 0:
        raise CheckpointError("Operation sequence must be a positive integer.")


def _field(call: Any, key: str) -> Any:
    return call.get(key) if isinstance(call, dict) else getattr(call, key, None)
