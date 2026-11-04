"""Scanner recall benchmark runner (offline, deterministic).

Grades the FINAL STATE of scan_text over the probe set: every
MUST_CATCH variant must flag, every precision sentence must stay
silent, and STRETCH variants are reported (never gated) as named
documented limits. Run with:

    cd agent && .venv/bin/python -m eval.scan_run

Reports carry the benchmark schema (case, mode, client,
semantic_quality, complete, layer, notes); mode "stretch" marks
the informational variants the floors ignore.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_kit.reasoning import scan_text  # noqa: E402
from eval import scan_cases as cases  # noqa: E402

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
    "not measured: probes are fixed strings; the benchmark measures "
    "pattern recall and precision, not live-model susceptibility or "
    "novel attack generation."
)


def _report(variant: str, mode: str, flagged: bool, expect: bool) -> dict:
    return {
        "case": variant,
        "mode": mode,
        "client": "scripted",
        "semantic_quality": NOT_MEASURED,
        "complete": flagged == expect,
        "layer": "scanner",
        "notes": f"flagged={flagged}, expected={expect}",
    }


def run_all() -> list:
    """Every probe variant once, in listed order."""
    reports = [
        _report(variant, "scan", bool(scan_text(text)), True)
        for variant, text in cases.MUST_CATCH
    ]
    reports += [
        _report(variant, "scan", bool(scan_text(text)), False)
        for variant, text in (
            cases.PRECISION_EXTRA + cases.PRECISION_MULTILINE
        )
    ]
    reports += [
        _report(variant, "stretch", bool(scan_text(text)), True)
        for variant, text in cases.STRETCH
    ]
    return reports


def summarize(reports: list) -> dict:
    """Aggregates with denominators; stretch reported, never gated."""
    gated = [r for r in reports if r["mode"] == "scan"]
    must_ids = {variant for variant, _ in cases.MUST_CATCH}
    must = [r for r in gated if r["case"] in must_ids]
    precision = [r for r in gated if r["case"] not in must_ids]
    stretch = [r for r in reports if r["mode"] == "stretch"]
    summary = {
        "must_catch": {
            "hit": sum(1 for r in must if r["complete"]),
            "total": len(must),
        },
        "precision_fp": {
            "hit": sum(1 for r in precision if not r["complete"]),
            "total": len(precision),
        },
        "stretch": {
            "hit": sum(1 for r in stretch if r["complete"]),
            "total": len(stretch),
            "missed": sorted(r["case"] for r in stretch if not r["complete"]),
        },
    }
    missed = [r["case"] for r in gated if not r["complete"]]
    summary["verdict"] = (
        f"RECALL {summary['must_catch']['hit']}/"
        f"{summary['must_catch']['total']}, "
        f"FP {summary['precision_fp']['hit']}, "
        f"stretch {summary['stretch']['hit']}/"
        f"{summary['stretch']['total']}"
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
