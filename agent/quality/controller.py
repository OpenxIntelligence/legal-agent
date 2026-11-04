"""Review orchestration: run check modules, track repairs, recheck after revision.

One review runs registered modules deterministic-first against a memo
draft, turns material defects into bounded repair tasks, and resolves
tasks the revision fixed. It never alters the acceptance policy: memo
rules and the claims check still gate every filing, and QC skips never
weaken acceptance. The only model calls are inside registered semantic
modules, ledgered under their own usage purposes.
"""

from __future__ import annotations

import datetime

from quality import policy, records, registry

MAX_REPAIR_ATTEMPTS_PER_ISSUE = 2
MAX_QC_REVIEWS_PER_RUN = 5


def review(ctx, memo: dict) -> dict:
    """Review one memo draft. Returns {"verdict", "repair_feedback", "review"}.

    Verdicts follow quality/policy: "repair" (revise and resubmit),
    "incomplete" (a required assessment could not complete; retry or
    pause — never proceed silently), "deferred" (budget skipped while
    obligations stand open; a person must dispose them), or "clean"
    (proceed; advisory limits and retained obligations stay listed).
    repair_feedback is the exact agent-facing text; identical drafts
    get byte-identical feedback, so the M5 stall breaker trips on the
    third identical submit — except "incomplete" reviews, which always
    re-run so an outage never caches itself as the answer. Every
    outcome persists on the case record.
    """
    from quality import modules  # noqa: F401  (importing registers builtins)

    case, data = ctx.case, ctx.case.data
    qc_reviews = data.setdefault("qc_reviews", [])
    data.setdefault("assessments", [])
    tasks = data.setdefault("repair_tasks", {})
    state = data.setdefault("qc_state", {})
    memo_v = records.memo_version(data, memo)
    evidence_v = records.evidence_fingerprint(data)
    checklist_ids = sorted(
        item for item in (data.get("checklist") or {}) if isinstance(item, str)
    )
    if "checklist" not in state:
        state["checklist"] = {
            item: str(entry.get("question", ""))
            for item, entry in (data.get("checklist") or {}).items()
            if isinstance(entry, dict)
        }
    review_id = f"QC{len(qc_reviews) + 1}"
    last = state.get("last") or {}
    if (
        last.get("memo") == memo_v["hash"]
        and last.get("evidence") == evidence_v
        and last.get("checklist") == checklist_ids
        and last.get("verdict") != policy.REVIEW_INCOMPLETE
    ):
        # Identical resubmit: cost nothing, repeat the cached verdict
        # verbatim (the loop's stall breaker sees identical outcomes).
        # Incomplete reviews always re-run: an outage must be retryable
        # once the evaluator recovers, even with unchanged inputs.
        return _duplicate(
            case, qc_reviews, tasks, last, review_id, memo_v, evidence_v
        )
    if state.get("reviews", 0) >= MAX_QC_REVIEWS_PER_RUN:
        return _skipped(case, qc_reviews, tasks, review_id, memo_v, evidence_v)
    state["reviews"] = state.get("reviews", 0) + 1
    prior: dict[str, dict] = {}
    assessments = _run_modules(ctx, memo, memo_v, evidence_v, prior)
    review = _settle(
        memo_v, evidence_v, review_id, prior, assessments, tasks, state
    )
    review["claims_stale"] = case.claims_staleness()
    qc_reviews.append(review)
    data["assessments"].extend(assessments)
    state["last"] = {
        "id": review_id,
        "memo": memo_v["hash"],
        "evidence": evidence_v,
        "checklist": checklist_ids,
        "verdict": review["verdict"],
        "repair_feedback": review["repair_feedback"],
    }
    case._save(
        "qc_reviewed",
        "quality",
        {"review": review_id, "verdict": review["verdict"]},
    )
    return {
        "verdict": review["verdict"],
        "repair_feedback": review["repair_feedback"],
        "review": review,
    }


def _duplicate(case, qc_reviews, tasks, last, review_id, memo_v, evidence_v) -> dict:
    from quality import modules  # noqa: F401

    assessments = [
        records.assessment(
            module=spec["name"],
            module_version=spec["version"],
            memo_version=memo_v,
            evidence_version=evidence_v,
            outcome="unexecuted",
            summary=f"unchanged since {last.get('id')}",
        )
        for spec in registry.ordered()
    ]
    review = {
        "id": review_id,
        "at": _now(),
        "memo_version": memo_v,
        "evidence_version": evidence_v,
        "duplicate_of": last.get("id"),
        "assessments": assessments,
        "verdict": last.get("verdict", "clean"),
        "state_reasons": [f"unchanged since {last.get('id')}"],
        "repair_feedback": list(last.get("repair_feedback") or []),
        "tasks_opened": [],
        "tasks_resolved": [],
        "regressions": [],
        "open_tasks": _open_tasks(tasks),
        "unresolved_tasks": _unresolved_tasks(tasks),
        "claims_stale": case.claims_staleness(),
    }
    qc_reviews.append(review)
    case.data["assessments"].extend(assessments)
    case._save(
        "qc_reviewed", "quality", {"review": review_id, "duplicate_of": last.get("id")}
    )
    return {
        "verdict": review["verdict"],
        "repair_feedback": review["repair_feedback"],
        "review": review,
    }


def _skipped(case, qc_reviews, tasks, review_id, memo_v, evidence_v) -> dict:
    from quality import modules  # noqa: F401

    reason = (
        f"run QC review budget exhausted ({MAX_QC_REVIEWS_PER_RUN} reviews); "
        "the claims check still gates this memo"
    )
    assessments = [
        records.assessment(
            module=spec["name"],
            module_version=spec["version"],
            memo_version=memo_v,
            evidence_version=evidence_v,
            outcome="unexecuted",
            summary=reason,
        )
        for spec in registry.ordered()
    ]
    verdict, reasons = policy.aggregate_review([], {}, tasks, skipped=True)
    review = {
        "id": review_id,
        "at": _now(),
        "memo_version": memo_v,
        "evidence_version": evidence_v,
        "skipped": reason,
        "assessments": assessments,
        "verdict": verdict,
        "state_reasons": reasons,
        "repair_feedback": [],
        "tasks_opened": [],
        "tasks_resolved": [],
        "regressions": [],
        "open_tasks": _open_tasks(tasks),
        "unresolved_tasks": _unresolved_tasks(tasks),
        "claims_stale": case.claims_staleness(),
    }
    qc_reviews.append(review)
    case.data["assessments"].extend(assessments)
    case._save("qc_reviewed", "quality", {"review": review_id, "skipped": True})
    return {"verdict": verdict, "repair_feedback": [], "review": review}


def _run_modules(ctx, memo, memo_v, evidence_v, prior) -> list:
    assessments = []
    for spec in registry.ordered():
        assessments.append(
            _run_one(ctx, memo, memo_v, evidence_v, prior, spec)
        )
        prior[spec["name"]] = assessments[-1]
    return assessments


def _run_one(ctx, memo, memo_v, evidence_v, prior, spec) -> dict:
    name = spec["name"]
    base = dict(
        module=name,
        module_version=spec["version"],
        memo_version=memo_v,
        evidence_version=evidence_v,
    )

    def skip(outcome: str, summary: str) -> dict:
        return records.assessment(outcome=outcome, summary=summary, **base)

    for dep in spec.get("dependencies") or []:
        dep_outcome = (prior.get(dep) or {}).get("outcome")
        if dep_outcome in ("unavailable", "unexecuted"):
            return skip(
                "unexecuted", f"dependency {dep} is {dep_outcome}; not run"
            )
    declared_calls = (spec.get("budget") or {}).get(
        "max_judge_calls_per_review", 0
    )
    if declared_calls > 0 and _over_budget(ctx):
        # Before-call enforcement: the loop checks the budget between
        # turns, but a review stacks judge calls inside one turn, so QC
        # pre-checks each module that declares judge calls. One
        # in-flight call can still overshoot by its own size; the
        # loop's between-turn check then pauses before the next call.
        return skip(
            "unexecuted",
            "run token budget exhausted; the claims check still runs",
        )
    try:
        applies, reason = spec["applies"](ctx, memo, prior)
    except Exception as error:
        return skip("unavailable", f"applicability check failed: {_short(error)}")
    if not applies:
        return skip("inapplicable", reason or "module does not apply")
    try:
        assessment = spec["run"](
            ctx,
            memo,
            {"memo_version": memo_v, "evidence_version": evidence_v, "prior": prior},
        )
    except Exception as error:
        return skip("unavailable", f"module raised: {_short(error)}")
    if (
        not isinstance(assessment, dict)
        or assessment.get("outcome") not in records.OUTCOMES
    ):
        return skip("unavailable", f"module {name} returned an invalid assessment")
    return assessment


def _over_budget(ctx) -> bool:
    from agent_kit.progress import tokens_spent
    from agent_kit.settings import run_token_budget

    budget = run_token_budget()
    return budget is not None and tokens_spent(ctx.root, ctx.run_id) >= budget


def _settle(memo_v, evidence_v, review_id, prior, assessments, tasks, state):
    opened, resolved = [], []
    demanded: dict[str, str] = {}
    detect = prior.get("counterevidence") or {}
    material = prior.get("objection-materiality") or {}
    by_fact = {
        o.get("fact_id"): o for o in detect.get("findings", []) if isinstance(o, dict)
    }
    # Material defects demand repair; immaterial ones resolve as qualified.
    if material.get("outcome") == "failed":
        for verdict in material.get("findings", []):
            if not isinstance(verdict, dict) or not verdict.get("material"):
                continue
            objection = by_fact.get(verdict.get("fact_id"), {})
            key = _detection_key(objection)
            if _ensure_detection_task(tasks, state, key, objection, memo_v, evidence_v):
                opened.append(key)
            elif tasks[key]["status"] != "open":
                tasks[key]["status"] = "open"
                tasks[key]["disposition"] = None
                tasks[key]["history"].append("reopened: objection material again")
            history = f"confirmed material: {verdict.get('reason', '')}".strip()
            tasks[key]["history"].append(history)
            if _demandable("counterevidence"):
                demanded[key] = _repair_line(tasks[key])
    # Objections no judge could assess stay open as pending, without blocking.
    if detect.get("outcome") == "failed" and material.get("outcome") in (
        "unavailable",
        "unexecuted",
    ):
        for objection in by_fact.values():
            key = _detection_key(objection)
            if _ensure_detection_task(
                tasks, state, key, objection, memo_v, evidence_v
            ):
                opened.append(key)
                tasks[key]["history"].append(
                    f"pending: materiality {material.get('outcome')}"
                )
    if material.get("outcome") in ("passed", "failed"):
        for verdict in material.get("findings", []):
            if (
                not isinstance(verdict, dict)
                or verdict.get("material")
                or verdict.get("fact_id") not in by_fact
            ):
                continue
            key = _detection_key(by_fact[verdict["fact_id"]])
            if key in tasks and tasks[key]["status"] == "open":
                _resolve(
                    tasks[key],
                    "qualified",
                    f"not material: {verdict.get('reason', '')}".strip(),
                )
                resolved.append(key)
    # Open tasks close only when the re-judge confirms the current
    # wording addresses the objection. Detection-clean alone — a bare
    # citation, a renamed finding, a deleted claim — never resolves,
    # and an unavailable judge leaves every task open.
    if material.get("outcome") in ("passed", "failed"):
        resolutions = {
            r.get("task"): r
            for r in (material.get("resolutions") or [])
            if isinstance(r, dict)
        }
        for key, task in tasks.items():
            if task["module"] != "counterevidence" or task["status"] != "open":
                continue
            resolution = resolutions.get(key)
            if resolution is None:
                continue
            reason = str(resolution.get("reason", "")).strip()
            if resolution.get("addressed"):
                _resolve(task, "resolved", f"addressed: {reason}")
                resolved.append(key)
            else:
                task["history"].append(f"still unaddressed: {reason}")
                if _demandable("counterevidence"):
                    demanded[key] = _repair_line(task)
    # Scope defects demand restore-or-amend; restored/amended items resolve.
    # A recurring finding reopens its task: resolving once must not
    # license removing the question again.
    scope = prior.get("checklist-scope") or {}
    if scope.get("outcome") == "failed":
        for finding in scope.get("findings", []):
            if not isinstance(finding, dict):
                continue
            item = finding.get("item", "")
            kind = finding.get("kind", "removed")
            key = f"checklist-scope:{item}"
            if key not in tasks:
                state["task_seq"] = state.get("task_seq", 0) + 1
                tasks[key] = {
                    "id": f"T{state['task_seq']}",
                    "key": key,
                    "module": "checklist-scope",
                    "kind": kind,
                    "target": {
                        "memo": memo_v,
                        "evidence": evidence_v,
                        "unit": None,
                    },
                    "description": _scope_description(item, kind),
                    "resolution": _scope_resolution(kind),
                    "status": "open",
                    "attempts": 0,
                    "disposition": None,
                    "history": [f"opened by {review_id}"],
                }
                opened.append(key)
            else:
                task = tasks[key]
                if task["status"] != "open":
                    task["status"] = "open"
                    task["disposition"] = None
                    task["history"].append(f"reopened at {review_id}")
                task["kind"] = kind
                task["description"] = _scope_description(item, kind)
                task["resolution"] = _scope_resolution(kind)
                note = "still missing" if kind == "removed" else "still unamended"
                task["history"].append(f"{note} at {review_id}")
            if _demandable("checklist-scope"):
                demanded[key] = _repair_line(tasks[key])
    if scope.get("outcome") == "passed":
        for key, task in tasks.items():
            if (
                task["module"] == "checklist-scope"
                and task["status"] == "open"
            ):
                note = (
                    "wording restored or amendment recorded"
                    if task.get("kind") == "changed"
                    else "question restored"
                )
                _resolve(task, "resolved", note)
                resolved.append(key)
    # Per-issue attempts: the third strike marks the task unresolved and
    # the review proceeds to acceptance (qualified completion).
    for key in sorted(demanded):
        task = tasks[key]
        task["attempts"] += 1
        if task["attempts"] > MAX_REPAIR_ATTEMPTS_PER_ISSUE:
            _resolve(
                task,
                "unresolved",
                f"still open after {task['attempts']} reviews; "
                "proceeding to acceptance",
            )
            resolved.append(key)
            del demanded[key]
    regressions = _regressions(state, detect)
    feedback = []
    for key in sorted(demanded):
        line = demanded[key]
        if any(key.startswith(f"counterevidence:{unit}:") for unit in regressions):
            line = f"REGRESSION: {line}"
            tasks[key]["history"].append("regressed: failed again after passing")
        feedback.append(line)
    verdict, reasons = policy.aggregate_review(assessments, demanded, tasks)
    return {
        "id": review_id,
        "at": _now(),
        "memo_version": memo_v,
        "evidence_version": evidence_v,
        "assessments": assessments,
        "verdict": verdict,
        "state_reasons": reasons,
        "repair_feedback": feedback,
        "tasks_opened": opened,
        "tasks_resolved": resolved,
        "regressions": regressions,
        "open_tasks": _open_tasks(tasks),
        "unresolved_tasks": _unresolved_tasks(tasks),
    }


def _scope_description(item: str, kind: str) -> str:
    if kind == "changed":
        return (
            f"checklist question {item!r} was reworded after the baseline "
            "without a recorded amendment"
        )
    return f"checklist question {item!r} was removed after the review baseline"


def _scope_resolution(kind: str) -> str:
    if kind == "changed":
        return (
            "restore the original wording, or record an amendment "
            "explaining how the original obligation is preserved"
        )
    return (
        "restore the original question, or escalate the item "
        "to a person with the reason recorded"
    )


def _demandable(module: str) -> bool:
    """Only required modules can demand repair; advisory findings never block."""
    return policy.obligation_of(registry.MODULES.get(module, {})) == "required"


def _open_tasks(tasks) -> list:
    return sorted(k for k, t in tasks.items() if t["status"] == "open")


def _unresolved_tasks(tasks) -> list:
    return sorted(k for k, t in tasks.items() if t["status"] == "unresolved")


def _detection_key(objection: dict) -> str:
    return (
        f"counterevidence:{objection.get('unit_id', '')}:{objection.get('fact_id', '')}"
    )


def _ensure_detection_task(tasks, state, key, objection, memo_v, evidence_v):
    if key in tasks:
        return False
    state["task_seq"] = state.get("task_seq", 0) + 1
    tasks[key] = {
        "id": f"T{state['task_seq']}",
        "key": key,
        "module": "counterevidence",
        "fact_id": objection.get("fact_id", ""),
        "target": {
            "memo": memo_v,
            "evidence": evidence_v,
            "unit": objection.get("unit_id"),
        },
        "description": str(objection.get("reason", "")),
        "resolution": (
            f"cite {objection.get('fact_id', '')} in the finding, or cover "
            "it in gaps and survive re-review"
        ),
        "status": "open",
        "attempts": 0,
        "disposition": None,
        "history": ["opened"],
    }
    return True


def _resolve(task, disposition, note) -> None:
    task["status"] = "resolved" if disposition != "unresolved" else "unresolved"
    task["disposition"] = disposition
    task["history"].append(note)


def _repair_line(task) -> str:
    return f"{task['description']} Resolution: {task['resolution']}"


def _regressions(state, detect) -> list:
    """Units failing detection now that passed at the last executed review."""
    if detect.get("outcome") not in ("passed", "failed"):
        return []
    failed_now = {
        o.get("unit_id", "")
        for o in detect.get("findings", [])
        if isinstance(o, dict)
    }
    failed_before = set((state.get("detection_failed_units") or []))
    regressions = sorted(u for u in failed_now if u and u not in failed_before)
    if not state.get("detection_baselined"):
        state["detection_baselined"] = True
        state["detection_failed_units"] = sorted(failed_now)
        return []
    state["detection_failed_units"] = sorted(failed_now)
    return regressions


def _short(value: object, limit: int = 160) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[:limit] + "…"


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()
