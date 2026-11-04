"""Orchestrated iterative arm vs single-pass baseline (mechanics).

Each fixture runs twice through the same scripted drivers: once
under the full orchestrator (invalidation, reclassification,
contradiction integration, budgets), once as a single pass per
question with no feedback (premise updates dropped, no
reclassification, no re-answers). Final-state grades plus cost
diagnostics. Run with:

    cd agent && .venv/bin/python -m eval.orchestration_run

Scoring (frozen with the fixtures; denominators in every report):
  statuses              questions ending in the expected state
  contradictions        planted contradictions detected and integrated
  invalidations         premise bumps staling exactly the expected set
  preservation          unaffected findings identical (single pass each)
  reclassification      classification history matches truth
  disclosures           expected limitation keys named
  support               minimum evidence links per question
  conditions            downstream answers carry unsettled deps
  cost                  spend within caps; baseline comparison stated

Scripted drivers prove loop mechanics only — never judgment.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_kit.orchestration import contracts  # noqa: E402
from agent_kit.orchestration.engines import EngineFailed  # noqa: E402
from agent_kit.orchestration.orchestrator import Orchestrator  # noqa: E402
from eval import orchestration_cases  # noqa: E402

REPORT_KEYS = frozenset(
    {
        "case",
        "cases_version",
        "client",
        "semantic_quality",
        "orchestrated",
        "baseline",
        "complete",
    }
)


class FixtureDriver:
    """One scripted driver serving every engine for a fixture.

    Script queues pop per call; exhausted queues repeat their
    last response (the driver keeps finding the same thing);
    unscripted questions get benign defaults. Listed failures
    raise EngineFailed a fixed number of times, then behave.
    """

    def __init__(self, case: dict) -> None:
        self.queues = {qid: list(items)
                       for qid, items in case.get("scripts", {}).items()}
        self.last: dict[str, dict] = {}
        self.failures = dict(case.get("failures", {}))
        self.calls: list[tuple[str, str]] = []

    def _next(self, method: str, qid: str, default: dict) -> dict:
        self.calls.append((method, qid))
        if self.failures.get(qid, 0) > 0:
            self.failures[qid] -= 1
            raise EngineFailed(f"scripted {method} failure for {qid}")
        queue = self.queues.setdefault(qid, [])
        if queue:
            self.last[qid] = queue.pop(0)
        return dict(self.last.get(qid, default))

    def gather(self, question, needs):
        return self._next("gather", question["id"],
                          {"facts": [], "coverage": "complete"})

    def assess(self, question, evidence):
        return self._next("assess", question["id"], {"elements": []})

    def check(self, question):
        return self._next("check", question["id"],
                          {"passed": True, "problems": []})

    def fix(self, question, defects):
        return self._next("fix", question["id"], {})

    def challenge(self, position):
        self.calls.append(("challenge", "-"))
        return {"counterarguments": "considered", "risks": "stated"}


def _drivers(case: dict) -> dict:
    one = FixtureDriver(case)
    return {"research": one, "analysis": one, "verification": one,
            "repair": one, "review": one}


def _provider(case: dict):
    def provide(question, state):
        del state
        return {"transcripts": case.get("transcripts", {}),
                "evidence": case.get("evidence", {}).get(
                    question["id"], [])}
    return provide


def _seed(orch: Orchestrator, case: dict) -> None:
    for premise in case.get("premises", []):
        added = contracts.add_premise(
            orch.state, premise["statement"], source="fixture"
        )
        orch.state["premises"][premise["id"]] = orch.state[
            "premises"
        ].pop(added["id"])
        orch.state["premises"][premise["id"]]["id"] = premise["id"]
    orch.classify()
    orch.seed_questions(case.get("seeds", []))


def run_orchestrated(name: str) -> dict:
    """Full orchestrator run; returns the run record + state."""
    case = orchestration_cases.CASES[name]
    orch = Orchestrator({}, _drivers(case), context=dict(case["context"]))
    orch.context_provider = _provider(case)
    _seed(orch, case)
    report = orch.run()
    report["state"] = {
        "questions": orch.state["questions"],
        "findings": orch.state["findings"],
    }
    return report


def run_baseline(name: str) -> dict:
    """Single pass per question; feedback dropped. Returns same shape."""
    case = orchestration_cases.CASES[name]
    orch = Orchestrator({}, _drivers(case), context=dict(case["context"]))
    orch.context_provider = _provider(case)
    _seed(orch, case)
    ran: set[str] = set()
    while True:
        ready = [q for q in contracts.ready_questions(orch.state)
                 if q["id"] not in ran]
        if not ready:
            break
        for question in orch._order(ready):
            ran.add(question["id"])
            try:
                orch._run_question(question)
            except contracts.BudgetExhausted:
                break
    report = orch._finish(orch._approval())
    report["state"] = {
        "questions": orch.state["questions"],
        "findings": orch.state["findings"],
    }
    return report


def _grade_arm(name: str, report: dict, baseline: bool) -> dict:
    truth = orchestration_cases.CASES[name][
        "baseline_truth" if baseline else "truth"
    ]
    checks: dict[str, bool] = {}
    questions = report["state"]["questions"]
    if baseline:
        checks["outcome"] = report["outcome"] == truth["outcome"]
        checks["open_contradictions"] = (
            sum(1 for c in report["contradictions"]
                if c["status"] == "open")
            == truth["open_contradictions"]
        )
        checks["classification"] = (
            [h["task_type"] for h in report["classification_history"]]
            == truth["classification"]
        )
        return {"checks": checks, "complete": all(checks.values()),
                "spend": report["spend"]}
    checks["outcome"] = report["outcome"] == truth["outcome"]
    checks["statuses"] = all(
        questions.get(qid, {}).get("status") == status
        for qid, status in truth["statuses"].items()
    ) and set(questions) == set(truth["statuses"])
    integrated = [c for c in report["contradictions"]
                  if c["status"] in ("resolved", "disclosed")]
    checks["contradictions"] = (
        len(integrated) == truth["contradictions_integrated"]
        and all(c["status"] != "open" for c in report["contradictions"])
    )
    observed_inv = [
        {"premise": i["premise"], "staled": sorted(i["staled"])}
        for i in report["invalidations"]
    ]
    checks["invalidations"] = observed_inv == truth["invalidations"]
    preserved = True
    for qid in truth.get("preserved", []):
        history = questions.get(qid, {}).get("history", [])
        passes = sum(1 for h in history
                     if h.get("change", "").endswith("->in_progress"))
        stale_findings = [
            f for f in report["state"]["findings"].values()
            if f.get("question_id") == qid and f.get("stale")
        ]
        if passes != 1 or stale_findings:
            preserved = False
    checks["preservation"] = preserved
    checks["reclassification"] = (
        [h["task_type"] for h in report["classification_history"]]
        == truth["reclassified"]
    )
    disclosed = " ".join(report["disclosures"])
    checks["disclosures"] = all(
        key in disclosed for key in truth.get("disclosed_keys", [])
    )
    checks["support"] = all(
        len(questions.get(qid, {}).get("support", [])) >= minimum
        for qid, minimum in truth.get("min_support", {}).items()
    )
    checks["escalations"] = sorted(
        qid for qid, q in questions.items() if q["status"] == "escalated"
    ) == sorted(truth.get("escalated", []))
    checks["conditions"] = all(
        questions.get(qid, {}).get("conditions", []) == expected
        for qid, expected in truth.get("conditions", {}).items()
    )
    spend = report["spend"]
    checks["cost"] = (
        spend["tool_calls"] <= 120 and spend["engine_passes"] <= 24
        and spend["iterations"] <= 40 and spend["judge_calls"] <= 12
    )
    return {"checks": checks, "complete": all(checks.values()),
            "spend": spend}


def run_case(name: str) -> dict:
    """Both arms plus grades."""
    orchestrated = run_orchestrated(name)
    baseline = run_baseline(name)
    graded_orch = _grade_arm(name, orchestrated, baseline=False)
    graded_base = _grade_arm(name, baseline, baseline=True)
    for report in (orchestrated, baseline):
        report.pop("state", None)
    return {
        "case": name,
        "cases_version": orchestration_cases.CASES_VERSION,
        "client": "scripted",
        "semantic_quality": (
            "not measured: drivers are scripted; this proves "
            "control-plane mechanics (ordering, invalidation, "
            "reclassification, budgets, approval), not judgment"
        ),
        "orchestrated": graded_orch,
        "baseline": graded_base,
        "complete": graded_orch["complete"] and graded_base["complete"],
    }


def run_all() -> list:
    """Every fixture, in name order."""
    return [run_case(name)
            for name in sorted(orchestration_cases.CASES)]


def main() -> None:
    with contextlib.redirect_stdout(io.StringIO()):
        reports = run_all()
    print(json.dumps(reports, indent=2))


if __name__ == "__main__":
    main()
