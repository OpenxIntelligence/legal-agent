"""Contracts: task classification, question DAG, premises, budgets.

Pure data + rules; no model calls, no I/O. The orchestrator and
engines build on these. States, transitions, and budget semantics
are enforced here so every caller shares one definition.
"""

from __future__ import annotations

TASK_TYPES = (
    "single_issue_research",
    "multi_regime_analysis",
    "transaction_review",
    "dispute_investigation",
    "intake_triage",
    "unknown",
)

QUESTION_CLASSES = ("evidence", "analysis", "verify", "repair", "review")

PLANE_VERSION = "orchestration-control-plane/v1"

QUESTION_STATUSES = (
    "proposed",
    "ready",
    "assigned",
    "in_progress",
    "answered",
    "blocked",
    "stale",
    "disclosed",
    "escalated",
)

TERMINAL_QUESTION_STATUSES = ("answered", "disclosed", "escalated")

TRANSITIONS = {
    "proposed": ("ready", "blocked"),
    "ready": ("assigned", "blocked", "stale"),
    "assigned": ("in_progress", "blocked", "stale"),
    "in_progress": ("answered", "blocked", "stale", "disclosed", "escalated"),
    "answered": ("stale",),
    "blocked": ("ready", "disclosed", "escalated"),
    "stale": ("ready", "disclosed", "escalated"),
    "disclosed": (),
    "escalated": (),
}

# Per-task quality bars: approval requires every item, else each
# gap is explicitly disclosed. Adversarial challenge may come
# from the peer adapter or the built-in review engine; what
# matters is that it ran or its absence is disclosed.
QUALITY_BARS = {
    "single_issue_research": {
        "required_classes": ("evidence", "analysis", "verify", "review"),
        "need_adversarial": False,
        "allow_unresolved": True,
    },
    "multi_regime_analysis": {
        "required_classes": ("evidence", "analysis", "verify", "review"),
        "need_adversarial": True,
        "allow_unresolved": True,
    },
    "transaction_review": {
        "required_classes": ("evidence", "verify", "review"),
        "need_adversarial": False,
        "allow_unresolved": True,
    },
    "dispute_investigation": {
        "required_classes": ("evidence", "analysis", "verify", "repair", "review"),
        "need_adversarial": True,
        "allow_unresolved": True,
    },
    "intake_triage": {
        "required_classes": ("evidence", "review"),
        "need_adversarial": False,
        "allow_unresolved": True,
    },
    "unknown": {
        "required_classes": (),
        "need_adversarial": False,
        "allow_unresolved": False,
    },
}


class PlanError(ValueError):
    """A planning refusal: corrupt state, unknown transition, cycle."""


def classify_task(context: dict) -> dict:
    """Rule-based task classification over matter context.

    Never guesses: unrecognized contexts classify as unknown
    (the orchestrator escalates). Returns the task type, the
    classification premises (tracked for reclassification),
    required engines in execution order, and the quality bar.
    """
    context = context if isinstance(context, dict) else {}
    purpose = str(context.get("purpose") or "").lower()
    raw_realms = context.get("realms", []) or []
    if not isinstance(raw_realms, (list, tuple)):
        raw_realms = []
    realms = [str(r).lower() for r in raw_realms]
    dispute = bool(context.get("dispute")) or "dispute" in purpose
    transaction = (
        bool(context.get("transaction")) or "transaction" in purpose
    )
    intake = bool(context.get("intake")) or "triage" in purpose
    multi = len({r for r in realms if r}) > 1 or "multi" in purpose

    if dispute:
        task_type = "dispute_investigation"
    elif transaction:
        task_type = "transaction_review"
    elif intake:
        task_type = "intake_triage"
    elif multi:
        task_type = "multi_regime_analysis"
    elif purpose or realms:
        task_type = "single_issue_research"
    else:
        task_type = "unknown"

    premises = [
        {"id": "cls-purpose", "statement": f"purpose indicates {task_type}"},
        {
            "id": "cls-scope",
            "statement": (
                "multi-regime" if multi or dispute else "single-regime scope"
            ),
        },
    ]
    order = ["research", "analysis", "verification", "repair", "review"]
    required = [
        engine
        for engine in order
        if _engine_needed(engine, task_type)
    ]
    return {
        "task_type": task_type,
        "premises": premises,
        "engines": required,
        "bar": dict(QUALITY_BARS[task_type]),
    }


def _engine_needed(engine: str, task_type: str) -> bool:
    bar = QUALITY_BARS[task_type]
    mapping = {
        "research": "evidence",
        "analysis": "analysis",
        "verification": "verify",
        "repair": "repair",
        "review": "review",
    }
    return mapping[engine] in bar["required_classes"]


def new_state() -> dict:
    """Empty orchestration state (stored under data['orchestration'])."""
    return {
        "classification": {},
        "classification_history": [],
        "questions": {},
        "question_seq": 0,
        "premises": {},
        "findings": {},
        "finding_seq": 0,
        "contradictions": [],
        "invalidations": [],
        "spend": {"iterations": 0, "tool_calls": 0, "judge_calls": 0,
                  "engine_passes": 0},
        "disclosures": [],
        "escalations": [],
        "approval": None,
        "seed_source": "",
        "decomposition_advisory": [],
        "policy": {},
        "policy_history": [],
    }


def _fingerprint(text: str) -> str:
    """Normalized question text for redundancy checks."""
    return "".join(
        ch.lower() for ch in str(text or "") if ch.isalnum()
    )


def add_question(state: dict, text: str, qclass: str, deps=None,
                 premises=None, issue: str = "") -> dict:
    """Propose a question; refused for unknown classes or cycles."""
    if qclass not in QUESTION_CLASSES:
        raise PlanError(f"unknown question class {qclass!r}")
    for dep in deps or []:
        if dep not in state["questions"]:
            raise PlanError(f"question depends on unknown {dep!r}")
    mark = _fingerprint(text)
    if mark:
        for other in state["questions"].values():
            if _fingerprint(other.get("text", "")) == mark:
                raise PlanError(
                    f"question {other['id']} already asks this: merge "
                    "them or sharpen the difference"
                )
    state["question_seq"] += 1
    qid = f"Q{state['question_seq']}"
    state["questions"][qid] = {
        "id": qid,
        "text": str(text),
        "class": qclass,
        "issue": str(issue or ""),
        "task_id": "",
        "deps": list(deps or []),
        "premises": list(premises or []),
        "status": "proposed",
        "support": [],
        "conditions": [],
        "answer_version": 0,
        "answered_at_inv": 0,
        "engine": "",
        "failures": 0,
        "stagnant_passes": 0,
        "last_progress": "",
        "history": [{"change": "proposed", "detail": str(text)[:120]}],
    }
    _check_cycles(state)
    return state["questions"][qid]


def set_status(state: dict, qid: str, to: str, reason: str = "") -> None:
    """Transition a question; refused transitions raise."""
    question = state["questions"].get(qid)
    if question is None:
        raise PlanError(f"unknown question {qid!r}")
    if to not in QUESTION_STATUSES:
        raise PlanError(f"unknown status {to!r}")
    if to not in TRANSITIONS.get(question["status"], ()):
        raise PlanError(
            f"question {qid} cannot move {question['status']} -> {to}"
        )
    question["history"].append(
        {"change": f"{question['status']}->{to}", "detail": str(reason)[:200]}
    )
    question["status"] = to


def add_premise(state: dict, statement: str, source: str = "") -> dict:
    """Add a versioned premise (version 1)."""
    pid = f"P{len(state['premises']) + 1}"
    while pid in state["premises"]:
        pid = f"P{int(pid[1:]) + 1}"
    state["premises"][pid] = {
        "id": pid,
        "statement": str(statement),
        "version": 1,
        "source": str(source or ""),
        "history": [{"version": 1, "detail": "stated"}],
    }
    return state["premises"][pid]


def bump_premise(state: dict, pid: str, statement: str, reason: str) -> dict:
    """New premise version; dependents are found via dependents_of."""
    premise = state["premises"].get(pid)
    if premise is None:
        raise PlanError(f"unknown premise {pid!r}")
    premise["version"] += 1
    premise["statement"] = str(statement)
    premise["history"].append(
        {"version": premise["version"], "detail": str(reason)[:200]}
    )
    return premise


def link_support(state: dict, qid: str, premise_id: str,
                 premise_version: int, refs=None) -> None:
    """Attach versioned premise + evidence support to a question."""
    question = state["questions"].get(qid)
    if question is None:
        raise PlanError(f"unknown question {qid!r}")
    question["support"].append(
        {
            "premise": premise_id,
            "premise_version": premise_version,
            "refs": [dict(r) for r in refs or []],
        }
    )
    if premise_id not in question["premises"]:
        question["premises"].append(premise_id)


def ready_questions(state: dict) -> list[dict]:
    """Runnable questions whose deps are terminal, in id order.

    Proposed, blocked, stale, and ready questions all run: ready
    means dependencies are met (invalidation re-queues blocked
    work through it), so excluding it would strand questions.
    Answered, disclosed, and escalated deps all unblock:
    terminality lets independent work continue around disclosed
    gaps and escalations, while the quality bar gates approval.
    """
    out = []
    for qid in sorted(state["questions"]):
        question = state["questions"][qid]
        if question["status"] not in ("proposed", "blocked", "stale",
                                      "ready"):
            continue
        if all(
            state["questions"].get(d, {}).get("status")
            in TERMINAL_QUESTION_STATUSES
            for d in question["deps"]
        ):
            out.append(question)
    return out


def unsettled_deps(state: dict, qid: str) -> list:
    """Deps of qid that are terminal but not answered, with statuses.

    A disclosed or escalated prerequisite permits conditional
    continuation, but the downstream answer must carry the
    condition: it can never silently count as a satisfied
    factual premise.
    """
    question = state["questions"].get(qid, {})
    out = []
    for dep in question.get("deps", []):
        status = state["questions"].get(dep, {}).get("status", "")
        if status != "answered":
            out.append((dep, status))
    return out


def dependents_of(state: dict, premise_id: str) -> list[str]:
    """Questions transitively depending on a premise (sorted)."""
    direct = sorted(
        qid
        for qid, question in state["questions"].items()
        if premise_id in question.get("premises", [])
    )
    seen = set(direct)
    queue = list(direct)
    while queue:
        current = queue.pop(0)
        for qid, question in state["questions"].items():
            if qid in seen:
                continue
            if current in question.get("deps", []):
                seen.add(qid)
                queue.append(qid)
    return sorted(seen)


def _check_cycles(state: dict) -> None:
    """Refuse dependency cycles with the offending path."""
    visiting: dict[str, str] = {}

    def visit(qid: str, stack: list) -> None:
        if qid in stack:
            raise PlanError(
                "dependency cycle: " + " -> ".join(stack + [qid])
            )
        if qid in visiting:
            return
        visiting[qid] = qid
        for dep in state["questions"][qid].get("deps", []):
            visit(dep, stack + [qid])

    for qid in state["questions"]:
        visit(qid, [])


class BudgetExhausted(RuntimeError):
    """A budget cap stopped the work; the reason names the cap."""


class Budgets:
    """Iteration/tool/judge caps with explicit spend accounting."""

    def __init__(self, caps: dict | None = None) -> None:
        caps = caps or {}
        self.caps = {
            "iterations": int(caps.get("iterations", 40)),
            "tool_calls": int(caps.get("tool_calls", 120)),
            "judge_calls": int(caps.get("judge_calls", 12)),
            "engine_passes": int(caps.get("engine_passes", 24)),
            "passes_per_question": int(caps.get("passes_per_question", 6)),
            "no_progress_passes": int(caps.get("no_progress_passes", 2)),
            "max_failures": int(caps.get("max_failures", 3)),
        }
        self.spent = {key: 0 for key in
                      ("iterations", "tool_calls", "judge_calls",
                       "engine_passes")}

    def consume(self, kind: str, amount: int = 1) -> None:
        """Spend budget; refused with the cap named when exhausted."""
        if kind not in self.spent:
            raise PlanError(f"unknown budget kind {kind!r}")
        if self.spent[kind] + amount > self.caps[kind]:
            raise BudgetExhausted(
                f"{kind} budget exhausted "
                f"({self.spent[kind]}/{self.caps[kind]})"
            )
        self.spent[kind] += amount

    def remaining(self, kind: str) -> int:
        return self.caps[kind] - self.spent[kind]
