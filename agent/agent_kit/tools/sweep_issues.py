"""sweep_issues: read documents for several issues concurrently.

One bounded reader worker per issue (at most MAX_WORKERS), each with
a lease, a token reservation, and pinned input versions. Workers
return verified-quote evidence proposals; the coordinator validates
quotes, merges deterministically, records worker attempts, and
settles usage. Nothing is recorded as fact: review the proposals and
record what matters with record_facts.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .. import settings
from ..pool import SweepPool

if TYPE_CHECKING:
    from . import Context

ASSIGNMENT = {
    "type": "object",
    "properties": {
        "issue": {
            "type": "string",
            "description": "id of an item in your checklist (set_checklist)",
        },
        "document_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": "up to 4 inbox documents for this issue's reader",
        },
    },
    "required": ["issue", "document_ids"],
}

DEFINITION = {
    "type": "function",
    "name": "sweep_issues",
    "description": "Read documents for up to 3 issues at once, one bounded reader "
    "per issue. Each reader returns verified-quote evidence proposals for its "
    "issue; nothing is recorded as fact. Review the proposals and record what "
    "matters with record_facts. Whole-call admission: bad assignments or an "
    "unaffordable token budget refuse the call before anything is spent.",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {"sweeps": {"type": "array", "items": ASSIGNMENT}},
        "required": ["sweeps"],
    },
}


def run(ctx: Context, args: dict) -> dict:
    pool = SweepPool(ctx.case, ctx.client, max_workers=settings.max_workers())
    return pool.sweep(args.get("sweeps", []))
