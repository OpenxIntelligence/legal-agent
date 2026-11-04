"""analyze_document: read up to 4 inbox documents for one request.

Each document's text is extracted once (document_text) and every quote the model returns
is checked against it: an evidence record is verified only if its quote is on its page.
The documents are read in parallel; a document that fails comes back with no evidence and
the error in its note, and the others still return. A document flagged confidential
cannot be read. Successfully read documents are remembered in the case record (the
escalation rule needs them); failed reads are remembered separately as failures.
"""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, TYPE_CHECKING

import openai
from openai import OpenAI

from .. import document_text
from ..document_text import DocumentError

if TYPE_CHECKING:
    from . import Context

MAX_DOCS_PER_CALL = 4

DEFINITION = {
    "type": "function",
    "name": "analyze_document",
    "description": "Read up to 4 inbox documents (PDF, scan, table, chart, or photo) for one request. Returns "
    "evidence records with page, exact quote, claim, interpretation, ambiguity, and verified (quote found on the page).",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "document_ids": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "maxItems": MAX_DOCS_PER_CALL,
            },
            "request": {"type": "string"},
        },
        "required": ["document_ids", "request"],
    },
}

PROMPT = (
    "You are reviewing case document {doc_id} for this request: {request}\n"
    "The verified page text is below. Every quote must be copied exactly from that text, "
    "on the page you name. Keep the quote (what the document says) separate from your "
    "interpretation (what it means for the request). If the document is irrelevant, return no evidence "
    "and say so in note. If text is illegible or ambiguous, say so in ambiguity rather than guessing.\n"
    'Return only JSON: {{"evidence": [{{"page": 1, "quote": "...", "claim": "...", '
    '"interpretation": "...", "ambiguity": ""}}], "note": "..."}}\n\nVERIFIED PAGE TEXT\n{text}'
)
MAX_ATTEMPTS = 2


def run(ctx: Context, args: dict) -> dict:
    ids = args.get("document_ids") or []
    ids = [ids] if isinstance(ids, str) else [str(d) for d in ids]
    flagged = [d for d in ids if ctx.case.flagged(d)]
    if flagged:
        return {"error": f"confidential documents cannot be read: {', '.join(flagged)}"}
    result = analyze(
        ctx.client, ctx.root, ids, str(args.get("request", "")), run_id=ctx.run_id
    )
    succeeded, failed = [], {}
    for entry in result.get("documents", []):
        doc_id = entry.get("document_id")
        if not doc_id:
            continue
        note = str(entry.get("note", ""))
        if note.startswith("error"):
            failed[doc_id] = {
                "note": note,
                "class": classify_read_error(entry.get("error_kind", "")),
            }
        else:
            succeeded.append(doc_id)
    ctx.case.mark_read(succeeded)
    ctx.case.mark_read_failed(failed)
    return result


def classify_read_error(kind: str) -> str:
    """Map a read failure kind to a recovery class.

    Transient provider problems retry; corrupt documents go straight
    to replacement; anything unrecognized is unknown (safe retry
    policy), never a bypass.
    """
    if kind in ("timeout", "connection", "rate_limit"):
        return "transient"
    if kind in ("extraction",):
        return "corrupt"
    if kind in ("quarantine",):
        return "restricted"
    if kind in ("model_output", "os", "value"):
        return "internal"
    return "unknown"


def analyze(
    client: OpenAI,
    root: Path,
    document_ids: list,
    request: str,
    *,
    run_id: str | None = None,
    purpose_prefix: str = "",
) -> dict:
    """{"documents": [{"document_id", "evidence": [...], "note"}]}, in the order asked.

    purpose_prefix tags the usage ledger entries (e.g. a worker id),
    so concurrent readers stay attributable. Empty keeps the plain
    extract_text / analyze_document purposes.
    """
    if not document_ids:
        raise DocumentError("document_ids is empty")
    if len(document_ids) > MAX_DOCS_PER_CALL:
        raise DocumentError(f"at most {MAX_DOCS_PER_CALL} documents per call")
    document_ids = list(dict.fromkeys(document_ids))
    for document_id in document_ids:
        document_text.get_document(
            root, document_id
        )  # an unknown id fails before any model call

    def read(document_id: str) -> dict:
        try:
            return _analyze_one(
                client, root, document_id, request, run_id,
                purpose_prefix=purpose_prefix,
            )
        except (DocumentError, openai.APIError, OSError, ValueError) as error:
            return {
                "document_id": document_id,
                "evidence": [],
                "note": f"error: {error}",
                "error_kind": _error_kind(error),
            }

    with ThreadPoolExecutor(max_workers=len(document_ids)) as pool:
        return {"documents": list(pool.map(read, document_ids))}


def _error_kind(error: Exception) -> str:
    if isinstance(error, openai.APITimeoutError):
        return "timeout"
    if isinstance(error, openai.APIConnectionError):
        return "connection"
    if isinstance(error, openai.RateLimitError):
        return "rate_limit"
    if isinstance(error, DocumentError):
        if "quarantin" in str(error):
            return "quarantine"
        if "could not extract text" in str(error):
            return "extraction"
        return "model_output"
    if isinstance(error, OSError):
        return "os"
    return "value"


def _analyze_one(
    client: OpenAI,
    root: Path,
    document_id: str,
    request: str,
    run_id: str | None = None,
    *,
    purpose_prefix: str = "",
) -> dict:
    """Extract (once) and analyze one document for one request."""
    ledger = (root, run_id) if run_id else None
    document = document_text.get_document(root, document_id)
    redactions = _redaction_scan(root, document)
    _quarantine_confirmed(root, document_id, redactions)
    text = document_text.extract_text(
        client, root, document_id, run_id=run_id, purpose_prefix=purpose_prefix
    )
    key = hashlib.sha256(f"{document['sha256']}|{request}".encode()).hexdigest()[:24]
    cached = document_text.cache(root) / "analysis" / f"{key}.json"
    if cached.is_file():
        return json.loads(cached.read_text())
    pages_text = "\n\n".join(f"[page {p['page']}]\n{p['text']}" for p in text["pages"])
    error, parsed = "", None
    for _attempt in range(MAX_ATTEMPTS):
        prompt = PROMPT.format(doc_id=document_id, request=request, text=pages_text)
        if error:
            prompt += f"\n\nYour previous output was invalid: {error}"
        content: list[dict[str, Any]] = [{"type": "input_text", "text": prompt}]
        if text.get("file_id"):
            content.append({"type": "input_file", "file_id": text["file_id"]})
        output = document_text.ask(
            client,
            content,
            purpose=f"{purpose_prefix}analyze_document",
            ledger=ledger,
        )
        try:
            parsed = json.loads(output)
            if not isinstance(parsed.get("evidence", []), list):
                raise ValueError("evidence must be a list")
            break
        except (ValueError, AttributeError) as problem:
            error, parsed = str(problem), None
    if parsed is None:
        raise DocumentError(f"could not analyze {document_id}: {error}")
    evidence: list[dict[str, Any]] = []
    for item in parsed.get("evidence", []):
        try:
            page = int(item.get("page"))
        except (TypeError, ValueError):
            continue
        quote = str(item.get("quote", ""))
        evidence.append(
            {
                "document_id": document_id,
                "page": page,
                "quote": quote,
                "claim": str(item.get("claim", "")),
                "interpretation": str(item.get("interpretation", "")),
                "ambiguity": str(item.get("ambiguity", "")),
                "verified": document_text.quote_on_page(
                    quote, document_text.page_text(root, document_id, page) or ""
                ),
            }
        )
    result = {
        "document_id": document_id,
        "evidence": evidence,
        "note": str(parsed.get("note", "")),
    }
    if redactions is not None:
        result["redactions"] = redactions
    document_text.atomic_write(cached, json.dumps(result, indent=1))
    return result


def _quarantine_confirmed(
    root: Path, document_id: str, scan: dict | None
) -> None:
    """Fail a document closed on render-confirmed hidden text.

    The scan runs before any transcript exists, so quarantining
    here keeps covered text out of transcription, prompts, and
    evidence. Heuristic-only findings stay advisory (attached
    to the result below); a failed scan records its error and
    never blocks analysis.
    """
    from .. import redactions as seal

    if seal.confirmed_hidden(scan):
        findings = scan.get("findings") or [] if isinstance(scan, dict) else []
        seal.quarantine(
            root,
            document_id,
            reason="render-confirmed text hidden under an opaque region",
            findings=len(findings),
        )
        raise DocumentError(
            f"{document_id} quarantined: {len(findings)} render-confirmed "
            "hidden-text region(s). Covered text cannot enter the "
            "transcript; a reviewer must release it (./helper quarantine)."
        )


def _redaction_scan(root: Path, document: dict) -> dict | None:
    """Bad-redaction findings for a PDF, else None.

    Transcription reads the content layer, so covered text can
    leak into the transcript; every PDF analysis carries its
    scan so the leak is visible, not silent. Render-confirmed
    findings quarantine the document before extraction (see
    _quarantine_confirmed); heuristic-only findings stay
    advisory, and a failed scan records its error without
    blocking analysis.
    """
    from .. import redactions

    if not str(document.get("file", "")).lower().endswith(".pdf"):
        return None
    try:
        data = (root / "workspace/inbox" / document["file"]).read_bytes()
    except (OSError, KeyError) as error:
        return {"findings": [], "pages": 0, "truncated": False,
                "error": f"could not read for redaction scan: {error}"}
    if not data.startswith(b"%PDF"):
        # Named .pdf but not PDF bytes (all the text fixtures):
        # nothing redactable to scan. A real PDF that fails to
        # parse still reports its error below.
        return None
    try:
        return redactions.scan_pdf_bytes(data)
    except redactions.RedactionError as error:
        return {"findings": [], "pages": 0, "truncated": False,
                "error": str(error)}
