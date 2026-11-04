"""Frozen legal-quality benchmark runner (mechanics + deterministic detection).

Each fixture runs through the real audit path on a temporary
workspace — open, structural checks, scripted semantic judge,
record — plus defect synthesis when the implementation provides
it, and is graded against planted truth. The matrix case drives
readiness/staleness stages. Run with:

    cd agent && .venv/bin/python -m eval.quality_run

Scoring (frozen with the cases; denominators in every report):
  structural_match        rows with observed == expected structural / truth rows
  semantic_agreement      rows with observed == expected semantic / truth rows
  defect_recall           expected (category, target) defects observed / expected
  defect_precision        expected defects observed / all observed defects
  quotation_accuracy      altered/wrong-page rows structurally failed / such rows
  issue_coverage          uncovered_issue defects observed / planted
  counterevidence         uncited_counterevidence observed / planted
  contradiction           contradictory_evidence observed / planted contested rows
  unsupported_detected    scripted-unsupported units semantically failed / planted
  abstention              gaps rows matching truth / gaps rows with truth
                        (qualified handling; empty gaps surface as
                        unstated_open_issues defects, not rows)
  clean_false_flags       mismatches + defects on clean rows (must be 0)

structural_match, quotation_accuracy, issue_coverage,
counterevidence, contradiction, and the jurisdiction stages are
deterministic: they measure real detection. semantic_agreement
and unsupported_detected use the scripted judge, so they prove
routing/plumbing only — never judgment quality. Reports always
state their client and what they do not measure.
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
        "rows",
        "defects",
        "stages",
        "metrics",
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


def _observed_defects(instance: dict, data: dict, root: Path) -> list:
    synthesize = getattr(artifacts, "synthesize_defects", None)
    if synthesize is None:
        return []
    return [
        {"category": d.get("category"), "target": d.get("target")}
        for d in synthesize(instance, data, root)
        if isinstance(d, dict)
    ]


def run_audit_case(name: str) -> dict:
    """Run one audit fixture through the real path; grade vs truth."""
    case = quality_cases.CASES[name]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        run_id, record, instance = quality_cases.materialize(root, name)
        fake = support.scripted(root, [], unsupported=_unsupported_for(case))
        ctx = tools.Context(fake.client, root, run_id, record, True)
        rows = instance["rows"]
        structural = artifacts.audit_structural(root, record.data, rows)
        semantic = artifacts.audit_support(ctx, rows, structural)
        artifacts.record_checks(
            instance, record.data, root, structural, semantic
        )
        record.artifact_save(instance, "artifact_checked", "eval")
        observed = _observed_defects(instance, record.data, root)
        return _grade_audit(name, case, instance, observed)


def _grade_audit(name: str, case: dict, instance: dict, observed: list) -> dict:
    truth = case["truth"]
    rows = {}
    complete = True
    for row in instance["rows"]:
        row_id = row["row_id"]
        expected = truth.get(row_id)
        if expected is None:
            complete = False
            continue
        checks = row.get("checks") or {}
        seen = {
            "structural": (checks.get("structural") or {}).get("outcome"),
            "semantic": (checks.get("semantic") or {}).get("outcome"),
        }
        match = (
            seen["structural"] == expected["structural"]
            and seen["semantic"] == expected["semantic"]
        )
        if not match:
            complete = False
        rows[row_id] = {
            "class": expected["class"],
            "expected": {
                "structural": expected["structural"],
                "semantic": expected["semantic"],
            },
            "observed": seen,
            "match": match,
        }
    if set(rows) != set(truth):
        complete = False
    expected_defects = [
        {"category": d["category"], "target": d["target"]}
        for d in case.get("defects", [])
    ]
    matched = [d for d in observed if d in expected_defects]
    extra = [d for d in observed if d not in expected_defects]
    missing = [d for d in expected_defects if d not in observed]
    if missing or extra:
        complete = False
    return {
        "case": name,
        "client": "scripted",
        "semantic_quality": (
            "not measured: semantic verdicts are scripted; "
            "the semantic arm proves routing and record plumbing only"
        ),
        "rows": rows,
        "defects": {
            "expected": expected_defects,
            "observed": observed,
            "matched": matched,
            "missing": missing,
            "extra": extra,
        },
        "stages": {},
        "metrics": {},
        "complete": complete,
    }


def run_matrix_case(name: str) -> dict:
    """Drive the jurisdiction fixture through its three stages."""
    case = quality_cases.CASES[name]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        run_id, record, instance = quality_cases.materialize(root, name)
        del run_id
        stages = {}
        # Stage 1: fresh cell blocks readiness with exact reasons.
        readiness = artifacts.jx_readiness(record.data, instance)
        blockers = sorted(
            reason
            for entry in readiness["blockers"]
            for reason in entry["reasons"]
        )
        stages["stage1_blockers"] = {
            "expected": case["stages"]["stage1_blockers"],
            "observed": blockers,
            "match": blockers == case["stages"]["stage1_blockers"],
        }
        # Stage 2: link a known branch, record an explicitly
        # illustrative citation, a known effective date, confirm.
        branch = record.open_branch(
            "qv_license", "ca", "unknown",
            basis="eval fixture: operator notes a California filing",
        )
        artifacts.link_cell_branch(instance, record.data, "C1", branch["id"])
        record.resolve_branch(branch["id"], "known", "eval fixture")
        artifacts.set_cell(
            instance, record.data, "C1",
            proposition="eval fixture: illustrative only, no rule stated",
            citation="illustrative only: no authority consulted",
            effective_date="2026-01-01",
            last_checked="2026-10-29",
        )
        artifacts.link_cell_branch(instance, record.data, "C1", branch["id"])
        artifacts.flag_cell(instance, "C1", "confirmed", "eval fixture")
        record.artifact_save(instance, "artifact_staged", "eval stage 2")
        readiness = artifacts.jx_readiness(record.data, instance)
        stages["stage2_resolved"] = {
            "expected": case["stages"]["stage2_resolved"],
            "observed": readiness["resolved"],
            "match": readiness["resolved"] == case["stages"]["stage2_resolved"],
        }
        stages["stage2_provisional"] = {
            "expected": case["stages"]["stage2_provisional"],
            "observed": readiness["provisional"],
            "match": readiness["provisional"]
            == case["stages"]["stage2_provisional"],
        }
        # Stage 3: disputing the branch restales the cell.
        record.resolve_branch(branch["id"], "disputed", "eval fixture")
        cell = next(
            c for c in instance["cells"] if c.get("cell_id") == "C1"
        )
        stale = artifacts.cell_staleness(record.data, cell)
        readiness = artifacts.jx_readiness(record.data, instance)
        stages["stage3_stale_reason"] = {
            "expected": case["stages"]["stage3_stale_reason"],
            "observed": list(stale["reasons"]),
            "match": case["stages"]["stage3_stale_reason"] in stale["reasons"],
        }
        stages["stage3_provisional"] = {
            "expected": case["stages"]["stage3_provisional"],
            "observed": readiness["provisional"],
            "match": readiness["provisional"]
            == case["stages"]["stage3_provisional"],
        }
        complete = all(s["match"] for s in stages.values())
        return {
            "case": name,
            "client": "scripted",
            "semantic_quality": "not applicable: no judge runs in this case",
            "rows": {},
            "defects": {
                "expected": [], "observed": [], "matched": [],
                "missing": [], "extra": [],
            },
            "stages": stages,
            "metrics": {},
            "complete": complete,
        }


def run_case(name: str) -> dict:
    if quality_cases.CASES[name].get("kind") == "matrix":
        return run_matrix_case(name)
    return run_audit_case(name)


def run_all() -> list:
    """Every fixture, in name order."""
    return [run_case(name) for name in sorted(quality_cases.CASES)]


def summarize(reports: list) -> dict:
    """Benchmark-wide metrics with denominators (see module docstring)."""
    structural_hit = structural_total = 0
    semantic_hit = semantic_total = 0
    quote_hit = quote_total = 0
    unsupported_hit = unsupported_total = 0
    abstain_hit = abstain_total = 0
    clean_false_flags = 0
    defect_expected = defect_observed = defect_matched = 0
    by_category: dict[str, dict[str, int]] = {}
    for report in reports:
        case = quality_cases.CASES[report["case"]]
        for row_id, row in report["rows"].items():
            structural_total += 1
            semantic_total += 1
            if row["observed"]["structural"] == row["expected"]["structural"]:
                structural_hit += 1
            if row["observed"]["semantic"] == row["expected"]["semantic"]:
                semantic_hit += 1
            if row["class"] in ("altered_quote", "wrong_page"):
                quote_total += 1
                if row["observed"]["structural"] == "failed":
                    quote_hit += 1
            if row["class"] in ("unsupported_inference", "unsupported_conclusion"):
                unsupported_total += 1
                if row["observed"]["semantic"] == "failed":
                    unsupported_hit += 1
            if row_id == "RG":
                abstain_total += 1
                if row["match"]:
                    abstain_hit += 1
            if row["class"] == "clean" and not row["match"]:
                clean_false_flags += 1
        for defect in report["defects"]["expected"]:
            defect_expected += 1
            bucket = by_category.setdefault(
                defect["category"], {"expected": 0, "observed": 0}
            )
            bucket["expected"] += 1
        for defect in report["defects"]["observed"]:
            defect_observed += 1
        for defect in report["defects"]["matched"]:
            defect_matched += 1
            by_category[defect["category"]]["observed"] += 1
        clean_false_flags += len(report["defects"]["extra"])
        for row in report["defects"]["observed"]:
            if row["target"] in report["rows"] and report["rows"][
                row["target"]
            ]["class"] == "clean":
                # A defect on a clean row is a false flag unless the
                # row legitimately carries one (RC inherits citations).
                if row["target"] != "RC":
                    clean_false_flags += 1
    stages_hit = stages_total = 0
    for report in reports:
        for stage in report["stages"].values():
            stages_total += 1
            if stage["match"]:
                stages_hit += 1
    return {
        "client": "scripted",
        "cases": len(reports),
        "cases_complete": sum(1 for r in reports if r["complete"]),
        "structural_match": {
            "hit": structural_hit, "total": structural_total,
        },
        "semantic_agreement": {
            "hit": semantic_hit, "total": semantic_total,
            "note": "scripted judge: plumbing only",
        },
        "defect_recall": {"hit": defect_matched, "total": defect_expected},
        "defect_precision": {
            "hit": defect_matched, "total": defect_observed,
        },
        "quotation_accuracy": {"hit": quote_hit, "total": quote_total},
        "issue_coverage": by_category.get(
            "uncovered_issue", {"expected": 0, "observed": 0}
        ),
        "counterevidence": by_category.get(
            "uncited_counterevidence", {"expected": 0, "observed": 0}
        ),
        "contradiction": by_category.get(
            "contradictory_evidence", {"expected": 0, "observed": 0}
        ),
        "unsupported_detected": {
            "hit": unsupported_hit, "total": unsupported_total,
            "note": "scripted judge: plumbing only",
        },
        "abstention": {"hit": abstain_hit, "total": abstain_total},
        "jurisdiction_stages": {"hit": stages_hit, "total": stages_total},
        "clean_false_flags": clean_false_flags,
    }


def main() -> None:
    with contextlib.redirect_stdout(io.StringIO()):
        reports = run_all()
    print(json.dumps({"reports": reports, "summary": summarize(reports)},
                     indent=2))


if __name__ == "__main__":
    main()
