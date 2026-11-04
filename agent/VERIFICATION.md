# Verification layer

On for runs started with `VERIFICATION=on` in `.env`. Every check is one file in `verification/` with one entry point, `run_verification(ctx, work) -> {"passed": bool, "feedback": ...}`. The tools in `agent_kit/tools/` call them at fixed points, so the agent cannot skip them. You build two; the rest is pre-built.

| When | File | Check | Built by | On failure |
|---|---|---|---|---|
| `set_checklist` | `verification/verify_checklist.py` | Does the checklist cover the case's ground truth? Muse Spark marks each ground-truth item covered or not; code turns that into a score | You | Refused with feedback only (never the ground truth). After 3 tries the checklist is accepted, and the score and feedback are kept for the attorney. |
| `request_evidence` | `verification/verify_evidence_request.py` | The item is on the checklist and not already answered (unless corroboration, contradiction, or a grounded discrepancy applies); the need is new by purpose and target; relevant visible documents have been read or documented as exceptions | Pre-built | Refused with the reason |
| `submit_memo` | `verification/verify_memo.py` | Every item has a finding (open items allowed: filing reports the investigation); a finding marked asserted cites at least one recorded fact for its item; every citation matches a recorded fact that is still on its page; nothing confidential is cited; a signature item marked asserted has a consistent signature check on a document it cites; no evidence request is still open | You | Refused with the problems |
| `submit_memo` | `verification/verify_claims.py` | The conclusion, every finding, and gaps are supported as written (one judge call per memo, after `verify_memo.py` passes); the outcome is passed, failed, or unavailable, with the assessed scope recorded | Pre-built | Refused with the claims, or paused for retry when unavailable |
| 3 refused memos | | | Pre-built | Escalated to a person |
| The memo passes | | Pause for attorney sign-off | Pre-built | Rejected: the notes go back to the agent |

Also pre-built with verification on: `flag_privileged`, `request_evidence`, and `check_signatures`, which compares a signature with a reference page the agent names (visual consistency only; anything but consistent becomes a flag for the attorney); a flagged document cannot be read again. Always: at most 10 facts per checklist item (only supporting facts settle an item) and 3 citations per finding.

The ground truth stays outside this folder; `load_reference(root)` at the top of `verify_checklist.py` (pre-built) reads it. Your feedback goes to the agent as written, so it must point to what is missing without quoting, paraphrasing, or counting the ground-truth items.

## What you build

```python
# verification/verify_checklist.py
def run_verification(ctx, items: list) -> dict:
    """{"passed": bool, "score": float, "feedback": str}"""

# verification/verify_memo.py
def run_verification(ctx, memo: dict) -> dict:
    """{"passed": bool, "feedback": [problem, ...]}"""
```

Each file's docstring has the full spec. Until a check is built, a run with verification on stops at it, and `./helper test --verification` shows what is missing.

## On work already done

`./helper verify RUN_ID --memo` and `./helper verify RUN_ID --checklist` run the same checks on a finished run (the agent is not involved; the run is not changed). `./helper branch RUN_ID` starts a new run with verification on from the run's checklist step; resume it to continue.
