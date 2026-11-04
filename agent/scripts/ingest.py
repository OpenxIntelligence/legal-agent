#!/usr/bin/env python3
"""Human document intake: validated files into the shared inbox.

    ./helper ingest --filename MEMO.txt --text-file /tmp/memo.txt
    ./helper ingest --filename SCAN.pdf --b64-file /tmp/scan.b64

Text transcribes deterministically; PDFs run the redaction
gate and wait for model extraction. Data follows
LEGAL_AGENT_ROOT. JSON to stdout; errors to stderr, non-zero.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE_ROOT))

from agent_kit import ingest  # noqa: E402
from agent_kit.document_text import DocumentError  # noqa: E402


def _resolve_root() -> Path:
    override = (os.environ.get("LEGAL_AGENT_ROOT") or "").strip()
    if override:
        root = Path(override)
        if not root.is_dir():
            raise RuntimeError(
                f"LEGAL_AGENT_ROOT is not a directory: {override}")
        return root
    return CODE_ROOT


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--filename", required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--text-file", default="")
    group.add_argument("--b64-file", default="")
    args = parser.parse_args(argv)
    try:
        root = _resolve_root()
        if args.text_file:
            text = Path(args.text_file).read_text()
            result = ingest.add_document(
                root, filename=args.filename, text=text)
        else:
            blob = Path(args.b64_file).read_text().strip()
            result = ingest.add_document(
                root, filename=args.filename, content_b64=blob)
        print(json.dumps(result, indent=1))
    except (DocumentError, RuntimeError, OSError, ValueError) as error:
        print(f"ingest: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
