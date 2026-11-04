"""The first-principles reasoning gate. Deterministic, offline.

Called by submit_memo and review_draft after the memo rules pass
(verification on), and by ./helper verify RUN --memo.

    run_verification(ctx, memo) -> {
        "passed": bool, "feedback": [problem, ...],
        "advisory": [...], "mode": "identified" | "reasoning" | "legacy",
        "gate": "reasoning-v1",
    }

The gate engages once the run does first-principles work (a task
type is set or a foundational answer is recorded); recording a
task identification engages the stricter identified mode, which
additionally requires classification, a workflow plan, triage of
unknowns, and a declared work product. Legacy runs with no
findings pass with an advisory note; findings filed without
task identity fail closed and name identification as the remedy.
In engaged modes every problem fails the filing;
advisory notes name what still needs human judgment. Structural
only: the gate checks that the reasoning exists, is complete, and
is linked — never that it is correct.
"""

from __future__ import annotations

from agent_kit.reasoning import GATE_VERSION, run_gate


def run_verification(ctx, memo: dict) -> dict:
    result = run_gate(ctx.case.data, memo if isinstance(memo, dict) else {})
    return {
        "passed": result["passed"],
        "feedback": list(result["problems"]),
        "advisory": list(result["advisory"]),
        "mode": result["mode"],
        "gate": GATE_VERSION,
    }
