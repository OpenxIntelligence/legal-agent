"""Calculation known-answer fixtures: dates, holidays, money, interest.

MUST: (id, op, inputs, expected result). Hand-verified answers
covering leap days, month ends, negative offsets, start-exclusive
business days across weekends and explicit holidays, the 0.1+0.2
float trap, rounding, and both interest bases.

MUST_REFUSE: (id, op, inputs). Malformed calls — bad dates,
impossible days, negative business counts, bad holiday entries,
non-decimal money, bad interest basis, unknown ops, and the
missing-holidays omission — that must raise instead of guessing.

Known limits (documented, not gated): weekends are Sat/Sun only
(other closures arrive as holidays); interest is simple only;
money rounds HALF_UP to cents with no currency tracking.
"""

from __future__ import annotations

# (variant id, op, inputs, expected result string).
MUST = (
    ("leap-day", "add_days", {"start": "2024-02-28", "days": 1}, "2024-02-29"),
    ("non-leap", "add_days", {"start": "2023-02-28", "days": 1}, "2023-03-01"),
    ("month-end", "add_days", {"start": "2026-01-31", "days": 1}, "2026-02-01"),
    ("zero-days", "add_days", {"start": "2026-01-01", "days": 0}, "2026-01-01"),
    ("negative-days", "add_days", {"start": "2026-03-01", "days": -1}, "2026-02-28"),
    ("str-days", "add_days", {"start": "2026-01-01", "days": "30"}, "2026-01-31"),
    ("biz-fri-plus-1", "add_business_days",
     {"start": "2026-01-02", "days": 1, "holidays": []}, "2026-01-05"),
    ("biz-week", "add_business_days",
     {"start": "2026-01-05", "days": 5, "holidays": []}, "2026-01-12"),
    ("biz-holiday", "add_business_days",
     {"start": "2026-01-02", "days": 1, "holidays": ["2026-01-05"]},
     "2026-01-06"),
    ("biz-zero", "add_business_days",
     {"start": "2026-01-05", "days": 0, "holidays": []}, "2026-01-05"),
    ("between", "days_between",
     {"start": "2026-01-01", "end": "2026-01-31"}, "30"),
    ("between-reversed", "days_between",
     {"start": "2026-01-31", "end": "2026-01-01"}, "-30"),
    ("between-same", "days_between",
     {"start": "2026-01-01", "end": "2026-01-01"}, "0"),
    ("money-add", "money_add", {"operands": ["10.00", "0.05"]}, "10.05"),
    ("money-float-trap", "money_add", {"operands": ["0.1", "0.2"]}, "0.30"),
    ("money-sub", "money_subtract",
     {"amount": "10.00", "subtrahend": "0.07"}, "9.93"),
    ("money-mul", "money_multiply",
     {"amount": "10.00", "factor": "3"}, "30.00"),
    ("money-mul-round", "money_multiply",
     {"amount": "10.00", "factor": "0.333"}, "3.33"),
    ("interest-360", "interest",
     {"principal": "10000", "rate_pct": "5", "days": 36, "basis": 360},
     "50.00"),
    ("interest-365", "interest",
     {"principal": "10000", "rate_pct": "5", "days": 36, "basis": 365},
     "49.32"),
    ("interest-zero-days", "interest",
     {"principal": "10000", "rate_pct": "5", "days": 0}, "0.00"),
)

# (variant id, op, inputs). Each must raise ComputeError.
MUST_REFUSE = (
    ("bad-month", "add_days", {"start": "2026-13-01", "days": 1}),
    ("bad-shape", "add_days", {"start": "01/02/2026", "days": 1}),
    ("no-leap-2023", "add_days", {"start": "2023-02-29", "days": 1}),
    ("bool-days", "add_days", {"start": "2026-01-01", "days": True}),
    ("text-days", "add_days", {"start": "2026-01-01", "days": "soon"}),
    ("negative-biz", "add_business_days",
     {"start": "2026-01-05", "days": -1, "holidays": []}),
    ("missing-holidays", "add_business_days",
     {"start": "2026-01-05", "days": 1}),
    ("bad-holiday", "add_business_days",
     {"start": "2026-01-05", "days": 1, "holidays": ["Jan 6"]}),
    ("bad-money", "money_add", {"operands": ["10.00", "abc"]}),
    ("empty-money", "money_add", {"operands": []}),
    ("bad-basis", "interest",
     {"principal": "100", "rate_pct": "5", "days": 10, "basis": 366}),
    ("negative-interest-days", "interest",
     {"principal": "100", "rate_pct": "5", "days": -3}),
    ("unknown-op", "compound", {"start": "2026-01-01"}),
)
