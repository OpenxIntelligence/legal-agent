#!/usr/bin/env python3
"""Run the verification checks on a finished run's memo or checklist (the agent is not involved).

./helper verify RUN_ID --memo        # the memo rules (verification/verify_memo.py) and the claims check
./helper verify RUN_ID --checklist   # the checklist against the case's ground truth (verification/verify_checklist.py)

The same check functions the tools call. Results are saved with the run
(workspace/runs/RUN_ID/verification.json) for the console; the run itself is not changed.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib
import json
import os
import sys
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parents[1]


def _resolve_root() -> Path:
    """Keep executable imports in the checkout and all matter data in its own root."""
    override = (os.environ.get("LEGAL_AGENT_ROOT") or "").strip()
    if not override:
        return CODE_ROOT
    root = Path(override).expanduser().resolve()
    if not root.is_dir():
        raise RuntimeError(f"LEGAL_AGENT_ROOT is not a directory: {override}")
    return root


ROOT = _resolve_root()
sys.path.append(
    str(CODE_ROOT)
)  # after the test runner's own paths, so tests import the same modules

from agent_kit import tools  # noqa: E402
from agent_kit.case_record import CaseRecord  # noqa: E402
from agent_kit.client import make_client, MissingKeyError  # noqa: E402
from agent_kit.progress import now  # noqa: E402
from agent_kit.tools.set_checklist import normalize_result  # noqa: E402
from quality import policy  # noqa: E402
from verification import verify_claims  # noqa: E402


def context(client, root: Path, run_id: str) -> tools.Context:
    return tools.Context(client, root, run_id, CaseRecord(root, run_id), True)


def verify_memo(ctx: tools.Context) -> dict:
    filed = ctx.case.data.get("memo")
    if not filed:
        raise RuntimeError(f"{ctx.run_id} has no memo to verify")
    memo = {
        k: filed.get(k, "" if k != "findings" else [])
        for k in ("conclusion", "findings", "gaps")
    }
    rules = importlib.import_module("verification.verify_memo").run_verification(
        ctx, memo
    )
    rule_problems = [str(p) for p in rules.get("feedback") or []]
    if not rules.get("passed"):
        # Memo rules are a prerequisite: no semantic verdict on an invalid memo.
        return {
            "what": "memo",
            "passed": False,
            "state": "revision-required",
            "rules": rule_problems,
            "reasoning": [],
            "reasoning_advisories": [],
            "claims": ["claims check not run: memo rules failed"],
            "claims_outcome": verify_claims.OUTCOME_UNAVAILABLE,
            "claims_scope": None,
            "claims_verifier": verify_claims.VERIFIER_VERSION,
            "claims_support": {},
        }
    reasoning = importlib.import_module(
        "verification.verify_reasoning"
    ).run_verification(ctx, memo)
    reasoning_problems = [str(p) for p in reasoning.get("feedback") or []]
    if not reasoning.get("passed"):
        # The reasoning gate is a prerequisite like the memo rules:
        # no semantic verdict on an unreasoned memo.
        return {
            "what": "memo",
            "passed": False,
            "state": "revision-required",
            "rules": [],
            "reasoning": reasoning_problems,
            "reasoning_advisories": [],
            "claims": ["claims check not run: reasoning gate failed"],
            "claims_outcome": verify_claims.OUTCOME_UNAVAILABLE,
            "claims_scope": None,
            "claims_verifier": verify_claims.VERIFIER_VERSION,
            "claims_support": {},
        }
    claims = verify_claims.run_verification(ctx, memo)
    return {
        "what": "memo",
        "passed": bool(claims.get("passed")),
        "state": policy.aggregate_memo(True, claims.get("outcome")),
        "rules": [],
        "reasoning": [],
        # The fresh gate run's non-blocking notes (e.g. contested
        # one-sided assertions): manual verify re-runs the checks,
        # so it surfaces these live rather than from memo_checks.
        "reasoning_advisories": [
            str(a) for a in reasoning.get("advisory") or []
        ],
        "claims": [str(p) for p in claims.get("feedback") or []],
        "claims_outcome": claims.get("outcome"),
        "claims_scope": claims.get("scope"),
        "claims_verifier": claims.get("verifier"),
        "claims_support": claims.get("support") or {},
    }


def verify_checklist(ctx: tools.Context) -> dict:
    checklist = ctx.case.data["checklist"]
    if not checklist:
        raise RuntimeError(f"{ctx.run_id} has no checklist to verify")
    keys = ("question", "evidence_needed", "needs_signature_check")
    items = [{"id": k, **{f: v.get(f) for f in keys}} for k, v in checklist.items()]
    verify_checklist_ = importlib.import_module("verification.verify_checklist")
    result = normalize_result(verify_checklist_.run_verification(ctx, items))
    if result["score"] is None:
        raise RuntimeError(
            "this case has no ground truth, so its checklist cannot be checked"
        )
    return dict(result, what="checklist")


def format_memo(result: dict) -> str:
    """Human-readable memo result: verdict, claims outcome, scope, support refs."""
    lines = [
        policy.primary_memo_label(
            bool(result.get("passed")),
            result.get("rules") or [],
            result.get("claims_outcome"),
        )
    ]
    outcome = result.get("claims_outcome")
    if outcome:
        scope = result.get("claims_scope") or {}
        expected = scope.get("expected") or []
        assessed = scope.get("assessed") or []
        verifier = result.get("claims_verifier") or "unknown verifier"
        lines.append(
            f"  claims check: {outcome} "
            f"(assessed {len(assessed)} of {len(expected)} units · "
            f"verifier {verifier})"
        )
    for problem in result.get("rules") or []:
        lines.append(f"  rule:  {problem}")
    for problem in result.get("reasoning") or []:
        lines.append(f"  reasoning: {problem}")
    for advisory in result.get("reasoning_advisories") or []:
        lines.append(f"  advisory: {advisory}")
    for problem in result.get("claims") or []:
        lines.append(f"  claim: {problem}")
    for unit_id, refs in (result.get("claims_support") or {}).items():
        if refs:
            lines.append(f"  support {unit_id}: {'; '.join(str(r) for r in refs)}")
    return "\n".join(lines)


def save(root: Path, run_id: str, result: dict) -> dict:
    path = root / "workspace/runs" / run_id / "verification.json"
    try:
        saved = json.loads(path.read_text())
    except (OSError, ValueError):
        saved = []
    result = dict(result, at=now())
    path.write_text(json.dumps(saved + [result], indent=1))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("run_id")
    what = parser.add_mutually_exclusive_group(required=True)
    what.add_argument("--memo", action="store_true")
    what.add_argument("--checklist", action="store_true")
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    args = parser.parse_args()
    if not (ROOT / "workspace/runs" / args.run_id / "progress.json").is_file():
        sys.exit(f"no such run: {args.run_id}")
    try:
        with contextlib.redirect_stdout(sys.stderr):  # stdout carries only the result
            ctx = context(make_client(), ROOT, args.run_id)
            result = (verify_memo if args.memo else verify_checklist)(ctx)
    except (MissingKeyError, RuntimeError, FileNotFoundError) as error:
        sys.exit(str(error))
    result = save(ROOT, args.run_id, result)
    if args.json:
        print(json.dumps(result, indent=2))
    elif args.memo:
        print(format_memo(result))
    else:
        print(
            f"CHECKLIST {'passes' if result['passed'] else 'fails'} · score {result['score']:.0%}"
        )
        if result["feedback"]:
            print(f"  feedback: {result['feedback']}")


if __name__ == "__main__":
    main()
