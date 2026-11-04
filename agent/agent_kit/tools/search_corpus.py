"""search_corpus: keyword search over transcribed pages.

Finds passages without reading whole documents: results group
document → page → passage with per-page match counts. Only
analyzed documents are searchable; untranscribed ones are listed
as unindexed and flagged confidential ones as restricted, so a
search that finds nothing never implies the record holds nothing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .. import document_text, sources
from ..case_record import CaseError

if TYPE_CHECKING:
    from . import Context

DEFINITION = {
    "type": "function",
    "name": "search_corpus",
    "description": "Search transcribed document pages for a word or phrase, returning "
    "matching passages grouped by document and page. Use it to locate exact "
    "statements before reading whole documents. Only analyzed documents are "
    "searched; untranscribed and confidential documents are listed, not read.",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "query": {
                "type": "string",
                "description": "word or phrase to find (at least two characters)",
            },
            "document_ids": {
                "type": ["array", "null"],
                "items": {"type": "string"},
                "description": "search only these inbox documents (default: all)",
            },
        },
        "required": ["query"],
    },
}


def run(ctx: Context, args: dict) -> dict:
    case = ctx.case
    query = (args or {}).get("query", "")
    if not isinstance(query, str):
        raise CaseError("search_corpus needs a text query")
    document_ids = (args or {}).get("document_ids")
    if document_ids is not None:
        if (not isinstance(document_ids, list)
                or not all(isinstance(d, str) for d in document_ids)):
            raise CaseError(
                "search_corpus document_ids must be a list of strings")
    flagged = {
        flag.get("document_id")
        for flag in case.data.get("flags", [])
        if isinstance(flag, dict) and flag.get("kind") == "privileged"
    }
    noted = case.data.get("sources") or {}
    meta = {}
    if isinstance(noted, dict):
        for document_id, entry in noted.items():
            if not isinstance(entry, dict):
                continue
            repo = sources.REPOS.get(str(entry.get("repo", "")))
            if repo is not None:
                meta[document_id] = {"jurisdiction": repo.get("region")}
    try:
        return document_text.search_corpus(
            ctx.root,
            query,
            list(document_ids) if document_ids else None,
            flagged=flagged,
            meta=meta,
        )
    except document_text.DocumentError as error:
        raise CaseError(str(error)) from None
