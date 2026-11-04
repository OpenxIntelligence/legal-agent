"""register_authority: register one relied-upon legal proposition.

The document needs noted provenance first. The registry adds the
proposition, its authority level (binding, persuasive,
secondary), effective date, verification status, and subsequent
treatment — or records the limitation. Legal-rule facts citing
documents with no registered authority fail the filing gate.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..reasoning import AUTHORITY_LEVELS, AUTHORITY_VERIFICATION

if TYPE_CHECKING:
    from . import Context

DEFINITION = {
    "type": "function",
    "name": "register_authority",
    "description": "Register one legal proposition the analysis relies on: its "
    "source document (provenance first), authority level, effective date, "
    "verification status, and subsequent treatment. Required for every "
    "document cited for a legal rule.",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "proposition": {
                "type": "string",
                "description": "the legal proposition relied on",
            },
            "document_id": {"type": "string"},
            "citation": {"type": "string"},
            "version": {"type": "string"},
            "effective_date": {
                "type": "string",
                "description": "law's date as YYYY-MM-DD, not retrieval date",
            },
            "level": {
                "type": "string",
                "enum": list(AUTHORITY_LEVELS),
            },
            "verification": {
                "type": "string",
                "enum": list(AUTHORITY_VERIFICATION),
            },
            "treatment_checked": {
                "type": "boolean",
                "description": "whether subsequent treatment was checked",
            },
            "treatment": {
                "type": "string",
                "description": "subsequent treatment, or that it was not checked",
            },
            "assessment": {
                "type": "string",
                "description": "AA-id of an existing supporting assessment; "
                "required when verification is 'verified' (otherwise "
                "register provisional and assess_authority next)",
            },
        },
        "required": [
            "proposition",
            "document_id",
            "citation",
            "version",
            "effective_date",
            "level",
            "verification",
            "treatment_checked",
            "treatment",
        ],
    },
}


def run(ctx: Context, args: dict) -> dict:
    entry = ctx.case.register_authority(
        proposition=str(args.get("proposition", "")),
        document_id=str(args.get("document_id", "")),
        citation=str(args.get("citation", "")),
        version=str(args.get("version", "")),
        effective_date=str(args.get("effective_date", "")),
        level=str(args.get("level", "")),
        verification=str(args.get("verification", "")),
        treatment_checked=bool(args.get("treatment_checked", False)),
        treatment=str(args.get("treatment", "")),
        assessment=str(args.get("assessment", "")),
    )
    return {
        "registered": entry["id"],
        "level": entry["level"],
        "verification": entry["verification"],
    }
