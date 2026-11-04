"""Offline eval mechanics: scripted clients, temp workspaces, JSON reports.

This package runs the synthetic cases in eval/cases.py end to end. It is a
mechanics harness, not a quality gate: every run below uses a scripted
(mocked) model, and scripted runs prove the harness works — never that
reasoning or retrieval is good. Live quality evaluation needs real model
calls on held-out cases; see docs/agent-v2-evaluation.md.
"""

__all__ = ["REPORT_KEYS", "run_all", "run_case"]


def __getattr__(name):
    # Listing synthetic pilot keys must not import SDK-backed execution machinery.
    if name in __all__:
        from importlib import import_module
        return getattr(import_module(".run", __name__), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
