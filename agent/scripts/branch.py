#!/usr/bin/env python3
"""Continue a run from its checklist with the verification layer on.

./helper branch RUN_ID      # prints the new run's id; then ./helper run --resume NEW_RUN_ID

The new run keeps the conversation up to the agent's first set_checklist call, runs that
checklist through the checklist check, and stops. The original run is not changed.
"""

from __future__ import annotations

import argparse
import contextlib
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

from agent_kit.client import make_client, MissingKeyError  # noqa: E402
from agent_kit.progress import branch  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("run_id")
    args = parser.parse_args()
    try:
        with contextlib.redirect_stdout(
            sys.stderr
        ):  # stdout carries only the JSON result
            new_id = branch(make_client(), ROOT, args.run_id)
    except (MissingKeyError, RuntimeError, FileNotFoundError) as error:
        sys.exit(str(error))
    print(json.dumps({"run_id": new_id, "branched_from": args.run_id}, indent=2))
    print(f"Continue it with: ./helper run --resume {new_id}", file=sys.stderr)


if __name__ == "__main__":
    main()
