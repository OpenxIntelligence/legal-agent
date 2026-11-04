"""check_signatures (verification on): is the signature on one page consistent with a
genuine reference signature of the same person on another page?

The agent names both pages: the page to check, and a page the signer genuinely signed.
SAM 3.1 finds the signature-like ink on each page image (segment, below), then the
judge compares the two pages with their boxes. The verdict is one of VERDICTS.
Anything other than "consistent" becomes a signature flag the attorney reviews at
sign-off, and the memo rules refuse a finding asserted on that signature.

This is a visual-consistency check only, not forgery detection or authentication:
"consistent" means the questioned signature looks like the same hand as the
reference, nothing more. A judge or provider failure is returned as an error
for a later retry, never as a fabricated verdict.
"""

from __future__ import annotations

import base64
import json
import re
import time
from pathlib import Path
from typing import cast, TYPE_CHECKING

from openai import OpenAI
from openai.types.responses import Response

from .. import document_text, usage
from ..settings import model, sam_model

if TYPE_CHECKING:
    from . import Context

VERDICTS = ("consistent", "inconsistent", "unclear", "unsigned")

DEFINITION = {
    "type": "function",
    "name": "check_signatures",
    "description": "Check whether the signature presented as the signer's on one page is visually consistent with a "
    "genuine signature of the same person on another page (the reference: a page you know they signed). "
    "Returns consistent, inconsistent, unclear, or unsigned. This is visual consistency only, not forgery "
    "detection or authentication. Check a signature before relying on it for a checklist item.",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "document_id": {
                "type": "string",
                "description": "the document whose signature you are checking",
            },
            "page": {"type": "integer"},
            "signer": {
                "type": "string",
                "description": "the person the signature is presented as",
            },
            "reference_document_id": {
                "type": "string",
                "description": "a document the signer genuinely signed",
            },
            "reference_page": {"type": "integer"},
        },
        "required": [
            "document_id",
            "page",
            "signer",
            "reference_document_id",
            "reference_page",
        ],
    },
}


JUDGE_VERDICTS = ("consistent", "inconsistent", "unclear")

PROMPT = """SIGNATURE COMPARISON. Decide whether the questioned signature is visually consistent with the reference signature.

Questioned: the first page image, with {n_questioned} marked box(es) (ink scribble regions).
Reference: the second page image, with {n_reference} marked box(es); a page the signer genuinely signed.
Compare letter shapes, loops, slant, and rhythm. This is a visual-consistency check only, not forgery detection or authentication.

Boxes (JSON):
{boxes}

Answer only with JSON: {{"verdict": "consistent" or "inconsistent" or "unclear", "reason": "one sentence"}}.
Use "unclear" when the images or boxes do not allow a decision."""


def _short(value: object, limit: int = 160) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[:limit] + "…"


def run(ctx: Context, args: dict) -> dict:
    from . import page_arg

    body = args if isinstance(args, dict) else {}
    raw_page = body.get("page", 1)
    raw_reference = body.get("reference_page", 1)
    document_id = str(body.get("document_id", ""))
    page = 1 if raw_page is None else page_arg(
        "check_signatures", "page", raw_page)
    reference_id = str(body.get("reference_document_id", ""))
    reference_page = 1 if raw_reference is None else page_arg(
        "check_signatures", "reference_page", raw_reference)
    signer = str(args.get("signer", ""))
    if (reference_id, reference_page) == (document_id, page):
        return {
            "error": "the reference must be a different page, one the signer genuinely signed"
        }
    questioned_path = document_text.page_image(
        ctx.root, document_id, page
    )  # both pages must exist
    reference_path = document_text.page_image(ctx.root, reference_id, reference_page)
    ledger = (ctx.root, ctx.run_id)
    try:
        questioned_boxes = segment(ctx.client, questioned_path, ledger=ledger)
        reference_boxes = segment(ctx.client, reference_path, ledger=ledger)
    except Exception as error:
        return {"error": f"signature segmentation failed: {_short(error)}"}
    if not questioned_boxes:
        return _record(
            ctx,
            document_id,
            page,
            signer,
            reference_id,
            reference_page,
            [],
            reference_boxes,
            "unsigned",
            "no signature-like ink found on the questioned page",
        )
    if not reference_boxes:
        return {
            "error": (
                f"{reference_id} p{reference_page} shows no signature-like ink; "
                "name a reference page the signer genuinely signed"
            )
        }
    start = time.perf_counter()
    try:
        response = cast(
            Response,
            ctx.client.responses.create(
                model=model(),
                input=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": PROMPT.format(
                                    n_questioned=len(questioned_boxes),
                                    n_reference=len(reference_boxes),
                                    boxes=json.dumps(
                                        {
                                            "questioned": questioned_boxes,
                                            "reference": reference_boxes,
                                        }
                                    ),
                                ),
                            },
                            {
                                "type": "input_image",
                                "image_url": data_url(questioned_path),
                            },
                            {
                                "type": "input_image",
                                "image_url": data_url(reference_path),
                            },
                        ],
                    }
                ],
                text={"format": {"type": "json_object"}},
            ),
        )
    except Exception as error:
        usage.record_failure(
            ctx.root,
            ctx.run_id,
            "signature_judge",
            error,
            latency_ms=(time.perf_counter() - start) * 1000,
        )
        return {
            "error": (
                f"signature comparison unavailable: {_short(error)}; "
                "retry check_signatures"
            )
        }
    usage.record(
        ctx.root,
        ctx.run_id,
        "signature_judge",
        response,
        latency_ms=(time.perf_counter() - start) * 1000,
    )
    try:
        parsed = json.loads(response.output_text)
    except (ValueError, AttributeError, TypeError):
        return {
            "error": "signature comparison returned invalid JSON; retry check_signatures"
        }
    verdict = parsed.get("verdict") if isinstance(parsed, dict) else None
    if verdict not in JUDGE_VERDICTS:
        return {
            "error": (
                "signature comparison returned an invalid verdict; "
                "retry check_signatures"
            )
        }
    reason = parsed.get("reason", "")
    reason = reason if isinstance(reason, str) and reason.strip() else "no reason given"
    return _record(
        ctx,
        document_id,
        page,
        signer,
        reference_id,
        reference_page,
        questioned_boxes,
        reference_boxes,
        verdict,
        f"visual consistency only: {reason}",
    )


def _record(
    ctx: Context,
    document_id: str,
    page: int,
    signer: str,
    reference_id: str,
    reference_page: int,
    boxes: list,
    reference_boxes: list,
    verdict: str,
    reason: str,
) -> dict:
    ctx.case.add_signature_check(
        {
            "document_id": document_id,
            "page": page,
            "signer": signer,
            "verdict": verdict,
            "reason": reason,
            "boxes": boxes,
            "reference": {
                "document_id": reference_id,
                "page": reference_page,
                "boxes": reference_boxes,
            },
        }
    )
    return {
        "document_id": document_id,
        "page": page,
        "signer": signer,
        "verdict": verdict,
        "reason": reason,
    }


# ---- SAM 3.1 ---------------------------------------------------------------
#
# SAM returns special-token text in output_text:
#     <0f>0<|box;x1=..;y1=..;x2=..;y2=..;w=..;h=..|><|mask;...|>,1<|box;...|>...
# Only the boxes are needed. SAM 3.1 does not match "signature" on a scanned page;
# "ink scribble" finds handwritten signatures.

SIGNATURE_PROMPT = "ink scribble"
BOX = re.compile(r"(\d+)<\|box;x1=(\d+);y1=(\d+);x2=(\d+);y2=(\d+);w=(\d+);h=(\d+)\|>")


def parse_boxes(output_text: str) -> list:
    """Every box in SAM's output, in order. Empty output means zero matches."""
    boxes = []
    for match in BOX.finditer(output_text or ""):
        ordinal, x1, y1, x2, y2, w, h = (int(v) for v in match.groups())
        boxes.append(
            {
                "ordinal": ordinal,
                "x1": x1,
                "y1": y1,
                "x2": x2,
                "y2": y2,
                "width": w,
                "height": h,
            }
        )
    return boxes


def data_url(image: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(image.read_bytes()).decode()


def segment(
    client: OpenAI, image: Path, prompt: str = SIGNATURE_PROMPT, ledger=None
) -> list:
    """One SAM call on one page image: the boxes it found.

    Failures are ledgered, then re-raised unchanged.
    """
    start = time.perf_counter()
    try:
        response = cast(
            Response,
            client.responses.create(
                model=sam_model(),
                input=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": prompt},
                            {"type": "input_image", "image_url": data_url(image)},
                        ],
                    }
                ],
            ),
        )
    except Exception as error:
        if ledger is not None:
            usage.record_failure(
                ledger[0],
                ledger[1],
                "sam_segment",
                error,
                latency_ms=(time.perf_counter() - start) * 1000,
            )
        raise
    if ledger is not None:
        usage.record(
            ledger[0],
            ledger[1],
            "sam_segment",
            response,
            latency_ms=(time.perf_counter() - start) * 1000,
        )
    return parse_boxes(response.output_text)
