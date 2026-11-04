"""CourtListener resolve-only lookup (G3 spike, EXPERIMENTAL).

Resolves a parsed legal citation to a CourtListener opinion
cluster: case name, court, filing date, and cluster id. This is
plumbing, not verification: a resolution says the citation
matches a cluster, not that the cluster states good law, and
nothing in the filing path consumes resolutions yet (wiring
waits on the matrix's adoption decision).

UNVERIFIED against the live API: this environment has no
CourtListener token (the API requires authentication), so the
endpoint shapes below come from documentation memory, not a
live response. Every assumption is marked ASSUMPTION. Before
any gate wiring, run the live smoke checklist:

1. Provision a free CourtListener token as $COURTLISTENER_TOKEN
   (or $COURTLISTENER_TOKEN_FILE).
2. lookup_citation("531 U.S. 98") returns found with cluster
   "Bush v. Gore" (or the current canonical name).
3. lookup_citation("999 U.S. 999") returns found=False.
4. lookup_docket on a real docket_number + court returns its
   case name; lookup_judge on a sitting judge returns an id
   (if /judges/ 404s, re-point to /people/); lookup_disclosures
   on that id returns that judge's filing years.
5. An invalid token returns available=False with reason auth.
6. Re-check the API terms of use and rate limits.
7. fetch_opinion_text on the Bush cluster returns text over
   1000 chars mentioning Gore; a dissent-only cluster still
   returns text flagged with its type.
8. find_excerpts on that text for "equal protection" returns
   a non-empty window (or documents that the opinion never
   uses the phrase).

Token errors, timeouts, and HTTP failures all surface as
explicit unavailable results: the lookup never silently
returns "not found" for a failure, and never raises for
network conditions (only for programmer errors like a bad
transport).

Endpoints (ASSUMPTION — verify in the smoke test):
- opinions: GET /search/?q="<cite>"&type=o → results[0]
  carries cluster_id; GET /clusters/<id>/ → case_name, court,
  date_filed
- RECAP: GET /dockets/?docket_number=<n>&court=<c> →
  results[0] carries id, case_name, docket_number, court
- judges: GET /judges/?name=<n> → results[0] carries id, name
  (endpoint path ASSUMPTION — may be /people/; the smoke test
  settles it)
- financial disclosures: GET /financial-disclosures/
  ?judge=<id> → results list (endpoint + params ASSUMPTION)
- opinions: GET /clusters/<id>/ → sub_opinions (opinion ids or
  URLs); GET /opinions/<id>/ → {id, type, plain_text, html}.
  Type codes ASSUMPTION ("010combined"-style with combined /
  lead preferred); sub_opinions shape ASSUMPTION.

Oral arguments are deliberately unwired: nothing consumes them.

Opinion text extends resolution toward holdings checks: given a
resolved cluster, fetch_opinion_text returns one opinion's full
text (majority/combined preferred, concurrence/dissent as
fallback), and find_excerpts locates query terms within it for
reviewer inspection. Text never enters the filing path: nothing
is wired to the gate, and the smoke test must still pass first.
"""

from __future__ import annotations

import html as html_module
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser

from agent_kit.secrets import MissingSecretError, resolve as resolve_secret

CL_API = "https://www.courtlistener.com/api/rest/v3"
CL_TIMEOUT_SECONDS = 15
CL_USER_AGENT = "Legal-Agent/0.1 (source-resolution)"
MAX_OPINION_CHARS = 50_000
SUB_OPINION_CAP = 5

TOKEN_NAME = "COURTLISTENER_TOKEN"


class CaselawError(RuntimeError):
    """Programmer error using the lookup (bad transport, bad args)."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _default_transport(method: str, url: str, headers: dict) -> tuple:
    """GET over urllib; returns (status, parsed JSON)."""
    request = urllib.request.Request(url, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=CL_TIMEOUT_SECONDS) as out:
            return out.status, json.loads(out.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as error:
        try:
            body = json.loads(error.read().decode("utf-8") or "{}")
        except ValueError:
            body = {}
        return error.code, body


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Token {token}",
        "Accept": "application/json",
        "User-Agent": CL_USER_AGENT,
    }


def _token_or(token: str | None) -> tuple:
    """Explicit token, else secrets resolution; (None, reason) when missing."""
    if token is not None:
        return token, ""
    try:
        return (
            resolve_secret(
                TOKEN_NAME, hint="provision a free CourtListener token"
            ),
            "",
        )
    except MissingSecretError:
        return None, "no token: set $COURTLISTENER_TOKEN"


def _check_transport(transport) -> None:
    if not callable(transport):
        raise CaselawError("transport must be callable")


def _get(fetch, path: str, params: dict, headers: dict) -> tuple:
    """One GET; (True, payload) or (False, reason string)."""
    url = f"{CL_API}{path}?" + urllib.parse.urlencode(params)
    try:
        status, payload = fetch("GET", url, headers)
    except TimeoutError as error:
        return False, f"timeout: {error}"
    except (OSError, ValueError) as error:
        return False, f"network: {error}"
    if status in (401, 403):
        return False, "auth: token rejected"
    if status != 200 or not isinstance(payload, dict):
        return False, f"unexpected: HTTP {status}"
    return True, payload


def _first_result(payload: dict) -> dict | None:
    results = payload.get("results") or []
    if results and isinstance(results[0], dict):
        return results[0]
    return None


def _result(key: str, query: str, available: bool, found, reason: str) -> dict:
    return {
        "query": query,
        key: found,
        "available": available,
        "found": found is not None and found != [],
        "backend": "courtlistener",
        "fetched_at": _now(),
        "reason": reason,
    }


def lookup_citation(
    cite: str, *, token: str | None = None, transport=None
) -> dict:
    """Resolve one citation string to a CourtListener cluster.

    Returns {query, cluster, available, found, backend,
    fetched_at, reason}: available=False means the lookup itself
    failed (reason: auth/network/timeout/unexpected); found=False
    with available=True means no cluster matched. cluster holds
    {cluster_id, case_name, court, date_filed} with "" for fields
    the API did not supply. transport(method, url, headers) may
    be injected for offline tests.
    """
    cite = str(cite or "").strip()
    if not cite:
        raise CaselawError("cite must be a non-empty citation string")
    fetch = transport or _default_transport
    _check_transport(fetch)
    token, reason = _token_or(token)
    if token is None:
        return _result("cluster", cite, False, None, reason)
    headers = _headers(token)
    ok, payload = _get(fetch, "/search/", {"q": f'"{cite}"', "type": "o"}, headers)
    if not ok:
        return _result("cluster", cite, False, None, payload)
    hit = _first_result(payload)
    if hit is None:
        return _result("cluster", cite, True, None, "")
    cluster_id = hit.get("cluster_id") or hit.get("cluster")
    if isinstance(cluster_id, str) and cluster_id.startswith("http"):
        cluster_id = cluster_id.rstrip("/").rsplit("/", 1)[-1]
    if not cluster_id:
        return _result(
            "cluster", cite, False, None,
            "unexpected: search hit has no cluster",
        )
    ok, cluster = _get(fetch, f"/clusters/{cluster_id}/", {}, headers)
    if not ok:
        return _result("cluster", cite, False, None, cluster)
    return _result(
        "cluster",
        cite,
        True,
        {
            "cluster_id": str(cluster_id),
            "case_name": str(cluster.get("case_name") or ""),
            "court": str(cluster.get("court") or ""),
            "date_filed": str(cluster.get("date_filed") or ""),
        },
        "",
    )


def lookup_docket(
    docket_number: str, court: str, *, token: str | None = None,
    transport=None,
) -> dict:
    """Resolve a RECAP docket number + court to its docket record.

    Same envelope as lookup_citation with a "docket" key holding
    {docket_id, case_name, docket_number, court} ("" for missing
    fields). Endpoint and fields ASSUMPTION until the smoke test.
    """
    docket_number = str(docket_number or "").strip()
    court = str(court or "").strip()
    if not docket_number or not court:
        raise CaselawError("docket_number and court must both be non-empty")
    fetch = transport or _default_transport
    _check_transport(fetch)
    token, reason = _token_or(token)
    query = f"{docket_number} ({court})"
    if token is None:
        return _result("docket", query, False, None, reason)
    ok, payload = _get(
        fetch, "/dockets/",
        {"docket_number": docket_number, "court": court},
        _headers(token),
    )
    if not ok:
        return _result("docket", query, False, None, payload)
    hit = _first_result(payload)
    if hit is None:
        return _result("docket", query, True, None, "")
    return _result(
        "docket",
        query,
        True,
        {
            "docket_id": str(hit.get("id") or ""),
            "case_name": str(hit.get("case_name") or ""),
            "docket_number": str(hit.get("docket_number") or docket_number),
            "court": str(hit.get("court") or court),
        },
        "",
    )


def lookup_judge(
    name: str, *, token: str | None = None, transport=None
) -> dict:
    """Resolve a judge name to its judges-record id and name.

    Same envelope with a "judge" key holding {judge_id, name}.
    Endpoint path ASSUMPTION (/judges/ — may be /people/):
    a 404 "unexpected" from the smoke test settles it.
    """
    name = str(name or "").strip()
    if not name:
        raise CaselawError("name must be a non-empty judge name")
    fetch = transport or _default_transport
    _check_transport(fetch)
    token, reason = _token_or(token)
    if token is None:
        return _result("judge", name, False, None, reason)
    ok, payload = _get(fetch, "/judges/", {"name": name}, _headers(token))
    if not ok:
        return _result("judge", name, False, None, payload)
    hit = _first_result(payload)
    if hit is None:
        return _result("judge", name, True, None, "")
    return _result(
        "judge",
        name,
        True,
        {
            "judge_id": str(hit.get("id") or ""),
            "name": str(hit.get("name") or name),
        },
        "",
    )


def lookup_disclosures(
    judge_id: str, *, token: str | None = None, transport=None
) -> dict:
    """List financial-disclosure records filed by one judge id.

    Same envelope with a "disclosures" list of {record_id, year}
    ("" for missing fields); found=False when the list is empty.
    Endpoint and params ASSUMPTION until the smoke test.
    """
    judge_id = str(judge_id or "").strip()
    if not judge_id:
        raise CaselawError("judge_id must be non-empty (see lookup_judge)")
    fetch = transport or _default_transport
    _check_transport(fetch)
    token, reason = _token_or(token)
    if token is None:
        return _result("disclosures", judge_id, False, None, reason)
    ok, payload = _get(
        fetch, "/financial-disclosures/", {"judge": judge_id},
        _headers(token),
    )
    if not ok:
        return _result("disclosures", judge_id, False, None, payload)
    if not isinstance(payload.get("results"), list):
        return _result(
            "disclosures", judge_id, False, None,
            "unexpected: disclosures payload has no results list",
        )
    return _result(
        "disclosures",
        judge_id,
        True,
        [
            {
                "record_id": str(entry.get("id") or ""),
                "year": str(entry.get("year") or ""),
            }
            for entry in payload["results"]
            if isinstance(entry, dict)
        ],
        "",
    )


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _strip_html(markup: str) -> str:
    """Visible text of an HTML opinion, entities resolved."""
    extractor = _TextExtractor()
    extractor.feed(str(markup or ""))
    return html_module.unescape(" ".join("".join(extractor.parts).split()))


# CourtListener's own preference order (OPINION_TEXT_SOURCE_FIELDS
# in cl/search/models.py, verified 2026-10-30): citation-linked
# HTML first, plain text last. Markup fields strip to text;
# plain_text is used verbatim.
OPINION_TEXT_FIELDS = (
    "html_with_citations",
    "xml_harvard",
    "xml_scan",
    "html_columbia",
    "html_lawbox",
    "html_anon_2020",
    "html",
    "plain_text",
)


def _best_opinion_text(opinion: dict) -> tuple[str, str]:
    """(text, source field) following CourtListener's order."""
    for field in OPINION_TEXT_FIELDS:
        raw = opinion.get(field) or ""
        if field == "plain_text":
            text = str(raw).strip()
        else:
            text = _strip_html(raw)
        if text:
            return text, field
    return "", ""


def _opinion_ref_id(ref) -> str:
    """An opinion id from a sub_opinions entry (id or URL)."""
    if isinstance(ref, int):
        return str(ref)
    text = str(ref or "").strip().rstrip("/")
    if not text:
        return ""
    if text.startswith("http"):
        return text.rsplit("/", 1)[-1]
    return text


def _preferred_opinion_type(opinion_type: str) -> bool:
    """Majority/combined/lead opinions outrank separate writings."""
    lowered = str(opinion_type or "").lower()
    if not lowered:
        return True
    return "combined" in lowered or "lead" in lowered


def fetch_opinion_text(
    cluster_id: str, *, token: str | None = None, transport=None
) -> dict:
    """Fetch one opinion's full text for a resolved cluster id.

    Returns the usual envelope with an "opinion" key holding
    {opinion_id, type, text, text_source, truncated,
    char_count}. The first majority/combined/lead opinion with
    text wins; concurrence or dissent text is the fallback (its
    type flags it); at most SUB_OPINION_CAP sub-opinions fetch.
    Text follows CourtListener's own field order
    (OPINION_TEXT_FIELDS: html_with_citations first,
    plain_text last), truncates at MAX_OPINION_CHARS with
    char_count holding the full length, and names its source
    field. An empty text with found=True means the opinion
    exists but carries no text — check text, not just found.
    Endpoint and type-code shapes ASSUMPTION until the smoke
    test; the field ORDER is verified from provider source.
    """
    cluster_id = str(cluster_id or "").strip()
    if not cluster_id:
        raise CaselawError("cluster_id must be a non-empty cluster id")
    fetch = transport or _default_transport
    _check_transport(fetch)
    token, reason = _token_or(token)
    query = f"cluster:{cluster_id}"
    if token is None:
        return _result("opinion", query, False, None, reason)
    headers = _headers(token)
    ok, cluster = _get(fetch, f"/clusters/{cluster_id}/", {}, headers)
    if not ok:
        return _result("opinion", query, False, None, cluster)
    refs = cluster.get("sub_opinions") or []
    if not isinstance(refs, list) or not refs:
        return _result(
            "opinion", query, True, None, "cluster lists no opinions"
        )
    fallback: dict | None = None
    for ref in refs[:SUB_OPINION_CAP]:
        opinion_id = _opinion_ref_id(ref)
        if not opinion_id:
            continue
        ok, opinion = _get(
            fetch, f"/opinions/{opinion_id}/", {}, headers
        )
        if not ok or not isinstance(opinion, dict):
            reason = opinion if isinstance(opinion, str) else "unexpected"
            return _result("opinion", query, False, None, reason)
        text, text_source = _best_opinion_text(opinion)
        entry = {
            "opinion_id": str(opinion.get("id") or opinion_id),
            "type": str(opinion.get("type") or ""),
            "text": text,
            "text_source": text_source,
            "truncated": len(text) > MAX_OPINION_CHARS,
            "char_count": len(text),
        }
        if len(text) > MAX_OPINION_CHARS:
            entry["text"] = text[:MAX_OPINION_CHARS]
        if not text:
            if fallback is None:
                fallback = entry
            continue
        if _preferred_opinion_type(entry["type"]):
            return _result("opinion", query, True, entry, "")
        if fallback is None or not fallback["text"]:
            fallback = entry
    if fallback is not None:
        note = "" if fallback["text"] else "no opinion text in cluster payload"
        return _result("opinion", query, True, fallback, note)
    return _result(
        "opinion", query, False, None,
        "unexpected: no fetchable sub-opinions",
    )


def find_excerpts(
    text: str, query: str, *, limit: int = 3, window: int = 400
) -> list[dict]:
    """Locate query terms in opinion text for reviewer inspection.

    Pure function, no network: tokenizes the query (stopwords
    dropped, number words folded, same as retrieval ranking),
    clusters term hits within `window` characters, and returns
    up to `limit` windows as [{"text", "terms"}] ranked by
    distinct terms covered, then hit count. Empty when the
    query has no content terms or nothing matches. Hits are
    found by stem (recall); displayed terms stay the
    reviewer's own words, never stems.
    """
    from agent_kit import bm25

    originals = bm25.fold_numbers(bm25.tokenize(query))
    if not originals or not str(text or "").strip():
        return []
    lowered = str(text).lower()
    hits: list[tuple[int, str]] = []
    for original in set(originals):
        needle = bm25.stem(original)
        start = 0
        while True:
            at = lowered.find(needle, start)
            if at < 0:
                break
            hits.append((at, original))
            start = at + 1
    if not hits:
        return []
    hits.sort()
    clusters: list[list[tuple[int, str]]] = []
    for position, term in hits:
        if clusters and position - clusters[-1][0][0] <= window:
            clusters[-1].append((position, term))
        else:
            clusters.append([(position, term)])
    ranked = sorted(
        clusters,
        key=lambda group: (
            -len({term for _, term in group}),
            -len(group),
            group[0][0],
        ),
    )
    excerpts = []
    for group in ranked[: max(1, int(limit))]:
        first = group[0][0]
        last = group[-1][0] + len(group[-1][1])
        center = (first + last) // 2
        start = max(0, center - window // 2)
        excerpt = str(text)[start: start + window].strip()
        excerpts.append(
            {
                "text": excerpt,
                "terms": sorted({term for _, term in group}),
            }
        )
    return excerpts
