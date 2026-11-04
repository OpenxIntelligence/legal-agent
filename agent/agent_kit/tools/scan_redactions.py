"""scan_redactions: scan an inbox PDF for rectangles covering live text.

A black rectangle over live text looks redacted but copies out —
and our transcription reads the content layer, so covered text
can leak into transcripts, facts, and memos. The scan reports
page, box, and covered-character count per finding; it never
returns the covered text. Findings record a reviewer flag; the
document stays usable. Reports only, never blocks.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .. import document_text, redactions
from ..case_record import CaseError

if TYPE_CHECKING:
    from . import Context

DEFINITION = {
    "type": "function",
    "name": "scan_redactions",
    "description": "Scan an inbox PDF for bad redactions: rectangles covering "
    "live text whose content can leak into the transcript. Reports page, box, "
    "and covered-character counts; never returns the covered text. Use on "
    "untrusted PDFs before relying on them. Reports only.",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "document_id": {
                "type": "string",
                "description": "inbox PDF to scan, e.g. DOC-BRIEF",
            },
        },
        "required": ["document_id"],
    },
}


def run(ctx: Context, args: dict) -> dict:
    doc_id = str(args.get("document_id", ""))
    try:
        document = document_text.get_document(ctx.root, doc_id)
    except document_text.DocumentError as error:
        raise CaseError(str(error)) from None
    if not str(document.get("file", "")).lower().endswith(".pdf"):
        raise CaseError(
            f"{doc_id} is not a PDF: only PDFs carry redactable layers"
        )
    try:
        data = (ctx.root / "workspace/inbox" / document["file"]).read_bytes()
    except OSError as error:
        raise CaseError(f"could not read {doc_id}: {error}") from None
    try:
        report = redactions.scan_pdf_bytes(data)
    except redactions.RedactionError as error:
        raise CaseError(str(error)) from None
    findings = report["findings"]
    if findings:
        pages = sorted({f["page"] for f in findings})
        ctx.case.note_redaction_scan(doc_id, len(findings), pages)
    return {"scanned": doc_id, **report}
