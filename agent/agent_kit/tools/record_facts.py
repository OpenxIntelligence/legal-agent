"""record_facts: record up to four pieces of evidence in one call.

Each entry is validated exactly as record_fact would validate it, in
order; one bad entry fails only itself. Prefer this over repeated
record_fact calls when several passages are ready. Recording never
settles an item; only a passed assessment marks it answered.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..case_record import CLAIM_KINDS, MAX_FACTS_PER_BATCH

if TYPE_CHECKING:
    from . import Context

ENTRY_SCHEMA = {
    "type": "object",
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
}

DEFINITION = {
    "type": "function",
    "name": "record_facts",
    "description": (
        f"Collect evidence for checklist items, up to {MAX_FACTS_PER_BATCH} "
        "passages in one call. Each entry is checked exactly as record_fact "
        "checks one passage — quote exactly on that page of an analyzed, "
        "non-confidential document — and one bad entry fails only itself "
        "while the rest still record. Say how each quote bears on its item "
        "(stance): supports, opposes, or context. Recording never settles "
        "an item; only a passed assessment marks it answered. The result "
        "gives each entry's fact id or error, and which items are open."
    ),
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "facts": {
                "type": "array",
                "description": (
                    "one to four fact proposals, each like record_fact's arguments"
                ),
                "items": ENTRY_SCHEMA,
            },
        },
        "required": ["facts"],
    },
}


def run(ctx: Context, args: dict) -> dict:
    return {"recorded": ctx.case.record_facts(args.get("facts", []))}
