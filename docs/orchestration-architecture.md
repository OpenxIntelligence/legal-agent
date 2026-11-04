# Hierarchical Iterative Reasoning Architecture

Status: design + implementation (this doc is normative for the
 contracts; behavior lives in `agent/agent_kit/orchestration/`).

## Why this layer exists

The agent loop runs turns; verifiers gate filing; the defect
ledger tracks audit findings. None of them plans the work:
there is no task classification, no question dependency graph,
no premise-targeted invalidation, no diminishing-value stop,
and no approval rule beyond "checks passed". This layer adds
that control plane. It does not replace tools, verifiers, or
the loop — it drives them through explicit workflow states.

Non-goals: model routing, live drivers beyond thin tool
wrappers, loop.py integration (follow-up), UI (untouched).

## Components

```
Orchestrator (control plane, deterministic)
  │  classifies task · seeds question DAG · orders engines
  │  budgets · invalidates premises · approves or discloses
  ├── ResearchEngine      evidence gathering + coverage
  ├── AnalysisEngine      element/counterevidence assessment (peer-seamed)
  ├── VerificationEngine  memo rules + claims + QC + defect synthesis
  ├── RepairEngine        defect-driven revise/recheck
  └── ReviewEngine        contradiction integration + disclosure + approval check
```

Each engine runs a bounded internal loop and returns
structured findings. Drivers perform tool work; offline
tests and evals inject scripted drivers, so all control
flow is deterministic and every model-dependent claim
stays labeled.

## Shared contracts

Question: `{id, text, class, task_id, deps[], premises[],
status, support[], answer_version, engine, history[]}`.
Status: proposed → ready → assigned → in_progress →
answered | blocked | stale → (re-queued); terminal:
answered, disclosed, escalated. Questions map 1:1 onto
case tasks (states, dependencies, leases, attempts are
reused, not rebuilt); orchestration-only state lives in
`data["orchestration"]`.

Premise: `{id, statement, version, source}`. Support
links a question to premise versions + evidence refs
(doc/page/quote/fact). A premise bump stales exactly
its transitive dependents; nothing else recomputes.

Finding: `{question_id, answer_version, claims[],
support[], limitations[], unresolved[], engine,
verified_by[], at}`. Versions are append-only; invali-
dation marks stale, never deletes.

Engine result: `{findings[], gaps[], contradictions[],
uncertainty[], unresolved[], tool_calls, iterations,
verified: bool, justification}`.

## Orchestrator contract

Responsibility: plan, order, bound, invalidate, approve.
Inputs: matter context, checklist, budget caps, driver map.
Outputs: run record (classification history, questions,
findings, contradictions, invalidations, spend,
approval-or-disclosure).
Verification: completion approval requires the task
quality bar (below) or explicit disclosure; every stop
names its reason.
Dependencies: engine registry, task ledger, budget state.
Stopping: quality bar met (approve) · budget exhausted
(disclose + escalate unfinished) · no progress in N
passes (disclose) · repeated failure (escalate).

Task classification is rule-based over matter context
(purpose, realms, parties, forum): single-issue research,
multi-regime analysis, transaction review, dispute
investigation, intake triage, unknown (escalate rather
than guess). Classification premises are tracked; when
answers contradict them the orchestrator reclassifies
and adjusts the engine set mid-run.

Per-task quality bars (approval requires all, else each
gap is disclosed): required question classes answered
or disclosed; every answer has support or a stated
limitation; contradictions integrated (not dropped);
defects none-open-or-accepted; adversarial challenge
present or its absence disclosed; assumptions versioned.

## Engine contracts

ResearchEngine — resolve evidence questions.
Inputs: open evidence questions, coverage map. Outputs:
recorded facts with provenance, coverage report, gaps.
Verification: quote/record rules at write time; coverage
recomputed per pass. Deps: record/discovery drivers.
Stopping: coverage complete · cap reached · two passes
with no new support (diminishing value).

AnalysisEngine — assess elements and counterevidence.
Inputs: analysis questions + recorded evidence. Outputs:
per-element findings with both-sides arguments,
missing proof, provisional result. Verification:
peer `validate_element` when importable, else schema
self-check (clearly labeled which ran). Deps:
evidence. Stopping: elements assessed · cap · no
change between passes. Peer seam: the peer's `validate_element`
runs opportunistically when importable;
built-in otherwise is schema +
counterevidence, and reports state which
ran. Peer *tools* are model-invoked, so
production drivers call them; the control
plane never pretends to run them.

VerificationEngine — run the gates. Inputs: memo +
evidence snapshot. Outputs: rule/claims/QC/defect
results per question. Verification: the gates
themselves (memo rules, claims, QC, defect
synthesis — all reused). Deps: research output.
Stopping: single pass per snapshot; re-runs only
on new snapshots.

RepairEngine — close revision-fixable defects.
Inputs: open defects. Outputs: revisions, recheck
results, repair states. Verification: positive
current verdicts (pins/versions/evidence); stale
passes never repair (reuses the ledger rules).
Deps: verification output. Stopping: fixable set
repaired · cap · a defect regresses twice
(escalate with evidence).

ReviewEngine — integrate and challenge. Inputs:
all findings, contradictions, defects, premises.
Outputs: integrated position, contradiction
resolutions-or-disclosures, disclosure list,
approval recommendation. Verification: every
contradiction and open defect is either
resolved or disclosed; nothing silent. Deps:
all engines. Stopping: integrated · cap.
Peer seam: the peer's
`validate_adversarial` runs
opportunistically when importable, and
is labeled when it does.

Engines are independent only where it pays:
verification and repair reuse distinct
machinery with distinct failure modes;
research and review need distinct tool
access. No engine exists for parallelism
alone — ordering is by dependency, and the
eval measures cost against value.

## Invalidation and selective recompute

Premise bump sources: engine finding that
contradicts a premise, defect repair that
changes a relied-upon input, reclassification,
explicit assumption change. The orchestrator
marks transitive dependents stale (task
reopen with reason), re-queues only the stale
subgraph, and preserves unaffected findings
bit-for-bit (provenance intact). Full restart
is refused by construction: there is no API
for it, only targeted reopen.

## Budgets and anti-loop rules

Caps: global iterations, global tool calls,
per-engine passes, per-question attempts,
judge calls. Diminishing value: K passes
with no closures and no new support stops
the question (disclose). Repeated failure:
same question failing M times escalates to
a person with the evidence trail. Stale
re-runs are skipped (snapshot unchanged).
Every stop records its trigger.

## Reuse map (nothing rebuilt)

Task states/deps/leases/attempts/reopen,
scheduler scoring + budget state, memo
rules, claims checks, QC modules,
counterevidence, defect ledger + repair
rules, evidence fingerprinting, artifact
revisions. Peer validators
(`validate_element`,
`validate_adversarial`) run
opportunistically when importable;
built-ins cover their absence and every
report states which ran. Peer *tools*
are invoked by production drivers, not
by this control plane.

## FQ seeding seam (follow-up)

`Orchestrator.seed_from_foundation`
seeds the DAG from the peer's
foundational-question records via
its `orchestration_seeds`
converter, optionally running its
`validate_decomposition` gate
first in the gate's native FQ
schema (never by shoehorning
control-plane seeds into FQ
classes). Gate problems raise
`PlanError` before any question
is proposed; advisories are
recorded in
`state["decomposition_advisory"]`;
provenance lands in
`state["seed_source"]`. The peer
module is imported
opportunistically: absent means
this call fails closed with a
clear message, and all seam
tests use injected fakes so the
suite passes with or without
the peer's modules. Mirrored
locally (no peer dependency):
`add_question` refuses
text-redundant questions, the
same normalization the gate
uses. Proven this session
against the real peer code with
synthetic FQ records (gated
seed, refusal, full run); the
probe is throwaway
(`/tmp/fq_seam_probe.py`), not
committed.

Bar completion (follow-up):
FQ records map to
evidence/analysis/review, so
`seed_from_foundation` also
proposes the bar-required
classes the seeds lack (verify
over all seeds, then repair,
then review), or every
FQ-seeded run would disclose.
A completion question that
collides with a seed text is
disambiguated once rather
than refused.

## Evaluation

`eval/orchestration_cases.py` (frozen
fixtures: assumption change, conflicting
authorities, missing evidence, initial
misclassification) × `eval/orchestration_
run.py` (orchestrated iterative arm vs
single-pass baseline arm, same scripted
drivers). Final-state grades + cost
diagnostics. Scripted drivers prove loop
mechanics only — never judgment quality.

## ADR: control-plane shape

Status: accepted for this increment.

### Alternatives considered

#### Option 1: model-driven subagents per engine
Each engine a recursive agent with its own
prompt and tool scope; orchestrator routes
messages. Pros: flexible, natural language
decomposition. Cons: unbounded cost and
latency, untestable offline, repeated model
calls mistaken for reasoning depth, no
deterministic invalidation. Rejected: it
buys flexibility we cannot measure or bound.

#### Option 2: extend the turn loop in place
Add classification/invalidation branches
to `legal_agent/loop.py`. Pros: no new
package, immediate integration. Cons: the
loop is already turn/budget/lease logic;
bolting a question DAG and engine registry
onto it couples planning to execution and
makes offline testing of the control plane
impossible without the loop. Rejected:
wrong layer, untestable in isolation.

#### Option 3 (chosen): deterministic control plane + drivers
Planning, ordering, budgets, invalidation,
and approval are pure deterministic code
over case records; engines declare
contracts and act through injected
drivers (scripted offline, tool-backed
live). Pros: fully testable offline,
bounded by construction, reuses every
existing record/verifier/ledger, peer
modules plug in as drivers. Cons: live
drivers beyond thin wrappers are future
work; loop.py integration is a follow-up.
Rationale: the request demands measured
value and bounded cost — only a
deterministic plane delivers both.

## Error handling

Driver failures are engine findings
(gaps/uncertainty), never exceptions past
the engine boundary: the engine reports,
the orchestrator re-budgets or escalates.
Malformed engine output fails the engine's
own verification and counts as a failed
attempt (repeated-failure rule applies).
Unknown task types escalate, never guess.
Corrupt orchestration state (missing
question/task links) fails closed with an
explicit diagnostic, refusing to plan on
it.

## Rollback and migration

Additive only: new package, new record
keys, new eval/tests/docs. Nothing
existing is modified, so rollback is
deleting the new files. No data migration:
legacy runs simply have no orchestration
record. Loop integration, when proposed,
must keep legacy turn behavior identical
with the orchestrator unconfigured.

## Consequences

Positive: plannable, bounded, measurable
multi-engine work; targeted recompute
instead of restarts; explicit disclosure
instead of silent gaps.
Negative: two ways to run work until loop
integration lands (direct loop vs
orchestrated); engine value must be
re-proven per task type by the eval.
Neutral: question state duplicates some
task state by reference (single source
stays the task ledger).

## Benchmark result (2026-10-29)

`cd agent && .venv/bin/python -m eval.orchestration_run`:
all three fixtures green on both arms. Orchestrated arm
reaches `approved` with contradictions integrated,
selective invalidation, and mid-run reclassification;
the single-pass baseline stays `disclosed: quality bar
unmet` with its planted contradiction open — the
feedback loops, not the drivers, close the gap (same
scripted drivers both arms). Cost: harborlight 15 vs
7 engine passes, lighthouse 7 vs 5, westhaven 6 vs 4
— the improvement costs a bounded constant, not a
loop. The third fixture (westhaven-amendment) covers
the engine-proposed new premise branch and the
disclosure-resolved contradiction status. It caught
two more real bugs: identical re-run findings
counted as progress (infinite loop instead of
stagnation), and contradictions resolving on
unrelated later invalidations without a re-answer
after them (now `answered_at_inv` must postdate the
raising). Full suites on the peer tree: 1065 tests,
0 failures, both modes (off 29 skipped, on 13
skipped). Unit tests: `tests/test_orchestration.py`,
37/37.

Bugs the benchmark caught and fixed (all in new
files; frozen truth untouched):
- `ready_questions` ignored `ready` status while
  invalidation re-queued through it: stranded
  questions. Ready is now runnable.
- Engines dropped driver `flags`/`realms`, so
  reclassification signals never reached the
  orchestrator. Verified findings now carry them;
  unverified quotes still carry nothing.
- Repeated identical premise updates re-invalidated
  forever (14 records, budget death on lighthouse).
  Re-stating the current premise is now a no-op.
- Unit-test `ScriptedDriver` returned vacuous-clean
  passes after script exhaustion, clearing gaps
  without work. It now repeats its last response
  (matching the eval fake); only never-scripted
  questions get clean defaults.
- Fixture plant correction (documented in the cases
  file): lighthouse's archive lookup needed 3
  scripted failures, not 2, to reach the frozen
  `escalated` truth under max_failures=3; clean-pass
  elements now satisfy the peer element schema.

Canonical matrix/plan/progress integration is staged
here, not merged into the tracked docs, because the
peer is concurrently editing this branch: merging
tracked-doc edits now would risk clobbering that
work. Suggested requirement IDs when merging: ORCH-1
(classify + order), ORCH-2 (bounded engine loops),
ORCH-3 (dependency tracking + selective
invalidation), ORCH-4 (contradiction integration),
ORCH-5 (reclassification), ORCH-6 (budgets +
anti-loop stops), ORCH-7 (quality-bar approval or
disclosure), ORCH-8 (orchestrated-vs-baseline
benchmark). All implemented; evidence is the eval
report plus the 30 unit tests. Live-driver quality
remains unproven by design (scripted drivers prove
mechanics only).
