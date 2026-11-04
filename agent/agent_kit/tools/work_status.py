"""work_status: read the investigation's durable work state.

Lists issue tasks with their states, names the next suggested task
with the reason, briefs it from the record, and reports the budget
and explicitly unknown matter context. Read-only: it changes nothing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .. import scheduler

if TYPE_CHECKING:
    from . import Context

DEFINITION = {
    "type": "function",
    "name": "work_status",
    "description": "Read the investigation's work state: issue tasks and their states "
    "(open, blocked, completed, stale, failed), the next suggested task and why, a "
    "briefing for it rebuilt from the record, the token budget, and matter context "
    "still unknown. Read-only. Optionally brief a specific task instead.",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "task": {
                "type": "string",
                "description": "brief this task id instead of the suggested next one",
            },
        },
        "required": [],
    },
}


def run(ctx: Context, args: dict) -> dict:
    case = ctx.case
    case.ensure_root_task()
    tasks = case.data.get("tasks", {})
    rows = []
    for task in sorted(tasks.values(), key=lambda t: t.get("seq", 0)):
        if not isinstance(task, dict):
            continue
        rows.append(
            {
                "id": task.get("id"),
                "kind": task.get("kind"),
                "issues": task.get("issues") or [],
                "status": task.get("status"),
                "blocked_on": task.get("blocked_on") or "",
                "waiting_on": scheduler.waiting_on(case, task),
                "attempts": len(task.get("attempts") or []),
            }
        )
    suggested = scheduler.select_next(case)
    brief_id = str(args.get("task") or "") or suggested["task"]
    briefing = scheduler.build_task_context(case, brief_id) if brief_id else None
    branches = case.data.get("branches", {})
    return {
        "tasks": rows,
        "next": suggested,
        "briefing": briefing,
        "budget": scheduler.budget_state(ctx.root, ctx.run_id),
        "matter_unknowns": case.matter_unknowns(),
        "branches": [
            {
                "id": bid,
                "issue": branch.get("issue"),
                "regime": branch.get("regime_label", branch.get("regime")),
                "status": branch.get("status"),
            }
            for bid, branch in sorted(branches.items())
            if isinstance(branch, dict)
        ],
    }
