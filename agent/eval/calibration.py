"""Judge calibration gate: kappa against human labels before promotion.

No LLM judge grades anything in this repo yet (scripted plumbing
only). Before any judge is promoted to grading, this gate requires
Cohen's kappa >= 0.6 against human labels on a held-out slice,
re-checked on every rubric or judge change:

    cd agent && .venv/bin/python -m eval.calibration J JUDGE HUMAN

Both files are JSONL with {"id", "label"} per line; labels are
pass/fail/unknown (case-insensitive). Unknown pairs are excluded
from kappa but counted: a judge that abstains too often fails.
Exit 0 when calibrated, 1 when not, 2 on unusable input.

The agent cannot supply the human side: labels in HUMAN must come
from a person reading the artifacts, or kappa measures nothing.
See docs/judge-calibration.md for the sampling protocol.

The second half of this module scores probability outputs
(Brier/ECE/bootstrap) once a judge is promoted; it fits
nothing and refuses probabilistic claims below MIN_LABELS
independent labels.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

#: Minimum kappa for promotion (substantial agreement).
THRESHOLD = 0.6

#: Maximum abstention rate: an always-unsure judge is uncalibrated.
MAX_UNKNOWN = 0.2

#: Minimum paired pass/fail labels: kappa on tiny samples is noise.
MIN_N = 20

LABELS = ("pass", "fail")


def cohen_kappa(first: list[str], second: list[str]) -> float:
    """Cohen's kappa for two equal-length pass/fail label lists."""
    if len(first) != len(second) or not first:
        raise ValueError("need two non-empty label lists of equal length")
    table = {(a, b): 0 for a in LABELS for b in LABELS}
    for a, b in zip(first, second):
        if a not in LABELS or b not in LABELS:
            raise ValueError(f"labels must be pass/fail, got {a!r}/{b!r}")
        table[(a, b)] += 1
    total = len(first)
    observed = (table[("pass", "pass")] + table[("fail", "fail")]) / total
    expected = (
        (table[("pass", "pass")] + table[("pass", "fail")])
        * (table[("pass", "pass")] + table[("fail", "pass")])
        + (table[("fail", "pass")] + table[("fail", "fail")])
        * (table[("pass", "fail")] + table[("fail", "fail")])
    ) / (total * total)
    if expected == 1.0:  # single-class slice: agreement is the answer
        return 1.0 if observed == 1.0 else 0.0
    return (observed - expected) / (1.0 - expected)


def load_labels(path: str) -> dict[str, str]:
    """Read a JSONL label file into {id: label}, validating shape."""
    labels: dict[str, str] = {}
    try:
        lines = Path(path).read_text().splitlines()
    except OSError as error:
        raise ValueError(f"cannot read {path}: {error}") from None
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            raise ValueError(f"{path}:{number}: not JSON") from None
        if not isinstance(row, dict):
            raise ValueError(f"{path}:{number}: need an object per line")
        item = row.get("id")
        label = str(row.get("label", "")).strip().lower()
        if not item or not isinstance(item, str):
            raise ValueError(f"{path}:{number}: need a string id")
        if label not in ("pass", "fail", "unknown"):
            raise ValueError(
                f"{path}:{number}: label must be pass/fail/unknown, "
                f"got {row.get('label')!r}"
            )
        if item in labels:
            raise ValueError(f"{path}:{number}: duplicate id {item!r}")
        labels[item] = label
    return labels


def calibrate(
    judge: dict[str, str],
    human: dict[str, str],
    threshold: float = THRESHOLD,
    max_unknown: float = MAX_UNKNOWN,
    min_n: int = MIN_N,
) -> dict:
    """Kappa report for paired judge/human labels; strict on pairing."""
    only_judge = sorted(set(judge) - set(human))
    only_human = sorted(set(human) - set(judge))
    if only_judge or only_human:
        raise ValueError(
            "label ids must match exactly: "
            f"judge-only {only_judge[:5]}, human-only {only_human[:5]}"
            + (" (capped)" if len(only_judge) + len(only_human) > 10 else "")
        )
    paired = sorted(set(judge) & set(human))
    unknowns = [i for i in paired if "unknown" in (judge[i], human[i])]
    known = [i for i in paired if i not in unknowns]
    unknown_rate = len(unknowns) / len(paired) if paired else 1.0
    disagreements = [i for i in known if judge[i] != human[i]]
    kappa = (
        cohen_kappa([judge[i] for i in known], [human[i] for i in known])
        if known
        else 0.0
    )
    reasons = []
    if len(known) < min_n:
        reasons.append(f"only {len(known)} paired labels, need {min_n}")
    if unknown_rate > max_unknown:
        reasons.append(
            f"unknown rate {unknown_rate:.2f} exceeds {max_unknown:.2f}"
        )
    if kappa < threshold:
        reasons.append(f"kappa {kappa:.3f} below {threshold:.2f}")
    return {
        "kappa": round(kappa, 4),
        "n": len(known),
        "unknowns": len(unknowns),
        "unknown_rate": round(unknown_rate, 4),
        "agreement": round(
            (len(known) - len(disagreements)) / len(known) if known else 0.0,
            4,
        ),
        "disagreements": disagreements,
        "threshold": threshold,
        "passed": not reasons,
        "reasons": reasons,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="judge calibration gate")
    parser.add_argument("judge", help="judge labels (.jsonl)")
    parser.add_argument("human", help="human labels (.jsonl)")
    parser.add_argument("--threshold", type=float, default=THRESHOLD)
    parser.add_argument("--max-unknown", type=float, default=MAX_UNKNOWN)
    parser.add_argument("--min-n", type=int, default=MIN_N)
    args = parser.parse_args(argv)
    try:
        report = calibrate(
            load_labels(args.judge),
            load_labels(args.human),
            threshold=args.threshold,
            max_unknown=args.max_unknown,
            min_n=args.min_n,
        )
    except ValueError as error:
        print(f"calibration: error: {error}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())


# ---- probability calibration (Brier/ECE/bootstrap) ----
#
# Added alongside the kappa judge gate: once a judge is
# promoted, its probability outputs still need scoring-rule
# evaluation on independent labels before any claim ships.
#
# Evaluates (prediction, outcome) pairs with Brier score,
# expected calibration error, reliability tables, and bootstrap
# percentile intervals (no normality assumed). Labels arrive as
# dicts {prediction, outcome, source, group?}; sources naming
# the agent itself are rejected loudly — calibration labels
# must be independent of the agent's judgments (human review,
# gold fixtures, held-out adjudication).
#
# The harness fits NOTHING. Platt/isotonic fitting waits for
# real label volume; below MIN_LABELS independent labels the
# verdict is INADEQUATE and any probabilistic claim stays
# deferred. Intended prediction targets (labels pending):
# passage-supports-claim and draft-passes-review.

import math
import random

MIN_LABELS = 30
AGENT_SOURCES = frozenset({"agent", "self", "model"})


def _checked(pairs: list) -> list[dict]:
    """Validated label dicts, or ValueError on agent-sourced labels."""
    checked = []
    for pair in pairs:
        if not isinstance(pair, dict):
            raise ValueError("labels must be dicts")
        source = str(pair.get("source", "") or "").lower()
        if source in AGENT_SOURCES:
            raise ValueError(
                "independent labels required: "
                f"source {source!r} is the agent itself"
            )
        prediction = pair.get("prediction")
        outcome = pair.get("outcome")
        if not isinstance(prediction, (int, float)) or not (
            0.0 <= prediction <= 1.0
        ):
            raise ValueError("prediction must be a probability in [0, 1]")
        if outcome not in (0, 1, True, False):
            raise ValueError("outcome must be 0/1")
        checked.append(
            {
                "prediction": float(prediction),
                "outcome": 1 if outcome else 0,
                "source": source or "unknown",
                "group": str(pair.get("group", "") or "all"),
            }
        )
    return checked


def brier_score(pairs: list) -> float:
    """Mean squared error of probabilistic predictions."""
    checked = _checked(pairs)
    if not checked:
        return 0.0
    return sum(
        (pair["prediction"] - pair["outcome"]) ** 2 for pair in checked
    ) / len(checked)


def reliability_table(pairs: list, bins: int = 10) -> list[dict]:
    """Per-bin count, mean prediction, and mean outcome.

    Equal-width bins over [0, 1]; the top edge belongs to the
    last bin. Empty bins report None means.
    """
    checked = _checked(pairs)
    edges = [n / bins for n in range(bins + 1)]
    table = []
    for lower, upper in zip(edges, edges[1:]):
        last = upper >= 1.0
        members = [
            pair
            for pair in checked
            if (lower <= pair["prediction"] < upper)
            or (last and pair["prediction"] == upper)
        ]
        if not members:
            table.append(
                {"bin": [lower, upper], "n": 0,
                 "mean_pred": None, "mean_outcome": None}
            )
            continue
        table.append(
            {
                "bin": [lower, upper],
                "n": len(members),
                "mean_pred": sum(p["prediction"] for p in members)
                / len(members),
                "mean_outcome": sum(p["outcome"] for p in members)
                / len(members),
            }
        )
    return table


def expected_calibration_error(pairs: list, bins: int = 10) -> float:
    """Mean |accuracy - confidence| weighted by bin share."""
    checked = _checked(pairs)
    if not checked:
        return 0.0
    total = 0.0
    for row in reliability_table(checked, bins):
        if not row["n"]:
            continue
        total += (
            row["n"] / len(checked)
        ) * abs(row["mean_pred"] - row["mean_outcome"])
    return total


def bootstrap_ci(
    metric,
    pairs: list,
    *,
    resamples: int = 1000,
    confidence: float = 0.95,
    seed: int = 7,
) -> dict:
    """Percentile interval for a metric via seeded resampling.

    Deterministic for a fixed seed. No distribution assumed;
    with tiny n the interval is wide, which is the point.
    """
    checked = _checked(pairs)
    if not checked:
        return {"estimate": 0.0, "low": 0.0, "high": 0.0, "n": 0}
    rng = random.Random(seed)
    draws = sorted(
        metric([rng.choice(checked) for _ in checked])
        for _ in range(max(1, int(resamples)))
    )
    tail = (1.0 - confidence) / 2.0
    low_at = min(len(draws) - 1, int(tail * len(draws)))
    high_at = min(len(draws) - 1, int((1.0 - tail) * len(draws)))
    return {
        "estimate": metric(checked),
        "low": draws[low_at],
        "high": draws[high_at],
        "n": len(checked),
    }


def by_group(pairs: list) -> dict[str, dict]:
    """Per-group n, outcome rate, outcome std, and Brier score."""
    checked = _checked(pairs)
    groups: dict[str, list[dict]] = {}
    for pair in checked:
        groups.setdefault(pair["group"], []).append(pair)
    summary = {}
    for name in sorted(groups):
        members = groups[name]
        rate = sum(p["outcome"] for p in members) / len(members)
        variance = sum(
            (p["outcome"] - rate) ** 2 for p in members
        ) / len(members)
        summary[name] = {
            "n": len(members),
            "rate": rate,
            "std": math.sqrt(variance),
            "brier": brier_score(members),
        }
    return summary


def summarize(pairs: list, *, bins: int = 10) -> dict:
    """Adequacy verdict plus the full metric panel.

    Below MIN_LABELS independent labels the verdict is
    INADEQUATE and probabilistic claims stay deferred no
    matter how good the point estimates look. At or above,
    the verdict is SUFFICIENT-FOR-FITTING — fitting plus a
    held-out protocol are still required, not waived.
    """
    checked = _checked(pairs)
    sources = sorted({pair["source"] for pair in checked})
    if len(checked) < MIN_LABELS:
        verdict = (
            f"INADEQUATE: {len(checked)} independent labels, "
            f"need >={MIN_LABELS} for any probabilistic claim"
        )
    else:
        verdict = (
            f"SUFFICIENT-FOR-FITTING: {len(checked)} labels; "
            "fit on train, verify on held-out before any claim"
        )
    return {
        "verdict": verdict,
        "n": len(checked),
        "sources": sources,
        "brier": brier_score(checked),
        "ece": expected_calibration_error(checked, bins),
        "brier_ci": bootstrap_ci(brier_score, checked),
        "groups": by_group(checked),
    }
