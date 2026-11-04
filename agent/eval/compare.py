"""Paired QC on/off comparison (mechanics only, never quality evidence).

The same case runs twice on scripted clients under identical
verification, evidence, budget, and model configuration — once
with the quality review, once with it skipped (the claims check
still gates both arms). Reports pair final states with deltas and
diagnostics (turns, tokens, calls, failures, latency, wall time).

A scripted judge returns what it was told to return, so arm
differences here prove the harness and the plumbing, not that
review/repair helps. Run with:

    cd agent && .venv/bin/python -m eval.compare
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval import cases  # noqa: E402
from eval import pinned  # noqa: E402
from eval import run  # noqa: E402

COMPARISON_KEYS = frozenset(
    {
        "case",
        "verification",
        "client",
        "semantic_quality",
        "cost_usd",
        "arms",
        "delta",
    }
)


def compare_case(name: str, *, verification: bool = True, turns: int = 30) -> dict:
    """Run one case in both arms; return the paired comparison report."""
    arms = {}
    for qc in (True, False):
        start = time.perf_counter()
        report = run.run_case(
            name, verification=verification, turns=turns, qc=qc, include_qc=True
        )
        report["diagnostics"]["elapsed_s"] = round(
            time.perf_counter() - start, 2
        )
        arms["qc_on" if qc else "qc_off"] = report
    return {
        "case": name,
        "verification": verification,
        "client": "scripted",
        "semantic_quality": "not measured — scripted judges return scripted verdicts",
        "cost_usd": "unknown (no pricing version)",
        "arms": arms,
        "delta": _delta(arms["qc_on"], arms["qc_off"]),
    }


def _delta(on: dict, off: dict) -> dict:
    """On-minus-off differences: sameness flags, structural diff paths, numeric deltas."""
    numeric = {}
    for key in (
        "refusals",
        "escalations",
        "turns",
        "agent_tokens",
        "tool_tokens",
        "tool_calls",
        "failures",
    ):
        left = (
            on["diagnostics"].get(key, on.get(key, 0))
            if key not in ("refusals", "escalations")
            else on.get(key, 0)
        )
        right = (
            off["diagnostics"].get(key, off.get(key, 0))
            if key not in ("refusals", "escalations")
            else off.get(key, 0)
        )
        numeric[key] = left - right
    numeric["latency_ms"] = round(
        on["diagnostics"].get("latency_ms", 0.0)
        - off["diagnostics"].get("latency_ms", 0.0),
        1,
    )
    structural = pinned.diff_reports(on, off)
    return {
        "same_status": on["status"] == off["status"],
        "same_coverage": on["coverage"] == off["coverage"],
        "same_memo": on["memo"] == off["memo"],
        "same_claims": on["claims"] == off["claims"],
        "qc_reviews_on": on["qc"]["reviews"],
        "qc_reviews_off": off["qc"]["reviews"],
        "diffs": structural["diffs"],
        "truncated": structural["truncated"],
        **numeric,
    }


def compare_all() -> list:
    """Every case, both arms, verification on."""
    return [compare_case(name) for name in sorted(cases.CASES)]


def main() -> None:
    with contextlib.redirect_stdout(io.StringIO()):
        reports = compare_all()
    print(json.dumps(reports, indent=2))


if __name__ == "__main__":
    main()
