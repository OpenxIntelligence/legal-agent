"""request_evidence (verification on): escalate one checklist item to a person.

verification/verify_evidence_request.py decides whether the request is allowed.
An allowed request opens a request in the case record and stops the run (needs_input)
until a person releases evidence or replies.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .. import document_text

if TYPE_CHECKING:
    from . import Context

DEFINITION = {
    "type": "function",
    "name": "request_evidence",
    "description": "Escalate to the reviewer when one checklist item cannot be answered from the visible documents. "
    "Name the item, the purpose, the material target, and exactly what evidence is needed. An answered item needs "
    "nothing further unless you need independent corroboration, must resolve a contradiction, or have a grounded "
    "new discrepancy to check. The run pauses until the reviewer responds. You cannot release documents.",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "item": {
                "type": "string",
                "description": "id of an item in your checklist (set_checklist)",
            },
            "need": {"type": "string", "description": "exactly what evidence is needed"},
            "purpose": {
                "type": "string",
                "description": "why: initial, follow_up, corroboration, contradiction, "
                "suspected_discrepancy, missing_primary, or replacement",
            },
            "target": {
                "type": "string",
                "description": "the material target: document, attachment, page, version, "
                "or date range this request is about",
            },
            "rationale": {
                "type": "string",
                "description": "why this request is needed and what uncertainty it could "
                "resolve; name the evidence behind a suspected discrepancy",
            },
            "read_exception": {
                "type": "string",
                "description": "when unexamined documents cannot matter to this request, "
                "explain why; the explanation is recorded",
            },
        },
        "required": ["item", "need"],
    },
}


def run(ctx: Context, args: dict) -> dict:
    from verification import verify_evidence_request

    item, need = str(args.get("item", "")), str(args.get("need", ""))
    detail = {
        "item": item,
        "need": need,
        "purpose": args.get("purpose"),
        "target": str(args.get("target", "") or ""),
        "rationale": str(args.get("rationale", "") or ""),
        "read_exception": str(args.get("read_exception", "") or ""),
    }
    check = verify_evidence_request.run_verification(ctx, detail)
    if not check["passed"]:
        return {"error": check["feedback"]}
    return escalate(
        ctx,
        item,
        need,
        "missing_evidence",
        purpose=args.get("purpose"),
        target=detail["target"],
        rationale=detail["rationale"],
        read_exception=detail["read_exception"],
    )


def escalate(
    ctx: Context,
    item: str,
    need: str,
    rule: str,
    purpose: str | None = None,
    target: str = "",
    rationale: str = "",
    read_exception: str = "",
) -> dict:
    """Open a request to a person for one checklist item; the run stops (needs_input)."""
    from verification import verify_evidence_request

    visible = [d["id"] for d in document_text.list_documents(ctx.root)]
    prior = [r for r in ctx.case.data["requests"] if r.get("item") == item]
    resolved = purpose or verify_evidence_request.purpose_of(need)
    if not prior and resolved == "follow_up":
        resolved = "initial"
    request = ctx.case.open_request(
        item,
        need,
        rule,
        visible,
        purpose=resolved,
        target=target,
        rationale=rationale,
        read_exception=read_exception,
    )
    return {
        "escalated": True,
        "request": request["id"],
        "item": item,
        "need": need,
        "status": "needs_input",
    }
