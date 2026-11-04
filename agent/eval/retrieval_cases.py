"""Retrieval baseline probes: normalized recall, paraphrase gap, silence guards.

Corpus is the shared tiny case (support.INITIAL, analyzed): DOC-REPORT,
DOC-MINUTES, DOC-MEMO, DOC-REF, one page each. Queries run through
document_text.search_corpus, the same normalized-substring search the
search_corpus tool uses.

MUST_HIT: (id, query, expected [(doc, page)]). Exact and
mechanically-normalized variants (case, whitespace, NBSP, curly
quotes) of present content. The floor requires all of these:
they are the same known strings, transformed.

PARAPHRASE: (id, query, expected [(doc, page)]). Same meaning,
different words. Gated: normalized-substring search cannot
match these by construction, so they pin the BM25 fallback
(agent_kit/bm25) — exact quotes still win, paraphrases get a
second chance, and near-miss contradictions stay silent.

PRECISION: (id, query) with nothing expected back — absent
content, near-miss numbers, and the restricted/unindexed
listings (a search that finds nothing must never imply the
record holds nothing).

Known limits (documented, not gated): single-page corpus, so
page targeting is trivially p1; ranking/window quality is not
measured; substring matching means short numeric queries hit
longer numbers sharing the prefix (e.g. "defects: 2" hits
"defects: 21") — a known property, not asserted here.
"""

from __future__ import annotations

# (variant id, query, expected [(document_id, page)] hits).
MUST_HIT = (
    ("exact-report", "Open Severity 1 defects: 21", [("DOC-REPORT", 1)]),
    ("case-report", "open severity 1 DEFECTS", [("DOC-REPORT", 1)]),
    ("ws-report", "Open   Severity\t1 defects", [("DOC-REPORT", 1)]),
    ("exact-minutes", "Mr. Lindgren confirmed the system is ready",
     [("DOC-MINUTES", 1)]),
    ("nbsp-minutes", "system is\u00a0ready for go-live", [("DOC-MINUTES", 1)]),
    ("dash-minutes", "ready for go\u2013live", [("DOC-MINUTES", 1)]),
    ("exact-ref", "signed by Dana Okafor, Test Manager", [("DOC-REF", 1)]),
    ("span-report", "defects: 21 at period end", [("DOC-REPORT", 1)]),
)

# Same meaning, different words. Baseline: all miss.
PARAPHRASE = (
    ("para-defects", "unresolved critical bugs number twenty-one",
     [("DOC-REPORT", 1)]),
    ("para-ready", "Lindgren said the platform was prepared to launch",
     [("DOC-MINUTES", 1)]),
    ("para-signoff", "approved by test lead Okafor", [("DOC-REF", 1)]),
    ("para-status", "weekly report shows green overall", [("DOC-REPORT", 1)]),
)

# (variant id, query). Nothing may hit.
PRECISION = (
    ("absent-count", "Open Severity 1 defects: 22"),
    ("absent-status", "overall status is red"),
    ("absent-word", "bankruptcy proceedings"),
    ("absent-person", "signed by Dana Whitfield"),
)

# Restricted/unindexed listing checks, handled by the runner with
# explicit flagged/unanalyzed setups rather than plain queries.
LISTINGS = (
    "restricted-memo",
    "unindexed-ref",
)
