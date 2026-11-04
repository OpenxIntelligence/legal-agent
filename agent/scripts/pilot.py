#!/usr/bin/env python3
"""Pilot key review: list draft keys, record attorney sign-off.

    ./helper pilot list
    ./helper pilot review MATTER ISSUE --approve|--request-changes \\
        --reviewer NAME [--notes TEXT] --version PILOT_VERSION

Reviews live in <data-root>/workspace/pilot_reviews.json (data
follows LEGAL_AGENT_ROOT). A review binds to the exact pilot
version shown: a stale version is refused, never applied.
History is append-only; the latest entry is the state.

JSON to stdout; errors to stderr with a non-zero exit.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_ROOT))

from eval import pilot_cases as pilot  # noqa: E402
from eval.calibration import cohen_kappa  # noqa: E402


def _resolve_root() -> Path:
    """Data root: LEGAL_AGENT_ROOT when set, else this agent folder."""
    override = (os.environ.get("LEGAL_AGENT_ROOT") or "").strip()
    if override:
        root = Path(override)
        if not root.is_dir():
            raise RuntimeError(
                f"LEGAL_AGENT_ROOT is not a directory: {override}")
        return root
    return CODE_ROOT


def _reviews_path(root: Path) -> Path:
    return root / "workspace" / "pilot_reviews.json"


def _read_reviews(root: Path) -> dict:
    path = _reviews_path(root)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except ValueError:
        raise RuntimeError("pilot reviews file is corrupt") from None
    return data if isinstance(data, dict) else {}


def _find(matter_id: str, issue_id: str) -> tuple[dict, dict]:
    for matter in pilot.MATTERS:
        if matter["id"] != matter_id:
            continue
        for issue in matter["issues"]:
            if issue["id"] == issue_id:
                return matter, issue
    raise RuntimeError(f"unknown pilot issue {matter_id}/{issue_id}")


def list_keys(root: Path) -> dict:
    """Matters, draft keys, and current review state."""
    stored = _read_reviews(root)
    matters = []
    for matter in pilot.MATTERS:
        issues = []
        for issue in matter["issues"]:
            key = f"{matter['id']}/{issue['id']}"
            entries = stored.get(key, [])
            entries = entries if isinstance(entries, list) else []
            entries = [e for e in entries if isinstance(e, dict)]
            issues.append({
                "id": issue["id"],
                "query": issue["query"],
                "required": [
                    {"label": need["label"],
                     "passages": [list(p) for p in need["passages"]]}
                    for need in issue["required"]
                ],
                "key_verdict": issue["key_verdict"],
                "key_gaps": list(issue["key_gaps"]),
                "state": (entries[-1].get("decision") or "pending")
                if entries else "pending",
                "history": entries,
            })
        matters.append({
            "id": matter["id"], "title": matter["title"], "issues": issues})
    return {
        "pilot_version": pilot.PILOT_VERSION,
        "key_status": pilot.KEY_STATUS,
        "matters": matters,
    }


def record_review(
    root: Path,
    matter_id: str,
    issue_id: str,
    decision: str,
    reviewer: str,
    notes: str,
    pilot_version: str,
) -> dict:
    """Append one review entry. Refuses unknown ids and stale versions."""
    if decision not in ("approved", "changes_requested"):
        raise RuntimeError("decision must be approved or changes_requested")
    if not (reviewer or "").strip():
        raise RuntimeError("reviewer name is required")
    if (pilot_version or "").strip() != pilot.PILOT_VERSION:
        raise RuntimeError(
            f"stale pilot version {pilot_version!r}: current is "
            f"{pilot.PILOT_VERSION}; re-read the keys before signing"
        )
    _find(matter_id, issue_id)
    stored = _read_reviews(root)
    key = f"{matter_id}/{issue_id}"
    entries = stored.get(key, [])
    entries = entries if isinstance(entries, list) else []
    entry = {
        "decision": decision,
        "reviewer": reviewer.strip(),
        "notes": (notes or "").strip(),
        "pilot_version": pilot.PILOT_VERSION,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    entries.append(entry)
    stored[key] = entries
    path = _reviews_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(stored, indent=1) + "\n")
    return {"matter": matter_id, "issue": issue_id, **entry}


def agreement_report(root: Path) -> dict:
    """Pairwise inter-rater agreement over shared reviewed issues.

    Latest decision per (reviewer, issue); approved maps to pass,
    changes_requested to fail. Pairs with no shared issue are
    listed without kappa. Report only: 0.6 is the documented
    bar, policy decides what it gates.
    """
    stored = _read_reviews(root)
    by_reviewer: dict[str, dict[str, str]] = {}
    for key, entries in stored.items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            reviewer = str(entry.get("reviewer", "") or "").strip()
            decision = entry.get("decision", "")
            if not reviewer or decision not in (
                    "approved", "changes_requested"):
                continue
            by_reviewer.setdefault(reviewer, {})[str(key)] = (
                "pass" if decision == "approved" else "fail"
            )
    reviewers = sorted(by_reviewer)
    pairs = []
    for left in range(len(reviewers)):
        for right in range(left + 1, len(reviewers)):
            first, second = reviewers[left], reviewers[right]
            shared = sorted(
                set(by_reviewer[first]) & set(by_reviewer[second]))
            pair: dict = {
                "reviewers": [first, second],
                "shared": len(shared),
                "kappa": None,
                "agreement": None,
                "disagreements": [],
            }
            if shared:
                a = [by_reviewer[first][k] for k in shared]
                b = [by_reviewer[second][k] for k in shared]
                pair["kappa"] = round(cohen_kappa(a, b), 4)
                pair["agreement"] = round(
                    sum(1 for x, y in zip(a, b) if x == y) / len(a), 4)
                pair["disagreements"] = [
                    k for k, x, y in zip(shared, a, b) if x != y
                ]
            pairs.append(pair)
    return {
        "pilot_version": pilot.PILOT_VERSION,
        "reviewers": reviewers,
        "pairs": pairs,
        "bar": 0.6,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="draft keys + review state as JSON")
    sub.add_parser(
        "agreement", help="pairwise inter-rater kappa as JSON")
    review = sub.add_parser("review", help="record one sign-off")
    review.add_argument("matter")
    review.add_argument("issue")
    group = review.add_mutually_exclusive_group(required=True)
    group.add_argument("--approve", action="store_true")
    group.add_argument("--request-changes", action="store_true")
    review.add_argument("--reviewer", default="")
    review.add_argument("--notes", default="")
    review.add_argument("--version", default="")
    args = parser.parse_args(argv)
    try:
        root = _resolve_root()
        if args.command == "list":
            print(json.dumps(list_keys(root), indent=1))
        elif args.command == "agreement":
            print(json.dumps(agreement_report(root), indent=1))
        else:
            print(json.dumps(record_review(
                root, args.matter, args.issue,
                "approved" if args.approve else "changes_requested",
                args.reviewer, args.notes, args.version,
            ), indent=1))
    except RuntimeError as error:
        print(f"pilot: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
