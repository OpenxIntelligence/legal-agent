"""Task engines: bounded internal loops behind declared contracts.

Each engine defines a problem, determines needs, acts through an
injected driver, verifies its findings, reports gaps and
uncertainty, and stops when further work is unjustified. Drivers
perform tool work; offline tests inject scripted drivers. Peer
reasoning validators plug in opportunistically when importable
(clearly labeled); built-in checks cover their absence.
"""

from __future__ import annotations

from . import contracts


class EngineFailed(RuntimeError):
    """An engine pass failed; the orchestrator counts the attempt."""


class Engine:
    """Bounded loop template. Subclasses fill the five steps."""

    name = "base"
    contract: dict = {
        "responsibility": "unassigned",
        "inputs": [],
        "outputs": [],
        "verification": "none",
        "dependencies": [],
        "stopping": "single pass",
    }
    max_passes = 3

    def run(self, question: dict, ctx: dict) -> dict:
        """Bounded internal loop; returns a structured result."""
        budgets: contracts.Budgets = ctx["budgets"]
        result: dict = {
            "engine": self.name,
            "question_id": question["id"],
            "passes": 0,
            "findings": [],
            "gaps": [],
            "contradictions": [],
            "uncertainty": [],
            "unresolved": [],
            "premise_updates": [],
            "tool_calls": 0,
            "iterations": 0,
            "judge_calls": 0,
            "verified": False,
            "notes": [],
        }
        support_before = len(question.get("support", []))
        for _ in range(self.max_passes):
            budgets.consume("engine_passes")
            budgets.consume("iterations")
            result["passes"] += 1
            result["iterations"] += 1
            # Gaps/uncertainty describe the current pass only: a later
            # pass that fixes them clears them. Findings,
            # contradictions, and premise updates accumulate.
            result["gaps"] = []
            result["uncertainty"] = []
            result["unresolved"] = []
            problem = self.define(question, ctx)
            needs = self.determine_needs(question, ctx, problem)
            acted = self.act(question, ctx, problem, needs, result)
            if isinstance(acted, dict):
                result["premise_updates"].extend(
                    acted.get("premise_updates", []) or []
                )
            self.verify(question, ctx, acted, result)
            self.assess(question, ctx, acted, result)
            if self._progressed(question, support_before, result):
                support_before = len(question.get("support", []))
                result["last_progress_pass"] = result["passes"]
            if not self.justify_more(question, ctx, result):
                break
        result["verified"] = self.verified(question, ctx, result)
        return result

    def define(self, question, ctx) -> dict:
        """The problem this engine must resolve, in its own terms."""
        return {"question_id": question["id"], "text": question.get("text", "")}

    def determine_needs(self, question, ctx, problem) -> dict:
        """Information and tools required."""
        return {}

    def act(self, question, ctx, problem, needs, result) -> dict:
        """Perform the work through the driver. Returns raw output."""
        raise NotImplementedError

    def verify(self, question, ctx, acted, result) -> None:
        """Check the output; append gaps/uncertainty, never silently pass."""

    def assess(self, question, ctx, acted, result) -> None:
        """Name gaps, contradictions, uncertainty from this pass."""

    def justify_more(self, question, ctx, result) -> bool:
        """Whether another pass is justified (default: gaps remain)."""
        return bool(result["gaps"] or result["unresolved"])

    def verified(self, question, ctx, result) -> bool:
        """Engine's own verification verdict over the accumulated result."""
        return not result["gaps"] and not result["unresolved"]

    def _progressed(self, question, support_before: int, result: dict) -> bool:
        support_now = len(question.get("support", []))
        if support_now > support_before:
            return True
        return bool(result["findings"]) and result["passes"] == 1

    def _spend_tools(self, ctx, result, count: int) -> None:
        ctx["budgets"].consume("tool_calls", count)
        result["tool_calls"] += count


class ResearchEngine(Engine):
    """Resolve evidence questions with verified quotes + coverage."""

    name = "research"
    contract = {
        "responsibility": "gather and verify evidence for evidence questions",
        "inputs": ["question", "transcripts", "coverage map"],
        "outputs": ["verified facts", "coverage report", "gaps"],
        "verification": "every quote re-checked against supplied transcripts",
        "dependencies": [],
        "stopping": "coverage complete, cap, or two passes with no new support",
    }
    max_passes = 3

    def act(self, question, ctx, problem, needs, result) -> dict:
        driver = ctx["drivers"]["research"]
        self._spend_tools(ctx, result, 1)
        return driver.gather(question, needs)

    def verify(self, question, ctx, acted, result) -> None:
        from agent_kit import document_text

        transcripts = ctx.get("transcripts", {})
        for fact in acted.get("facts", []) or []:
            if not isinstance(fact, dict):
                result["gaps"].append(
                    f"malformed fact entry from driver: {str(fact)[:80]}"
                )
                continue
            page_text = (transcripts.get(fact.get("document_id"), {}) or {}).get(
                fact.get("page"), ""
            )
            if not fact.get("quote") or not document_text.quote_on_page(
                fact.get("quote", ""), page_text
            ):
                result["gaps"].append(
                    f"unverified quote from {fact.get('document_id')} "
                    f"p{fact.get('page')}: not on the supplied page"
                )
            else:
                finding = {
                    "kind": "fact",
                    "document_id": fact.get("document_id"),
                    "page": fact.get("page"),
                    "quote": fact.get("quote"),
                    "stance": fact.get("stance", "supports"),
                    "verified": True,
                }
                # Reclassification signals ride with verified
                # facts: the orchestrator reads flags/realms from
                # findings, never from unverified driver output.
                for signal in ("flags", "realms"):
                    if fact.get(signal) is not None:
                        finding[signal] = fact.get(signal)
                result["findings"].append(finding)
        for gap in acted.get("gaps", []) or []:
            result["gaps"].append(str(gap))
        stances = {f.get("stance", "supports") for f in result["findings"]}
        if "supports" in stances and "opposes" in stances:
            supporting = [f["document_id"] for f in result["findings"]
                          if f.get("stance", "supports") == "supports"]
            opposing = [f["document_id"] for f in result["findings"]
                        if f.get("stance") == "opposes"]
            result["contradictions"].append(
                {"about": question["id"],
                 "sides": [f"supports: {','.join(sorted(set(supporting)))}",
                           f"opposes: {','.join(sorted(set(opposing)))}"]}
            )

    def assess(self, question, ctx, acted, result) -> None:
        if acted.get("coverage") == "complete" and not result["gaps"]:
            result["unresolved"] = []
        elif acted.get("coverage") != "complete":
            result["unresolved"].append("coverage incomplete")


class AnalysisEngine(Engine):
    """Assess elements with both-sides arguments and missing proof."""

    name = "analysis"
    contract = {
        "responsibility": "assess elements and counterevidence per question",
        "inputs": ["question", "recorded evidence"],
        "outputs": ["element findings", "both-sides arguments", "missing proof"],
        "verification": "peer validate_element when importable, else schema self-check",
        "dependencies": ["research"],
        "stopping": "elements assessed, cap, or no change between passes",
    }
    max_passes = 2

    def act(self, question, ctx, problem, needs, result) -> dict:
        driver = ctx["drivers"]["analysis"]
        self._spend_tools(ctx, result, 1)
        return driver.assess(question, ctx.get("evidence", []))

    def verify(self, question, ctx, acted, result) -> None:
        for element in acted.get("elements", []) or []:
            if not isinstance(element, dict):
                result["gaps"].append(
                    f"malformed element entry from driver: {str(element)[:80]}"
                )
                continue
            missing = [
                field
                for field in ("test", "finding", "for_side", "against_side")
                if not element.get(field)
            ]
            if missing:
                result["gaps"].append(
                    f"element {element.get('element', '?')} missing: "
                    + ", ".join(missing)
                )
            else:
                finding = {"kind": "element",
                           **{k: element.get(k) for k in
                              ("test", "element", "finding")}}
                for signal in ("flags", "realms"):
                    if element.get(signal) is not None:
                        finding[signal] = element.get(signal)
                result["findings"].append(finding)
        validator = _peer_validator("analysis")
        result["notes"].append(
            "peer validate_element ran"
            if validator else "schema self-check ran (peer module absent)"
        )
        if validator:
            for element in acted.get("elements", []) or []:
                if not isinstance(element, dict):
                    continue
                for problem in validator(element):
                    result["gaps"].append(f"peer validation: {problem}")
        opposing = [f for f in ctx.get("evidence", [])
                    if f.get("stance") == "opposes"]
        cited = " ".join(str(e) for e in acted.get("elements", []) or [])
        unaddressed = [
            fact for fact in opposing
            if str(fact.get("quote", ""))[:24] not in cited
        ]
        for fact in unaddressed:
            result["uncertainty"].append(
                f"opposing evidence not addressed: {fact.get('document_id')}"
            )
        if unaddressed:
            result["contradictions"].append(
                {"about": question["id"],
                 "sides": ["recorded supporting evidence",
                           f"opposing: {','.join(sorted({f.get('document_id', '?') for f in unaddressed}))}"]}
            )
        for gap in acted.get("missing", []) or []:
            result["unresolved"].append(str(gap))


class VerificationEngine(Engine):
    """Run the gates: memo rules, claims, QC, defect synthesis."""

    name = "verification"
    contract = {
        "responsibility": "verify memo and claims against evidence",
        "inputs": ["memo snapshot", "evidence snapshot", "audit bundle (optional)"],
        "outputs": ["rule/claims/QC/defect results per question"],
        "verification": "the gates themselves (all reused)",
        "dependencies": ["research", "analysis"],
        "stopping": "single pass per snapshot; re-runs only on new snapshots",
    }
    max_passes = 1

    def act(self, question, ctx, problem, needs, result) -> dict:
        audit = ctx.get("audit")
        if audit is not None:
            return self._real_checks(ctx, audit, result)
        driver = ctx["drivers"]["verification"]
        self._spend_tools(ctx, result, 1)
        return {"driver_stated": driver.check(question)}

    def _real_checks(self, ctx, audit, result) -> dict:
        from agent_kit import artifacts

        self._spend_tools(ctx, result, 2)
        rows = audit["instance"]["rows"]
        structural = artifacts.audit_structural(
            audit["root"], audit["data"], rows
        )
        defects = [
            {"category": d.get("category"), "target": d.get("target")}
            for d in artifacts.synthesize_defects(
                audit["instance"], audit["data"], audit["root"]
            )
        ]
        return {"structural": structural, "defects": defects,
                "real_checks": True}

    def verify(self, question, ctx, acted, result) -> None:
        if acted.get("real_checks"):
            for defect in acted.get("defects", []) or []:
                result["gaps"].append(
                    f"{defect['category']} on {defect['target']}"
                )
            if not acted.get("defects"):
                result["findings"].append(
                    {"kind": "verification", "outcome": "clean"}
                )
        else:
            stated = acted.get("driver_stated") or {}
            result["notes"].append("driver-stated verdicts (not system-verified)")
            for problem in stated.get("problems", []) or []:
                result["gaps"].append(str(problem))
            if stated.get("passed"):
                result["findings"].append(
                    {"kind": "verification", "outcome": "driver-stated pass"}
                )

    def justify_more(self, question, ctx, result) -> bool:
        return False

    def verified(self, question, ctx, result) -> bool:
        return not result["gaps"]


class RepairEngine(Engine):
    """Close revision-fixable defects with regression guards."""

    name = "repair"
    contract = {
        "responsibility": "revise and recheck defect findings",
        "inputs": ["open defects", "audit bundle"],
        "outputs": ["revisions", "recheck results", "repair states"],
        "verification": "positive current verdicts only (ledger rules reused)",
        "dependencies": ["verification"],
        "stopping": "fixable set repaired, cap, or a defect regresses twice",
    }
    max_passes = 3

    def determine_needs(self, question, ctx, problem) -> dict:
        audit = ctx.get("audit")
        if audit is None:
            return {}
        from agent_kit import artifacts

        defects = artifacts.synthesize_defects(
            audit["instance"], audit["data"], audit["root"]
        )
        return {"defects": [f"{d.get('category')} on {d.get('target')}"
                            for d in defects]}

    def act(self, question, ctx, problem, needs, result) -> dict:
        audit = ctx.get("audit")
        defects = needs.get("defects", [])
        if audit is None:
            driver = ctx["drivers"]["repair"]
            self._spend_tools(ctx, result, 1)
            return {"driver_stated": driver.fix(question, defects)}
        return self._real_repair(ctx, audit, defects, question, result)

    def _real_repair(self, ctx, audit, defects, question, result) -> dict:
        from agent_kit import artifacts

        driver = ctx["drivers"]["repair"]
        repaired, regressions = [], []
        for defect in defects:
            plan = driver.fix(question, [defect])
            if not plan.get("revision"):
                result["unresolved"].append(
                    f"{defect} needs evidence/amendment/judgment"
                )
                continue
            self._spend_tools(ctx, result, 2)
            revision = plan["revision"]
            row_id = revision.get("row_id") if isinstance(revision, dict) else None
            if not row_id:
                result["unresolved"].append(
                    f"{defect} has a malformed revision plan: needs "
                    "evidence/amendment/judgment"
                )
                continue
            artifacts.revise_row(
                audit["instance"], audit["data"], audit["root"],
                row_id,
                assertion=revision.get("assertion"),
                citation=revision.get("citation"),
            )
            rows = audit["instance"]["rows"]
            structural = artifacts.audit_structural(
                audit["root"], audit["data"], rows
            )
            semantic = plan.get("recheck_semantic")
            if semantic is None:
                semantic = {
                    row.get("row_id"): (row.get("checks") or {}).get(
                        "semantic", {"outcome": "not_run"})
                    for row in rows
                }
            artifacts.record_checks(
                audit["instance"], audit["data"], audit["root"],
                structural, semantic,
            )
            events = artifacts.refresh_defects(
                audit["instance"], audit["data"], audit["root"]
            )
            if any("repaired" in e for e in events):
                repaired.append(defect)
            if any("regressed" in e for e in events):
                regressions.append(defect)
        return {"repaired": repaired, "regressions": regressions,
                "real_repair": True, "considered": len(defects)}

    def verify(self, question, ctx, acted, result) -> None:
        if acted.get("real_repair"):
            for defect in acted.get("repaired", []) or []:
                result["findings"].append(
                    {"kind": "repair", "defect": defect, "state": "repaired"}
                )
            for defect in acted.get("regressions", []) or []:
                result["gaps"].append(f"regressed: {defect}")
            if not acted.get("repaired") and not acted.get("regressions"):
                result["findings"].append(
                    {"kind": "repair", "state": "not_applicable",
                     "basis": "no open defects in scope: no repair executed",
                     "defects_seen": acted.get("considered", 0)}
                )
        else:
            result["notes"].append("driver-stated repairs (not system-verified)")
            result["findings"].append(
                {"kind": "repair", "state": "driver-stated"}
            )

    def justify_more(self, question, ctx, result) -> bool:
        return bool(result["gaps"]) and result["passes"] < self.max_passes


class ReviewEngine(Engine):
    """Integrate contradictions and disclosures; recommend approval."""

    name = "review"
    contract = {
        "responsibility": "integrate findings, challenge, disclose",
        "inputs": ["all findings", "contradictions", "defects", "premises"],
        "outputs": ["integrated position", "disclosures", "approval recommendation"],
        "verification": "every contradiction/defect resolved or disclosed",
        "dependencies": ["research", "analysis", "verification", "repair"],
        "stopping": "integrated, cap",
    }
    max_passes = 2

    def act(self, question, ctx, problem, needs, result) -> dict:
        driver = ctx["drivers"]["review"]
        self._spend_tools(ctx, result, 1)
        challenge = driver.challenge(ctx.get("position", {}))
        peer = _peer_validator("adversarial")
        result["notes"].append(
            "peer adversarial review ran"
            if peer else "built-in challenge ran (peer module absent)"
        )
        return {"challenge": challenge, "peer": bool(peer)}

    def verify(self, question, ctx, acted, result) -> None:
        open_items = [
            c for c in ctx.get("contradictions", [])
            if c.get("status") == "open"
        ] + [
            d for d in ctx.get("defects", [])
            if d.get("status") in ("open", "unresolved")
        ]
        challenge = acted.get("challenge") or {}
        addressed = " ".join(
            str(v) for v in challenge.values()
        ) if isinstance(challenge, dict) else ""
        for item in open_items:
            key = item.get("id") or item.get("key") or str(item)[:40]
            if key and key not in addressed:
                result["unresolved"].append(f"undisclosed open item: {key}")
        result["findings"].append(
            {"kind": "integration",
             "contradictions_seen": len(ctx.get("contradictions", [])),
             "challenge_ran": True}
        )

    def justify_more(self, question, ctx, result) -> bool:
        return False

    def verified(self, question, ctx, result) -> bool:
        return not result["unresolved"]


def _peer_validator(kind: str):
    """Peer validators when importable, else None. Never raises."""
    try:
        if kind == "analysis":
            from agent_kit.reasoning import validate_element

            return validate_element
        if kind == "adversarial":
            from agent_kit.reasoning import validate_adversarial

            return validate_adversarial
    except ImportError:
        return None
    return None


ENGINE_CLASSES = {
    "research": ResearchEngine,
    "analysis": AnalysisEngine,
    "verification": VerificationEngine,
    "repair": RepairEngine,
    "review": ReviewEngine,
}

ENGINE_ORDER = ["research", "analysis", "verification", "repair", "review"]

REGISTRY: dict[str, type] = dict(ENGINE_CLASSES)


def register(name: str, engine_cls: type) -> None:
    """Register an engine class (peer adapters use this)."""
    if not isinstance(engine_cls, type) or not callable(
        getattr(engine_cls, "run", None)
    ):
        raise contracts.PlanError(
            f"cannot register {name!r}: not an engine class with run()"
        )
    REGISTRY[name] = engine_cls


def available() -> list[str]:
    """Registered engine names in execution order."""
    return [name for name in ENGINE_ORDER if name in REGISTRY] + sorted(
        name for name in REGISTRY if name not in ENGINE_ORDER
    )


def peer_modules_present() -> dict[str, bool]:
    """Which peer reasoning modules are importable (never raises).

    The live-driver seam: peer tools are model-invoked tools, so
    production drivers call them; the control plane only probes
    presence to label which verification ran.
    """
    present: dict[str, bool] = {}
    for name in ("answer_foundational", "analyze_element",
                 "adversarial_review"):
        try:
            __import__(f"agent_kit.tools.{name}")
            present[name] = True
        except ImportError:
            present[name] = False
    try:
        __import__("agent_kit.reasoning")
        present["reasoning"] = True
    except ImportError:
        present["reasoning"] = False
    return present
