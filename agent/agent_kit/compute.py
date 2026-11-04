"""Deterministic date and money arithmetic (Item 5, G5).

Pure stdlib functions behind the calculate tool: deadlines,
day counts, and decimal-safe money math the model must never
do in prose. Conventions, fixed and documented:

- Dates are strict YYYY-MM-DD (the same shape as authority
  effective dates). Anything else is refused, naming the flaw.
- add_days counts calendar days from the start date:
  2026-01-01 + 1 = 2026-01-02.
- add_business_days excludes the start date itself (the
  triggering day never counts) and skips Saturdays, Sundays,
  and the caller-supplied holidays: 2026-01-02 (Fri) + 1 =
  2026-01-05 (Mon). Weekends are Sat/Sun only; jurisdictions
  with other non-business days must pass them as holidays.
- Money is decimal strings in, "0.00"-shaped strings out
  (ROUND_HALF_UP). Floats never touch money.
- Simple interest is principal * rate_pct/100 * days/basis
  with basis 360 or 365, stated by the caller.

replay(op, inputs) is the single dispatch the tool and the
filing gate share: the tool records through it, the gate
re-runs it to catch tampered or malformed computation records.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

OPS = (
    "add_days",
    "add_business_days",
    "days_between",
    "money_add",
    "money_subtract",
    "money_multiply",
    "interest",
)

_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_CENT = Decimal("0.01")


class ComputeError(ValueError):
    """A computation was refused: bad date, money, or inputs."""


def parse_date(value: object) -> date:
    """Strict YYYY-MM-DD to date, else ComputeError naming the flaw."""
    text = str(value or "").strip()
    match = _DATE_RE.match(text)
    if not match:
        raise ComputeError(
            f"{text!r} is not a date: use YYYY-MM-DD (the law's date)"
        )
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        raise ComputeError(
            f"{text!r} is not a real calendar date"
        ) from None


def _parse_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ComputeError(f"{name} must be a whole number, got {value!r}")
    try:
        return int(str(value).strip())
    except ValueError:
        raise ComputeError(f"{name} must be a whole number, got {value!r}") from None


def add_days(start: object, days: object) -> str:
    """Calendar-day arithmetic: start + days as YYYY-MM-DD."""
    return (parse_date(start) + timedelta(days=_parse_int(days, "days"))).isoformat()


def add_business_days(
    start: object, days: object, holidays: object = ()
) -> str:
    """Business-day arithmetic: start-exclusive, Sat/Sun + holidays skipped.

    `holidays` is an explicit list of YYYY-MM-DD strings (possibly
    empty, never omitted silently): jurisdiction-specific closures
    arrive as input, not as model memory. Each entry is validated.
    """
    if not isinstance(holidays, (list, tuple)):
        raise ComputeError(
            f"holidays must be a list of YYYY-MM-DD dates, got {holidays!r}"
        )
    closed = set()
    for entry in holidays:
        closed.add(parse_date(entry).isoformat())
    total = _parse_int(days, "days")
    if total < 0:
        raise ComputeError(f"business days must not be negative, got {total}")
    current = parse_date(start)
    added = 0
    while added < total:
        current += timedelta(days=1)
        if current.weekday() >= 5 or current.isoformat() in closed:
            continue
        added += 1
    return current.isoformat()


def days_between(start: object, end: object) -> int:
    """Whole days from start to end (negative when end precedes start)."""
    return (parse_date(end) - parse_date(start)).days


def _parse_money(value: object, name: str) -> Decimal:
    try:
        return Decimal(str(value or "").strip())
    except (InvalidOperation, ValueError, AttributeError):
        raise ComputeError(
            f"{name} must be a decimal amount like '1234.56', got {value!r}"
        ) from None


def _fmt_money(amount: Decimal) -> str:
    return str(amount.quantize(_CENT, rounding=ROUND_HALF_UP))


def money_add(operands: object) -> str:
    """Sum of decimal amounts."""
    if not isinstance(operands, (list, tuple)) or not operands:
        raise ComputeError("money_add needs a non-empty list of amounts")
    total = Decimal("0")
    for operand in operands:
        total += _parse_money(operand, "amount")
    return _fmt_money(total)


def money_subtract(amount: object, subtrahend: object) -> str:
    """Difference of two decimal amounts."""
    return _fmt_money(
        _parse_money(amount, "amount") - _parse_money(subtrahend, "subtrahend")
    )


def money_multiply(amount: object, factor: object) -> str:
    """A decimal amount times a decimal factor (count, share, rate)."""
    return _fmt_money(
        _parse_money(amount, "amount") * _parse_money(factor, "factor")
    )


def interest(
    principal: object, rate_pct: object, days: object, basis: object = 365
) -> str:
    """Simple interest: principal * rate_pct/100 * days/basis."""
    count = _parse_int(days, "days")
    if count < 0:
        raise ComputeError(f"interest days must not be negative, got {count}")
    base = _parse_int(basis, "basis")
    if base not in (360, 365):
        raise ComputeError(f"interest basis must be 360 or 365, got {base}")
    amount = (
        _parse_money(principal, "principal")
        * _parse_money(rate_pct, "rate_pct")
        / Decimal("100")
        * Decimal(count)
        / Decimal(base)
    )
    return _fmt_money(amount)


def replay(op: str, inputs: dict) -> str:
    """Re-run one recorded computation; the tool and gate share this.

    Raises ComputeError for an unknown op or malformed inputs, so a
    tampered or half-written computation record fails loudly.
    """
    if not isinstance(inputs, dict):
        raise ComputeError("computation inputs must be an object")
    if op == "add_days":
        return add_days(inputs.get("start"), inputs.get("days"))
    if op == "add_business_days":
        if "holidays" not in inputs:
            raise ComputeError(
                "add_business_days needs explicit holidays "
                "(possibly an empty list)"
            )
        return add_business_days(
            inputs.get("start"), inputs.get("days"), inputs.get("holidays")
        )
    if op == "days_between":
        return str(days_between(inputs.get("start"), inputs.get("end")))
    if op == "money_add":
        return money_add(inputs.get("operands"))
    if op == "money_subtract":
        return money_subtract(inputs.get("amount"), inputs.get("subtrahend"))
    if op == "money_multiply":
        return money_multiply(inputs.get("amount"), inputs.get("factor"))
    if op == "interest":
        return interest(
            inputs.get("principal"), inputs.get("rate_pct"),
            inputs.get("days"), inputs.get("basis", 365),
        )
    raise ComputeError(f"unknown computation {op!r}: must be one of {', '.join(OPS)}")
