"""Dependency-aware staleness: which assessed units still stand.

A claims check binds the memo draft and evidence fingerprint it
assessed. When the memo or the evidence later changes, this module
decides per unit whether the verdict still stands:

- A revised memo draft stales every unit: the words changed.
- A new, removed, or changed fact stales the units it is relevant
  to: units whose support references name the fact's document, or
  whose finding item is the fact's item. The conclusion synthesizes
  the memo, so it is relevant to every document cited anywhere in
  the assessed support map — but not to uncited, unmatched evidence.
- A fact on an uncited document for an unmatched item stales
  nothing, but is always listed as unassessed new evidence.
- Units backed only by investigation-state references stale on any
  evidence change: an exhaustion claim ("no dated record found")
  is exactly what new evidence can overturn.
- Checks recorded before the fact index existed cannot scope
  relevance: any evidence change stales every unit, said plainly.

Staleness never rewrites the historical verdict; it marks what
needs reassessment before the result can be relied on again.
"""

from __future__ import annotations

import hashlib
import re

from quality.records import evidence_fingerprint, memo_hash

DOC_ID = re.compile(r"\b([A-Z][A-Z0-9]*-[A-Z0-9-]+)\b")


def fact_index(facts: list) -> dict:
    """Compact per-fact snapshot for later relevance diffing."""
    index = {}
    for fact in facts or []:
        if not isinstance(fact, dict) or not fact.get("id"):
            continue
        quote = str(fact.get("quote", ""))
        index[str(fact["id"])] = {
            "document_id": str(fact.get("document_id", "")),
            "page": str(fact.get("page", "")),
            "quote_hash": hashlib.sha256(quote.encode()).hexdigest()[:12],
            "stance": str(fact.get("stance", "supports")),
            "item": str(fact.get("item", "")),
        }
    return index


def assess(check: dict, current_facts: list, current_memo: dict | None = None) -> dict:
    """Staleness of one stored claims check against current state.

    Returns {"stale": {unit: reason}, "unassessed": [fact ids],
    "unknown": bool}. Unknown (no version binding on the check)
    means staleness cannot be told, never that all is current.
    """
    scope = check.get("scope") if isinstance(check, dict) else None
    if not isinstance(scope, dict) or "evidence" not in scope:
        return {"stale": {}, "unassessed": [], "unknown": True}
    assessed = list(scope.get("assessed") or scope.get("expected") or [])
    if current_memo is not None and isinstance(scope.get("content"), str):
        current_content = memo_hash(
            {
                key: current_memo.get(key)
                for key in ("conclusion", "findings", "gaps")
            }
            if isinstance(current_memo, dict)
            else {}
        )
        if scope["content"] != current_content:
            return {
                "stale": {
                    unit: "memo revised since assessment" for unit in assessed
                },
                "unassessed": [],
                "unknown": False,
            }
    if scope.get("evidence") == evidence_fingerprint({"facts": current_facts}):
        return {"stale": {}, "unassessed": [], "unknown": False}
    old_index = check.get("fact_index")
    if not isinstance(old_index, dict):
        return {
            "stale": {
                unit: "evidence changed; legacy check cannot scope relevance"
                for unit in assessed
            },
            "unassessed": [],
            "unknown": False,
        }
    new_index = fact_index(current_facts)
    support = check.get("support") if isinstance(check.get("support"), dict) else {}
    union_docs = set()
    for refs in support.values():
        if isinstance(refs, list):
            union_docs |= set(DOC_ID.findall(" ".join(str(r) for r in refs)))
    changes = _diff(old_index, new_index)
    stale: dict[str, str] = {}
    for fact_id, kind, snapshot in changes:
        for unit in assessed:
            if _relevant(unit, snapshot, support.get(unit), union_docs):
                stale.setdefault(
                    unit,
                    f"{kind} {fact_id} ({snapshot['document_id']}) "
                    "is relevant and unassessed",
                )
    unassessed = sorted(
        fact_id for fact_id, kind, _ in changes if kind == "new evidence"
    )
    return {"stale": stale, "unassessed": unassessed, "unknown": False}


def _diff(old: dict, new: dict) -> list:
    """(fact id, change kind, snapshot) for added/removed/changed facts."""
    changes = []
    for fact_id in sorted(set(old) | set(new)):
        before, after = old.get(fact_id), new.get(fact_id)
        if before is None:
            changes.append((fact_id, "new evidence", after))
        elif after is None:
            changes.append((fact_id, "withdrawn evidence", before))
        elif before != after:
            changes.append((fact_id, "changed evidence", after))
    return changes


def _relevant(unit: str, snapshot: dict, refs, union_docs: set) -> bool:
    """Whether a changed fact matters to an assessed unit."""
    if unit == "conclusion":
        return bool(snapshot["document_id"]) and snapshot["document_id"] in union_docs
    if not isinstance(refs, list):
        refs = []
    texts = [str(r) for r in refs if isinstance(r, (str, int))]
    if texts and all("investigation state" in t.lower() for t in texts):
        return True
    mentioned = set(DOC_ID.findall(" ".join(texts)))
    if snapshot["document_id"] and snapshot["document_id"] in mentioned:
        return True
    if unit.startswith("finding:") and unit.split(":", 1)[1] == snapshot["item"]:
        return True
    return False
