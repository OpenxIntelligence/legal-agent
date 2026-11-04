"""The Legal Agent: the agentic loop.

run_agent works the case; resume_run continues a run that stopped.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openai import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    OpenAI,
    RateLimitError,
)

from agent_kit import settings, tools
from agent_kit.case_record import ROOT_TASK, CaseRecord
from agent_kit.progress import (
    ContextMeasureError,
    checkpoint_steering,
    context_bytes,
    load_progress,
    new_run,
    resume,
    save_progress,
    tokens_spent,
)
from agent_kit.scheduler import attempt_outcome

STALL_REPEATS = 3

#: Turns left at which a run without any adversarial review is reminded to
#: review before filing. Six covers four panel lenses plus the filing turn,
#: with one turn of slack.
REVIEW_REMINDER_TURNS_LEFT = 6

REVIEW_REMINDER = (
    "Six or fewer turns remain. Before filing, call adversarial_review "
    "once per lens (evidence, law, procedure, strategy) until the panel "
    "report shows no empty seats."
)

#: Provider failures worth one deliberate resume-and-retry, once the SDK's
#: own retries have already run. Anything else is a fatal provider error.
TRANSIENT_PROVIDER_ERRORS = (APIConnectionError, APITimeoutError, RateLimitError)


def provider_stop(error: APIError) -> tuple[str, str]:
    """The (stop_reason, next_action) for a failed model call."""
    if isinstance(error, TRANSIENT_PROVIDER_ERRORS):
        return (
            "provider_transient",
            "Resume to retry the model call; the failure may clear.",
        )
    return (
        "provider_error",
        "Inspect the error; resume only after the cause is addressed.",
    )


def steering_messages(*, root: Path, run_id: str, messages: list | None = None) -> list:
    """User-role messages steering this slice from pending directives.

    Called at slice starts (fresh runs and resumes): every
    proposed/acknowledged directive not yet delivered to this
    run becomes one attributed user message, oldest first, and
    delivery is recorded so a later resume never repeats it.
    A directive submitted mid-slice lands on the next resume —
    slices are the safe interruption boundary. Delivery never
    advances the directive lifecycle (no ack/apply) and never
    approves, files, or widens authorization.
    """
    case = CaseRecord(Path(root), run_id)
    pending = case.pending_directives(run_id)
    if not pending:
        return []
    saved = load_progress(root, run_id)
    delivered = set(saved.get("steering_directive_ids") or [])
    fresh = [d for d in pending if d["id"] not in delivered]
    additions = [
        {
            "role": "user",
            "content": (
                f"Steering directive {d['id']} ({d['kind']}) "
                f"from {d['author'].get('type', 'human')}:"
                f"{d['author'].get('id', '?')}\n{d['body_md']}"
            ),
        }
        for d in fresh
    ]
    # A crash before this write leaves directives pending. A crash after it
    # leaves their payload in the saved conversation; the IDs prevent a second
    # insertion even if acknowledging the case record never completed.
    if fresh:
        base = list(saved["messages"] if messages is None else messages)
        checkpoint_steering(root, run_id, base + additions, [d["id"] for d in fresh])
    case.mark_directives_injected([d["id"] for d in pending], run_id)
    return additions


def run_agent(*, root: Path, client: OpenAI, turns: int = 30) -> dict:
    settings.validate_runtime_config()
    root = Path(root)
    instructions = Path(__file__).with_name("system_instructions.md").read_text()
    objective = (settings.case_dir(root) / "objective.md").read_text()
    run_id = new_run(root, instructions=instructions, objective=objective)
    _begin_slice(root, run_id, turns)
    messages: list = [{"role": "user", "content": objective}]
    messages.extend(steering_messages(root=root, run_id=run_id, messages=messages))
    progress = _run_turns(
        root=root,
        client=client,
        run_id=run_id,
        messages=messages,
        instructions=instructions,
        turns=turns,
    )
    _commit_slice(root, run_id, progress)
    return progress


def resume_run(*, root: Path, run_id: str, client: OpenAI, turns: int = 15) -> dict:
    settings.validate_runtime_config()
    root = Path(root)
    step = resume(root, run_id)
    if step["message"] is None:
        data = load_progress(root, run_id)
        data["note"] = step["note"]
        return data
    _begin_slice(root, run_id, turns)
    saved = load_progress(root, run_id)
    messages: list = list(saved["messages"])
    instructions = (
        Path(root) / "workspace" / "runs" / run_id / "instructions.md"
    ).read_text()
    messages.append({"role": "user", "content": step["message"]})
    messages.extend(steering_messages(root=root, run_id=run_id, messages=messages))
    progress = _run_turns(
        root=root,
        client=client,
        run_id=run_id,
        messages=messages,
        instructions=instructions,
        turns=turns,
    )
    _commit_slice(root, run_id, progress)
    return progress


def _begin_slice(root: Path, run_id: str, turns: int) -> None:
    """Open this slice's attempt on the root task (see begin_attempt)."""
    record = CaseRecord(Path(root), run_id)
    record.ensure_root_task()
    record.begin_attempt(ROOT_TASK, worker="loop", allocated_turns=turns)


def _commit_slice(root: Path, run_id: str, progress: dict) -> None:
    """Close the slice's attempt with the outcome its stop maps to."""
    record = CaseRecord(Path(root), run_id)
    status = progress.get("status", "running")
    stop = (progress.get("notes") or {}).get("stop_reason") or ""
    outcome = attempt_outcome(status, stop)
    summary = f"slice ended {status}" + (f" ({stop})" if stop else "")
    record.commit_attempt(
        ROOT_TASK,
        status=outcome,
        summary=summary,
        stop_reason=stop,
    )


def _run_turns(
    *,
    root: Path,
    client: OpenAI,
    run_id: str,
    messages: list,
    instructions: str,
    turns: int,
) -> dict:
    last_call: tuple | None = None
    repeats = 0
    budget = settings.run_token_budget()
    context_cap = settings.max_context_bytes()
    for index in range(turns):
        # Enforcement boundary: the budget is checked between turns, before the
        # next model call — not before the tool/verify calls inside a turn.
        # A turn already in flight can therefore overshoot the budget by up to
        # one agent call plus its tool calls (see the QA controller note on
        # pre-checking repair calls).
        if budget is not None:
            spent = tokens_spent(root, run_id)
            reserve = settings.synthesis_reserve_tokens()
            if spent >= budget:
                save_progress(
                    root,
                    run_id,
                    messages,
                    status="paused",
                    budget_exceeded=(
                        f"token budget exceeded: {spent} of {budget} tokens spent."
                    ),
                    stop_reason="budget_exhausted",
                    stop_action=(
                        "Raise RUN_TOKEN_BUDGET to continue this run, "
                        "or review the partial work."
                    ),
                )
                return load_progress(root, run_id)
            if spent + reserve >= budget:
                save_progress(
                    root,
                    run_id,
                    messages,
                    status="paused",
                    budget_exceeded=(
                        f"token budget reached the synthesis reserve: {spent} of "
                        f"{budget} tokens spent, {reserve} held for synthesis "
                        "and review."
                    ),
                    stop_reason="synthesis_reserve_reached",
                    stop_action=(
                        "Raise RUN_TOKEN_BUDGET to continue gathering evidence, "
                        "or review the partial work."
                    ),
                )
                return load_progress(root, run_id)
        try:
            size = context_bytes(messages)
        except ContextMeasureError as error:
            save_progress(
                root,
                run_id,
                messages,
                status="paused",
                context_exceeded=(
                    f"context size unknown: {error} Start a new run."
                ),
                stop_reason="context_unknown",
                stop_action="Start a new run; the saved context cannot be measured.",
            )
            return load_progress(root, run_id)
        if size > context_cap:
            save_progress(
                root,
                run_id,
                messages,
                status="paused",
                context_exceeded=(
                    f"context too large: {size} of {context_cap} bytes."
                ),
                stop_reason="context_exceeded",
                stop_action="Raise MAX_CONTEXT_BYTES to continue, or start a new run.",
            )
            return load_progress(root, run_id)
        is_last = index == turns - 1
        if is_last:
            messages.append(
                {
                    "role": "user",
                    "content": "This is your last turn. Finish with submit_memo.",
                }
            )
        if (
            turns - index <= REVIEW_REMINDER_TURNS_LEFT
            and not _review_reminded(messages)
            and not _has_adversarial_review(root, run_id)
        ):
            messages.append({"role": "user", "content": REVIEW_REMINDER})
        try:
            response = client.responses.create(
                model=settings.model(),
                instructions=instructions,
                input=messages,
                tools=tools.definitions(root, run_id),
                parallel_tool_calls=False,
            )
        except APIError as error:
            detail = str(error) or type(error).__name__
            reason, action = provider_stop(error)
            save_progress(
                root,
                run_id,
                messages,
                status="paused",
                error=detail,
                stall=None,
                stop_reason=reason,
                stop_action=action,
            )
            return load_progress(root, run_id)
        output = getattr(response, "output", None)
        if not isinstance(output, list):
            save_progress(
                root,
                run_id,
                messages,
                status="paused",
                error="malformed model response",
                stall=None,
                stop_reason="malformed_response",
                stop_action="Resume to let the model try again.",
            )
            return load_progress(root, run_id)
        messages.extend(output)
        call = _function_call(output)
        if call is None:
            messages.append(
                {
                    "role": "user",
                    "content": "Call exactly one tool per turn. Continue with a tool call.",
                }
            )
            last_call, repeats = None, 0
            status = "paused" if is_last else "running"
            save_progress(
                root,
                run_id,
                messages,
                response,
                status=status,
                stall=None,
                stop_reason="no_tool_call" if is_last else None,
                stop_action=(
                    "Resume with more turns to continue." if is_last else None
                ),
            )
            if is_last:
                return load_progress(root, run_id)
            continue
        op_seq = (
            sum(
                1
                for message in messages
                if isinstance(message, dict)
                and message.get("type") == "function_call_output"
            )
            + 1
        )
        result = tools.run(client, root, run_id, call, op_seq=op_seq)
        call_id = call.get("call_id") if isinstance(call, dict) else getattr(call, "call_id", None)
        messages.append(
            {
                "type": "function_call_output",
                "call_id": call_id,
                "output": json.dumps(result, default=str),
            }
        )
        signature = (_call_signature(call), _stable(result))
        if signature == last_call:
            repeats += 1
        else:
            last_call, repeats = signature, 1
        status = result.get("status", "running")
        stall = None
        stop_reason = stop_action = None
        if status == "running" and repeats >= STALL_REPEATS:
            status = "paused"
            stall = (
                f"{signature[0][0]} was called {repeats} times in a row with "
                "identical arguments and identical outcomes; "
                "the run is not progressing."
            )
            stop_reason = "stall_no_progress"
            stop_action = (
                "Resume and try a different action "
                "instead of repeating the last one."
            )
        if status == "running" and is_last:
            status = "paused"
            stop_reason = "turn_slice_exhausted"
            stop_action = "Resume with more turns to continue."
        save_progress(
            root,
            run_id,
            messages,
            response,
            status=status,
            stall=stall,
            stop_reason=stop_reason,
            stop_action=stop_action,
        )
        if status != "running":
            return load_progress(root, run_id)
    return load_progress(root, run_id)


def _call_signature(call: Any) -> tuple:
    if isinstance(call, dict):
        name, raw = call.get("name"), call.get("arguments")
    else:
        name, raw = getattr(call, "name", None), getattr(call, "arguments", None)
    return (name, _stable(raw))


def _stable(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, sort_keys=True)
    except TypeError:
        return str(value)


def _review_reminded(messages: list) -> bool:
    """Whether the review reminder is already in the conversation."""
    return any(
        isinstance(message, dict) and message.get("content") == REVIEW_REMINDER
        for message in messages
    )


def _has_adversarial_review(root: Path, run_id: str) -> bool:
    """Whether the run recorded any adversarial review yet."""
    return bool(CaseRecord(Path(root), run_id).data.get("adversarial"))


def _function_call(output: list) -> Any | None:
    for item in output:
        item_type = item.get("type") if isinstance(item, dict) else getattr(item, "type", None)
        if item_type == "function_call":
            return item
    return None
