"""Per-run model-usage ledger: every tool-level model call, attributed by purpose.

Agent turns already record their tokens in progress.json; this module covers the
model calls tools and checks make outside turns (extraction, analysis,
segmentation, judges). One JSON file per run,
`workspace/runs/<run-id>/usage.json`: a list of entries with
`at`, `purpose`, `model`, `input_tokens`, `output_tokens`,
`latency_ms` (null when unmeasured), `failed`, and `error`.
Failed calls keep null token counts: unknown usage is unknown,
never zero. Entries written before the newer fields existed stay
readable and total as before.
"""

from __future__ import annotations

import datetime
import json
import threading
from pathlib import Path

_LOCK = threading.Lock()


def _path(root: Path, run_id: str) -> Path:
    return Path(root) / "workspace" / "runs" / run_id / "usage.json"


def _num(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def record(
    root: Path,
    run_id: str,
    purpose: str,
    response,
    *,
    latency_ms: float | None = None,
) -> dict:
    """Append one ledger entry from a Responses API response. Returns the entry."""
    usage = getattr(response, "usage", None)
    entry = {
        "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "purpose": purpose,
        "model": getattr(response, "model", "") or "",
        "input_tokens": _num(getattr(usage, "input_tokens", 0)),
        "output_tokens": _num(getattr(usage, "output_tokens", 0)),
        "latency_ms": _latency(latency_ms),
        "failed": False,
        "error": "",
    }
    _append(root, run_id, entry)
    return entry


def record_failure(
    root: Path,
    run_id: str,
    purpose: str,
    error: object,
    *,
    model: str = "",
    latency_ms: float | None = None,
) -> dict:
    """Append a failed call: null token counts, short error, no payload."""
    entry = {
        "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "purpose": purpose,
        "model": model or "",
        "input_tokens": None,
        "output_tokens": None,
        "latency_ms": _latency(latency_ms),
        "failed": True,
        "error": _short(error),
    }
    _append(root, run_id, entry)
    return entry


def _append(root: Path, run_id: str, entry: dict) -> None:
    path = _path(root, run_id)
    with _LOCK:
        try:
            entries = json.loads(path.read_text())
        except (OSError, ValueError):
            entries = []
        if not isinstance(entries, list):
            entries = []
        entries.append(entry)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(entries, indent=1))
        tmp.replace(path)


def _latency(value) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and value >= 0:
        return round(float(value), 1)
    return None


def _short(value: object, limit: int = 160) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[:limit] + "…"


def totals(root: Path, run_id: str) -> dict:
    """Ledger totals: calls, failures, tokens, known latency, per-purpose."""
    try:
        entries = json.loads(_path(root, run_id).read_text())
    except (OSError, ValueError):
        entries = []
    by_purpose: dict = {}
    calls, failures = 0, 0
    input_tokens, output_tokens, latency_ms, latency_calls = 0, 0, 0.0, 0
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        calls += 1
        failed = entry.get("failed") is True
        failures += 1 if failed else 0
        got_in, got_out = _num(entry.get("input_tokens")), _num(
            entry.get("output_tokens")
        )
        input_tokens += got_in
        output_tokens += got_out
        known = _latency(entry.get("latency_ms"))
        latency_calls += 1 if known is not None else 0
        latency_ms += known or 0.0
        slot = by_purpose.setdefault(
            str(entry.get("purpose") or "unknown"),
            {
                "calls": 0,
                "failures": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "latency_ms": 0.0,
            },
        )
        slot["calls"] += 1
        slot["failures"] += 1 if failed else 0
        slot["input_tokens"] += got_in
        slot["output_tokens"] += got_out
        slot["latency_ms"] += known or 0.0
    return {
        "calls": calls,
        "failures": failures,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "latency_ms": round(latency_ms, 1),
        "latency_calls": latency_calls,
        "by_purpose": by_purpose,
    }
