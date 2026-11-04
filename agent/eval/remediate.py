"""Phase F stage 3: correction-loop mechanics eval.

Reuses the stage-1 defect fixtures (`eval/artifact_cases`) and
runs each through the full reviewer loop with a declared,
deterministic fix policy: initial check, revise what revision
can fix, supply what evidence can supply, recheck, review with
qualifications for what remains, export. Run with:

    cd agent && .venv/bin/python -m eval.remediate

Grading: fixable rows must pass both layers after remediation;
unfixable rows (absent source, revoked access) must still fail
and appear in the export's limitations; the review must bind
the inspected snapshot; privileged quotes stay redacted.
Scripted judges return scripted verdicts, so this proves loop
mechanics — not judgment, and not human effort.
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
        "fixed",
        "unresolved",
        "review_bound",
        "export_ok",
        "complete",
        "diagnostics",
    }
)

# The declared reviewer policy: exact replacement values per
# defective row. Rows absent here are either clean or cannot be
# fixed by revision (absent source, revoked access).
FIXES = {
    "eval-citation-defects": {
        # Quote lives on page 2, not page 1.
        "R2": {"citation": {"page": 2}},
        # Supply the exact quoted words.
        "R4": {"citation": {
            "quote": "The parties confirmed the June 1 date."
        }},
        # Completed analysis supplies the transcript.
        "R6": {"transcribe": "EVAL-SCAN"},
        # Re-citing re-pins the current sha.
        "R7": {"citation": {
            "document_id": "EVAL-EMAIL",
            "page": 1,
            "quote": "The parties confirmed the June 1 date.",
        }},
    },
    "eval-inference-defects": {
        # Narrow to what the quote establishes.
        "R1": {"assertion": "The valve showed wear."},
        "RC": {"assertion": "The valve showed wear of undetermined cause."},
    },
    "eval-clean-ledger": {},
}

# Rows revision cannot fix: absent source, revoked access, and
# the conclusion row that inherits their citations.
UNFIXABLE = {
    "eval-citation-defects": {"R3", "R5", "RC"},
    "eval-inference-defects": set(),
    "eval-clean-ledger": set(),
}


def _scripted(root: Path, units: set[str]):
    def mark(text: str) -> list:
        return [
            {"unit": unit, "reason": "remediation eval: scripted unsupported"}
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
    record.artifact_save(instance, "artifact_checked", "remediation eval")
    return len(fake.calls)


def _apply_fixes(case_name, root, record, instance) -> int:
    case = artifact_cases.CASES[case_name]
    revisions = 0
    for row_id, fix in FIXES.get(case_name, {}).items():
        if "transcribe" in fix:
            doc_id = fix["transcribe"]
            artifact_cases.write_document(
                root, doc_id, case["documents"][doc_id]
            )
            revisions += 1
            continue
        artifacts.revise_row(
            instance,
            record.data,
            root,
            row_id,
            assertion=fix.get("assertion"),
            citation=fix.get("citation"),
        )
        revisions += 1
    record.artifact_save(instance, "artifact_revised", "remediation eval")
    return revisions


def run_case(name: str) -> dict:
    """Run one fixture through the full reviewer loop; grade it."""
    case = artifact_cases.CASES[name]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        run_id, record, instance = artifact_cases.materialize(root, name)
        judge_calls = _check(
            record, root, instance, set(case.get("unsupported", []))
        )
        revisions = _apply_fixes(name, root, record, instance)
        # Narrowed assertions are scripted supported: the recheck
        # proves routing and recording, not that narrowing was wise.
        judge_calls += _check(record, root, instance, set())
        snapshot = artifacts.current_snapshot(instance, record.data)
        qualifications = [
            f"{row_id} unresolved: revision cannot supply "
            f"{'a missing source' if row_id != 'RC' else 'its inherited citations'}"
            for row_id in sorted(UNFIXABLE.get(name, set()))
        ]
        artifacts.review_instance(
            instance, record.data, "approved",
            "remediation eval review", qualifications, snapshot,
        )
        record.artifact_save(instance, "artifact_reviewed", "remediation eval")
        review_bound = (
            instance["review"].get("snapshot")
            == artifacts.current_snapshot(instance, record.data)
        )
        report = artifacts.export_instance(
            instance, record.data, root, None
        )
        return _grade(
            name, instance, report, review_bound, revisions, judge_calls
        )


def _grade(name, instance, report, review_bound, revisions, judge_calls):
    rows = {r["row_id"]: r for r in instance["rows"]}
    exported = {r["row_id"]: r for r in report["rows"]}
    fixed = []
    unresolved = []
    complete = bool(review_bound)
    for row_id, row in sorted(rows.items()):
        checks = row.get("checks") or {}
        structural = (checks.get("structural") or {}).get("outcome")
        semantic = (checks.get("semantic") or {}).get("outcome")
        if row_id in UNFIXABLE.get(name, set()):
            ok = structural == "failed" and bool(
                exported[row_id]["limitations"]
            )
            unresolved.append({"row": row_id, "ok": ok})
            complete = complete and ok
            continue
        # Gaps rows carry no citations: not_run structural with a
        # passed semantic is their fixed state, not a defect.
        if row.get("kind") == "gaps":
            ok = structural == "not_run" and semantic == "passed"
        else:
            ok = structural == "passed" and semantic == "passed"
        fixed.append({"row": row_id, "ok": ok})
        complete = complete and ok
    # Privileged quotes never leak, even for unresolved rows.
    export_ok = complete and all(
        c.get("quote") == "[redacted: privileged]"
        for r in report["rows"]
        for c in r["citations"]
        if c.get("document_id") == "EVAL-DRAFT"
    )
    complete = complete and export_ok
    return {
        "case": name,
        "client": "scripted",
        "semantic_quality": (
            "not measured: recheck verdicts are scripted; this "
            "proves loop mechanics (revise, recheck, review, "
            "export), not judgment or human effort"
        ),
        "fixed": fixed,
        "unresolved": unresolved,
        "review_bound": review_bound,
        "export_ok": export_ok,
        "complete": complete,
        "diagnostics": {
            "revisions": revisions,
            "rechecks": 2,
            "judge_calls": judge_calls,
            "versions": len(instance.get("versions") or []),
        },
    }


def run_all() -> list:
    """Every defect fixture, in name order."""
    return [run_case(name) for name in sorted(artifact_cases.CASES)]


def main() -> None:
    with contextlib.redirect_stdout(io.StringIO()):
        reports = run_all()
    print(json.dumps(reports, indent=2))


if __name__ == "__main__":
    main()
