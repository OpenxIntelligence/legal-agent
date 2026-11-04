"""Bounded retrieval experiments (RET-3). Nothing here ships directly.

Each candidate searches the same matter roots through the same
result shape as document_text.search_corpus: substring primary
(exact quotes always win), flagged/unindexed exclusions, and
the near-miss guard are preserved by every arm. Candidates vary
only the fallback ranking, so differences isolate ranking
effects. Tune on dev; report heldout once.

Arms:
  base      current substring + BM25 fallback (reference)
  minterms  BM25 fallback requires >=2 distinct matched terms
  cut       relaxed RATIO_CUT + page cap (sibling recall)
  stem      BM25 over stemmed tokens (morphology)
  version   superseded docs demoted below current (explicit map)
  aspects   comma/and-split subqueries merged by best rank
  combo     minterms + cut + stem + version + aspects together
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_kit import bm25 as bm25_rank  # noqa: E402
from agent_kit import document_text  # noqa: E402
from agent_kit.document_text import (  # noqa: E402
    _window,
    cached_text,
    list_documents,
    normalize,
)

SUPERSEDED = {"MB-PLAN-V1", "HB-CONTRACT-ORIG"}
CURRENT_OF = {"MB-PLAN-V1": "MB-PLAN-V2"}


def _eligible(root: Path, flagged: set):
    """(eligible pages, unindexed, restricted) mirroring search_corpus."""
    eligible, unindexed, restricted = [], [], []
    for document in sorted(list_documents(root), key=lambda d: d["id"]):
        doc_id = document["id"]
        if doc_id in flagged:
            restricted.append(doc_id)
            continue
        cached = cached_text(root, doc_id)
        if cached is None:
            unindexed.append(doc_id)
            continue
        for entry in sorted(cached.get("pages", []),
                            key=lambda e: e.get("page", 0)):
            eligible.append((doc_id, entry.get("page", 0),
                             entry.get("text", "") or ""))
    return eligible, unindexed, restricted


def _shape(documents: list, query: str, unindexed: list,
           restricted: list, truncated: bool) -> dict:
    return {
        "query": str(query),
        "documents": documents,
        "passage_count": sum(len(d["passages"]) for d in documents),
        "unindexed": unindexed,
        "restricted": restricted,
        "truncated": truncated,
    }


def _substring(query: str, eligible: list, limit: int,
               window: int) -> tuple[list, bool]:
    needle = normalize(query)
    by_doc: dict[str, dict] = {}
    documents: list[dict] = []
    kept, truncated = 0, False
    for doc_id, page, text in eligible:
        hits = normalize(text).count(needle)
        if not hits:
            continue
        if kept >= limit:
            truncated = True
            continue
        kept += 1
        entry = by_doc.get(doc_id)
        if entry is None:
            entry = {"document_id": doc_id, "passages": []}
            by_doc[doc_id] = entry
            documents.append(entry)
        entry["passages"].append(
            {"page": page, "matches_on_page": hits,
             "passage": _window(text, query, window)}
        )
    return documents, truncated


def _stem_token(token: str) -> str:
    """Compact early-stopping suffix stemmer (ASCII content tokens).

    Covers the corpus morphology (plurals, -ed/-ing/-ly/-tion
    families, employment/employed). Deliberately small: this is
    an experiment, not a linguistics claim.
    """
    word = token
    if len(word) <= 3:
        return word
    for suffix, cut in (("ational", 5), ("tional", 2), ("ment", 0),
                        ("ization", 0), ("fulness", 4), ("ousness", 4),
                        ("iveness", 3), ("ition", 0)):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            word = word[: len(word) - len(suffix) + cut] or word
            break
    if word.endswith("ies") and len(word) > 4:
        word = word[:-3] + "y"
    elif word.endswith("es") and len(word) > 4:
        word = word[:-2]
    elif word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        word = word[:-1]
    if word.endswith("eed"):
        pass
    elif word.endswith("ed") and len(word) > 4:
        word = word[:-2]
    elif word.endswith("ing") and len(word) > 5:
        word = word[:-3]
    if word.endswith("ly") and len(word) > 4:
        word = word[:-2]
    if word.endswith("tion") and len(word) > 5:
        word = word[:-4]
    return word or token


def _terms(text: str, stem: bool) -> list[str]:
    # Frozen pre-stemming pipeline: the RET-3 record measured
    # these terms. query_terms now stems (adopted ranker change);
    # the arms keep measuring what they measured.
    tokens = bm25_rank.fold_numbers(bm25_rank.tokenize(text))
    return [_stem_token(t) for t in tokens] if stem else tokens


def _rank_pages(query: str, eligible: list, *, stem: bool,
                min_terms: int, min_idf_mass: float,
                ratio_cut: float, cap: int,
                demote_superseded: bool) -> list[tuple[str, int]]:
    """Ranked (doc, page) under experimental parameters."""
    terms = _terms(query, stem)
    if not terms or not eligible:
        return []
    texts = {(d, p): t for d, p, t in eligible}
    units = [((d, p), _terms(t, stem)) for d, p, t in eligible]
    ranked = bm25_rank.rank(
        terms, [(f"{d}\x00{p}", toks) for (d, p), toks in units]
    )
    token_sets = {f"{d}\x00{p}": set(toks) for (d, p), toks in units}
    query_set = set(terms)
    doc_freq: dict[str, int] = {}
    for toks in token_sets.values():
        for token in toks:
            doc_freq[token] = doc_freq.get(token, 0) + 1
    count = len(token_sets)
    legit = []
    for key, score in ranked:
        doc_id, _, page = key.partition("\x00")
        if bm25_rank.near_miss(query, texts[(doc_id, int(page))]):
            continue
        matched = query_set & token_sets[key]
        if len(matched) < min_terms:
            continue
        mass = sum(bm25_rank.idf(doc_freq.get(t, 0), count)
                   for t in matched)
        if mass < min_idf_mass:
            continue
        legit.append((key, score))
    best = legit[0][1] if legit else 0.0
    if best <= 0.0:
        return []
    cutoff = best * ratio_cut
    out = []
    for key, score in legit:
        if score < cutoff or len(out) >= cap:
            break
        doc_id, _, page = key.partition("\x00")
        out.append((doc_id, int(page)))
    if demote_superseded:
        out = sorted(out, key=lambda pair: pair[0] in SUPERSEDED)
    return out


def _fallback(query: str, eligible: list, limit: int, window: int,
              **knobs) -> tuple[list, bool]:
    ranked = _rank_pages(query, eligible, **knobs)
    by_doc: dict[str, dict] = {}
    documents: list[dict] = []
    kept, truncated = 0, False
    texts = {(d, p): t for d, p, t in eligible}
    for doc_id, page in ranked:
        if kept >= limit:
            truncated = True
            continue
        kept += 1
        entry = by_doc.get(doc_id)
        if entry is None:
            entry = {"document_id": doc_id, "passages": []}
            by_doc[doc_id] = entry
            documents.append(entry)
        entry["passages"].append(
            {"page": page, "matches_on_page": 1,
             "passage": _window(texts[(doc_id, page)], query, window)}
        )
    return documents, truncated


def _search(root: Path, query: str, flagged: set, limit: int = 20,
            window: int = 120, **knobs) -> dict:
    eligible, unindexed, restricted = _eligible(root, flagged)
    documents, truncated = _substring(query, eligible, limit, window)
    if not documents:
        documents, truncated = _fallback(
            query, eligible, limit, window, **knobs)
    return _shape(documents, query, unindexed, restricted, truncated)


BASE_KNOBS = {"stem": False, "min_terms": 1, "min_idf_mass": 0.0,
              "ratio_cut": 0.7, "cap": 20, "demote_superseded": False}


def search_base(root, query, flagged=None, **kw):
    return _search(root, query, set(flagged or set()), **BASE_KNOBS)


def search_minterms(root, query, flagged=None, **kw):
    knobs = dict(BASE_KNOBS, min_terms=2)
    return _search(root, query, set(flagged or set()), **knobs)


def search_cut(root, query, flagged=None, **kw):
    knobs = dict(BASE_KNOBS, ratio_cut=0.5, cap=6)
    return _search(root, query, set(flagged or set()), **knobs)


def search_stem(root, query, flagged=None, **kw):
    knobs = dict(BASE_KNOBS, stem=True)
    return _search(root, query, set(flagged or set()), **knobs)


def search_version(root, query, flagged=None, **kw):
    knobs = dict(BASE_KNOBS, demote_superseded=True)
    return _search(root, query, set(flagged or set()), **knobs)


def _split_aspects(query: str) -> list[str]:
    parts = [p.strip() for chunk in query.split(",")
             for p in chunk.split(" and ") if p.strip()]
    return parts if len(parts) > 1 else [query]


def search_aspects(root, query, flagged=None, **kw):
    """Subquery per comma/and-aspect, merged by best rank."""
    flagged = set(flagged or set())
    aspects = _split_aspects(query)
    if len(aspects) == 1:
        return _search(root, query, flagged, **BASE_KNOBS)
    eligible, unindexed, restricted = _eligible(root, flagged)
    merged: dict[tuple[str, int], int] = {}
    for aspect in aspects:
        documents, _ = _substring(aspect, eligible, 20, 120)
        if not documents:
            documents, _ = _fallback(aspect, eligible, 20, 120,
                                      **BASE_KNOBS)
        rank = 0
        for document in documents:
            for passage in document["passages"]:
                rank += 1
                key = (document["document_id"], passage["page"])
                merged[key] = min(merged.get(key, rank), rank)
    ordered = sorted(merged, key=lambda k: (merged[k], k))
    by_doc: dict[str, dict] = {}
    documents = []
    texts = {(d, p): t for d, p, t in eligible}
    for doc_id, page in ordered[:20]:
        entry = by_doc.get(doc_id)
        if entry is None:
            entry = {"document_id": doc_id, "passages": []}
            by_doc[doc_id] = entry
            documents.append(entry)
        entry["passages"].append(
            {"page": page, "matches_on_page": 1,
             "passage": _window(texts[(doc_id, page)], query, 120)}
        )
    return _shape(documents, query, unindexed, restricted, False)


def _combo_search(root, query, flagged, knobs):
    aspects = _split_aspects(query)
    if len(aspects) == 1:
        return _search(root, query, flagged, **knobs)
    eligible, unindexed, restricted = _eligible(root, flagged)
    merged: dict[tuple[str, int], int] = {}
    for aspect in aspects:
        documents, _ = _substring(aspect, eligible, 20, 120)
        if not documents:
            documents, _ = _fallback(aspect, eligible, 20, 120, **knobs)
        rank = 0
        for document in documents:
            for passage in document["passages"]:
                rank += 1
                key = (document["document_id"], passage["page"])
                merged[key] = min(merged.get(key, rank), rank)
    ordered = sorted(merged, key=lambda k: (merged[k], k))
    if knobs["demote_superseded"]:
        ordered = sorted(ordered, key=lambda pair: pair[0] in SUPERSEDED)
    by_doc: dict[str, dict] = {}
    documents = []
    texts = {(d, p): t for d, p, t in eligible}
    for doc_id, page in ordered[:20]:
        entry = by_doc.get(doc_id)
        if entry is None:
            entry = {"document_id": doc_id, "passages": []}
            by_doc[doc_id] = entry
            documents.append(entry)
        entry["passages"].append(
            {"page": page, "matches_on_page": 1,
             "passage": _window(texts[(doc_id, page)], query, 120)}
        )
    return _shape(documents, query, unindexed, restricted, False)


def _knob_search(knobs):
    def run(root, query, flagged=None, **kw):
        return _search(root, query, set(flagged or set()), **knobs)
    return run


search_idf2 = _knob_search(dict(BASE_KNOBS, min_idf_mass=2.0))
search_idf3 = _knob_search(dict(BASE_KNOBS, min_idf_mass=3.0))
search_idf4 = _knob_search(dict(BASE_KNOBS, min_idf_mass=4.0))


def search_combo(root, query, flagged=None, **kw):
    """All lexical knobs together (tuned values live here)."""
    knobs = {"stem": True, "min_terms": 2, "min_idf_mass": 0.0,
             "ratio_cut": 0.5, "cap": 6, "demote_superseded": True}
    return _combo_search(root, query, set(flagged or set()), knobs)


def search_combo2(root, query, flagged=None, **kw):
    """Refined combo: aspects + fixed stem + version + IDF mass."""
    knobs = {"stem": True, "min_terms": 1, "min_idf_mass": 3.0,
             "ratio_cut": 0.7, "cap": 20, "demote_superseded": True}
    return _combo_search(root, query, set(flagged or set()), knobs)


ARMS = {
    "base": search_base,
    "minterms": search_minterms,
    "cut": search_cut,
    "stem": search_stem,
    "version": search_version,
    "aspects": search_aspects,
    "idf2": search_idf2,
    "idf3": search_idf3,
    "idf4": search_idf4,
    "combo": search_combo,
    "combo2": search_combo2,
}
