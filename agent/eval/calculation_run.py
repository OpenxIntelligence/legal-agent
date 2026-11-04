"""Calculation benchmark runner (offline, deterministic).

Grades compute.replay over the known-answer fixtures: every MUST
case must return its hand-verified answer, every MUST_REFUSE case
must raise ComputeError instead of guessing. Run with:

    cd agent && .venv/bin/python -m eval.calculation_run

Reports carry the benchmark schema (case, mode, client,
semantic_quality, complete, layer, notes). This runner is
standalone: it is not part of the regression gate (which pins
six benchmarks); tests/test_calculate.py asserts its floors.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_kit.compute import ComputeError, replay  # noqa: E402
from eval import calculation_cases as cases  # noqa: E402

REPORT_KEYS = frozenset(
    {
        "case",
        "mode",
        "client",
        "semantic_quality",
        "complete",
        "layer",
        "notes",
    }
)

NOT_MEASURED = (
    "not measured: fixed fixtures over pure functions; the "
    "benchmark measures arithmetic conformance to documented "
    "conventions, not jurisdiction-specific deadline rules."
)


def _report(variant: str, complete: bool, notes: str) -> dict:
    return {
        "case": variant,
        "mode": "calculation",
        "client": "scripted",
        "semantic_quality": NOT_MEASURED,
        "complete": complete,
        "layer": "calculation",
        "notes": notes,
    }


def run_all() -> list:
    """Every fixture once, in listed order."""
    reports = []
    for variant, op, inputs, expected in cases.MUST:
        try:
            actual = replay(op, dict(inputs))
        except ComputeError as error:
            reports.append(_report(variant, False, f"raised: {error}"))
            continue
        reports.append(_report(
            variant, actual == expected,
            f"actual={actual!r}, expected={expected!r}",
        ))
    for variant, op, inputs in cases.MUST_REFUSE:
        try:
            actual = replay(op, dict(inputs))
        except ComputeError as error:
            reports.append(_report(variant, True, f"refused: {error}"))
            continue
        reports.append(_report(
            variant, False, f"answered {actual!r} instead of refusing"
        ))
    return reports


def summarize(reports: list) -> dict:
    """Aggregates with denominators."""
    must_ids = {variant for variant, _, _, _ in cases.MUST}
    must = [r for r in reports if r["case"] in must_ids]
    refused = [r for r in reports if r["case"] not in must_ids]
    summary = {
        "answers": {
            "hit": sum(1 for r in must if r["complete"]),
            "total": len(must),
        },
        "refusals": {
            "hit": sum(1 for r in refused if r["complete"]),
            "total": len(refused),
        },
    }
    missed = [r["case"] for r in reports if not r["complete"]]
    summary["verdict"] = (
        f"ANSWERS {summary['answers']['hit']}/"
        f"{summary['answers']['total']}, "
        f"REFUSALS {summary['refusals']['hit']}/"
        f"{summary['refusals']['total']}"
        + (f", MISSED: {missed}" if missed else "")
    )
    return summary


def main() -> None:
    with contextlib.redirect_stdout(io.StringIO()):
        reports = run_all()
    print(json.dumps(
        {"reports": reports, "summary": summarize(reports)}, indent=2
    ))


if __name__ == "__main__":
    main()
