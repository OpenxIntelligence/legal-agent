# Legal Agent runtime

The runtime investigates a saved question against supplied records, records evidence and counterevidence, requests missing information, and produces a memo for human review. The browser workspace creates a separate data root for each matter; executable code remains in this directory.

Start with the [product setup and workspace guide](../README.md). The default provider configuration uses the Meta Model API through the OpenAI Python SDK. Live investigations and model-based verification send content to the configured provider and may incur charges.

## Install and configure

From this directory:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
test -e .env || cp .env.example .env
```

Use the browser **Settings** page to save, replace, or remove the model API key. Configure provider URL, model, and optional CourtListener credentials in local `.env`. Keep the populated file private and untracked. It is plaintext, not encrypted. Settings reports key presence without testing provider access.

`VERIFICATION=on` is the default. An existing `.env` containing `VERIFICATION=off` overrides it. Optional `RUN_TOKEN_BUDGET` and `MAX_CONTEXT_BYTES` settings pause work at runtime limits; they are not provider billing controls.

## Optional command-line case

A command-line case contains `objective.md` and a `documents/` folder. Set `CASE_DIR` in `.env`, relative to this directory, or pass it in the environment:

```sh
CASE_DIR=../examples/cases/northbridge-v-caldera ./helper setup
```

The example is fictional. **Setup replaces the current command-line inbox with the selected case documents, installs dependencies, and checks the provider connection.** It is not required to start the browser workspace. Use it deliberately; it is not an offline readiness check.

Run commands from this directory:

```sh
./helper describe
./helper run --turns 10
./helper run --resume RUN_ID --turns 10
./helper review RUN_ID --answer R1 --notes "Response to the evidence request"
./helper review RUN_ID --reject --notes "Issues requiring revision"
./helper review RUN_ID --approve
./helper verify RUN_ID --memo
./helper branch RUN_ID
```

Run and verification commands may call the provider. Review decisions are explicit saved human actions; approval is not an automatic determination of legal correctness. Resume continues only a supported stopped state. Use the browser’s recovery action for interrupted execution, inspect the saved records, and resume only after checking possible effects beyond the last checkpoint. Recovery does not roll back tool effects or guarantee exactly-once execution.

`./helper reset` replaces the command-line inbox; `--clear-runs` also removes its saved runs. These maintenance commands are not part of normal matter creation.

## Offline checks

```sh
LEGAL_AGENT_FAKE_MODEL=1 VERIFICATION=on .venv/bin/python -m unittest discover -s tests
```

This runs the isolated test suite directly without `./helper setup` or its provider check. Fake-model results establish runtime behavior only. The [UI harness](../ui-harness/README.md) covers synthetic persistence, evidence review, Settings, and browser workflows.

## Runtime map

- `legal_agent/loop.py`: model/tool loop, slices, continuation, and steering.
- `legal_agent/system_instructions.md`: instructions snapshotted into each new run.
- `verification/`: evidence, claims, checklist, and memo checks. See [VERIFICATION.md](VERIFICATION.md) and [ESCALATION_RULES.md](ESCALATION_RULES.md).
- `agent_kit/tools/`: tool schemas and execution.
- `agent_kit/case_record.py`: persisted evidence, requests, review, and audit records.
- `agent_kit/document_text.py`: extracted page text, source matching, and local corpus search.
- `agent_kit/progress.py`: conversation checkpoints and run state.
- `agent_kit/runtime_ownership.py`: local runner ownership and interruption recovery.
- `agent_kit/client.py` and `settings.py`: provider client and runtime configuration.
- `tests/`: isolated runtime and API regressions.

Within each data root, `workspace/inbox/` holds uploaded records, `workspace/cache/` extracted text, `workspace/runs/` saved conversations, and `workspace/case_records/` structured records. Workspace-created matters receive their own roots. `LEGAL_AGENT_ROOT` selects a separate root for tooling; it does not create an OS sandbox. Agent tools retain the local account’s permissions.

Documents awaiting extraction are not yet searchable. Restricted, unavailable, or quarantined sources must remain excluded from evidence access. Local record queries and keyword/BM25 retrieval do not provide an external legal research database or establish that an authority is current.
