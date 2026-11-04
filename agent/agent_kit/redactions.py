"""Bad-redaction scanner for inbox PDFs.

A rectangle drawn over live text looks redacted but copies
out: the text stays in the PDF's content layer. Our own
pipeline reads that layer (model transcription), so a badly
redacted exhibit can leak its covered text into transcripts,
facts, and memos. This scanner reports rectangles covering
live text — page, box, covered-character count — so the
reviewer knows the transcript may hold text that was meant to
be hidden.

The method (rectangles-over-text) is inspired by Free Law
Project's X-Ray, independently implemented here on pdfplumber
plus a pypdfium2 render check: X-Ray itself ships on PyMuPDF,
whose AGPL license we do not accept into this tree, so no
X-Ray code or dependency is used.

How a finding is confirmed: a filled rectangle over
non-space text is only a candidate. The page is rendered and
the rectangle's region sampled: a uniform region means the
text is truly hidden (confirmed); visible strokes mean a
translucent highlight (dropped). This is what catches
white-out boxes that a color rule must miss, and drops
highlights that a color rule must either miss or misflag.

Findings never carry the covered text — not quotes, not
excerpts. Counts and boxes only. Report-only, never blocking:
callers decide what a finding means.

Known limits (documented, not hidden):
- No z-order: text drawn atop a same-color rectangle
  (invisible by design) renders uniform and flags the same
  as a rectangle over text. Both deserve a glance, so the
  scanner fails toward disclosure.
- Scanned/image-only pages carry no text layer and stay
  silent; OCR text is out of scope.
- Render budget: at most RENDER_BUDGET suspicious pages
  render per scan; past that the scan stops with truncated
  set. Pages that fail to render fall back to the dark-fill
  rule for that page only.
"""

from __future__ import annotations

import io

MAX_FINDINGS = 100
RENDER_BUDGET = 50
UNIFORM_FRACTION = 0.98
_MIN_SIDE = 4.0
_HEADER_CUTOFF = 43.0
_DARK_LUMINANCE = 0.4
_RENDER_SCALE = 1.0


class RedactionError(Exception):
    """The bytes are not a readable PDF."""


def _luminance(color: object) -> float | None:
    """Relative luminance of a pdfplumber color, or None if unknown."""
    if isinstance(color, (int, float)):
        values = (float(color),) * 3
    elif isinstance(color, (tuple, list)) and all(
        isinstance(v, (int, float)) for v in color
    ):
        if len(color) == 1:
            values = (float(color[0]),) * 3
        elif len(color) == 3:
            values = tuple(float(v) for v in color)
        else:
            return None
    else:
        return None
    red, green, blue = (min(max(v, 0.0), 1.0) for v in values)

    def channel(value: float) -> float:
        if value <= 0.03928:
            return value / 12.92
        return ((value + 0.055) / 1.055) ** 2.4

    return (
        0.2126 * channel(red)
        + 0.7152 * channel(green)
        + 0.0722 * channel(blue)
    )


def _is_dark_fill(rect: dict) -> bool:
    """Fallback rule when a page fails to render (see module docstring)."""
    if not rect.get("fill"):
        return False
    luminance = _luminance(rect.get("non_stroking_color"))
    if luminance is None:
        return True
    return luminance < _DARK_LUMINANCE


def _covered_chars(rect: dict, chars: list) -> int:
    """Non-space characters whose center falls inside the rectangle."""
    count = 0
    for char in chars:
        if not str(char.get("text", "") or "").strip():
            continue
        try:
            cx = (float(char["x0"]) + float(char["x1"])) / 2.0
            cy = (float(char["top"]) + float(char["bottom"])) / 2.0
        except (KeyError, TypeError, ValueError):
            continue
        if (
            float(rect["x0"]) <= cx <= float(rect["x1"])
            and float(rect["top"]) <= cy <= float(rect["bottom"])
        ):
            count += 1
    return count


def _candidate(rect: dict) -> bool:
    """Filled, big enough, and below the header zone — any color."""
    if not isinstance(rect, dict) or not rect.get("fill"):
        return False
    try:
        width = float(rect["width"])
        height = float(rect["height"])
        top = float(rect["top"])
    except (KeyError, TypeError, ValueError):
        return False
    if width <= _MIN_SIDE or height <= _MIN_SIDE:
        return False
    return top > _HEADER_CUTOFF


def _region_uniform(image, rect: dict) -> bool:
    """Whether the rectangle's rendered region is one flat color.

    Both pdfplumber and the render use top-down points, so the
    box maps 1:1 at scale 1. The box erodes a pixel per side to
    skip antialiased fringe; a top-color fraction at or above
    UNIFORM_FRACTION counts as uniform.
    """
    try:
        x0 = float(rect["x0"])
        top = float(rect["top"])
        x1 = float(rect["x1"])
        bottom = float(rect["bottom"])
    except (KeyError, TypeError, ValueError):
        return False
    width, height = image.size
    left = min(max(int(round(x0)) + 1, 0), width)
    upper = min(max(int(round(top)) + 1, 0), height)
    right = min(max(int(round(x1)) - 1, 0), width)
    lower = min(max(int(round(bottom)) - 1, 0), height)
    if right <= left or lower <= upper:
        left, upper = min(int(round(x0)), width), min(int(round(top)), height)
        right, lower = min(int(round(x1)), width), min(
            int(round(bottom)), height
        )
        if right <= left or lower <= upper:
            return False
    region = image.crop((left, upper, right, lower))
    colors = region.getcolors(maxcolors=region.size[0] * region.size[1] + 1)
    if not colors:
        return False
    top_count = max(count for count, _ in colors)
    total = sum(count for count, _ in colors)
    return total > 0 and top_count / total >= UNIFORM_FRACTION


def scan_pdf_bytes(data: bytes) -> dict:
    """Scan PDF bytes for rectangles covering live text.

    Returns {"pages", "findings": [{"page", "bbox", "chars",
    "basis"}], "truncated"}. bbox is [x0, top, x1, bottom] in
    PDF points; basis is "render" (region renders uniform:
    the text is truly hidden) or "dark-fill" (the page could
    not render, so the dark-fill heuristic stands in).
    Raises RedactionError on unreadable input. The covered text
    is counted transiently and never leaves this function.
    """
    import pdfplumber

    if not isinstance(data, bytes) or not data.startswith(b"%PDF"):
        raise RedactionError("not a PDF: missing the %PDF header")
    findings: list[dict] = []
    truncated = False
    renders: dict[int, object] = {}
    rendered = 0
    pdfium_doc = _open_pdfium(data)
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            pages = len(pdf.pages)
            for number, page in enumerate(pdf.pages, start=1):
                chars = [
                    c for c in page.chars if isinstance(c, dict)
                ]
                candidates = []
                for rect in page.rects:
                    if not _candidate(rect):
                        continue
                    covered = _covered_chars(rect, chars)
                    if covered > 0:
                        candidates.append((rect, covered))
                if not candidates:
                    continue
                if number not in renders:
                    if rendered >= RENDER_BUDGET:
                        truncated = True
                        break
                    renders[number] = _render_page(pdfium_doc, number - 1)
                    rendered += 1
                image = renders[number]
                for rect, covered in candidates:
                    if image is None:
                        confirmed = _is_dark_fill(rect)
                        basis = "dark-fill"
                    else:
                        confirmed = _region_uniform(image, rect)
                        basis = "render"
                    if not confirmed:
                        continue
                    if len(findings) >= MAX_FINDINGS:
                        truncated = True
                        break
                    findings.append(
                        {
                            "page": number,
                            "bbox": [
                                round(float(rect["x0"]), 1),
                                round(float(rect["top"]), 1),
                                round(float(rect["x1"]), 1),
                                round(float(rect["bottom"]), 1),
                            ],
                            "chars": covered,
                            "basis": basis,
                        }
                    )
                if truncated:
                    break
    except RedactionError:
        raise
    except Exception as error:
        raise RedactionError(
            f"could not read the PDF: {error}"
        ) from None
    finally:
        if pdfium_doc is not None:
            try:
                pdfium_doc.close()
            except Exception:
                pass
    return {"pages": pages, "findings": findings, "truncated": truncated}


def _open_pdfium(data: bytes):
    """One open pypdfium2 document, or None when unavailable.

    A missing dependency or an unparseable file degrades the
    whole scan to the dark-fill rule rather than failing it.
    """
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return None
    try:
        return pdfium.PdfDocument(data)
    except Exception:
        return None


def _render_page(pdfium_doc, index: int):
    """Render one page to PIL RGB, or None when rendering fails."""
    if pdfium_doc is None:
        return None
    try:
        page = pdfium_doc.get_page(index)
        try:
            return page.render(scale=_RENDER_SCALE).to_pil().convert("RGB")
        finally:
            page.close()
    except Exception:
        return None


def confirmed_hidden(scan) -> bool:
    """Whether a scan proves text hidden under an opaque region.

    Only render-basis findings count: the region renders
    uniform, so the eye sees no text while the content layer
    still carries it. Dark-fill-basis findings (the page
    could not render) stay advisory — a heuristic must not
    seal exhibits. Malformed scans are not confirmation.
    """
    if not isinstance(scan, dict):
        return False
    return any(
        isinstance(finding, dict) and finding.get("basis") == "render"
        for finding in (scan.get("findings") or [])
    )


# ---- quarantine -----------------------------------------------------------
#
# A confirmed finding quarantines the document BEFORE any
# transcript exists: extraction refuses it, cached text reads
# as absent, search lists it under "quarantined" instead of
# searching it, and analysis fails the document closed. Only
# a human releases it (scripts/quarantine.py); the agent has
# no release tool. Entries are never deleted: a release is
# recorded with its note, so the audit trail survives.
#
# Residuals, stated openly: the suppressed cache file stays
# on disk (reads are gated, not the bytes — disk access is
# reviewer-trusted); a transcription already in flight when
# the quarantine lands cannot be recalled, so concurrent
# first reads of the same bad PDF race the scan. The render
# heuristic proves nothing about secure redaction either:
# quarantine means "look before trusting", not "certified".


def quarantine_path(root) -> "Path":
    from pathlib import Path

    return Path(root) / "workspace" / "quarantine.json"


def _load_quarantine(root) -> dict:
    import json

    try:
        data = json.loads(quarantine_path(root).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def quarantine_list(root) -> dict:
    """The full store: active entries plus released history."""
    return _load_quarantine(root)


def is_quarantined(root, document_id: str) -> bool:
    """Whether the document is currently sealed from the pipeline."""
    entry = _load_quarantine(root).get(str(document_id))
    return isinstance(entry, dict) and "released" not in entry


def quarantine(
    root, document_id: str, *, reason: str, findings: int = 0
) -> dict:
    """Seal a document. Re-quarantining refreshes the entry."""
    import datetime
    import json

    from .document_text import atomic_write

    store = _load_quarantine(root)
    entry = {
        "reason": str(reason or ""),
        "findings": max(0, int(findings or 0)),
        "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    store[str(document_id)] = entry
    path = quarantine_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, json.dumps(store, indent=1))
    return entry


def release(root, document_id: str, *, note: str) -> dict:
    """Record a human release. Refuses documents never quarantined."""
    import datetime
    import json

    from .document_text import atomic_write

    store = _load_quarantine(root)
    entry = store.get(str(document_id))
    if not isinstance(entry, dict) or "released" in entry:
        raise ValueError(f"{document_id} is not quarantined")
    entry["released"] = {
        "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "note": str(note or ""),
    }
    atomic_write(quarantine_path(root), json.dumps(store, indent=1))
    return entry
