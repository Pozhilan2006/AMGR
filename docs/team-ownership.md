# Team ownership boundaries

## Member 1 — Data-flow capture and runtime instrumentation

Owns:

- Python SDK design and packaging
- runtime event capture and serialization
- component metadata and identity references
- correlation context and flow propagation
- transport interfaces and pre-operation enforcement hooks

Shared coordination required for:

- event schema changes
- metadata conventions
- API contract updates
- any runtime guard that affects execution paths

## Member 2 — Flow graph, lineage, and provenance

Owns:

- graph persistence and modeling
- event ingestion-to-graph mapping
- lineage and provenance derivation
- graph queries for explanation and investigation
- graph-context retrieval for downstream consumers

Shared coordination required for:

- graph schema or node/edge contracts
- lineage data model changes
- new event metadata fields used in traversal or provenance

## Member 3 — Policy engine and runtime enforcement

Owns:

- policy schemas and versioning
- deterministic rule evaluation
- precedence and conflict resolution
- authorization API and decision history
- approval state, lifecycle, and enforcement checks

Shared coordination required for:

- policy schema shape changes
- authorization request/response contract changes
- approval workflow state transitions
- decision history integration with UI and operator surfaces

## Member 4 — AI Security Operator

Owns:

- natural-language explanation flow
- structured policy proposal generation
- graph-context querying for reasoning and diagnosis
- AI-provider integration boundaries

Constraints:

- The operator must not bypass Member 3 authorization rules.
- It should present structured recommendations, not direct runtime write access.

Shared coordination required for:

- graph and policy query contract changes
- decision explanation payloads
- approval or override workflows

## Member 5 — Security dashboard and policy management UI

Owns:

- graph visualization and runtime overview screens
- policy and decision views
- approval workflow displays
- violations, audit summaries, and frontend API integration

Shared coordination required for:

- API contract changes
- shared TypeScript models
- graph node/edge schemas
- status semantics for decisions and approvals

## Shared coordination rules

All five members must coordinate before changing any of the following:

- shared schemas or API contracts
- database models or migrations
- route shapes or response payloads
- event metadata semantics
- policy evaluation semantics and precedence rules

The working rule is simple: if the change crosses a module boundary, it requires explicit review and alignment before merge.
