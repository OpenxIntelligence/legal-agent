"""Does the agent's checklist cover what the case needs?

Called when the agent calls set_checklist with VERIFICATION=on, and by
./helper verify RUN --checklist.

    run_verification(ctx, items) -> {"passed": bool, "score": float, "feedback": str}

- items: the agent's checklist items: [{"id", "question", "evidence_needed", ...}, ...]
- load_reference(ctx.root) (below) returns the case's ground truth,
  [{"id": "...", "question": "..."}, ...], or None when the case has none (then the
  checklist passes).

1. Ask Muse Spark (ctx.client.responses.create, JSON output) to mark each reference item
   covered or not by some agent item, even if worded differently, and to write feedback.
2. score = covered reference items / all reference items; passed = every one is covered.
3. feedback: what kind of question the checklist is missing, and any item that only
   restates background. It goes to the agent, so it must point to the direction only:
   never quote, paraphrase, or count the reference items. Empty when it passes.

Only passed and feedback reach the agent; the score is shown in the console.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from agent_kit import usage
from agent_kit.settings import case_dir, model

# ---- optional evaluation reference ----------------------------------------
# Optional evaluation references live in explicit GROUND_TRUTH_DIR, one
# <case folder name>.json per case. No teaching reference ships with the app.


def reference_path(root: Path) -> Path | None:
    configured = os.environ.get("GROUND_TRUTH_DIR", "").strip()
    if not configured:
        return None
    folder = Path(configured).expanduser()
    if not folder.is_absolute():
        folder = (Path(root) / folder).resolve()
    return folder / f"{case_dir(root).name}.json"


def load_reference(root: Path) -> list | None:
    """The case's ground-truth items, or None if the case has none."""
    path = reference_path(root)
    return json.loads(path.read_text())["items"] if path is not None and path.is_file() else None


# ---- coverage assessment ---------------------------------------------------

FALLBACK_FEEDBACK = (
    "The checklist may leave out an area the question turns on. "
    "Look again at what must be settled from the documents, and replace any item "
    "that only restates background from the assignment with a question the documents can answer."
)

PROMPT = """REFERENCE CHECKLIST. Judge whether the agent checklist covers the reference checklist. A reference item is covered when some agent item asks the same question, even if worded differently.

Reference checklist (ground truth). Do not repeat its questions, ids, counts, or coverage in your feedback:
{reference}

Agent checklist:
{agent}

Return only JSON: {{"covered": {{"<reference-id>": true}}, "feedback": "..."}}.
Feedback goes to the agent in one or two short sentences with a broad investigative direction. Do not enumerate the missing reference items, turn each missing item into a hint, or propose replacement questions; the agent must still work out the missing questions from its own assignment and documents. You may name an existing agent item that merely restates assignment background. Never include reference text, paraphrased reference questions, reference ids, counts, coverage maps, or scores. Empty feedback when every reference item is covered."""


def run_verification(ctx, items: list) -> dict:
    reference = load_reference(ctx.root)
    if reference is None:
        return {"passed": True, "score": None, "feedback": "",
                "skipped": True, "reason": "No checklist reference is configured or available; reference coverage was not assessed."}
    if not isinstance(items, list):
        items = []
    start = time.perf_counter()
    try:
        response = ctx.client.responses.create(
            model=model(),
            input=PROMPT.format(
                reference=json.dumps(reference), agent=json.dumps(items)
            ),
            text={"format": {"type": "json_object"}},
        )
    except Exception as error:
        usage.record_failure(
            ctx.root,
            ctx.run_id,
            "checklist_judge",
            error,
            latency_ms=(time.perf_counter() - start) * 1000,
        )
        raise
    usage.record(
        ctx.root,
        ctx.run_id,
        "checklist_judge",
        response,
        latency_ms=(time.perf_counter() - start) * 1000,
    )
    try:
        parsed = json.loads(response.output_text)
        covered = parsed.get("covered")
        judge_feedback = parsed.get("feedback")
        if not isinstance(covered, dict):
            raise ValueError("missing covered map")
    except (ValueError, AttributeError, TypeError):
        return {"passed": False, "score": 0.0, "feedback": FALLBACK_FEEDBACK}
    total = len(reference)
    if total == 0:
        return {"passed": True, "score": 1.0, "feedback": ""}
    covered_count = 0
    for ref in reference:
        rid = str(ref.get("id", "")) if isinstance(ref, dict) else ""
        if covered.get(rid) is True:
            covered_count += 1
    score = covered_count / total
    if covered_count == total:
        return {"passed": True, "score": score, "feedback": ""}
    if not isinstance(judge_feedback, str) or not judge_feedback.strip():
        judge_feedback = FALLBACK_FEEDBACK
    return {"passed": False, "score": score, "feedback": judge_feedback}
