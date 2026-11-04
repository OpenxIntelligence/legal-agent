"""Legal citation extraction behind eyecite (Item 3, G1).

Wraps Free Law Project eyecite (BSD-2-Clause, pinned in
requirements.txt with a reporters-db floor) in plain-data
forms the registry and gates can store and compare. Two facts
shape every use:

- Parsing is not verification. "999 U.S. 999" parses because
  U.S. Reports is a known reporter; whether volume 999 page
  999 exists needs a corpus lookup (open gap G3). This module
  reports structure; existence stays unresolved.
- Short forms resolve to antecedents. Id./supra/short cites
  carry the resolved full citation text when the passage
  supplies one, else an empty antecedent.

extract(text) -> [{text, span, kind, reporter, volume, page,
year, court, pin_cite, antecedent}]. Missing fields are "".
CITATION_BACKEND pins the exact extractor versions in every
record that stores parsed citations.
"""

from __future__ import annotations

from importlib import metadata

from eyecite import get_citations, resolve_citations

_KINDS = {
    "FullCaseCitation": "full_case",
    "ShortCaseCitation": "short_case",
    "SupraCitation": "supra",
    "IdCitation": "id",
    "FullLawCitation": "law",
    "FullJournalCitation": "journal",
}


def _backend() -> str:
    try:
        eye = metadata.version("eyecite")
        reporters = metadata.version("reporters-db")
    except metadata.PackageNotFoundError:
        return "unversioned"
    return f"eyecite/{eye}+reporters-db/{reporters}"


CITATION_BACKEND = _backend()


def _form(citation, antecedent: str) -> dict:
    groups = getattr(citation, "groups", {}) or {}
    found = getattr(citation, "metadata", None)
    span = citation.span()
    kind = _KINDS.get(type(citation).__name__, "unknown")
    return {
        "text": citation.matched_text(),
        "span": [span[0], span[1]],
        "kind": kind,
        "reporter": str(groups.get("reporter") or ""),
        "volume": str(groups.get("volume") or ""),
        "page": str(groups.get("page") or ""),
        "year": str(getattr(found, "year", "") or ""),
        "court": str(getattr(found, "court", "") or ""),
        "pin_cite": str(getattr(found, "pin_cite", "") or ""),
        "antecedent": antecedent,
    }


def extract(text: object) -> list[dict]:
    """Every legal citation in the text as plain-data forms.

    Empty or non-string input yields []. Raises nothing for
    unparseable prose: no citations found is an empty list, and
    callers distinguish "parsed nothing" from backend failure by
    the CITATION_BACKEND they record alongside.
    """
    if not isinstance(text, str) or not text.strip():
        return []
    found = list(get_citations(text))
    if not found:
        return []
    antecedents: dict[tuple, str] = {}
    try:
        resolved = resolve_citations(found)
    except Exception:
        resolved = {}
    for resource, cites in (resolved or {}).items():
        head = resource.citation.matched_text()
        head_span = tuple(resource.citation.span())
        for cite in cites or []:
            # The head cites itself; only short forms resolve to it.
            if tuple(cite.span()) != head_span:
                antecedents[tuple(cite.span())] = head
    return [
        _form(cite, antecedents.get(tuple(cite.span()), "")) for cite in found
    ]
