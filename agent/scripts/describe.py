#!/usr/bin/env python3
"""What the agent gets when it starts: the case, its objective, the tools offered, and build
status (JSON). Used by the console.

./helper describe
"""

from __future__ import annotations

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
sys.path.insert(0, str(CODE_ROOT))

from agent_kit import progress, tools  # noqa: E402
from agent_kit.settings import case_dir, verification_enabled  # noqa: E402


def _has_ground_truth(root: Path) -> bool:
    try:
        return (
            importlib.import_module("verification.verify_checklist")
            .reference_path(root)
            .is_file()
        )
    except Exception:  # verify_checklist.py is broken mid-edit: report no ground truth
        return False


def built(path: Path) -> bool:
    return path.is_file() and "NotImplementedError(" not in path.read_text()


def describe(root: Path | None = None) -> dict:
    """The current case, environment, and configuration, as data."""
    root = root or ROOT
    verification = verification_enabled()
    case = case_dir(root)
    objective = (case / "objective.md").read_text()
    title = next(
        (line[2:].strip() for line in objective.splitlines() if line.startswith("# ")),
        case.name,
    )
    inbox = root / "workspace/inbox"
    return {
        "verification": verification,
        "config": progress.config_snapshot(verification),
        "case": str(case),
        "title": title,
        "objective": objective,
        "ground_truth_checklist": _has_ground_truth(root),
        "tools": [
            {
                "name": name,
                "description": tools.DEFINITIONS[name]["description"],
                "parameters": list(
                    tools.DEFINITIONS[name]["parameters"]["properties"]
                ),
            }
            for name in tools.names(verification)
        ],
        "built": {
            path: built(CODE_ROOT / path)
            for path in (
                "legal_agent/loop.py",
                "verification/verify_checklist.py",
                "verification/verify_memo.py",
            )
        },
        "documents": len([p for p in inbox.glob("*") if p.is_file()])
        if inbox.exists()
        else 0,
    }


def main() -> None:
    print(json.dumps(describe(), indent=2))


if __name__ == "__main__":
    main()
