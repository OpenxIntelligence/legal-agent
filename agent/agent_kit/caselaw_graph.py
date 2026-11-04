"""CourtListener citation-graph adapter (offline, token-free).

Builds a citation edge map from opinion records for
agent_kit.pagerank: {citing-opinion-id: [cited-opinion-ids]}.
Keys are CourtListener OPINION ids (the thing that does the
citing), not cluster ids: clusters group opinions, and an
edge must name the actual citing opinion and the actual
cited opinion. Cluster membership is a separate mapping and
is not inferred here.

Upstream fetch: UNWRITTEN. The endpoint SHAPE is verified
from provider source (2026-10-30): v4 `opinions-cited`
carries citing_opinion + cited_opinion (opinion-detail
URLs) with one row per pair (unique_together) and a depth
count. Live behavior (auth, pagination) still needs the
token smoke test. This adapter starts AFTER the fetch, from
records you hand it. Feed it verified API payloads, never
guesses. Depth counts citation frequency, not authority:
never weight edges by it for legal conclusions.

Each (citing, cited) pair appears once: edges record that a
citation exists, never how many times text was matched, so
citation counts alone cannot manufacture edges. Self-cites
drop; unparseable refs report under "unresolved" instead of
silently vanishing.

Centrality is discovery fuel, not authority: a high rank
means "look here", never binding, valid, well-treated,
in-jurisdiction, or supportive of any claim (see
docs/adr-graph-calibration-steering.md Phase B).
"""

from __future__ import annotations


def _ref_id(ref) -> str | None:
    """An opinion id from an int, numeric string, or opinion URL."""
    if isinstance(ref, bool):
        return None
    if isinstance(ref, int):
        return str(ref) if ref > 0 else None
    text = str(ref or "").strip()
    if not text:
        return None
    if text.startswith("http"):
        text = text.rstrip("/").rsplit("/", 1)[-1]
    return text if text.isdigit() and int(text) > 0 else None


def cited_edges(records: list) -> dict:
    """Edge map plus audit lists from opinion records.

    Each record is {id, cites?}: id names the citing opinion,
    cites lists cited-opinion refs (ints, numeric strings, or
    /opinions/<id>/ URLs). Returns {"edges", "nodes",
    "unresolved", "dropped_self"}. Deterministic: sorted nodes,
    sorted outlinks.
    """
    edges: dict[str, set[str]] = {}
    unresolved: list[dict] = []
    dropped_self = 0
    for record in records or []:
        if not isinstance(record, dict):
            continue
        citing = _ref_id(record.get("id"))
        if citing is None:
            continue
        outlinks = edges.setdefault(citing, set())
        cites = record.get("cites") or []
        if not isinstance(cites, list):
            continue
        for ref in cites:
            cited = _ref_id(ref)
            if cited is None:
                unresolved.append({"citing": citing, "ref": ref})
            elif cited == citing:
                dropped_self += 1
            else:
                outlinks.add(cited)
    nodes = sorted(edges)
    cited_only = set()
    for outlinks in edges.values():
        cited_only.update(outlinks)
    for cited in cited_only:
        edges.setdefault(cited, set())
    return {
        "edges": {node: sorted(edges[node]) for node in sorted(edges)},
        "nodes": nodes,
        "unresolved": unresolved,
        "dropped_self": dropped_self,
    }
