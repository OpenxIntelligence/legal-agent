#!/usr/bin/env python3
"""Per-issue evidence needs + live status for one run (read-only).

    ./helper needs RUN_ID

Prints JSON: every checklist item, its frozen needs (if any),
and the current sufficiency verdict over recorded facts.
Powers the audit view's Evidence needs section.

JSON to stdout; errors to stderr with a non-zero exit.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_ROOT))

from agent_kit.case_record import CaseError, CaseRecord  # noqa: E402


def _resolve_root() -> Path:
    override = (os.environ.get("LEGAL_AGENT_ROOT") or "").strip()
    if override:
        root = Path(override)
        if not root.is_dir():
            raise RuntimeError(
                f"LEGAL_AGENT_ROOT is not a directory: {override}")
        return root
    return CODE_ROOT


def needs_for(root: Path, run_id: str) -> dict:
    """Checklist items with needs state. Unknown run refused."""
    if not (root / "workspace/case_records" / f"{run_id}.json").is_file():
        raise RuntimeError(f"no such run: {run_id}")
    case = CaseRecord(root, run_id)
    checklist = case.data.get("checklist") or {}
    items = []
    for item_id, entry in checklist.items():
        row: dict = {
            "item": item_id,
            "question": (entry or {}).get("question", "")
            if isinstance(entry, dict) else "",
            "state": "undeclared",
            "required": [],
            "missing": [],
            "adverse_covered": True,
            "version_confusion": [],
            "abstain": False,
        }
        try:
            status = case.evidence_status(item_id)
        except CaseError:
            items.append(row)
            continue
        row["state"] = (
            "abstain"
            if status.get("abstain")
            else "sufficient"
            if status["sufficient"] and status["adverse_covered"]
            else "insufficient"
        )
        stored = (case.data.get("evidence_needs") or {}).get(item_id, {})
        if isinstance(stored, dict) and isinstance(
                stored.get("required"), list):
            row["required"] = [
                {"label": n.get("label", "?"),
                 "passages": n.get("passages", [])}
                for n in stored["required"] if isinstance(n, dict)
            ]
        row["missing"] = status.get("missing", [])
        row["adverse_covered"] = status.get("adverse_covered", True)
        row["version_confusion"] = status.get("version_confusion", [])
        row["abstain"] = bool(status.get("abstain"))
        items.append(row)
    return {"run_id": run_id, "items": items}


def _parse_required(spec: str) -> dict:
    """One 'label @ DOC:1, DOC2:2' line; passages optional."""
    label, _, pairs = spec.partition("@")
    label = label.strip()
    if not label:
        raise CaseError("a requirement needs its label")
    out = []
    for chunk in (pairs or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if ":" not in chunk:
            raise CaseError(
                f"{chunk!r} must look like DOC:1")
        doc, _, page = chunk.partition(":")
        out.append([doc.strip(), page.strip()])
    return {"label": label, "passages": out}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id")
    parser.add_argument("action", nargs="?", default="show",
                        choices=("show", "set"))
    parser.add_argument("--item", default="")
    parser.add_argument("--required", action="append", default=[])
    args = parser.parse_args(argv)
    try:
        root = _resolve_root()
        if args.action == "set":
            if not args.item.strip():
                raise CaseError("set needs --item")
            if not args.required:
                raise CaseError("set needs --required lines")
            case = CaseRecord(root, args.run_id)
            needs = case.set_evidence_needs(
                args.item.strip(),
                [_parse_required(spec) for spec in args.required])
            print(json.dumps({"item": needs["item"],
                              "required": needs["required"]}, indent=1))
        else:
            print(json.dumps(
                needs_for(root, args.run_id), indent=1))
    except (RuntimeError, CaseError, FileNotFoundError) as error:
        print(f"needs: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
