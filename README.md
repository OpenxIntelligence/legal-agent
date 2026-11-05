# Legal Agent

A local, AI-native workspace for researching legal questions and examining documents. Keep the conversation, findings, editable review, and sources together; inspect the evidence before relying on an answer.

## Start locally

Use Python 3.11 or newer. From the repository root:

```sh
python3 -m venv agent/.venv
agent/.venv/bin/python -m pip install -r agent/requirements.txt
# Preserve any existing local configuration.
test -e agent/.env || cp agent/.env.example agent/.env
./legal-agent
```

Open **http://127.0.0.1:8765**. The default is the **live local console**; `--live` selects the same mode explicitly. Opening the console does not call your model. Send or Start initiates a model investigation when credentials and document preparation are ready.

Open **Settings** to save your model API key. The field is write-only: saved keys are never returned to the browser. The server writes `agent/.env` with owner-only permissions; the file is plaintext, not encrypted, and is ignored by Git. A configured key is not proof that the provider accepts it.

Configure `MODEL_API_BASE_URL` and `MODEL_API_MODEL` in `agent/.env` for your provider. The included defaults use the Meta Model API through the OpenAI Python SDK. Other providers must support the API and tool/document behavior this runtime uses; changing the URL alone does not establish compatibility. An optional `COURTLISTENER_TOKEN` enables configured caselaw lookups. Keep `VERIFICATION=on` for verification and review gates. `CASE_DIR` selects the optional command-line case; new workspace matters use their own saved question and records.

Live investigation sends matter content to the configured provider and may incur charges. Workspace actions use slices of up to 30 turns. Set `RUN_TOKEN_BUDGET` in local configuration if you need a per-run token budget; this is not a billing limit.

## Work in a matter

1. Describe a meaningful legal question (at least 10 characters), with relevant jurisdiction, dates, and requested output. Upload supporting records if available; documents are optional.
2. Choose **Send**. The initial question is saved first, then an investigation starts when a key is configured and preparation succeeds. Without a key, the question is saved and you can connect in Settings before starting. Follow the Copilot conversation beside findings and work products. Recorded activity shows attempted work, not verified conclusions.
3. Open source citations and the local **Record explorer** to inspect documents, claims, citations, requests, and review records. These read-only queries make no model call.
4. Answer open requests or send directions. Choose **Queue** to hold an instruction until the next resume, or **Steer now** to deliver it before the next model call after the current tool completes. Neither interrupts a tool already running. Edit or cancel pending messages before delivery; delivered messages require a new direction. Delivery confirms that context reached the model conversation, not that the agent acted on it.
5. Open an evidence review, edit claims, check sources, and save a versioned review. Export the saved review separately from the original agent draft and conversation; unsent edits are excluded.

New documents require a new investigation so the agent uses the updated records. Earlier runs remain available in run history. Draft messages persist per investigation in browser storage when available; saved matter records persist on the local server. PDF upload is not a guarantee of successful extraction or safe contents: the workspace shows extraction and quarantine states explicitly.

Runner ownership survives server restarts. When an interrupted run offers **Recover saved run**, recovery updates saved state without calling the model. Inspect activity and records before choosing Resume: tool effects after the last checkpoint may already exist, and continuation may repeat work. A run without a usable checkpoint requires a new investigation.

## Research and analysis

An investigation can begin without uploads. The agent can plan research, search Federal Register documents, retrieve supported public primary-source pages, and save extracted passages into the same evidence pipeline as uploaded records. Search coverage is limited: Federal Register search is not a general web or complete caselaw search. A failed search never establishes that no relevant law or notice exists.

Retrieved sources retain their original URL, retrieval time and content hash. Findings link to saved passages for inspection. Retrieval does not establish that an authority is current, controlling, or legally applicable; informational Federal Register pages require checking the official publication. The agent should distinguish a supported general rule from facts still needed to apply it to a particular matter.

The workspace shows the run's recorded plan, checklist, requests and source activity. Findings appear in a comparison table beside Copilot, with gaps and next steps below. A turn limit pauses unfinished research for continuation instead of requiring a premature memo. Historical drafts remain unchanged; start a new investigation to use updated research instructions.

## Optional isolated demo

```sh
./legal-agent --demo
```

Demo mode uses the standard library, requires no API key, and never loads `agent/.env`. It saves questions and context but cannot investigate or answer them. Saved demo work persists in `.local/demo/`; fictional examples illustrate review mechanics, not live-model accuracy. Live and demo workspaces are separate.

Use `--port 8877` to change the server port. Use `--demo-root /path/to/empty-folder` for a separate demo workspace; the launcher refuses to adopt a nonempty folder it does not own.

## Troubleshooting Send

- **The page still says Demo:** check the URL and the terminal serving that port. An older demo server or browser tab can remain open after installing newer code. Stop that server in its terminal, run `./legal-agent`, and reopen the printed URL. Alternatively run `./legal-agent --port 8877` and open that port. Refresh the page after restarting.
- **Your question saves without an answer:** check Settings for a configured key and inspect document preparation errors. A missing key makes Send save-only. After connecting, return to the saved matter and start the investigation. Saving is not confirmation of a model response.
- **A message such as “Hi” is rejected:** intake requires a question of at least 10 characters. This is a matter investigation workspace, not general chat. Describe the situation and what you need to determine, such as “Does this contract allow termination without notice?”
- **A configured key still fails:** inspect the visible run error and verify the provider URL, model, and account access. Key presence does not validate authentication, quota, or model compatibility. Resolve the error before retrying; repeated live requests may incur charges.

## Execution and limits

Launch options require an integer turn count from 1 to 200 and boolean step controls. Invalid values are rejected before changing execution state. Saved checkpoints are checked for run identity and execution shape before resume; an invalid checkpoint requires inspection rather than automatic repair.

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
