"""Run settings read from the environment (./helper loads .env)."""

from __future__ import annotations

import os
from pathlib import Path

_TRUE = {"on", "1", "true", "yes"}


def model() -> str:
    """The model the agent talks to (MODEL_API_MODEL, default muse-spark-1.3)."""
    return os.environ.get("MODEL_API_MODEL") or "muse-spark-1.3"


def sam_model() -> str:
    """The segmentation model check_signatures uses (SAM_MODEL, default sam-3.1)."""
    return os.environ.get("SAM_MODEL") or "sam-3.1"


def verification_enabled() -> bool:
    """VERIFICATION=on: new runs get the verification layer (agent/verification/): the
    checks run at fixed points, three more tools, escalation, and attorney sign-off."""
    value = os.environ.get("VERIFICATION", "on").strip().lower()
    if value in _TRUE:
        return True
    if value in {"off", "0", "false", "no"}:
        return False
    raise ConfigError("VERIFICATION must be on or off")


def qc_enabled() -> bool:
    """QC_ENABLED=off skips the quality review before the claims check.

    On by default. The claims check still gates every filing either
    way; the switch exists so evaluations can compare review/repair
    against no review under otherwise identical conditions.
    """
    return os.environ.get("QC_ENABLED", "on").strip().lower() not in {
        "off",
        "0",
        "false",
        "no",
    }


class ConfigError(ValueError):
    """An explicitly supplied runtime setting has an unusable value."""


def run_token_budget() -> int | None:
    """Per-run token budget (RUN_TOKEN_BUDGET, unset = unlimited).

    Raises ConfigError for an explicit but non-positive-integer value, so a
    typo cannot silently disable the budget.
    """
    value = os.environ.get("RUN_TOKEN_BUDGET", "").strip()
    if not value:
        return None
    try:
        budget = int(value)
    except ValueError:
        raise ConfigError(
            f"RUN_TOKEN_BUDGET must be a positive integer, got {value!r}"
        ) from None
    if budget <= 0:
        raise ConfigError(
            f"RUN_TOKEN_BUDGET must be a positive integer, got {value!r}"
        )
    return budget


DEFAULT_SYNTHESIS_RESERVE_TOKENS = 20000


def synthesis_reserve_tokens() -> int:
    """Tokens held back for synthesis and review (SYNTHESIS_RESERVE_TOKENS).

    Gathering work pauses once the remaining budget reaches this
    reserve, so readers cannot spend the run's last capacity. Only
    meaningful with RUN_TOKEN_BUDGET set; 0 disables the reserve.
    Raises ConfigError for an explicit but non-integer value.
    """
    value = os.environ.get("SYNTHESIS_RESERVE_TOKENS", "").strip()
    if not value:
        return DEFAULT_SYNTHESIS_RESERVE_TOKENS
    try:
        reserve = int(value)
    except ValueError:
        raise ConfigError(
            f"SYNTHESIS_RESERVE_TOKENS must be a non-negative integer, got {value!r}"
        ) from None
    if reserve < 0:
        raise ConfigError(
            f"SYNTHESIS_RESERVE_TOKENS must be a non-negative integer, got {value!r}"
        )
    return reserve


DEFAULT_MAX_WORKERS = 3


def max_workers() -> int:
    """Parallel sweep workers per call (MAX_WORKERS, default 3).

    Raises ConfigError for an explicit but non-positive-integer value.
    """
    value = os.environ.get("MAX_WORKERS", "").strip()
    if not value:
        return DEFAULT_MAX_WORKERS
    try:
        workers = int(value)
    except ValueError:
        raise ConfigError(
            f"MAX_WORKERS must be a positive integer, got {value!r}"
        ) from None
    if workers <= 0:
        raise ConfigError(
            f"MAX_WORKERS must be a positive integer, got {value!r}"
        )
    return workers


DEFAULT_MAX_CONTEXT_BYTES = 1500000


def max_context_bytes() -> int:
    """Request context cap in bytes (MAX_CONTEXT_BYTES); the run pauses past it.

    Raises ConfigError for an explicit but non-positive-integer value.
    """
    value = os.environ.get("MAX_CONTEXT_BYTES", "").strip()
    if not value:
        return DEFAULT_MAX_CONTEXT_BYTES
    try:
        cap = int(value)
    except ValueError:
        raise ConfigError(
            f"MAX_CONTEXT_BYTES must be a positive integer, got {value!r}"
        ) from None
    if cap <= 0:
        raise ConfigError(
            f"MAX_CONTEXT_BYTES must be a positive integer, got {value!r}"
        )
    return cap


def validate_runtime_config() -> None:
    """Fail fast on explicitly misconfigured run controls.

    Called at run entry (start and resume) so a typo'd budget or cap refuses
    the run instead of running unguarded partway through.
    """
    verification_enabled()
    run_token_budget()
    synthesis_reserve_tokens()
    max_context_bytes()


def case_dir(root: Path) -> Path:
    """The case folder (CASE_DIR, absolute or relative to the agent folder): objective.md and documents/."""
    value = os.environ.get("CASE_DIR", "").strip()
    if not value:
        raise RuntimeError("CASE_DIR is not set: point it at a case folder in .env")
    path = Path(value).expanduser()
    return path if path.is_absolute() else (Path(root) / path).resolve()
