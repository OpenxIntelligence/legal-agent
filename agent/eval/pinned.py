"""Pinned reference-vs-candidate gate (spectre-lite, offline mechanics only).

Each eval case runs under a scripted client and its final-state report is
compared against a committed pinned reference. Differences are reported as
value-free field paths (never quoted payloads), and any divergence
quarantines promotion:

    cd agent && .venv/bin/python -m eval.pinned --capture  # refresh references
    cd agent && .venv/bin/python -m eval.pinned            # gate (exit 1 on quarantine)

Quarantine kinds: divergent (normalized report differs), unlisted (no
pinned reference for the case), capacity (candidate exhausted its turn
budget), unable (comparator error). Like the rest of eval/, scripted arms
prove mechanics and non-regression, never judgment quality.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval import cases  # noqa: E402
from eval import run  # noqa: E402

PINNED_DIR = Path(__file__).resolve().parent / "pinned"

# Wall-clock leaves that must never gate: dropped before comparison.
VOLATILE_PATHS = frozenset(
    {"diagnostics.elapsed_s", "diagnostics.latency_ms"}
)

MAX_DIFFS = 100


def _strip_volatile(node, prefix: str = "") -> None:
    if isinstance(node, dict):
        for key in [k for k in node if f"{prefix}{k}" in VOLATILE_PATHS]:
            del node[key]
        for key, value in node.items():
            _strip_volatile(value, f"{prefix}{key}.")
    elif isinstance(node, list):
        for value in node:
            _strip_volatile(value, prefix)


def normalize(report: dict) -> dict:
    """A comparison-ready copy of a report with volatile leaves removed."""
    clean = json.loads(json.dumps(report))
    _strip_volatile(clean)
    return clean


def _diff(ref, cand, path: str, out: list) -> None:
    if len(out) >= MAX_DIFFS:
        return
    if isinstance(ref, dict) and isinstance(cand, dict):
        for key in ref:
            if key not in cand:
                out.append({"path": f"{path}.{key}", "kind": "missing"})
            else:
                _diff(ref[key], cand[key], f"{path}.{key}", out)
        for key in cand:
            if key not in ref:
                out.append({"path": f"{path}.{key}", "kind": "extra"})
    elif isinstance(ref, list) and isinstance(cand, list):
        if len(ref) != len(cand):
            out.append(
                {
                    "path": f"{path}.length",
                    "kind": "changed",
                    "delta": len(cand) - len(ref),
                }
            )
        for index, (left, right) in enumerate(zip(ref, cand)):
            _diff(left, right, f"{path}[{index}]", out)
    elif type(ref) is not type(cand):
        out.append({"path": path, "kind": "changed"})
    elif ref != cand:
        entry = {"path": path, "kind": "changed"}
        if isinstance(ref, (int, float)) and not isinstance(ref, bool):
            entry["delta"] = cand - ref
        out.append(entry)


def diff_reports(ref: dict, cand: dict) -> dict:
    """Value-free structural diff: field paths and change kinds only.

    String and boolean payloads are never quoted; numeric leaves carry
    only the delta. Capped at MAX_DIFFS with a truncation flag.
    """
    out: list = []
    _diff(normalize(ref), normalize(cand), "$", out)
    capped = out[:MAX_DIFFS]
    return {"diffs": capped, "truncated": len(out) >= MAX_DIFFS}


def _read_reference(pinned_dir: Path, name: str):
    path = pinned_dir / f"{name}.json"
    if not path.exists():
        return None, "unlisted"
    try:
        return json.loads(path.read_text()), ""
    except (json.JSONDecodeError, OSError):
        return None, "unable"


def compare_case(
    name: str, candidate: dict, *, pinned_dir: Path = PINNED_DIR,
    turns: int = 30,
) -> dict:
    """Compare one candidate report against its pinned reference."""
    reference, problem = _read_reference(pinned_dir, name)
    if problem == "unlisted":
        return {
            "case": name,
            "quarantined": True,
            "quarantine": ["unlisted"],
            "diffs": [],
            "truncated": False,
        }
    if problem == "unable":
        return {
            "case": name,
            "quarantined": True,
            "quarantine": ["unable"],
            "diffs": [],
            "truncated": False,
        }
    diff = diff_reports(reference, candidate)
    quarantine = []
    if diff["diffs"]:
        quarantine.append("divergent")
    if candidate.get("diagnostics", {}).get("turns", 0) >= turns:
        quarantine.append("capacity")
    return {
        "case": name,
        "quarantined": bool(quarantine),
        "quarantine": quarantine,
        "diffs": diff["diffs"],
        "truncated": diff["truncated"],
    }


def capture(*, pinned_dir: Path = PINNED_DIR, turns: int = 30) -> list:
    """Run every case and write its report as the pinned reference."""
    pinned_dir.mkdir(parents=True, exist_ok=True)
    names = []
    with contextlib.redirect_stdout(io.StringIO()):
        for name in sorted(cases.CASES):
            report = run.run_case(name, turns=turns)
            (pinned_dir / f"{name}.json").write_text(
                json.dumps(report, indent=2, sort_keys=True) + "\n"
            )
            names.append(name)
    return names


def gate(
    *, pinned_dir: Path = PINNED_DIR, turns: int = 30,
) -> tuple[dict, int]:
    """Run every case against its reference; return (report, exit code)."""
    comparisons = []
    with contextlib.redirect_stdout(io.StringIO()):
        for name in sorted(cases.CASES):
            try:
                candidate = run.run_case(name, turns=turns)
            except Exception as error:
                comparisons.append(
                    {
                        "case": name,
                        "quarantined": True,
                        "quarantine": ["unable"],
                        "diffs": [],
                        "truncated": False,
                        "error": f"{type(error).__name__}: {error}",
                    }
                )
                continue
            comparisons.append(
                compare_case(name, candidate, pinned_dir=pinned_dir, turns=turns)
            )
    quarantined = [c["case"] for c in comparisons if c["quarantined"]]
    report = {
        "verdict": "PASS" if not quarantined else "QUARANTINED",
        "quarantined": quarantined,
        "cases": comparisons,
    }
    return report, 0 if not quarantined else 1


def main(argv: list | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if args == ["--capture"]:
        names = capture()
        print(json.dumps({"captured": names}, indent=2))
        return 0
    report, code = gate()
    print(json.dumps(report, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
