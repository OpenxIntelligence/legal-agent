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
    if not isinstance(run_id, str) or not re.fullmatch(r"run-[A-Za-z0-9_-]+", run_id):
        raise ValueError("Invalid run ID.")
    root = Path(root).resolve()
    folder = root / "workspace" / "runs" / run_id
    if folder.is_symlink() or folder.resolve().parent != (root / "workspace" / "runs").resolve():
        raise ValueError("Invalid run path.")
    path = folder / "progress.json"
    if path.is_symlink():
        raise ValueError("Invalid progress path.")
    with runner_lock(root, run_id):
        progress = json.loads(path.read_text())
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
