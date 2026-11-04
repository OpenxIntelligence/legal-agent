"""Graph-ranking experiment fixtures: synthetic citation networks.

Eight scenarios with independently defined expected behavior,
written from legal reasoning before any method ran on them.
Each fixture: nodes carry text, jurisdiction, date, and status;
edges map citer -> cited; the query drives the text arm (which
also seeds personalized PageRank).

Schema per fixture:
  id, split (dev | heldout), notes, nodes
  {id: {text, jurisdiction, date, status}}, edges {src: [dsts]},
  query, jurisdiction (matter, or None), required [must be in
  method top-3], adverse [must-find subset], must_not_top
  [fail the method if ranked #1].

The split is POST-HOC and disclosed as such: every method was
designed while all eight fixtures were visible (the
irrelevant-landmark fixture directly tuned the dangling
fix, so it is dev). Held-out numbers here guard future
regressions; they prove nothing about generalization. Real
held-out validation awaits live citation data.

Texts use controlled vocabulary so the BM25 text arm behaves
as designed; the graph methods see only edges (plus legal
attributes for the fused method). Nothing here calls a network.
"""

from __future__ import annotations

HARBOR = "harbor defects readiness"


def _node(text, jurisdiction="CA", date="2020-01-01", status="current"):
    return {
        "text": text,
        "jurisdiction": jurisdiction,
        "date": date,
        "status": status,
    }


BASIC_LANDMARK = {
    "id": "basic-landmark",
    "split": "dev",
    "notes": "Sanity: a cited, relevant, current landmark tops every method.",
    "nodes": {
        "L": _node("Harbor defects void readiness claims under the fitness rule."),
        "A": _node("Harbor readiness requires a defect-free survey."),
        "B": _node("Defects found during harbor trials delay signoff."),
        "C": _node("Readiness declared despite open harbor defects."),
        "X": _node("Orchard irrigation schedules for summer months."),
    },
    "edges": {"A": ["L"], "B": ["L"], "C": ["L"]},
    "query": HARBOR,
    "jurisdiction": "CA",
    "required": ["L"],
    "adverse": [],
    "must_not_top": ["X"],
}

CITATION_ONLY = {
    "id": "citation-only-discovery",
    "split": "heldout",
    "notes": "The graph value-add: L shares no query words but is cited "
    "by every text match. Text must miss it; seeded graph must find it. "
    "Distractors keep top-3 selective.",
    "nodes": {
        "L": _node("Maritime fitness doctrine and seaworthiness certification."),
        "A": _node("Harbor defects void a readiness declaration."),
        "B": _node("Readiness claims fail on open harbor defects."),
        "D1": _node("Tax amortization schedules."),
        "D2": _node("Orchard depreciation tables."),
    },
    "edges": {"A": ["L"], "B": ["L"], "D2": ["D1"]},
    "query": HARBOR,
    "jurisdiction": "CA",
    "required": ["L"],
    "adverse": [],
    "must_not_top": [],
}

IRRELEVANT_LANDMARK = {
    "id": "irrelevant-landmark",
    "split": "dev",
    "notes": "A heavily cited off-topic landmark: global graph methods "
    "rank it top (documented failure); seeded/text methods must not.",
    "nodes": {
        "BIG": _node("Tax amortization of orchard equipment."),
        "T1": _node("Orchard tax schedules and depreciation."),
        "T2": _node("Equipment amortization rules for growers."),
        "T3": _node("Depreciation of agricultural assets."),
        "A": _node("Harbor defects void a readiness declaration."),
        "B": _node("Readiness claims fail on open harbor defects."),
    },
    "edges": {"T1": ["BIG"], "T2": ["BIG"], "T3": ["BIG"], "A": ["B"]},
    "query": HARBOR,
    "jurisdiction": "CA",
    "required": ["A", "B"],
    "adverse": [],
    "must_not_top": ["BIG"],
}

NEGATIVE_TREATMENT = {
    "id": "negative-treatment",
    "split": "heldout",
    "notes": "Graph edges carry no negation: pure graph tops the "
    "overruled landmark (documented failure); fused must exclude "
    "it from ranking and surface it flagged instead.",
    "nodes": {
        "L": _node(
            "Harbor defects bar readiness. Harbor defects bar "
            "readiness. OVERRULED.",
            status="overruled",
        ),
        "C1": _node("Citing the old harbor bar."),
        "M": _node("Harbor defects weigh against readiness findings."),
    },
    "edges": {"C1": ["L"], "M": ["L"]},
    "query": HARBOR,
    "jurisdiction": "CA",
    "required": ["M"],
    "adverse": ["L"],
    "must_not_top": ["L"],
}

SUPERSEDED = {
    "id": "superseded-law",
    "split": "dev",
    "notes": "Old landmark out-cites its restatement: pure graph tops "
    "stale law (documented failure); fused must prefer current.",
    "nodes": {
        "OLD": _node(
            "Harbor defects bar readiness. Harbor defects bar "
            "readiness under the 1970 rule.",
            date="1970-01-01",
            status="superseded",
        ),
        "C1": _node("The 1970 harbor defects bar for readiness."),
        "C2": _node("Readiness barred under the old defects rule."),
        "NEW": _node(
            "Harbor defects weigh against readiness findings today.",
            date="2024-01-01",
        ),
    },
    "edges": {"C1": ["OLD"], "C2": ["OLD"], "NEW": ["OLD"]},
    "query": HARBOR,
    "jurisdiction": "CA",
    "required": ["NEW"],
    "adverse": [],
    "must_not_top": ["OLD"],
}

JURISDICTION_CONFLICT = {
    "id": "jurisdiction-conflict",
    "split": "heldout",
    "notes": "Out-of-jurisdiction landmark vs binding local mid "
    "authority: pure graph tops the foreign landmark (documented "
    "failure); fused must require the matter jurisdiction.",
    "nodes": {
        "OUT": _node(
            "Harbor defects bar readiness. Harbor defects bar readiness.",
            jurisdiction="NY",
        ),
        "N1": _node("New York harbor readiness doctrine."),
        "N2": _node("Eastern dock fitness decisions."),
        "HOME": _node("Harbor defects weigh on readiness here."),
    },
    "edges": {"N1": ["OUT"], "N2": ["OUT"]},
    "query": HARBOR,
    "jurisdiction": "CA",
    "required": ["HOME"],
    "adverse": [],
    "must_not_top": ["OUT"],
}

DISCONNECTED = {
    "id": "disconnected-isolate",
    "split": "dev",
    "notes": "A relevant but uncited isolate: pure graph misses it "
    "(documented: graph needs a text backstop); fused union finds it.",
    "nodes": {
        "ISO": _node("Harbor defects void a declaration outright."),
        "A": _node("Harbor defects void readiness claims here."),
        "B": _node("Readiness claims fail on open harbor defects."),
        "D": _node("Orchard tax notes."),
    },
    "edges": {"A": ["B", "D"]},
    "query": HARBOR,
    "jurisdiction": "CA",
    "required": ["ISO", "A", "B"],
    "adverse": [],
    "must_not_top": [],
}

CIRCULAR_INCOMPLETE = {
    "id": "circular-incomplete",
    "split": "heldout",
    "notes": "Robustness: a citation circle plus dropped edges must "
    "complete sanely with the landmark still top — no crash, no NaN.",
    "nodes": {
        "L": _node("Harbor defects void readiness claims."),
        "A": _node("Citing harbor readiness doctrine generally."),
        "B": _node("More harbor readiness commentary here."),
        "Z": _node("Unrelated orchard notes."),
    },
    "edges": {"A": ["B"], "B": ["A"], "A": ["L"]},
    "query": HARBOR,
    "jurisdiction": "CA",
    "required": ["L"],
    "adverse": [],
    "must_not_top": ["Z"],
}

FIXTURES = [
    BASIC_LANDMARK,
    CITATION_ONLY,
    IRRELEVANT_LANDMARK,
    NEGATIVE_TREATMENT,
    SUPERSEDED,
    JURISDICTION_CONFLICT,
    DISCONNECTED,
    CIRCULAR_INCOMPLETE,
]
