#!/usr/bin/env python3
"""C-0 collaboration backbone: drafts, threads, directives, context, edges.

    ./helper collab RUN show
    ./helper collab RUN draft-save --title T --body TEXT [--author N] [--draft DR1] [--base N]
    ./helper collab RUN draft-send --draft DR1 [--author N]
    ./helper collab RUN draft-verify --draft DR1 --verdict passed|challenged [--rationale TEXT]
    ./helper collab RUN thread-open --object draft:DR1 --body TEXT [--challenge] [--author N]
                                 [--version V] [--anchor-start N --anchor-end M] [--quoted TEXT]
    ./helper collab RUN thread-reply --thread TH1 --body TEXT [--challenge] [--author N]
    ./helper collab RUN thread-edit --thread TH1 --message M1 --body TEXT [--author N]
    ./helper collab RUN thread-resolve --thread TH1 [--resolution TEXT] [--author N]
    ./helper collab RUN thread-reopen --thread TH1 --reason TEXT [--author N]
    ./helper collab RUN directive-submit --kind KIND --body TEXT [--base type:id ...] [--from-thread TH1] [--author N]
    ./helper collab RUN directive-ack --directive DIR1 [--agent NAME --role ROLE]
    ./helper collab RUN directive-apply --directive DIR1 [--invalidate type:id ...]
    ./helper collab RUN directive-refuse --directive DIR1 --rationale TEXT
    ./helper collab RUN directive-supersede --directive DIR1 --by DIR2
    ./helper collab RUN context-add --source SRC --body TEXT | --file REF [--author N]
    ./helper collab RUN context-admit --context CI1 --author NAME
    ./helper collab RUN context-reject --context CI1 --reason TEXT [--author N]
    ./helper collab RUN edge-add --src type:id --dst type:id --relation REL [--author N]

Authors default to human "console" (associate). --agent NAME --role ROLE
names an agent participant; --user-role client|associate|attorney|admin
names a human's powers (admitting quarantined context needs attorney
or admin). Data follows LEGAL_AGENT_ROOT. JSON to stdout; errors to
stderr, non-zero.
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


def _case(root: Path, run_id: str) -> CaseRecord:
    if "/" in run_id or ".." in run_id:
        raise CaseError(f"unknown run {run_id!r}")
    return CaseRecord(root, run_id)


def _author(args) -> dict:
    if args.agent:
        return {"type": "agent", "id": args.agent, "agent_role": args.role or ""}
    return {
        "type": "human",
        "id": args.author or "console",
        "role": args.user_role or "associate",
    }


def _ref(text: str) -> dict:
    kind, _, rest = (text or "").partition(":")
    if not kind or not rest:
        raise CaseError(f"refs must look like draft:DR1, got {text!r}")
    return {"type": kind, "id": rest}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_id")
    parser.add_argument(
        "op",
        choices=(
            "show", "draft-save", "draft-send", "draft-get", "draft-verify",
            "thread-open", "thread-reply", "thread-edit",
            "thread-resolve", "thread-reopen",
            "directive-submit", "directive-ack", "directive-apply",
            "directive-refuse", "directive-supersede",
            "context-add", "context-admit", "context-reject",
            "edge-add",
        ),
    )
    parser.add_argument("--title", default="")
    parser.add_argument("--body", default="")
    parser.add_argument("--author", default="")
    parser.add_argument("--user-role", default="")
    parser.add_argument("--agent", default="")
    parser.add_argument("--role", default="")
    parser.add_argument("--draft", default="")
    parser.add_argument("--base", default="")
    parser.add_argument("--object", default="")
    parser.add_argument("--version", default="")
    parser.add_argument("--anchor-start", default="")
    parser.add_argument("--anchor-end", default="")
    parser.add_argument("--quoted", default="")
    parser.add_argument("--anchor-context", default="")
    parser.add_argument("--message", default="")
    parser.add_argument("--challenge", action="store_true")
    parser.add_argument("--thread", default="")
    parser.add_argument("--resolution", default="")
    parser.add_argument("--reason", default="")
    parser.add_argument("--kind", default="")
    parser.add_argument("--verdict", default="")
    parser.add_argument("--base-ref", dest="base_refs", action="append", default=[])
    parser.add_argument("--from-thread", default="")
    parser.add_argument("--directive", default="")
    parser.add_argument("--invalidate", action="append", default=[])
    parser.add_argument("--rationale", default="")
    parser.add_argument("--by", default="")
    parser.add_argument("--source", default="")
    parser.add_argument("--file", default="")
    parser.add_argument("--context", default="")
    parser.add_argument("--src", default="")
    parser.add_argument("--dst", default="")
    parser.add_argument("--relation", default="")
    args = parser.parse_args(argv)
    try:
        case = _case(_resolve_root(), args.run_id)
        who = _author(args)
        if args.op == "show":
            print(json.dumps(case.collab_summary(), indent=1))
        elif args.op == "draft-save":
            base = int(args.base) if args.base.strip() else None
            draft = case.composer_save(
                args.title, args.body, who,
                draft_id=args.draft, base_version=base)
            print(json.dumps(
                {"id": draft["id"], "state": draft["state"],
                 "version": draft["version"]}, indent=1))
        elif args.op == "draft-send":
            draft = case.composer_send(args.draft, who)
            print(json.dumps({"id": draft["id"], "state": draft["state"]}, indent=1))
        elif args.op == "draft-get":
            print(json.dumps(case.composer_get(args.draft), indent=1))
        elif args.op == "draft-verify":
            draft = case.draft_verify(
                args.draft, args.verdict, who, args.rationale)
            print(json.dumps(
                {"id": draft["id"],
                 "verification_status": draft["verification_status"]},
                indent=1))
        elif args.op == "thread-open":
            ref = _ref(args.object)
            anchor = None
            if (args.anchor_start.strip() or args.anchor_end.strip()
                    or args.quoted or args.anchor_context):
                anchor = {
                    "start": int(args.anchor_start.strip() or 0),
                    "end": int(args.anchor_end.strip() or 0),
                    "quoted": args.quoted,
                    "context": args.anchor_context,
                }
            thread = case.thread_open(
                ref["type"], ref["id"], args.body, who,
                version_id=args.version, anchor=anchor,
                challenge=args.challenge)
            print(json.dumps(
                {"id": thread["id"], "state": thread["state"]}, indent=1))
        elif args.op == "thread-reply":
            thread = case.thread_reply(
                args.thread, args.body, who, challenge=args.challenge)
            print(json.dumps(
                {"id": thread["id"], "messages": len(thread["messages"])},
                indent=1))
        elif args.op == "thread-edit":
            thread = case.thread_edit_message(
                args.thread, args.message, args.body, who)
            print(json.dumps(
                {"id": thread["id"], "messages": len(thread["messages"])},
                indent=1))
        elif args.op == "thread-resolve":
            thread = case.thread_resolve(args.thread, who, args.resolution)
            print(json.dumps(
                {"id": thread["id"], "state": thread["state"]}, indent=1))
        elif args.op == "thread-reopen":
            thread = case.thread_reopen(args.thread, who, args.reason)
            print(json.dumps(
                {"id": thread["id"], "state": thread["state"]}, indent=1))
        elif args.op == "directive-submit":
            directive = case.directive_submit(
                args.kind, args.body, who,
                base_refs=[_ref(r) for r in args.base_refs],
                from_thread=args.from_thread)
            print(json.dumps(
                {"id": directive["id"], "state": directive["state"]}, indent=1))
        elif args.op == "directive-ack":
            directive = case.directive_ack(args.directive, who)
            print(json.dumps(
                {"id": directive["id"], "state": directive["state"]}, indent=1))
        elif args.op == "directive-apply":
            directive = case.directive_apply(
                args.directive, who,
                invalidation=[_ref(r) for r in args.invalidate])
            print(json.dumps(
                {"id": directive["id"], "state": directive["state"]}, indent=1))
        elif args.op == "directive-refuse":
            directive = case.directive_refuse(
                args.directive, who, args.rationale)
            print(json.dumps(
                {"id": directive["id"], "state": directive["state"]}, indent=1))
        elif args.op == "directive-supersede":
            directive = case.directive_supersede(
                args.directive, who, args.by)
            print(json.dumps(
                {"id": directive["id"], "state": directive["state"]}, indent=1))
        elif args.op == "context-add":
            item = case.context_add(args.source, who, args.body, args.file)
            print(json.dumps(
                {"id": item["id"], "state": item["state"]}, indent=1))
        elif args.op == "context-admit":
            item = case.context_admit(args.context, who)
            print(json.dumps(
                {"id": item["id"], "state": item["state"]}, indent=1))
        elif args.op == "context-reject":
            item = case.context_reject(args.context, who, args.reason)
            print(json.dumps(
                {"id": item["id"], "state": item["state"]}, indent=1))
        elif args.op == "edge-add":
            src, dst = _ref(args.src), _ref(args.dst)
            edge = case.edge_add(
                src["type"], src["id"], dst["type"], dst["id"],
                args.relation, who)
            print(json.dumps({"id": edge["id"]}, indent=1))
    except (CaseError, RuntimeError, OSError, ValueError) as error:
        print(f"collab: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
