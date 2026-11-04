"""Task-identification benchmark runner (offline, deterministic).

Each fixture runs through the real filing path on a temporary
workspace — identification gate, memo rules, scripted claims check —
twice: as a baseline run (reasoning mode without identification)
and as an identified run (the upgraded behavior). Reports grade the
FINAL STATE against planted truth, with operation counts kept as
diagnostics. Run with:

    cd agent && .venv/bin/python -m eval.identification_run

Metrics (denominators in every report):
  sound_filed           sound fixtures filed with every check passing
  defects_caught        defective fixtures refused at any check
  deterministic_catches defective fixtures refused pre-judge (gate/rules)
  gate_correct          fixtures where the gate verdict matches truth
  identification_cover  fixtures with a complete identification chain
                        (identification, classification, workflow,
                        work product all present and valid)
  triage_complete       gaps needing triage (unresolved questions and
                        unknown matter fields) that carry a decision
  dependency_valid      fixtures whose question dependencies are valid
                        (known refs, acyclic)
  workflow_selected     fixtures with a recorded workflow plan
  legal_accuracy        scripted claims verdicts matching planted verdicts

All but legal_accuracy are deterministic. legal_accuracy uses the
scripted judge (routing/plumbing only). Whether the recorded
identification is CORRECT — right categories, right dependencies,
right triage — needs human review or a calibrated judge, and the
reports say so.
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
from eval import identification_cases  # noqa: E402
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
        "chain",
        "metrics",
        "complete",
    }
)

NOT_MEASURED = (
    "not measured: semantic verdicts are scripted; the semantic arm "
    "proves routing and record plumbing only. Whether the recorded "
    "identification is CORRECT needs human review or a calibrated judge."
)


def _unsupported_for(case: dict):
    wanted = set(case.get("unsupported", []))

    def mark(text: str) -> list:
        return [
            {"unit": unit, "reason": "eval fixture: scripted unsupported"}
            for unit in sorted(wanted)
        ]

    return mark


def run_fixture(name: str, *, with_identification: bool) -> dict:
    """Run one fixture in one mode; grade final state vs truth."""
    case = identification_cases.CASES[name]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        run_id, record = identification_cases.materialize(
            root, name, with_identification=with_identification
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
        return _grade(
            name, case, with_identification, record.data, gate, rules, claims
        )


def _grade(name, case, with_identification, data, gate, rules, claims) -> dict:
    truth = case["truth"]
    mode = "identified" if with_identification else "baseline"
    expected_gate = (
        truth["identified_gate"] if with_identification else truth["baseline_gate"]
    )
    gate_ok = bool(gate["passed"]) == bool(expected_gate)
    gate_problems_ok = all(
        any(want in got for got in gate["feedback"])
        for want in (truth.get("identified_problems", []) if with_identification else [])
    )
    rules_ok = bool(rules["passed"]) == bool(truth["rules"])
    claims_ok = claims.get("outcome") == truth["claims"]
    chain = _chain_state(data)
    filed = (
        bool(gate["passed"])
        and bool(rules["passed"])
        and claims.get("outcome") == "passed"
    )
    if case["sound"]:
        complete = gate_ok and gate_problems_ok and rules_ok and claims_ok and filed
    else:
        complete = gate_ok and gate_problems_ok and rules_ok and not filed
    if not gate["passed"]:
        caught_at = "gate"
    elif not rules["passed"]:
        caught_at = "rules"
    elif claims.get("outcome") != "passed":
        caught_at = "claims"
    else:
        caught_at = "filed"
    ops = (
        len(data.get("facts") or [])
        + len(data.get("foundational") or [])
        + len(data.get("elements") or [])
        + len(data.get("adversarial") or [])
        + len(data.get("triages") or [])
        + (1 if data.get("identification") else 0)
        + (1 if (data.get("workflow") or {}).get("components") else 0)
        + (1 if data.get("work_product") else 0)
        + 1
    )
    return {
        "case": name,
        "mode": mode,
        "client": "scripted",
        "semantic_quality": NOT_MEASURED,
        "gate": {
            "expected": expected_gate,
            "observed": bool(gate["passed"]),
            "match": gate_ok and gate_problems_ok,
            "problems": [str(p) for p in gate["feedback"]],
        },
        "rules": {
            "expected": truth["rules"],
            "observed": bool(rules["passed"]),
            "match": rules_ok,
        },
        "claims": {
            "expected": truth["claims"],
            "observed": claims.get("outcome"),
            "match": claims_ok,
        },
        "chain": chain,
        "metrics": {
            "filed": filed,
            "caught_at": caught_at,
            "ops": ops,
        },
        "complete": complete,
    }


def _chain_state(data: dict) -> dict:
    identification = data.get("identification") or {}
    classification = data.get("classification") or {}
    workflow = (data.get("workflow") or {}).get("components") or []
    product = data.get("work_product") or {}
    answers = [a for a in (data.get("foundational") or []) if isinstance(a, dict)]
    refs = {
        str(t.get("ref", ""))
        for t in (data.get("triages") or [])
        if isinstance(t, dict)
    }
    needing = [
        a.get("id")
        for a in answers
        if a.get("status") == "unresolved"
    ] + [
        f"matter:{name}"
        for name, field in ((data.get("matter", {}) or {}).get("fields", {}) or {}).items()
        if isinstance(field, dict) and field.get("status") == "unknown"
    ]
    return {
        "identified": bool(identification),
        "classified": bool(classification.get("categories")),
        "workflow": bool(workflow),
        "product": bool(product),
        "chain_complete": bool(
            identification
            and classification.get("categories")
            and workflow
            and product
        ),
        "triage_needing": len(needing),
        "triage_decided": sum(1 for ref in needing if ref in refs),
        "dependencies_valid": not reasoning.validate_answer_edges(answers),
    }


def run_all() -> list:
    """Every fixture in both modes (baseline first, then identified)."""
    return [
        run_fixture(name, with_identification=with_identification)
        for name in sorted(identification_cases.CASES)
        for with_identification in (False, True)
    ]


def summarize(reports: list) -> dict:
    """Per-mode aggregates with denominators (see module docstring)."""
    summary = {}
    for mode in ("baseline", "identified"):
        rows = [r for r in reports if r["mode"] == mode]
        sound = [r for r in rows if identification_cases.CASES[r["case"]]["sound"]]
        defective = [
            r for r in rows if not identification_cases.CASES[r["case"]]["sound"]
        ]
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
            "deterministic_catches": {
                "hit": sum(
                    1
                    for r in defective
                    if r["metrics"]["caught_at"] in ("gate", "rules")
                ),
                "total": len(defective),
            },
            "gate_correct": {
                "hit": sum(1 for r in rows if r["gate"]["match"]),
                "total": len(rows),
            },
            "identification_cover": {
                "hit": sum(1 for r in rows if r["chain"]["chain_complete"]),
                "total": len(rows),
            },
            "triage_complete": {
                "hit": sum(r["chain"]["triage_decided"] for r in rows),
                "total": sum(r["chain"]["triage_needing"] for r in rows),
            },
            "dependency_valid": {
                "hit": sum(1 for r in rows if r["chain"]["dependencies_valid"]),
                "total": len(rows),
            },
            "workflow_selected": {
                "hit": sum(1 for r in rows if r["chain"]["workflow"]),
                "total": len(rows),
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
    base, up = summary["baseline"], summary["identified"]
    summary["comparison"] = {
        "defects_caught_delta": (
            up["defects_caught"]["hit"] - base["defects_caught"]["hit"]
        ),
        "deterministic_delta": (
            up["deterministic_catches"]["hit"]
            - base["deterministic_catches"]["hit"]
        ),
        "sound_filed_delta": (
            up["sound_filed"]["hit"] - base["sound_filed"]["hit"]
        ),
        "ops_delta": round(up["mean_ops"] - base["mean_ops"], 1),
        "verdict": _verdict(base, up),
    }
    return summary


def _verdict(base: dict, up: dict) -> str:
    if up["sound_filed"]["hit"] < up["sound_filed"]["total"]:
        return "REGRESSION: sound fixtures refused"
    if up["defects_caught"]["hit"] <= base["defects_caught"]["hit"]:
        return "NO GAIN: no additional defects caught"
    if base["sound_filed"]["hit"] < base["sound_filed"]["total"]:
        return "MIXED: baseline itself refuses sound fixtures; fixtures need review"
    net = up["defects_caught"]["hit"] - base["defects_caught"]["hit"]
    det = (
        up["deterministic_catches"]["hit"] - base["deterministic_catches"]["hit"]
    )
    return (
        f"IMPROVED: +{net} defects caught net, +{det} pre-judge, "
        "sound filings preserved"
    )


def main() -> None:
    with contextlib.redirect_stdout(io.StringIO()):
        reports = run_all()
    print(json.dumps(
        {"reports": reports, "summary": summarize(reports)}, indent=2
    ))


if __name__ == "__main__":
    main()
