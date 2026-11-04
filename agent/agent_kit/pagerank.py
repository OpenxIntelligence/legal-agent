"""PageRank authority scoring over legal citation graphs.

A citation is a vote: an opinion cited by many opinions —
especially by itself-cited opinions — carries more weight
than an uncited one. This module scores that structure with
the standard PageRank power iteration (damping 0.85,
dangling nodes distribute uniformly), for steering retrieval
rankings and weighting holdings by authority.

Scores are RELATIVE ranks, not probabilities. Nothing here
calibrates a score into P(correct): that needs labeled
relevance data we do not have (see the judge-calibration
track). Any consumer treating these as probabilities without
that calibration is manufacturing certainty.

Live-data seam (key-gated): build the edge map from
CourtListener once the token lands — cluster cited_by counts
give inbound weight, opinion citation extraction gives edges.
Nothing is wired to retrieval or the gate until that data
exists; this module ships with synthetic-graph tests only.

Variants: rank() is global PageRank; personalized() restarts
on seed nodes (query-conditioned discovery); hits_authority()
returns HITS authority scores (endorsement by good hubs). All
three are relative ranks, never probabilities.
"""

from __future__ import annotations

DAMPING = 0.85
TOLERANCE = 1e-6
MAX_ITERATIONS = 100


def rank(
    edges: dict[str, list[str]],
    *,
    damping: float = DAMPING,
    tolerance: float = TOLERANCE,
    max_iterations: int = MAX_ITERATIONS,
) -> dict[str, float]:
    """PageRank scores for a citation graph (node -> nodes it cites).

    Nodes appearing only as citation targets join with no
    outlinks. Synchronous power iteration from a uniform start,
    so results are deterministic for a given edge map. Returns
    {node: score} summing to 1.0 ({} for an empty graph).
    Raises ValueError on a non-list adjacency entry.
    """
    nodes = set(edges)
    for cited in edges.values():
        if not isinstance(cited, list):
            raise ValueError("adjacency entries must be lists of node ids")
        nodes.update(str(target) for target in cited)
    ordered = sorted(nodes)
    count = len(ordered)
    if not count:
        return {}
    outlinks = {
        node: [str(target) for target in edges.get(node, [])] for node in ordered
    }
    scores = {node: 1.0 / count for node in ordered}
    base = (1.0 - damping) / count
    for _ in range(max_iterations):
        dangling = sum(
            scores[node] for node in ordered if not outlinks[node]
        )
        shared = damping * dangling / count
        updated = {}
        for node in ordered:
            total = base + shared
            for source in ordered:
                if node in outlinks[source]:
                    total += (
                        damping * scores[source] / len(outlinks[source])
                    )
            updated[node] = total
        drift = sum(
            abs(updated[node] - scores[node]) for node in ordered
        )
        scores = updated
        if drift < tolerance:
            break
    total = sum(scores.values()) or 1.0
    return {node: scores[node] / total for node in ordered}


def _node_order(edges: dict[str, list[str]]) -> tuple[list[str], dict]:
    """Sorted node list plus validated outlink map (shared by variants)."""
    nodes = set(edges)
    for cited in edges.values():
        if not isinstance(cited, list):
            raise ValueError("adjacency entries must be lists of node ids")
        nodes.update(str(target) for target in cited)
    ordered = sorted(nodes)
    outlinks = {
        node: [str(target) for target in edges.get(node, [])] for node in ordered
    }
    return ordered, outlinks


def personalized(
    edges: dict[str, list[str]],
    seeds: list[str],
    *,
    damping: float = DAMPING,
    tolerance: float = TOLERANCE,
    max_iterations: int = MAX_ITERATIONS,
) -> dict[str, float]:
    """Personalized PageRank: restarts land on seed nodes only.

    r = (1-d) p + d P^T r with p uniform over usable seeds.
    Dangling nodes redistribute to the seed distribution (not
    uniformly): uniform dangling leaks walk mass into
    disconnected components and breaks personalization — the
    graph experiment proved an off-topic landmark cluster
    siphoning rank through that leak. Unknown seeds are
    ignored; with no usable seeds every node scores 0.
    Deterministic for a given edge map and seed list.
    """
    ordered, outlinks = _node_order(edges)
    count = len(ordered)
    if not count:
        return {}
    usable = [str(seed) for seed in seeds if str(seed) in outlinks]
    if not usable:
        return dict.fromkeys(ordered, 0.0)
    restart = {
        node: (1.0 / len(usable) if node in usable else 0.0)
        for node in ordered
    }
    scores = dict(restart)
    for _ in range(max_iterations):
        dangling = sum(
            scores[node] for node in ordered if not outlinks[node]
        )
        updated = {}
        for node in ordered:
            total = damping * dangling * restart[node]
            for source in ordered:
                if outlinks[source] and node in outlinks[source]:
                    total += (
                        damping * scores[source] / len(outlinks[source])
                    )
            updated[node] = (1.0 - damping) * restart[node] + total
        drift = sum(
            abs(updated[node] - scores[node]) for node in ordered
        )
        scores = updated
        if drift < tolerance:
            break
    total = sum(scores.values()) or 1.0
    return {node: scores[node] / total for node in ordered}


def hits_authority(
    edges: dict[str, list[str]],
    *,
    tolerance: float = TOLERANCE,
    max_iterations: int = MAX_ITERATIONS,
) -> dict[str, float]:
    """HITS authority scores: endorsed by good hubs, normalized to 1.

    Alternates authority <- sum of hub scores of citers and hub
    <- sum of authority scores of cited, L2-normalized per round.
    Deterministic for a given edge map.
    """
    import math

    ordered, outlinks = _node_order(edges)
    if not ordered:
        return {}
    authority = dict.fromkeys(ordered, 1.0)
    hub = dict.fromkeys(ordered, 1.0)
    for _ in range(max_iterations):
        cited_by: dict[str, list[str]] = {node: [] for node in ordered}
        for source in ordered:
            for target in outlinks[source]:
                if target in cited_by:
                    cited_by[target].append(source)
        new_auth = {
            node: sum(hub[source] for source in cited_by[node])
            for node in ordered
        }
        new_hub = {
            node: sum(authority[target] for target in outlinks[node])
            for node in ordered
        }
        auth_norm = math.sqrt(sum(v * v for v in new_auth.values())) or 1.0
        hub_norm = math.sqrt(sum(v * v for v in new_hub.values())) or 1.0
        new_auth = {node: v / auth_norm for node, v in new_auth.items()}
        new_hub = {node: v / hub_norm for node, v in new_hub.items()}
        drift = sum(
            abs(new_auth[node] - authority[node]) for node in ordered
        )
        authority, hub = new_auth, new_hub
        if drift < tolerance:
            break
    total = sum(authority.values()) or 1.0
    return {node: authority[node] / total for node in ordered}
