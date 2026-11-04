"""Registry of QC check modules.

Each module declares its name, version, kind, purpose, applicability,
inputs, output schema, dependencies, budget
({"max_judge_calls_per_review": non-negative int}), obligation
(required/advisory), and run entry. The controller runs deterministic
modules first (alphabetical within a kind), then semantic ones whose
dependencies executed and whose applicability holds.
"""

from __future__ import annotations

REQUIRED = (
    "name",
    "version",
    "kind",
    "purpose",
    "applies",
    "inputs",
    "output_schema",
    "dependencies",
    "budget",
    "obligation",
    "run",
)
KINDS = ("deterministic", "semantic")
OBLIGATIONS = ("required", "advisory")

MODULES: dict[str, dict] = {}


def register(spec: dict) -> dict:
    """Validate a module declaration and register it. Incomplete specs fail loud."""
    missing = [key for key in REQUIRED if key not in spec]
    if missing:
        raise ValueError(f"module is missing declarations: {', '.join(missing)}")
    if spec["kind"] not in KINDS:
        raise ValueError(f"module {spec.get('name')!r} has kind {spec['kind']!r}")
    if spec["obligation"] not in OBLIGATIONS:
        raise ValueError(
            f"module {spec.get('name')!r} has obligation {spec['obligation']!r}"
        )
    if not isinstance(spec.get("version"), str) or not spec["version"]:
        raise ValueError(f"module {spec.get('name')!r} needs a version string")
    if not callable(spec["applies"]) or not callable(spec["run"]):
        raise ValueError(f"module {spec.get('name')!r} needs applies/run callables")
    budget = spec.get("budget")
    calls = budget.get("max_judge_calls_per_review") if isinstance(budget, dict) else None
    if type(calls) is not int or calls < 0:
        raise ValueError(
            f"module {spec.get('name')!r} needs a budget with "
            "max_judge_calls_per_review as a non-negative int"
        )
    MODULES[spec["name"]] = spec
    return spec


def ordered() -> list:
    """Registered modules: deterministic first, alphabetical within a kind."""
    return sorted(
        MODULES.values(),
        key=lambda spec: (0 if spec["kind"] == "deterministic" else 1, spec["name"]),
    )


def clear() -> None:
    """Forget registered modules (tests only)."""
    MODULES.clear()
