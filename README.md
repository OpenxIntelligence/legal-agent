# Legal Agent

A local, AI-native workspace for investigating legal questions against documents. Keep the conversation, findings, editable review, and sources together; inspect the evidence before relying on an answer.

## Start locally

Use Python 3.11 or newer. The isolated demo needs no API key or Python dependencies:

```sh
./legal-agent --demo
```

Open **http://127.0.0.1:8765**. Prepare a question, upload text, Markdown, or PDF documents, and save context. Demo mode does not investigate or answer your question: model calls are disabled, and it never loads `agent/.env`. Saved demo work persists in `.local/demo/`. Synthetic examples illustrate review mechanics, not model accuracy.

Use `--port 8877` to change the port, or `--demo-root /path/to/empty-folder` for a separate demo workspace. The launcher refuses to adopt a nonempty folder it does not own.

## Connect a model

```sh
python3 -m venv agent/.venv
agent/.venv/bin/python -m pip install -r agent/requirements.txt
# Preserve any existing local configuration.
test -e agent/.env || cp agent/.env.example agent/.env
./legal-agent --live
```

Open **Settings** to save a model API key. The field is write-only: saved keys are never returned to the browser. The server writes `agent/.env` with owner-only permissions; the file is plaintext, not encrypted, and is ignored by Git. A configured key is not proof that the provider accepts it.

Configure `MODEL_API_BASE_URL` and `MODEL_API_MODEL` in `agent/.env` for your provider. The included defaults use the Meta Model API through the OpenAI Python SDK. Other providers must support the API and tool/document behavior this runtime uses; changing the URL alone does not establish compatibility. An optional `COURTLISTENER_TOKEN` enables configured caselaw lookups. Keep `VERIFICATION=on` for verification and review gates. `CASE_DIR` selects the optional command-line case; new workspace matters use their own saved question and records.

Starting or resuming a live investigation sends matter content to the configured provider and may incur charges. Workspace actions use slices of up to 10 turns. Set `RUN_TOKEN_BUDGET` in local configuration if you need a per-run token budget; this is not a billing limit.

## Work in a matter

1. Describe the question and relevant jurisdiction, dates, and requested output. Upload the records you want examined.
2. Start the investigation. Follow the Copilot conversation beside findings and work products. Recorded activity shows attempted work, not verified conclusions.
3. Open source citations and the local **Record explorer** to inspect documents, claims, citations, requests, and review records. These read-only queries make no model call.
4. Answer open requests or send directions. Messages saved during an active slice are queued for a later resume; a saved receipt does not confirm application.
5. Open an evidence review, edit claims, check sources, and save a versioned review. Export the saved review separately from the original agent draft and conversation; unsent edits are excluded.

New documents require a new investigation so the agent uses the updated records. Earlier runs remain available in run history. Draft messages persist per investigation in browser storage when available; saved matter records persist on the local server. PDF upload is not a guarantee of successful extraction or safe contents: the workspace shows extraction and quarantine states explicitly.

Runner ownership survives server restarts. When an interrupted run offers **Recover saved run**, recovery updates saved state without calling the model. Inspect activity and records before choosing Resume: tool effects after the last checkpoint may already exist, and continuation may repeat work. A run without a usable checkpoint requires a new investigation.

## Execution and limits

This is a trusted local application, not a public multi-user service. The server binds to loopback. Agent tools run with your account’s permissions; matter folders and child-process credential filtering do not provide an operating-system sandbox. Settings reports execution permissions, recovery, database connections, and external trace export status. External database and telemetry services are not activated by the UI.

The agent can record evidence, counterevidence, gaps, chronology, and review decisions. Some checks use a model; others compare quotes or structured records. Neither tool completion nor deterministic checks establish legal correctness. Have a qualified legal professional review the evidence, applicable law, jurisdiction, deadlines, and final output.

## Offline validation

From the repository root, after installing dependencies:

```sh
(cd agent && LEGAL_AGENT_FAKE_MODEL=1 VERIFICATION=on .venv/bin/python -m unittest discover -s tests)
PYTHON="$PWD/agent/.venv/bin/python" agent/.venv/bin/python ui-harness/journey.py
node ui-harness/workspace-state.test.mjs
node ui-harness/recovery_validate.mjs
PYTHON="$PWD/agent/.venv/bin/python" sh ui-harness/matter_run.sh
PYTHON="$PWD/agent/.venv/bin/python" sh ui-harness/settings_run.sh
```

Browser checks require Node with built-in WebSocket support and Chromium. Set `BROWSER_SHELL` to its executable. The harnesses use disposable synthetic data and fake or mocked model boundaries; passing them does not establish live-provider or legal accuracy. See [UI validation](ui-harness/README.md) for gallery checks, ports, screenshots, and coverage.

## Project map

- [`agent/`](agent/README.md): agent loop, document tools, verification, saved records, and offline tests.
- `ui/`: browser workspace and local HTTP server.
- `scripts/`: local launcher.
- `examples/cases/`: fictional case fixtures for optional command-line use.
- `ui-harness/`: synthetic HTTP, browser, and workspace behavior checks.
- `docs/`: product design, infrastructure assessments, and evaluation notes.

See the [infrastructure selection guide](docs/product/agentic-infrastructure-landscape.md) for current capabilities and proposed additions. Proposed integrations are not connected services. See [PROVENANCE.md](PROVENANCE.md) for upstream attribution and licensing status; third-party UI notices remain under `ui/vendor/`.
