#!/usr/bin/env python3
"""Objection ledger: raise, disprove, restore standing challenges.

    ./helper objections RUN raise --text TEXT [--reporter NAME]
    ./helper objections RUN disprove --ref OBJ1 --counter TEXT [--reporter NAME]
    ./helper objections RUN restore --ref OBJ1 --reason TEXT [--reporter NAME]

Case-level ledger shared by the audit views; the contradiction
panel reads it via panel_export. Data follows LEGAL_AGENT_ROOT.
JSON to stdout; errors to stderr, non-zero.
"""

from __future__ import annotations

import argparse
import json
import os
import re
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


def _case(root: Path, run_id: str) -> CaseRecord:
    if "/" in run_id or ".." in run_id:
        raise CaseError(f"unknown run {run_id!r}")
    return CaseRecord(root, run_id)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id")
    parser.add_argument("op", choices=("raise", "disprove", "restore"))
    parser.add_argument("--text", default="")
    parser.add_argument("--ref", default="")
    parser.add_argument("--counter", default="")
    parser.add_argument("--reason", default="")
    parser.add_argument("--reporter", default="")
    args = parser.parse_args(argv)
    try:
        case = _case(_resolve_root(), args.run_id)
        if args.op in ("disprove", "restore") and re.fullmatch(
                r"OBJ\d+", args.ref or ""):
            known = any(
                isinstance(entry, dict) and entry.get("id") == args.ref
                for entry in case.data.get("objections") or [])
            if not known:
                raise CaseError(f"unknown objection {args.ref!r}")
        if args.op == "raise":
            finding = case.raise_objection(
                args.text, reporter=args.reporter.strip())
        elif args.op == "disprove":
            finding = case.disprove_objection(
                args.ref, args.counter, reporter=args.reporter.strip())
        else:
            finding = case.restore_objection(
                args.ref, args.reason, reporter=args.reporter.strip())
        print(json.dumps({
            "id": finding["id"],
            "status": finding["status"],
            "objection": finding["objection"],
        }, indent=1))
    except (CaseError, RuntimeError, OSError, ValueError) as error:
        print(f"objections: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
