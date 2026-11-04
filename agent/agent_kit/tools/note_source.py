"""note_source: record where a supplied authority document came from.

The document must be analyzed first. The repository must be registered,
with a citation and the source version. Provenance is model-asserted.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..sources import REPOS

if TYPE_CHECKING:
    from . import Context

DEFINITION = {
    "type": "function",
    "name": "note_source",
    "description": "Record where a supplied authority document came from: the "
    "registered repository, its citation, and the source version. The document "
    "must be analyzed first.",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "document_id": {"type": "string"},
            "repo": {
                "type": "string",
                "enum": sorted(REPOS),
                "description": "registered repository the text was supplied from",
            },
            "citation": {"type": "string"},
            "version": {
                "type": "string",
                "description": "source version or date (today's text may not govern)",
            },
            "language": {
                "type": "string",
                "description": "required for non-English-original sources",
            },
        },
        "required": ["document_id", "repo", "citation", "version"],
    },
}


def run(ctx: Context, args: dict) -> dict:
    noted = ctx.case.note_source(
        str(args.get("document_id", "")),
        str(args.get("repo", "")),
        str(args.get("citation", "")),
        str(args.get("version", "")),
        str(args.get("language", "") or ""),
    )
    return {"noted": noted}
