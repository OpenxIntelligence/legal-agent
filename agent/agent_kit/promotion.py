"""Promotion: reviewed work becomes a replayable evaluation example.

A promotion packages an immutable input snapshot (documents +
memo), the original and repaired outputs, per-row expected
behavior, and reference-label provenance into one JSON record
with an explicit dataset role and exposure history. It never
invents truth: expected behavior is supplied by the trusted
caller, covers every row, and is refused unless each failed
row has fresh, non-conflicting reviewed labels (passed rows
are behavior-preservation expectations, not reviewed
correctness claims).

Roles are development or regression only. Sealed release
examples are authored by an evaluation custodian and can never
be created, overwritten, or re-roled here — this module does
not know the holdout location at all.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

from agent_kit.case_record import CaseError
from agent_kit.progress import now
from agent_kit import review_labels

ROLES = ("development", "regression")

__all__ = ["ROLES", "promote_example", "preservation_truth"]


def _transcript_pages(root: Path, sha: str | None, doc_id: str) -> list:
    if not sha:
        raise CaseError(
            f"cannot promote: {doc_id} has no pinned source version")
    path = root / "workspace" / "cache" / "text" / f"{sha}.json"
    if not path.is_file():
        raise CaseError(
            f"cannot promote: transcript for {doc_id} is unavailable")
    try:
        data = json.loads(path.read_text())
    except ValueError:
        raise CaseError(
            f"cannot promote: transcript for {doc_id} is corrupt") from None
    pages = data.get("pages") if isinstance(data, dict) else None
    if not isinstance(pages, list) or not pages:
        raise CaseError(
            f"cannot promote: transcript for {doc_id} has no pages")
    return [str(page.get("text") or "") for page in pages
            if isinstance(page, dict)]


def _snapshot_documents(instance: dict, root: Path) -> dict:
    docs: dict[str, dict] = {}
    for row in instance.get("rows") or []:
        if not isinstance(row, dict):
            continue
        for cite in row.get("citations") or []:
            if not isinstance(cite, dict):
                continue
            doc_id = str(cite.get("document_id") or "")
            if not doc_id or doc_id in docs:
                continue
            sha = cite.get("pin_sha")
            docs[doc_id] = {
                "pages": _transcript_pages(root, sha, doc_id),
                "sha": sha,
            }
    if not docs:
        raise CaseError("cannot promote: no cited source snapshot")
    return docs


def _slim_rows(rows: list) -> list:
    slim = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        checks = row.get("checks") or {}
        slim.append({
            "row_id": row.get("row_id"),
            "kind": row.get("kind"),
            "assertion": row.get("assertion"),
            "citations": [
                {"document_id": c.get("document_id"),
                 "page": c.get("page"), "quote": c.get("quote")}
                for c in row.get("citations") or []
                if isinstance(c, dict)
            ],
            "checks": {
                layer: (checks.get(layer) or {}).get("outcome")
                for layer in ("structural", "semantic")
            },
        })
    return slim


def promote_example(
    instance: dict,
    data: dict,
    root: Path,
    dest: Path,
    *,
    example_id: str,
    role: str,
    truth: dict,
    failure: str,
    behavior: str,
    actor: str,
    limitations: tuple = (),
) -> dict:
    """Create one replayable example record (create-only).

    Raises CaseError on a sealed role, bad id/truth, missing or
    conflicting or stale labels, an unreproducible snapshot, or
    an id that already exists. Never mutates the instance.
    """
    if role == "sealed":
        raise CaseError(
            "cannot promote to sealed: release examples are "
            "custodian-authored, never promoted from development")
    if role not in ROLES:
        raise CaseError(
            f"role must be one of {', '.join(ROLES)}")
    ident = str(example_id or "").strip()
    if not ident or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-"
                        for c in ident):
        raise CaseError("example_id must be nonempty [a-z0-9-]")
    if not str(actor or "").strip():
        raise CaseError("promoting actor is required")
    if not str(failure or "").strip():
        raise CaseError("original failure description is required")
    if not str(behavior or "").strip():
        raise CaseError("expected behavior description is required")
    if not isinstance(truth, dict) or not truth:
        raise CaseError("truth must map row ids to outcomes")
    rows = {r.get("row_id"): r for r in instance.get("rows") or []
            if isinstance(r, dict)}
    missing = sorted(set(rows) - set(truth))
    if missing:
        raise CaseError(
            f"truth must cover every row; missing {', '.join(missing)}")
    for row_id, expected in truth.items():
        if row_id not in rows:
            raise CaseError(f"truth names unknown row {row_id!r}")
        if not isinstance(expected, dict):
            raise CaseError(f"truth for {row_id} must be a mapping")
        for layer in ("structural", "semantic"):
            if expected.get(layer) not in (
                    "passed", "failed", "not_run", "incomplete"):
                raise CaseError(
                    f"truth for {row_id} needs a {layer} outcome")
        if not str(expected.get("class") or "").strip():
            raise CaseError(
                f"truth for {row_id} needs a defect class")
    failed = [row_id for row_id, expected in truth.items()
              if expected.get("semantic") == "failed"]
    labeled = [
        row_id for row_id in truth
        if any(review_labels.label_fresh(
            label, instance, rows[row_id])
            for label in review_labels.label_status(
                instance, row_id)["labels"])
    ]
    if not labeled:
        raise CaseError(
            "cannot promote: no fresh reviewed labels on any row; "
            "relabel the current revision")
    for row_id in failed:
        status = review_labels.label_status(instance, row_id)
        if status["state"] == "pending":
            raise CaseError(
                f"cannot promote: failed row {row_id} has no "
                "reviewed labels")
        if status["state"] == "unresolved":
            raise CaseError(
                f"cannot promote: {row_id} labels conflict; "
                "adjudicate first")
        for label in status["labels"]:
            if not review_labels.label_fresh(
                    label, instance, rows[row_id]):
                raise CaseError(
                    f"cannot promote: {row_id} has stale labels; "
                    "relabel the current revision")
    memo = data.get("memo") if isinstance(data, dict) else None
    if not isinstance(memo, dict) or not memo.get("findings"):
        raise CaseError("cannot promote: matter memo is missing")
    versions = instance.get("versions") or []
    first_rows = versions[0].get("rows") if versions else None
    record = {
        "id": ident,
        "version": "reviewed-example/v1",
        "role": role,
        "documents": _snapshot_documents(instance, root),
        "memo": copy.deepcopy(memo),
        "truth": {rid: {"class": str(exp["class"]),
                        "structural": exp["structural"],
                        "semantic": exp["semantic"]}
                  for rid, exp in truth.items()},
        "original_output": _slim_rows(first_rows or instance.get("rows")),
        "repaired_output": _slim_rows(instance.get("rows")),
        "reference": {
            row_id: {
                "labels": [label["id"] for label in
                           review_labels.label_status(
                               instance, row_id)["labels"]],
                "origins": sorted({
                    label.get("origin") for label in
                    review_labels.label_status(
                        instance, row_id)["labels"]}),
                "adjudication": review_labels.label_status(
                    instance, row_id)["adjudication"],
            }
            for row_id in truth
        },
        "failure": str(failure).strip(),
        "behavior": str(behavior).strip(),
        "limitations": [str(item) for item in limitations],
        "candidate": {"client": "scripted",
                      "promotion_tool": "promotion/v1"},
        "exposure": [{"use": "promoted", "actor": str(actor).strip(),
                      "at": now()}],
        "promoted_from": {
            "matter": instance.get("matter"),
            "instance": instance.get("instance_id"),
            "template": instance.get("template"),
            "template_version": instance.get("template_version"),
            "versions": len(versions),
        },
    }
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / f"{ident}.json"
    if path.exists():
        raise CaseError(f"example {ident} already exists; use a new id")
    path.write_text(json.dumps(record, indent=1) + "\n")
    return record


def preservation_truth(instance: dict, classes: dict) -> dict:
    """Behavior-preservation truth from recorded check outcomes.

    Outcomes are the rows' currently recorded structural/semantic
    results (unassessed layers become ``not_run``); the caller
    supplies a defect class per row. The result feeds
    :func:`promote_example`, which still enforces label freshness
    and agreement on failed rows — confirming current behavior is
    a reviewer assertion, not a correctness proof.
    """
    if not isinstance(classes, dict):
        raise CaseError("classes must map every row id to a class")
    truth = {}
    for row in instance.get("rows") or []:
        if not isinstance(row, dict):
            continue
        row_id = row.get("row_id")
        cls = str((classes or {}).get(row_id) or "").strip()
        if not cls:
            raise CaseError(
                f"row {row_id!r} needs a defect class to promote")
        checks = row.get("checks") or {}
        truth[row_id] = {
            "class": cls,
            "structural": _recorded_outcome(checks.get("structural")),
            "semantic": _recorded_outcome(checks.get("semantic")),
        }
    if not truth:
        raise CaseError("cannot promote: the instance has no rows")
    return truth


def _recorded_outcome(layer: object) -> str:
    outcome = layer.get("outcome") if isinstance(layer, dict) else None
    if outcome in ("passed", "failed", "not_run", "incomplete"):
        return outcome
    return "not_run"
