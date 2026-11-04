"""Panel export: read-only objection-ledger + lens-coverage view for the kernel.

Stdlib-only, zero first-party imports: safe for import-sensitive
modules (e.g. the artifact kernel). The case record stays the
source of truth; this never writes. Contract for peer kernel use
is documented in docs/oss-mining/07-panel-export-contract.md.
"""

from __future__ import annotations

#: The fixed panel seats. Pinned equal to
#: agent_kit.case_record.ADVERSARIAL_LENSES by test (no import, no drift).
REQUIRED_LENSES = ("evidence", "law", "procedure", "strategy")


def _reviews(data: dict) -> list:
    return [
        entry for entry in data.get("adversarial", []) or []
        if isinstance(entry, dict)
    ]


def _findings(data: dict) -> list:
    return [
        entry for entry in data.get("objections", []) or []
        if isinstance(entry, dict)
    ]


def _lenses(reviews: list) -> list:
    return sorted(
        {
            str(review.get("lens", "") or "")
            for review in reviews
            if str(review.get("lens", "") or "")
        }
    )


def _finding_view(finding: dict, *, disproven: bool) -> dict:
    view = {
        "id": finding.get("id"),
        "objection": str(finding.get("objection", "") or ""),
        "reporters": [
            str(reporter)
            for reporter in finding.get("reporters", []) or []
        ],
        "at": finding.get("at"),
    }
    if disproven:
        view["counter_evidence"] = str(
            finding.get("counter_evidence", "") or ""
        )
    return view


def panel_export(case_data: dict) -> dict | None:
    """Objection ledger + lens coverage for export, or None when absent.

    None when the run has neither adversarial reviews nor ledger
    objections (same convention as the kernel's adversarial_summary).
    Malformed entries are skipped, never raised.
    """
    data = case_data if isinstance(case_data, dict) else {}
    reviews = _reviews(data)
    findings = _findings(data)
    if not reviews and not findings:
        return None
    covered = _lenses(reviews)
    missing = [lens for lens in REQUIRED_LENSES if lens not in covered]
    opened = [f for f in findings if f.get("status") == "open"]
    dropped = [f for f in findings if f.get("status") == "disproven"]
    return {
        "reviews": len(reviews),
        "latest": reviews[-1].get("id") if reviews else None,
        "lenses": covered,
        "required": list(REQUIRED_LENSES),
        "missing": missing,
        "complete": not missing,
        "objections": {
            "open": [_finding_view(f, disproven=False) for f in opened],
            "disproven": [_finding_view(f, disproven=True) for f in dropped],
            "counts": {
                "open": len(opened),
                "disproven": len(dropped),
                "total": len(opened) + len(dropped),
            },
        },
    }
