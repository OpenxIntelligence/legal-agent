"""Phase F stage 2: matched console-vs-gallery claims agreement.

The same filed memo is assessed twice with an identically
scripted judge: once through the console claims verifier
(`verify_claims.run_verification`, the submit/manual path),
once through the gallery audit path (structural layer plus
`audit_support`). The report grades per-unit agreement and
names gallery-only structural detections the console path has
no layer for. Run with:

    cd agent && .venv/bin/python -m eval.matched

Scripted judges return scripted verdicts, so agreement proves
the two surfaces interpret the same answers the same way —
not that either judges well.
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
from agent_kit.case_record import CaseRecord  # noqa: E402
from eval.artifact_cases import write_document  # noqa: E402
from tests import support  # noqa: E402
from verification import verify_claims  # noqa: E402

REPORT_KEYS = frozenset(
    {
        "case",
        "client",
        "semantic_quality",
        "units",
        "agree",
        "disagreements",
        "gallery_only",
        "diagnostics",
    }
)

CASES = {
    "matched-clean": {
        "title": "Matched clean memo (both surfaces should pass)",
        "documents": {
            "MATCH-LEDGER": [
                "Ledger page one. Paid invoices total 20000.",
                "Ledger page two. No invoices are overdue.",
            ],
        },
        "memo": {
            "conclusion": "The ledger is reconciled and nothing is overdue.",
            "findings": [
                {
                    "item": "totals",
                    "status": "asserted",
                    "summary": "Paid invoices total 20000.",
                    "citations": [
                        {
                            "document_id": "MATCH-LEDGER",
                            "page": 1,
                            "quote": "Paid invoices total 20000.",
                        }
                    ],
                },
                {
                    "item": "status",
                    "status": "asserted",
                    "summary": "No invoices are overdue.",
                    "citations": [
                        {
                            "document_id": "MATCH-LEDGER",
                            "page": 2,
                            "quote": "No invoices are overdue.",
                        }
                    ],
                },
            ],
            "gaps": "No open items remain in the reviewed ledger.",
        },
        "unsupported": [],
        "expect_agree": True,
    },
    "matched-overclaim": {
        "title": "Matched overclaim (both surfaces should fail the unit)",
        "documents": {
            "MATCH-REPORT": [
                "Inspection report. The valve showed wear. The cause "
                "of the wear was not determined.",
            ],
            "MATCH-CONTRACT": [
                "Contract page one. Delivery is due on June 1.",
                "Contract page two. Late delivery accrues a fee.",
            ],
        },
        "memo": {
            "conclusion": "The valve failed because maintenance lapsed.",
            "findings": [
                {
                    "item": "overreach",
                    "status": "asserted",
                    "summary": "The valve failed because maintenance lapsed.",
                    "citations": [
                        {
                            "document_id": "MATCH-REPORT",
                            "page": 1,
                            "quote": "The valve showed wear.",
                        }
                    ],
                },
                {
                    "item": "wrong-page",
                    "status": "asserted",
                    "summary": "Late delivery accrues a fee.",
                    "citations": [
                        {
                            "document_id": "MATCH-CONTRACT",
                            "page": 1,
                            "quote": "Late delivery accrues a fee.",
                        }
                    ],
                },
            ],
            "gaps": "No maintenance log was found in the reviewed record.",
        },
        # Console unit ids; the gallery arm maps them to rows.
        "unsupported": ["finding:overreach", "conclusion"],
        "expect_agree": True,
    },
}


def materialize(root: Path, name: str) -> CaseRecord:
    """Write the fixture matter and file its memo; return the record."""
    case = CASES[name]
    run_id = f"matched-{name}"
    for doc_id, pages in case["documents"].items():
        write_document(root, doc_id, pages)
    record = CaseRecord(root, run_id)
    record.file_memo(case["memo"], "unverified")
    return record


def _scripted(root: Path, units: set[str]):
    def mark(text: str) -> list:
        return [
            {"unit": unit, "reason": "matched eval: scripted unsupported"}
            for unit in sorted(units)
        ]

    return support.scripted(root, [], unsupported=mark)


def _console_arm(record: CaseRecord, root: Path, unsupported: set[str]):
    """The console claims path over the filed memo."""
    fake = _scripted(root, unsupported)
    ctx = tools.Context(fake.client, root, record.data["run_id"], record, True)
    result = verify_claims.run_verification(ctx, record.data["memo"])
    return result, len(fake.calls)


def _gallery_arm(record: CaseRecord, root: Path, unsupported: set[str]):
    """The gallery audit path over the same filed memo.

    Console finding units name checklist items; audit rows are
    numbered in findings order, so the script translates.
    """
    if not record.data.get("memo"):
        return None
    memo = record.data["memo"]
    findings = [f for f in memo.get("findings") or [] if isinstance(f, dict)]
    translated = set()
    for unit in unsupported:
        if unit in ("conclusion", "gaps"):
            translated.add(unit)
            continue
        _, _, item = unit.partition(":")
        for index, finding in enumerate(findings):
            if finding.get("item") == item:
                translated.add(f"finding:R{index + 1}")
    fake = _scripted(root, translated)
    ctx = tools.Context(fake.client, root, record.data["run_id"], record, True)
    instance = record.artifact_open("matched audit")
    rows = instance["rows"]
    structural = artifacts.audit_structural(root, record.data, rows)
    semantic = artifacts.audit_support(ctx, rows, structural)
    artifacts.record_checks(instance, record.data, root, structural, semantic)
    record.artifact_save(instance, "artifact_checked", "matched eval")
    return instance, len(fake.calls)


def _verdict_to_outcome(verdict: str | None) -> str:
    return {"supported": "passed", "unsupported": "failed"}.get(
        verdict or "", "unavailable"
    )


def run_case(name: str) -> dict:
    """Assess one fixture on both surfaces; grade agreement."""
    case = CASES[name]
    unsupported = set(case.get("unsupported", []))
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        record = materialize(root, name)
        console, console_calls = _console_arm(record, root, unsupported)
        gallery = _gallery_arm(record, root, unsupported)
        gallery_calls = gallery[1] if gallery else 0
        return _grade(name, case, console, gallery[0] if gallery else None,
                      console_calls, gallery_calls)


def _grade(name, case, console, gallery, console_calls, gallery_calls):
    console_verdicts = console.get("verdicts") or {}
    console_unavailable = (
        console.get("outcome") == verify_claims.OUTCOME_UNAVAILABLE
    )
    expected_units = [
        u["id"] for u in verify_claims.build_units(case["memo"], [])
    ]
    memo = case["memo"]
    findings = [f for f in memo.get("findings") or [] if isinstance(f, dict)]
    row_of = {f"finding:{f.get('item')}": f"R{index + 1}"
              for index, f in enumerate(findings)}
    row_of["conclusion"] = "RC"
    row_of["gaps"] = "RG"
    rows = {r["row_id"]: r for r in (gallery["rows"] if gallery else [])}
    units = {}
    disagreements = []
    gallery_only = []
    for unit_id in expected_units:
        row_id = row_of.get(unit_id)
        if console_unavailable or unit_id not in console_verdicts:
            console_outcome = "unavailable"
        else:
            console_outcome = _verdict_to_outcome(
                console_verdicts[unit_id]
            )
        row = rows.get(row_id or "")
        checks = (row.get("checks") or {}) if row else {}
        structural = (checks.get("structural") or {}).get("outcome")
        semantic = (checks.get("semantic") or {}).get("outcome")
        if structural == "failed" and semantic == "not_run":
            gallery_only.append(
                {
                    "unit": unit_id,
                    "row": row_id,
                    "structural": structural,
                    "note": "console path has no citation layer",
                }
            )
            agree = True
        else:
            agree = console_outcome == semantic
        if not agree:
            disagreements.append(unit_id)
        units[unit_id] = {
            "row": row_id,
            "console": console_outcome,
            "gallery_structural": structural,
            "gallery_semantic": semantic,
            "agree": agree,
        }
    if gallery is None:
        return {
            "case": name,
            "client": "scripted",
            "semantic_quality": "not measured",
            "gallery": "not_applicable (no filed memo)",
            "units": units,
            "agree": False,
            "disagreements": disagreements,
            "gallery_only": gallery_only,
            "diagnostics": {
                "console_judge_calls": console_calls,
                "gallery_judge_calls": gallery_calls,
            },
        }
    return {
        "case": name,
        "client": "scripted",
        "semantic_quality": (
            "not measured: agreement proves the surfaces interpret "
            "the same scripted answers the same way, not judgment"
        ),
        "units": units,
        "agree": not disagreements,
        "disagreements": disagreements,
        "gallery_only": gallery_only,
        "diagnostics": {
            "console_judge_calls": console_calls,
            "gallery_judge_calls": gallery_calls,
        },
    }


def run_all() -> list:
    """Every fixture, in name order."""
    return [run_case(name) for name in sorted(CASES)]


def main() -> None:
    with contextlib.redirect_stdout(io.StringIO()):
        reports = run_all()
    print(json.dumps(reports, indent=2))


if __name__ == "__main__":
    main()
