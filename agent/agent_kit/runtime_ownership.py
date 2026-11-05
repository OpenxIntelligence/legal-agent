"""Local runner ownership and explicit checkpoint recovery (no model calls).

The flock is authoritative; PID metadata is informational and may be stale.
Never unlink or replace the lock file: its inode is the shared ownership key.
This coordinates cooperating local runners, not an OS sandbox.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import tempfile


class CheckpointError(ValueError):
    """A saved run cannot safely identify or reconstruct its checkpoint."""


def run_folder(root: Path, run_id: str) -> Path:
    """Resolve a local run identifier without following workspace/run links."""
    if not isinstance(run_id, str) or not re.fullmatch(r"run-[A-Za-z0-9_-]{1,160}", run_id):
        raise ValueError("Invalid run ID.")
    root = Path(root).resolve()
    workspace = root / "workspace"
    runs = workspace / "runs"
    folder = runs / run_id
    if any(path.is_symlink() for path in (workspace, runs, folder)):
        raise ValueError("Invalid run path: symbolic links are not allowed.")
    return folder


def run_file(root: Path, run_id: str, name: str) -> Path:
    if name not in ("progress.json", "instructions.md", "objective.md"):
        raise ValueError("Invalid checkpoint file.")
    path = run_folder(root, run_id) / name
    if path.is_symlink():
        raise ValueError("Invalid checkpoint path: symbolic links are not allowed.")
    return path


def validate_checkpoint(progress, run_id: str) -> dict:
    """Validate identity and execution-critical shape without inventing state.

    Historical checkpoints may omit optional metadata such as config or turns;
    present fields must have their expected types. Return the original data.
    """
    def reject(detail):
        raise CheckpointError(f"Invalid checkpoint for {run_id}: {detail}.")
    def count(value):
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0
    if not isinstance(progress, dict):
        reject("expected an object")
    if progress.get("run_id") != run_id:
        reject("run ID does not match its directory")
    if progress.get("status") not in ("running", "paused", "needs_input", "awaiting_signoff", "answered", "done"):
        reject("unknown status")
    if not count(progress.get("turn")):
        reject("turn must be a non-negative integer")
    messages = progress.get("messages")
    if not isinstance(messages, list) or any(not isinstance(m, dict) for m in messages):
        reject("messages must be a list of objects")
    if not isinstance(progress.get("notes"), dict):
        reject("notes must be an object")
    if "verification" in progress and not isinstance(progress["verification"], bool):
        reject("verification must be a boolean")
    if "config" in progress and not isinstance(progress["config"], dict):
        reject("config must be an object")
    turns = progress.get("turns", [])
    if not isinstance(turns, list) or any(not isinstance(t, dict) for t in turns):
        reject("turns must be a list of objects")
    for entry in turns:
        if "end" not in entry:
            reject("turn entry is missing its end boundary")
        for key in ("end", "saw", "output"):
            if key in entry and (not count(entry[key]) or entry[key] > len(messages)):
                reject(f"turn {key} is outside the conversation")
        if all(key in entry for key in ("saw", "output", "end")) and not entry["saw"] <= entry["output"] <= entry["end"]:
            reject("turn boundaries are out of order")
        for key in ("turn", "input_tokens", "output_tokens"):
            if key in entry and not count(entry[key]):
                reject(f"turn {key} must be a non-negative integer")
    ids = progress.get("steering_directive_ids", [])
    if not isinstance(ids, list) or any(not isinstance(i, str) or not i.strip() for i in ids) or len(ids) != len(set(ids)):
        reject("steering receipt IDs must be unique non-empty strings")
    return progress


def _lock_file(root: Path):
    workspace = Path(root) / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    return (workspace / "runner.lock").open("a+", encoding="utf-8")


@contextmanager
def runner_lock(root: Path, run_id: str = ""):
    """Own runner execution until exit; the OS releases ownership on death."""
    with _lock_file(root) as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("An agent runner already owns this workspace.") from None
        try:
            receipt = {"pid": os.getpid(), "started": datetime.now(timezone.utc).isoformat(),
                       "run_id": run_id}
            handle.seek(0)
            handle.truncate()
            json.dump(receipt, handle)
            handle.flush()
            yield receipt
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def owner_status(root: Path) -> dict:
    """Probe ownership without trusting process IDs or changing lock metadata."""
    with _lock_file(root) as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            active = True
        else:
            active = False
            fcntl.flock(handle, fcntl.LOCK_UN)
        handle.seek(0)
        try:
            receipt = json.load(handle)
        except (ValueError, OSError):
            receipt = {}
        if not isinstance(receipt, dict):
            receipt = {}
        return {"active": active, "pid": receipt.get("pid"),
                "started": receipt.get("started"), "run_id": receipt.get("run_id", "")}


def reconcile_abandoned(root: Path, run_id: str) -> dict:
    """Explicitly make an abandoned saved checkpoint resumable, never replay it.

    No saved conversation means no safe continuation point. Reject rather than
    synthesizing context or assuming an uncheckpointed tool did not run.
    """
    root = Path(root).resolve()
    folder = run_folder(root, run_id)
    path = run_file(root, run_id, "progress.json")
    with runner_lock(root, run_id):
        try:
            progress = json.loads(path.read_text())
        except (ValueError, UnicodeError) as error:
            raise CheckpointError(f"Invalid checkpoint for {run_id}: unreadable JSON.") from error
        validate_checkpoint(progress, run_id)
        if not isinstance(progress, dict) or progress.get("status") != "running":
            raise RuntimeError("Only an abandoned running investigation can be recovered.")
        messages = progress.get("messages")
        if not isinstance(messages, list) or not messages:
            raise RuntimeError("No saved conversation checkpoint; start a new investigation.")
        at = datetime.now(timezone.utc).isoformat()
        notes = progress.setdefault("notes", {})
        if not isinstance(notes, dict):
            raise RuntimeError("Saved checkpoint notes are invalid.")
        recoveries = notes.setdefault("runner_recoveries", [])
        if not isinstance(recoveries, list):
            raise RuntimeError("Saved recovery history is invalid.")
        recoveries.append({"at": at, "previous_status": "running", "action": "manual_reconcile"})
        notes["stop_reason"] = "runner_interrupted"
        notes["stop_action"] = "Review the saved checkpoint and explicitly resume when ready. Unsaved provider output may be lost; tool effects after the checkpoint may already exist."
        progress["status"] = "paused"
        # Separate temp names avoid collision with existing progress writers;
        # lock ownership prevents cooperating runners racing this transition.
        fd, temporary = tempfile.mkstemp(prefix=".recovered-", dir=folder)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(progress, handle, indent=1)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return progress
