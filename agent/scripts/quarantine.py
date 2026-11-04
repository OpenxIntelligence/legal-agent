#!/usr/bin/env python3
"""The reviewer's side of redaction quarantine (a person, not the agent).

    ./helper quarantine --list
    ./helper quarantine DOC-ID --release --notes "inspected: ..."

A render-confirmed bad redaction seals the document before any
transcript exists. Releasing re-opens extraction, search, and
analysis for that document; the release is recorded with its
note and never erased. Inspect the actual PDF before releasing:
the scanner proves a region LOOKS hidden, never that the
redaction is secure.
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

from agent_kit import redactions  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("document_id", nargs="?")
    parser.add_argument("--list", action="store_true", dest="list_all")
    parser.add_argument("--release", action="store_true")
    parser.add_argument("--notes", default="")
    args = parser.parse_args()
    if args.list_all or not args.document_id:
        store = redactions.quarantine_list(ROOT)
        active = {
            doc: entry
            for doc, entry in store.items()
            if isinstance(entry, dict) and "released" not in entry
        }
        print(json.dumps({"quarantined": active, "history": store}, indent=2))
        return
    if not args.release:
        parser.error("say what to do: --release --notes ... (or --list)")
    if not args.notes.strip():
        parser.error("--release needs --notes describing the inspection")
    try:
        entry = redactions.release(
            ROOT, args.document_id, note=args.notes.strip()
        )
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps({args.document_id: entry}, indent=2))


if __name__ == "__main__":
    main()
