"""outside counsel spend (one artifact slice; split from artifacts.py, behavior unchanged)."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from agent_kit import document_text
from agent_kit.case_record import CaseError
from agent_kit.progress import now

from .kernel import (
    TEMPLATE_AUDIT,
    TEMPLATE_BOARD,
    TEMPLATE_CAL,
    TEMPLATE_CT,
    TEMPLATE_DL,
    TEMPLATE_DR,
    TEMPLATE_EC,
    TEMPLATE_GV,
    TEMPLATE_HZ,
    TEMPLATE_IN,
    TEMPLATE_JX,
    TEMPLATE_PB,
    TEMPLATE_PT,
    TEMPLATE_QA,
    TEMPLATE_SP,
    TEMPLATE_TS,
    TEMPLATE_AUDIT_VERSION,
    TEMPLATE_BOARD_VERSION,
    TEMPLATE_JX_VERSION,
    TEMPLATE_CT_VERSION,
    TEMPLATE_PB_VERSION,
    TEMPLATE_CAL_VERSION,
    TEMPLATE_IN_VERSION,
    TEMPLATE_DL_VERSION,
    TEMPLATE_DR_VERSION,
    TEMPLATE_HZ_VERSION,
    TEMPLATE_GV_VERSION,
    TEMPLATE_EC_VERSION,
    TEMPLATE_TS_VERSION,
    TEMPLATE_PT_VERSION,
    TEMPLATE_QA_VERSION,
    TEMPLATE_SP_VERSION,
    _blank_review,
    _excerpt,
    _flagged,
    _inbox_shas,
    _next_instance_id,
    _stable,
    _structural_outcome,
    audit_citation,
    register,
)


SP_CURRENCIES = ("USD", "EUR", "GBP", "SAR")
SP_STATUS = ("received", "approved", "paid", "disputed")
_SP_NEXT = {
    "received": "approve or dispute",
    "disputed": "approve or receive",
}
_SP_DECISIONS = {
    "received": ("approved", "disputed"),
    "approved": ("paid", "disputed"),
    "disputed": ("approved", "received"),
    "paid": ("received",),
}


def _sp_amount(value: str, field: str, required: bool) -> str:
    """Validate a money string; empty stays empty unless required.

    Accepted: digits with an optional two-decimal fraction.
    Anything else is refused, never rounded into meaning.
    """
    from decimal import Decimal

    value = str(value or "").strip()
    if not value:
        if required:
            raise CaseError(f"an invoice needs its {field}")
        return ""
    if not re.fullmatch(r"\d+(\.\d{1,2})?", value):
        raise CaseError(f"{field} must be a decimal amount, got {value!r}")
    return f"{Decimal(value):.2f}"


def _sp_total(amounts: list[str]) -> str:
    from decimal import Decimal

    return f"{sum((Decimal(a) for a in amounts), Decimal('0')):.2f}"


def open_sp_instance(
    data: dict, run_id: str, title: str, source_id: str = ""
) -> dict:
    """Create a spend tracker (pure; caller saves).

    No prerequisites: vendors, engagements, and
    invoices are reviewer records.
    """
    instance = {
        "id": _next_instance_id(data),
        "template": TEMPLATE_SP,
        "template_version": TEMPLATE_SP_VERSION,
        "matter": run_id,
        "title": title or "Outside Counsel Spend",
        "owner": "local",
        "viewers": ["local"],
        "created_at": now(),
        "vendors": [],
        "engagements": [],
        "invoices": [],
        "versions": [],
        "review": _blank_review(),
        "history": [{"at": now(), "event": "opened", "detail": ""}],
    }
    instance["versions"].append(
        {
            "v": 1,
            "at": now(),
            "reason": "opened",
            "vendors": [],
            "engagements": [],
            "invoices": [],
        }
    )
    return instance


def sp_get_vendor(instance: dict, vn_id: str) -> dict:
    vendor = next(
        (v for v in instance.get("vendors") or [] if v.get("vn_id") == vn_id),
        None,
    )
    if vendor is None:
        raise CaseError(f"unknown vendor {vn_id!r}")
    return vendor


def sp_get_engagement(instance: dict, eg_id: str) -> dict:
    engagement = next(
        (e for e in instance.get("engagements") or [] if e.get("eg_id") == eg_id),
        None,
    )
    if engagement is None:
        raise CaseError(f"unknown engagement {eg_id!r}")
    return engagement


def sp_get_invoice(instance: dict, iv_id: str) -> dict:
    invoice = next(
        (i for i in instance.get("invoices") or [] if i.get("iv_id") == iv_id),
        None,
    )
    if invoice is None:
        raise CaseError(f"unknown invoice {iv_id!r}")
    return invoice


def _sp_version(instance: dict, reason: str) -> None:
    versions = instance.setdefault("versions", [])
    versions.append(
        {
            "v": len(versions) + 1,
            "at": now(),
            "reason": reason,
            "vendors": copy.deepcopy(instance.get("vendors")),
            "engagements": copy.deepcopy(instance.get("engagements")),
            "invoices": copy.deepcopy(instance.get("invoices")),
        }
    )


def _sp_touch(instance: dict, event: str, detail: str) -> None:
    instance["history"].append({"at": now(), "event": event, "detail": detail})
    instance["review"] = _blank_review()


def _next_vn_id(instance: dict) -> str:
    taken = {v.get("vn_id", "") for v in instance.get("vendors") or []}
    seq = 1
    while f"VN{seq}" in taken:
        seq += 1
    return f"VN{seq}"


def _next_eg_id(instance: dict) -> str:
    taken = {e.get("eg_id", "") for e in instance.get("engagements") or []}
    seq = 1
    while f"EG{seq}" in taken:
        seq += 1
    return f"EG{seq}"


def _next_iv_id(instance: dict) -> str:
    taken = {i.get("iv_id", "") for i in instance.get("invoices") or []}
    seq = 1
    while f"IV{seq}" in taken:
        seq += 1
    return f"IV{seq}"


def _vendor_pin(vendor: dict) -> str:
    return _stable(
        {"name": vendor.get("name"), "contact": vendor.get("contact")}
    )


def _engagement_pin(engagement: dict) -> str:
    return _stable(
        {
            "vn_id": engagement.get("vn_id"),
            "title": engagement.get("title"),
            "budget": engagement.get("budget"),
            "currency": engagement.get("currency"),
        }
    )


def add_vn(instance: dict, name: str, contact: str = "") -> dict:
    """Register one vendor (pure; caller saves)."""
    if not str(name or "").strip():
        raise CaseError("a vendor needs its name")
    vendor = {
        "vn_id": _next_vn_id(instance),
        "name": str(name).strip(),
        "contact": str(contact or "").strip(),
        "history": [{"at": now(), "change": "registered", "detail": ""}],
    }
    instance.setdefault("vendors", []).append(vendor)
    _sp_version(instance, f"registered {vendor['vn_id']}")
    _sp_touch(instance, "vendor_registered", vendor["vn_id"])
    return vendor


def set_vn(
    instance: dict,
    vn_id: str,
    name: str | None = None,
    contact: str | None = None,
) -> dict:
    """Revise a vendor (pure; caller saves).

    Revisions do not rewrite history: engagements
    pinned to the old identity go stale with
    reasons until the reviewer re-binds them.
    """
    vendor = sp_get_vendor(instance, vn_id)
    if name is not None:
        if not str(name).strip():
            raise CaseError("a vendor needs its name")
        vendor["name"] = str(name).strip()
    if contact is not None:
        vendor["contact"] = str(contact).strip()
    vendor["history"].append(
        {"at": now(), "change": "revised", "detail": vn_id}
    )
    _sp_version(instance, f"revised {vn_id}")
    _sp_touch(instance, "vendor_revised", vn_id)
    return vendor


def add_eg(
    instance: dict,
    vn_id: str,
    title: str,
    budget: str = "",
    currency: str = "USD",
) -> dict:
    """Open one engagement with a vendor (pure; caller saves).

    The currency is fixed at opening from the
    supported list; the budget stays empty until the
    reviewer states it.
    """
    vendor = sp_get_vendor(instance, vn_id)
    if not str(title or "").strip():
        raise CaseError("an engagement needs its title")
    if currency not in SP_CURRENCIES:
        raise CaseError(f"currency must be one of: {', '.join(SP_CURRENCIES)}")
    budget = _sp_amount(budget, "budget", False)
    engagement = {
        "eg_id": _next_eg_id(instance),
        "vn_id": vn_id,
        "vn_pin": _vendor_pin(vendor),
        "title": str(title).strip(),
        "budget": budget,
        "currency": currency,
        "history": [{"at": now(), "change": "opened", "detail": ""}],
    }
    instance.setdefault("engagements", []).append(engagement)
    _sp_version(instance, f"opened {engagement['eg_id']}")
    _sp_touch(instance, "engagement_opened", engagement["eg_id"])
    return engagement


def set_eg(
    instance: dict,
    eg_id: str,
    vn_id: str | None = None,
    title: str | None = None,
    budget: str | None = None,
    currency: str | None = None,
    rebind: bool = False,
) -> dict:
    """Revise an engagement (pure; caller saves).

    Moving an engagement re-pins the new vendor;
    rebind refreshes the pin after the reviewer
    confirms a stale vendor revision. Currency
    cannot change once invoices exist: past
    amounts keep the meaning they were recorded
    with.
    """
    engagement = sp_get_engagement(instance, eg_id)
    if vn_id is not None:
        vendor = sp_get_vendor(instance, vn_id)
        engagement["vn_id"] = vn_id
        engagement["vn_pin"] = _vendor_pin(vendor)
    if title is not None:
        if not str(title).strip():
            raise CaseError("an engagement needs its title")
        engagement["title"] = str(title).strip()
    if budget is not None:
        engagement["budget"] = _sp_amount(budget, "budget", False)
    if currency is not None:
        if currency not in SP_CURRENCIES:
            raise CaseError(
                f"currency must be one of: {', '.join(SP_CURRENCIES)}"
            )
        mine = [
            i for i in instance.get("invoices") or []
            if i.get("eg_id") == eg_id
        ]
        if mine and currency != engagement.get("currency"):
            raise CaseError(
                f"{eg_id} already has invoices in "
                f"{engagement.get('currency')}; open a new engagement instead"
            )
        engagement["currency"] = currency
    if rebind:
        engagement["vn_pin"] = _vendor_pin(
            sp_get_vendor(instance, engagement["vn_id"])
        )
    engagement["history"].append(
        {"at": now(), "change": "revised", "detail": eg_id}
    )
    _sp_version(instance, f"revised {eg_id}")
    _sp_touch(instance, "engagement_revised", eg_id)
    return engagement


def add_iv(
    instance: dict,
    eg_id: str,
    label: str,
    amount: str,
    notes: str = "",
) -> dict:
    """Log one invoice against an engagement (pure; caller saves).

    The amount is required and must parse; it takes
    the engagement's currency, which is never
    converted.
    """
    engagement = sp_get_engagement(instance, eg_id)
    if not str(label or "").strip():
        raise CaseError("an invoice needs its label")
    amount = _sp_amount(amount, "amount", True)
    invoice = {
        "iv_id": _next_iv_id(instance),
        "eg_id": eg_id,
        "eg_pin": _engagement_pin(engagement),
        "label": str(label).strip(),
        "amount": amount,
        "notes": str(notes or "").strip(),
        "status": "received",
        "resolution": "",
        "history": [{"at": now(), "change": "logged", "detail": ""}],
    }
    instance.setdefault("invoices", []).append(invoice)
    _sp_version(instance, f"logged {invoice['iv_id']}")
    _sp_touch(instance, "invoice_logged", invoice["iv_id"])
    return invoice


def set_iv(
    instance: dict,
    iv_id: str,
    eg_id: str | None = None,
    label: str | None = None,
    amount: str | None = None,
    notes: str | None = None,
    rebind: bool = False,
) -> dict:
    """Revise an invoice (pure; caller saves).

    Paid invoices are locked: reopen them first.
    Moving an invoice re-pins the new engagement;
    rebind refreshes the pin after the reviewer
    confirms a stale engagement revision.
    """
    invoice = sp_get_invoice(instance, iv_id)
    if invoice.get("status") == "paid":
        raise CaseError(f"{iv_id} is paid; reopen it first")
    if eg_id is not None:
        engagement = sp_get_engagement(instance, eg_id)
        invoice["eg_id"] = eg_id
        invoice["eg_pin"] = _engagement_pin(engagement)
    if label is not None:
        if not str(label).strip():
            raise CaseError("an invoice needs its label")
        invoice["label"] = str(label).strip()
    if amount is not None:
        invoice["amount"] = _sp_amount(amount, "amount", True)
    if notes is not None:
        invoice["notes"] = str(notes).strip()
    if rebind:
        invoice["eg_pin"] = _engagement_pin(
            sp_get_engagement(instance, invoice["eg_id"])
        )
    invoice["history"].append(
        {"at": now(), "change": "revised", "detail": iv_id}
    )
    _sp_version(instance, f"revised {iv_id}")
    _sp_touch(instance, "invoice_revised", iv_id)
    return invoice


def decide_iv(
    instance: dict, iv_id: str, decision: str, reason: str = ""
) -> dict:
    """Move an invoice through its lifecycle (pure; caller saves).

    Every decision except reopening to received
    needs a stated reason on the record.
    """
    invoice = sp_get_invoice(instance, iv_id)
    status = invoice.get("status") or "received"
    if decision not in (_SP_DECISIONS.get(status) or ()):
        allowed = ", ".join(_SP_DECISIONS.get(status) or ())
        raise CaseError(f"{iv_id} is {status}; allowed next: {allowed or 'none'}")
    if decision != "received" and not str(reason or "").strip():
        raise CaseError(f"{decision} needs a stated reason")
    invoice["status"] = decision
    invoice["resolution"] = (
        str(reason or "").strip() if decision != "received" else ""
    )
    invoice["history"].append(
        {"at": now(), "change": decision, "detail": str(reason or "").strip()}
    )
    _sp_version(instance, f"{decision} {iv_id}")
    _sp_touch(instance, f"invoice_{decision}", iv_id)
    return invoice


def eg_status(instance: dict, engagement: dict) -> dict:
    """Current, stale, or orphaned for one engagement.

    A vendor revision stales its engagements; a
    missing vendor orphans them.
    """
    reasons: list[str] = []
    state = "current"
    vn_id = engagement.get("vn_id") or ""
    vendor = next(
        (v for v in instance.get("vendors") or [] if v.get("vn_id") == vn_id),
        None,
    )
    if vendor is None:
        state = "orphaned"
        reasons.append(f"vendor {vn_id or '?'} is gone")
    elif _vendor_pin(vendor) != engagement.get("vn_pin"):
        state = "stale"
        reasons.append(f"vendor {vn_id} changed since this engagement")
    return {"state": state, "reasons": reasons}


def iv_status(instance: dict, invoice: dict) -> dict:
    """Current, stale, or orphaned for one invoice.

    An engagement revision stales its invoices; a
    missing engagement orphans them.
    """
    reasons: list[str] = []
    state = "current"
    eg_id = invoice.get("eg_id") or ""
    engagement = next(
        (e for e in instance.get("engagements") or [] if e.get("eg_id") == eg_id),
        None,
    )
    if engagement is None:
        state = "orphaned"
        reasons.append(f"engagement {eg_id or '?'} is gone")
    elif _engagement_pin(engagement) != invoice.get("eg_pin"):
        state = "stale"
        reasons.append(f"engagement {eg_id} changed since this invoice")
    return {"state": state, "reasons": reasons}


def sp_spend(instance: dict, eg_id: str) -> dict:
    """Exact per-engagement totals in the engagement's currency.

    Invoiced counts every invoice; approved counts
    approved and paid; remaining compares invoiced
    against the budget. No currency is converted.
    """
    from decimal import Decimal

    engagement = sp_get_engagement(instance, eg_id)
    mine = [
        i for i in instance.get("invoices") or [] if i.get("eg_id") == eg_id
    ]
    invoiced = _sp_total([i.get("amount", "0") for i in mine])
    approved = _sp_total(
        [i.get("amount", "0") for i in mine if i.get("status") in ("approved", "paid")]
    )
    paid = _sp_total(
        [i.get("amount", "0") for i in mine if i.get("status") == "paid"]
    )
    budget = engagement.get("budget") or ""
    remaining = f"{Decimal(budget or '0') - Decimal(invoiced):.2f}" if budget else ""
    return {
        "eg_id": eg_id,
        "currency": engagement.get("currency"),
        "budget": budget,
        "invoiced": invoiced,
        "approved": approved,
        "paid": paid,
        "remaining": remaining,
        "over": bool(budget and Decimal(invoiced) > Decimal(budget)),
    }


def sp_queue(instance: dict) -> list[dict]:
    """Received and disputed invoices with their link state."""
    queue = []
    for invoice in instance.get("invoices") or []:
        if invoice.get("status") not in ("received", "disputed"):
            continue
        status = iv_status(instance, invoice)
        queue.append(
            {
                "iv_id": invoice.get("iv_id"),
                "label": invoice.get("label"),
                "eg_id": invoice.get("eg_id"),
                "amount": invoice.get("amount"),
                "status": invoice.get("status"),
                "link_state": status["state"],
                "reasons": status["reasons"],
            }
        )
    return queue


def sp_summary(instance: dict) -> dict:
    """Counts, per-engagement spend, queue, and stale rows."""
    invoices = instance.get("invoices") or []
    by_status: dict[str, int] = {}
    for invoice in invoices:
        status = invoice.get("status") or "received"
        by_status[status] = by_status.get(status, 0) + 1
    spend = [
        sp_spend(instance, e.get("eg_id", ""))
        for e in instance.get("engagements") or []
    ]
    unbudgeted = [s["eg_id"] for s in spend if not s["budget"]]
    over = [s["eg_id"] for s in spend if s["over"]]
    stale = []
    for engagement in instance.get("engagements") or []:
        status = eg_status(instance, engagement)
        if status["state"] != "current":
            stale.append(
                {
                    "eg_id": engagement.get("eg_id"),
                    "state": status["state"],
                    "reasons": status["reasons"],
                }
            )
    for invoice in invoices:
        status = iv_status(instance, invoice)
        if status["state"] != "current":
            stale.append(
                {
                    "iv_id": invoice.get("iv_id"),
                    "state": status["state"],
                    "reasons": status["reasons"],
                }
            )
    totals: dict[str, str] = {}
    for row in spend:
        currency = row["currency"] or ""
        totals[currency] = _sp_total(
            [totals.get(currency, "0.00"), row["invoiced"]]
        )
    return {
        "counts": by_status,
        "vendors": len(instance.get("vendors") or []),
        "spend": spend,
        "totals_by_currency": totals,
        "unbudgeted": unbudgeted,
        "over": over,
        "queue": sp_queue(instance),
        "stale": stale,
    }


def sp_snapshot(instance: dict, data: dict) -> dict:
    """Content hash a spend review binds (recomputed, never stored)."""
    from quality.records import evidence_fingerprint

    return {
        "content": _stable(
            {
                "vendors": instance.get("vendors"),
                "engagements": instance.get("engagements"),
                "invoices": instance.get("invoices"),
            }
        ),
        "evidence": evidence_fingerprint(data),
        "template_version": instance.get("template_version"),
    }


def sp_diff(instance: dict, old: int, new: int) -> dict:
    """Before/after comparison between two tracker versions."""
    versions = {v.get("v"): v for v in instance.get("versions") or []}
    if old not in versions or new not in versions:
        raise CaseError(f"versions {old}/{new} are not in this instance")
    changes = []
    before_vn = {v.get("vn_id"): v for v in versions[old].get("vendors") or []}
    after_vn = {v.get("vn_id"): v for v in versions[new].get("vendors") or []}
    for vn_id in sorted(set(before_vn) | set(after_vn)):
        if vn_id not in before_vn:
            changes.append({"vn_id": vn_id, "change": "added"})
            continue
        if vn_id not in after_vn:
            changes.append({"vn_id": vn_id, "change": "removed"})
            continue
        first, second = before_vn[vn_id], after_vn[vn_id]
        for key in ("name", "contact"):
            if first.get(key) != second.get(key):
                changes.append(
                    {
                        "vn_id": vn_id,
                        "change": key,
                        "before": first.get(key),
                        "after": second.get(key),
                    }
                )
    before_eg = {e.get("eg_id"): e for e in versions[old].get("engagements") or []}
    after_eg = {e.get("eg_id"): e for e in versions[new].get("engagements") or []}
    for eg_id in sorted(set(before_eg) | set(after_eg)):
        if eg_id not in before_eg:
            changes.append({"eg_id": eg_id, "change": "added"})
            continue
        if eg_id not in after_eg:
            changes.append({"eg_id": eg_id, "change": "removed"})
            continue
        first, second = before_eg[eg_id], after_eg[eg_id]
        for key in ("vn_id", "title", "budget", "currency"):
            if first.get(key) != second.get(key):
                changes.append(
                    {
                        "eg_id": eg_id,
                        "change": key,
                        "before": first.get(key),
                        "after": second.get(key),
                    }
                )
    before_iv = {i.get("iv_id"): i for i in versions[old].get("invoices") or []}
    after_iv = {i.get("iv_id"): i for i in versions[new].get("invoices") or []}
    for iv_id in sorted(set(before_iv) | set(after_iv)):
        if iv_id not in before_iv:
            changes.append({"iv_id": iv_id, "change": "added"})
            continue
        if iv_id not in after_iv:
            changes.append({"iv_id": iv_id, "change": "removed"})
            continue
        first, second = before_iv[iv_id], after_iv[iv_id]
        for key in (
            "eg_id", "label", "amount", "notes", "status", "resolution",
        ):
            if first.get(key) != second.get(key):
                changes.append(
                    {
                        "iv_id": iv_id,
                        "change": key,
                        "before": first.get(key),
                        "after": second.get(key),
                    }
                )
    return {"old": old, "new": new, "changes": changes}


def sp_export(
    instance: dict,
    data: dict,
    version: int | None = None,
) -> dict:
    """Versioned JSON report; historical exports stay frozen."""
    from quality.records import evidence_fingerprint

    versions = instance.get("versions") or []
    if version is None:
        vendors = instance.get("vendors") or []
        engagements = instance.get("engagements") or []
        invoices = instance.get("invoices") or []
        historical = False
    else:
        match = next((v for v in versions if v.get("v") == version), None)
        if match is None:
            raise CaseError(f"version {version} is not in this instance")
        vendors = match.get("vendors") or []
        engagements = match.get("engagements") or []
        invoices = match.get("invoices") or []
        historical = True
    out_invoices = []
    for invoice in invoices:
        limitations = []
        if not historical:
            limitations.extend(iv_status(instance, invoice)["reasons"])
        if invoice.get("status") in _SP_NEXT:
            limitations.append(
                f"status: {invoice.get('status')} "
                f"(next: {_SP_NEXT[invoice['status']]})"
            )
        out_invoices.append(
            {
                "iv_id": invoice.get("iv_id"),
                "eg_id": invoice.get("eg_id"),
                "label": invoice.get("label"),
                "amount": invoice.get("amount"),
                "notes": invoice.get("notes"),
                "status": invoice.get("status"),
                "resolution": invoice.get("resolution"),
                "limitations": limitations,
            }
        )
    summary = {} if historical else sp_summary(instance)
    review = instance.get("review") or {}
    return {
        "template": instance.get("template"),
        "template_version": instance.get("template_version"),
        "instance": instance.get("id"),
        "matter": instance.get("matter"),
        "title": instance.get("title"),
        "exported_version": version or (versions[-1].get("v") if versions else 1),
        "historical": historical,
        "evidence_fingerprint": evidence_fingerprint(data) if not historical else None,
        "summary": summary,
        "vendors": [
            {
                "vn_id": v.get("vn_id"),
                "name": v.get("name"),
                "contact": v.get("contact"),
            }
            for v in vendors
        ],
        "engagements": [
            {
                "eg_id": e.get("eg_id"),
                "vn_id": e.get("vn_id"),
                "title": e.get("title"),
                "budget": e.get("budget"),
                "currency": e.get("currency"),
            }
            for e in engagements
        ],
        "invoices": out_invoices,
        "review": {
            "decision": review.get("decision"),
            "notes": review.get("notes"),
            "qualifications": review.get("qualifications"),
            "at": review.get("at"),
            "snapshot": review.get("snapshot"),
            "scope": (
                "the reviewed snapshot only; later revisions need a new review"
                if review.get("decision") == "approved"
                else "no approval recorded"
            ),
        },
        "limits": [
            "synthetic demonstration: vendors, budgets, and "
            "invoices are reviewer-logged records",
            "totals are exact decimals per currency; "
            "currencies are never converted",
            "an unbudgeted engagement cannot be over or under "
            "budget",
        ]
        + (["historical version: liveness was not recomputed"] if historical else []),
    }


register(
    TEMPLATE_SP,
    snapshot=sp_snapshot,
    open=open_sp_instance,
    diff=sp_diff,
    export=sp_export,
    export_takes_root=False,
    count_key="invoices",
    review_keys=('vendors', 'engagements', 'invoices'),
)

__all__ = ['SP_CURRENCIES', 'SP_STATUS', '_SP_NEXT', '_SP_DECISIONS', '_sp_amount', '_sp_total', 'open_sp_instance', 'sp_get_vendor', 'sp_get_engagement', 'sp_get_invoice', '_sp_version', '_sp_touch', '_next_vn_id', '_next_eg_id', '_next_iv_id', '_vendor_pin', '_engagement_pin', 'add_vn', 'set_vn', 'add_eg', 'set_eg', 'add_iv', 'set_iv', 'decide_iv', 'eg_status', 'iv_status', 'sp_spend', 'sp_queue', 'sp_summary', 'sp_snapshot', 'sp_diff', 'sp_export']
