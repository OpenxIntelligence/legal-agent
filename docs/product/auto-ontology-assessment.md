# Auto Ontology integration assessment

Assessed 2026-11-03 against NVIDIA/auto-ontology commit `cdaf61e1bd0cf3820d11d72b43bb460816aebbd3`. Read-only source assessment; nothing installed, connected, ingested, or executed against a model or legal dataset.

## Recommendation

Pilot **natural-language queries over a matter-scoped structured evidence snapshot**, with NVIDIA Auto Ontology supplying semantic mapping and SQL generation/execution through a guarded connector. Its value goes beyond a vocabulary library: “Which disputed findings still cite changed documents?” can become joins, filters, and reproducible results that open the original claim and passage in Legal Agent. Keep the existing investigation/review system authoritative.

Start with one synthetic matter and three query families: open review defects, competing claims linked to the same source, and evidence chronology. Add Auto Ontology as an optional backend service behind one Legal Agent tool, not a second product UI or a replacement agent loop. Do not advertise it as connected until an end-to-end source-linked query actually succeeds. Whether it improves accuracy/latency enough to justify the stack remains an experiment.

## What exists today

Legal Agent stores intake in `ui/matters.py` (`matter.json`, document receipts, isolated matter workspace). `ui/ui.py` resolves matter context, run progress, case records, source text and artifact endpoints. `agent/agent_kit/case_record.py` stores run-scoped facts with claim kind, stance, document/page/quote/hash and supersession. `agent/agent_kit/artifacts/kernel.py` provides versioned review rows and deliberately separates quote integrity from semantic support. Current tools in `agent/agent_kit/tools/__init__.py` operate over those records; there is no Auto Ontology adapter or structured SQL evidence projection in this path.

A queryable snapshot would therefore be **new derived infrastructure**, rebuilt from existing records rather than a new writable source of legal truth. Existing JSON records, artifact versions, permission checks and approval gates remain authoritative. Do not relabel extraction as verification or inferred chronology as an adjudicated fact.

## Verified upstream capabilities and limits

The [upstream README](https://github.com/NVIDIA/auto-ontology/blob/cdaf61e1bd0cf3820d11d72b43bb460816aebbd3/README.md) describes semantic metadata plus natural-language database querying. The [execution agent](https://github.com/NVIDIA/auto-ontology/blob/cdaf61e1bd0cf3820d11d72b43bb460816aebbd3/auto_ontology/retrieval/text_to_sql/agents/sql_execution.py) actually calls a database connector; this is an executable analytics pipeline. [SQL validation](https://github.com/NVIDIA/auto-ontology/blob/cdaf61e1bd0cf3820d11d72b43bb460816aebbd3/auto_ontology/retrieval/text_to_sql/agents/sql_parse_validation.py) checks parsing and other query defects, but those checks are not proof of legal correctness or matter authorization.

The [MCP contract](https://github.com/NVIDIA/auto-ontology/blob/cdaf61e1bd0cf3820d11d72b43bb460816aebbd3/docs/mcp.md) offers readiness, answerability, semantic lookup and `ask_question`; the latter returns executed SQL and rows. Its documented 100-row response cap is an output bound, not by itself an execution/memory limit. Readiness and successful glossary lookup must not be shown as proof a database query works.

The [chat SQL wrapper](https://github.com/NVIDIA/auto-ontology/blob/cdaf61e1bd0cf3820d11d72b43bb460816aebbd3/auto_ontology/retrieval/text_to_sql/chat_sql.py) applies a configurable statement timeout (default 30 seconds); unsupported connectors can execute without that cap. Its INFO logging includes generated SQL, so legal literals need log redaction/retention controls. The [DuckDB connector](https://github.com/NVIDIA/auto-ontology/blob/cdaf61e1bd0cf3820d11d72b43bb460816aebbd3/auto_ontology/connectors/duckdb.py) defaults to read-only and supports cancellation. Read-only database mode alone must not be treated as a filesystem/network sandbox.

## Proposed data and capability contract

Build an immutable projection for exactly one authorized `(matter_id, run_id, artifact_version)` snapshot. IDs are composite with run/version because facts such as `F1` repeat across runs. Keep unknown values NULL; do not infer absent fields while projecting.

| Proposed relation | Minimal fields and meaning |
| --- | --- |
| `snapshot` | snapshot ID, matter/run IDs, schema version, source-record digest, creation time |
| `documents` | document ID, display name, recorded/current SHA-256, extraction and access state; no filesystem paths |
| `claims` | claim ID, assertion, kind (`fact`, `legal_rule`, `applicability`, `inference`), stance, superseded ID, recorded time |
| `citations` | claim ID, document ID, page, recorded quote, pinned hash; allowlisted excerpts only |
| `review_rows` | artifact/row/version IDs, assertion, independent structural/support/review states, contested flag |
| `review_defects` | row ID, defect kind/state and recorded rationale |
| `requests` | request ID, text, status, linked claim IDs where actually recorded |
| `events` | event ID, reported time, time precision, event text, claim/citation IDs; populated only when an explicit event representation exists |

Exclude privileged/restricted/quarantined content before projection according to current permissions. No conversation drafts, credentials, provider config, local file locations or unrelated matters. Register domain definitions explicitly: “open,” “disputed,” “superseded,” “unchecked,” and “source changed” are different predicates. A claim timestamp is when recorded, not when its alleged event occurred. Initial chronology queries must report unavailable event data rather than silently treating recording dates as event dates.

Expose a proposed `query_matter_records(question)` tool. The trusted server binds matter/run/snapshot from the active authorized context; the model cannot choose a database, connection string, user identity or filesystem location. Return snapshot ID, normalized executed SQL, bounded rows, truncation/error status and resolvable claim/document/page references. UI results appear as a **structured record query**, alongside conversation/findings, with “Inspect source”; they cannot approve a memo, change evidence, answer a blocking request, or start another investigation. Empty rows mean no matching records in this snapshot, not absence of real-world evidence.

## Execution boundary

`ask_question` executes internally: validating SQL returned in its answer is **too late**. Route every candidate execution, including probes and retries, through a Legal Agent-controlled connector guard, or deploy against a physically isolated database containing only the allowed projection. Catalog/retrieval scope must be isolated too, because metadata or histories can leak before SQL executes. A model-generated `WHERE matter_id = ...` clause is not an access control.

For the pilot, prefer an isolated worker containing one projected DuckDB database, with no host mounts, external network access, extension loading, arbitrary file access or unrelated catalog; a guarded connector must enforce the policy below before calling DuckDB. If the upstream service requires broader access for ingestion, separate its trusted snapshot-build phase from query workers. A production Postgres alternative uses a dedicated per-matter projection database and SELECT-only role; shared-table RLS would require a separate authorization review. Upstream user-level RBAC alone does not establish Legal Agent matter-level isolation.

Proposed initial budgets/policy (to validate, not current features): parse exactly one statement with the matching SQL dialect; permit SELECT and nonrecursive read-only CTEs only; recursively allowlist every referenced relation, column and safe function. Reject DML/DDL, multi-statements, administrative commands, extension operations, dynamic SQL, external table/file/network functions, system catalogs and cross-database references. Deny by default on parse/authorization failures; do not repair policy failures by relaxing rules. Bind user literals as parameters wherever the adapter supports them.

Enforce a 5-second SQL deadline, 60-second total question deadline, at most three query attempts, 100 returned rows/256 KiB, bounded cell sizes, 256 MiB worker memory and cancellation that actually terminates execution. Fetch at most 101 rows to detect truncation; a LIMIT alone does not bound aggregates or joins. Use a bounded worker pool and kill workers exceeding budgets. Preserve the generated query plus rejection reason internally without leaking legal text to general logs.

## Dependencies, benefit and acceptance

The [upstream dependency manifest](https://github.com/NVIDIA/auto-ontology/blob/cdaf61e1bd0cf3820d11d72b43bb460816aebbd3/pyproject.toml) requires Python 3.12–3.13 and brings LangGraph/LangChain, SQLGlot, DuckDB, pandas and multiple database drivers. Its documented deployment also adds FastAPI, Postgres/pgvector, a Next.js authentication gateway and model/embedding configuration. Legal Agent currently has a much smaller Python dependency list. Run a pinned sidecar with separate dependencies; do not merge the entire stack into `agent/requirements.txt`. Hosted inference may receive question text, metadata and selected results: deployment/model authorization must be explicit before real records are sent. No GPU purchase is implied by this recommendation.

This cost is defensible for repeatable relational analysis across many structured claims/reviews. It is less compelling for the current small intake of up to ten documents or one source lookup. Measure correctness, provenance coverage, latency and model cost against direct deterministic record queries and the existing agent on the same synthetic fixtures. Keep ordinary source retrieval for narrative legal questions, and retain qualified human legal review.

Before connection is considered ready, require:

1. Three real synthetic end-to-end questions return correct rows and resolvable citations; inspect source from the workspace, repeat against a pinned snapshot, and visibly mark stale results after document or artifact revision.
2. Negative isolation tests: a second matter with identical document/fact IDs and a secret marker never appears in SQL results, schema discovery, term search, conversation history, errors or cached results. Test changed/missing matter context, unauthorized snapshot IDs, revoked access, excluded privileged sources and malicious instructions inside records. Cache keys include authorized principal, matter, snapshot and policy version.
3. Negative execution tests: UNION/CTE/subquery escapes, cross-database/schema access, DML hidden in CTEs, semicolon batches, file/network/table functions, catalog enumeration, long-running joins/recursive queries, oversized cells and cancellation all fail closed without executing forbidden operations. Verify all retry/probe paths use the guarded connector. Busy timeouts and connection failures produce explicit errors, never fabricated legal answers.

This is a proposed integration contract, not evidence that Auto Ontology is currently connected or that its upstream defaults already enforce these controls.

## Executable adapter boundary and phased status

A second source pass confirmed the smallest **full upstream flow** is a separate Python worker calling [`stream_agent_response(payload)`](https://github.com/NVIDIA/auto-ontology/blob/cdaf61e1bd0cf3820d11d72b43bb460816aebbd3/auto_ontology/retrieval/text_to_sql/main.py), rather than a nonexistent `OntologyAgent(question, database)` constructor. The following is an integration contract, not installed or executable Legal Agent code:

```python
from auto_ontology.retrieval.text_to_sql.main import stream_agent_response
from auto_ontology.utils.retriever import (
    get_data_objects_retriever, get_semantic_objects_retriever,
)

payload = {
    "question": question,
    "connectors": [guarded_snapshot_connector],
    "data_retriever": get_data_objects_retriever(),
    "semantic_retriever": get_semantic_objects_retriever(),
    "target_db": guarded_snapshot_connector.database_name,
    "prediction": False,
    "acronyms": approved_glossary,  # [{"name": ..., "description": ...}]
}
for event in stream_agent_response(payload):
    # Validate and translate bounded events into Legal Agent's response contract.
    pass
```

`question`, nonempty `connectors`, and `data_retriever` are required at runtime. The semantic retriever is optional, but omitting it skips semantic searches; it does **not** remove catalog infrastructure. Events include `step`, `sql`, `result` with `answer`, and `error`; successful SQL answers include `sql_code`, `sql_columns`, and `sql_response_from_db`. An emitted SQL event or nonempty SQL string alone does not prove successful execution. Enforce completion/error/result-shape checks and retain the guarded connector's actual execution receipt.

The [SQLDatabase contract](https://github.com/NVIDIA/auto-ontology/blob/cdaf61e1bd0cf3820d11d72b43bb460816aebbd3/auto_ontology/connectors/base.py) supports an injected guarded connector with `execute(sql, parameters=None, *, timeout_s=None) -> pandas.DataFrame`, `supports_statement_timeout=True`, `dialect="duckdb"`, and a server-bound `database_name`. Implement its constructor, `get_tables`, `get_columns`, `get_queries`, `get_views`, `get_pks`, `get_fks`, and `close`; metadata methods must describe only the authorized snapshot. The built-in constructor is `DuckDBDatabase(connection_string, *, read_only=True)`. Its constructor itself executes `SELECT current_database()`; blindly subclassing it with a strict `execute` override can reject initialization. Prefer composition or a dedicated connector with explicit trusted metadata operations. Never pass an unguarded connector alongside it or expose its raw connection through a separate query path. All probes and retries need the same enforcement.

Verified infrastructure constraints:

- [`Retriever(vdb, embed_params)`](https://github.com/NVIDIA/auto-ontology/blob/cdaf61e1bd0cf3820d11d72b43bb460816aebbd3/auto_ontology/utils/retriever.py) uses PostgresVDB and a live embedding endpoint. The factory functions construct pgvector-backed collections. Candidate preparation also calls catalog DAL methods for table, attribute and join context. A dummy retriever plus DuckDB is not equivalent to the upstream ontology flow; provision/migrate/ingest the catalog and matching embeddings first. A direct Python adapter can avoid the Next.js UI, but cannot simply discard these backend requirements.
- Postgres configuration is `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_DATABASE`. Catalog migrations and ingestion must be verified against the pinned version before marking ready. Keep its writable catalog connection separate from the read-only legal snapshot connector.
- Model roles use `REASONING_{ENDPOINT,API_KEY,MODEL}`, `NON_REASONING_{ENDPOINT,API_KEY,MODEL}`, and `EMBED_{ENDPOINT,API_KEY,MODEL}`, falling back field-by-field to `DEFAULT_MODELS_*`; [`model_config.py`](https://github.com/NVIDIA/auto-ontology/blob/cdaf61e1bd0cf3820d11d72b43bb460816aebbd3/auto_ontology/utils/model_config.py) defines precedence. Upstream reads model configuration and builds the graph during import, so configure only the sidecar's environment before importing; restart it after a configuration change. Legal Agent's `MODEL_API_*` variables are not this contract, and its existing key must not be silently forwarded. Chat and embedding endpoints need separately verified compatibility; having a chat key does not establish working embeddings.
- Upstream requires Python `>=3.12,<3.14`. This Mac has installed 3.13.13 and 3.12.13 interpreters; default `python3` is 3.14.7. No interpreter download is necessary, but upstream dependencies are not established by this inspection. Its manifest includes a pinned external Apache Ossie converter dependency and multiple database drivers; installation/import compatibility remains untested.

Deliver in phases: **record explorer foundation** (local deterministic projections and fixed read-only queries; no upstream connection or NL-to-SQL claim), **adapter provisioned** (pinned worker, catalog, ingestion, models and guarded connector independently ready), then **verified ontology query pilot** (three real synthetic end-to-end questions plus the isolation/limits failures above). Until that final evidence exists, UI status must continue to say Auto Ontology is disconnected. No upstream stack, remote code, provider call, or real legal data was installed or executed during this assessment.
