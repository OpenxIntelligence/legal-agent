# Foundational architecture directive

Owner direction adopted 2026-11-05. Applies across projects as a shared design
standard. Each repository owns its product purpose, domain vocabulary,
architecture decisions, implementation, and acceptance evidence.

## One platform, independently evolving capabilities

Design a coherent ecosystem with centralized governance and decentralized
execution. Define the ecosystem and its boundaries first, then design the
engines within it. A new project inherits this philosophy without inheriting
another project's domain assumptions, data, credentials, or infrastructure.

The platform provides consistent identity, authorization, navigation,
coordination, configuration, provenance, audit, and observability. Specialized
engines own their domain behavior and collaborate through governed contracts.
Shared intelligence means authorized contextual collaboration with traceable
sources, not unrestricted access to every domain's information.

## Design rules

1. **Modular responsibilities.** Give each major capability a clear purpose,
   interface, dependency boundary, and owner. Keep domain logic separate from
   UI, provider adapters, infrastructure, and orchestration where practical.
2. **Independent evolution.** Make engines testable and replaceable through
   stable interfaces. Version contracts and provide compatibility and migration
   paths when changes affect consumers.
3. **Appropriate autonomy.** Let engines execute their own work without routing
   every operation through the core application. Support independent deployment,
   scaling, storage, and processing environments when workload and ownership
   justify them.
4. **Central governance.** Apply consistent identity, permissions, privacy,
   credential custody, audit, and model governance across execution boundaries.
   Enforcement belongs at the resource and execution boundary, not only in UI
   controls, agent prompts, or a gateway.
5. **Governed interoperability.** Use explicit APIs, typed in-process interfaces,
   events, and adapters as appropriate. REST, GraphQL, MCP, A2A, and brokers are
   choices for a demonstrated boundary, not mandatory infrastructure.
6. **Domain data ownership.** Identify the owner and authorized consumers of
   each data model. Exchange information through governed access contracts;
   avoid hidden cross-domain writes. Preserve provenance, lineage, integrity,
   retention requirements, and access restrictions through transformations.
7. **Dynamic composition.** Support configurable workflows, registered
   capabilities, replaceable models, tools, and processing engines where useful.
   Capability registration defines schemas, permissions, compatibility, and
   lifecycle; it does not grant arbitrary code execution or data access.
8. **Reusable infrastructure.** Share identity, policy, configuration, model
   access, events, storage primitives, and observability when reuse improves
   consistency. Keep shared services focused so they do not become a universal
   domain model or a bottleneck for unrelated engines.
9. **Fault isolation.** Bound failures with resource limits, cancellation,
   timeouts, retry policy, and recovery appropriate to the workload. Asynchronous
   contracts specify delivery, deduplication, ordering, and replay behavior when
   those properties matter. Report partial failure rather than silent success.
10. **Unified experience.** Preserve coherent navigation, workspace context,
    run status, permissions, and artifact inspection across engines and
    verticals. Allow specialized interfaces and workflows within that coherence.
11. **Technology flexibility.** Put vendor and framework integrations behind
    purposeful adapters. Select dependencies for actual requirements and verify
    their capabilities and licensing before adoption.
12. **Continuous evolution.** Support additional engines, product verticals,
    datasets, technologies, workloads, and teams through explicit extension
    points. Record tradeoffs and revise boundaries as evidence changes.

## Apply at the scale of the project

Logical modularity and separate deployment are different decisions. A modular
application in one process can satisfy this directive. Extract a service when
isolation, scaling, deployment cadence, technology, security, or team ownership
provides a concrete benefit. Record the operating cost and new failure modes.

Do not create every illustrative engine, introduce Kubernetes or messaging by
default, or promise unlimited extensibility. Build the capabilities the product
needs and preserve credible paths to extend them. Existing systems should adopt
the standard incrementally at affected boundaries; this directive does not
authorize unrelated rewrites, migrations, deployments, or repository-wide churn.

Centralized governance describes consistent authority and policy, not a
requirement for one synchronous coordinator or one physical server. Enforcement
and execution can be distributed while retaining a shared policy and audit
model. Central services need availability and recovery appropriate to their
role.

## Seven design layers

For substantial architecture work, cover the relevant layers in repository
architecture documentation or an ADR. A short decision can cover several layers;
seven separate documents are not required.

| Layer | Decisions to make |
| --- | --- |
| Platform | Ecosystem purpose, boundaries, shared services, coordination, extension points |
| Engine | Responsibilities, interfaces, dependencies, ownership, internal structure, replacement |
| Infrastructure | Execution, deployment, storage, messaging, capacity, recovery, operational cost |
| Intelligence | Models, agent harnesses, tools, orchestration, evaluation, permissions, model governance |
| Data | Ownership, schemas, pipelines, interoperability, provenance, access, retention |
| Security | Identity, authorization, isolation, credential custody, privacy, audit, compliance needs |
| Product | Verticals, specialized workflows and interfaces, shared navigation, expansion strategy |

An AI engine's analysis and interpretation remain assessments. Preserve the
user's intent, distinguish assumptions from evidence, retain source lineage,
and verify consequential outputs. Autonomous improvement uses versioned,
evaluated changes within authorized scope; a generated proposal is not a
validated capability or permission to deploy it.

## Minimum useful component contract

Define what is relevant for each actual engine or shared service:

- Purpose, domain owner, inputs, outputs, and consumer expectations.
- Public API or event schemas, supported versions, and compatibility policy.
- Allowed dependencies, owned state, data access, and permission enforcement.
- Execution lifecycle, configuration, resource limits, cancellation, and recovery.
- Observable failures, correlation identifiers, audit events, and provenance.
- Contract checks, behavior checks, rollout, migration, and rollback strategy.

An in-process module may express these through interfaces, tests, and a short
README; a deployed service needs operating contracts as well. Independently
owned storage is an option, not a requirement for a database per module.

## Architecture review and evidence

For a consequential decision, explain the intended user outcome, affected
boundaries, alternatives, dependency direction, ownership, and why the chosen
level of distribution is justified. Verify the behaviors claimed: for example,
replace an adapter without changing domain logic, reject unauthorized access,
preserve provenance through a handoff, or recover an interrupted workflow.

Document current implementation separately from target architecture. A module
name, diagram, registry entry, or passing unit test does not prove independent
deployment, fault isolation, multi-tenancy, or production readiness. Scale
verification to the change and retain unresolved limitations explicitly.
