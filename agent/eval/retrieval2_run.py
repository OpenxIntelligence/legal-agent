"""Frozen retrieval benchmark runner (mechanics + metrics, no judgment).

Builds isolated per-matter roots from retrieval2_cases (fake
PDFs + page images + transcript cache through the real
document_text paths), runs every query through the real
search_corpus entry point, and grades ranks deterministically
against frozen judgments. Retrieval here is deterministic, so
one trial per query suffices; latency is measured, not gated.

Metrics per query: rank list, recall@5/10, reciprocal rank,
nDCG@10, required-complete, prohibited-free, abstain-empty
(with restricted/unindexed listing checks where applicable).
Aggregates by split (dev/heldout) and category, plus p50/p95
latency, index build time, and corpus fingerprints.

Run with: cd agent && .venv/bin/python -m eval.retrieval2_run
"""

from __future__ import annotations

import hashlib
import json
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_kit import document_text  # noqa: E402
from eval import retrieval2_cases as cases  # noqa: E402

REPORT_KEYS = frozenset(
    {
        "cases_version",
        "corpus_fingerprints",
        "client",
        "semantic_quality",
        "index",
        "latency_ms",
        "queries",
        "aggregate",
        "complete",
    }
)

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
)


def fingerprint_matter(matter: str) -> str:
    """Stable sha over canonical corpus text (docs + pages + meta)."""
    corpus = cases.CORPORA[matter]
    canonical = json.dumps(
        {"docs": corpus["docs"], "meta": corpus["meta"],
         "restricted": sorted(corpus["restricted"]),
         "unindexed": sorted(corpus["unindexed"]),
         "supersedes": corpus["supersedes"]},
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def build_matter_root(matter: str, parent: Path) -> tuple[Path, dict]:
    """Isolated root with inbox PDFs, page images, transcript cache.

    Returns (root, stats). Unindexed docs get files but no cache
    entry, exactly like untranscribed real documents.
    """
    started = time.perf_counter()
    root = parent / matter
    inbox = root / "workspace/inbox"
    inbox.mkdir(parents=True)
    (inbox / "pages").mkdir(exist_ok=True)
    corpus = cases.CORPORA[matter]
    for doc_id, pages in corpus["docs"].items():
        (inbox / f"{doc_id}.pdf").write_bytes(
            ("\n".join(pages)).encode()
        )
        for n in range(1, len(pages) + 1):
            (inbox / "pages" / f"{doc_id}-p{n}.png").write_bytes(
                PNG + doc_id.encode() + bytes([n])
            )
    cached = 0
    for doc_id, pages in corpus["docs"].items():
        if doc_id in corpus["unindexed"]:
            continue
        document = document_text.get_document(root, doc_id)
        document_text._save_text(
            root, document,
            [{"page": n + 1, "text": text}
             for n, text in enumerate(pages)],
            "retrieval2-fixture",
        )
        cached += 1
    storage = sum(
        p.stat().st_size for p in (root / "workspace").rglob("*")
        if p.is_file()
    )
    return root, {
        "docs": len(corpus["docs"]), "pages": sum(
            len(p) for p in corpus["docs"].values()),
        "cached": cached,
        "build_ms": (time.perf_counter() - started) * 1000,
        "storage_bytes": storage,
    }


def ranks_of(result: dict) -> list[tuple[str, int]]:
    """Returned (doc, page) pairs in rank order."""
    out = []
    for document in result.get("documents", []):
        for passage in document.get("passages", []):
            out.append((document["document_id"], passage["page"]))
    return out


def reciprocal_rank(ranks: list, expected: set) -> float:
    for i, pair in enumerate(ranks, start=1):
        if pair in expected:
            return 1.0 / i
    return 0.0


def ndcg_at(ranks: list, expected: set, k: int = 10) -> float:
    import math

    gains = [1.0 if pair in expected else 0.0 for pair in ranks[:k]]
    dcg = sum(g / math.log2(i + 2) for i, g in enumerate(gains))
    ideal = sum(1.0 / math.log2(i + 2)
                for i in range(min(len(expected), k)))
    return dcg / ideal if ideal else 1.0


def grade_query(spec: tuple, ranks: list, result: dict) -> dict:
    """Deterministic grade of one ranked list against frozen truth."""
    _, _, _, category, expected, required, prohibited, abstain, _ = spec
    expected_set = set(expected)
    top5, top10 = ranks[:5], ranks[:10]
    grade: dict = {
        "id": spec[0], "category": category,
        "returned": len(ranks),
        "recall_at_5": (len(expected_set & set(top5)) / len(expected_set)
        if expected_set else None),
        "recall_at_10": (len(expected_set & set(top10)) / len(expected_set)
        if expected_set else None),
        "mrr": reciprocal_rank(ranks, expected_set)
        if expected_set else None,
        "ndcg_at_10": ndcg_at(ranks, expected_set)
        if expected_set else None,
        "required_complete": all(p in top10 for p in required),
        "prohibited_free": not any(p in top10 for p in prohibited),
        "empty": not ranks,
    }
    if category == "restricted":
        grade["restricted_listed"] = bool(result.get("restricted"))
    if category == "unindexed":
        grade["unindexed_listed"] = bool(result.get("unindexed"))
    if abstain:
        ok = not ranks and grade["prohibited_free"]
        if category == "restricted":
            ok = ok and grade["restricted_listed"]
        if category == "unindexed":
            ok = ok and grade["unindexed_listed"]
        grade["complete"] = ok
    else:
        grade["complete"] = bool(
            grade["required_complete"] and grade["prohibited_free"]
        )
    return grade


def run_all() -> dict:
    """Grade every query on freshly built isolated roots."""
    parent = Path(tempfile.mkdtemp(prefix="retrieval2-"))
    try:
        roots, index, latencies = {}, {}, []
        for matter in cases.CORPORA:
            root, stats = build_matter_root(matter, parent)
            roots[matter] = root
            index[matter] = stats
        queries = []
        for spec in cases.QUERIES:
            _, matter = spec[0], spec[1]
            flagged = set(cases.CORPORA[matter]["restricted"])
            started = time.perf_counter()
            result = document_text.search_corpus(
                roots[matter], spec[2], flagged=flagged,
                meta=cases.CORPORA[matter]["meta"])
            latencies.append((time.perf_counter() - started) * 1000)
            grade = grade_query(spec, ranks_of(result), result)
            grade["latency_ms"] = latencies[-1]
            grade["split"] = spec[8]
            queries.append(grade)
    finally:
        shutil.rmtree(parent, ignore_errors=True)
    aggregate = summarize(queries)
    lat_sorted = sorted(latencies)
    return {
        "cases_version": cases.CASES_VERSION,
        "corpus_fingerprints": {
            matter: fingerprint_matter(matter)
            for matter in cases.CORPORA
        },
        "client": "deterministic",
        "semantic_quality": (
            "not measured: ranks graded against frozen relevance "
            "judgments; this proves retrieval mechanics, not "
            "legal-answer quality"
        ),
        "index": index,
        "latency_ms": {
            "p50": statistics.median(lat_sorted),
            "p95": lat_sorted[max(0, int(len(lat_sorted) * 0.95) - 1)],
            "count": len(lat_sorted),
        },
        "queries": queries,
        "aggregate": aggregate,
        "complete": all(q["complete"] for q in queries),
    }


def summarize(queries: list) -> dict:
    """Mean metrics overall, by split, and by category."""

    def means(items: list) -> dict:
        def avg(key: str):
            values = [q[key] for q in items if q[key] is not None]
            return sum(values) / len(values) if values else None

        return {
            "n": len(items),
            "recall_at_5": avg("recall_at_5"),
            "recall_at_10": avg("recall_at_10"),
            "mrr": avg("mrr"),
            "ndcg_at_10": avg("ndcg_at_10"),
            "required_complete": sum(
                1 for q in items if q["required_complete"]) / len(items),
            "prohibited_free": sum(
                1 for q in items if q["prohibited_free"]) / len(items),
            "complete": sum(1 for q in items if q["complete"]) / len(items),
        }

    return {
        "overall": means(queries),
        "by_split": {
            split: means([q for q in queries if q["split"] == split])
            for split in ("dev", "heldout")
        },
        "by_category": {
            category: means([q for q in queries
                             if q["category"] == category])
            for category in sorted({q["category"] for q in queries})
        },
    }


def main() -> None:
    print(json.dumps(run_all(), indent=2))


if __name__ == "__main__":
    main()
