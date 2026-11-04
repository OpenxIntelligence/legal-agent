"""Bounded SQL over supplied legal records, never over files or an external DB.

This is a projection of recorded facts and reviews, not an ontology, extraction,
or legal verification service. The caller must supply current source access
decisions, including revocations in other runs of the same matter.
"""
import hashlib
import json
import math
import re
import sqlite3
import time

MAX_SQL_BYTES = 8192
MAX_RECORDS = 10000
MAX_TABLE_RECORDS = 2500
MAX_INPUT_BYTES = 4_000_000
MAX_INPUT_CELL_BYTES = 12000
MAX_ROWS = 200
MAX_CELL_BYTES = 4096
MAX_OUTPUT_BYTES = 131072
MAX_COLUMNS = 32
MAX_SECONDS = 0.25
MAX_VM_STEPS = 100000

_FIELDS = {
    "documents": "document_id file state sha256 source_handle",
    "claims": "claim_id origin item text status stance kind source_handle",
    "citations": "citation_id owner_type owner_id artifact_id document_id page quote source_handle",
    "requests": "request_id item need rule purpose status response at",
    "review_rows": "artifact_id row_id kind item assertion assertion_version finding_status structural_outcome semantic_outcome review_decision source_handle",
    "review_defects": "artifact_id defect_id category severity target kind rationale status action source_handle",
}
SCHEMA = {table: ["matter_id", "run_id", "record_key"] + fields.split() for table, fields in _FIELDS.items()}
PRESETS = {
    "documents": {"label": "Available documents", "sql": "SELECT document_id, file, state, source_handle FROM documents ORDER BY document_id"},
    "claims": {"label": "Recorded claims and findings", "sql": "SELECT claim_id, origin, item, text, status, stance, source_handle FROM claims ORDER BY origin, claim_id"},
    "citations": {"label": "Source citations", "sql": "SELECT owner_type, owner_id, artifact_id, document_id, page, quote, source_handle FROM citations ORDER BY owner_type, owner_id, citation_id"},
    "open-requests": {"label": "Open evidence requests", "sql": "SELECT request_id, item, need, rule, purpose, status FROM requests WHERE status = 'open' ORDER BY request_id"},
    "review-defects": {"label": "Recorded review defects", "sql": "SELECT artifact_id, defect_id, category, severity, target, rationale, status, action, source_handle FROM review_defects ORDER BY artifact_id, defect_id"},
}


class QueryError(ValueError):
    """A safe, non-SQL-echoing error suitable for the local HTTP layer."""


def _dict(value):
    return value if isinstance(value, dict) else {}


def _records(value):
    if not isinstance(value, list):
        return []
    if len(value) > MAX_RECORDS:
        raise QueryError("The supplied record snapshot is too large.")
    return [item for item in value if isinstance(item, dict)]


def _text(value):
    # Never stringify arbitrary nested objects into a queryable data dump.
    if value is None:
        return None
    if not isinstance(value, (str, int, float, bool)):
        return None
    text = str(value)
    if len(text.encode("utf-8")) > MAX_INPUT_CELL_BYTES:
        raise QueryError("A supplied record field exceeds the snapshot limit.")
    return text


def _page(value):
    return value if type(value) is int and 1 <= value <= 1000000 else None


def build_snapshot(matter: dict, run: dict | None, documents: list[dict]) -> dict:
    """Project one selected matter/run; all inputs are supplied in memory.

    `documents` must contain only current scope metadata and access decisions;
    accessible=False, source_available=False and quarantined=True are denied.
    A supplied run must occur in matter.runs. This complements the caller's
    scoped filesystem lookup; matching ids alone do not authenticate records.
    """
    matter = _dict(matter)
    matter_id = matter.get("id")
    if not isinstance(matter_id, str) or not re.fullmatch(r"matter-[a-f0-9]{32}", matter_id):
        raise QueryError("A valid scoped matter snapshot is required.")
    run_id = ""
    case = {}
    if run is not None:
        if not isinstance(run, dict):
            raise QueryError("A valid scoped run snapshot is required.")
        run_id = run.get("id")
        if not isinstance(run_id, str) or not re.fullmatch(r"run-[A-Za-z0-9_-]{1,120}", run_id):
            raise QueryError("A valid scoped run snapshot is required.")
        if run_id not in {entry.get("id") for entry in _records(matter.get("runs"))}:
            raise QueryError("The run does not belong to the supplied matter snapshot.")
        if run.get("matter_id", matter_id) != matter_id:
            raise QueryError("The run does not belong to the supplied matter snapshot.")
        case = _dict(run.get("case"))
        if case.get("run_id", run_id) != run_id:
            raise QueryError("The run record has a different scope.")
    denied = {flag.get("document_id") for flag in _records(case.get("flags")) if flag.get("kind") == "privileged"}
    tables = {name: [] for name in SCHEMA}
    handles = {}
    total_bytes = 0
    total_records = 0
    omitted = 0
    keys = {name: set() for name in SCHEMA}

    def add(table, identity, **fields):
        nonlocal total_bytes, total_records
        row = {name: _text(fields.get(name)) for name in SCHEMA[table]}
        row.update(matter_id=matter_id, run_id=run_id,
                   record_key=json.dumps([matter_id, run_id, table, *identity], separators=(",", ":")))
        if row["record_key"] in keys[table]:
            raise QueryError("The supplied record snapshot contains duplicate record keys.")
        keys[table].add(row["record_key"])
        for column in ("page", "assertion_version"):
            if column in row:
                row[column] = fields.get(column) if type(fields.get(column)) is int else None
        size = len(json.dumps(row, ensure_ascii=True).encode())
        total_bytes += size
        total_records += 1
        if total_records > MAX_RECORDS or len(tables[table]) >= MAX_TABLE_RECORDS or total_bytes > MAX_INPUT_BYTES:
            raise QueryError("The supplied record snapshot exceeds projection limits.")
        tables[table].append(row)

    # Matter receipts bind ids to this matter. Current metadata cannot introduce
    # an unrelated document even when accidentally supplied by a caller.
    receipts = {doc.get("id", doc.get("document_id")): doc for doc in _records(matter.get("documents"))}
    allowed = {}
    for doc in _records(documents):
        doc_id = doc.get("id", doc.get("document_id"))
        if not isinstance(doc_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", doc_id):
            continue
        receipt = receipts.get(doc_id)
        if receipt is None or doc_id in denied or doc.get("accessible") is False or doc.get("source_available") is False or doc.get("quarantined") or receipt.get("state") == "quarantined":
            omitted += 1
            continue
        if doc_id in allowed:
            raise QueryError("The supplied document snapshot contains duplicate ids.")
        allowed[doc_id] = doc

    def source(document_id, page=None):
        if document_id not in allowed:
            return None
        page = _page(page)
        handle = "src-" + hashlib.sha256(json.dumps([matter_id, run_id, document_id, page]).encode()).hexdigest()[:24]
        handles[handle] = {"handle": handle, "matter_id": matter_id, "run_id": run_id,
                           "document_id": document_id, "page": page}
        return handle

    for doc_id in sorted(allowed):
        doc = allowed[doc_id]
        receipt = receipts[doc_id]
        add("documents", [doc_id], document_id=doc_id, file=doc.get("file", receipt.get("file")),
            state=doc.get("state", receipt.get("state")), sha256=doc.get("sha256", doc.get("sha12", receipt.get("sha256"))),
            source_handle=source(doc_id))

    # Without recorded source lineage there is no safe way to determine whether
    # text inherited a revoked source. Omit it when any matter source is denied.
    restricted = bool(denied) or any(doc_id not in allowed for doc_id in receipts)

    def citations_for(record):
        citations = _records(record.get("citations"))
        if any(citation.get("document_id") not in allowed for citation in citations):
            return None
        return citations

    def add_citations(owner_type, owner_id, citations, artifact_id=None):
        for index, citation in enumerate(citations):
            citation_id = f"{owner_type}:{artifact_id or ''}:{owner_id}:{index + 1}"
            add("citations", [owner_type, artifact_id, owner_id, index + 1], citation_id=citation_id,
                owner_type=owner_type, owner_id=owner_id, artifact_id=artifact_id,
                document_id=citation.get("document_id"), page=_page(citation.get("page")),
                quote=citation.get("quote"), source_handle=source(citation.get("document_id"), citation.get("page")))

    for index, fact in enumerate(_records(case.get("facts"))):
        if fact.get("document_id") not in allowed:
            omitted += 1
            continue
        fact_id = _text(fact.get("id")) or f"fact-{index + 1}"
        add("claims", ["fact", fact_id, index], claim_id=fact_id, origin="recorded_fact", item=fact.get("item"),
            text=fact.get("claim"), status=None, stance=fact.get("stance"), kind=fact.get("kind"),
            source_handle=source(fact.get("document_id"), fact.get("page")))
        add_citations("fact", fact_id, [fact])

    memo = _dict(case.get("memo"))
    for index, finding in enumerate(_records(memo.get("findings"))):
        citations = citations_for(finding)
        if citations is None or (restricted and not citations):
            omitted += 1
            continue
        finding_id = f"finding-{index + 1}"
        add("claims", ["memo_finding", finding_id], claim_id=finding_id, origin="memo_finding", item=finding.get("item"),
            text=finding.get("summary"), status=finding.get("status"), stance=None, kind="finding",
            source_handle=source(citations[0].get("document_id"), citations[0].get("page")) if citations else None)
        add_citations("memo_finding", finding_id, citations)

    for index, request in enumerate(_records(case.get("requests"))):
        if restricted:
            omitted += 1
            continue
        request_id = _text(request.get("id")) or f"request-{index + 1}"
        add("requests", [request_id, index], request_id=request_id, **{name: request.get(name) for name in ("item", "need", "rule", "purpose", "status", "response", "at")})

    artifacts = _dict(case.get("artifacts"))
    if len(artifacts) > MAX_TABLE_RECORDS:
        raise QueryError("The supplied record snapshot is too large.")
    for artifact_key in sorted(artifacts):
        instance = _dict(artifacts[artifact_key])
        if instance.get("template") != "citation-claim-audit":
            continue
        if instance.get("matter", run_id) != run_id:
            omitted += 1
            continue
        artifact_id = _text(instance.get("id")) or _text(artifact_key)
        included_rows = set()
        for index, row in enumerate(_records(instance.get("rows"))):
            citations = citations_for(row)
            if citations is None or (restricted and not citations):
                omitted += 1
                continue
            row_id = _text(row.get("row_id")) or f"row-{index + 1}"
            included_rows.add(row_id)
            checks = _dict(row.get("checks"))
            add("review_rows", [artifact_id, row_id, index], artifact_id=artifact_id, row_id=row_id, kind=row.get("kind"),
                item=row.get("item"), assertion=row.get("assertion"), assertion_version=row.get("assertion_version"),
                finding_status=row.get("finding_status"), structural_outcome=_dict(checks.get("structural")).get("outcome"),
                semantic_outcome=_dict(checks.get("semantic")).get("outcome"), review_decision=_dict(instance.get("review")).get("decision"),
                source_handle=source(citations[0].get("document_id"), citations[0].get("page")) if citations else None)
            add_citations("review_row", row_id, citations, artifact_id)
        for index, defect in enumerate(_records(instance.get("defects"))):
            sources = _records(defect.get("sources"))
            if (restricted and not sources) or any(entry.get("document_id") not in allowed for entry in sources) or (defect.get("kind") == "row" and defect.get("target") not in included_rows):
                omitted += 1
                continue
            defect_id = _text(defect.get("id")) or f"defect-{index + 1}"
            add("review_defects", [artifact_id, defect_id, index], artifact_id=artifact_id, defect_id=defect_id,
                **{name: defect.get(name) for name in ("category", "severity", "target", "kind", "rationale", "status", "action")},
                source_handle=source(sources[0].get("document_id"), sources[0].get("page")) if sources else None)

    digest = hashlib.sha256(json.dumps({"matter_id": matter_id, "run_id": run_id, "tables": tables}, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()
    return {"id": digest, "matter_id": matter_id, "run_id": run_id, "tables": tables, "source_handles": handles,
            "counts": {name: len(rows) for name, rows in tables.items()},
            "warnings": ["Recorded claims and checks are not independently verified by this query.",
                         f"{omitted} records were omitted because sources were unavailable, outside access scope, or untraceable while a source was excluded."]}


_FUNCTIONS = {"abs", "avg", "coalesce", "count", "glob", "ifnull", "iif", "instr", "length", "like", "lower", "ltrim", "max", "min", "nullif", "replace", "round", "rtrim", "substr", "substring", "sum", "total", "trim", "typeof", "upper"}


def query_snapshot(snapshot: dict, sql: str) -> dict:
    """Execute one bounded read-only SELECT against a fresh in-memory DB.

    No URI, extension loader, user function, filesystem or persistent connection
    is supplied. The SQLite authorizer allowlists reads and scalar functions;
    its default decision is denial, including PRAGMA/ATTACH/system schemas.
    """
    if not isinstance(sql, str) or not sql.strip() or len(sql.encode("utf-8")) > MAX_SQL_BYTES:
        raise QueryError("Supply one SELECT query of at most 8192 bytes.")
    # WITH is permitted only if every operation passes the read-only authorizer.
    first = re.sub(r"\A(?:\s+|--[^\n]*(?:\n|$)|/\*.*?\*/)*", "", sql, flags=re.S)
    if not re.match(r"(?:SELECT|WITH)\b", first, re.I):
        raise QueryError("Only a read-only SELECT is permitted.")
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("tables"), dict):
        raise QueryError("A bounded matter snapshot is required.")
    matter_id, run_id = snapshot.get("matter_id"), snapshot.get("run_id")
    db = sqlite3.connect(":memory:", cached_statements=0)
    try:
        db.enable_load_extension(False)
        db.execute("PRAGMA temp_store=MEMORY")
        db.execute("PRAGMA cache_size=-1024")
        count = 0
        size = 0
        for table, columns in SCHEMA.items():
            definitions = ", ".join('"' + column + '" ' + ("INTEGER" if column in ("page", "assertion_version") else "TEXT") for column in columns)
            db.execute(f'CREATE TABLE "{table}" ({definitions})')
            supplied = snapshot["tables"].get(table)
            if not isinstance(supplied, list) or len(supplied) > MAX_TABLE_RECORDS:
                raise QueryError("The supplied record snapshot exceeds projection limits.")
            values = []
            for row in supplied:
                count += 1
                if not isinstance(row, dict) or row.get("matter_id") != matter_id or row.get("run_id") != run_id:
                    raise QueryError("A projected record has a different scope.")
                cells = []
                for column in columns:
                    cell = row.get(column)
                    if not isinstance(cell, (str, int, float, type(None))):
                        raise QueryError("The supplied projection contains an invalid value.")
                    cell_size = len(str(cell).encode()) if cell is not None else 0
                    if cell_size > MAX_INPUT_CELL_BYTES:
                        raise QueryError("A supplied record field exceeds the snapshot limit.")
                    size += cell_size
                    cells.append(cell)
                if count > MAX_RECORDS or size > MAX_INPUT_BYTES:
                    raise QueryError("The supplied record snapshot exceeds projection limits.")
                values.append(cells)
            db.executemany(f'INSERT INTO "{table}" VALUES ({",".join("?" for _ in columns)})', values)
        db.commit()
        db.execute("PRAGMA query_only=ON")
        db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 65536)
        db.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, MAX_SQL_BYTES)
        db.setlimit(sqlite3.SQLITE_LIMIT_COLUMN, MAX_COLUMNS)
        db.setlimit(sqlite3.SQLITE_LIMIT_COMPOUND_SELECT, 20)
        db.setlimit(sqlite3.SQLITE_LIMIT_EXPR_DEPTH, 40)
        db.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
        db.setlimit(sqlite3.SQLITE_LIMIT_LIKE_PATTERN_LENGTH, 1024)

        def authorize(action, arg1, arg2, database, trigger):
            if action == sqlite3.SQLITE_SELECT:
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_READ and arg1 in SCHEMA and ((database == "main" and arg2 in SCHEMA[arg1]) or (database in ("main", None) and arg2 == "")):
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() in _FUNCTIONS:
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY

        db.set_authorizer(authorize)
        deadline = time.monotonic() + MAX_SECONDS
        steps = 0

        def progress():
            nonlocal steps
            steps += 100
            return int(steps > MAX_VM_STEPS or time.monotonic() > deadline)

        db.set_progress_handler(progress, 100)
        cursor = db.execute(sql)
        columns = [entry[0] for entry in cursor.description or []]
        if not columns or len(columns) > MAX_COLUMNS:
            raise QueryError("Only bounded SELECT results are supported.")
        output_size = 4096 + len(json.dumps(columns, ensure_ascii=True).encode())
        rows = []
        truncated = False
        cells_truncated = 0
        used_handles = {}
        known_handles = _dict(snapshot.get("source_handles"))
        for result in cursor:
            if time.monotonic() > deadline:
                raise QueryError("The query exceeded its execution budget.")
            if len(rows) >= MAX_ROWS:
                truncated = True
                break
            values = []
            row_handles = {}
            for cell in result:
                if isinstance(cell, str):
                    if cell in known_handles:
                        handle = known_handles[cell]
                        if not isinstance(handle, dict) or handle.get("matter_id") != matter_id or handle.get("run_id") != run_id:
                            raise QueryError("A projected source handle has a different scope.")
                        row_handles[cell] = handle
                    encoded = cell.encode("utf-8")
                    if len(encoded) > MAX_CELL_BYTES:
                        cell = encoded[:MAX_CELL_BYTES].decode("utf-8", errors="ignore")
                        cells_truncated += 1
                elif isinstance(cell, float) and not math.isfinite(cell):
                    cell = None
                elif not isinstance(cell, (int, float, type(None))):
                    raise QueryError("The query returned an unsupported value.")
                values.append(cell)
            candidate_handles = dict(used_handles, **row_handles)
            candidate_size = output_size + len(json.dumps(values, ensure_ascii=True).encode())
            # Bound source metadata as part of the output too.
            if candidate_size + len(json.dumps(list(candidate_handles.values()), ensure_ascii=True).encode()) > MAX_OUTPUT_BYTES:
                truncated = True
                break
            rows.append(values)
            used_handles = candidate_handles
            output_size = candidate_size
        return {"columns": columns, "rows": rows, "row_count": len(rows), "truncated": truncated,
                "cells_truncated": cells_truncated, "source_handles": list(used_handles.values()),
                "snapshot": {"id": snapshot.get("id"), "matter_id": matter_id, "run_id": run_id},
                "limits": {"rows": MAX_ROWS, "cell_bytes": MAX_CELL_BYTES, "output_bytes": MAX_OUTPUT_BYTES,
                           "seconds": MAX_SECONDS, "vm_steps": MAX_VM_STEPS}}
    except sqlite3.Error:
        raise QueryError("Query rejected: use a bounded SELECT over the supplied record tables and supported functions.") from None
    finally:
        db.close()
