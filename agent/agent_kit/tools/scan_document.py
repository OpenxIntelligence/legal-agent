"""scan_document: scan analyzed text for injection and override markers.

Inbox documents are untrusted data. The scan reports narrow,
high-precision markers (instruction overrides, role-play shifts,
exfiltration probes) with page and span; it never blocks. The
document must be analyzed first. Obfuscation and paraphrase can
evade any pattern set — recall limits are measured, not assumed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .. import document_text
from ..case_record import CaseError
from ..reasoning import scan_text

if TYPE_CHECKING:
    from . import Context

DEFINITION = {
    "type": "function",
    "name": "scan_document",
    "description": "Scan an analyzed document's text for prompt-injection and "
    "instruction-override markers, reported per page with spans. Use on "
    "untrusted documents before relying on them. Reports only.",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "document_id": {"type": "string"},
        },
        "required": ["document_id"],
    },
}


def run(ctx: Context, args: dict) -> dict:
    doc_id = str(args.get("document_id", ""))
    try:
        transcript = document_text.cached_text(ctx.root, doc_id)
    except document_text.DocumentError as error:
        raise CaseError(str(error)) from None
    if transcript is None:
        raise CaseError(
            f"{doc_id} has not been analyzed yet; call analyze_document first"
        )
    findings = []
    for page in transcript.get("pages", []) or []:
        if not isinstance(page, dict):
            continue
        for hit in scan_text(page.get("text", "")):
            findings.append({"page": page.get("page"), **hit})
    return {"scanned": doc_id, "findings": findings}
