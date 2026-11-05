"""Deterministic adaptive steering over the recorded case state.

`recommend()` wraps `reasoning.suggest_next_actions` with hard
resource overrides and an explicit stop signal. It is a pure
function of its inputs: same state plus same usage plus same
budgets always yields the same recommendation, and every
recommendation carries its reason.

Semantics:

- HALT (budget exhausted): the only recommendation is to
  escalate — `request_approval` so a human decides whether to
  extend the budget. No investigation action is suggested.
- INVESTIGATION COMPLETE: source-backed findings have resolved coverage,
  no open research obligations remain, a current challenge is recorded,
  and the underlying policy suggests only filing. Filing itself still passes
  through the gates; the policy never approves work.
- Otherwise: the ranked investigation actions pass through
  unchanged.

The policy never suggests `decide_approval`: recording a
human decision is not an agent action. It never changes
conclusions, expands authorization, or files anything; it
only ranks what to do next.
"""

from __future__ import annotations

from .reasoning import investigation_ready, suggest_next_actions

BUDGET_KEYS = ("max_calls", "max_input_tokens", "max_output_tokens")
USAGE_KEYS = {"max_calls": "calls", "max_input_tokens": "input_tokens",
              "max_output_tokens": "output_tokens"}


def _num(value) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def recommend(
    case_data: dict,
    usage: dict | None = None,
    budgets: dict | None = None,
    limit: int = 5,
) -> dict:
    """Rank next actions with budget overrides and a stop signal."""
    spent = usage if isinstance(usage, dict) else {}
    caps = budgets if isinstance(budgets, dict) else {}
    for cap_key in BUDGET_KEYS:
        cap = _num(caps.get(cap_key))
        used = _num(spent.get(USAGE_KEYS[cap_key], 0))
        if cap is not None and used is not None and used >= cap:
            return {
                "halt": True,
                "investigation_complete": False,
                "actions": [
                    {
                        "tool": "request_approval",
                        "target": cap_key,
                        "reason": (
                            f"budget {cap_key} exhausted ({used}/{cap}): "
                            "stop investigating and ask a human whether "
                            "to extend the budget"
                        ),
                    }
                ],
            }
    actions = suggest_next_actions(case_data, limit=limit)
    complete = (
        investigation_ready(case_data)
        and len(actions) == 1 and actions[0].get("tool") == "submit_memo"
    )
    return {
        "halt": False,
        "investigation_complete": complete,
        "actions": actions,
    }
