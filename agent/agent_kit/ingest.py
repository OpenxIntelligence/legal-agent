"""Human document intake: validated files into the shared inbox.

A lawyer adds a file from the interface; this module stores it
and runs the same deterministic gates the analysis path uses.
No model calls happen here, so states stay honest:

- text (.txt/.md): transcribed deterministically (one page
  per form-feed section) and injection-scanned on arrival.
- pdf (.pdf): stored, redaction-scanned, quarantined on
  render-confirmed hidden text; transcription stays pending
  until model extraction runs (not from this path).
- anything else: refused with the reason, nothing written.

Filenames are allowlisted (no traversal, no overwrite, capped
sizes). A failed scan records its error on a pending PDF;
covered text never reaches a transcript.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re

from agent_kit import document_text, redactions
from agent_kit.document_text import DocumentError
from agent_kit.reasoning import scan_text

TEXT_SUFFIXES = (".txt", ".md")
PDF_SUFFIXES = (".pdf",)
ALLOWED_SUFFIXES = TEXT_SUFFIXES + PDF_SUFFIXES
MAX_TEXT_CHARS = 256_000
MAX_PDF_BYTES = 5_000_000
_STEM = re.compile(r"[A-Za-z0-9_-]{1,64}")

__all__ = [
    "ALLOWED_SUFFIXES",
    "MAX_TEXT_CHARS",
    "MAX_PDF_BYTES",
    "add_document",
]


def _split_filename(filename: str) -> tuple[str, str]:
    name = str(filename or "").strip()
    if "/" in name or "\\" in name or "\x00" in name:
        raise DocumentError(f"refusing filename {name!r}")
    if "." not in name:
        raise DocumentError(f"{name!r} needs an extension")
    stem, dot, suffix = name.rpartition(".")
    suffix = f".{suffix.lower()}"
    if not _STEM.fullmatch(stem):
        raise DocumentError(f"refusing filename {name!r}")
    if suffix not in ALLOWED_SUFFIXES:
        raise DocumentError(
            f"{suffix} is not accepted; use "
            + ", ".join(ALLOWED_SUFFIXES))
    return stem, suffix


def _scan_upload_pdf(data: bytes) -> dict | None:
    """Redaction scan mirroring the analysis gate (public API only)."""
    if not data.startswith(b"%PDF"):
        return None
    try:
        return redactions.scan_pdf_bytes(data)
    except redactions.RedactionError as error:
        return {"findings": [], "pages": 0, "truncated": False,
                "error": str(error)}


def add_document(root, *, filename: str, text=None,
                 content_b64: str | None = None) -> dict:
    """Store one uploaded file; return its id, state, and findings."""
    stem, suffix = _split_filename(filename)
    inbox = root / "workspace" / "inbox"
    clash = [p.name for p in inbox.glob(f"{stem}.*")] if inbox.is_dir() \
        else []
    if clash:
        raise DocumentError(
            f"{stem} already exists as {clash[0]}; rename the upload")
    if suffix in TEXT_SUFFIXES:
        if content_b64 is not None:
            raise DocumentError("text uploads send text, not base64")
        body = str(text or "")
        if len(body) > MAX_TEXT_CHARS:
            raise DocumentError(
                f"text exceeds {MAX_TEXT_CHARS} characters")
        data = body.encode()
    else:
        if text is not None:
            raise DocumentError("pdf uploads send base64 bytes, not text")
        try:
            data = base64.b64decode(content_b64 or "", validate=True)
        except (binascii.Error, ValueError):
            raise DocumentError("pdf bytes are not valid base64") from None
        if len(data) > MAX_PDF_BYTES:
            raise DocumentError(
                f"pdf exceeds {MAX_PDF_BYTES} bytes")
        if not data.startswith(b"%PDF"):
            raise DocumentError(
                "pdf uploads must be real PDF bytes (%PDF header)")
    inbox.mkdir(parents=True, exist_ok=True)
    path = inbox / f"{stem}{suffix}"
    path.write_bytes(data)
    sha = hashlib.sha256(data).hexdigest()
    try:
        if suffix in TEXT_SUFFIXES:
            sections = body.split("\f")
            transcript = document_text.store_text_pages(
                root, stem, sections)
            findings = []
            for page in transcript.get("pages", []) or []:
                for hit in scan_text(page.get("text", "")):
                    findings.append(
                        {"page": page.get("page"), **hit})
            return {
                "document_id": stem,
                "file": path.name,
                "pages": len(transcript["pages"]),
                "sha256": sha,
                "state": "transcribed",
                "injection_findings": findings,
            }
        scan = _scan_upload_pdf(data)
        if redactions.confirmed_hidden(scan):
            count = len((scan or {}).get("findings") or [])
            redactions.quarantine(
                root, stem,
                reason="render-confirmed text hidden under an "
                "opaque region",
                findings=count)
            return {
                "document_id": stem,
                "file": path.name,
                "pages": document_text.page_count(path),
                "sha256": sha,
                "state": "quarantined",
                "quarantine_reason": "render-confirmed text hidden "
                "under an opaque region",
                "redaction_scan": scan,
            }
        return {
            "document_id": stem,
            "file": path.name,
            "pages": document_text.page_count(path),
            "sha256": sha,
            "state": "transcription_pending",
            "redaction_scan": scan,
        }
    except Exception:
        path.unlink(missing_ok=True)
        raise
