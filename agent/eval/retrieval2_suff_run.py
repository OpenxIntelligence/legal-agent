"""Run the sealed sufficiency set once: search + gate verdicts.

Same search call shape as the retrieval2 benchmark (restricted
exclusions, corpus meta). Reports per-query retrieval hits and
the sufficiency gate's verdict. No tuning: truths are frozen in
retrieval2_suff.py; this runner only applies them.

    cd agent && .venv/bin/python -m eval.retrieval2_suff_run
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_kit import document_text  # noqa: E402
from agent_kit.sufficiency import assess  # noqa: E402
from eval import retrieval2_cases as cases  # noqa: E402
from eval import retrieval2_suff as suff  # noqa: E402
from eval.retrieval2_run import build_matter_root  # noqa: E402


def ranks_of(result: dict) -> list:
    ranks = []
    for group in result.get("documents") or []:
        for passage in group.get("passages") or []:
            ranks.append((group["document_id"], passage["page"]))
    return ranks


def main() -> None:
    parent = Path(tempfile.mkdtemp(prefix="r2suff-"))
    try:
        roots = {}
        for matter in {q["matter"] for q in suff.QUERIES}:
            roots[matter], _ = build_matter_root(matter, parent)
        out = []
        for spec in suff.QUERIES:
            matter = cases.CORPORA[spec["matter"]]
            started = time.perf_counter()
            result = document_text.search_corpus(
                roots[spec["matter"]], spec["query"],
                flagged=set(matter["restricted"]),
                meta=matter["meta"])
            verdict = assess(
                spec["required"], ranks_of(result),
                adverse=spec["adverse"], superseded=spec["superseded"])
            out.append({
                "id": spec["id"],
                "matter": spec["matter"],
                "ranks": ranks_of(result),
                "unindexed": result.get("unindexed"),
                "restricted": result.get("restricted"),
                "latency_ms": (time.perf_counter() - started) * 1000,
                **verdict,
            })
    finally:
        shutil.rmtree(parent, ignore_errors=True)
    print(json.dumps({
        "suff_version": suff.SUFF_VERSION,
        "cases_version": cases.CASES_VERSION,
        "n": len(out),
        "sufficient": sum(1 for q in out if q["sufficient"]),
        "abstain_advised": sum(1 for q in out if q["abstain"]),
        "queries": out,
    }, indent=1))


if __name__ == "__main__":
    main()
