"""Labeling-packet generator for judge calibration (offline).

Collects assessment units across the memo-bearing benchmark
suites (reasoning, quality, identification), stratified by
sound/defective cases, into a JSONL packet a human labels per
docs/judge-calibration.md. One item, one id, one future label:

    cd agent && .venv/bin/python -m eval.label_packet PACKET.jsonl [N]

Each line: {id, source, sound, unit, memo, facts}. Unit ids are
stable ("<suite>:<case>:<unit>"); selection is deterministic
(sorted stride, no RNG). N defaults to 24 (protocol: 20-50).

The packet carries artifacts only — never verdicts. The human
labels each unit pass/fail/unknown from the evidence; the judge
side runs separately (eval/judge_slice.py) so the two label
streams stay independent until eval/calibration.py compares
them. The agent under evaluation must never fill the human
side: see the protocol's labeling section.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval import (  # noqa: E402
    identification_cases,
    quality_cases,
    reasoning_cases,
)
from verification.verify_claims import build_units  # noqa: E402

SUITES = (
    ("reasoning", reasoning_cases, {"with_reasoning": True}),
    ("quality", quality_cases, {}),
    ("identification", identification_cases, {"with_identification": True}),
)


def _slim_fact(fact: dict) -> dict:
    return {
        key: fact.get(key, "")
        for key in (
            "id", "item", "document_id", "page", "quote", "claim",
            "stance", "kind",
        )
    }


def collect_units() -> list:
    """Every assessment unit with its memo and facts, stably ordered."""
    items = []
    for suite, module, kwargs in SUITES:
        for name in sorted(module.CASES):
            case = module.CASES[name]
            memo = case.get("memo") or {}
            if not memo.get("findings"):
                continue
            with tempfile.TemporaryDirectory() as tmp:
                made = module.materialize(Path(tmp), name, **kwargs)
                record = made[1]
                facts = [
                    _slim_fact(f)
                    for f in record.data.get("facts", [])
                    if isinstance(f, dict)
                ]
            for unit in build_units(memo, facts):
                items.append({
                    "id": f"{suite}:{name}:{unit['id']}",
                    "source": f"{suite}:{name}",
                    "sound": bool(case.get("sound", False)),
                    "unit": unit,
                    "memo": memo,
                    "facts": facts,
                })
    items.sort(key=lambda item: item["id"])
    return items


def build_packet(n: int = 24) -> list:
    """Deterministic stratified sample: half sound, half defective."""
    if n < 1:
        raise ValueError("need at least one packet item")
    units = collect_units()
    sound = [u for u in units if u["sound"]]
    defective = [u for u in units if not u["sound"]]
    if not sound or not defective:
        raise ValueError("need sound and defective units to stratify")
    half = (n + 1) // 2
    picked = _stride(sound, half) + _stride(defective, n - half)
    picked.sort(key=lambda item: item["id"])
    return picked


def _stride(units: list, count: int) -> list:
    if count >= len(units):
        return list(units)
    step = len(units) / count
    return [units[int(round(i * step))] for i in range(count)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path, help="packet JSONL to write")
    parser.add_argument("n", type=int, nargs="?", default=24)
    args = parser.parse_args()
    packet = build_packet(args.n)
    with open(args.out, "w") as handle:
        for item in packet:
            handle.write(json.dumps(item) + "\n")
    print(json.dumps({
        "items": len(packet),
        "sound": sum(1 for i in packet if i["sound"]),
        "defective": sum(1 for i in packet if not i["sound"]),
        "out": str(args.out),
    }))


if __name__ == "__main__":
    main()
