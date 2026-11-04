"""Citation benchmark runner (offline, deterministic).

Grades agent_kit.citations over the fixture passages: every
EXTRACT case must yield exactly its expected forms, every
SILENCE case must yield nothing, and STRETCH state-code forms
are reported (never gated) so reporters-db drift shows instead
of flipping the gate. Run with:

    cd agent && .venv/bin/python -m eval.citation_run

Reports carry the benchmark schema (case, mode, client,
semantic_quality, complete, layer, notes). This runner is
standalone: it is not part of the regression gate (which pins
six benchmarks); tests/test_citations.py asserts its floors.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_kit.citations import extract  # noqa: E402
from eval import citation_cases as cases  # noqa: E402

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
    "not measured: fixed passages; the benchmark measures "
    "citation parsing and short-form resolution, not whether a "
    "parsed citation exists (gap G3) or states good law."
)

_FIELDS = (
    "kind", "text", "reporter", "volume", "page", "year",
    "pin_cite", "antecedent",
)


def _report(variant: str, mode: str, complete: bool, notes: str) -> dict:
    return {
        "case": variant,
        "mode": mode,
        "client": "scripted",
        "semantic_quality": NOT_MEASURED,
        "complete": complete,
        "layer": "citation",
        "notes": notes,
    }


def _forms(text: str) -> list:
    return [
        tuple(form[field] for field in _FIELDS) for form in extract(text)
    ]


def run_all() -> list:
    """Every fixture once, in listed order."""
    reports = []
    for variant, text, expected in cases.EXTRACT:
        actual = _forms(text)
        reports.append(_report(
            variant, "citation", actual == [tuple(e) for e in expected],
            f"actual={actual}",
        ))
    for variant, text in cases.SILENCE:
        actual = _forms(text)
        reports.append(_report(
            variant, "citation", actual == [], f"actual={actual}"
        ))
    for variant, text, expected in cases.STRETCH:
        actual = _forms(text)
        reports.append(_report(
            variant, "stretch", actual == [tuple(e) for e in expected],
            f"actual={actual}",
        ))
    return reports


def summarize(reports: list) -> dict:
    """Aggregates with denominators; stretch reported, never gated."""
    gated = [r for r in reports if r["mode"] == "citation"]
    extract_ids = {variant for variant, _, _ in cases.EXTRACT}
    extract = [r for r in gated if r["case"] in extract_ids]
    silence = [r for r in gated if r["case"] not in extract_ids]
    stretch = [r for r in reports if r["mode"] == "stretch"]
    summary = {
        "extract": {
            "hit": sum(1 for r in extract if r["complete"]),
            "total": len(extract),
        },
        "silence": {
            "hit": sum(1 for r in silence if r["complete"]),
            "total": len(silence),
        },
        "stretch": {
            "hit": sum(1 for r in stretch if r["complete"]),
            "total": len(stretch),
            "missed": sorted(r["case"] for r in stretch if not r["complete"]),
        },
    }
    missed = [r["case"] for r in gated if not r["complete"]]
    summary["verdict"] = (
        f"EXTRACT {summary['extract']['hit']}/"
        f"{summary['extract']['total']}, "
        f"SILENCE {summary['silence']['hit']}/"
        f"{summary['silence']['total']}, "
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
