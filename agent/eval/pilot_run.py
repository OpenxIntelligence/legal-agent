"""Run pilot matters offline: search each issue, assess needs, compare keys.

Same search call shape as the retrieval2 benchmark (restricted
exclusions, corpus meta). For each issue: retrieve, run the
sufficiency verdict, map it to sufficient/insufficient/abstain,
and compare against the DRAFT key. Mismatches are findings for
attorney key review — never auto-corrected here.

    cd agent && .venv/bin/python -m eval.pilot_run
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_kit import document_text  # noqa: E402
from agent_kit.sufficiency import assess  # noqa: E402
from eval import pilot_cases as pilot  # noqa: E402
from eval import retrieval2_cases as cases  # noqa: E402
from eval.retrieval2_run import build_matter_root  # noqa: E402


def ranks_of(result: dict) -> list:
    ranks = []
    for group in result.get("documents") or []:
        for passage in group.get("passages") or []:
            ranks.append((group["document_id"], passage["page"]))
    return ranks


def verdict_of(verdict: dict) -> str:
    if verdict["abstain"]:
        return "abstain"
    if verdict["sufficient"] and verdict["adverse_covered"]:
        return "sufficient"
    return "insufficient"


def main() -> None:
    parent = Path(tempfile.mkdtemp(prefix="pilot-"))
    try:
        roots = {}
        for matter in cases.CORPORA:
            roots[matter], _ = build_matter_root(matter, parent)
        out = []
        for matter_spec in pilot.MATTERS:
            issues = []
            for issue in matter_spec["issues"]:
                matter = cases.CORPORA[issue["matter"]]
                result = document_text.search_corpus(
                    roots[issue["matter"]], issue["query"],
                    flagged=set(matter["restricted"]),
                    meta=matter["meta"])
                verdict = assess(
                    list(issue["required"]), ranks_of(result),
                    adverse=list(issue["adverse"]),
                    superseded=list(issue["superseded"]))
                observed = verdict_of(verdict)
                issues.append({
                    "id": issue["id"],
                    "ranks": ranks_of(result),
                    "observed": observed,
                    "key": issue["key_verdict"],
                    "match": observed == issue["key_verdict"],
                    "missing": verdict["missing"],
                    "adverse_covered": verdict["adverse_covered"],
                    "version_confusion": verdict["version_confusion"],
                })
            out.append({"id": matter_spec["id"], "issues": issues})
    finally:
        shutil.rmtree(parent, ignore_errors=True)
    total = sum(len(m["issues"]) for m in out)
    matched = sum(1 for m in out for i in m["issues"] if i["match"])
    print(json.dumps({
        "pilot_version": pilot.PILOT_VERSION,
        "key_status": pilot.KEY_STATUS,
        "matters": len(out),
        "issues": total,
        "key_match": matched,
        "results": out,
    }, indent=1))


if __name__ == "__main__":
    main()
