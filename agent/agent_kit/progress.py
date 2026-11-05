"""A run on disk: the console shows it turn by turn, and a stopped run resumes from it.

    run_id = new_run(root, instructions=..., objective=...)
    messages = [{"role": "user", "content": objective}]
    # each turn: response = client.responses.create(...); messages += response.output;
    #            result = tools.run(...) for the tool the model picked; append its function_call_output
    save_progress(root, run_id, messages, response)                     # end of every turn
    save_progress(root, run_id, messages, response, status="answered")  # the turn that ends the run

    progress = load_progress(root, run_id)  # progress["messages"], ["status"], ["turn"]
    step = resume(root, run_id)             # continue a stopped run: step["message"] for the model, or None
    new_id = branch(client, root, run_id)   # a new run with verification on, from the run's checklist

Everything goes in workspace/runs/<run-id>/progress.json.
"""

from __future__ import annotations

import datetime
import json
import os
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, TYPE_CHECKING

from . import usage
from .runtime_ownership import CheckpointError, run_folder, run_file, validate_checkpoint
from .settings import (
    DEFAULT_MAX_CONTEXT_BYTES,
    ConfigError,
    max_context_bytes,
    model,
    run_token_budget,
    synthesis_reserve_tokens,
    validate_runtime_config,
    verification_enabled,
)

if TYPE_CHECKING:
    from openai import OpenAI

CONTROL = "workspace/control.json"


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def new_run(
    root: Path, *, instructions: str, objective: str, verification: bool | None = None
) -> str:
    """Start a run: a new folder under workspace/runs/. Returns its id.

    verification defaults to VERIFICATION in .env; it is fixed for the run's lifetime.
    """
    if verification is None:
        verification = verification_enabled()
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"run-{stamp}-{uuid.uuid4().hex[:6]}"
    folder = _folder(root, run_id)
    folder.mkdir(parents=True)
    (folder / "instructions.md").write_text(instructions)
    (folder / "objective.md").write_text(objective)
    _write(
        root,
        run_id,
        {
            "run_id": run_id,
            "verification": verification,
            "model": model(),
            "config": config_snapshot(verification),
            "started": now(),
            "status": "running",
            "turn": 0,
            "messages": [],
            "turns": [],
            "notes": {},
        },
    )
    print(
        f"Run {run_id} started · verification {'on' if verification else 'off'} · model {model()}",
        flush=True,
    )
    return run_id


def save_progress(
    root: Path,
    run_id: str,
    messages: list,
    response: Any = None,
    *,
    status: str = "running",
    **notes: Any,
) -> None:
    """End of a turn: save the conversation and the turn's tokens, print one line.

    `status` is "running" until the run stops (answered, paused, needs_input, ...).
    Without `response`, only the status changes (no model call this time).
    Anything in `notes` comes back from load_progress.
    """
    progress = load_progress(root, run_id)
    plain = [_plain(m) for m in messages]
    entry: dict[str, Any] = {"time": now(), "status": status, "end": len(plain)}
    if response is not None:
        output = [_plain(item) for item in response.output]
        before = progress["turns"][-1]["end"] if progress.get("turns") else 0
        start = next(
            (i for i in range(before, len(plain)) if output and plain[i] == output[0]),
            before,
        )
        usage = response.usage
        progress["turn"] += 1
        entry.update(
            turn=progress["turn"],
            saw=start,
            output=start + len(output),
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
        )
    progress.setdefault("turns", []).append(entry)
    progress["notes"].update(notes)
    progress.update(messages=plain, status=status)
    _write(root, run_id, progress)
    print(_line(root, progress, entry), flush=True)
    if status == "running":
        _wait_for_step(root)


def load_progress(root: Path, run_id: str) -> dict:
    """A run's saved progress: messages, status, turn, verification, notes."""
    path = run_file(root, run_id, "progress.json")
    if not path.is_file():
        raise FileNotFoundError(f"no such run: {run_id}")
    try:
        data = json.loads(path.read_text())
    except (ValueError, UnicodeError) as error:
        raise CheckpointError(f"Invalid checkpoint for {run_id}: unreadable JSON.") from error
    return validate_checkpoint(data, run_id)


def config_snapshot(verification: bool | None = None) -> dict:
    """The settings a run starts under, stored with the run for later review.

    Never raises on bad values: they are recorded in "errors" so a snapshot
    taken for review still reports what was configured.
    """
    errors: list[str] = []
    try:
        budget = run_token_budget()
    except ConfigError as error:
        budget = None
        errors.append(str(error))
    try:
        reserve = synthesis_reserve_tokens()
    except ConfigError as error:
        reserve = None
        errors.append(str(error))
    try:
        context_cap = max_context_bytes()
    except ConfigError as error:
        context_cap = DEFAULT_MAX_CONTEXT_BYTES
        errors.append(str(error))
    return {
        "config_version": 3,
        "model": model(),
        "verification": (
            verification if verification is not None else verification_enabled()
        ),
        "run_token_budget": budget,
        "synthesis_reserve_tokens": reserve,
        "max_context_bytes": context_cap,
        "errors": errors,
    }


def tokens_spent(root: Path, run_id: str) -> int:
    """Agent-turn tokens plus ledgered tool-call tokens for a run."""
    try:
        turns = load_progress(root, run_id).get("turns", [])
    except FileNotFoundError:
        return 0
    agent = sum(
        (t.get("input_tokens") or 0) + (t.get("output_tokens") or 0) for t in turns
    )
    ledger = usage.totals(root, run_id)
    return agent + ledger["input_tokens"] + ledger["output_tokens"]


#: Runtime settings compared on resume. Verification is the run's own
#: fixed flag, not ambient configuration, so it is never a "change".
CONFIG_COMPARE_KEYS = (
    "model",
    "run_token_budget",
    "synthesis_reserve_tokens",
    "max_context_bytes",
)


def config_changes(saved: dict) -> dict:
    """Current runtime config vs a saved snapshot: {key: {"was", "now"}}."""
    current = config_snapshot()
    changes = {}
    for key in CONFIG_COMPARE_KEYS:
        was, is_now = (saved or {}).get(key), current.get(key)
        if was != is_now:
            changes[key] = {"was": was, "now": is_now}
    return changes


def resume(root: Path, run_id: str) -> dict:
    """Continue a stopped run: apply what a person did since it stopped.

    Returns {"message": what to tell the model next, or None when there is nothing to
    continue, "status": the run's status, "note": why not}. Approving the memo ends the
    run as done. When the run continues under changed runtime config, the
    change is recorded on the run (progress notes + case audit) and the
    model is told to treat prior judge verdicts as needing reassessment.
    """
    step = _resume_step(root, run_id)
    if step.get("message") is None:
        return step
    progress = load_progress(root, run_id)
    changes = config_changes(progress.get("config") or {})
    if not changes:
        return step
    record = {"at": now(), "changes": changes}
    notes = progress.setdefault("notes", {})
    notes.setdefault("config_changes", []).append(record)
    _write(root, run_id, progress)
    from .case_record import CaseRecord

    CaseRecord(Path(root), run_id)._save(
        "config_changed", "system", {"changes": changes}
    )
    summary = ", ".join(
        f"{key} {detail['was']} → {detail['now']}" for key, detail in changes.items()
    )
    return dict(
        step,
        message=(
            step["message"]
            + f"\nNote: run configuration changed since this run started "
            f"({summary}); treat prior judge verdicts as needing reassessment."
        ),
    )


def _resume_step(root: Path, run_id: str) -> dict:
    """The pre-change resume decision (see resume)."""
    from . import document_text
    from .case_record import CaseRecord

    validate_runtime_config()
    progress = load_progress(root, run_id)
    status = progress["status"]
    case = CaseRecord(Path(root), run_id)
    if status == "paused":
        if progress["notes"].get("branched_from") and not progress["turn"]:
            return _go(BRANCH_MESSAGE)
        over = progress["notes"].get("budget_exceeded")
        if over and _still_over_budget(root, run_id):
            return _stop(
                status, f"{over} Raise RUN_TOKEN_BUDGET to continue this run."
            )
        flooded = progress["notes"].get("context_exceeded")
        if flooded:
            state = _context_state(root, run_id)
            if state == "unknown":
                return _stop(
                    status,
                    f"{flooded} The saved context cannot be serialized to bytes, "
                    "so its size is unknown. Start a new run.",
                )
            if state == "over":
                return _stop(
                    status,
                    f"{flooded} Raise MAX_CONTEXT_BYTES to continue, or start a new run.",
                )
        stall = progress["notes"].get("stall")
        if stall:
            return _go(
                f"{stall} Try a different action instead of repeating the last one."
            )
        message = "You have more turns. Continue the investigation. Submit a memo only when ready; otherwise preserve unresolved work and pause."
        stop = progress["notes"].get("stop_reason")
        if stop:
            action = progress["notes"].get("stop_action") or ""
            message += f" (Last stop: {stop}" + (f" — {action}" if action else "") + ".)"
        return _go(message)
    if status == "needs_input":
        requests = case.data["requests"]
        if not requests:
            return _stop(status, "no request to answer")
        request = requests[-1]
        visible = [d["id"] for d in document_text.list_documents(Path(root))]
        fresh = [d for d in visible if d not in request["visible_ids"]]
        if not fresh and request["status"] != "answered":
            return _stop(
                status, "nothing new: release evidence or answer the request first"
            )
        if request["status"] == "open":
            request = case.answer_request(
                request["id"], f"Released {len(fresh)} new document(s)."
            )
        message = f"A person answered your request {request['id']} ({request['item']}): {request['response']}"
        if fresh:
            message += (
                f"\nReleased documents: {', '.join(fresh)}. "
                "Re-check findings the new documents may affect, and record "
                "anything that cuts against an item with stance opposes."
            )
        return _go(message + "\nContinue the investigation.")
    if status == "awaiting_signoff":
        review = (case.data.get("memo") or {}).get("review")
        if not review:
            return _stop(
                status, "waiting for the attorney to approve or reject the memo"
            )
        if review["decision"] == "approved":
            save_progress(root, run_id, progress["messages"], status="done")
            return _stop("done", "the attorney approved the memo")
        message = f"The attorney rejected the memo: {review['notes']}."
        comments = [
            c for c in (review.get("comments") or []) if isinstance(c, dict)
        ]
        if comments:
            quoted = "; ".join(
                f"[{c.get('target', 'general')}] {c.get('text', '')}"
                for c in comments
            )
            message += f" Reviewer comments: {quoted}."
        return _go(message + " Address the notes and submit a revised memo.")
    return _stop(status, f"run is {status}; nothing to resume")


BRANCH_MESSAGE = (
    "This run continues from your checklist with the verification layer on. "
    "Fix what the check says, then plan, investigate, and file your memo."
)


def branch(client: OpenAI, root: Path, run_id: str) -> str:
    """Start a new run with verification on that continues `run_id` from its first set_checklist call.

    The new run keeps the conversation up to that call, runs the call again (so the
    checklist check refuses a weak checklist with feedback), and stops as "paused":
    resume it to let the agent continue. Returns the new run's id.
    """
    from . import tools

    root = Path(root)
    messages = load_progress(root, run_id)["messages"]
    index = next(
        (
            i
            for i, m in enumerate(messages)
            if m.get("type") == "function_call" and m.get("name") == "set_checklist"
        ),
        None,
    )
    if index is None:
        raise RuntimeError(f"{run_id} never called set_checklist")
    new_id = new_run(
        root,
        instructions=run_file(root, run_id, "instructions.md").read_text(),
        objective=run_file(root, run_id, "objective.md").read_text(),
        verification=True,
    )
    call = messages[index]
    result = tools.run(client, root, new_id, call)
    if (
        result.get("status") == "paused"
    ):  # a check that is not built yet: nothing to continue
        shutil.rmtree(_folder(root, new_id), ignore_errors=True)
        (root / "workspace/case_records" / f"{new_id}.json").unlink(missing_ok=True)
        raise RuntimeError(result["error"])
    output = {
        "type": "function_call_output",
        "call_id": call.get("call_id"),
        "output": json.dumps(result),
    }
    save_progress(
        root,
        new_id,
        messages[: index + 1] + [output],
        status="paused",
        branched_from=run_id,
    )
    return new_id


# ---- helpers ------------------------------------------------------------------------


def _still_over_budget(root: Path, run_id: str) -> bool:
    budget = run_token_budget()
    if budget is None:
        return False
    return (
        tokens_spent(root, run_id) + synthesis_reserve_tokens() >= budget
    )


class ContextMeasureError(RuntimeError):
    """The conversation cannot be serialized, so its byte size is unknown."""


def context_bytes(messages: list) -> int:
    """Request context size in bytes, with SDK objects normalized to plain JSON.

    Raises ContextMeasureError when serialization fails: unknown size is not
    zero size, and callers must say so rather than report 0.
    """
    try:
        return len(json.dumps([_plain(m) for m in messages]).encode())
    except (TypeError, ValueError) as error:
        raise ContextMeasureError(
            f"cannot serialize context to measure bytes: {error}"
        ) from error


def _context_state(root: Path, run_id: str) -> str:
    """Saved context vs the cap: "under", "over", or "unknown"."""
    try:
        size = context_bytes(load_progress(root, run_id).get("messages", []))
    except FileNotFoundError:
        return "under"
    except ContextMeasureError:
        return "unknown"
    return "over" if size > max_context_bytes() else "under"


def _go(message: str) -> dict:
    return {"message": message, "status": "running", "note": ""}


def _stop(status: str, note: str) -> dict:
    return {"message": None, "status": status, "note": note}


def _folder(root: Path, run_id: str) -> Path:
    return run_folder(root, run_id)


def checkpoint_steering(root: Path, run_id: str, messages: list, directive_ids: list[str]) -> None:
    """Persist delivery and its payload together before acknowledging the inbox.

    This is not a completed turn and must not consume step-mode credits.
    The runner owns writes to progress.json for the duration of a slice.
    """
    if not isinstance(directive_ids, list) or not directive_ids or any(
        not isinstance(item, str) or not item.strip() for item in directive_ids
    ) or len(directive_ids) != len(set(directive_ids)):
        raise CheckpointError("Steering receipt IDs must be unique non-empty strings.")
    progress = load_progress(root, run_id)
    progress["messages"] = _plain(messages)
    delivered = progress.setdefault("steering_directive_ids", [])
    delivered.extend(item for item in directive_ids if item not in delivered)
    _write(root, run_id, progress)


def _write(root: Path, run_id: str, progress: dict) -> None:
    validate_checkpoint(progress, run_id)
    path = run_file(root, run_id, "progress.json")
    # Serialize before touching the existing checkpoint. Unique temporary names
    # also prevent a stale leftover/symlink from redirecting checkpoint writes.
    payload = json.dumps(progress, indent=1, allow_nan=False)
    fd, temporary = tempfile.mkstemp(prefix=".progress-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _plain(value: Any) -> Any:
    """SDK objects (such as the items of response.output) as plain JSON values."""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def _line(root: Path, progress: dict, entry: dict) -> str:
    """The terminal line for one saved turn."""
    run_id, status = progress["run_id"], entry["status"]
    if "turn" not in entry:
        return f"Run {run_id}: {status}"
    messages = progress["messages"]
    call = next(
        (
            m
            for m in messages[entry["saw"] : entry["output"]]
            if m.get("type") == "function_call"
        ),
        None,
    )
    if call is None:
        did = "no tool call (the model answered in text)"
    else:
        output = next(
            (
                m.get("output")
                for m in messages[entry["output"] : entry["end"]]
                if m.get("type") == "function_call_output"
                and m.get("call_id") == call.get("call_id")
            ),
            "",
        )
        did = f"{call.get('name')} → {_summary(output)}"
    line = f"Turn {entry['turn']}: {did} · facts {_facts(root, run_id)} · {status}"
    if status != "running":
        line += f"\nRun {run_id} stopped: {status} after {progress['turn']} turns"
    return line


def _summary(output: Any) -> str:  # noqa: C901
    try:
        result = json.loads(output) if isinstance(output, str) else output
    except ValueError:
        return str(output)[:80]
    if not isinstance(result, dict):
        return str(result)[:80]
    if "error" in result:
        return "refused: " + str(result["error"])[:80]
    documents = result.get("documents")
    if documents and isinstance(documents[0], dict) and "evidence" in documents[0]:
        records = [e for d in documents for e in d["evidence"]]
        return f"{len(records)} quotes, {sum(e.get('verified', False) for e in records)} verified"
    for key, text in (
        ("documents", "{n} documents"),
        ("checklist", "checklist set ({n} items)"),
        ("plan", "plan saved ({n} steps)"),
    ):
        if key in result:
            return text.format(n=len(result[key]))
    if "recorded" in result:
        return f"fact {result['recorded'].get('id')} recorded"
    if result.get("escalated"):
        return "escalated to a person"
    if result.get("filed"):
        return f"memo filed ({result.get('memo')})"
    if "verdict" in result:
        return f"signature {result['verdict']}"
    if "flagged" in result:
        return "flagged confidential"
    return "ok"


def _facts(root: Path, run_id: str) -> int:
    record = Path(root) / "workspace/case_records" / f"{run_id}.json"
    try:
        return len(json.loads(record.read_text()).get("facts", []))
    except (OSError, ValueError):
        return 0


def _wait_for_step(root: Path) -> None:
    """Step mode (ticked in the console): wait for 'Next turn' before the next turn."""
    path = Path(root) / CONTROL
    announced = False
    while True:
        try:
            control = json.loads(path.read_text())
        except (OSError, ValueError):
            return
        if not control.get("step"):
            if control.get("waiting"):
                _write_control(path, dict(control, waiting=False))
            return
        if control.get("credits", 0) > 0:
            _write_control(
                path, dict(control, credits=control["credits"] - 1, waiting=False)
            )
            return
        if not announced:
            _write_control(path, dict(control, waiting=True))
            print("Step mode: waiting for 'Next turn' in the console", flush=True)
            announced = True
        time.sleep(0.3)


def _write_control(path: Path, control: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(control))
    tmp.replace(path)
