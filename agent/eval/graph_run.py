"""Graph-ranking experiment: citation methods vs the textual baseline.

Runs six discovery methods plus ablations over the synthetic
citation networks in graph_cases.py: BM25 text (the existing
baseline), citation count, global PageRank, seeded personalized
PageRank, HITS authority, and a fused candidate (text ∪ PPR,
hard legal filters, CombSUM order, excluded-for-cause items
surfaced flagged). No network, fully deterministic.

A scenario completes when every required authority lands in
the method top-3 and no must_not_top node ranks first. Adverse
handling is measured separately: an adverse source must be
surfaced flagged or ranked below first — never topped, never
hidden. The verdict compares fused against text (the adoption
question) and reports ablation deltas isolating each signal.

Run: cd agent && .venv/bin/python -m eval.graph_run
"""

from __future__ import annotations

import contextlib
import io
import json

from agent_kit import bm25, pagerank
from eval import graph_cases

REPORT_KEYS = frozenset(
    {"case", "method", "mode", "split", "complete", "ranked",
     "flagged", "notes"}
)
TOP_K = 3
SEED_K = 2

METHODS = (
    "text",
    "indegree",
    "pagerank",
    "ppr",
    "hits",
    "fused",
    "fused_no_text",
    "fused_no_graph",
    "fused_no_filter",
)


def _text_ranked(fixture: dict) -> list[tuple[str, float]]:
    """BM25 top units with positive score, best first."""
    query = bm25.query_terms(fixture["query"])
    documents = [
        (node_id, bm25.query_terms(node["text"]))
        for node_id, node in fixture["nodes"].items()
    ]
    return [
        (key, score)
        for key, score in bm25.rank(query, documents)
        if score > 0.0
    ]


def _indegree(edges: dict) -> dict[str, int]:
    counts: dict[str, int] = {}
    for source, targets in edges.items():
        counts.setdefault(str(source), 0)
        for target in targets or []:
            target = str(target)
            counts[target] = counts.get(target, 0) + 1
    return counts


def _top(scores: dict, key_order=None) -> list[str]:
    """Top-K ids by score desc, id asc for determinism."""
    return [
        node
        for node, _ in sorted(
            scores.items(), key=lambda item: (-item[1], item[0])
        )[:TOP_K]
    ]


def _filter_reason(fixture: dict, node_id: str) -> str:
    """Hard legal exclusion, or "" when the node stays rankable."""
    node = fixture["nodes"][node_id]
    matter = fixture.get("jurisdiction")
    if matter and node.get("jurisdiction") != matter:
        return "out-of-jurisdiction"
    if node.get("status") == "overruled":
        return "overruled"
    if node.get("status") == "superseded":
        return "superseded"
    return ""


def _fused(
    fixture: dict,
    text_ranked: list[tuple[str, float]],
    ppr_scores: dict[str, float],
    *,
    use_text: bool,
    use_graph: bool,
    use_filters: bool,
) -> tuple[list[str], list[dict]]:
    """Union candidates, filter for cause, two-tier CombSUM order.

    Excluded-for-cause members surface flagged with their
    reason — filtered from ranking, never hidden. Ordering is
    two-tier: text-positive candidates always outrank text-zero
    ones (topicality dominates graph proximity for discovery —
    an on-topic isolate beats an off-topic node cited by a
    match), CombSUM over per-signal max-normalized scores
    within each tier, id asc breaking ties.
    """
    pool: set[str] = set()
    if use_text:
        pool.update(node for node, _ in text_ranked[:TOP_K])
    if use_graph:
        pool.update(_top(ppr_scores))
    text_max = max(
        [score for node, score in text_ranked if node in pool] + [0.0]
    )
    ppr_max = max([ppr_scores.get(node, 0.0) for node in pool] + [0.0])
    ranked: list[str] = []
    flagged: list[dict] = []
    for node in pool:
        reason = _filter_reason(fixture, node) if use_filters else ""
        if reason:
            flagged.append({"id": node, "reason": reason})
        else:
            ranked.append(node)
    text_of = dict(text_ranked)

    def combined(node: str) -> float:
        text_part = (text_of.get(node, 0.0) / text_max) if text_max else 0.0
        ppr_part = (
            (ppr_scores.get(node, 0.0) / ppr_max) if ppr_max else 0.0
        )
        return (text_part if use_text else 0.0) + (
            ppr_part if use_graph else 0.0
        )

    ranked.sort(
        key=lambda node: (
            0 if text_of.get(node, 0.0) > 0.0 else 1,
            -combined(node),
            node,
        )
    )
    flagged.sort(key=lambda entry: entry["id"])
    return ranked[:TOP_K], flagged


def _run_method(fixture: dict, method: str) -> tuple[list[str], list[dict]]:
    """Ranked top-K ids plus flagged exclusions for one method."""
    nodes = fixture["nodes"]
    edges = {
        str(source): [str(target) for target in targets or []]
        for source, targets in fixture["edges"].items()
    }
    text_ranked = _text_ranked(fixture)
    if method == "text":
        return [node for node, _ in text_ranked[:TOP_K]], []
    if method == "indegree":
        return _top(_indegree(edges)), []
    if method == "pagerank":
        return _top(pagerank.rank(edges)), []
    if method == "hits":
        return _top(pagerank.hits_authority(edges)), []
    seeds = [node for node, _ in text_ranked[:SEED_K]]
    ppr_scores = pagerank.personalized(edges, seeds) if seeds else {}
    if method == "ppr":
        if not seeds:
            return [], []
        return _top(ppr_scores), []
    if method == "fused":
        return _fused(fixture, text_ranked, ppr_scores, use_text=True,
                      use_graph=True, use_filters=True)
    if method == "fused_no_text":
        global_scores = pagerank.rank(edges)
        return _fused(fixture, [], global_scores, use_text=False,
                      use_graph=True, use_filters=True)
    if method == "fused_no_graph":
        return _fused(fixture, text_ranked, {}, use_text=True,
                      use_graph=False, use_filters=True)
    if method == "fused_no_filter":
        return _fused(fixture, text_ranked, ppr_scores, use_text=True,
                      use_graph=True, use_filters=False)
    raise ValueError(f"unknown method {method!r}")


def _grade(fixture: dict, ranked: list[str], flagged: list[dict]) -> dict:
    """Scenario verdict: required found, adverse cautioned, top clean."""
    required = set(fixture.get("required", []))
    adverse = set(fixture.get("adverse", []))
    banned = set(fixture.get("must_not_top", []))
    ranked_set = set(ranked)
    flagged_ids = {entry["id"] for entry in flagged}
    required_ok = required <= ranked_set
    top_ok = not ranked or ranked[0] not in banned
    adverse_ok = all(
        node in flagged_ids or (node in ranked_set and ranked[:1] != [node])
        for node in adverse
    )
    return {
        "complete": bool(required_ok and top_ok),
        "required_hit": len(required & ranked_set),
        "required_total": len(required),
        "adverse_ok": bool(adverse_ok),
        "false_top": bool(ranked and ranked[0] in banned),
    }


def run_all() -> list:
    """Every fixture × every method, fully deterministic."""
    reports = []
    for fixture in graph_cases.FIXTURES:
        for method in METHODS:
            ranked, flagged = _run_method(fixture, method)
            grade = _grade(fixture, ranked, flagged)
            reports.append(
                {
                    "case": fixture["id"],
                    "method": method,
                    "mode": "graph",
                    "split": fixture.get("split", "dev"),
                    "complete": grade["complete"],
                    "ranked": ranked,
                    "flagged": flagged,
                    "notes": (
                        f"ranked={ranked} flagged="
                        f"{[f['id'] + ':' + f['reason'] for f in flagged]} "
                        f"required+{grade['required_hit']}/"
                        f"{grade['required_total']} "
                        f"adverse_ok={grade['adverse_ok']} "
                        f"false_top={grade['false_top']}"
                    ),
                }
            )
    return reports


def summarize(reports: list) -> dict:
    """Per-method aggregates plus the adoption comparison."""
    methods: dict[str, dict] = {}
    for method in METHODS:
        mine = [r for r in reports if r["method"] == method]
        adverse_cases = [
            f for f in graph_cases.FIXTURES if f.get("adverse")
        ]
        adverse_ids = {f["id"] for f in adverse_cases}
        adverse_mine = [r for r in mine if r["case"] in adverse_ids]
        by_split = {}
        for split in ("dev", "heldout"):
            part = [r for r in mine if r["split"] == split]
            by_split[split] = {
                "complete": sum(1 for r in part if r["complete"]),
                "total": len(part),
            }
        methods[method] = {
            "complete": sum(1 for r in mine if r["complete"]),
            "total": len(mine),
            "false_tops": sum(
                1 for r in mine if "false_top=True" in r["notes"]
            ),
            "adverse_ok": sum(
                1 for r in adverse_mine if "adverse_ok=True" in r["notes"]
            ),
            "adverse_total": len(adverse_mine),
            "by_split": by_split,
        }
    fused, text = methods["fused"], methods["text"]
    held_fused = fused["by_split"]["heldout"]
    held_text = text["by_split"]["heldout"]
    verdict = (
        f"FUSED {fused['complete']}/{fused['total']} vs "
        f"TEXT {text['complete']}/{text['total']}; "
        f"fused false-tops {fused['false_tops']}, "
        f"adverse {fused['adverse_ok']}/{fused['adverse_total']}; "
        f"heldout FUSED {held_fused['complete']}/{held_fused['total']} vs "
        f"TEXT {held_text['complete']}/{held_text['total']} (post-hoc split)"
    )
    return {"methods": methods, "verdict": verdict}


def main() -> None:
    with contextlib.redirect_stdout(io.StringIO()):
        reports = run_all()
    print(json.dumps(
        {"reports": reports, "summary": summarize(reports)}, indent=2
    ))


if __name__ == "__main__":
    main()
