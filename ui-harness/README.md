# UI journey harness (`ui-harness/`)

Repo-owned synthetic acceptance for the gallery + audit workspace.
Drives the REAL `ui.py` request handler over in-memory socket pairs
(no ports bound — runs even where listeners are forbidden), with a
fictional demo matter in a temp root that is deleted afterwards.
Mutations travel the same `./helper artifact` path the server uses.

## Run

From the repo root:

```bash
agent/.venv/bin/python ui-harness/journey.py
```

Exit 0 + `journey green` on success; first failure aborts non-zero
with the step named. Nothing outside the temp root is touched; no
keys, accounts, network, or browser needed. The harness exports
`LEGAL_AGENT_FAKE_MODEL=1`, so every `./helper artifact check`
subprocess records semantic support `unavailable` instead of
constructing a live model client (even when `agent/.env` holds a
key); the journey asserts that honest outcome.

## What it proves

Gallery -> audit open -> real source inspection -> revise (new
version stored) -> recheck -> explicit-clear stores `""` ->
review of the exact revision -> stale approval rejected ->
unknown row refused -> revoked source blocked -> traversal
blocked -> JSON + Markdown export (ids, revisions,
qualifications, synthetic marking) -> fresh-subprocess reopen
of the same state, plus a light board-open regression.

## What it does not prove

Screen-reader behavior and visual polish beyond the captured
screenshots (see below). DOM rendering, keyboard operation,
and focus management are covered by the real-browser runner.

## Real-browser runner (`browser_run.sh` + `browser_validate.mjs`)

Drives chromium headless-shell over CDP with zero npm
dependencies (Node builtins only) against a real loopback
`ui.py` server on an isolated synthetic root. Covers gallery
render, open-demo, assertions table, row select, revise (v1
-> v2), checker, first-objection raise + disprove, two
disagreeing blind labels + adjudication, hand note, needs
freeze presence, document upload, export, reload persistence,
Tab keyboard traversal, board + matrix render over the
same matter, a full 16-demo gallery sweep, and the
regression-examples panel (empty state + honest refusal
on the adversarial demo), with PNG screenshots.

```bash
sh ui-harness/browser_run.sh   # needs loopback + unsandboxed launch; see below
```

Observed 2026-11-01: system Chrome aborts under the agent
shell sandbox (`_RegisterApplication` SIGABRT, exit 134) and
the cached headless-shell segfaults there too (sandbox-blocked
IOKit); both run fine unsandboxed. The runner therefore needs
an environment that permits loopback listeners and an
unsandboxed browser launch. It fails loudly (no silent
substitute) when either is unavailable. Last green run:
41/41 checks (including the 16-demo sweep),
screenshots under `/tmp/browser-shots/`.

## Question-led matter workflow

Run `sh ui-harness/matter_run.sh` for isolated Chromium acceptance of the actual
question composer, selected text attachment, saved matter/reload/follow-up,
inline extraction, cross-matter refusal, honest demo behavior, and mobile layouts.
It also checks task starters, draft preservation, drag/drop and file validation, saved
investigation search, selected-run history, reload, and scoped review return. Run
history fixtures are written only inside the runner-owned ephemeral synthetic root.
The suite also holds a real message response across polling to check duplicate
submission, busy controls, and draft preservation. Native Enter/Shift+Enter checks
exercise submission and newlines at a short laptop viewport. Post-intake uploads
persist through reload and open extracted sources. Live Send checks verify save
before dispatch and visible provider errors. Resume, steering delivery, and model
dispatch are mocked at the isolated boundary; seeded pending-answer persistence
is read through the API. No model is invoked.
It also checks simultaneous conversation/findings, keyboard navigation, independent
per-investigation drafts, request replacement provenance, and source return focus.
Inline editing saves a real work-product version, survives polling, and preserves
the original model memo; provider dispatch remains mocked.
The workspace suite also checks ordinary filename normalization/collisions,
mobile conversation order and citation cards, missing-key save-only behavior,
failed-process recovery, new-document dispatch to a new investigation,
stale editor conflicts/reconciliation, saved-review exports excluding unsent text,
and invalidation of displayed/in-flight evidence after access changes.
Run `node ui-harness/workspace-state.test.mjs` for browser-storage fallback and
exact submitted-draft clearing checks. It never starts a provider request. `BROWSER_SHOTS` changes the screenshot directory.
Use `PYTHON=python3` with the existing gallery browser runner when no local venv is
present. Matter run/resume roots and steering delivery also have offline regressions
in `agent/tests/test_matter_*.py`.

## Local credential Settings

Run `sh ui-harness/settings_run.sh` for Chromium acceptance against two disposable
servers: a fake live workspace and an isolated demo. The runner creates its own
fake `agent/.env` inside a temporary directory and never opens the project’s key
file or dispatches a model job. It exercises actual save, replacement, removal,
owner-only permissions, status readback, reload, blank/invalid input, confirmation
cancel, navigation cleanup, and write-only status/browser-storage behavior.
403/409 UI error responses are mocked; the invalid-token400 and demo403 are real.
Desktop1280 and mobile390 screenshots and `settings-results.json` are written to
`/tmp/legal-agent-settings-shots` (override with `BROWSER_SHOTS`). `PYTHON`,
`BROWSER_PORT`, `BROWSER_DEMO_PORT`, and `BROWSER_DEBUG_PORT` can override defaults.
The fake helper refuses execution; no provider request is part of these checks.
