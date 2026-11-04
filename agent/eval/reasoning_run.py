"""First-principles reasoning benchmark runner (offline, deterministic).

Each fixture runs through the real filing path on a temporary
workspace — reasoning gate, memo rules, scripted claims check —
twice: once as a legacy run (no reasoning artifacts: fails
closed demanding identification) and once as a reasoning run
(the upgraded behavior, which must discriminate sound from
defective).
Reports grade the FINAL STATE (gate/rules/claims outcomes, artifact
presence, disclosure) against planted truth, with operation counts
kept as diagnostics. Run with:

    cd agent && .venv/bin/python -m eval.reasoning_run

Metrics (denominators in every report):
  sound_filed           sound fixtures filed with every check passing
  defects_caught        defective fixtures refused at any check
  deterministic_catches defective fixtures refused pre-judge (gate/rules)
  gate_correct          fixtures where the gate verdict matches truth
  reasoning_coverage    required question classes answered / required
  adversarial_rate      fixtures with an adversarial review recorded
  uncertainty_explicit  fixtures with planted unknowns that disclose them
                        (nonempty gaps + every unresolved answer resolved-by-path)
  factual_discipline    filed memos whose inferences all carry a basis
  citation_accuracy     filed memos with zero memo-rule citation problems
  legal_accuracy        scripted claims verdicts matching planted verdicts

reasoning_coverage, adversarial_rate, uncertainty_explicit,
factual_discipline, and citation_accuracy are deterministic: they
measure real artifacts and real rules. legal_accuracy uses the
scripted judge, so it proves routing/plumbing only — never
judgment quality. Reports always state their client and what they
do not measure. Semantic quality (is the reasoning CORRECT, not
merely present) is not measured here; it needs human review or a
calibrated judge, and the reports say so.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_kit import reasoning, tools  # noqa: E402
from eval import reasoning_cases  # noqa: E402
from tests import support  # noqa: E402
from verification import verify_reasoning  # noqa: E402
from verification.verify_memo import run_verification as memo_rules  # noqa: E402
from verification.verify_claims import run_verification as claims_check  # noqa: E402

REPORT_KEYS = frozenset(
    {
        "case",
        "mode",
        "client",
        "semantic_quality",
        "gate",
        "rules",
        "claims",
        "artifacts",
        "metrics",
        "complete",
    }
)

NOT_MEASURED = (
    "not measured: semantic verdicts are scripted; the semantic arm "
    "proves routing and record plumbing only. Whether the recorded "
    "reasoning is CORRECT needs human review or a calibrated judge."
)


def _unsupported_for(case: dict):
    wanted = set(case.get("unsupported", []))

    def mark(text: str) -> list:
        return [
            {"unit": unit, "reason": "eval fixture: scripted unsupported"}
            for unit in sorted(wanted)
        ]

    return mark


def run_fixture(name: str, *, with_reasoning: bool) -> dict:
    """Run one fixture in one mode; grade final state vs truth."""
    case = reasoning_cases.CASES[name]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        run_id, record = reasoning_cases.materialize(
            root, name, with_reasoning=with_reasoning
        )
        fake = support.scripted(root, [], unsupported=_unsupported_for(case))
        ctx = tools.Context(fake.client, root, run_id, record, True)
        memo = dict(case["memo"])
        gate = verify_reasoning.run_verification(ctx, memo)

        class _MemoCtx:
            def __init__(self, case):
                self.case = case

        rules = memo_rules(_MemoCtx(record), memo)
        claims = claims_check(ctx, memo)
        return _grade(name, case, with_reasoning, record.data, gate, rules, claims)


def _grade(name, case, with_reasoning, data, gate, rules, claims) -> dict:
    truth = case["truth"]
    mode = "upgraded" if with_reasoning else "baseline"
    gate_ok = bool(gate["passed"]) == bool(truth["gate"])
    gate_problems_ok = all(
        any(want in got for got in gate["feedback"])
        for want in truth.get("gate_problems", [])
    )
    rules_ok = bool(rules["passed"]) == bool(truth["rules"])
    claims_ok = claims.get("outcome") == truth["claims"]
    answers = [a for a in (data.get("foundational") or []) if isinstance(a, dict)]
    required = reasoning.REQUIRED_CLASSES[case["task_type"]]
    covered = {a.get("class") for a in answers}
    coverage = {c: (c in covered) for c in required}
    reviews = data.get("adversarial") or []
    elements = data.get("elements") or []
    gaps = case["memo"].get("gaps", "")
    gaps_ok = not case["uncertainty"] or bool(str(gaps or "").strip())
    resolutions_ok = all(
        a.get("status") != "unresolved" or str(a.get("resolution", "")).strip()
        for a in answers
    )
    inferences = [
        f for f in (data.get("facts") or [])
        if isinstance(f, dict) and f.get("kind") == "inference"
    ]
    inferences_ok = all(
        str(f.get("basis", "") or "").strip() for f in inferences
    )
    filed = bool(gate["passed"]) and bool(rules["passed"]) and claims.get("outcome") == "passed"
    if not gate["passed"]:
        caught_at = "gate"
    elif not rules["passed"]:
        caught_at = "rules"
    elif claims.get("outcome") != "passed":
        caught_at = "claims"
    else:
        caught_at = "filed"
    if case["sound"]:
        complete = gate_ok and gate_problems_ok and rules_ok and claims_ok and filed
    else:
        complete = gate_ok and gate_problems_ok and rules_ok and not filed
    ops = (
        len(data.get("facts") or [])
        + len(answers)
        + len(elements)
        + len(reviews)
        + 1
    )
    return {
        "case": name,
        "mode": mode,
        "client": "scripted",
        "semantic_quality": NOT_MEASURED,
        "gate": {
            "expected": truth["gate"],
            "observed": bool(gate["passed"]),
            "match": gate_ok and gate_problems_ok,
            "problems": [str(p) for p in gate["feedback"]],
        },
        "rules": {
            "expected": truth["rules"],
            "observed": bool(rules["passed"]),
            "match": rules_ok,
            "problems": [str(p) for p in rules.get("feedback") or []],
        },
        "claims": {
            "expected": truth["claims"],
            "observed": claims.get("outcome"),
            "match": claims_ok,
        },
        "artifacts": {
            "task_type": data.get("task_type", ""),
            "answers": len(answers),
            "elements": len(elements),
            "reviews": len(reviews),
            "coverage": coverage,
        },
        "metrics": {
            "filed": filed,
            "caught_at": caught_at,
            "gaps_disclosed": gaps_ok,
            "resolutions_ok": resolutions_ok,
            "inferences_ok": inferences_ok,
            "citation_problems": sum(
                1
                for p in (rules.get("feedback") or [])
                if "cit" in str(p).lower()
            ),
            "ops": ops,
        },
        "complete": complete,
    }


def run_all() -> list:
    """Every fixture in both modes (baseline first, then upgraded)."""
    return [
        run_fixture(name, with_reasoning=with_reasoning)
        for name in sorted(reasoning_cases.CASES)
        for with_reasoning in (False, True)
    ]


def summarize(reports: list) -> dict:
    """Per-mode aggregates with denominators (see module docstring)."""
    summary = {}
    for mode in ("baseline", "upgraded"):
        rows = [r for r in reports if r["mode"] == mode]
        sound = [r for r in rows if reasoning_cases.CASES[r["case"]]["sound"]]
        defective = [r for r in rows if not reasoning_cases.CASES[r["case"]]["sound"]]
        uncertain = [
            r for r in rows
            if reasoning_cases.CASES[r["case"]]["uncertainty"]
        ]
        coverage_hit = coverage_total = 0
        for row in rows:
            for covered in row["artifacts"]["coverage"].values():
                coverage_total += 1
                coverage_hit += 1 if covered else 0
        filed = [r for r in rows if r["metrics"]["filed"]]
        summary[mode] = {
            "cases": len(rows),
            "sound_filed": {
                "hit": sum(1 for r in sound if r["metrics"]["filed"]),
                "total": len(sound),
            },
            "defects_caught": {
                "hit": sum(1 for r in defective if not r["metrics"]["filed"]),
                "total": len(defective),
            },
            "gate_correct": {
                "hit": sum(1 for r in rows if r["gate"]["match"]),
                "total": len(rows),
            },
            "reasoning_coverage": {"hit": coverage_hit, "total": coverage_total},
            "adversarial_rate": {
                "hit": sum(1 for r in rows if r["artifacts"]["reviews"] > 0),
                "total": len(rows),
            },
            "uncertainty_explicit": {
                "hit": sum(
                    1
                    for r in uncertain
                    if r["metrics"]["gaps_disclosed"]
                    and r["metrics"]["resolutions_ok"]
                ),
                "total": len(uncertain),
            },
            "factual_discipline": {
                "hit": sum(1 for r in filed if r["metrics"]["inferences_ok"]),
                "total": len(filed),
            },
            "citation_accuracy": {
                "hit": sum(
                    1 for r in filed if r["metrics"]["citation_problems"] == 0
                ),
                "total": len(filed),
            },
            "deterministic_catches": {
                "hit": sum(
                    1
                    for r in defective
                    if r["metrics"]["caught_at"] in ("gate", "rules")
                ),
                "total": len(defective),
            },
            "legal_accuracy": {
                "hit": sum(1 for r in rows if r["claims"]["match"]),
                "total": len(rows),
                "note": "scripted judge: plumbing only",
            },
            "mean_ops": (
                sum(r["metrics"]["ops"] for r in rows) / len(rows) if rows else 0
            ),
        }
    base, up = summary["baseline"], summary["upgraded"]
    summary["comparison"] = {
        "defects_caught_delta": (
            up["defects_caught"]["hit"] - base["defects_caught"]["hit"]
        ),
        "sound_filed_delta": (
            up["sound_filed"]["hit"] - base["sound_filed"]["hit"]
        ),
        "coverage_delta": (
            up["reasoning_coverage"]["hit"] - base["reasoning_coverage"]["hit"]
        ),
        "deterministic_delta": (
            up["deterministic_catches"]["hit"]
            - base["deterministic_catches"]["hit"]
        ),
        "ops_delta": round(up["mean_ops"] - base["mean_ops"], 1),
        "verdict": _verdict(base, up),
    }
    return summary


def _verdict(base: dict, up: dict) -> str:
    # Closeout §7.1: the baseline fails closed on every fixture
    # (no identity, no filing), so the comparison is about
    # discrimination, not net catches: the upgraded path must
    # file all sound fixtures and catch all defective ones.
    if up["sound_filed"]["hit"] < up["sound_filed"]["total"]:
        return "REGRESSION: sound fixtures refused"
    if up["defects_caught"]["hit"] < up["defects_caught"]["total"]:
        return "REGRESSION: defective fixtures filed"
    if base["sound_filed"]["hit"] > 0:
        return "MIXED: baseline filed fixtures it should refuse; gate needs review"
    net = up["sound_filed"]["hit"] - base["sound_filed"]["hit"]
    return (
        f"IMPROVED: +{net} sound filed over the fail-closed baseline, "
        "all defective caught"
    )


def main() -> None:
    with contextlib.redirect_stdout(io.StringIO()):
        reports = run_all()
    print(json.dumps(
        {"reports": reports, "summary": summarize(reports)}, indent=2
    ))


if __name__ == "__main__":
    main()
