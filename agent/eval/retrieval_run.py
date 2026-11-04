"""Retrieval baseline benchmark runner (offline, deterministic).

Grades the FINAL STATE of document_text.search_corpus over fixed
queries on the shared tiny case: every MUST_HIT variant must
return exactly its expected (doc, page) hits, every PRECISION
query must stay silent, restricted/unindexed documents must be
listed rather than searched, and every PARAPHRASE variant must
return exactly its expected hit via the BM25 fallback. Run with:

    cd agent && .venv/bin/python -m eval.retrieval_run

Reports carry the benchmark schema (case, mode, client,
semantic_quality, complete, layer, notes). This runner is
standalone: it is not part of the regression gate (which pins
six benchmarks); tests/test_retrieval.py asserts its floors.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_kit import document_text  # noqa: E402
from agent_kit.tools.analyze_document import analyze  # noqa: E402
from eval import retrieval_cases as cases  # noqa: E402
from tests import support  # noqa: E402

REPORT_KEYS = frozenset(
    {
        "case",
        "mode",
        "client",
        "semantic_quality",
        "complete",
        "layer",
        "notes",
    }
)

NOT_MEASURED = (
    "not measured: fixed queries over a fixed 4-doc single-page "
    "corpus; the benchmark measures normalized-substring recall, "
    "BM25 fallback recall, and silence, not ranking quality, "
    "multi-page behavior, or live-model querying."
)


def _report(variant: str, mode: str, complete: bool, notes: str) -> dict:
    return {
        "case": variant,
        "mode": mode,
        "client": "scripted",
        "semantic_quality": NOT_MEASURED,
        "complete": complete,
        "layer": "retrieval",
        "notes": notes,
    }


def _hits(result: dict) -> list:
    found = []
    for doc in result.get("documents") or []:
        for passage in doc.get("passages") or []:
            found.append((doc.get("document_id"), passage.get("page")))
    return sorted(found)


def _build_corpus(analyze_ids: list) -> tuple:
    """A tiny analyzed corpus; caller must cleanup the temp dir."""
    root, tmp = support.make_root()
    fake = support.scripted(root, [])
    if analyze_ids:
        analyze(fake.client, root, list(analyze_ids), "baseline")
    return root, tmp


def run_all() -> list:
    """Every probe query once, in listed order."""
    reports = []
    root, tmp = _build_corpus(sorted(support.INITIAL))
    try:
        for variant, query, expected in cases.MUST_HIT:
            result = document_text.search_corpus(root, query)
            found = _hits(result)
            reports.append(_report(
                variant, "retrieval", found == sorted(expected),
                f"found={found}, expected={sorted(expected)}",
            ))
        for variant, query in cases.PRECISION:
            result = document_text.search_corpus(root, query)
            found = _hits(result)
            reports.append(_report(
                variant, "retrieval", found == [],
                f"found={found}, expected=[]",
            ))
        flagged = document_text.search_corpus(
            root, "privileged", flagged={"DOC-MEMO"}
        )
        reports.append(_report(
            "restricted-memo", "retrieval",
            _hits(flagged) == []
            and flagged.get("restricted") == ["DOC-MEMO"],
            f"restricted={flagged.get('restricted')}",
        ))
        for variant, query, expected in cases.PARAPHRASE:
            result = document_text.search_corpus(root, query)
            found = _hits(result)
            reports.append(_report(
                variant, "retrieval", found == sorted(expected),
                f"found={found}, expected={sorted(expected)}",
            ))
    finally:
        tmp.cleanup()
    partial, ptmp = _build_corpus(
        [d for d in support.INITIAL if d != "DOC-REF"]
    )
    try:
        result = document_text.search_corpus(partial, "Dana Okafor")
        reports.append(_report(
            "unindexed-ref", "retrieval",
            _hits(result) == []
            and result.get("unindexed") == ["DOC-REF"],
            f"unindexed={result.get('unindexed')}",
        ))
    finally:
        ptmp.cleanup()
    return reports


def summarize(reports: list) -> dict:
    """Aggregates with denominators; every bucket gated."""
    gated = [r for r in reports if r["mode"] == "retrieval"]
    must_ids = {variant for variant, _, _ in cases.MUST_HIT}
    para_ids = {variant for variant, _, _ in cases.PARAPHRASE}
    must = [r for r in gated if r["case"] in must_ids]
    paraphrase = [r for r in gated if r["case"] in para_ids]
    precision = [
        r for r in gated
        if r["case"] not in must_ids and r["case"] not in para_ids
    ]
    summary = {
        "must_hit": {
            "hit": sum(1 for r in must if r["complete"]),
            "total": len(must),
        },
        "precision_fp": {
            "hit": sum(1 for r in precision if not r["complete"]),
            "total": len(precision),
        },
        "paraphrase": {
            "hit": sum(1 for r in paraphrase if r["complete"]),
            "total": len(paraphrase),
            "missed": sorted(
                r["case"] for r in paraphrase if not r["complete"]
            ),
        },
    }
    missed = [r["case"] for r in gated if not r["complete"]]
    summary["verdict"] = (
        f"RECALL {summary['must_hit']['hit']}/"
        f"{summary['must_hit']['total']}, "
        f"FP {summary['precision_fp']['hit']}, "
        f"paraphrase {summary['paraphrase']['hit']}/"
        f"{summary['paraphrase']['total']}"
        + (f", MISSED: {missed}" if missed else "")
    )
    return summary


def main() -> None:
    with contextlib.redirect_stdout(io.StringIO()):
        reports = run_all()
    print(json.dumps(
        {"reports": reports, "summary": summarize(reports)}, indent=2
    ))


if __name__ == "__main__":
    main()
