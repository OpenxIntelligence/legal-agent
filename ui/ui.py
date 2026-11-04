#!/usr/bin/env python3
"""Legal Agent: a local UI to start, watch, and review investigations.

Standard library only. From the project folder:

    python3 ui/ui.py
    open http://127.0.0.1:8765

It finds the project automatically (the folder that contains ui/). Use
--project PATH to point it at a different clone (--workshop remains an alias).

It reads each run's progress.json (agent/workspace/runs/<run>/, written by
agent_kit.progress) and case record (agent/workspace/case_records/), and acts only
through the project's own commands (./helper run and ./helper review).
The runtime saves progress at each turn; the console reads those saved records
without importing agent execution code.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

CONSOLE = Path(__file__).resolve().parent
DEFAULT_WORKSHOP = CONSOLE.parent  # ui/ sits at the root of legal-agent-workshop
MAX_TURNS = 200


def read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def read_progress(run_dir: Path) -> dict:
    return read_json(run_dir / "progress.json") or {}


def _parse(value: Any) -> dict:
    try:
        out = json.loads(value) if isinstance(value, str) else value
    except ValueError:
        return {"result": value}
    return out if isinstance(out, dict) else {"result": out}


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            str(c.get("text", "")) for c in content if isinstance(c, dict)
        ).strip()
    return json.dumps(content)


def _saw(items: list) -> dict:
    """What the model saw on one turn, in a few lines."""
    counts: dict[str, int] = {}
    for item in items:
        kind = {
            "function_call_output": "tool results",
            "function_call": "earlier tool calls",
            "reasoning": "reasoning notes",
        }.get(item.get("type", ""))
        kind = kind or (
            "user messages" if item.get("role") == "user" else "model messages"
        )
        counts[kind] = counts.get(kind, 0) + 1
    last = items[-1] if items else {}
    return {
        "items": len(items),
        "counts": counts,
        "newest_observation": str(last.get("output", ""))[:900]
        if last.get("type") == "function_call_output"
        else "",
        "newest_message": _text(last.get("content"))[:600]
        if last.get("role") == "user"
        else "",
    }


def _epoch(stamp: str | None) -> float:
    try:
        return datetime.fromisoformat(stamp or "").timestamp()
    except ValueError:
        return 0.0


def build_events(  # noqa: C901
    progress: dict, case: dict | None, job: dict | None
) -> list:
    """The run as a list of events for the timeline, rebuilt from the saved conversation
    and the case record."""
    events: list = []
    if not progress:
        return events
    messages = progress.get("messages") or []
    case = case or {}
    requests = {r.get("id"): r for r in case.get("requests") or []}
    checks = list(case.get("checklist_checks") or [])
    pause_events: list = []  # (reason, detail) for submit_memo pauses awaiting retry

    def add(event_type: str, actor: str, stamp: Any, /, **payload: Any) -> None:
        events.append(
            {
                "event_type": event_type,
                "actor": actor,
                "timestamp": stamp,
                "payload": payload,
            }
        )

    def what_happened(n: int, stamp: Any, name: str, args: dict, result: dict) -> None:
        """Checks, escalations, and flags the case system recorded for this tool call."""
        if name == "set_checklist":
            draft = [
                str(i.get("id")) for i in args.get("items") or [] if isinstance(i, dict)
            ]
            check = next((c for c in checks if c.get("draft") == draft), None)
            if check is not None:
                checks.remove(check)
                add(
                    "verification",
                    "agent",
                    stamp,
                    turn=n,
                    check="checklist",
                    passed=bool(check.get("passed")),
                    score=check.get("score"),
                    feedback=check.get("feedback", ""),
                    accepted=bool(check.get("accepted")),
                    attempt=check.get("attempt"),
                )
        if name == "submit_memo" and result.get("problems"):
            which = result.get("check", "memo")
            if which in ("claims", "qc", "reasoning"):
                add("verification", "agent", stamp, turn=n, check="memo", passed=True)
            extra: dict = {}
            if which in ("claims", "qc"):
                extra = {
                    "outcome": result.get("outcome"),
                    "scope": result.get("scope"),
                    "verifier": result.get("verifier"),
                    "support": result.get("support"),
                    "qc_review": result.get("qc_review"),
                }
            add(
                "verification",
                "agent",
                stamp,
                turn=n,
                check=which,
                passed=False,
                problems=result["problems"],
                **extra,
            )
            if result.get("status") == "paused":
                if result.get("outcome") == "unavailable":
                    pause_events.append(
                        ("verification_unavailable", result.get("error", ""))
                    )
                elif result.get("outcome") == "incomplete":
                    pause_events.append(("qc_incomplete", result.get("error", "")))
        if result.get("filed") and result.get("status") == "awaiting_signoff":
            add("verification", "agent", stamp, turn=n, check="memo", passed=True)
            if any(
                c.get("check") == "reasoning" and c.get("passed")
                for c in case.get("memo_checks") or []
            ):
                add(
                    "verification",
                    "agent",
                    stamp,
                    turn=n,
                    check="reasoning",
                    passed=True,
                )
            add(
                "verification",
                "agent",
                stamp,
                turn=n,
                check="claims",
                passed=True,
                outcome=result.get("outcome"),
                scope=result.get("scope"),
                verifier=result.get("verifier"),
                support=result.get("support"),
                qc=result.get("qc"),
            )
            add(
                "escalation",
                "agent",
                stamp,
                turn=n,
                rule="memo_signoff",
                need="Attorney sign-off on the filed memo.",
            )
        if result.get("escalated"):
            request = requests.get(result.get("request"), {})
            add(
                "escalation",
                "agent",
                stamp,
                turn=n,
                rule=request.get("rule", ""),
                item=result.get("item"),
                need=result.get("need"),
            )
        if isinstance(result.get("flagged"), dict):
            add(
                "flag",
                "agent",
                stamp,
                turn=n,
                kind="privileged",
                document_id=result["flagged"].get("document_id"),
            )

    add(
        "run_started",
        "agent",
        progress.get("started"),
        verification=bool(progress.get("verification")),
    )
    branched_from = (progress.get("notes") or {}).get("branched_from")
    if branched_from:
        check = checks.pop(0) if checks else {}
        add(
            "branched",
            "agent",
            progress.get("started"),
            branched_from=branched_from,
            passed=bool(check.get("passed")) if check else None,
            score=check.get("score"),
            feedback=check.get("feedback", ""),
        )
    status, last_time = "running", progress.get("started")
    for index, entry in enumerate(progress.get("turns") or []):
        stamp = entry.get("time")
        if status != "running":
            pause_events.clear()
            add("run_started", "agent", stamp, resumed=True)
        n = entry.get("turn")
        if n is not None:
            saw, out, end = (
                entry.get("saw", 0),
                entry.get("output", 0),
                entry.get("end", 0),
            )
            add("model_request", "agent", stamp, turn=n, saw=_saw(messages[:saw]))
            add(
                "model_response",
                "model",
                stamp,
                turn=n,
                input_tokens=entry.get("input_tokens", 0),
                output_tokens=entry.get("output_tokens", 0),
            )
            call = next(
                (m for m in messages[saw:out] if m.get("type") == "function_call"),
                None,
            )
            if call is not None:
                name, args = call.get("name", ""), _parse(call.get("arguments") or "{}")
                add("tool_call", "agent", stamp, turn=n, tool_name=name, arguments=args)
                output = next(
                    (
                        m
                        for m in messages[out:end]
                        if m.get("type") == "function_call_output"
                        and m.get("call_id") == call.get("call_id")
                    ),
                    None,
                )
                if output is not None:
                    result = _parse(output.get("output"))
                    add(
                        "tool_result",
                        "tool",
                        stamp,
                        turn=n,
                        tool_name=name,
                        result=result,
                    )
                    what_happened(n, stamp, name, args, result)
            add("state_snapshot", "agent", stamp, turn=n, status=entry.get("status"))
        status, last_time = entry.get("status", "running"), stamp
        if status != "running" and not (branched_from and index == 0):
            if status == "paused" and pause_events:
                reason, detail = pause_events[-1]
                add(
                    "run_finished",
                    "agent",
                    stamp,
                    status=status,
                    reason=reason,
                    detail=detail,
                )
            else:
                add("run_finished", "agent", stamp, status=status)
    if job and (status == "running" or job.get("started", 0) > _epoch(last_time)):
        stamp = datetime.now(timezone.utc).isoformat()
        if status != "running":
            pause_events.clear()
            add("run_started", "agent", stamp, resumed=True)
        add(
            "model_request",
            "agent",
            stamp,
            turn=int(progress.get("turn") or 0) + 1,
            pending=True,
        )
    return events


def run_state(progress: dict, events: list) -> dict:
    """What the side panels show: status, turn, the latest plan, the documents read."""
    plan: list = []
    analyzed: list = []
    args: dict = {}
    for e in events:
        p = e["payload"]
        if e["event_type"] == "tool_call":
            args = p.get("arguments") or {}
        elif e["event_type"] == "tool_result":
            result = p.get("result") or {}
            if p.get("tool_name") == "update_plan" and isinstance(
                result.get("plan"), list
            ):
                plan = result["plan"]
            if p.get("tool_name") == "analyze_document" and "error" not in result:
                ids = args.get("document_ids") or []
                succeeded = {
                    d.get("document_id")
                    for d in result.get("documents", [])
                    if isinstance(d, dict)
                    and not str(d.get("note", "")).startswith("error")
                }
                analyzed += [d for d in ids if d not in analyzed and d in succeeded]
    return {
        **(progress.get("notes") or {}),
        "run_id": progress.get("run_id"),
        "verification": bool(progress.get("verification")),
        "model": progress.get("model"),
        "turn": progress.get("turn"),
        "status": progress.get("status"),
        "plan": plan,
        "analyzed_ids": analyzed,
        "context_bytes": _context_bytes(progress),
    }


def _context_bytes(progress: dict) -> int | None:
    """Saved-context size for display, or None when it cannot be measured.

    Saved messages are already plain JSON, so failure is unexpected — but
    unmeasurable is reported as unknown (None), never as 0 bytes.
    """
    try:
        return len(json.dumps(progress.get("messages", [])))
    except (TypeError, ValueError):
        return None


def _tool_tokens(run_dir) -> int | None:
    """Ledgered tool-call tokens for a run, or None when no ledger exists."""
    try:
        entries = json.loads((run_dir / "usage.json").read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(entries, list):
        return None
    total = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            total += int(entry.get("input_tokens") or 0) + int(
                entry.get("output_tokens") or 0
            )
        except (TypeError, ValueError):
            continue
    return total


def memo_diff(current: dict | None, previous: dict | None) -> list:
    """Changed sections between two memo filings for revision review.

    Returns [{section, change, before, after}] with plain-string
    before/after; [] when there is no previous filing to compare.
    Sections: conclusion, gaps, finding:<item>.
    """
    if not isinstance(current, dict) or not isinstance(previous, dict):
        return []

    def finding_text(finding: dict) -> str:
        citations = finding.get("citations") or []
        refs = ", ".join(
            f"{c.get('document_id')} p{c.get('page')}"
            for c in citations
            if isinstance(c, dict)
        )
        return f"{finding.get('status')}: {finding.get('summary')} [{refs}]"

    diff = []
    for section in ("conclusion", "gaps"):
        before, after = previous.get(section, ""), current.get(section, "")
        if before != after:
            diff.append(
                {
                    "section": section,
                    "change": "changed",
                    "before": str(before),
                    "after": str(after),
                }
            )
    before_by_item = {
        f.get("item"): f
        for f in (previous.get("findings") or [])
        if isinstance(f, dict)
    }
    after_by_item = {
        f.get("item"): f for f in (current.get("findings") or []) if isinstance(f, dict)
    }
    for item in sorted(set(before_by_item) | set(after_by_item)):
        old, new = before_by_item.get(item), after_by_item.get(item)
        if old is None:
            diff.append(
                {
                    "section": f"finding:{item}",
                    "change": "added",
                    "before": "",
                    "after": finding_text(new),
                }
            )
        elif new is None:
            diff.append(
                {
                    "section": f"finding:{item}",
                    "change": "removed",
                    "before": finding_text(old),
                    "after": "",
                }
            )
        elif finding_text(old) != finding_text(new):
            diff.append(
                {
                    "section": f"finding:{item}",
                    "change": "changed",
                    "before": finding_text(old),
                    "after": finding_text(new),
                }
            )
    return diff


def metrics(events: list, case: dict | None) -> dict:
    m: dict[str, Any] = {
        "agent_turns": 0,
        "tool_calls": 0,
        "tokens": 0,
        "rejected": len((case or {}).get("rejected_facts") or []),
        "escalations": 0,
    }
    for e in events:
        k, p = e.get("event_type"), e.get("payload") or {}
        if k == "model_request" and not p.get("pending"):
            m["agent_turns"] += 1
        elif k == "model_response":
            m["tokens"] += int(p.get("input_tokens") or 0) + int(
                p.get("output_tokens") or 0
            )
        elif k == "tool_call":
            m["tool_calls"] += 1
        elif (
            k in ("verification", "branched")
            and p.get("passed") is False
            and p.get("outcome") != "unavailable"
            and p.get("check") != "qc"
        ):
            # QC repairs are not substantive refusals, so they stay out.
            m["rejected"] += 1
        elif k == "escalation":
            m["escalations"] += 1
    return m


def scope_view(case: dict | None) -> dict:
    """Matter context and legal branches for the Investigation panel.

    Pure dict reading; legacy records without matter/branches come
    back with empty lists and a None version, never invented scope.
    """
    case = case or {}
    matter = case.get("matter") if isinstance(case.get("matter"), dict) else {}
    fields = matter.get("fields") if isinstance(matter.get("fields"), dict) else {}
    known, unknown = [], []
    for name in sorted(fields):
        entry = fields[name]
        if not isinstance(entry, dict):
            continue
        row = {
            "field": name,
            "value": entry.get("value", ""),
            "status": entry.get("status", "unknown"),
            "provenance": entry.get("provenance", ""),
        }
        (unknown if row["status"] == "unknown" else known).append(row)
    branches = case.get("branches") if isinstance(case.get("branches"), dict) else {}
    return {
        "matter_version": matter.get("version"),
        "known": known,
        "unknown": [row["field"] for row in unknown],
        "branches": [
            {
                "id": branch.get("id", branch_id),
                "issue": branch.get("issue", ""),
                "regime": branch.get("regime_label", branch.get("regime", "")),
                "status": branch.get("status", ""),
                "basis": branch.get("basis", ""),
            }
            for branch_id, branch in sorted(branches.items())
            if isinstance(branch, dict)
        ],
    }


def _waiting_on(tasks: dict, task: dict) -> list:
    """Prerequisite ids not completed (mirrors the scheduler's rule)."""
    waiting = []
    for dep in task.get("dependencies") or []:
        other = tasks.get(dep)
        if not isinstance(other, dict) or other.get("status") != "completed":
            waiting.append(dep)
    return waiting


def workflow_view(case: dict | None) -> dict:
    """Issue-task states, attempts, and blockers, in task order."""
    case = case or {}
    tasks = case.get("tasks") if isinstance(case.get("tasks"), dict) else {}
    rows = []
    for task in sorted(tasks.values(), key=lambda t: t.get("seq", 0)):
        if not isinstance(task, dict):
            continue
        attempts = task.get("attempts") or []
        last = attempts[-1] if attempts else {}
        rows.append(
            {
                "id": task.get("id", ""),
                "kind": task.get("kind", ""),
                "question": task.get("question", ""),
                "issues": task.get("issues") or [],
                "status": task.get("status", ""),
                "blocked_on": task.get("blocked_on") or "",
                "waiting_on": _waiting_on(tasks, task),
                "attempts": len(attempts),
                "last_outcome": (last or {}).get("status", "")
                if isinstance(last, dict)
                else "",
            }
        )
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return {"tasks": rows, "counts": counts}


def dependency_view(case: dict | None) -> dict:
    """Why work is blocked or stale, and what would unblock it."""
    case = case or {}
    tasks = case.get("tasks") if isinstance(case.get("tasks"), dict) else {}
    blocked, waiting, stale = [], [], []
    for task in sorted(tasks.values(), key=lambda t: t.get("seq", 0)):
        if not isinstance(task, dict):
            continue
        if task.get("status") == "blocked":
            blocked.append(
                {"task": task.get("id", ""), "blocked_on": task.get("blocked_on") or ""}
            )
        deps = _waiting_on(tasks, task)
        if deps and task.get("status") in ("open", "stale"):
            waiting.append({"task": task.get("id", ""), "waits_on": deps})
        if task.get("status") == "stale":
            history = task.get("history") or []
            why = ""
            if history and isinstance(history[-1], dict):
                why = history[-1].get("reason", "")
            stale.append({"task": task.get("id", ""), "reason": why})
    requests = case.get("requests") if isinstance(case.get("requests"), list) else []
    return {
        "blocked": blocked,
        "waiting": waiting,
        "stale": stale,
        "open_requests": [
            {
                "id": request.get("id", ""),
                "item": request.get("item", ""),
                "need": request.get("need", ""),
                "status": request.get("status", ""),
            }
            for request in requests
            if isinstance(request, dict) and request.get("status") == "open"
        ],
    }


def budget_view(progress: dict, usage_total: int | None) -> dict:
    """Allowance, observed usage, and reserve (mirrors budget_state).

    `usage_total` is the ledgered tool-call token total, or None
    when no ledger exists — then tool usage is unknown and the
    observed figure is a lower bound. Without an allowance the run
    is unenforced; enforcement happens between turns.
    """
    progress = progress or {}
    config = progress.get("config") if isinstance(progress.get("config"), dict) else {}
    allowance = config.get("run_token_budget")
    reserve = config.get("synthesis_reserve_tokens", 0) or 0
    agent = 0
    for turn in progress.get("turns") or []:
        if not isinstance(turn, dict):
            continue
        agent += int(turn.get("input_tokens") or 0) + int(turn.get("output_tokens") or 0)
    observed = agent + (usage_total or 0)
    enforced = allowance is not None
    try:
        allowance = int(allowance) if enforced else None
    except (TypeError, ValueError):
        allowance, enforced = None, False
    return {
        "allowance": allowance,
        "reserve": reserve,
        "observed_agent": agent,
        "observed_tool": usage_total,
        "observed": observed,
        "remaining_for_work": allowance - observed - reserve if enforced else None,
        "enforced": enforced,
        "tool_tokens_unknown": usage_total is None,
        "turns_used": progress.get("turn"),
    }


class Jobs:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.items: list = []

    def start(
        self, kind: str, argv: list, cwd: Path, runs: Path, run_id: str = "", *, env=None
    ) -> dict:
        before = {p.name for p in runs.glob("run-*")} if runs.exists() else set()
        process = subprocess.Popen(
            argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
        )
        job = {
            "id": len(self.items) + 1,
            "kind": kind,
            "argv": " ".join(argv),
            "run_id": run_id,
            "started": time.time(),
            "returncode": None,
            "log": "",
        }
        with self.lock:
            self.items.append(job)
        threading.Thread(target=self._pump, args=(job, process), daemon=True).start()
        if not run_id:
            threading.Thread(
                target=self._discover, args=(job, runs, before), daemon=True
            ).start()
        return job

    def _pump(self, job: dict, process) -> None:
        for line in process.stdout:
            with self.lock:
                job["log"] = (job["log"] + line)[-20000:]
        job["returncode"] = process.wait()

    def _discover(self, job: dict, runs: Path, before: set) -> None:
        while not job["run_id"]:
            fresh = (
                sorted(p.name for p in runs.glob("run-*") if p.name not in before)
                if runs.exists()
                else []
            )
            if fresh:
                job["run_id"] = fresh[-1]
                return
            if job["returncode"] is not None:
                return
            time.sleep(0.2)

    def snapshot(self) -> list:
        with self.lock:
            return [dict(j, running=j["returncode"] is None) for j in self.items]

    def active_for(self, run_id: str):
        return next(
            (
                j
                for j in reversed(self.snapshot())
                if j["run_id"] == run_id and j["running"]
            ),
            None,
        )

    def any_running(self) -> bool:
        return any(j["running"] for j in self.snapshot())


class Console:
    def __init__(self, workshop: Path | None, *, data_root: Path | None = None, parent=None) -> None:
        self.data_root = data_root
        self.parent = parent
        self._matter_contexts = {}
        self._matter_lock = threading.RLock()
        self._launch_lock = threading.RLock()
        self._settings_lock = parent._settings_lock if parent is not None else threading.RLock()
        self.jobs = Jobs()
        self.workshop: Path | None = None
        self._sha_cache: dict = {}
        if workshop:
            self.set_workshop(str(workshop))

    def _process_env(self):
        # Do not delegate the server's ambient credentials or interpreter/shell
        # injection variables to its workers. This limits inherited values;
        # local processes still have the account's filesystem/network access.
        system_names = {
            "PATH", "HOME", "TMPDIR", "TMP", "TEMP", "LANG", "LANGUAGE",
            "LC_ALL", "LC_CTYPE", "LC_MESSAGES", "TZ", "SYSTEMROOT", "WINDIR",
            "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
        }
        setting_names = {
            "SAM_MODEL", "VERIFICATION", "QC_ENABLED", "RUN_TOKEN_BUDGET",
            "SYNTHESIS_RESERVE_TOKENS", "MAX_WORKERS", "MAX_CONTEXT_BYTES",
            "COURTLISTENER_TOKEN", "COURTLISTENER_TOKEN_FILE",
        }
        env = {k: v for k, v in os.environ.items() if k in system_names}
        isolated = self._isolated()
        configured = {} if isolated else self._env()
        if not isolated:
            for name in setting_names:
                value = configured.get(name, os.environ.get(name))
                if value is not None:
                    env[name] = value.strip().strip('"').strip("'")
        # The saved model key is authoritative, including when it is removed.
        # Neither an inherited key nor an inherited *_FILE can resurrect it.
        for name, default in (("MODEL_API_KEY", ""), ("MODEL_API_MODEL", "muse-spark-1.3"), ("MODEL_API_BASE_URL", "https://api.meta.ai/v1")):
            env[name] = configured.get(name, default).strip().strip('"').strip("'")
        # Set on legacy and matter launches alike: helper must never shell-source
        # the .env we have already parsed as plain configuration values.
        interpreter = self._code_agent / ".venv/bin/python"
        python = str(interpreter) if interpreter.is_file() and os.access(interpreter, os.X_OK) else sys.executable
        env.update(LEGAL_AGENT_ROOT=str(self.agent), CASE_DIR=str(self.case_dir()), PYTHON=python)
        if isolated:
            env.pop("MODEL_API_KEY", None)
            env["LEGAL_AGENT_FAKE_MODEL"] = "1"
        return env

    def queue_message(self, run_id: str, text: str, request_id: str = "") -> dict:
        with self._launch_lock:
            if not isinstance(text, str) or not text.strip() or len(text.strip()) > 12000:
                raise ValueError("Follow-up must contain 1 to 12,000 characters.")
            self._run_dir(run_id)
            if request_id:
                record = read_json(self.agent / "workspace/case_records" / f"{run_id}.json") or {}
                if not any(q.get("id") == request_id for q in record.get("requests", [])):
                    raise ValueError("Unknown request for this investigation; save the message without a request association.")
            if self._running():
                # Never read-modify-write the case record while its agent owns it.
                import uuid
                pending_path = self.agent / "workspace/steering-inbox.json"
                pending = read_json(pending_path) or []
                item = {"queue_id": uuid.uuid4().hex, "run_id": run_id,
                        "text": text.strip(), "request_id": request_id}
                pending.append(item)
                temporary = pending_path.with_suffix(".tmp")
                temporary.write_text(json.dumps(pending))
                temporary.replace(pending_path)
                return {"state": "queued", "run_id": run_id, "queue_id": item["queue_id"]}
            return self._queue_message(run_id, text, request_id)

    def _flush_steering(self, run_id: str) -> None:
        pending_path = self.agent / "workspace/steering-inbox.json"
        pending = read_json(pending_path) or []
        for item in list(pending):
            if item.get("run_id") != run_id:
                continue
            self._queue_message(run_id, item["text"], item.get("request_id", ""))
            pending.remove(item)
            temporary = pending_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(pending))
            temporary.replace(pending_path)

    def _queue_message(self, run_id: str, text: str, request_id: str = "") -> dict:
        if not isinstance(text, str) or not text.strip() or len(text.strip()) > 12000:
            raise ValueError("Follow-up must contain 1 to 12,000 characters.")
        self._run_dir(run_id)
        if request_id:
            self.review(run_id, "answer", text.strip(), request_id)
        directive = self.collab_op(run_id, {"op": "directive-submit", "kind": "redirect",
                                          "body": text.strip(), "author": "workspace-user", "user_role": "client"})
        return {"state": "queued", "run_id": run_id, "directive_id": directive["id"]}

    def matter_store(self):
        import importlib.util
        with self._matter_lock:
            if not hasattr(self, "_matter_store"):
                spec = importlib.util.spec_from_file_location("legal_agent_matters", CONSOLE / "matters.py")
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                self._matter_store = module.MatterStore(self.agent / "workspace/matters", self._ingest_matter)
            return self._matter_store

    def _ingest_matter(self, root: Path, body: dict) -> dict:
        scoped = Console(self.workshop, data_root=root, parent=self)
        return scoped.document_add(body)

    def matter_context(self, matter_id: str):
        path = self.matter_store().path(matter_id)
        with self._matter_lock:
            if matter_id not in self._matter_contexts:
                self._matter_contexts[matter_id] = Console(self.workshop, data_root=path, parent=self)
            return self._matter_contexts[matter_id]

    def matter_detail(self, matter_id: str) -> dict:
        detail = self.matter_store().get(matter_id)
        scoped = self.matter_context(matter_id)
        isolated = self._isolated()
        configured = not isolated and bool(scoped._process_env().get("MODEL_API_KEY", "").strip())
        reason = ("demo" if isolated else "preparation_incomplete" if detail.get("status") == "incomplete"
                  else "model_key_missing" if not configured else "")
        detail.update(demo=isolated, api_key_configured=configured, execution_reason=reason,
                      can_run=not reason, runs=scoped.list_runs(), jobs=scoped.jobs.snapshot()[-5:])
        # Track file/policy changes beyond the selected run's own record.
        workspace = scoped.agent / "workspace"
        paths = [workspace / "quarantine.json"]
        paths += sorted((workspace / "inbox").glob("*"))
        paths += sorted((workspace / "case_records").glob("*.json"))
        stamps = []
        for path in paths:
            try:
                stat = path.lstat()
                stamp = (stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size, stat.st_ino)
            except OSError:
                stamp = None
            stamps.append((str(path.relative_to(workspace)), stamp))
        detail["record_revision"] = hashlib.sha256(json.dumps(stamps).encode()).hexdigest()
        return detail

    def matter_add_documents(self, matter_id: str, body: dict) -> dict:
        scoped = self.matter_context(matter_id)
        with scoped._launch_lock:
            if scoped._running():
                raise RuntimeError("Wait for the running investigation to pause before adding documents.")
            self.matter_store().add_documents(matter_id, body)
            return self.matter_detail(matter_id)

    def matter_records(self, matter_id: str, run_id: str = "", preset: str = "documents") -> dict:
        """Query derived records from one owned matter; never a caller-selected DB."""
        import importlib.util
        spec = importlib.util.spec_from_file_location("legal_agent_matter_query", CONSOLE / "matter_query.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if preset not in module.PRESETS:
            raise ValueError("Choose an available record view.")
        matter = self.matter_detail(matter_id)
        scoped = self.matter_context(matter_id)
        run = None
        if run_id:
            if run_id not in {item["id"] for item in matter["runs"]}:
                raise FileNotFoundError("Run not found in this investigation.")
            run = scoped.run_detail(run_id)
        documents = []
        for item in scoped.documents().get("visible", []):
            # Reuse the source viewer boundary, which checks privilege in ALL
            # this matter's runs as well as quarantine and path safety.
            doc = {"id": item["id"], "file": item["file"], "accessible": False}
            try:
                source = scoped.document_source(item["id"])
                doc.update(accessible=True, state=source.get("state"), sha12=source.get("sha12"))
            except (OSError, ValueError, RuntimeError):
                pass
            documents.append(doc)
        snapshot = module.build_snapshot(matter, run, documents)
        result = module.query_snapshot(snapshot, module.PRESETS[preset]["sql"])
        return {**result, "preset": preset,
                "presets": [{"id": key, "label": value["label"]} for key, value in module.PRESETS.items()],
                "sql": module.PRESETS[preset]["sql"], "warnings": snapshot.get("warnings", []),
                "engine": "local-records", "auto_ontology_connected": False}

    def _settings_module(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("legal_agent_local_settings", CONSOLE / "local_settings.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _settings_busy(self) -> bool:
        # copy() avoids holding _matter_lock while a child holds its launch lock.
        if self._running() or any(child._running() for child in self._matter_contexts.copy().values()):
            return True
        # A restarted server may not have opened the matter that owns a live runner.
        return any(self._runtime_module().owner_status(path.parent).get("active")
                   for path in (self.agent / "workspace/matters").glob("*/workspace"))

    def _runtime_module(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("legal_agent_runtime_ownership", CONSOLE.parent / "agent/agent_kit/runtime_ownership.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _running(self) -> bool:
        if self.jobs.any_running():
            return True
        if not self.workshop and self.data_root is None:
            return False
        return bool(self._runtime_module().owner_status(self.agent).get("active"))

    def recovery_status(self, progress: dict, job=None) -> dict:
        can_resume = bool(progress.get("messages"))
        if job:
            return {"state": "none", "can_resume": False, "detail": ""}
        if progress.get("status") == "running":
            if self._runtime_module().owner_status(self.agent).get("active"):
                return {"state": "active_elsewhere", "can_resume": False,
                        "detail": "A runner still owns this workspace. This server cannot capture its console output; saved findings continue to refresh."}
            if not can_resume:
                return {"state": "unavailable", "can_resume": False, "detail": "Execution stopped before saving a conversation checkpoint. Inspect any saved records and start a new investigation; this run cannot safely resume."}
            return {"state": "available", "can_resume": can_resume,
                    "detail": "No runner owns this workspace. Mark this interrupted run as paused, then inspect its saved activity and records. No model call will be made."}
        recovered = (progress.get("notes") or {}).get("stop_reason") == "runner_interrupted"
        return {"state": "recovered" if recovered else "none", "can_resume": can_resume if recovered else True,
                "detail": "Inspect saved activity and records before continuing: a tool may have changed records after the last conversation checkpoint. Resuming may repeat work." if recovered else ""}

    def recover_run(self, run_id: str) -> dict:
        self._require_live("recovering interrupted runs")
        with self._launch_lock, self._settings_lock:
            self._run_dir(run_id)
            if self._running():
                raise RuntimeError("Wait for the current investigation to stop before recovering a run.")
            self._runtime_module().reconcile_abandoned(self.agent, run_id)
            return self.run_detail(run_id)

    def settings_status(self, csrf_token: str) -> dict:
        with self._settings_lock:
            isolated = self._isolated()
            module = self._settings_module()
            result = ({"api_key_configured": False, "model": "synthetic demo", "provider_host": "none"}
                      if isolated else module.status(self._code_agent / ".env"))
            busy = self._settings_busy()
            result.update(isolated=isolated, can_edit=not isolated and not busy,
                          busy=busy, csrf_token=csrf_token, key_validation=module.KEY_RULE,
                          execution={"sandboxed": False,
                                     "environment_policy": "allowlist-v1",
                                     "credential_scope": "Only configured model and CourtListener credentials are passed to child processes. Other ambient credentials are omitted.",
                                     "recovery": "Runner ownership survives server restarts. Interrupted runs require explicit recovery and review before manual continuation.",
                                     "external_databases": False,
                                     "telemetry_export": False,
                                     "permissions": "Agent tools run locally with your current user permissions.",
                                     "provider_data": "Live investigations send matter content to the configured model provider."})
            return result

    def settings_key(self, body: dict, csrf_token: str) -> dict:
        with self._settings_lock:
            if self._isolated():
                raise RuntimeError("API key changes are disabled in the synthetic demo.")
            if self._settings_busy():
                raise RuntimeError("Wait for all investigations to stop before changing the API key.")
            self._settings_module().update_key(self._code_agent / ".env", body)
            return self.settings_status(csrf_token)

    # ---- workshop ----
    def set_workshop(self, path: str) -> dict:
        root = Path(path).expanduser().resolve()
        if (root / "helper").is_file() and (root.parent / "ui").is_dir():
            root = root.parent  # pointed at agent/
        if not (root / "agent/helper").is_file():
            raise RuntimeError(
                f"not a Legal Agent project folder (no agent/helper): {root}"
            )
        if self._running():
            raise RuntimeError("wait for the running agent to stop first")
        self.workshop = root
        return self.info()

    @property
    def _code_agent(self) -> Path:
        """Agent folder whose helper + scripts execute console actions."""
        if self.workshop:
            return self.workshop / "agent"
        return DEFAULT_WORKSHOP / "agent"

    @property
    def agent(self) -> Path:
        """Data root for workspace reads (and subprocess data).

        LEGAL_AGENT_ROOT isolates the console onto a synthetic data
        root; code still executes from the workshop's agent folder.
        """
        if self.data_root is not None:
            return self.data_root
        override = (os.environ.get("LEGAL_AGENT_ROOT") or "").strip()
        if override:
            root = Path(override).expanduser().resolve()
            if not root.is_dir():
                raise RuntimeError(f"LEGAL_AGENT_ROOT is not a directory: {override}")
            return root
        if not self.workshop:
            raise RuntimeError("choose the Legal Agent project folder first")
        return self.workshop / "agent"

    def _isolated(self) -> bool:
        if self.parent is not None:
            return self.parent._isolated()
        return bool((os.environ.get("LEGAL_AGENT_ROOT") or "").strip())

    def _require_live(self, action: str) -> None:
        if self._isolated():
            raise RuntimeError(
                f"{action} is disabled on an isolated synthetic root: "
                "only the gallery and artifact workspaces are served"
            )

    @property
    def runs(self) -> Path:
        return self.agent / "workspace/runs"

    def _env(self) -> dict:
        if self.parent is not None:
            return self.parent._env()
        env = {}
        if self._isolated():
            return env
        path = self.agent / ".env"
        if path.is_file():
            for line in path.read_text().splitlines():
                match = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$", line)
                if match:
                    env[match[1]] = match[2].strip()
        return env

    def case_dir(self) -> Path:
        """The case the agent works on (CASE_DIR in agent/.env, relative to agent/)."""
        if self.data_root is not None:
            return self.data_root / "case"
        value = self._env().get("CASE_DIR", "")
        path = Path(value).expanduser() if value else self.agent / "case"
        return path if path.is_absolute() else (self.agent / path).resolve()

    def _display_path(self, path: Path) -> str:
        """A path relative to the workshop folder when it is inside it (cases/xyz)."""
        if self.workshop is None:
            return str(path)
        try:
            return str(path.resolve().relative_to(self.workshop))
        except ValueError:
            return str(path)

    def cases(self) -> list:
        """Every folder under cases/ that has an objective.md, with its title."""
        folder = self.workshop / "examples/cases" if self.workshop and not self._isolated() else None
        if not folder or not folder.is_dir():
            return []
        out = []
        for case in sorted(p for p in folder.iterdir() if p.is_dir()):
            objective = case / "objective.md"
            if not objective.is_file():
                continue
            title = next(
                (
                    line[2:].strip()
                    for line in objective.read_text(errors="replace").splitlines()
                    if line.startswith("# ")
                ),
                case.name,
            )
            out.append(
                {"id": case.name, "path": self._display_path(case), "title": title}
            )
        return out

    def select_case(self, case_id: str) -> dict:
        """Point CASE_DIR in agent/.env at cases/<case_id> and re-seed the inbox."""
        if self._running():
            raise RuntimeError(
                "wait for the running agent to stop before switching cases"
            )
        if case_id not in {c["id"] for c in self.cases()}:
            raise FileNotFoundError(case_id)
        path = self.agent / ".env"
        lines = path.read_text().splitlines() if path.is_file() else []
        setting = f"CASE_DIR=../examples/cases/{case_id}"
        for i, line in enumerate(lines):
            if line.split("=", 1)[0].strip() == "CASE_DIR":
                lines[i] = setting
                break
        else:
            lines.append(setting)
        tmp = path.with_suffix(".tmp")
        tmp.write_text("\n".join(lines) + "\n")
        tmp.replace(path)
        self._cmd(["./helper", "reset"], self.agent)
        return self.info()

    def info(self) -> dict:
        if not self.workshop:
            return {"workshop": None, "jobs": []}
        env = self._env()
        return {
            "workshop": str(self.workshop),
            "isolated": self._isolated(),
            "product": "Legal Agent",
            "case": self._display_path(self.case_dir()),
            "cases": self.cases(),
            "verification": env.get("VERIFICATION", "on").strip().lower()
            in ("on", "1", "true", "yes"),
            "api_key_configured": bool(env.get("MODEL_API_KEY")),
            "model": env.get("MODEL_API_MODEL", "muse-spark-1.3"),
            "agent_ready": self._agent_ready(),
            "jobs": self.jobs.snapshot()[-10:],
            "control": self.control(),
        }

    def _agent_ready(self) -> bool:
        loop = self._code_agent / "legal_agent/loop.py"
        return loop.is_file() and "is not implemented yet" not in loop.read_text()

    # ---- reads ----
    def list_runs(self) -> list:
        if not self.workshop or not self.runs.exists():
            return []
        rows = []
        for run_dir in sorted(self.runs.glob("run-*"), reverse=True):
            progress = read_progress(run_dir)
            rows.append(
                {
                    "id": run_dir.name,
                    "status": "running"
                    if self.jobs.active_for(run_dir.name)
                    else progress.get("status", "unknown"),
                    "verification": bool(progress.get("verification")),
                    "turn": progress.get("turn"),
                    "started": progress.get("started"),
                }
            )
        return rows

    def pending_request_ids(self, run_id: str) -> list[str]:
        """Request answers saved outside the active case record, not yet delivered."""
        self._run_dir(run_id)
        pending = read_json(self.agent / "workspace/steering-inbox.json") or []
        if not isinstance(pending, list):
            return []
        ids = []
        for item in pending:
            if not isinstance(item, dict) or item.get("run_id") != run_id:
                continue
            request_id = item.get("request_id")
            if (isinstance(request_id, str) and re.fullmatch(r"R[1-9][0-9]*", request_id)
                    and isinstance(item.get("text"), str) and item["text"].strip()
                    and request_id not in ids):
                ids.append(request_id)
        return ids

    def run_detail(self, run_id: str) -> dict:
        run_dir = self._run_dir(run_id)
        progress = read_progress(run_dir)
        case = read_json(self.agent / "workspace/case_records" / f"{run_id}.json")
        job = self.jobs.active_for(run_id)
        paused = bool(job) and bool(
            self.control().get("waiting")
        )  # step mode, between turns
        events = build_events(progress, case, None if paused else job)
        instructions = run_dir / "instructions.md"
        pause_reason, pause_detail = None, None
        if progress.get("status") == "paused" and not job:
            finished = next(
                (
                    e
                    for e in reversed(events)
                    if e["event_type"] == "run_finished"
                    and (e["payload"] or {}).get("status") == "paused"
                ),
                None,
            )
            if finished is not None:
                pause_reason = (finished["payload"] or {}).get("reason")
                pause_detail = (finished["payload"] or {}).get("detail")
        history = (case or {}).get("memo_history") or []
        memo = (case or {}).get("memo")
        source = self.source_changes(case)
        return {
            "verification": read_json(run_dir / "verification.json") or [],
            "branched_from": (progress.get("notes") or {}).get("branched_from"),
            "config_changes": (progress.get("notes") or {}).get("config_changes") or [],
            "source_changed": source["changed"],
            "source_unavailable": source["unavailable"],
            "memo_diff": memo_diff(memo, history[-1] if history else None),
            "error": (progress.get("notes") or {}).get("error")
            if progress.get("status") == "paused"
            else None,
            "stalled": (progress.get("notes") or {}).get("stall")
            if progress.get("status") == "paused"
            else None,
            "budget_exceeded": (progress.get("notes") or {}).get("budget_exceeded")
            if progress.get("status") == "paused"
            else None,
            "context_exceeded": (progress.get("notes") or {}).get("context_exceeded")
            if progress.get("status") == "paused"
            else None,
            "stop_reason": (progress.get("notes") or {}).get("stop_reason")
            if progress.get("status") == "paused"
            else None,
            "stop_action": (progress.get("notes") or {}).get("stop_action")
            if progress.get("status") == "paused"
            else None,
            "pause_reason": pause_reason,
            "pause_detail": pause_detail,
            "id": run_id,
            "started": progress.get("started"),
            "status": "running" if job else progress.get("status", "unknown"),
            "state": run_state(progress, events),
            "case": case,
            "recovery": self.recovery_status(progress, job),
            "pending_request_ids": self.pending_request_ids(run_id),
            "events": events,
            "metrics": {
                **metrics(events, case),
                "tool_tokens": _tool_tokens(run_dir),
            },
            "instructions": instructions.read_text()[:400]
            if instructions.is_file()
            else "",
            "job": job,
            "scope": scope_view(case),
            "workflow": workflow_view(case),
            "dependencies": dependency_view(case),
            "budget": budget_view(progress, _tool_tokens(run_dir)),
        }

    def source_changes(self, case: dict | None) -> dict:
        """Recorded facts whose inbox file changed (or vanished) since.

        Compares each live fact's pinned sha against the current inbox
        file, hashing each file at most once per mtime. Returns
        {"changed": [fact ids], "unavailable": [fact ids]}. Facts
        recorded before sha pinning, and documents with no inbox file
        by that stem, land in "unavailable" only when their sha is
        present but unmatchable; unpinned facts are skipped silently.
        """
        changed, unavailable = [], []
        facts = (case or {}).get("facts") or []
        if not self.workshop or not isinstance(facts, list):
            return {"changed": changed, "unavailable": unavailable}
        inbox = self.agent / "workspace/inbox"
        by_stem = {}
        if inbox.exists():
            for path in sorted(inbox.iterdir()):
                if path.is_file() and path.suffix.lower() in {
                    ".pdf",
                    ".jpg",
                    ".jpeg",
                    ".png",
                }:
                    by_stem.setdefault(path.stem, path)
        for fact in facts:
            if not isinstance(fact, dict) or not fact.get("sha256"):
                continue
            path = by_stem.get(str(fact.get("document_id", "")))
            if path is None:
                unavailable.append(fact.get("id"))
                continue
            if self._file_sha(path) != fact.get("sha256"):
                changed.append(fact.get("id"))
        return {"changed": changed, "unavailable": unavailable}

    def _file_sha(self, path: Path) -> str:
        try:
            mtime = path.stat().st_mtime_ns
        except OSError:
            return ""
        cached = self._sha_cache.get(str(path))
        if cached and cached[0] == mtime:
            return cached[1]
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            return ""
        self._sha_cache[str(path)] = (mtime, digest)
        return digest

    def page_image(self, doc: str, page: str) -> bytes:
        if self.data_root is not None:
            self.document_source(doc)  # Same revoked/quarantined access boundary as text.
        name = f"{doc}-p{int(page)}.png"
        if "/" in doc or ".." in doc:
            raise FileNotFoundError(doc)
        folders = [self.agent / "workspace/inbox/pages"] if self.data_root is not None else [
            self.agent / "workspace/inbox/pages", self.case_dir() / "documents/pages"]
        for folder in folders:
            target = folder / name
            if target.is_file() and not target.is_symlink() and target.resolve().is_relative_to(folder.resolve()):
                return target.read_bytes()
        raise FileNotFoundError(name)

    def _quarantine_map(self) -> dict:
        """Active quarantine entries {doc_id: {reason, findings, at}}.

        Read straight from the store (no agent import): a release
        is recorded with "released", so active means present and
        unreleased. The store never holds covered text.
        """
        try:
            import json as _json

            store = _json.loads(
                (self.agent / "workspace/quarantine.json").read_text()
            )
        except (OSError, ValueError):
            return {}
        if not isinstance(store, dict):
            return {}
        return {
            doc: entry
            for doc, entry in store.items()
            if isinstance(entry, dict) and "released" not in entry
        }

    def documents(self) -> dict:
        if not self.workshop:
            return {"visible": [], "withheld": 0, "quarantined": []}
        inbox = self.agent / "workspace/inbox"
        sealed = self._quarantine_map()
        visible = (
            [
                {
                    "id": path.stem,
                    "file": path.name,
                    "quarantined": path.stem in sealed,
                    "quarantine_reason": (
                        str(sealed[path.stem].get("reason", ""))
                        if path.stem in sealed
                        else ""
                    ),
                }
                for path in sorted(inbox.iterdir())
                if path.is_file()
                and path.suffix.lower() in {
                    ".pdf", ".jpg", ".jpeg", ".png", ".txt", ".md"}
            ]
            if inbox.exists()
            else []
        )
        names = {d["id"] for d in visible}
        return {
            "visible": visible,
            "withheld": 0,
            "quarantined": sorted(sealed),
        }

    def document_source(self, doc: str) -> dict:
        """Inspect current cached extraction, without triggering a model request."""
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", doc):
            raise FileNotFoundError(doc)
        if doc in self._quarantine_map():
            raise RuntimeError("This document is quarantined and cannot be opened.")
        for record in (self.agent / "workspace/case_records").glob("*.json"):
            case = read_json(record) or {}
            if any(isinstance(flag, dict) and flag.get("kind") == "privileged"
                   and flag.get("document_id") == doc for flag in case.get("flags", []) or []):
                raise RuntimeError("Source access was revoked in this investigation.")
        inbox = self.agent / "workspace/inbox"
        target = next((p for p in inbox.glob(doc + ".*")
                       if p.is_file() and p.suffix.lower() in {".txt", ".md", ".pdf"}), None)
        if target is None or target.is_symlink() or not target.resolve().is_relative_to(inbox.resolve()):
            raise FileNotFoundError(doc)
        sha = hashlib.sha256(target.read_bytes()).hexdigest()
        cache = self.agent / "workspace/cache/text" / f"{sha}.json"
        if cache.is_symlink():
            raise RuntimeError("Source cache is unavailable.")
        cached = read_json(cache) or {}
        pages = cached.get("pages") or []
        return {"document_id": doc, "file": target.name, "sha12": sha[:12],
                "state": "ready" if pages else "pending", "pages": pages,
                "fidelity": "extraction", "fidelity_note": "Saved extraction text. Compare important quotations with the original document. No model request is made when opening this view."}

    def document_add(self, body: dict) -> dict:
        """./helper ingest …: one validated file into the inbox.

        JSON in: {filename, text} or {filename, content_b64}.
        The payload travels via a temp file (never argv); the
        ingest script validates, stores, scans, and reports.
        """
        filename = str((body or {}).get("filename") or "")
        text = (body or {}).get("text")
        blob = (body or {}).get("content_b64")
        if text is not None and blob is not None:
            raise RuntimeError("send text or content_b64, not both")
        if text is None and blob is None:
            raise RuntimeError("send text or content_b64")
        if text is not None and not isinstance(text, str):
            raise RuntimeError("text must be a string")
        if blob is not None and not isinstance(blob, str):
            raise RuntimeError("content_b64 must be a string")
        tmp = tempfile.NamedTemporaryFile(
            prefix="upload-", suffix=".txt", delete=False)
        try:
            if text is not None:
                tmp.write(text.encode())
                flag = "--text-file"
            else:
                tmp.write(blob.encode())
                flag = "--b64-file"
            tmp.close()
            out = subprocess.run(
                ["./helper", "ingest", "--filename", filename,
                 flag, tmp.name],
                cwd=self._code_agent,
                env=self._process_env(), capture_output=True,
                text=True,
            )
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
        if out.returncode:
            raise RuntimeError(
                (out.stderr or out.stdout).strip()[-800:])
        return json.loads(out.stdout)

    def describe(self) -> dict:
        self._require_live("describe")
        out = subprocess.run(
            ["./helper", "describe"], cwd=self._code_agent, env=self._process_env(), capture_output=True, text=True
        )
        if out.returncode:
            raise RuntimeError(
                (out.stderr or out.stdout).strip()[-400:] or "describe failed"
            )
        return json.loads(out.stdout)

    # ---- step mode (workspace/control.json, read by agent_kit.progress after each saved turn) ----
    def control(self) -> dict:
        return read_json(self.agent / "workspace/control.json") or {
            "step": False,
            "credits": 0,
            "waiting": False,
        }

    def set_control(self, change: dict) -> dict:
        control = self.control()
        if "step" in change:
            control["step"] = bool(change["step"])
            if not control["step"]:
                control["waiting"] = False
        if change.get("next"):
            control["credits"] = int(control.get("credits", 0)) + 1
        path = self.agent / "workspace/control.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(control))
        tmp.replace(path)
        return control

    # ---- actions ----
    def start_run(self, turns: int, step: bool = False) -> dict:
        self._require_live("starting agent runs")
        with self._launch_lock, self._settings_lock:
            self._require_model_key()
            if self._running():
                raise RuntimeError("an agent run is already in progress")
            self._reset_control(step)
            return self._launch("run", turns, "")

    def resume_run(self, run_id: str, turns: int, step: bool | None = None) -> dict:
        self._require_live("resuming agent runs")
        with self._launch_lock, self._settings_lock:
            self._require_model_key()
            self._run_dir(run_id)
            if self._running():
                raise RuntimeError("an agent run is already in progress")
            self._flush_steering(run_id)
            self._reset_control(bool(step))
            return self._launch("resume", turns, run_id)

    def _require_model_key(self) -> None:
        if not self._process_env().get("MODEL_API_KEY", "").strip():
            raise RuntimeError("No model API key is configured. Add an API key in Settings before starting or resuming an investigation.")

    def _reset_control(self, step: bool) -> None:
        """A new run starts with no saved 'Next turn' clicks: the first turn always runs."""
        self.set_control({"step": step})
        control = dict(self.control(), credits=0, waiting=False)
        path = self.agent / "workspace/control.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(control))
        tmp.replace(path)

    def _launch(self, kind: str, turns: int, run_id: str) -> dict:
        # Register subprocesses atomically against provider key replacement.
        with self._settings_lock:
            return self._launch_with_settings(kind, turns, run_id)

    def _launch_with_settings(self, kind: str, turns: int, run_id: str) -> dict:
        if self._running():
            raise RuntimeError("an agent run is already in progress")
        turns = max(1, min(int(turns), MAX_TURNS))
        argv = ["./helper", "run", "--turns", str(turns)]
        if run_id:
            argv += ["--resume", run_id]
        if self.data_root is not None:
            return self.jobs.start(kind, argv, self._code_agent, self.runs, run_id, env=self._process_env())
        return self.jobs.start(kind, argv, self.agent, self.runs, run_id, env=self._process_env())

    def _cmd(self, argv: list, cwd: Path) -> dict:
        out = subprocess.run(argv, cwd=self._code_agent if self.data_root is not None else cwd, env=self._process_env(), capture_output=True, text=True)
        if out.returncode:
            raise RuntimeError((out.stderr or out.stdout).strip()[-800:])
        return {"message": (out.stdout + out.stderr).strip()[-800:]}

    def release(self) -> dict:
        raise RuntimeError("Archived evidence release is unavailable. Upload documents or answer the investigation request instead.")

    def review(
        self,
        run_id: str,
        decision: str,
        notes: str,
        request_id: str = "",
        qualifications=None,
        comments=None,
    ) -> dict:
        self._require_live("run review")
        self._run_dir(run_id)
        argv = ["./helper", "review", run_id]
        if decision == "answer":
            argv += ["--answer", request_id]
        else:
            argv += ["--approve" if decision == "approve" else "--reject"]
        if notes:
            argv += ["--notes", notes]
        for extra, flag in ((qualifications, "--qualify"), (comments, "--comment")):
            for value in extra or []:
                if isinstance(value, str) and value.strip():
                    argv += [flag, value]
        return self._cmd(argv, self._code_agent)

    def verify(self, run_id: str, what: str) -> dict:
        """./helper verify RUN --memo | --checklist: the checks on a finished run's work."""
        self._require_live("verify")
        self._run_dir(run_id)
        if what not in ("memo", "checklist"):
            raise RuntimeError("verify the memo or the checklist")
        out = subprocess.run(
            ["./helper", "verify", run_id, f"--{what}", "--json"],
            cwd=self._code_agent,
            env=self._process_env(), capture_output=True,
            text=True,
        )
        if out.returncode:
            raise RuntimeError((out.stderr or out.stdout).strip()[-800:])
        return _json_result(out.stdout)

    def branch(self, run_id: str) -> dict:
        """./helper branch RUN: a new run, verification on, from the run's checklist turn."""
        self._require_live("branch")
        self._run_dir(run_id)
        if self._running():
            raise RuntimeError("wait for the running agent to stop")
        out = subprocess.run(
            ["./helper", "branch", run_id],
            cwd=self._code_agent,
            env=self._process_env(), capture_output=True,
            text=True,
        )
        if out.returncode:
            raise RuntimeError((out.stderr or out.stdout).strip()[-800:])
        return _json_result(out.stdout)

    def _artifact(self, argv: list) -> dict:
        """./helper artifact …: gallery/artifacts; mutations live server-side."""
        return _json_result(self._artifact_text(argv))

    def _artifact_text(self, argv: list) -> str:
        """Raw stdout of ./helper artifact … (code runs here, data follows LEGAL_AGENT_ROOT)."""
        out = subprocess.run(
            ["./helper", "artifact"] + [str(a) for a in argv],
            cwd=self._code_agent,
            env=self._process_env(), capture_output=True,
            text=True,
        )
        if out.returncode:
            raise RuntimeError((out.stderr or out.stdout).strip()[-800:])
        return out.stdout

    def _pilot(self, argv: list) -> dict:
        """./helper pilot …: draft keys in, sign-offs out (JSON)."""
        out = subprocess.run(
            ["./helper", "pilot"] + [str(a) for a in argv],
            cwd=self._code_agent,
            env=self._process_env(), capture_output=True,
            text=True,
        )
        if out.returncode:
            raise RuntimeError((out.stderr or out.stdout).strip()[-800:])
        return _json_result(out.stdout)

    def _needs(self, run_id: str) -> dict:
        """./helper needs …: read-only per-issue needs + status."""
        self._run_dir(run_id)
        out = subprocess.run(
            ["./helper", "needs", run_id],
            cwd=self._code_agent,
            env=self._process_env(), capture_output=True,
            text=True,
        )
        if out.returncode:
            raise RuntimeError((out.stderr or out.stdout).strip()[-800:])
        return _json_result(out.stdout)

    def vendor_file(self, name: str) -> bytes:
        """Vendored C-1 bundles from disk. Basename .js only; anything
        else fails closed (the handler maps FileNotFoundError to 404)."""
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*\.js", name or ""):
            raise FileNotFoundError(name)
        path = CONSOLE / "vendor" / name
        if not path.is_file():
            raise FileNotFoundError(name)
        return path.read_bytes()

    def collab_show(self, run_id: str) -> dict:
        """./helper collab … show: read-only collaboration state."""
        self._run_dir(run_id)
        out = subprocess.run(
            ["./helper", "collab", run_id, "show"],
            cwd=self._code_agent,
            env=self._process_env(), capture_output=True,
            text=True,
        )
        if out.returncode:
            raise RuntimeError((out.stderr or out.stdout).strip()[-800:])
        return _json_result(out.stdout)

    def collab_op(self, run_id: str, body: dict) -> dict:
        """./helper collab …: one collaboration mutation. Unknown ops fail."""
        self._run_dir(run_id)
        op = str((body or {}).get("op") or "")
        # op -> ([(body key, flag)], [(body key, repeated flag)]).
        spec = {
            "draft-save": ([("title", "--title"), ("body", "--body"),
                             ("author", "--author"), ("user_role", "--user-role"), ("draft", "--draft"),
                             ("base", "--base")], []),
            "draft-send": ([("draft", "--draft"), ("author", "--author"), ("user_role", "--user-role")], []),
            "draft-get": ([("draft", "--draft")], []),
            "draft-verify": ([("draft", "--draft"), ("verdict", "--verdict"),
                               ("rationale", "--rationale"),
                               ("author", "--author"), ("user_role", "--user-role"),
                               ("agent", "--agent"), ("role", "--role")], []),
            "thread-open": ([("object", "--object"), ("body", "--body"),
                              ("author", "--author"), ("user_role", "--user-role"), ("version", "--version"),
                              ("anchor_start", "--anchor-start"),
                              ("anchor_end", "--anchor-end"),
                              ("quoted", "--quoted"),
                              ("anchor_context", "--anchor-context")], []),
            "thread-reply": ([("thread", "--thread"), ("body", "--body"),
                               ("author", "--author"), ("user_role", "--user-role"),
                               ("agent", "--agent"), ("role", "--role")], []),
            "thread-edit": ([("thread", "--thread"), ("message", "--message"),
                              ("body", "--body"), ("author", "--author"), ("user_role", "--user-role")], []),
            "thread-resolve": ([("thread", "--thread"),
                                 ("resolution", "--resolution"),
                                 ("author", "--author"), ("user_role", "--user-role")], []),
            "thread-reopen": ([("thread", "--thread"), ("reason", "--reason"),
                                ("author", "--author"), ("user_role", "--user-role")], []),
            "directive-submit": ([("kind", "--kind"), ("body", "--body"),
                                   ("author", "--author"), ("user_role", "--user-role"),
                                   ("from_thread", "--from-thread")],
                                  [("base_refs", "--base-ref")]),
            "directive-ack": ([("directive", "--directive"),
                                ("agent", "--agent"), ("role", "--role")], []),
            "directive-apply": ([("directive", "--directive"),
                                  ("agent", "--agent"), ("role", "--role")],
                                 [("invalidate", "--invalidate")]),
            "directive-refuse": ([("directive", "--directive"),
                                   ("rationale", "--rationale"),
                                   ("agent", "--agent"), ("role", "--role")], []),
            "directive-supersede": ([("directive", "--directive"),
                                      ("by", "--by"),
                                      ("author", "--author"), ("user_role", "--user-role")], []),
            "context-add": ([("source", "--source"), ("body", "--body"),
                              ("file", "--file"), ("author", "--author"), ("user_role", "--user-role")], []),
            "context-admit": ([("context", "--context"),
                                ("author", "--author"), ("user_role", "--user-role")], []),
            "context-reject": ([("context", "--context"),
                                 ("reason", "--reason"),
                                 ("author", "--author"), ("user_role", "--user-role")], []),
            "edge-add": ([("src", "--src"), ("dst", "--dst"),
                           ("relation", "--relation"),
                           ("author", "--author"), ("user_role", "--user-role")], []),
        }
        if op not in spec:
            raise RuntimeError(f"unknown collab op {op!r}")
        scalars, repeated = spec[op]
        argv = ["./helper", "collab", run_id, op]
        for key, flag in scalars:
            value = str((body or {}).get(key) or "")
            if value:
                argv += [flag, value]
        for key, flag in repeated:
            values = (body or {}).get(key) or []
            if not isinstance(values, list):
                raise RuntimeError(f"{key} must be a list")
            for value in values:
                argv += [flag, str(value)]
        if op in ("thread-open", "thread-reply") and (body or {}).get("challenge"):
            argv.append("--challenge")
        out = subprocess.run(
            argv, cwd=self._code_agent, env=self._process_env(), capture_output=True, text=True)
        if out.returncode:
            raise RuntimeError((out.stderr or out.stdout).strip()[-800:])
        return json.loads(out.stdout)

    def needs_set(self, run_id: str, body: dict) -> dict:
        """./helper needs … set: freeze one item's obligations."""
        self._run_dir(run_id)
        item = str((body or {}).get("item") or "")
        lines = (body or {}).get("required") or []
        if not isinstance(lines, list) or not lines:
            raise RuntimeError("set needs required lines")
        argv = ["./helper", "needs", run_id, "set", "--item", item]
        for line in lines:
            argv += ["--required", str(line)]
        out = subprocess.run(
            argv,
            cwd=self._code_agent,
            env=self._process_env(), capture_output=True,
            text=True,
        )
        if out.returncode:
            raise RuntimeError((out.stderr or out.stdout).strip()[-800:])
        return _json_result(out.stdout)

    def pilot_list(self) -> dict:
        return self._pilot(["list"])

    def pilot_agreement(self) -> dict:
        return self._pilot(["agreement"])

    def pilot_review(
        self,
        matter: str,
        issue: str,
        decision: str,
        reviewer: str,
        notes: str,
        pilot_version: str,
    ) -> dict:
        if decision == "approved":
            flag = "--approve"
        elif decision == "changes_requested":
            flag = "--request-changes"
        else:
            raise RuntimeError("decision must be approved or changes_requested")
        argv = ["review", matter, issue, flag, "--reviewer", reviewer,
                "--version", pilot_version]
        if (notes or "").strip():
            argv += ["--notes", notes]
        return self._pilot(argv)

    def gallery(self) -> dict:
        return self._artifact(["gallery"])

    def gallery_demo(self) -> dict:
        if self._running():
            raise RuntimeError("wait for the running agent to stop")
        return self._artifact(["demo"])

    # Primary content key per template, in first-non-empty order. The
    # console never imports agent code, so this mirrors the registry's
    # count_key entries (agent_kit/artifacts/*); keep them in sync.
    _COUNT_KEYS = (
        "rows",
        "events",
        "cells",
        "clauses",
        "deviations",
        "obligations",
        "intakes",
        "deadlines",
        "requests",
        "watches",
        "uses",
        "filings",
        "terms",
        "elements",
        "questions",
        "invoices",
    )

    def artifact_list(self, run_id: str) -> dict:
        self._run_dir(run_id)
        case = read_json(self.agent / "workspace/case_records" / f"{run_id}.json")
        found = (case or {}).get("artifacts") or {}
        return {
            "instances": [
                {
                    "id": instance.get("id", key),
                    "title": instance.get("title", ""),
                    "template": instance.get("template", ""),
                    "rows": len(
                        next(
                            (
                                instance.get(key)
                                for key in self._COUNT_KEYS
                                if instance.get(key)
                            ),
                            [],
                        )
                    ),
                    "versions": len(instance.get("versions") or []),
                    "review": (instance.get("review") or {}).get("decision", "pending"),
                }
                for key, instance in sorted(found.items())
                if isinstance(instance, dict)
            ]
        }

    def _artifact_id(self, instance_id: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", instance_id or ""):
            raise FileNotFoundError(instance_id)
        return instance_id

    def artifact_show(self, run_id: str, instance_id: str) -> dict:
        self._run_dir(run_id)
        return self._artifact(["show", run_id, self._artifact_id(instance_id)])

    def artifact_open(
        self, run_id: str, title: str, template: str = "", source: str = ""
    ) -> dict:
        self._run_dir(run_id)
        argv = ["open", run_id, "--title", title or ""]
        if (template or "").strip():
            argv += ["--template", template]
        if (source or "").strip():
            argv += ["--source", self._artifact_id(source)]
        return self._artifact(argv)

    # Verb table for template operations: noun for errors, allowed
    # ops, forwarded body keys, and extras (with-link, rebind flag).
    _ARTIFACT_OPS = {
        "board": ("board", ("confirm", "add", "annotate", "link", "dismiss"),
            ("event", "fact", "span", "date", "label", "issues", "facts", "uncertainty", "owner", "kind", "reason"), ("with",)),
        "matrix": ("matrix", ("add", "set", "link", "flag", "retract"),
            ("cell", "issue", "regime", "layer", "proposition", "citation", "effective_date", "last_checked", "caveat", "branch", "status", "note", "reason"), ()),
        "contract": ("contract", ("add", "set", "flag", "retract"),
            ("clause", "document", "page", "quote", "heading", "effect", "risk", "risk_note", "status", "note", "reason"), ()),
        "playbook": ("playbook", ("add-rule", "set-rule", "retract-rule", "assess"),
            ("rule", "topic", "position", "severity", "clause", "verdict", "rationale", "reason"), ()),
        "calendar": ("calendar", ("add", "set", "resolve"),
            ("ob", "clause", "kind", "title", "due_date", "notice_days", "notes", "status", "reason"), ()),
        "intake": ("intake", ("add", "set", "triage", "route", "decide"),
            ("intake", "title", "description", "category", "urgency", "requester", "assignee", "issue", "route", "rationale", "status", "reason"), ()),
        "deadlines": ("deadline", ("add", "set", "resolve"),
            ("deadline", "title", "kind", "owner", "due_date", "ob", "notes", "status", "reason"), ()),
        "discovery": ("discovery", ("add", "set", "serve", "respond", "decide"),
            ("request", "title", "description", "direction", "category", "due_date", "counterpart", "received", "summary", "documents", "notes", "status", "reason"), ()),
        "horizon": ("horizon", ("add", "set", "decide"),
            ("watch", "title", "summary", "regime", "source_desc", "observed", "effective", "checked", "relevance", "assignee", "cell", "status", "reason"), ()),
        "governance": ("governance", ("add", "set", "decide"),
            ("use", "title", "description", "data", "system", "risk", "owner", "conditions", "intake", "status", "reason"), ()),
        "entity": ("entity", ("add-en", "set-en", "add", "set", "rebind", "decide"),
            ("entity", "filing", "name", "regime", "identifier", "title", "kind", "due_date", "period", "notes", "status", "reason"), ()),
        "termsheet": ("termsheet", ("add-sheet", "set-sheet", "add", "set", "position", "decide"),
            ("sheet", "term", "label", "party", "date", "notes", "topic", "proposition", "text", "status", "reason"), ()),
        "patent": ("patent", ("add-subject", "set-subject", "add", "set", "reading", "decide"),
            ("subject", "element", "label", "kind", "ref", "notes", "claim_ref", "text", "reading", "status", "reason"), ()),
        "qa": ("qa", ("add", "set", "answer", "decide"),
            ("question_id", "question", "asker", "topic", "assignee", "due_date", "answer", "documents", "status", "reason"), ()),
        "spend": ("spend", ("add-vendor", "set-vendor", "add-eng", "set-eng", "add", "set", "decide"),
            ("vendor", "engagement", "invoice", "name", "contact", "title", "budget", "currency", "label", "amount", "notes", "status", "reason"), ("rebind",)),
    }

    def artifact_op(
        self, run_id: str, instance_id: str, verb: str, body: dict
    ) -> dict:
        """One template operation (op allowlist + argv building, shared)."""
        self._run_dir(run_id)
        try:
            noun, ops, keys, extras = self._ARTIFACT_OPS[verb]
        except KeyError:
            raise RuntimeError(f"unknown artifact verb {verb!r}") from None
        op = str(body.get("op") or "")
        if op not in ops:
            raise RuntimeError(f"unknown {noun} operation {op!r}")
        argv = [verb, run_id, self._artifact_id(instance_id), "--op", op]
        for key in keys:
            value = body.get(key)
            if value is not None and str(value) != "":
                argv += [f"--{key}", str(value)]
        if "with" in extras and str(body.get("with", "")):
            argv += ["--with", str(body["with"])]
        if "rebind" in extras and str(body.get("rebind") or "").lower() in (
            "1", "true", "yes",
        ):
            argv += ["--rebind"]
        return self._artifact(argv)

    def artifact_board(
        self, run_id: str, instance_id: str, body: dict
    ) -> dict:
        return self.artifact_op(run_id, instance_id, "board", body)

    def artifact_matrix(
        self, run_id: str, instance_id: str, body: dict
    ) -> dict:
        return self.artifact_op(run_id, instance_id, "matrix", body)

    def artifact_contract(
        self, run_id: str, instance_id: str, body: dict
    ) -> dict:
        return self.artifact_op(run_id, instance_id, "contract", body)

    def artifact_playbook(
        self, run_id: str, instance_id: str, body: dict
    ) -> dict:
        return self.artifact_op(run_id, instance_id, "playbook", body)

    def artifact_calendar(
        self, run_id: str, instance_id: str, body: dict
    ) -> dict:
        return self.artifact_op(run_id, instance_id, "calendar", body)

    def artifact_intake(
        self, run_id: str, instance_id: str, body: dict
    ) -> dict:
        return self.artifact_op(run_id, instance_id, "intake", body)

    def artifact_deadlines(
        self, run_id: str, instance_id: str, body: dict
    ) -> dict:
        return self.artifact_op(run_id, instance_id, "deadlines", body)

    def artifact_discovery(
        self, run_id: str, instance_id: str, body: dict
    ) -> dict:
        return self.artifact_op(run_id, instance_id, "discovery", body)

    def artifact_horizon(
        self, run_id: str, instance_id: str, body: dict
    ) -> dict:
        return self.artifact_op(run_id, instance_id, "horizon", body)

    def artifact_governance(
        self, run_id: str, instance_id: str, body: dict
    ) -> dict:
        return self.artifact_op(run_id, instance_id, "governance", body)

    def artifact_entity(
        self, run_id: str, instance_id: str, body: dict
    ) -> dict:
        return self.artifact_op(run_id, instance_id, "entity", body)

    def artifact_termsheet(
        self, run_id: str, instance_id: str, body: dict
    ) -> dict:
        return self.artifact_op(run_id, instance_id, "termsheet", body)

    def artifact_patent(
        self, run_id: str, instance_id: str, body: dict
    ) -> dict:
        return self.artifact_op(run_id, instance_id, "patent", body)

    def artifact_qa(
        self, run_id: str, instance_id: str, body: dict
    ) -> dict:
        return self.artifact_op(run_id, instance_id, "qa", body)

    def artifact_spend(
        self, run_id: str, instance_id: str, body: dict
    ) -> dict:
        return self.artifact_op(run_id, instance_id, "spend", body)

    def artifact_check(self, run_id: str, instance_id: str, rows: str) -> dict:
        self._run_dir(run_id)
        argv = ["check", run_id, self._artifact_id(instance_id)]
        if (rows or "").strip():
            argv += ["--rows", rows]
        return self._artifact(argv)

    def artifact_mark(self, run_id: str, instance_id: str, body: dict) -> dict:
        self._run_dir(run_id)
        row = str(body.get("row") or "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", row):
            raise RuntimeError(f"unknown audit row {row!r}")
        try:
            index = int(body.get("index", 0) or 0)
        except (TypeError, ValueError):
            raise RuntimeError("citation index must be a number")
        category = str(body.get("category") or "")
        if category not in ("wrong", "incomplete", "irrelevant"):
            raise RuntimeError("category must be wrong, incomplete, or irrelevant")
        argv = [
            "mark", run_id, self._artifact_id(instance_id),
            "--row", row, "--index", str(index),
            "--category", category,
        ]
        if str(body.get("note") or "").strip():
            argv += ["--note", str(body["note"])]
        return self._artifact(argv)

    def artifact_assign(self, run_id: str, instance_id: str, body: dict) -> dict:
        self._run_dir(run_id)
        row = str((body or {}).get("row") or "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", row):
            raise RuntimeError(f"unknown audit row {row!r}")
        return self._artifact(
            ["assign", run_id, self._artifact_id(instance_id),
             "--row", row])

    def artifact_label(self, run_id: str, instance_id: str, body: dict) -> dict:
        self._run_dir(run_id)
        token = str((body or {}).get("token") or "")
        if not re.fullmatch(r"[0-9a-f]{32}", token):
            raise RuntimeError("assignment token is unknown or already used")
        verdict = str((body or {}).get("verdict") or "")
        if verdict not in ("supported", "contradicted", "insufficient"):
            raise RuntimeError("verdict must be supported, contradicted, or insufficient")
        origin = str((body or {}).get("origin") or "")
        if origin not in ("human", "simulated"):
            raise RuntimeError("origin must be human or simulated")
        argv = ["label", run_id, self._artifact_id(instance_id),
               "--token", token, "--verdict", verdict,
               "--rationale", str((body or {}).get("rationale") or ""),
               "--actor", str((body or {}).get("actor") or ""),
               "--origin", origin]
        if (body or {}).get("missing_evidence"):
            argv.append("--missing-evidence")
        return self._artifact(argv)

    def run_objection(self, run_id: str, body: dict) -> dict:
        self._run_dir(run_id)
        op = str((body or {}).get("op") or "")
        if op not in ("raise", "disprove", "restore"):
            raise RuntimeError("op must be raise, disprove, or restore")
        argv = ["./helper", "objections", run_id, op]
        for key, flag in (("text", "--text"), ("ref", "--ref"),
                          ("counter", "--counter"), ("reason", "--reason"),
                          ("reporter", "--reporter")):
            value = str((body or {}).get(key) or "")
            if value:
                argv += [flag, value]
        out = subprocess.run(
            argv, cwd=self._code_agent, env=self._process_env(), capture_output=True, text=True)
        if out.returncode:
            raise RuntimeError((out.stderr or out.stdout).strip()[-800:])
        return json.loads(out.stdout)

    def artifact_adjudicate(self, run_id: str, instance_id: str, body: dict) -> dict:
        self._run_dir(run_id)
        row = str((body or {}).get("row") or "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", row):
            raise RuntimeError(f"unknown audit row {row!r}")
        decision = str((body or {}).get("decision") or "")
        if decision not in ("upheld", "corrected", "unresolved"):
            raise RuntimeError("decision must be upheld, corrected, or unresolved")
        return self._artifact(
            ["adjudicate", run_id, self._artifact_id(instance_id),
             "--row", row, "--decision", decision,
             "--rationale", str((body or {}).get("rationale") or ""),
             "--actor", str((body or {}).get("actor") or "")])

    def artifact_promote(self, run_id: str, instance_id: str, body: dict) -> dict:
        self._run_dir(run_id)
        example_id = str((body or {}).get("example_id") or "")
        if not re.fullmatch(r"[a-z0-9-]{1,64}", example_id):
            raise RuntimeError("example_id must be nonempty [a-z0-9-]")
        role = str((body or {}).get("role") or "")
        if role not in ("development", "regression", "sealed"):
            raise RuntimeError("role must be development, regression, or sealed")
        classes = (body or {}).get("classes")
        if not isinstance(classes, dict) or not classes:
            raise RuntimeError("classes must map every row id to a class")
        for row_id, cls in classes.items():
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", str(row_id)):
                raise RuntimeError(f"unknown audit row {row_id!r}")
            if not str(cls or "").strip():
                raise RuntimeError(f"row {row_id!r} needs a defect class")
        return self._artifact(
            ["promote", run_id, self._artifact_id(instance_id),
             "--example-id", example_id,
             "--actor", str((body or {}).get("actor") or ""),
             "--role", role,
             "--failure", str((body or {}).get("failure") or ""),
             "--behavior", str((body or {}).get("behavior") or ""),
             "--classes", json.dumps(classes)])

    def artifact_examples(self, run_id: str, instance_id: str) -> dict:
        self._run_dir(run_id)
        return self._artifact(
            ["examples", run_id, self._artifact_id(instance_id)])

    def artifact_replay(self, run_id: str, instance_id: str, body: dict) -> dict:
        self._run_dir(run_id)
        example = str((body or {}).get("example") or "")
        if not re.fullmatch(r"[a-z0-9-]{1,64}", example):
            raise RuntimeError(f"unknown example {example!r}")
        return self._artifact(
            ["replay", run_id, self._artifact_id(instance_id),
             "--example", example])

    def artifact_note(self, run_id: str, instance_id: str, body: dict) -> dict:
        self._run_dir(run_id)
        return self._artifact(
            ["note", run_id, self._artifact_id(instance_id),
             "--text", str((body or {}).get("text") or ""),
             "--author", str((body or {}).get("author") or "")])

    def artifact_revise(self, run_id: str, instance_id: str, body: dict) -> dict:
        # Serialize HTTP row revisions, including legacy callers, so a guarded
        # revision cannot race another save between its read and mutation.
        with self._launch_lock:
            expected = body.get("expected_assertion_version")
            if "expected_assertion_version" in body:
                if type(expected) is not int or expected < 1:
                    raise ValueError("Supply a positive expected assertion version.")
                shown = self.artifact_show(run_id, instance_id)
                instance = shown.get("instance") or {}
                row = next((row for row in instance.get("rows", [])
                            if row.get("row_id") == body.get("row")), None)
                if row is None:
                    raise RuntimeError("This claim is no longer available. Reload the saved review.")
                if row.get("assertion_version", 1) != expected:
                    raise RuntimeError("This claim changed since you loaded it. Reload the saved review and reconcile your edit before saving.")
            return self._artifact_revise(run_id, instance_id, body)

    def _artifact_revise(self, run_id: str, instance_id: str, body: dict) -> dict:
        self._run_dir(run_id)
        row = str(body.get("row") or "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", row):
            raise RuntimeError(f"unknown audit row {row!r}")
        argv = ["revise", run_id, self._artifact_id(instance_id), "--row", row]
        if body.get("assertion") is not None:
            argv += ["--assertion", str(body["assertion"])]
        cite = body.get("cite")
        if isinstance(cite, dict):
            argv += [
                "--cite",
                f"{cite.get('document_id', '')},{cite.get('page', '')},{cite.get('quote', '')}",
            ]
            try:
                argv += ["--index", str(int(body.get("index", 0) or 0))]
            except (TypeError, ValueError):
                raise RuntimeError("citation index must be a number")
        if str(body.get("feedback") or "").strip():
            argv += ["--feedback", str(body["feedback"])]
        return self._artifact(argv)

    def artifact_review(
        self,
        run_id: str,
        instance_id: str,
        decision: str,
        notes: str,
        qualifications: list,
        snapshot: dict,
    ) -> dict:
        self._run_dir(run_id)
        if decision == "approved":
            flag = "--approve"
        elif decision == "changes_requested":
            flag = "--request-changes"
        else:
            raise RuntimeError("decision must be approved or changes_requested")
        argv = [
            "review",
            run_id,
            self._artifact_id(instance_id),
            flag,
            "--snapshot",
            json.dumps(snapshot or {}),
        ]
        if (notes or "").strip():
            argv += ["--notes", notes]
        for qualification in qualifications or []:
            if isinstance(qualification, str) and qualification.strip():
                argv += ["--qualify", qualification]
        return self._artifact(argv)

    def artifact_diff(self, run_id: str, instance_id: str, old: str, new: str) -> dict:
        self._run_dir(run_id)
        try:
            numbers = [str(int(old)), str(int(new))]
        except (TypeError, ValueError):
            raise RuntimeError("diff needs two version numbers")
        return self._artifact(["diff", run_id, self._artifact_id(instance_id), *numbers])

    def artifact_export(
        self, run_id: str, instance_id: str, version: str, format: str = "json"  # noqa: A002
    ) -> dict | str:
        self._run_dir(run_id)
        if (format or "json") not in ("json", "md"):
            raise RuntimeError("format must be json or md")
        argv = ["export", run_id, self._artifact_id(instance_id)]
        if (version or "").strip():
            try:
                argv += ["--version", str(int(version))]
            except (TypeError, ValueError):
                raise RuntimeError("version must be a number")
        if (format or "json") == "md":
            return self._artifact_text(argv + ["--format", "md"])
        return self._artifact(argv)

    def _artifact_scope(self, run_id: str, instance_id: str, doc: str) -> dict:
        """The artifact surface's source-access check (fail closed).

        The instance must exist in the stated run's own record, and
        the document must not be privileged there. Anything else —
        unknown instance, cross-matter id, revoked or unknown doc —
        raises before any bytes are read.
        """
        self._run_dir(run_id)
        self._artifact_id(instance_id)
        if not doc or "/" in doc or ".." in doc:
            raise FileNotFoundError(doc)
        case = read_json(self.agent / "workspace/case_records" / f"{run_id}.json")
        instance = ((case or {}).get("artifacts") or {}).get(instance_id)
        if not isinstance(instance, dict) or instance.get("matter", run_id) != run_id:
            raise FileNotFoundError(instance_id)
        flagged = {
            flag.get("document_id")
            for flag in (case or {}).get("flags", []) or []
            if isinstance(flag, dict) and flag.get("kind") == "privileged"
        }
        if doc in flagged:
            raise RuntimeError(f"access to {doc} is revoked in this matter")
        return case

    def artifact_source(self, run_id: str, instance_id: str, doc: str, page: str) -> dict:
        self._artifact_scope(run_id, instance_id, doc)
        try:
            page_no = int(page)
        except (TypeError, ValueError):
            raise FileNotFoundError(f"{doc} page {page}")
        target = None
        for path in sorted((self.agent / "workspace/inbox").glob("*")):
            if path.is_file() and path.stem == doc:
                target = path
                break
        if target is None:
            raise FileNotFoundError(doc)
        sha = hashlib.sha256(target.read_bytes()).hexdigest()
        cached = read_json(self.agent / "workspace/cache/text" / f"{sha}.json")
        pages = (cached or {}).get("pages") or []
        text = next(
            (entry.get("text", "") for entry in pages if entry.get("page") == page_no),
            None,
        )
        if text is None:
            raise FileNotFoundError(f"{doc} page {page_no}")
        capped, truncated = (text[:4000], len(text) > 4000)
        image = (self.agent / "workspace/inbox/pages" / f"{doc}-p{page_no}.png").is_file()
        return {
            "document_id": doc,
            "page": page_no,
            "pages": len(pages),
            "text": capped,
            "truncated": truncated,
            "sha12": sha[:12],
            "fidelity": "extraction",
            "fidelity_note": (
                "Saved extraction text. Quotations are checked against "
                "this text, not against the original page image."
            ),
            "image": image,
        }

    def artifact_page(self, run_id: str, instance_id: str, doc: str, page: str) -> bytes:
        self._artifact_scope(run_id, instance_id, doc)
        return self.page_image(doc, page)

    def reset(self, clear_runs: bool) -> dict:
        self._require_live("reset")
        if self._running():
            raise RuntimeError("stop the running agent before resetting")
        return self._cmd(
            ["./helper", "reset"] + (["--clear-runs"] if clear_runs else []), self.agent
        )

    def _run_dir(self, run_id: str) -> Path:
        if "/" in run_id or ".." in run_id or not run_id.startswith("run-"):
            raise FileNotFoundError(run_id)
        run_dir = self.runs / run_id
        if not run_dir.is_dir():
            raise FileNotFoundError(run_id)
        return run_dir


def _json_result(stdout: str) -> dict:
    """The JSON object a ./helper command printed last (any progress lines before it are skipped)."""
    start = stdout.rfind("\n{")
    try:
        return json.loads(stdout[start + 1 :] if start >= 0 else stdout)
    except ValueError:
        raise RuntimeError(f"unexpected output: {stdout.strip()[-300:]}") from None


def make_handler(console: Console) -> type[BaseHTTPRequestHandler]:  # noqa: C901
    import secrets
    import ipaddress
    csrf_token = secrets.token_urlsafe(32)
    base_console = console
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass

        def _send(self, status: int, body: bytes, kind: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, payload: object, status: int = 200) -> None:
            self._send(status, json.dumps(payload).encode(), "application/json")

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if n < 0 or n > 60000000:
                raise ValueError("Request exceeds the intake upload limit.")
            try:
                body = json.loads(self.rfile.read(n)) if n else {}
            except ValueError:
                raise ValueError("Send a valid JSON object.")
            if not isinstance(body, dict):
                raise ValueError("Send a JSON object.")
            return body

        def _settings_request(self, mutate: bool = False) -> None:
            """Reject untrusted origins before consuming any secret request body."""
            def single(name):
                values = self.headers.get_all(name) or []
                if len(values) != 1:
                    raise PermissionError("Settings require a same-origin loopback request.")
                return values[0]
            host = single("Host")
            port = self.server.server_address[1]
            if host not in {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}:
                raise PermissionError("Settings require a same-origin loopback request.")
            try:
                loopback = ipaddress.ip_address(self.client_address[0]).is_loopback
            except ValueError:
                loopback = False
            if not loopback:
                raise PermissionError("Settings require a same-origin loopback request.")
            origins = self.headers.get_all("Origin") or []
            if (mutate or origins) and (len(origins) != 1 or origins[0] != "http://" + host):
                raise PermissionError("Settings require a same-origin loopback request.")
            if self.headers.get("Sec-Fetch-Site") not in (None, "same-origin", "none"):
                raise PermissionError("Settings require a same-origin loopback request.")
            if mutate:
                token = single("X-Legal-Agent-CSRF")
                if not secrets.compare_digest(token, csrf_token):
                    raise PermissionError("Refresh Settings before changing the API key.")
                content_type = single("Content-Type").lower().replace(" ", "")
                if content_type not in ("application/json", "application/json;charset=utf-8"):
                    raise ValueError("Settings accept application/json only.")
                if self.headers.get("Transfer-Encoding") is not None:
                    raise ValueError("Settings require a bounded JSON request.")
                length = single("Content-Length")
                if not length.isascii() or not length.isdecimal() or not 1 <= int(length) <= 8192:
                    raise ValueError("Settings require a bounded JSON request.")
                if base_console._isolated():
                    raise PermissionError("API key changes are disabled in the synthetic demo.")
                if base_console._settings_busy():
                    raise RuntimeError("Wait for all investigations to stop before changing the API key.")

        def _settings_response(self, mutate: bool = False) -> None:
            try:
                self._settings_request(mutate)
                if mutate:
                    # No body is consumed until origin, token and mode pass.
                    body = self._body()
                    result = base_console.settings_key(body, csrf_token)
                else:
                    result = base_console.settings_status(csrf_token)
                self._json(result)
            except PermissionError as error:
                self.close_connection = True
                self._json({"error": str(error)}, HTTPStatus.FORBIDDEN)
            except ValueError as error:
                self.close_connection = True
                self._json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            except RuntimeError as error:
                self.close_connection = True
                self._json({"error": str(error)}, HTTPStatus.CONFLICT)
            except Exception:
                self.close_connection = True
                self._json({"error": "Settings could not be accessed safely."}, HTTPStatus.CONFLICT)

        def do_GET(self) -> None:
            url = urlparse(self.path)
            if url.path == "/api/settings":
                return self._settings_response()
            q = parse_qs(url.query)
            parts = [p for p in url.path.split("/") if p]
            try:
                console = base_console
                if parts[:2] == ["api", "matters"]:
                    if len(parts) == 2:
                        return self._json(base_console.matter_store().list())
                    if len(parts) == 3:
                        return self._json(base_console.matter_detail(parts[2]))
                    if len(parts) == 4 and parts[3] == "records":
                        return self._json(base_console.matter_records(parts[2], q.get("run", [""])[0], q.get("preset", ["documents"])[0]))
                    console = base_console.matter_context(parts[2])
                    parts = ["api"] + parts[3:]
                if url.path in ("/", "/index.html"):
                    self._send(
                        200,
                        (CONSOLE / "index.html").read_bytes(),
                        "text/html; charset=utf-8",
                    )
                elif url.path in ("/theme.css", "/workspace.css", "/intake.css", "/review.css", "/desk.css", "/settings.js", "/settings.css", "/record-explorer.js", "/record-explorer.css", "/workspace-state.js", "/workspace-editor.js", "/matters.js"):
                    kind = "text/javascript; charset=utf-8" if url.path.endswith(".js") else "text/css; charset=utf-8"
                    self._send(200, (CONSOLE / url.path[1:]).read_bytes(), kind)
                elif len(parts) == 2 and parts[0] == "vendor":
                    self._send(
                        200,
                        console.vendor_file(parts[1]),
                        "text/javascript; charset=utf-8",
                    )
                elif parts == ["api", "info"]:
                    self._json(console.info())
                elif parts == ["api", "runs"]:
                    self._json(console.list_runs())
                elif parts == ["api", "documents"]:
                    self._json(console.documents())
                elif len(parts) == 4 and parts[:2] == ["api", "documents"] and parts[3] == "source":
                    self._json(console.document_source(parts[2]))
                elif parts == ["api", "describe"]:
                    self._json(console.describe())
                elif parts == ["api", "page"]:
                    self._send(
                        200,
                        console.page_image(
                            q.get("doc", [""])[0], q.get("page", ["1"])[0]
                        ),
                        "image/png",
                    )
                elif len(parts) == 3 and parts[:2] == ["api", "runs"]:
                    self._json(console.run_detail(parts[2]))
                elif parts == ["api", "gallery"]:
                    self._json(console.gallery())
                elif parts == ["api", "pilot"]:
                    self._json(console.pilot_list())
                elif parts == ["api", "pilot", "agreement"]:
                    self._json(console.pilot_agreement())
                elif (
                    len(parts) == 4
                    and parts[:2] == ["api", "runs"]
                    and parts[3] == "needs"
                ):
                    self._json(console._needs(parts[2]))
                elif (
                    len(parts) == 4
                    and parts[:2] == ["api", "runs"]
                    and parts[3] == "collab"
                ):
                    self._json(console.collab_show(parts[2]))
                elif len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "artifacts":
                    self._json(console.artifact_list(parts[2]))
                elif len(parts) == 5 and parts[:2] == ["api", "runs"] and parts[3] == "artifacts":
                    self._json(console.artifact_show(parts[2], parts[4]))
                elif (
                    len(parts) == 6
                    and parts[:2] == ["api", "runs"]
                    and parts[3] == "artifacts"
                    and parts[5] == "examples"
                ):
                    self._json(console.artifact_examples(parts[2], parts[4]))
                elif (
                    len(parts) == 6
                    and parts[:2] == ["api", "runs"]
                    and parts[3] == "artifacts"
                    and parts[5] == "diff"
                ):
                    self._json(
                        console.artifact_diff(
                            parts[2], parts[4], q.get("old", [""])[0], q.get("new", [""])[0]
                        )
                    )
                elif (
                    len(parts) == 6
                    and parts[:2] == ["api", "runs"]
                    and parts[3] == "artifacts"
                    and parts[5] == "export"
                ):
                    format = q.get("format", ["json"])[0]
                    if format == "md":
                        body = console.artifact_export(
                            parts[2], parts[4], q.get("version", [""])[0], "md"
                        )
                        assert isinstance(body, str)
                        self._send(HTTPStatus.OK, body.encode(), "text/markdown")
                    else:
                        self._json(
                            console.artifact_export(
                                parts[2], parts[4], q.get("version", [""])[0], format
                            )
                        )
                elif (
                    len(parts) == 6
                    and parts[:2] == ["api", "runs"]
                    and parts[3] == "artifacts"
                    and parts[5] == "source"
                ):
                    self._json(
                        console.artifact_source(
                            parts[2],
                            parts[4],
                            q.get("doc", [""])[0],
                            q.get("page", ["1"])[0],
                        )
                    )
                elif (
                    len(parts) == 6
                    and parts[:2] == ["api", "runs"]
                    and parts[3] == "artifacts"
                    and parts[5] == "page"
                ):
                    self._send(
                        200,
                        console.artifact_page(
                            parts[2],
                            parts[4],
                            q.get("doc", [""])[0],
                            q.get("page", ["1"])[0],
                        ),
                        "image/png",
                    )
                else:
                    self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            except FileNotFoundError as error:
                self._json({"error": f"not found: {error}"}, HTTPStatus.NOT_FOUND)
            except (RuntimeError, ValueError, TypeError) as error:
                self._json({"error": str(error)}, HTTPStatus.CONFLICT)

        def do_POST(self) -> None:
            if urlparse(self.path).path == "/api/settings/key":
                return self._settings_response(True)
            parts = [p for p in urlparse(self.path).path.split("/") if p]
            try:
                b = self._body()
                console = base_console
                if parts[:2] == ["api", "matters"]:
                    store = base_console.matter_store()
                    if len(parts) == 2:
                        with base_console._matter_lock:
                            item = store.create(b)
                        return self._json(base_console.matter_detail(item["id"]))
                    console = base_console.matter_context(parts[2])
                    if len(parts) == 4 and parts[3] == "documents":
                        return self._json(base_console.matter_add_documents(parts[2], b))
                    if len(parts) == 4 and parts[3] == "messages":
                        with base_console._matter_lock:
                            delivery = console.queue_message(str(b["run_id"]), b.get("text", ""), str(b.get("request_id", ""))) if b.get("run_id") else None
                            item = store.message(parts[2], b.get("text", ""), delivery=delivery)
                        result = base_console.matter_detail(item["id"])
                        if delivery:
                            result["delivery"] = delivery
                        return self._json(result)
                    if parts[3:] in (["start"], ["runs"]):
                        with console._launch_lock:
                            if store.get(parts[2]).get("status") == "incomplete":
                                raise RuntimeError("Some documents could not be saved. Prepare a complete investigation before starting.")
                            return self._json(console.start_run(b.get("turns", 10), bool(b.get("step", False))))
                    # Scoped matters cannot reset the workshop or mutate provider config.
                    if parts[3:] in (["reset"], ["release"], ["case"], ["workshop"], ["project"]):
                        raise RuntimeError("This action is not available in a matter.")
                    parts = ["api"] + parts[3:]
                if parts in (["api", "project"], ["api", "workshop"]):
                    self._json(console.set_workshop(b.get("path", "")))
                elif parts == ["api", "case"]:
                    self._json(console.select_case(str(b.get("case", ""))))
                elif parts == ["api", "runs"]:
                    self._json(
                        console.start_run(b.get("turns", 30), bool(b.get("step")))
                    )
                elif (
                    len(parts) == 4
                    and parts[:2] == ["api", "runs"]
                    and parts[3] == "resume"
                ):
                    self._json(
                        console.resume_run(parts[2], b.get("turns", 15), b.get("step"))
                    )
                elif len(parts) == 4 and parts[:2] == ["api", "runs"] and parts[3] == "recover":
                    self._json(console.recover_run(parts[2]))
                elif parts == ["api", "control"]:
                    self._json(console.set_control(b))
                elif (
                    len(parts) == 4
                    and parts[:2] == ["api", "runs"]
                    and parts[3] == "review"
                ):
                    self._json(
                        console.review(
                            parts[2],
                            b.get("decision", ""),
                            b.get("notes", ""),
                            b.get("request_id", ""),
                            b.get("qualifications"),
                            b.get("comments"),
                        )
                    )
                elif (
                    len(parts) == 4
                    and parts[:2] == ["api", "runs"]
                    and parts[3] == "verify"
                ):
                    self._json(console.verify(parts[2], str(b.get("what", ""))))
                elif (
                    len(parts) == 4
                    and parts[:2] == ["api", "runs"]
                    and parts[3] == "branch"
                ):
                    self._json(console.branch(parts[2]))
                elif (
                    len(parts) == 4
                    and parts[:2] == ["api", "runs"]
                    and parts[3] == "objections"
                ):
                    self._json(console.run_objection(parts[2], b))
                elif (
                    len(parts) == 4
                    and parts[:2] == ["api", "runs"]
                    and parts[3] == "needs"
                ):
                    self._json(console.needs_set(parts[2], b))
                elif (
                    len(parts) == 4
                    and parts[:2] == ["api", "runs"]
                    and parts[3] == "collab"
                ):
                    self._json(console.collab_op(parts[2], b))
                elif parts == ["api", "gallery", "demo"]:
                    self._json(console.gallery_demo())
                elif parts == ["api", "documents"]:
                    self._json(console.document_add(b))
                elif parts == ["api", "pilot", "review"]:
                    self._json(
                        console.pilot_review(
                            str(b.get("matter", "")),
                            str(b.get("issue", "")),
                            str(b.get("decision", "")),
                            str(b.get("reviewer", "")),
                            str(b.get("notes", "")),
                            str(b.get("pilot_version", "")),
                        )
                    )
                elif (
                    len(parts) == 4
                    and parts[:2] == ["api", "runs"]
                    and parts[3] == "artifacts"
                ):
                    self._json(
                        console.artifact_open(
                            parts[2],
                            str(b.get("title", "")),
                            str(b.get("template", "")),
                            str(b.get("source", "")),
                        )
                    )
                elif (
                    len(parts) == 6
                    and parts[:2] == ["api", "runs"]
                    and parts[3] == "artifacts"
                    and parts[5] == "check"
                ):
                    self._json(
                        console.artifact_check(parts[2], parts[4], str(b.get("rows", "")))
                    )
                elif (
                    len(parts) == 6
                    and parts[:2] == ["api", "runs"]
                    and parts[3] == "artifacts"
                    and parts[5] == "revise"
                ):
                    self._json(console.artifact_revise(parts[2], parts[4], b))
                elif (
                    len(parts) == 6
                    and parts[:2] == ["api", "runs"]
                    and parts[3] == "artifacts"
                    and parts[5] == "mark"
                ):
                    self._json(console.artifact_mark(parts[2], parts[4], b))
                elif (
                    len(parts) == 6
                    and parts[:2] == ["api", "runs"]
                    and parts[3] == "artifacts"
                    and parts[5] == "assign"
                ):
                    self._json(console.artifact_assign(parts[2], parts[4], b))
                elif (
                    len(parts) == 6
                    and parts[:2] == ["api", "runs"]
                    and parts[3] == "artifacts"
                    and parts[5] == "adjudicate"
                ):
                    self._json(console.artifact_adjudicate(parts[2], parts[4], b))
                elif (
                    len(parts) == 6
                    and parts[:2] == ["api", "runs"]
                    and parts[3] == "artifacts"
                    and parts[5] == "note"
                ):
                    self._json(console.artifact_note(parts[2], parts[4], b))
                elif (
                    len(parts) == 6
                    and parts[:2] == ["api", "runs"]
                    and parts[3] == "artifacts"
                    and parts[5] == "promote"
                ):
                    self._json(console.artifact_promote(parts[2], parts[4], b))
                elif (
                    len(parts) == 6
                    and parts[:2] == ["api", "runs"]
                    and parts[3] == "artifacts"
                    and parts[5] == "replay"
                ):
                    self._json(console.artifact_replay(parts[2], parts[4], b))
                elif (
                    len(parts) == 6
                    and parts[:2] == ["api", "runs"]
                    and parts[3] == "artifacts"
                    and parts[5] == "label"
                ):
                    self._json(console.artifact_label(parts[2], parts[4], b))
                elif (
                    len(parts) == 6
                    and parts[:2] == ["api", "runs"]
                    and parts[3] == "artifacts"
                    and parts[5] in console._ARTIFACT_OPS
                ):
                    self._json(
                        console.artifact_op(parts[2], parts[4], parts[5], b)
                    )
                elif (
                    len(parts) == 6
                    and parts[:2] == ["api", "runs"]
                    and parts[3] == "artifacts"
                    and parts[5] == "review"
                ):
                    self._json(
                        console.artifact_review(
                            parts[2],
                            parts[4],
                            str(b.get("decision", "")),
                            str(b.get("notes", "")),
                            b.get("qualifications") or [],
                            b.get("snapshot") or {},
                        )
                    )
                elif parts == ["api", "release"]:
                    self._json(console.release())
                elif parts == ["api", "reset"]:
                    self._json(console.reset(bool(b.get("clear_runs"))))
                else:
                    self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            except FileNotFoundError as error:
                self._json({"error": f"not found: {error}"}, HTTPStatus.NOT_FOUND)
            except (RuntimeError, ValueError, TypeError) as error:
                self._json({"error": str(error)}, HTTPStatus.CONFLICT)

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Legal Agent — local investigation and review"
    )
    parser.add_argument(
        "--project", "--workshop",
        dest="workshop",
        type=Path,
        default=None,
        help="a different Legal Agent project folder (default: the one containing ui/)",
    )
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--agent-root",
        type=Path,
        default=None,
        help="isolated synthetic data root (same as LEGAL_AGENT_ROOT): only the gallery is served",
    )
    args = parser.parse_args()
    if args.agent_root is not None:
        os.environ["LEGAL_AGENT_ROOT"] = str(args.agent_root)
    workshop = args.workshop
    if workshop is None and (DEFAULT_WORKSHOP / "agent/helper").is_file():
        workshop = DEFAULT_WORKSHOP
    console = Console(workshop)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(console))
    isolated = (os.environ.get("LEGAL_AGENT_ROOT") or "").strip()
    print(
        f"Legal Agent → http://{args.host}:{args.port}"
        + (f"  (isolated synthetic root: {isolated}; gallery only)" if isolated else "")
        + (
            f"  (project: {console.workshop})"
            if console.workshop
            else "  (choose the project folder in the UI)"
        )
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
