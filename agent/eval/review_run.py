"""Replay promoted review examples through the real audit path.

Reviewed examples (dataset roles development/regression) live in
eval/reviewed/; sealed release examples live in
eval/sealed_holdout/ and are NEVER part of a default replay.
Sealed replay requires an explicit opt-in and returns an
exposure receipt; sealed files are never rewritten.

    cd agent && .venv/bin/python -m eval.review_run [--sealed]

Every run restates its client and what it does not measure:
scripted semantic verdicts prove routing and record plumbing,
not judgment or legal quality.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eval import artifact_run  # noqa: E402
from eval.artifact_cases import write_document  # noqa: E402

HERE = Path(__file__).resolve().parent
REVIEWED_DIR = HERE / "reviewed"
HOLDOUT_DIR = HERE / "sealed_holdout"


def _unit_id(row_id: str) -> str:
    if row_id == "RC":
        return "conclusion"
    if row_id == "RG":
        return "gaps"
    return f"finding:{row_id}"


def _unsupported_for(example: dict):
    failed = [
        _unit_id(row_id)
        for row_id, expected in (example.get("truth") or {}).items()
        if expected.get("semantic") == "failed"
    ]

    def mark(text: str) -> list:
        return [
            {"unit": unit, "reason": "review replay: stored truth"}
            for unit in sorted(failed)
        ]

    return mark


def _read_examples(directory: Path) -> dict:
    examples = {}
    if not directory.is_dir():
        return examples
    for path in sorted(directory.glob("*.json")):
        examples[path.stem] = json.loads(path.read_text())
    return examples


def collect(*, include_sealed: bool = False) -> dict:
    """Reviewed examples, plus sealed ones only when asked."""
    examples = _read_examples(REVIEWED_DIR)
    if include_sealed:
        for name, example in _read_examples(HOLDOUT_DIR).items():
            examples[f"sealed:{name}"] = example
    return examples


def materialize(root: Path, name: str, catalogue: dict):
    """Build one example matter; return (run_id, record, instance)."""
    from agent_kit.case_record import CaseRecord

    example = catalogue[name]
    run_id = f"review-{name}"
    for doc_id, spec in (example.get("documents") or {}).items():
        write_document(root, doc_id, list(spec.get("pages") or []))
    record = CaseRecord(root, run_id)
    record.file_memo(dict(example.get("memo") or {}), "unverified")
    instance = record.artifact_open(f"reviewed-{name}")
    return run_id, record, instance


def run_all(*, include_sealed: bool = False) -> dict:
    """Replay the catalogue; sealed files are read, never written."""
    catalogue = collect(include_sealed=include_sealed)
    sealed_hashes = {}
    if include_sealed and HOLDOUT_DIR.is_dir():
        for path in sorted(HOLDOUT_DIR.glob("*.json")):
            sealed_hashes[path.name] = hashlib.sha256(
                path.read_bytes()).hexdigest()
    reports = []
    for name in sorted(catalogue):
        example = catalogue[name]
        maker = lambda root, _n, _c=catalogue: materialize(  # noqa: E731
            root, _n, _c)
        reports.append(artifact_run.run_case(
            name, cases=catalogue, materialize=maker,
            unsupported=_unsupported_for(example)))
    after = {}
    if include_sealed and HOLDOUT_DIR.is_dir():
        for path in sorted(HOLDOUT_DIR.glob("*.json")):
            after[path.name] = hashlib.sha256(
                path.read_bytes()).hexdigest()
    return {
        "client": "scripted",
        "semantic_quality": (
            "not measured: semantic verdicts are scripted; "
            "this replay proves routing and record plumbing only"
        ),
        "sealed_included": include_sealed,
        "sealed_files_unchanged": sealed_hashes == after,
        "exposure": (
            [{"use": "sealed-replay", "examples": sorted(sealed_hashes)}]
            if include_sealed else []
        ),
        "reports": reports,
        "summary": artifact_run.summarize(reports),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sealed", action="store_true",
                        help="also replay sealed release examples")
    args = parser.parse_args()
    print(json.dumps(run_all(include_sealed=args.sealed), indent=1))


if __name__ == "__main__":
    main()
