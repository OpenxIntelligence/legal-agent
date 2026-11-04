"""record_fact: record one piece of evidence for one checklist item in the case record.

Refused unless the quote is exactly on that page of an analyzed, non-confidential
document. Recording never settles the item; only a passed assessment marks it
answered. The result says which items are still open.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..case_record import CLAIM_KINDS

if TYPE_CHECKING:
    from . import Context

DEFINITION = {
    "type": "function",
    "name": "record_fact",
    "description": "Collect evidence for one checklist item in the case record. Refused unless the quote appears "
    "exactly on that page of an analyzed, non-confidential document. Say how the quote bears on the item "
    "(stance): supports (it shows the item true), opposes (it cuts against the item), or context (background). "
    "Say what the claim is (kind): a fact, a legal rule, an applicability conclusion, or an inference. "
    "Recording never settles the item; only a passed assessment marks it answered. "
    "The result says which items are still open.",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "item": {
                "type": "string",
                "description": "id of an item in your checklist (set_checklist)",
            },
            "document_id": {"type": "string"},
            "page": {"type": "integer"},
            "quote": {"type": "string"},
            "claim": {"type": "string"},
            "stance": {
                "type": "string",
                "enum": ["supports", "opposes", "context"],
                "description": "how the quote bears on the item (default supports)",
            },
            "basis": {
                "type": "string",
                "description": "why this passage matters: an independent observation, "
                "or a repeated statement and where it originates",
            },
            "supersedes": {
                "type": "string",
                "description": "id of a live fact this one corrects, if any",
            },
            "kind": {
                "type": "string",
                "enum": list(CLAIM_KINDS),
                "description": "what the claim is (default fact)",
            },
            "authority": {
                "type": "string",
                "description": "for kind legal_rule: id of the registered "
                "authority stating this rule (register_authority's A-id); "
                "the filing gate resolves this link",
            },
        },
        "required": ["item", "document_id", "page", "quote", "claim"],
    },
}


def run(ctx: Context, args: dict) -> dict:
    from . import page_arg

    fact = ctx.case.record_fact(
        str(args.get("item", "")),
        str(args.get("document_id", "")),
        page_arg("record_fact", "page", (args or {}).get("page")),
        str(args.get("quote", "")),
        str(args.get("claim", "")),
        str(args.get("stance", "supports")),
        str(args.get("basis", "") or ""),
        str(args.get("supersedes", "") or ""),
        str(args.get("kind", "fact")),
        str(args.get("authority", "") or ""),
    )
    return {"recorded": fact}
