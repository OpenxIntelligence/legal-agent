"""Typed-graph discovery ranking: what to inspect next.

Builds a small typed graph over checklist issues, recorded facts,
and inbox documents, then runs a deterministic query-conditioned
rank (personalized PageRank over the eligible graph) seeded on
one issue. The output suggests inspection order only.

Connectedness is not truth, applicable authority, or evidentiary
strength. Opposition edges are typed but never treated as negative
endorsement. Every suggestion carries that limitation in its
reason, and unexamined documents keep a coverage quota so an
isolated but decisive source stays discoverable.
"""

from __future__ import annotations

from typing import Any

from . import document_text, sources

DISCOVERY_ONLY = "discovery only: connectedness is not support"


def _inbox_ids(root: Any) -> list[str]:
    try:
        return [
            document["id"]
            for document in document_text.list_documents(root)
            if isinstance(document, dict) and document.get("id")
        ]
    except (OSError, ValueError):
        return []


def build_graph(case: Any) -> dict:
    """Issue/fact/document/branch nodes with typed edges.

    Returns {"nodes": {id: {"kind": ...}}, "edges": [(src, dst, rel)]}.
    Node ids are prefixed ("issue:", "fact:", "doc:", "branch:") so
    the kinds never collide. Edges are factual record links: a fact
    recorded for an issue with its stance, a fact extracted from a
    document, an issue's legal branch. Traversal treats them as
    undirected; the relation label is preserved for reasons.
    """
    nodes: dict[str, dict] = {}
    edges: list[tuple[str, str, str]] = []
    for item_id in case.data.get("checklist", {}) or {}:
        nodes[f"issue:{item_id}"] = {"kind": "issue"}
    for fact in case.data.get("facts", []) or []:
        if not isinstance(fact, dict) or "id" not in fact:
            continue
        fact_node = f"fact:{fact['id']}"
        nodes[fact_node] = {"kind": "fact"}
        edges.append((fact_node, f"issue:{fact.get('item')}", fact.get("stance", "recorded")))
        if fact.get("document_id"):
            doc_node = f"doc:{fact['document_id']}"
            nodes.setdefault(doc_node, {"kind": "document"})
            edges.append((fact_node, doc_node, "extracted_from"))
    for branch_id, branch in (case.data.get("branches") or {}).items():
        if not isinstance(branch, dict):
            continue
        branch_node = f"branch:{branch_id}"
        nodes[branch_node] = {"kind": "branch"}
        edges.append((f"issue:{branch.get('issue')}", branch_node, "applicability"))
    for document_id in _inbox_ids(case.root):
        nodes.setdefault(f"doc:{document_id}", {"kind": "document"})
    return {"nodes": nodes, "edges": edges}


def rank(
    graph: dict,
    seeds: list[str],
    damping: float = 0.85,
    iterations: int = 50,
) -> dict[str, float]:
    """Personalized PageRank scores over the graph, deterministically.

    r = (1-d) p + d P^T r, with restart vector p uniform over the
    seed nodes and P row-stochastic over undirected adjacency.
    Fixed iteration count and sorted node order keep the result
    deterministic; dangling mass restarts over the seeds
    (strongly-preferential PPR), so rank never leaks into a
    disconnected cluster. Unknown seed ids are ignored; with no
    usable seeds every node scores 0.
    """
    nodes = sorted(graph.get("nodes", {}))
    if not nodes:
        return {}
    adjacency: dict[str, set[str]] = {node: set() for node in nodes}
    for src, dst, _rel in graph.get("edges", []):
        if src in adjacency and dst in adjacency and src != dst:
            adjacency[src].add(dst)
            adjacency[dst].add(src)
    usable = [seed for seed in seeds if seed in adjacency]
    if not usable:
        return dict.fromkeys(nodes, 0.0)
    restart = {node: (1.0 / len(usable) if node in usable else 0.0) for node in nodes}
    scores = dict(restart)
    for _ in range(max(0, iterations)):
        nxt = {}
        for node in nodes:
            total = 0.0
            for peer in nodes:
                neighbors = adjacency[peer]
                if not neighbors:
                    total += scores[peer] * restart[node]
                elif node in neighbors:
                    total += scores[peer] / len(neighbors)
            nxt[node] = (1.0 - damping) * restart[node] + damping * total
        scores = nxt
    return scores


def _out_of_scope_repo(repo: str, region: str) -> bool:
    entry = sources.REPOS.get(repo or "")
    return bool(entry) and entry.get("region") != region


def suggest_inspections(
    case: Any,
    issue_id: str,
    limit: int = 5,
    scope: str | None = None,
) -> list[dict]:
    """Ranked inspection suggestions for one issue (discovery only).

    Candidates are inbox documents and facts recorded for the
    issue, ordered by graph score. At least two unexamined inbox
    documents are always included when they exist, so isolated
    evidence stays discoverable. When a scope resolves to a region,
    noted sources from other regions are excluded; documents
    without noted provenance stay, labelled unknown. A scope that
    needs clarification or is unknown filters nothing.
    """
    graph = build_graph(case)
    scores = rank(graph, [f"issue:{issue_id}"])
    read = set(case.data.get("read_ids", []) or [])
    inbox = _inbox_ids(case.root)
    noted_sources = case.data.get("sources") or {}
    region = None
    if scope:
        region = sources.normalize_scope(scope).get("region")
    excluded: set[str] = set()
    if region:
        for document_id in inbox:
            noted = noted_sources.get(document_id) or {}
            if _out_of_scope_repo(noted.get("repo", ""), region):
                excluded.add(document_id)
    suggestions: dict[str, dict] = {}
    for document_id in inbox:
        if document_id in excluded:
            continue
        examined = document_id in read
        noted = noted_sources.get(document_id) or {}
        provenance = noted.get("repo") or "unknown provenance"
        suggestions[f"doc:{document_id}"] = {
            "id": document_id,
            "kind": "document",
            "score": round(scores.get(f"doc:{document_id}", 0.0), 4),
            "examined": examined,
            "provenance": provenance,
        }
    for fact in case.data.get("facts", []) or []:
        if not isinstance(fact, dict) or fact.get("item") != issue_id:
            continue
        if (fact.get("document_id") or "") in excluded:
            continue
        suggestions[f"fact:{fact['id']}"] = {
            "id": fact["id"],
            "kind": "fact",
            "score": round(scores.get(f"fact:{fact['id']}", 0.0), 4),
            "examined": True,
            "provenance": fact.get("document_id", ""),
        }
    ranked = sorted(suggestions.values(), key=lambda row: (-row["score"], row["id"]))
    picks = ranked[: max(0, limit)]
    picked_ids = {row["id"] for row in picks}
    have = sum(1 for row in picks if row["kind"] == "document" and not row["examined"])
    for row in ranked:
        if have >= 2:
            break
        if row["kind"] == "document" and not row["examined"] and row["id"] not in picked_ids:
            picks.append(row)
            picked_ids.add(row["id"])
            have += 1
    for row in picks:
        if row["kind"] == "fact":
            row["reason"] = (
                f"recorded for {issue_id} with graph score {row['score']}; "
                f"{DISCOVERY_ONLY}"
            )
        elif not row["examined"]:
            row["reason"] = (
                f"unexamined inbox document ({row['provenance']}); coverage pick, "
                f"{DISCOVERY_ONLY}"
            )
        else:
            row["reason"] = (
                f"graph score {row['score']} from {issue_id} ({row['provenance']}); "
                f"{DISCOVERY_ONLY}"
            )
    return picks
