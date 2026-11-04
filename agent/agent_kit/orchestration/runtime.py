"""Opt-in orchestrated runs around the real run/resume machinery.

One explicit integrated path: a coordinator selects an eligible
question, compiles a bounded source-linked briefing, runs one
bounded engine slice with drivers backed by real case reads and
real deterministic checks, validates the result, and commits
durable progress to the case record. External provider judgment
is replaced by explicit scripts; an unconfigured semantic call
fails loudly instead of defaulting to clean success.

What is real here: transcripts and quote verification, the peer
element validator, audit structural checks and defect synthesis,
revision guards, atomic case persistence, and the usage ledger.
What is scripted: which passages/elements/plans the model would
propose. No model client is constructed: judge/token usage stays
explicitly unknown, never zero.

Legacy runs never switch mode: the orchestration key appears
only after an explicit call into this module, and resume works
from committed state alone, so a new process continues exactly
where the last commit left off.
"""

from __future__ import annotations

import copy
import datetime
import json
import sys
from pathlib import Path

from . import contracts, engines
from .orchestrator import Orchestrator

MODE = "orchestrated"
WORKING_CLASSES = ("evidence", "analysis")
BRIEFING_CHARS = 2000
EVIDENCE_FACTS = 50


class UnconfiguredCall(contracts.PlanError):
    """A semantic call with no scripted judgment. Never clean success."""


class CaseDrivers:
    """Engine drivers over one case: real reads, scripted judgments.

    `scripts` maps question id -> method name -> response. A
    response of {"error": msg} raises EngineFailed (provider
    failure path). Anything unconfigured raises UnconfiguredCall.
    """

    def __init__(self, scripts: dict, coordinator=None) -> None:
        # Scripts are configuration: each driver run replays them
        # from the start, and the caller's object is never mutated
        # (a re-queued question after invalidation must not find
        # its script sequence already consumed).
        self.scripts = copy.deepcopy(scripts or {})
        self.coordinator = coordinator

    def _next(self, method: str, question: dict):
        qid = question.get("id", "?")
        try:
            response = self.scripts[qid][method]
        except (KeyError, TypeError):
            raise UnconfiguredCall(
                f"no scripted {method} judgment for {qid}: refusing "
                "to invent one"
            )
        if isinstance(response, dict) and "error" in response:
            raise engines.EngineFailed(str(response["error"])[:200])
        if self.coordinator is not None:
            self.coordinator.note_call(method, qid)
        if isinstance(response, list):
            if not response:
                raise UnconfiguredCall(
                    f"exhausted {method} script for {qid}: refusing "
                    "to repeat success"
                )
            item = response.pop(0)
            if isinstance(item, dict) and "error" in item:
                raise engines.EngineFailed(str(item["error"])[:200])
            return item
        return response

    def gather(self, question: dict, needs: dict) -> dict:
        return dict(self._next("gather", question))

    def assess(self, question: dict, evidence: list) -> dict:
        return dict(self._next("assess", question))

    def check(self, question: dict) -> dict:
        return dict(self._next("check", question))

    def fix(self, question: dict, defects: list) -> dict:
        return dict(self._next("fix", question))

    def challenge(self, position: dict) -> dict:
        qid = position.get("question_id", "?")
        return dict(self._next("challenge", {"id": qid}))


def compile_briefing(state: dict, qid: str, limit: int = BRIEFING_CHARS) -> dict:
    """Bounded source-linked briefing for one question.

    Text, class, premise versions, inherited conditions, prior
    findings capped to the budget, and open contradictions
    touching the question or its deps. Pure function of state.
    """
    question = state["questions"][qid]
    premises = {
        pid: state["premises"].get(pid, {}).get("version", 0)
        for pid in question.get("premises", [])
    }
    findings = []
    budget = limit
    for fid in sorted(state["findings"]):
        if budget <= 0:
            break
        record = state["findings"][fid]
        if record.get("stale"):
            continue
        finding = record.get("finding", {})
        if not isinstance(finding, dict):
            continue
        link = {
            "by": record.get("question_id"),
            "doc": finding.get("document_id", ""),
            "page": finding.get("page", 0),
            "quote": str(finding.get("quote", ""))[:160],
        }
        findings.append(link)
        budget -= len(json.dumps(link))
    touched = {qid, *(question.get("deps", []))}
    conflicts = [
        {"id": c.get("id"), "about": c.get("about"),
         "sides": list(c.get("sides", []))}
        for c in state["contradictions"]
        if c.get("status") == "open" and c.get("about") in touched
    ]
    return {
        "question_id": qid,
        "text": question.get("text", ""),
        "class": question.get("class", ""),
        "premises": premises,
        "conditions": list(question.get("conditions", [])),
        "findings": findings,
        "open_conflicts": conflicts,
    }


class Coordinator:
    """Owns one orchestrated run: select, brief, slice, commit."""

    def __init__(self, case, scripts: dict, allowance: dict,
                 context: dict | None = None,
                 audit_id: str = "") -> None:
        self.case = case
        self.scripts = scripts
        self.audit_id = audit_id
        self.allowance_total = int(allowance.get("total",
                                                 allowance.get("slices", 0)))
        self.reserve = int(allowance.get("reserve", 0))
        self.per_call = int(allowance.get("per_call", 10 ** 9))
        drivers = CaseDrivers(scripts, coordinator=self)
        bundle = {name: drivers for name in
                  ("research", "analysis", "verification", "repair", "review")}
        self.orch = Orchestrator(case.data, bundle, context or {})
        self.orch.context_provider = self._provide
        self._ensure_mode()

    def _provide(self, question: dict, state: dict) -> dict:
        """Real case reads for engines: transcripts and audit bundle."""
        from agent_kit import document_text

        flagged = {
            flag.get("document_id")
            for flag in (self.case.data.get("flags") or [])
            if isinstance(flag, dict) and flag.get("kind") == "privileged"
        }
        transcripts = {}
        for document in document_text.list_documents(self.case.root):
            if document["id"] in flagged:
                continue
            cached = document_text.cached_text(self.case.root,
                                               document["id"])
            if cached:
                transcripts[document["id"]] = {
                    entry["page"]: entry["text"]
                    for entry in cached.get("pages", [])
                }
        facts = [f for f in (self.case.data.get("facts") or [])
                 if isinstance(f, dict)
                 and f.get("document_id") not in flagged]
        evidence = [
            {"fact_id": fact.get("id", ""),
             "item": fact.get("item", ""),
             "document_id": fact.get("document_id", ""),
             "page": fact.get("page", 0),
             "quote": str(fact.get("quote", ""))[:500],
             "stance": fact.get("stance", "supports")}
            for fact in facts[:EVIDENCE_FACTS]
        ]
        provided: dict = {"transcripts": transcripts, "evidence": evidence,
                         "position": {"question_id": question.get("id", "?")}}
        instances = self.case.data.get("artifacts") or {}
        if self.audit_id and self.audit_id in instances:
            provided["audit"] = {
                "instance": instances[self.audit_id],
                "data": self.case.data,
                "root": self.case.root,
            }
        return provided

    def _ensure_mode(self) -> None:
        state = self.orch.state
        state["mode"] = MODE
        if "allowance" not in state:
            state["allowance"] = {"total": self.allowance_total,
                                  "used": 0, "reserve": self.reserve}
        state.setdefault("op_seq", 0)
        if "case_binding" not in state:
            root_task = self.case.ensure_root_task()
            state["case_binding"] = {
                "run_id": self.case.data.get("run_id", ""),
                "root_task": root_task.get("id", ""),
            }
        state.setdefault("briefings", {})
        self.commit("orchestration_mode_pinned")

    def note_call(self, method: str, qid: str) -> None:
        self.orch.state["op_seq"] += 1

    def commit(self, action: str) -> None:
        self.case._save(action, "orchestrator",
                        {"op_seq": self.orch.state.get("op_seq", 0)})

    def slices_left(self) -> int:
        allowance = self.orch.state["allowance"]
        return allowance["total"] - allowance["used"]

    def eligible(self) -> list[dict]:
        """Runnable questions honoring the synthesis/review reserve."""
        ready = contracts.ready_questions(self.orch.state)
        if self.slices_left() <= self.orch.state["allowance"]["reserve"]:
            return [q for q in ready if q["class"] not in WORKING_CLASSES]
        return ready

    def cancel(self, reason: str) -> dict:
        """Request cancellation: no new slices; partial progress kept.

        The request persists with run state, so a later resume
        honors it too. Accounting is untouched: used allowance
        and usage entries stay exactly as they were.
        """
        self.orch.state["cancel_requested"] = str(reason or "")[:200]
        self.commit("orchestration_cancel_requested")
        return {"cancel_requested":
                self.orch.state["cancel_requested"]}

    def run(self, seeds: list[dict] | None = None) -> dict:
        """Run slices until the allowance ends or nothing is eligible."""
        if seeds is not None and not self.orch.state["questions"]:
            self.orch.seed_questions(seeds)
            self.commit("orchestration_seeded")
        if self.orch.state.get("cancel_requested"):
            if not self.orch.state.get("cancel_acknowledged"):
                self.orch.state["cancel_acknowledged"] = True
                self.commit("orchestration_cancelled")
            return self.orch._finish(
                f"cancelled: {self.orch.state['cancel_requested']}; "
                "partial progress saved"
            )
        call_used = 0
        reserved = False
        while self.slices_left() > 0 and call_used < self.per_call:
            picked = self.eligible()
            if not picked:
                if contracts.ready_questions(self.orch.state):
                    self.orch._disclose(
                        "working allowance exhausted: synthesis/review "
                        "reserve preserved"
                    )
                    self.commit("orchestration_reserved")
                    reserved = True
                break
            question = picked[0]
            self.orch.state["briefings"][question["id"]] = compile_briefing(
                self.orch.state, question["id"]
            )
            try:
                self.orch._run_question(question)
            except contracts.BudgetExhausted as error:
                self.orch._escalate(f"engine budget exhausted: {error}")
            self.orch.state["allowance"]["used"] += 1
            call_used += 1
            self._record_usage(question)
            self.commit("orchestration_slice")
        if not contracts.ready_questions(self.orch.state):
            outcome = self.orch._approval()
            self.commit("orchestration_finished")
            return self.orch._finish(outcome)
        if self.slices_left() <= 0:
            outcome = "slices exhausted: partial progress saved"
        elif reserved:
            outcome = ("paused: working allowance exhausted, "
                       "reserve preserved")
        else:
            outcome = "paused: call slice cap reached, resume to continue"
        report = self.orch._finish(outcome)
        self.commit("orchestration_paused")
        return report

    def _record_usage(self, question: dict) -> None:
        """One usage-ledger entry per slice. Tokens explicitly unknown.

        Uses the ledger's append path directly (same package) so the
        entry keeps null token counts: no model client runs here, so
        there is nothing measured to report. usage.totals still
        aggregates these calls honestly under their purpose.
        """
        from agent_kit import usage as usage_ledger

        usage_ledger._append(
            self.case.root, self.case.data.get("run_id", ""),
            {
                "at": datetime.datetime.now(
                    datetime.timezone.utc).isoformat(),
                "purpose": f"orchestration/{question['class']}:{question['id']}",
                "model": "",
                "input_tokens": None,
                "output_tokens": None,
                "latency_ms": None,
                "failed": False,
                "error": "",
                "measured": False,
                "op": self.orch.state.get("op_seq", 0),
            },
        )


def run_orchestrated(root, run_id: str, scripts: dict, allowance: dict,
                     seeds: list[dict] | None = None,
                     context: dict | None = None,
                     audit_id: str = "") -> dict:
    """Entry point: explicit opt-in orchestrated execution."""
    from agent_kit.case_record import CaseRecord

    case = CaseRecord(Path(root), run_id)
    coordinator = Coordinator(case, scripts, allowance, context=context,
                              audit_id=audit_id)
    if not coordinator.orch.state.get("classification"):
        coordinator.orch.classify()
        coordinator.commit("orchestration_classified")
    return coordinator.run(seeds=seeds)


def main(argv: list[str]) -> int:
    """Resume-capable CLI: same root+run continues where commits left off."""
    import argparse

    parser = argparse.ArgumentParser(description="Run orchestrated slices.")
    parser.add_argument("root")
    parser.add_argument("run_id")
    parser.add_argument("--scripts", required=True)
    parser.add_argument("--slices", type=int, required=True,
                        help="parent slice allowance (pinned on first run)")
    parser.add_argument("--reserve", type=int, default=0)
    parser.add_argument("--per-call", type=int, default=10 ** 9,
                        help="max slices this invocation runs")
    parser.add_argument("--seeds", default="")
    parser.add_argument("--context", default="{}")
    parser.add_argument("--audit", default="")
    args = parser.parse_args(argv)
    scripts = json.loads(Path(args.scripts).read_text())
    seeds = json.loads(Path(args.seeds).read_text()) if args.seeds else None
    report = run_orchestrated(
        args.root, args.run_id, scripts,
        {"total": args.slices, "reserve": args.reserve,
         "per_call": args.per_call},
        seeds=seeds, context=json.loads(args.context or "{}"),
        audit_id=args.audit,
    )
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
