#!/usr/bin/env python3
"""The reviewer's side of the case record (a person, not the agent).

    ./helper review RUN_ID --approve [--notes "..."] [--qualify "..." ...]
    ./helper review RUN_ID --reject --notes "what to fix" [--comment "item: ..."...]
    ./helper review RUN_ID --answer R1 --notes "reply to the evidence request"

Then resume the same run: ./helper run --resume RUN_ID
"""

from __future__ import annotations

import argparse
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

from agent_kit.case_record import CaseError, CaseRecord  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("run_id")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--approve", action="store_true")
    group.add_argument("--reject", action="store_true")
    group.add_argument("--answer", metavar="REQUEST_ID")
    parser.add_argument("--notes", default="")
    parser.add_argument(
        "--qualify",
        action="append",
        default=[],
        metavar="TEXT",
        help="a qualification kept with the review (repeatable)",
    )
    parser.add_argument(
        "--comment",
        action="append",
        default=[],
        metavar="TARGET:TEXT",
        dest="comments",
        help="a targeted review comment as target:text (repeatable)",
    )
    args = parser.parse_args()
    if not (ROOT / "workspace/case_records" / f"{args.run_id}.json").is_file():
        parser.error(f"no case record for {args.run_id}")
    case = CaseRecord(ROOT, args.run_id)
    try:
        if args.answer:
            if args.qualify or args.comments:
                parser.error("--qualify and --comment need --approve or --reject")
            result = case.answer_request(
                args.answer, args.notes or "Answered by reviewer."
            )
        else:
            if args.reject and not args.notes:
                parser.error("--reject needs --notes saying what to fix")
            parsed = []
            for raw in args.comments:
                target, sep, text = raw.partition(":")
                parsed.append(
                    {
                        "target": target.strip() or "general",
                        "text": (text if sep else raw).strip(),
                    }
                )
            result = case.review_memo(
                args.approve, args.notes, qualifications=args.qualify, comments=parsed
            )
    except CaseError as error:
        parser.error(str(error))
    print(
        json.dumps(
            {
                k: result.get(k)
                for k in ("id", "status", "review", "response")
                if k in result
            },
            indent=2,
        )
    )
    print(f"Now resume the same run: ./helper run --resume {args.run_id}")


if __name__ == "__main__":
    main()
