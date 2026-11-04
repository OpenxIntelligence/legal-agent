"""Run-record audit: is this run's record complete and reconstructable?

Checks the persisted record (not live judgment): every tool call
has its output, no operation was left half-applied, approvals
settled, findings trace to live facts, unknowns carry triage, and
filed memos carry their claims assessment. Usage:

    ./helper audit RUN_ID [--json]

Exit 0 when the record is complete, 1 otherwise. Complements
./helper verify (which re-checks memo content): audit checks the
record around it — the traceability a reviewer or regulator
reconstructs the run from.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parents[1]


def _resolve_root() -> Path:
    override = (os.environ.get("LEGAL_AGENT_ROOT") or "").strip()
    if not override:
        return CODE_ROOT
    root = Path(override).expanduser().resolve()
    if not root.is_dir():
        raise RuntimeError(f"LEGAL_AGENT_ROOT is not a directory: {override}")
    return root


ROOT = _resolve_root()
sys.path.insert(0, str(CODE_ROOT))

from agent_kit import tools  # noqa: E402
from agent_kit.case_record import CaseRecord  # noqa: E402
from agent_kit.progress import load_progress  # noqa: E402
from agent_kit.reasoning import quote_key, trace_fact_impact  # noqa: E402


def audit_run(root: Path, run_id: str) -> dict:
    """Audit one run's record; return {run_id, passed, findings}."""
    findings: list[dict] = []

    def fail(check: str, detail: str) -> None:
        findings.append({"check": check, "passed": False, "detail": detail})

    def note(check: str, detail: str) -> None:
        findings.append({"check": check, "passed": True, "detail": detail})

    try:
        progress = load_progress(root, run_id)
    except FileNotFoundError:
        return {
            "run_id": run_id,
            "passed": False,
            "findings": [
                {"check": "run_exists", "passed": False,
                 "detail": f"no such run: {run_id}"}
            ],
        }
    _check_pairing(progress, fail, note)
    _check_pending_op(root, run_id, fail, note)
    try:
        case = CaseRecord(root, run_id)
    except FileNotFoundError:
        fail("case_record", "no case record for this run")
        return {
            "run_id": run_id,
            "passed": all(f["passed"] for f in findings),
            "findings": findings,
        }
    data = case.data
    _check_approvals(data, fail, note)
    _check_finding_trace(data, fail, note)
    _check_impact(data, fail, note)
    _check_spans(data, fail, note)
    _check_triage(data, fail, note)
    _check_memo_assessment(progress, data, fail, note)
    return {
        "run_id": run_id,
        "passed": all(f["passed"] for f in findings),
        "findings": findings,
    }


def _check_pairing(progress, fail, note) -> None:
    calls = {}
    outputs = set()
    for message in progress.get("messages", []) or []:
        if not isinstance(message, dict):
            continue
        if message.get("type") == "function_call":
            calls[message.get("call_id", "")] = message.get("name", "?")
        elif message.get("type") == "function_call_output":
            outputs.add(message.get("call_id", ""))
    orphaned = sorted(
        f"{call} ({name})" for call, name in calls.items() if call not in outputs
    )
    if orphaned:
        fail(
            "message_pairing",
            f"tool calls without outputs: {', '.join(orphaned)}",
        )
    else:
        note("message_pairing", f"{len(calls)} calls all paired with outputs")


def _check_pending_op(root, run_id, fail, note) -> None:
    pending = tools.pending_op_path(root, run_id)
    if pending.is_file():
        fail(
            "pending_op",
            "pending_op.json left behind: an operation may be half-applied",
        )
    else:
        note("pending_op", "no half-applied operation")


def _check_approvals(data, fail, note) -> None:
    pending = [
        a.get("id", "?")
        for a in (data.get("approvals") or [])
        if isinstance(a, dict) and not a.get("decision")
    ]
    if pending:
        fail(
            "approvals",
            f"undecided approval requests: {', '.join(sorted(pending))}",
        )
    else:
        total = len(data.get("approvals") or [])
        note("approvals", f"{total} requests, all decided")


def _check_finding_trace(data, fail, note) -> None:
    memo = data.get("memo") or {}
    findings = memo.get("findings") or []
    if not findings:
        note("finding_trace", "no filed memo to trace")
        return
    live = {
        (str(f.get("document_id", "")), f.get("page"),
         " ".join(str(f.get("quote", "") or "").lower().split()))
        for f in (data.get("facts") or [])
        if isinstance(f, dict)
    }
    untraced = []
    for finding in findings:
        if not isinstance(finding, dict) or finding.get("status") != "asserted":
            continue
        item = finding.get("item", "?")
        citations = finding.get("citations") or []
        if not any(
            isinstance(c, dict)
            and (
                str(c.get("document_id", "")), c.get("page"),
                " ".join(str(c.get("quote", "") or "").lower().split()),
            )
            in live
            for c in citations
        ):
            untraced.append(item)
    if untraced:
        fail(
            "finding_trace",
            f"asserted findings without a live fact: {', '.join(sorted(untraced))}",
        )
    else:
        note("finding_trace", "every asserted finding traces to a live fact")


def _check_impact(data, fail, note) -> None:
    memo = data.get("memo") or {}
    if not (memo.get("findings") or []):
        note("impact", "no filed memo to trace impact for")
        return
    facts = [f for f in (data.get("facts") or []) if isinstance(f, dict)]
    withdrawn = [
        f for f in (data.get("withdrawn_facts") or []) if isinstance(f, dict)
    ]
    problems = []
    for fact in withdrawn:
        trace = trace_fact_impact(data, memo, str(fact.get("id", "")))
        if trace["cited_by"]:
            problems.append(
                f"filed memo cites withdrawn {trace['fact']}: "
                f"{', '.join(trace['cited_by'])}"
            )
    successors: dict[str, set] = {}
    for fact in facts:
        old = str(fact.get("supersedes") or "").strip()
        if old and fact.get("id"):
            successors.setdefault(old, set()).add(fact.get("id"))

    for finding in memo.get("findings") or []:
        if not isinstance(finding, dict):
            continue
        # Claim-local like the memo rules: each finding citing a
        # superseded fact must cite every successor itself. Keys
        # come from the shared contract (textnorm + page coerce).
        cited_here = {
            f.get("id")
            for citation in finding.get("citations") or []
            if isinstance(citation, dict)
            for f in facts
            if quote_key(
                f.get("document_id"), f.get("page"), f.get("quote")
            )
            == quote_key(
                citation.get("document_id"),
                citation.get("page"),
                citation.get("quote"),
            )
        }
        for old_id in sorted(successors):
            if old_id not in cited_here:
                continue
            missing = sorted(successors[old_id] - cited_here)
            if missing:
                problems.append(
                    f"filed memo finding "
                    f"{finding.get('item', '?')} cites superseded "
                    f"{old_id} without {', '.join(missing)}"
                )
    if problems:
        fail("impact", "; ".join(problems))
    else:
        note("impact", "filed memo rests on live, current facts")


def _check_spans(data, fail, note) -> None:
    memo = data.get("memo") or {}
    spans = []
    for finding in memo.get("findings") or []:
        if not isinstance(finding, dict):
            continue
        for citation in finding.get("citations") or []:
            if not isinstance(citation, dict):
                continue
            quote = str(citation.get("quote", "") or "")
            spans.append((str(finding.get("item", "?")), quote))
    if not spans:
        note("spans", "no filed memo to inventory")
        return
    lengths = sorted(len(q) for _, q in spans)
    median = lengths[len(lengths) // 2]
    digits = sum(1 for _, q in spans if any(c.isdigit() for c in q))
    shortest = min(spans, key=lambda pair: len(pair[1]))
    # Descriptive only: scripted heuristics cannot grade whether a
    # span proves its claim (that needs the specific terms, which
    # needs semantics). Graded specificity scoring belongs in the
    # live-judge arm; this inventory just shows the reviewer what
    # the memo rests on.
    note(
        "spans",
        f"{len(spans)} cited spans, median length {median} chars, "
        f"{digits} with digits, "
        f"shortest {shortest[0]} ({len(shortest[1])} chars)",
    )


def _check_triage(data, fail, note) -> None:
    if not isinstance(data.get("identification"), dict):
        note("triage", "no identification chain to check")
        return
    refs = {
        str(t.get("ref", ""))
        for t in (data.get("triages") or [])
        if isinstance(t, dict)
    }
    missing = [
        a.get("id", "?")
        for a in (data.get("foundational") or [])
        if isinstance(a, dict)
        and a.get("status") == "unresolved"
        and a.get("id") not in refs
    ]
    fields = (data.get("matter", {}) or {}).get("fields", {})
    missing.extend(
        f"matter:{name}"
        for name, field in fields.items()
        if isinstance(field, dict)
        and field.get("status") == "unknown"
        and f"matter:{name}" not in refs
    )
    if missing:
        fail(
            "triage",
            f"unknowns without triage decisions: {', '.join(sorted(missing))}",
        )
    else:
        note("triage", "every unknown carries a triage decision")


def _check_memo_assessment(progress, data, fail, note) -> None:
    memo = data.get("memo")
    if not memo:
        note("memo_assessment", "nothing filed")
        return
    if not progress.get("verification"):
        note("memo_assessment", "filed unverified (verification off)")
        return
    claims = [
        c for c in (data.get("memo_checks") or [])
        if isinstance(c, dict) and c.get("check") == "claims"
    ]
    if not claims:
        fail("memo_assessment", "filed memo has no claims assessment")
    elif not any(c.get("passed") for c in claims):
        fail("memo_assessment", "filed memo has no passing claims assessment")
    else:
        note("memo_assessment", "filed memo carries a passing claims assessment")


def format_report(report: dict) -> str:
    """The audit as human-readable lines."""
    lines = [
        f"audit {report['run_id']}: "
        f"{'COMPLETE' if report['passed'] else 'INCOMPLETE'}"
    ]
    for finding in report["findings"]:
        mark = "ok" if finding["passed"] else "GAP"
        lines.append(f"  {mark:3} {finding['check']}: {finding['detail']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit one run's record.")
    parser.add_argument("run_id")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    root = ROOT if (os.environ.get("LEGAL_AGENT_ROOT") or "").strip() else Path.cwd()
    report = audit_run(root, args.run_id)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(format_report(report))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
