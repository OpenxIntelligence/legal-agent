"""Defect-driven repair loop over the frozen quality benchmark.

Runs each audit fixture through check, defect synthesis, declared
revision fixes, recheck, qualified review, export — and grades the
DEFECT ledger (not just rows): fixable defects must reach repaired,
unfixable ones must stay visible and end accepted under explicit
qualifications, clean rows must gain no defects, and no defect may
regress silently. Run with:

    cd agent && .venv/bin/python -m eval.quality_remediate

The fix policy is declared below. Only revision-fixable defects
are revised (exact corrected citations, narrowed conclusion);
inference, contradiction, counterevidence, coverage, and gaps
defects need evidence, memo amendment, or human judgment, so the
loop must leave them open and qualify them — never massage them
into passing. Scripted recheck verdicts prove loop mechanics,
not judgment or human effort. The matrix case is audit-external
and stays in quality_run.py.
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
from eval import quality_cases  # noqa: E402
from tests import support  # noqa: E402

REPORT_KEYS = frozenset(
    {
        "case",
        "client",
        "semantic_quality",
        "repaired",
        "accepted",
        "still_open",
        "regressions",
        "review_bound",
        "export_ok",
        "complete",
        "diagnostics",
    }
)

# Declared reviewer policy: exact revisions per case. Absent
# defects cannot be fixed by revision and must stay visible.
FIXES = {
    "quality-quotes": {
        # Supply the exact page words.
        "R2": {"citation": {"quote": "Two invoices are overdue."}},
        # Quote lives on page 1, not page 2.
        "R3": {"citation": {"page": 1}},
    },
    "quality-counterevidence": {},
    "quality-coverage": {
        # Narrow to the audited findings.
        "RC": {"assertion": "The delivery and payment audits are complete."},
    },
}

# Defect keys revision is expected to repair (category:target).
REPAIRABLE = {
    "quality-quotes": {"citation_failed:R2", "citation_failed:R3"},
    "quality-counterevidence": set(),
    "quality-coverage": {"unsupported_conclusion:RC"},
}

AUDIT_CASES = sorted(
    name
    for name in quality_cases.CASES
    if quality_cases.CASES[name].get("kind") != "matrix"
)


def _scripted(root: Path, units: set[str]):
    def mark(text: str) -> list:
        return [
            {"unit": unit, "reason": "repair eval: scripted unsupported"}
            for unit in sorted(units)
        ]

    return support.scripted(root, [], unsupported=mark)


def _check(record, root, instance, unsupported: set[str]) -> int:
    fake = _scripted(root, unsupported)
    ctx = tools.Context(
        fake.client, root, record.data["run_id"], record, True
    )
    rows = instance["rows"]
    structural = artifacts.audit_structural(root, record.data, rows)
    semantic = artifacts.audit_support(ctx, rows, structural)
    artifacts.record_checks(instance, record.data, root, structural, semantic)
    artifacts.refresh_defects(instance, record.data, root)
    record.artifact_save(instance, "artifact_checked", "repair eval")
    return len(fake.calls)


def _apply_fixes(case_name, root, record, instance) -> int:
    revisions = 0
    for row_id, fix in FIXES.get(case_name, {}).items():
        artifacts.revise_row(
            instance,
            record.data,
            root,
            row_id,
            assertion=fix.get("assertion"),
            citation=fix.get("citation"),
        )
        revisions += 1
    artifacts.refresh_defects(instance, record.data, root)
    record.artifact_save(instance, "artifact_revised", "repair eval")
    return revisions


def run_case(name: str) -> dict:
    """Run one fixture through the defect-driven loop; grade it."""
    case = quality_cases.CASES[name]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        run_id, record, instance = quality_cases.materialize(root, name)
        del run_id
        judge_calls = _check(
            record, root, instance, set(case.get("unsupported", []))
        )
        opened = [
            (d["category"], d["target"])
            for d in instance.get("defects") or []
        ]
        revisions = _apply_fixes(name, root, record, instance)
        # Recheck: quotes/coverage fixes are scripted supported;
        # counterevidence keeps its unsupported script so unfixable
        # rows must still fail rather than get massaged.
        recheck_units = (
            set(case.get("unsupported", []))
            if name == "quality-counterevidence"
            else set()
        )
        judge_calls += _check(record, root, instance, recheck_units)
        snapshot = artifacts.current_snapshot(instance, record.data)
        still_open = sorted(
            f"{d['category']} on {d['target']}"
            for d in instance.get("defects") or []
            if d.get("status") == "open"
        )
        qualifications = [
            f"{label} unresolved by revision: needs evidence, "
            "memo amendment, or reviewer judgment"
            for label in still_open
        ]
        artifacts.review_instance(
            instance, record.data, "approved",
            "repair eval review", qualifications, snapshot,
        )
        record.artifact_save(instance, "artifact_reviewed", "repair eval")
        review_bound = (
            instance["review"].get("snapshot")
            == artifacts.current_snapshot(instance, record.data)
        )
        report = artifacts.export_instance(
            instance, record.data, root, None
        )
        return _grade(name, instance, report, review_bound, revisions,
                      judge_calls, opened)


def _grade(name, instance, report, review_bound, revisions, judge_calls,
           opened):
    defects = {
        f"{d['category']}:{d['target']}": d
        for d in instance.get("defects") or []
    }
    expected_open = {
        f"{d['category']}:{d['target']}"
        for d in quality_cases.CASES[name].get("defects", [])
    }
    repaired = []
    accepted = []
    still_open = []
    regressions = []
    complete = bool(review_bound)
    for key in sorted(expected_open):
        defect = defects.get(key)
        if defect is None:
            complete = False
            still_open.append({"defect": key, "ok": False})
            continue
        status = defect.get("status")
        regressed = any(
            h.get("change") == "regressed"
            for h in defect.get("history") or []
        )
        if regressed:
            regressions.append(key)
        if key in REPAIRABLE.get(name, set()):
            ok = status == "repaired" and not regressed
            repaired.append({"defect": key, "ok": ok})
            complete = complete and ok
        else:
            ok = status == "accepted" and not regressed
            accepted.append({"defect": key, "ok": ok})
            complete = complete and ok
    for key in sorted(set(defects) - expected_open):
        # Truth-unplanted defects are tolerated only when the loop
        # legitimately opened and repaired them mid-repair (e.g. a
        # conclusion stale between a finding revision and its
        # recheck). Anything else open is a false positive.
        defect = defects[key]
        regressed = any(
            h.get("change") == "regressed"
            for h in defect.get("history") or []
        )
        if regressed:
            regressions.append(key)
        if defect.get("status") == "repaired" and not regressed:
            repaired.append({"defect": key, "ok": True})
        else:
            complete = False
            still_open.append({"defect": key, "ok": False})
    export_ok = complete and all(
        any(
            d.get("category") == key.split(":")[0]
            and d.get("target") == key.split(":")[1]
            and d.get("status") in ("repaired", "accepted")
            for d in report.get("defects") or []
        )
        for key in expected_open
    )
    complete = complete and export_ok
    return {
        "case": name,
        "client": "scripted",
        "semantic_quality": (
            "not measured: recheck verdicts are scripted; this "
            "proves defect-loop mechanics (open, repair, accept, "
            "export), not judgment or human effort"
        ),
        "repaired": repaired,
        "accepted": accepted,
        "still_open": still_open,
        "regressions": regressions,
        "review_bound": review_bound,
        "export_ok": export_ok,
        "complete": complete,
        "diagnostics": {
            "revisions": revisions,
            "rechecks": 2,
            "judge_calls": judge_calls,
            "defects_opened": len(opened),
            "versions": len(instance.get("versions") or []),
        },
    }


def run_all() -> list:
    """Every audit fixture, in name order."""
    return [run_case(name) for name in AUDIT_CASES]


def main() -> None:
    with contextlib.redirect_stdout(io.StringIO()):
        reports = run_all()
    print(json.dumps(reports, indent=2))


if __name__ == "__main__":
    main()
