"""Central orchestration engine: plan, order, bound, invalidate, approve.

Deterministic control plane over case records. The orchestrator
classifies the task, seeds a question DAG, runs engines in
dependency order under budgets, integrates findings, invalidates
exactly the dependents of changed premises, reclassifies when
classification premises break, and approves completion only
against the task quality bar — otherwise disclosing limits.
"""

from __future__ import annotations

import re

from . import contracts, engines

CLASS_TO_ENGINE = {
    "evidence": "research",
    "analysis": "analysis",
    "verify": "verification",
    "repair": "repair",
    "review": "review",
}


def _peer_foundation():
    """Peer FQ seam when importable: (seeds_fn, gate_fn), else Nones."""
    try:
        from agent_kit.reasoning import (  # noqa: PLC0415
            orchestration_seeds,
            validate_decomposition,
        )
    except ImportError:
        return None, None
    return orchestration_seeds, validate_decomposition


class Orchestrator:
    """Hierarchical iterative run driver (see module docstring)."""

    def __init__(self, data: dict, drivers: dict,
                 caps: dict | None = None,
                 context: dict | None = None,
                 commit=None) -> None:
        self.data = data
        self.commit = commit or (lambda event, detail: None)
        self.drivers = drivers
        self.context = context or {}
        self.context_provider = None
        self.budgets = contracts.Budgets(caps)
        state = data.setdefault("orchestration", contracts.new_state())
        for key, value in contracts.new_state().items():
            state.setdefault(key, value)
        self.state = state
        self._validate_state()

    def _validate_state(self) -> None:
        for qid, question in self.state["questions"].items():
            if not isinstance(question, dict):
                raise contracts.PlanError(
                    f"corrupt orchestration state at {qid!r}: refusing to plan"
                )
            missing = [k for k in ("status", "class", "deps", "history")
                       if k not in question]
            if missing:
                raise contracts.PlanError(
                    f"corrupt orchestration state at {qid!r} "
                    f"(missing {', '.join(missing)}): refusing to plan"
                )
            if question["status"] not in contracts.QUESTION_STATUSES:
                raise contracts.PlanError(
                    f"corrupt orchestration state at {qid!r} "
                    f"(unknown status {question['status']!r}): refusing "
                    "to plan"
                )

    def classify(self) -> dict:
        """Classify (or reclassify) the task; premises become tracked."""
        classification = contracts.classify_task(self.context)
        if classification["task_type"] == "unknown":
            self._escalate(
                "unknown task type: refusing to guess the work plan"
            )
        for premise in classification["premises"]:
            if premise["id"] not in self.state["premises"]:
                added = contracts.add_premise(
                    self.state, premise["statement"], source="classification"
                )
                # Stable classification premise ids for reclassification.
                self.state["premises"][premise["id"]] = self.state[
                    "premises"
                ].pop(added["id"])
                self.state["premises"][premise["id"]]["id"] = premise["id"]
        self.state["classification"] = classification
        self.state["classification_history"].append(
            {"task_type": classification["task_type"],
             "engines": list(classification["engines"])}
        )
        missing = [e for e in classification["engines"]
                   if e not in self.drivers]
        if missing:
            raise contracts.PlanError(
                "missing drivers for required engines: "
                + ", ".join(sorted(missing))
            )
        self._pin_policy(classification, "classify")
        self.commit("orchestration_classified", classification["task_type"])
        return classification

    def _pin_policy(self, classification: dict, basis: str) -> None:
        """Freeze the run's requirements: plane, bar, engines, task type.

        Downstream checks read the pin, never a re-derived label:
        a missing validator, malformed state, or empty gate can
        fail loudly against the pin but can never silently
        re-derive a weaker success.
        """
        if self.state.get("policy"):
            self.state["policy_history"].append(self.state["policy"])
        self.state["policy"] = {
            "plane": contracts.PLANE_VERSION,
            "task_type": classification.get("task_type", ""),
            "bar": dict(classification.get("bar", {})),
            "engines": list(classification.get("engines", [])),
            "basis": str(basis)[:200],
            "pinned_at_inv": len(self.state["invalidations"]),
        }

    def seed_from_foundation(self, case_data: dict,
                             gate_task_type: str = "",
                             waived=None, categories=None) -> list[str]:
        """Seed the DAG from validated foundational-question records.

        The peer's FQ layer owns plan shape: its converter maps
        records to control-plane seeds, and its decomposition
        gate (run here when gate_task_type names one, in the
        gate's native FQ schema) refuses invalid plans before
        any question is proposed. Problems raise PlanError;
        advisories are recorded, never dropped.
        """
        seeds_fn, gate_fn = _peer_foundation()
        if seeds_fn is None:
            raise contracts.PlanError(
                "cannot seed from foundation: the peer FQ module "
                "is not importable"
            )
        records = [a for a in (case_data.get("foundational") or [])
                   if isinstance(a, dict)] if isinstance(case_data, dict) else []
        if gate_task_type:
            if gate_fn is None:  # pragma: no cover - same import as seeds_fn
                raise contracts.PlanError(
                    "cannot run the decomposition gate: the peer FQ "
                    "module is not importable"
                )
            problems, advisory = gate_fn(records, waived or [],
                                         categories or [], gate_task_type)
            self.state["decomposition_advisory"].extend(
                str(a)[:300] for a in advisory
            )
            if problems:
                raise contracts.PlanError(
                    "decomposition gate refused the plan: "
                    + "; ".join(str(p)[:200] for p in problems[:5])
                )
            self.commit("orchestration_gate_passed", gate_task_type)
        ids = self.seed_questions(seeds_fn(case_data))
        added = self._complete_bar(ids)
        self.state["seed_source"] = (
            f"foundation:{len(ids)} questions"
            + (f" +{len(added)} bar completion" if added else "")
            + (f" (gated: {gate_task_type})" if gate_task_type else "")
        )
        return ids

    def _complete_bar(self, ids: list[str]) -> list[str]:
        """Propose the bar-required classes the seeds lack.

        FQ records map to evidence/analysis/review, so a seeded
        DAG can never satisfy a bar needing verify or repair;
        without completion every FQ-seeded run would disclose.
        Added questions chain after the seeds (verify over all
        seeds, then repair, then review), mirroring what
        reclassification does for newly required classes.
        """
        if not self.state["classification"]:
            self.classify()
        task_type = self.state["classification"].get("task_type", "")
        required = self.state["classification"].get("bar", {}).get(
            "required_classes", ())
        have = {q["class"] for q in self.state["questions"].values()}
        missing = [n for n in ("evidence", "analysis", "verify",
                               "repair", "review")
                   if n in required and n not in have]
        seeded = list(ids)
        chain = ""
        added = []
        for need in missing:
            if need in ("evidence", "analysis"):
                deps: list[str] = []
            elif chain:
                deps = [chain]
            else:
                deps = list(seeded)
            text = f"Address {need} under {task_type}"
            try:
                question = contracts.add_question(self.state, text, need,
                                                 deps=deps)
            except contracts.PlanError:
                # Only the redundancy refusal can fire here (deps
                # are valid by construction): disambiguate once.
                question = contracts.add_question(
                    self.state, text + " (control-plane completion)",
                    need, deps=deps)
            if need in ("evidence", "analysis"):
                seeded.append(question["id"])
            else:
                chain = question["id"]
            added.append(question["id"])
        if added:
            self.commit("orchestration_bar_completed", ",".join(added))
        return added

    def seed_questions(self, seeds: list[dict]) -> list[str]:
        """Propose the initial question set (text/class/deps/premises)."""
        ids = []
        for seed in seeds:
            question = contracts.add_question(
                self.state,
                seed.get("text", ""),
                seed.get("class", "evidence"),
                deps=seed.get("deps", []),
                premises=seed.get("premises", []),
                issue=seed.get("issue", ""),
            )
            ids.append(question["id"])
        self.commit("orchestration_seeded", ",".join(ids))
        return ids

    def run(self) -> dict:
        """Execute the bounded iterative loop; return the run record."""
        if not self.state["classification"]:
            self.classify()
        if self.state["classification"].get("task_type") == "unknown":
            return self._finish("escalated: unknown task type")
        quiet_passes = 0
        try:
            while True:
                ready = contracts.ready_questions(self.state)
                if not ready:
                    break
                progressed = False
                for question in self._order(ready):
                    made_progress, updates = self._run_question(question)
                    if made_progress:
                        progressed = True
                    self._apply_premise_updates(updates)
                self._reconcile_contradictions()
                quiet_passes = 0 if progressed else quiet_passes + 1
                if quiet_passes >= self.budgets.caps["no_progress_passes"] + 1:
                    self._disclose(
                        "no question closed and no support grew in "
                        f"{quiet_passes} consecutive passes"
                    )
                    break
                if self._maybe_reclassify():
                    quiet_passes = 0
        except contracts.BudgetExhausted as error:
            self._disclose(f"budget exhausted: {error}")
            for question in self.state["questions"].values():
                if question["status"] not in contracts.TERMINAL_QUESTION_STATUSES:
                    self._escalate(
                        f"unfinished at budget end: {question['id']}"
                    )
        return self._finish(self._approval())

    def _order(self, ready: list[dict]) -> list[dict]:
        order = {name: index for index, name in enumerate(engines.ENGINE_ORDER)}
        return sorted(
            ready,
            key=lambda q: (order.get(CLASS_TO_ENGINE.get(q["class"], ""), 99),
                           q["id"]),
        )

    def _run_question(self, question: dict) -> tuple[bool, list]:
        """One engine pass; returns (progress, premise_updates)."""
        if question["status"] in contracts.TERMINAL_QUESTION_STATUSES:
            return False, []
        if question["status"] == "proposed":
            contracts.set_status(self.state, question["id"], "ready",
                                 "dependencies met")
        if question["status"] in ("blocked", "stale"):
            contracts.set_status(self.state, question["id"], "ready",
                                 "re-queued")
        if question["status"] == "ready":
            contracts.set_status(self.state, question["id"], "assigned",
                                 CLASS_TO_ENGINE[question["class"]])
        contracts.set_status(self.state, question["id"], "in_progress",
                             "engine pass started")
        engine_cls = engines.REGISTRY[CLASS_TO_ENGINE[question["class"]]]
        ctx = self._engine_ctx(question)
        try:
            result = engine_cls().run(question, ctx)
        except contracts.BudgetExhausted:
            raise
        except Exception as error:  # driver/model failure is a finding
            question["failures"] += 1
            question["history"].append(
                {"change": "engine_failed", "detail": str(error)[:200]}
            )
            if question["failures"] >= self.budgets.caps["max_failures"]:
                contracts.set_status(self.state, question["id"], "escalated",
                                     f"failed {question['failures']} times")
                self._escalate(
                    f"{question['id']} failed repeatedly: {error}"
                )
            else:
                contracts.set_status(self.state, question["id"], "blocked",
                                     str(error)[:200])
            return False, []
        return self._integrate(question, result), list(
            result.get("premise_updates", []) or []
        )

    def _engine_ctx(self, question: dict) -> dict:
        ctx = {
            "drivers": self.drivers,
            "budgets": self.budgets,
            "contradictions": self.state["contradictions"],
            "defects": [],
            "position": {},
        }
        if self.context_provider is not None:
            ctx.update(self.context_provider(question, self.state) or {})
        return ctx

    def _integrate(self, question: dict, result: dict) -> bool:
        """Merge one engine result; returns True on progress."""
        qid = question["id"]
        progressed = False
        self.state["spend"]["engine_passes"] += result.get("passes", 0)
        self.state["spend"]["tool_calls"] += result.get("tool_calls", 0)
        self.state["spend"]["judge_calls"] += result.get("judge_calls", 0)
        self.state["spend"]["iterations"] += result.get("iterations", 0)
        for finding in result.get("findings", []) or []:
            if not self._finding_repeats(qid, result.get("engine"), finding):
                progressed = True
            self.state["finding_seq"] += 1
            fid = f"F{self.state['finding_seq']}"
            self.state["findings"][fid] = {
                "id": fid,
                "question_id": qid,
                "engine": result.get("engine"),
                "finding": finding,
                "stale": False,
            }
        for ref in result.get("support", []) or []:
            contracts.link_support(
                self.state, qid, ref.get("premise", ""),
                int(ref.get("premise_version", 1)), ref.get("refs", []),
            )
            progressed = True
        for finding in result.get("findings", []) or []:
            if not isinstance(finding, dict):
                continue
            for pid in question.get("premises", []) or [""]:
                premise = self.state["premises"].get(pid, {})
                entry = {"premise": pid,
                         "premise_version": premise.get("version", 1),
                         "refs": [finding]}
                if entry not in question["support"]:
                    question["support"].append(entry)
                    progressed = True
        for contradiction in result.get("contradictions", []) or []:
            self._record_contradiction(qid, contradiction)
        for note in result.get("uncertainty", []) or []:
            question["history"].append({"change": "uncertainty",
                                       "detail": str(note)[:200]})
        gaps = list(result.get("gaps", []) or [])
        unresolved = list(result.get("unresolved", []) or [])
        if result.get("verified") and not unresolved:
            question["answer_version"] += 1
            question["answered_at_inv"] = len(self.state["invalidations"])
            question["conditions"] = [
                f"{dep} is {status or 'unsettled'}"
                for dep, status in contracts.unsettled_deps(self.state, qid)
            ]
            contracts.set_status(self.state, qid, "answered",
                                 f"v{question['answer_version']}")
            question["stagnant_passes"] = 0
            return True
        if gaps or unresolved:
            question["stagnant_passes"] = (
                0 if progressed else question["stagnant_passes"] + 1
            )
            if (question["stagnant_passes"]
                    >= self.budgets.caps["no_progress_passes"]):
                self._disclose_question(
                    qid, gaps + unresolved,
                    f"no progress in {question['stagnant_passes']} passes",
                )
                return progressed
            contracts.set_status(self.state, qid, "blocked",
                                 "; ".join((gaps + unresolved)[:2]))
            return progressed
        question["answer_version"] += 1
        question["answered_at_inv"] = len(self.state["invalidations"])
        question["conditions"] = [
            f"{dep} is {status or 'unsettled'}"
            for dep, status in contracts.unsettled_deps(self.state, qid)
        ]
        contracts.set_status(self.state, qid, "answered",
                             f"v{question['answer_version']}")
        return True

    def _finding_repeats(self, qid: str, engine: str,
                         finding: dict) -> bool:
        """Whether this exact finding is already recorded for the question.

        A re-run that reproduces its output byte-for-byte made no
        progress: without this, a question that keeps emitting
        findings without ever verifying would never stagnate and
        would loop until the budget died.
        """
        for recorded in self.state["findings"].values():
            if (recorded.get("question_id") == qid
                    and recorded.get("engine") == engine
                    and recorded.get("finding") == finding):
                return True
        return False

    def _record_contradiction(self, qid: str, contradiction: dict) -> None:
        about = contradiction.get("about", qid)
        sides = sorted(contradiction.get("sides", []))
        for existing in self.state["contradictions"]:
            if (existing.get("about") == about
                    and sorted(existing.get("sides", [])) == sides):
                # Open duplicates and already-settled identical
                # contradictions are not re-raised: re-answers and
                # disclosures stand until the sides change.
                return
        cid = f"C{len(self.state['contradictions']) + 1}"
        question = self.state["questions"].get(about, {})
        support = question.get("support", []) if isinstance(question, dict) else []
        self.state["contradictions"].append(
            {
                "id": cid,
                "about": about,
                "sides": list(contradiction.get("sides", [])),
                "status": "open",
                "raised_by": qid,
                "raised_at_version": question.get("answer_version", 0)
                if isinstance(question, dict) else 0,
                "raised_at_inv": len(self.state["invalidations"]),
                "raised_support": sorted(repr(e) for e in support),
                "reopens": [],
            }
        )

    @staticmethod
    def _support_signature(question: dict) -> list[str]:
        return sorted(repr(e) for e in question.get("support", []))

    def _reconcile_contradictions(self) -> None:
        """Resolve contradictions superseded by re-answers or disclosures.

        A re-answer resolves only when the about-question was
        answered after new information arrived (an invalidation
        later than the raising) AND the answer carries support
        refs that did not exist at the raising: re-running with
        byte-identical support is a repeat, not a resolution.
        Every closure writes a conflict-specific disposition
        (versions, rationale, linked assessment); disclosure
        matching is exact-token, never substring.
        """
        named = set(re.findall(r"C\d+", " ".join(self.state["disclosures"])))
        for contradiction in self.state["contradictions"]:
            if contradiction.get("status") != "open":
                continue
            if contradiction.get("id") in named:
                contradiction["status"] = "disclosed"
                contradiction["resolution"] = "named in disclosures"
                contradiction["disposition"] = {
                    "kind": "disclosed-unresolved",
                    "at_inv": len(self.state["invalidations"]),
                    "rationale": "named in disclosures without new assessment",
                }
                continue
            question = self.state["questions"].get(
                contradiction.get("about"), {})
            if not isinstance(question, dict):
                continue
            grown = [
                ref for ref in self._support_signature(question)
                if ref not in contradiction.get("raised_support", [])
            ]
            if (question.get("status") == "answered"
                    and question.get("answer_version", 0)
                    > contradiction.get("raised_at_version", 0)
                    and question.get("answered_at_inv", 0)
                    > contradiction.get("raised_at_inv", 0)
                    and grown):
                contradiction["status"] = "resolved"
                contradiction["resolution"] = (
                    "about-question re-answered at "
                    f"v{question['answer_version']} after invalidation"
                )
                contradiction["disposition"] = {
                    "kind": "resolved",
                    "at_inv": len(self.state["invalidations"]),
                    "question_version": question["answer_version"],
                    "premise_versions": {
                        entry.get("premise"): entry.get("premise_version")
                        for entry in question.get("support", [])
                        if isinstance(entry, dict)
                    },
                    "new_refs": len(grown),
                    "rationale": (
                        "re-answered under later premise versions with "
                        f"{len(grown)} new support refs; engine verified"
                    ),
                }

    def invalidate_premise(self, pid: str, statement: str, reason: str) -> list[str]:
        """Bump a premise and stale exactly its dependents. Returns them.

        Settled contradictions about staled questions reopen: a
        disposition reached under superseded premises no longer
        stands. The reopen trail is preserved on each record.
        Any recorded machine readiness is cleared with it: new
        source versions stale dependent readiness and reviews.
        """
        contracts.bump_premise(self.state, pid, statement, reason)
        if self.state.get("approval"):
            self.state["approval"] = None
            self.commit("orchestration_readiness_staled", pid)
        dependents = contracts.dependents_of(self.state, pid)
        staled = []
        for qid in dependents:
            question = self.state["questions"][qid]
            if question["status"] == "blocked":
                contracts.set_status(self.state, qid, "ready", reason)
                staled.append(qid)
            elif question["status"] in ("answered", "in_progress", "ready",
                                        "assigned"):
                contracts.set_status(self.state, qid, "stale", reason)
                staled.append(qid)
            for fid, finding in self.state["findings"].items():
                if finding.get("question_id") == qid:
                    finding["stale"] = True
        self.state["invalidations"].append(
            {"premise": pid, "reason": str(reason)[:200], "staled": staled}
        )
        reopened_at = len(self.state["invalidations"]) - 1
        for contradiction in self.state["contradictions"]:
            if (contradiction.get("status") in ("resolved", "disclosed")
                    and contradiction.get("about") in dependents):
                contradiction.setdefault("reopens", []).append(
                    {"reopened_at_inv": reopened_at, "premise": pid,
                     "prior": contradiction["status"]}
                )
                contradiction["status"] = "open"
                contradiction.pop("disposition", None)
        self.commit("orchestration_invalidated", pid)
        return staled

    def _apply_premise_updates(self, updates: list) -> None:
        """Apply engine-proposed premise changes (assumption changes)."""
        for update in updates:
            if not isinstance(update, dict):
                continue
            pid = update.get("premise", "")
            statement = str(update.get("statement", ""))
            reason = str(update.get("reason", "engine-proposed update"))
            if not statement:
                continue
            if pid and pid in self.state["premises"]:
                current = self.state["premises"][pid].get("statement", "")
                if current == statement:
                    # Re-stating the current premise is not a
                    # change: no bump, no dependents staled, no
                    # record. Without this, re-runs that repeat
                    # an update would invalidate forever.
                    continue
                self.invalidate_premise(pid, statement, reason)
            else:
                added = contracts.add_premise(self.state, statement,
                                             source="engine-proposed")
                self.state["invalidations"].append(
                    {"premise": added["id"], "reason": reason, "staled": []}
                )

    def _maybe_reclassify(self) -> bool:
        """Reclassify when classification premises break. Returns changed."""
        current = self.state["classification"].get("task_type")
        fresh = contracts.classify_task(self._live_context())
        if fresh["task_type"] != current and fresh["task_type"] != "unknown":
            self.state["classification"] = fresh
            self.state["classification_history"].append(
                {"task_type": fresh["task_type"],
                 "engines": list(fresh["engines"]),
                 "reason": "classification premises changed mid-run"}
            )
            have_classes = {q["class"] for q in self.state["questions"].values()}
            for need in fresh["bar"].get("required_classes", ()):
                if need not in have_classes:
                    contracts.add_question(
                        self.state,
                        f"Address {need} under {fresh['task_type']}",
                        need,
                    )
            self._pin_policy(fresh, "reclassification premises changed mid-run")
            self.commit("orchestration_reclassified", fresh["task_type"])
            return True
        return False

    def _live_context(self) -> dict:
        """Matter context plus what the run has learned (for reclassification)."""
        learned = dict(self.context)
        realms = set(str(r).lower() for r in learned.get("realms", []) or [])
        for finding in self.state["findings"].values():
            detail = finding.get("finding")
            if isinstance(detail, dict):
                for realm in detail.get("realms", []) or []:
                    realms.add(str(realm).lower())
                for flag in ("dispute", "transaction", "intake"):
                    if (detail.get("flags") or {}).get(flag):
                        learned[flag] = True
        if realms:
            learned["realms"] = sorted(realms)
        return learned

    #: Machine-readiness outcome. Never an attorney signature or
    #: completed human review: it certifies the bar held on the
    #: assessed snapshot, nothing more.
    OUTCOME_READY = "ready_for_review"

    def _approval(self) -> str:
        """Machine readiness iff the task quality bar holds; else disclose."""
        self._reconcile_contradictions()
        bar = self.state["classification"].get("bar", {})
        required = bar.get("required_classes", ())
        gaps = []
        by_class: dict[str, list] = {}
        for question in self.state["questions"].values():
            by_class.setdefault(question["class"], []).append(question)
        for need in required:
            settled = [q for q in by_class.get(need, [])
                       if q["status"] in ("answered", "disclosed")]
            if not settled:
                gaps.append(f"no settled {need} question")
        for question in self.state["questions"].values():
            if question["status"] == "answered" and not question["support"]:
                if not any(h.get("change") == "uncertainty"
                           for h in question["history"]):
                    gaps.append(f"{question['id']} answered without support")
        open_contradictions = [c for c in self.state["contradictions"]
                               if c.get("status") == "open"]
        if open_contradictions:
            gaps.append(
                f"{len(open_contradictions)} contradictions unintegrated"
            )
        if bar.get("need_adversarial"):
            challenged = any(
                f.get("finding", {}).get("challenge_ran")
                if isinstance(f.get("finding"), dict) else False
                for f in self.state["findings"].values()
            )
            if not challenged:
                gaps.append("adversarial challenge absent")
        if gaps:
            self._disclose("approval withheld: " + "; ".join(gaps))
            return "disclosed: quality bar unmet"
        self.state["approval"] = {"decision": "machine_ready",
                                  "bar": dict(bar),
                                  "at_inv": len(self.state["invalidations"])}
        self.commit("orchestration_ready", "quality bar met")
        return self.OUTCOME_READY

    def _disclose(self, detail: str) -> None:
        self.state["disclosures"].append(str(detail)[:300])

    def _disclose_question(self, qid: str, items: list, reason: str) -> None:
        contracts.set_status(self.state, qid, "disclosed", reason)
        self.state["disclosures"].append(
            f"{qid} disclosed ({reason}): "
            + "; ".join(str(i)[:120] for i in items[:3])
        )

    def _escalate(self, detail: str) -> None:
        self.state["escalations"].append(str(detail)[:300])

    def _finish(self, outcome: str) -> dict:
        self.commit("orchestration_finished", outcome)
        return {
            "outcome": outcome,
            "classification": self.state["classification"].get("task_type"),
            "classification_history": list(
                self.state["classification_history"]),
            "policy": dict(self.state.get("policy", {})),
            "policy_history": list(self.state.get("policy_history", [])),
            "questions": {
                qid: {"status": q["status"],
                      "answer_version": q["answer_version"],
                      "support": len(q["support"]),
                      "conditions": list(q.get("conditions", []))}
                for qid, q in self.state["questions"].items()
            },
            "findings": len(self.state["findings"]),
            "contradictions": list(self.state["contradictions"]),
            "invalidations": list(self.state["invalidations"]),
            "spend": dict(self.state["spend"]),
            "budgets": dict(self.budgets.spent),
            "disclosures": list(self.state["disclosures"]),
            "escalations": list(self.state["escalations"]),
            "approval": self.state["approval"],
        }
