"""reclassify_task: change the task type with recorded basis.

The assessment policy (task type + gate version + required
classes) is pinned server-side at identification; the gate
enforces the pin, never the live label. Reclassification is the
only path to a new type: it logs from/to/basis, re-pins under
the current gate, stales the validated decomposition so the
question set must be re-validated for the new policy, and stales
prior approval grants so they revalidate under the new policy.
A model actor may not narrow the assessed scope: a move that
drops pinned question classes is refused and needs the operator
(closeout §8, model-initiated moves).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..reasoning import REQUIRED_CLASSES, TASK_TYPES

if TYPE_CHECKING:
    from . import Context

DEFINITION = {
    "type": "function",
    "name": "reclassify_task",
    "description": "Change the matter's task type when the work proves "
    "different from the identification: records why, re-pins the "
    "assessment policy, and stales the decomposition for re-validation. "
    "A model call that would narrow the assessed question set is refused.",
    "parameters": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "task_type": {
                "type": "string",
                "enum": list(TASK_TYPES),
                "description": "the corrected gate task type",
            },
            "basis": {
                "type": "string",
                "description": "why the type changes: what was learned",
            },
        },
        "required": ["task_type", "basis"],
    },
}


def run(ctx: Context, args: dict) -> dict:
    new_type = str(args.get("task_type", "") or "").strip()
    actor = getattr(ctx, "actor", "model") or "model"
    if actor == "model" and new_type in REQUIRED_CLASSES:
        stored = str(ctx.case.data.get("task_type", "") or "")
        if stored and new_type != stored:
            pin = ctx.case.data.get("assessment_policy")
            old_required = (
                pin.get("required_classes")
                if isinstance(pin, dict)
                and isinstance(pin.get("required_classes"), list)
                else list(REQUIRED_CLASSES.get(stored, ()))
            )
            dropped = sorted(
                set(old_required) - set(REQUIRED_CLASSES[new_type])
            )
            if dropped:
                names = ", ".join(str(c) for c in dropped)
                return {
                    "error": (
                        f"reclassification {stored!r} -> {new_type!r} "
                        "drops assessed question classes "
                        f"({names}): a model actor may not narrow the "
                        "assessed scope; ask the operator to reclassify "
                        "with a recorded basis"
                    )
                }
    pin = ctx.case.reclassify_task_type(
        str(args.get("task_type", "")),
        str(args.get("basis", "")),
    )
    return {
        "reclassified": True,
        "task_type": pin["task_type"],
        "policy": pin,
    }
