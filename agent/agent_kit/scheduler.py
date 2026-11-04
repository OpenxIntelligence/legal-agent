"""Single-worker scheduler: which task is next, and what the budget allows.

Pure logic over a CaseRecord's durable state (no writes here; the
record's task methods are the one commit authority). The worker is
the existing agent loop: each run slice is an attempt on the root
task, and issue tasks track per-question obligations across slices
and restarts. Directing the worker at specific tasks — worker
contracts, leases, delegation — belongs to a later increment.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import discovery, settings
from .case_record import ROOT_TASK
from .progress import tokens_spent


def waiting_on(case: Any, task: dict) -> list:
    """Incomplete prerequisites of a task (empty when it may start)."""
    tasks = case.data.get("tasks", {})
    waiting = []
    for dep in task.get("dependencies") or []:
        other = tasks.get(dep)
        if not isinstance(other, dict) or other.get("status") != "completed":
            waiting.append(dep)
    return waiting


def score_task(case: Any, task: dict) -> dict:
    """An explainable priority score for one eligible task.

    Returns {"score": int, "factors": {...}, "notes": [...]}. Every
    factor is derived from durable record state — no model judgment,
    no calibrated probabilities. All weights are 1; the starting
    rubric prefers reassessing changed work, resolving legally loaded
    or contested issues, unblocking dependents, and trying fresh
    work before repeating heavily attempted work. Ties break on
    task order so selection stays deterministic.
    """
    factors = {"materiality": 0, "uncertainty": 0, "unblocking": 0, "freshness": 0}
    notes: list[str] = []
    item_id = (task.get("issues") or [None])[0]
    branches = [
        b
        for b in (case.data.get("branches") or {}).values()
        if isinstance(b, dict) and b.get("issue") == item_id
    ]
    if any(b.get("status") in ("disputed", "unknown") for b in branches):
        factors["materiality"] = 2
        notes.append("issue has an unresolved legal branch")
    elif branches:
        factors["materiality"] = 1
        notes.append("issue has a legal branch")
    if task.get("status") == "stale":
        factors["uncertainty"] = 2
        history = task.get("history") or []
        why = history[-1].get("reason", "its inputs changed") if history else ""
        notes.append(f"changed since completion ({why})")
    elif item_id in case.contested_items():
        factors["uncertainty"] = 1
        notes.append("recorded evidence is contested on this issue")
    tasks = case.data.get("tasks", {})
    dependents = [
        other.get("id")
        for other in tasks.values()
        if isinstance(other, dict)
        and (task.get("id") in (other.get("dependencies") or []))
        and other.get("status") not in ("completed", "withdrawn")
    ]
    factors["unblocking"] = min(2, len(dependents))
    if dependents:
        names = ", ".join(sorted(dependents))
        notes.append(f"unblocks {names}")
    attempts = [
        a for a in (task.get("attempts") or []) if isinstance(a, dict)
    ]
    if not attempts:
        factors["freshness"] = 1
        notes.append("not yet attempted")
    elif len(attempts) >= 2:
        factors["freshness"] = -1
        notes.append(f"attempted {len(attempts)} times already")
    factors["score"] = (
        factors["materiality"]
        + factors["uncertainty"]
        + factors["unblocking"]
        + factors["freshness"]
    )
    return {"score": factors.pop("score"), "factors": factors, "notes": notes}


def select_next(case: Any) -> dict:
    """The next task worth working, with the reason it was selected.

    Returns {"task": id or None, "reason": ..., "factors": ...}.
    Eligibility comes before ranking: only open or stale tasks
    whose dependencies are met can select, so blocked tasks and
    unmet dependencies never win on score — the reason says what
    would unblock them. Eligible tasks rank by the explainable
    rubric in score_task; ties break on task order.
    """
    tasks = case.data.get("tasks", {})
    issues = sorted(
        (
            task
            for task in tasks.values()
            if isinstance(task, dict)
            and task.get("kind") == "issue"
            and task.get("status") != "withdrawn"
        ),
        key=lambda t: t.get("seq", 0),
    )
    if not issues:
        return {
            "task": ROOT_TASK,
            "reason": "no issue tasks yet: set the checklist to open them",
            "factors": {},
        }
    waiting: dict[str, list] = {}
    scored: list[tuple[int, int, dict, dict]] = []
    for task in issues:
        if task.get("status") not in ("open", "stale"):
            continue
        deps = waiting_on(case, task)
        if deps:
            waiting[task["id"]] = deps
            continue
        result = score_task(case, task)
        scored.append((result["score"], task.get("seq", 0), task, result))
    if scored:
        scored.sort(key=lambda row: (-row[0], row[1]))
        _, _, task, result = scored[0]
        breakdown = ", ".join(
            f"{name} {value}"
            for name, value in result["factors"].items()
        )
        if task.get("status") == "stale":
            base = f"{task['id']} changed since completion: reassess it"
        else:
            base = f"next open issue: {task.get('question', task['id'])}"
        detail = "; ".join(result["notes"]) if result["notes"] else "no distinguishing factors"
        return {
            "task": task["id"],
            "reason": f"{base} (score {result['score']}: {breakdown}; {detail})",
            "factors": result["factors"],
        }
    if waiting:
        detail = "; ".join(
            f"{task_id} waits on {', '.join(deps)}" for task_id, deps in waiting.items()
        )
        return {
            "task": None,
            "reason": f"all open tasks wait: {detail}",
            "factors": {},
        }
    blocked = [t["id"] for t in issues if t.get("status") == "blocked"]
    if blocked:
        names = ", ".join(
            f"{task_id} ({tasks[task_id].get('blocked_on', 'waiting')})"
            for task_id in blocked
        )
        return {
            "task": None,
            "reason": f"no open task: blocked on {names}",
            "factors": {},
        }
    failed = [t["id"] for t in issues if t.get("status") == "failed"]
    if failed:
        return {
            "task": None,
            "reason": (
                f"no open task: {', '.join(failed)} failed "
                "(reopen one explicitly to retry it)"
            ),
            "factors": {},
        }
    return {
        "task": None,
        "reason": "every issue task is completed",
        "factors": {},
    }


def attempt_outcome(run_status: str, stop_reason: str | None) -> str:
    """A slice's attempt outcome from how the run stopped.

    Reaching a workflow milestone (filed, awaiting sign-off, asked a
    person, done) completes the slice. Spending the slice (turns,
    budget, reserve) or pausing for a retryable tool check leaves it
    partial. Anything else failed the slice; the stop reason keeps
    the category.
    """
    if run_status in ("answered", "awaiting_signoff", "needs_input", "done"):
        return "completed"
    if not stop_reason:
        return "partial"
    if stop_reason in (
        "turn_slice_exhausted",
        "budget_exhausted",
        "synthesis_reserve_reached",
    ):
        return "partial"
    return "failed"


def budget_state(root: Path, run_id: str) -> dict:
    """Observed usage, reserve, and allowance for one run.

    Gathering work may spend only `remaining_for_work` tokens; the
    reserve stays held for synthesis and review. Without a budget the
    run is unenforced and the reserve is moot.
    """
    allowance = settings.run_token_budget()
    reserve = settings.synthesis_reserve_tokens()
    observed = tokens_spent(Path(root), run_id)
    if allowance is None:
        return {
            "allowance": None,
            "observed": observed,
            "reserve": reserve,
            "remaining_for_work": None,
            "enforced": False,
        }
    return {
        "allowance": allowance,
        "observed": observed,
        "reserve": reserve,
        "remaining_for_work": allowance - observed - reserve,
        "enforced": True,
    }


BRIEFING_FACTS = 8


def build_task_context(case: Any, task_id: str) -> dict:
    """The focused briefing a worker needs for one task.

    Rebuilt from durable records — task, matter context, linked
    evidence with versions, open requests, contradictions — never
    from the conversation transcript. Full history stays in the
    record; this is the slice worth carrying.
    """
    task = case.task(task_id)
    data = case.data
    matter = data.get("matter", {}) if isinstance(data.get("matter"), dict) else {}
    fields = matter.get("fields", {})
    known = {
        name: {"value": entry.get("value"), "status": entry.get("status")}
        for name, entry in fields.items()
        if isinstance(entry, dict) and entry.get("status") != "unknown"
    }
    briefing: dict = {
        "task": task_id,
        "question": task.get("question", ""),
        "acceptance": task.get("acceptance", ""),
        "status": task.get("status"),
        "matter_version": matter.get("version", 0),
        "matter_known": known,
        "matter_unknown": case.matter_unknowns(),
        "waiting_on": waiting_on(case, task),
        "inputs": task.get("inputs") or {},
        "current_inputs": case.input_versions(),
    }
    if task.get("kind") != "issue":
        issues = [
            t["id"]
            for t in data.get("tasks", {}).values()
            if isinstance(t, dict)
            and t.get("kind") == "issue"
            and t.get("status") not in ("withdrawn", "completed")
        ]
        briefing["open_issue_tasks"] = sorted(issues)
        return briefing
    issue = (task.get("issues") or [None])[0]
    entry = data.get("checklist", {}).get(issue, {})
    by_id = {f["id"]: f for f in data.get("facts", []) if isinstance(f, dict)}
    facts = [by_id[fid] for fid in entry.get("facts", []) if fid in by_id]
    briefing["evidence"] = [
        {
            "id": fact.get("id"),
            "document_id": fact.get("document_id"),
            "page": fact.get("page"),
            "stance": fact.get("stance", "supports"),
            "quote": fact.get("quote", ""),
            "sha256": (fact.get("sha256") or "")[:12],
        }
        for fact in facts[-BRIEFING_FACTS:]
    ]
    briefing["evidence_total"] = len(facts)
    stances = {fact.get("stance", "supports") for fact in facts}
    briefing["contested"] = "supports" in stances and "opposes" in stances
    briefing["requests"] = [
        {
            "id": request.get("id"),
            "need": request.get("need", ""),
            "status": request.get("status"),
            "response": request.get("response"),
        }
        for request in data.get("requests", [])
        if isinstance(request, dict) and request.get("item") == issue
    ]
    briefing["suggested_inspections"] = (
        discovery.suggest_inspections(case, issue) if issue else []
    )
    return briefing
