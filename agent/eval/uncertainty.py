"""Uncertainty interfaces: rates with teeth, calibration with gates.

Four measurable targets, kept separate because they fail
separately:

- retrieval_relevance: is a returned passage actually
  relevant? Labels exist: the frozen retrieval2 judgments
  (human-authored, independent of the retriever).
- citation_support: does a quote support its claim? Labels
  need humans reading quote-vs-claim pairs. None yet.
- issue_coverage: did the run address every material issue?
  Labels need independent review of run records. None yet.
- reviewer_correctness: did the finished work pass review?
  Labels need reviewer verdicts through the kappa gate
  (eval/calibration.py). None yet.

What this module does: empirical rates with standard
deviations and seeded bootstrap intervals, plus an
abstention precision/recall interface — all from OUTCOMES,
never from rescaled BM25/PageRank scores. A rank is not a
probability, and nothing here manufactures one.

What it refuses: any probabilistic claim below
MIN_LABELS independent labels (see calibration.summarize).
Unmeasured targets report INADEQUATE with their label count,
not zeros dressed as knowledge.
"""

from __future__ import annotations

import math
import random

from eval import calibration

TARGETS = (
    "retrieval_relevance",
    "citation_support",
    "issue_coverage",
    "reviewer_correctness",
)


def rate_ci(
    outcomes: list[int],
    *,
    resamples: int = 1000,
    confidence: float = 0.95,
    seed: int = 7,
) -> dict:
    """Rate, std, and a seeded percentile interval over 0/1 outcomes."""
    clean = [1 if outcome else 0 for outcome in outcomes]
    if not clean:
        return {"rate": 0.0, "std": 0.0, "low": 0.0, "high": 0.0, "n": 0}
    rate = sum(clean) / len(clean)
    variance = sum((o - rate) ** 2 for o in clean) / len(clean)
    rng = random.Random(seed)
    draws = sorted(
        sum(rng.choice(clean) for _ in clean) / len(clean)
        for _ in range(max(1, int(resamples)))
    )
    tail = (1.0 - confidence) / 2.0
    low_at = min(len(draws) - 1, int(tail * len(draws)))
    high_at = min(len(draws) - 1, int((1.0 - tail) * len(draws)))
    return {
        "rate": rate,
        "std": math.sqrt(variance),
        "low": draws[low_at],
        "high": draws[high_at],
        "n": len(clean),
    }


def abstention(pairs: list[tuple]) -> dict:
    """Abstention quality from (should_abstain, did_abstain) pairs.

    Precision: of the times the system stayed silent, how often
    was silence correct? Recall: of the times it should have
    stayed silent, how often did it? With CIs on both.
    """
    should = [1 if s else 0 for s, _ in pairs]
    did = [1 if d else 0 for _, d in pairs]
    correct_silence = [s and d for s, d in zip(should, did)]
    precision = rate_ci(
        [c for c, d in zip(correct_silence, did) if d]
    )
    recall = rate_ci([c for c, s in zip(correct_silence, should) if s])
    return {
        "precision": precision,
        "recall": recall,
        "n": len(pairs),
        "silences": sum(did),
        "should_silences": sum(should),
    }


def summarize_retrieval(report: dict, abstain_ids) -> dict:
    """Uncertainty panel over one retrieval2 runner report.

    abstain_ids names the queries whose truth says nothing is
    relevant (read from the cases module by the caller, so this
    module stays decoupled from any one benchmark version).
    Binary outcomes per query: complete, required_complete,
    prohibited_free — plus the abstention interface over
    (should_abstain, returned_empty).
    """
    queries = report.get("queries", []) or []
    abstain_ids = set(abstain_ids or [])
    panel = {}
    for key in ("complete", "required_complete", "prohibited_free"):
        panel[key] = rate_ci(
            [q.get(key, False) for q in queries]
        )
    panel["abstention"] = abstention(
        [
            (q.get("id") in abstain_ids, q.get("empty", False))
            for q in queries
        ]
    )
    by_split = {}
    for split in ("dev", "heldout"):
        part = [q for q in queries if q.get("split") == split]
        by_split[split] = {
            "complete": rate_ci([q.get("complete", False) for q in part]),
            "n": len(part),
        }
    panel["by_split"] = by_split
    return panel


def calibration_status(target: str, labels: list) -> dict:
    """Adequacy verdict for a target's independent labels.

    labels are (prediction, outcome, source) pairs for the
    probability harness, or [] when none exist. Unknown targets
    are refused: silently scoring a mistyped target would be
    worse than useless.
    """
    if target not in TARGETS:
        raise ValueError(f"unknown uncertainty target {target!r}")
    pairs = [
        {"prediction": prediction, "outcome": outcome, "source": source}
        for prediction, outcome, source in labels
    ]
    summary = calibration.summarize(pairs)
    return {"target": target, **summary}
