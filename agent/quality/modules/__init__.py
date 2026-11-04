"""Built-in QC check modules (importing registers them)."""

from quality.modules import counterevidence, materiality, scope  # noqa: F401

__all__ = ["counterevidence", "materiality", "scope"]
