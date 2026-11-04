"""Each document's text, the source of truth for every quote: the inbox, page images, and
text extraction through the Files API. Used by analyze_document, the case record, and the
memo rules.

Each document's text is extracted once (uploaded through the Files API, then transcribed
by the model), saved under workspace/cache/ keyed by the file's SHA-256, and reused.
Every quote is checked against that saved text (quote_on_page); record_fact refuses a
quote that is not on the stated page.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import cast, TYPE_CHECKING

if TYPE_CHECKING:
    from openai import OpenAI
    from openai.types.responses import Response

from . import bm25 as bm25_rank
from . import redactions
from . import usage
from .settings import model
from .textnorm import normalize

_REGISTRY_LOCK = threading.Lock()
MAX_ATTEMPTS = 2


class DocumentError(ValueError):
    pass


def quote_on_page(quote: str, page_text: str) -> bool:
    q = normalize(quote)
    return len(q) >= 8 and q in normalize(page_text)


# ---- inbox ------------------------------------------------------------------


DOCUMENT_TYPES = {".pdf": "pdf", ".jpg": "image", ".jpeg": "image", ".png": "image",
                  ".txt": "text", ".md": "text"}
_PDF_PAGE = re.compile(rb"/Type\s*/Page(?![a-zA-Z])")


def page_count(path: Path) -> int:
    """Rendered page images if there are any (pages/<ID>-p<n>.png), else pages in the PDF, else 1."""
    rendered = len(list((path.parent / "pages").glob(f"{path.stem}-p*.png")))
    if rendered:
        return rendered
    if path.suffix.lower() in (".txt", ".md"):
        try:
            return max(1, len(path.read_bytes().split(b"\f")))
        except OSError:
            return 1
    if path.suffix.lower() == ".pdf":
        return max(1, len(_PDF_PAGE.findall(path.read_bytes())))
    return 1


def list_documents(root: Path) -> list:
    """Documents currently in workspace/inbox/: id (the file name), file, modality (pdf | image | text), pages, sha256."""
    inbox = root / "workspace/inbox"
    if not inbox.exists():
        return []
    return [
        {
            "id": path.stem,
            "file": path.name,
            "modality": DOCUMENT_TYPES[path.suffix.lower()],
            "pages": page_count(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in sorted(inbox.iterdir())
        if path.is_file() and path.suffix.lower() in DOCUMENT_TYPES
    ]


def get_document(root: Path, document_id: str) -> dict:
    for document in list_documents(root):
        if document["id"] == document_id:
            return document
    raise DocumentError(f"document {document_id!r} is not in the inbox")


def page_image(root: Path, document_id: str, page: int) -> Path:
    """The review platform's 100 dpi render of one page."""
    get_document(root, document_id)
    path = root / "workspace/inbox/pages" / f"{document_id}-p{page}.png"
    if not path.is_file():
        raise DocumentError(f"no page image for {document_id} page {page}")
    return path


# ---- text extraction (Files API) ----------------------------------------------


def ask(
    client: OpenAI, content: list, *, purpose: str = "model_call", ledger=None
) -> str:
    """One JSON-mode model call. Returns the output text.

    ledger is an optional (root, run_id) pair attributing the call's usage.
    Failures are ledgered, then re-raised unchanged.
    """
    start = time.perf_counter()
    try:
        response = cast(
            "Response",
            client.responses.create(
                model=model(),
                input=[{"role": "user", "content": content}],
                text={"format": {"type": "json_object"}},
            ),
        )
    except Exception as error:
        if ledger is not None:
            usage.record_failure(
                ledger[0],
                ledger[1],
                purpose,
                error,
                latency_ms=(time.perf_counter() - start) * 1000,
            )
        raise
    if ledger is not None:
        usage.record(
            ledger[0],
            ledger[1],
            purpose,
            response,
            latency_ms=(time.perf_counter() - start) * 1000,
        )
    return response.output_text


def cache(root: Path) -> Path:
    path = root / "workspace/cache"
    (path / "text").mkdir(parents=True, exist_ok=True)
    (path / "analysis").mkdir(parents=True, exist_ok=True)
    return path


def cached_text(root: Path, document_id: str) -> dict | None:
    """The saved transcription for a document, or None if not extracted yet.

    A quarantined document reads as absent even when a cache
    file exists: suppression is reversible (release restores
    reads), while the bytes stay on reviewer-trusted disk.
    """
    if redactions.is_quarantined(root, document_id):
        return None
    document = get_document(root, document_id)
    path = cache(root) / "text" / f"{document['sha256']}.json"
    return json.loads(path.read_text()) if path.is_file() else None


def page_text(root: Path, document_id: str, page: int) -> str | None:
    text = cached_text(root, document_id)
    if text is None:
        return None
    for entry in text["pages"]:
        if entry["page"] == page:
            return entry["text"]
    return ""


def search_corpus(
    root: Path,
    query: str,
    document_ids: list | None = None,
    flagged: frozenset | set | None = None,
    limit: int = 20,
    window: int = 120,
    meta: dict | None = None,
) -> dict:
    """Keyword search over transcribed pages, grouped document → page → passage.

    Matches the normalized query against each transcribed page and
    returns one passage (a context window around the first match)
    per matching page, with the per-page match count. Only analyzed
    documents are searchable: untranscribed ones are listed under
    "unindexed", flagged confidential ones under "restricted",
    quarantined ones under "quarantined" — none is searched.
    At most `limit` passages return; "truncated" says more
    matched. Raises DocumentError for an empty query or an
    unknown document id.

    Every passage carries machine-usable provenance: `offsets`
    (the match span into the cached page text, None when the
    match cannot be located, as in pure-paraphrase BM25 hits),
    `fingerprint` (the inbox file's sha256), and `jurisdiction` /
    `effective` from the optional per-document `meta` mapping
    (None when unknown — never fabricated). Offsets and
    fingerprints are ints/hex, never source text, so restricted
    content cannot leak through this metadata.

    When substring finds nothing, a BM25 fallback ranks the same
    eligible pages (same flagged/unindexed exclusions): exact
    quotes always win, paraphrases get a second chance, and
    near-miss contradictions stay silent (see agent_kit/bm25).
    """
    needle = normalize(query)
    if len(needle) < 2:
        raise DocumentError("search needs at least two characters of query text")
    inbox = {document["id"]: document for document in list_documents(root)}
    if document_ids is None:
        wanted = sorted(inbox)
    else:
        wanted = []
        for document_id in document_ids:
            if document_id not in inbox:
                raise DocumentError(f"document {document_id!r} is not in the inbox")
            if document_id not in wanted:
                wanted.append(document_id)
    flagged = set(flagged or set())
    meta = meta if isinstance(meta, dict) else {}
    documents = []
    unindexed: list[str] = []
    restricted: list[str] = []
    quarantined: list[str] = []
    kept = 0
    truncated = False
    eligible: list[tuple[str, int, str]] = []
    for document_id in wanted:
        if document_id in flagged:
            restricted.append(document_id)
            continue
        if redactions.is_quarantined(root, document_id):
            quarantined.append(document_id)
            continue
        cached = cached_text(root, document_id)
        if cached is None:
            unindexed.append(document_id)
            continue
        fingerprint = inbox[document_id]["sha256"]
        doc_meta = meta.get(document_id)
        doc_meta = doc_meta if isinstance(doc_meta, dict) else {}
        passages = []
        for entry in sorted(cached.get("pages", []), key=lambda e: e.get("page", 0)):
            text = entry.get("text", "") or ""
            eligible.append((document_id, entry.get("page", 0), text))
            hits = normalize(text).count(needle)
            if not hits:
                continue
            if kept >= limit:
                truncated = True
                continue
            kept += 1
            passages.append(
                {
                    "page": entry.get("page"),
                    "matches_on_page": hits,
                    "passage": _window(text, query, window),
                    "offsets": _span(text, query),
                    "fingerprint": fingerprint,
                    "jurisdiction": doc_meta.get("jurisdiction"),
                    "effective": doc_meta.get("effective"),
                }
            )
        if passages:
            documents.append({"document_id": document_id, "passages": passages})
    if not documents:
        shas = {doc_id: doc["sha256"] for doc_id, doc in inbox.items()}
        documents, truncated = _bm25_fallback(
            query, eligible, limit, window, shas, meta)
    return {
        "query": str(query),
        "documents": documents,
        "passage_count": sum(len(d["passages"]) for d in documents),
        "unindexed": unindexed,
        "restricted": restricted,
        "quarantined": quarantined,
        "truncated": truncated,
    }


def _bm25_fallback(
    query: str, eligible: list[tuple[str, int, str]], limit: int, window: int,
    shas: dict, meta: dict,
) -> tuple[list, bool]:
    """Rank eligible pages when substring found nothing.

    Returns (documents, truncated) in the search_corpus shape.
    Near-miss pages are excluded before ranking; only pages
    scoring within RATIO_CUT of the top page return; an
    all-zero top score stays silent. Passages carry the same
    provenance keys as substring hits; offsets stay None when
    no query token locates on the page.
    """
    terms = bm25_rank.query_terms(query)
    if not terms or not eligible:
        return [], False
    texts = {(doc_id, page): text for doc_id, page, text in eligible}
    units = [
        ((doc_id, page), bm25_rank.query_terms(texts[(doc_id, page)]))
        for doc_id, page, _ in eligible
    ]
    ranked = bm25_rank.rank(
        terms, [(f"{doc_id}\x00{page}", tokens) for (doc_id, page), tokens in units]
    )
    legit = []
    for key, score in ranked:
        doc_id, _, page = key.partition("\x00")
        if bm25_rank.near_miss(query, texts[(doc_id, int(page))]):
            continue
        legit.append((key, score))
    best = legit[0][1] if legit else 0.0
    if best <= 0.0:
        return [], False
    cutoff = best * bm25_rank.RATIO_CUT
    token_sets = {
        f"{doc_id}\x00{page}": set(tokens) for (doc_id, page), tokens in units
    }
    query_set = set(terms)
    documents: list[dict] = []
    by_doc: dict[str, dict] = {}
    kept = 0
    truncated = False

    def _keep(key: str) -> None:
        nonlocal kept, truncated
        doc_id, _, page = key.partition("\x00")
        text = texts[(doc_id, int(page))]
        if kept >= limit:
            truncated = True
            return
        kept += 1
        matched = len(query_set & token_sets[key])
        entry = by_doc.get(doc_id)
        if entry is None:
            entry = {"document_id": doc_id, "passages": []}
            by_doc[doc_id] = entry
            documents.append(entry)
        doc_meta = meta.get(doc_id)
        doc_meta = doc_meta if isinstance(doc_meta, dict) else {}
        entry["passages"].append(
            {
                "page": int(page),
                "matches_on_page": matched,
                "passage": _window(text, query, window),
                "offsets": _span(text, query),
                "fingerprint": shas.get(doc_id),
                "jurisdiction": doc_meta.get("jurisdiction"),
                "effective": doc_meta.get("effective"),
            }
        )

    taken: set[str] = set()
    for key, score in legit:
        if score < cutoff:
            break
        taken.add(key)
        _keep(key)
    if kept:
        # Coverage completion: a content term with no page in
        # the returned set means a whole obligation may be
        # unrepresented, so add the single best-scoring page
        # covering each such term (bounded; still above zero
        # and near-miss-free, since legit was pre-filtered).
        # The anchored cutoff set above is unchanged.
        covered: set[str] = set()
        for key in taken:
            covered |= query_set & token_sets[key]
        missing_terms = [
            term for term in dict.fromkeys(terms) if term not in covered
        ][: bm25_rank.MAX_COMPLETION_TERMS]
        for term in missing_terms:
            if kept >= limit:
                truncated = True
                break
            for key, score in legit:
                if score <= 0.0:
                    break
                if key not in taken and term in token_sets[key]:
                    taken.add(key)
                    _keep(key)
                    break
    if not kept:
        return [], False
    return documents, truncated


def _anchor(text: str, query: str) -> tuple[int, int] | None:
    """First-match (offset, length) of the query in the page text.

    Full-phrase anchor first, then the first normalized token
    found; None when nothing locates (pure-paraphrase BM25 hits).
    Offsets index the cached page text as stored.
    """
    lowered = text.lower()
    anchor = (query or "").strip().lower()
    at = lowered.find(anchor) if anchor else -1
    if at >= 0:
        return at, len(anchor)
    for token in normalize(query).split():
        at = lowered.find(token)
        if at >= 0:
            return at, len(token)
    return None


def _span(text: str, query: str) -> list[int] | None:
    """Match span as [start, end], or None when unlocatable."""
    found = _anchor(text, query)
    if found is None:
        return None
    at, length = found
    return [at, at + length]


def _window(text: str, query: str, window: int) -> str:
    """Context around the query's first match, with truncation marks."""
    anchor = (query or "").strip().lower()
    found = _anchor(text, query)
    at = found[0] if found is not None else 0
    start = max(0, at - window)
    end = min(len(text), at + len(anchor or text[:0]) + window)
    passage = text[start:end].strip()
    if start > 0:
        passage = "… " + passage
    if end < len(text):
        passage = passage + " …"
    return passage


def _file_id(client: OpenAI, root: Path, document: dict) -> str:
    registry_path = cache(root) / "files.json"

    def registry() -> dict:
        return json.loads(registry_path.read_text()) if registry_path.is_file() else {}

    with _REGISTRY_LOCK:
        known = registry().get(document["sha256"])
    if known:
        return known
    with (root / "workspace/inbox" / document["file"]).open("rb") as stream:
        file_id = client.files.create(file=stream, purpose="user_data").id
    with _REGISTRY_LOCK:
        current = registry()
        current[document["sha256"]] = file_id
        registry_path.write_text(json.dumps(current, indent=2))
    return file_id


TRANSCRIBE_PROMPT = (
    "Transcribe document {doc_id} ({pages} page(s)) exactly as written, page by page. "
    "Include headers, table cells, chart labels and values, handwriting, and stamps. "
    "Do not summarize, correct, or reorder. Write [illegible] for text you cannot read. "
    'Return only JSON: {{"pages": [{{"page": 1, "text": "..."}}]}} with one entry per page.'
)


def extract_text(
    client: OpenAI,
    root: Path,
    document_id: str,
    *,
    run_id: str | None = None,
    purpose_prefix: str = "",
) -> dict:
    """Extract once, save under workspace/cache/text/<sha>.json (see the module docstring)."""
    if redactions.is_quarantined(root, document_id):
        raise DocumentError(
            f"{document_id} is quarantined: a render-confirmed bad "
            "redaction hid text from view. A reviewer must release it "
            "(./helper quarantine) before transcription."
        )
    existing = cached_text(root, document_id)
    if existing is not None:
        return existing
    document = get_document(root, document_id)
    file_id = _file_id(client, root, document)
    prompt = TRANSCRIBE_PROMPT.format(doc_id=document["id"], pages=document["pages"])
    ledger = (root, run_id) if run_id else None
    error = ""
    for _attempt in range(MAX_ATTEMPTS):
        output = ask(
            client,
            [
                {
                    "type": "input_text",
                    "text": prompt
                    + (f"\nYour previous output was invalid: {error}" if error else ""),
                },
                {"type": "input_file", "file_id": file_id},
            ],
            purpose=f"{purpose_prefix}extract_text",
            ledger=ledger,
        )
        try:
            parsed = json.loads(output)
            pages = [
                {"page": int(p["page"]), "text": str(p["text"])}
                for p in parsed["pages"]
            ]
            if not pages:
                raise ValueError("no pages")
            break
        except (ValueError, KeyError, TypeError) as problem:
            error = str(problem)
    else:
        raise DocumentError(f"could not extract text from {document_id}: {error}")
    return _save_text(root, document, pages, "files_api", file_id)


def atomic_write(path: Path, text: str) -> None:
    """Write through a per-thread temp file so concurrent writers cannot tear it."""
    tmp = path.with_name(f"{path.name}.{os.getpid()}-{threading.get_ident()}.tmp")
    tmp.write_text(text)
    tmp.replace(path)


def _save_text(
    root: Path,
    document: dict,
    pages: list,
    source: str,
    file_id: str | None = None,
) -> dict:
    if redactions.is_quarantined(root, document["id"]):
        raise DocumentError(
            f"{document['id']} was quarantined mid-transcription: "
            "refusing to persist the transcript."
        )
    record = {
        "document_id": document["id"],
        "sha256": document["sha256"],
        "source": source,
        "file_id": file_id,
        "pages": pages,
    }
    # Atomic swap: two workers may transcribe the same uncached document
    # at once, and a torn cache file would corrupt every later read.
    path = cache(root) / "text" / f"{document['sha256']}.json"
    atomic_write(path, json.dumps(record, indent=1))
    return record


def store_text_pages(root: Path, document_id: str, texts: list[str]) -> dict:
    """Persist a deterministic transcript for an uploaded text file.

    No model involved: page N is the Nth form-feed section of the
    file. Refuses quarantined documents and empty transcripts.
    """
    pages = [{"page": number, "text": text}
             for number, text in enumerate(texts, start=1)]
    if not any(str(page["text"]).strip() for page in pages):
        raise DocumentError(f"{document_id} has no text to transcribe")
    document = get_document(root, document_id)
    return _save_text(root, document, pages, "upload-text")
