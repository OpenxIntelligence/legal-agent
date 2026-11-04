"""Judge-side slice runner for calibration (needs a model key).

Reads a labeling packet (eval/label_packet.py), runs the claims
judge over each item's memo, and writes JUDGE JSONL
({id, label} per unit) for eval/calibration.py to compare
against the human labels:

    cd agent && .venv/bin/python -m eval.judge_slice PACKET.jsonl JUDGE.jsonl

Verdicts map supported→pass, unsupported→fail, unknown→unknown.
Each item re-materializes its source case offline for the
investigation state, then judges with a LIVE client from
agent_kit.client.make_client() (needs $MODEL_API_KEY and an
operator-approved budget). For plumbing checks without a key,
pass --scripted to use canned verdicts — scripted output must
never be submitted as judge labels.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_kit import tools  # noqa: E402
from agent_kit.case_record import CaseRecord  # noqa: E402
from eval.label_packet import SUITES  # noqa: E402
from verification import verify_claims  # noqa: E402

LABELS = {"supported": "pass", "unsupported": "fail", "unknown": "unknown"}


def judge_packet(packet: list, client) -> list:
    """Run the judge over packet items; return [{id, label}]."""
    modules = {suite: (module, kwargs) for suite, module, kwargs in SUITES}
    labels = []
    for item in packet:
        suite, name = item["source"].split(":", 1)
        module, kwargs = modules[suite]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "case").mkdir()
            made = module.materialize(root, name, **kwargs)
            mat_run_id = made[0]
            case = CaseRecord(root, mat_run_id)
            ctx = tools.Context(client, root, mat_run_id, case, False)
            result = verify_claims.run_verification(ctx, item["memo"])
        verdicts = result.get("verdicts") or {}
        got = {
            unit_id: LABELS.get(verdict, "unknown")
            for unit_id, verdict in verdicts.items()
        }
        unit_id = item["unit"]["id"]
        labels.append({"id": item["id"], "label": got.get(unit_id, "unknown")})
    return labels


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packet", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument(
        "--scripted", action="store_true",
        help="canned verdicts for plumbing checks only",
    )
    args = parser.parse_args()
    with open(args.packet) as handle:
        packet = [json.loads(line) for line in handle if line.strip()]
    if args.scripted:
        from tests import support  # noqa: E402

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "case").mkdir()
            client = support.scripted(root, []).client
    else:
        from agent_kit.client import make_client  # noqa: E402

        client = make_client()
    labels = judge_packet(packet, client)
    with open(args.out, "w") as handle:
        for entry in labels:
            handle.write(json.dumps(entry) + "\n")
    print(json.dumps({"items": len(labels), "out": str(args.out)}))


if __name__ == "__main__":
    main()
