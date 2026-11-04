"""Phase F stage 1: offline audit-eval mechanics runner.

Each fixture runs through the real audit path on a temporary
workspace — open, structural checks, scripted semantic judge,
record — and is graded per row against planted truth. Run with:

    cd agent && .venv/bin/python -m eval.artifact_run

Reports always state their client and what they do not measure.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_kit import artifacts  # noqa: E402
from agent_kit import tools  # noqa: E402
from eval import artifact_cases  # noqa: E402
from tests import support  # noqa: E402

REPORT_KEYS = frozenset(
    {
        "case",
        "client",
        "semantic_quality",
        "rows",
        "classes",
        "clean_false_flags",
        "complete",
    }
)


def _unsupported_for(case: dict):
    wanted = set(case.get("unsupported", []))

    def mark(text: str) -> list:
        return [
            {"unit": unit, "reason": "eval fixture: scripted unsupported"}
            for unit in sorted(wanted)
        ]

    return mark


def run_case(
    name: str,
    *,
    cases: dict | None = None,
    materialize=None,
    unsupported=None,
) -> dict:
    """Run one fixture through the real audit path; grade vs truth.

    Optional catalogue/materialize/judge hooks let reviewed
    examples replay through this same path; defaults preserve
    the artifact_cases behavior exactly.
    """
    catalogue = artifact_cases.CASES if cases is None else cases
    maker = artifact_cases.materialize if materialize is None else materialize
    case = catalogue[name]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        run_id, record, instance = maker(root, name)
        mark = unsupported or _unsupported_for(case)
        fake = support.scripted(root, [], unsupported=mark)
        ctx = tools.Context(fake.client, root, run_id, record, True)
        rows = instance["rows"]
        structural = artifacts.audit_structural(root, record.data, rows)
        semantic = artifacts.audit_support(ctx, rows, structural)
        artifacts.record_checks(
            instance, record.data, root, structural, semantic
        )
        record.artifact_save(instance, "artifact_checked", "eval")
        return _grade(name, case, instance)


def _grade(name: str, case: dict, instance: dict) -> dict:
    truth = case["truth"]
    rows = {}
    classes: dict[str, dict[str, int]] = {}
    clean_false_flags = 0
    complete = True
    for row in instance["rows"]:
        row_id = row["row_id"]
        expected = truth.get(row_id)
        if expected is None:
            complete = False
            continue
        checks = row.get("checks") or {}
        observed = {
            "structural": (checks.get("structural") or {}).get("outcome"),
            "semantic": (checks.get("semantic") or {}).get("outcome"),
        }
        match = (
            observed["structural"] == expected["structural"]
            and observed["semantic"] == expected["semantic"]
        )
        if not match:
            complete = False
        if expected["class"] == "clean" and not match:
            clean_false_flags += 1
        bucket = classes.setdefault(
            expected["class"], {"rows": 0, "matched": 0}
        )
        bucket["rows"] += 1
        bucket["matched"] += 1 if match else 0
        rows[row_id] = {
            "class": expected["class"],
            "expected": {
                "structural": expected["structural"],
                "semantic": expected["semantic"],
            },
            "observed": observed,
            "match": match,
        }
    if set(rows) != set(truth):
        complete = False
    return {
        "case": name,
        "client": "scripted",
        "semantic_quality": (
            "not measured: semantic verdicts are scripted; "
            "this arm proves routing and record plumbing only"
        ),
        "rows": rows,
        "classes": classes,
        "clean_false_flags": clean_false_flags,
        "complete": complete,
    }


def run_all() -> list:
    """Every fixture, in name order."""
    return [run_case(name) for name in sorted(artifact_cases.CASES)]


def summarize(reports: list) -> dict:
    """Per-class detection across cases plus clean false flags."""
    classes: dict[str, dict[str, int]] = {}
    for report in reports:
        for name, bucket in report["classes"].items():
            total = classes.setdefault(name, {"rows": 0, "matched": 0})
            total["rows"] += bucket["rows"]
            total["matched"] += bucket["matched"]
    return {
        "client": "scripted",
        "cases": len(reports),
        "cases_complete": sum(1 for r in reports if r["complete"]),
        "classes": classes,
        "clean_false_flags": sum(
            r["clean_false_flags"] for r in reports
        ),
    }


def main() -> None:
    with contextlib.redirect_stdout(io.StringIO()):
        reports = run_all()
    print(json.dumps({"reports": reports, "summary": summarize(reports)},
                     indent=2))


if __name__ == "__main__":
    main()
