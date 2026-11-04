"""Offline eval mechanics runner: scripted clients, temp workspaces, JSON reports.

Each report grades the FINAL STATE (run status, checklist coverage, filed
memo, claims outcomes), with turns and tokens kept as diagnostics. The
report always states its client. Run with:

    cd agent && .venv/bin/python -m eval.run
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_kit import usage  # noqa: E402
from agent_kit.case_record import CaseRecord  # noqa: E402
from eval import cases  # noqa: E402
from legal_agent.loop import run_agent  # noqa: E402
from tests.fake_model import Scripted, ScriptedModel  # noqa: E402

REPORT_KEYS = frozenset(
    {
        "case",
        "verification",
        "client",
        "status",
        "coverage",
        "memo",
        "claims",
        "escalations",
        "refusals",
        "citation_set",
        "tool_sequence",
        "diagnostics",
    }
)
DIAGNOSTIC_KEYS = frozenset(
    {"turns", "agent_tokens", "tool_tokens", "tool_calls"}
)

_ENV_KEYS = (
    "CASE_DIR",
    "GROUND_TRUTH_DIR",
    "VERIFICATION",
    "RUN_TOKEN_BUDGET",
    "MAX_CONTEXT_BYTES",
    "QC_ENABLED",
)


def _swap_env(root: Path, verification: bool, qc: bool = True) -> dict:
    old = {key: os.environ.get(key) for key in _ENV_KEYS}
    os.environ["CASE_DIR"] = str(root / "case")
    os.environ["GROUND_TRUTH_DIR"] = str(root / "ground_truth")
    os.environ["VERIFICATION"] = "on" if verification else "off"
    os.environ["QC_ENABLED"] = "on" if qc else "off"
    os.environ.pop("RUN_TOKEN_BUDGET", None)
    os.environ.pop("MAX_CONTEXT_BYTES", None)
    return old


def _restore_env(old: dict) -> None:
    for key, value in old.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def _report(
    name: str,
    verification: bool,
    root: Path,
    progress: dict,
    include_qc: bool = False,
) -> dict:
    record = CaseRecord(root, progress["run_id"])
    data = record.data
    checklist = data.get("checklist", {})
    ledger = usage.totals(root, progress["run_id"])
    agent_tokens = sum(
        (t.get("input_tokens") or 0) + (t.get("output_tokens") or 0)
        for t in progress.get("turns", [])
    )
    memo = data.get("memo")
    diagnostics = {
        "turns": progress.get("turn", 0),
        "agent_tokens": agent_tokens,
        "tool_tokens": ledger["input_tokens"] + ledger["output_tokens"],
        "tool_calls": ledger["calls"],
    }
    if include_qc:
        diagnostics["failures"] = ledger["failures"]
        diagnostics["latency_ms"] = ledger["latency_ms"]
    report = {
        "case": name,
        "verification": verification,
        "client": "scripted",
        "status": progress["status"],
        "coverage": {
            status: sum(
                1 for v in checklist.values() if v.get("status") == status
            )
            for status in ("answered", "escalated", "open")
        }
        | {"total": len(checklist)},
        "memo": None if memo is None else {"status": memo.get("status")},
        "claims": [
            {
                "outcome": c.get("outcome"),
                "passed": c.get("passed"),
                "assessed": len((c.get("scope") or {}).get("assessed", [])),
            }
            for c in data.get("memo_checks", [])
            if c.get("check") == "claims"
        ],
        "escalations": len(data.get("requests", [])),
        "refusals": record.memo_refusals(),
        "citation_set": sorted(
            {
                (
                    str(citation.get("document_id", "")),
                    str(citation.get("page", "")),
                )
                for finding in (memo or {}).get("findings") or []
                if isinstance(finding, dict)
                for citation in finding.get("citations") or []
                if isinstance(citation, dict)
            }
        ),
        "tool_sequence": [
            m.get("name")
            for m in progress.get("messages", [])
            if isinstance(m, dict) and m.get("type") == "function_call"
        ],
        "diagnostics": diagnostics,
    }
    if include_qc:
        tasks = data.get("repair_tasks", {})
        report["qc"] = {
            "reviews": len(data.get("qc_reviews", [])),
            "opened": len(tasks),
            "resolved": sum(
                1 for t in tasks.values() if t.get("status") == "resolved"
            ),
            "unresolved": sum(
                1 for t in tasks.values() if t.get("status") == "unresolved"
            ),
            "skipped": any(
                a.get("action") == "qc_skipped" for a in data.get("audit", [])
            ),
        }
    return report


def run_case(
    name: str,
    *,
    verification: bool = True,
    turns: int = 30,
    qc: bool = True,
    include_qc: bool = False,
) -> dict:
    """Run one eval case with a scripted client; return its report."""
    case = cases.CASES[name]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "agent"
        cases.materialize(root, case)
        old = _swap_env(root, verification, qc)
        try:
            queue = list(case["script"])

            def planner(body):
                return queue.pop(0) if queue else None

            client = Scripted(
                ScriptedModel(cases.transcripts(case), planner, {})
            ).client
            progress = run_agent(root=root, client=client, turns=turns)
            return _report(name, verification, root, progress, include_qc)
        finally:
            _restore_env(old)


def run_all() -> list:
    """Every case in both verification modes."""
    return [
        run_case(name, verification=verification)
        for name in sorted(cases.CASES)
        for verification in (True, False)
    ]


def main() -> None:
    with contextlib.redirect_stdout(io.StringIO()):
        reports = run_all()
    print(json.dumps(reports, indent=2))


if __name__ == "__main__":
    main()
