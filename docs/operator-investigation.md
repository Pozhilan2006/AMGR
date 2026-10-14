# AI Security Operator investigation flow

## Supported intent types

The Phase 2 implementation supports the following bounded investigation intents as typed operations:

- `component_lookup`: identify a component and return the available metadata and direct-neighbor context known to the repository.
- `upstream_lineage`: ask for upstream lineage evidence for a known component.
- `downstream_lineage`: ask for downstream lineage evidence for a known component.
- `path_lookup`: ask for a known path between two identified components.
- `external_service_interaction`: look for recorded interactions involving an external service or API.
- `policy_decision_explanation`: ask for a recorded policy or authorization decision explanation.
- `event_history_lookup`: retrieve relevant recorded events or decision-history context.
- `amgr_capability_explanation`: explain documented AMGR capabilities and repository limitations.

Representative questions include:

- "What metadata do you have for component billing-agent?"
- "What are the upstream components for order-service?"
- "What downstream lineage is associated with model-gpt-4o?"
- "What is the path from api-gateway to database-primary?"
- "Did the external payment API have any recorded interactions?"
- "Why was this decision denied?"
- "Show the event history for flow 123e4567-e89b-12d3-a456-426614174000"
- "What functionality does AMGR provide?"

## Typed schema and validation rules

The intent contract is defined in `backend/app/ai_operator/query_schemas.py`.

The core model is `InvestigationIntent` and captures:

- `intent_type`: a constrained enum of supported operations.
- `component_id`, `source_component_id`, `destination_component_id`: normalized identifiers with a bounded length.
- `flow_id`, `event_id`, `decision_id`: optional identifiers when a question is framed around a specific flow or decision.
- `direction`: optional upstream/downstream direction when relevant.
- `limit`: bounded result count, `1..50`.
- `traversal_depth`: bounded depth, `1..8`.
- `clarification_required` and `clarification_question`: explicit clarification state used when a question is underspecified.

The service validates these fields before executing any query tool. Empty strings are normalized to `None`, and invalid or unsupported requests are rejected before an arbitrary operation is attempted.

## Actual query interfaces used

This phase intentionally uses the actual repository contracts, not hypothetical services.

- `backend/app/ai_operator/intent_router.py` implements the registry and validation logic for supported investigation intents.
- `backend/app/ai_operator/tools/graph_queries.py` contains the read-only graph query tools used by the operator.
- `backend/app/ai_operator/tools/lineage_queries.py` contains the read-only lineage query tools used by the operator.
- `backend/app/ai_operator/tools/decision_queries.py` contains the read-only decision-history query tools.
- `backend/app/ai_operator/service.py` integrates the typed investigation layer into the existing operator response flow.

The repository does not currently implement Member 2 or Member 3 runtime services. The actual available interfaces are the typed Pydantic models and placeholder service classes:

- `backend/app/schemas/flow_event.py` defines `FlowEvent` and `ComponentRef`.
- `backend/app/schemas/graph.py` defines `GraphNode`, `GraphEdge`, and `GraphResponse`.
- `backend/app/schemas/authorization.py` defines `AuthorizationRequest` and `AuthorizationDecision`.
- `backend/app/schemas/policy.py` defines `Policy`.
- `backend/app/api/routes/graph.py` returns `501 Not Implemented` for graph queries.
- `backend/app/api/routes/authorization.py` returns `501 Not Implemented` for authorization and decision handling.
- `backend/app/api/routes/flow_events.py` returns `501 Not Implemented` for event ingestion.
- `backend/app/services/graph_service.py` and `backend/app/services/lineage_service.py` are placeholder services, not real data integrations.

## Scope enforcement

The investigation layer requires the existing authenticated scope from the request context and reuses the same validated `OperatorScope` model and `resolve_scope` logic already enforced by the route layer in `backend/app/api/routes/operator.py`.

The effective scope is not derived from the user message. It is taken from `request.state.operator_identity` and validated against the request's authorized scopes.

The implementation also enforces read-only behavior and bounded retrieval controls:

- `limit` is restricted to `1..50`.
- `traversal_depth` is restricted to `1..8`.
- The service does not create, edit, approve, reject, or activate anything.
- Query tools are restricted to a supported registry; arbitrary functions, SQL, shell commands, or URLs are not executed.
- Only the minimal evidence required for the answer is passed to the model context.

## Evidence and response schema

The investigation response uses the typed `InvestigationResponse` schema in `backend/app/ai_operator/query_schemas.py` with:

- `answer`: concise explanation grounded in available evidence.
- `intent_type`: the chosen supported intent.
- `evidence`: a list of typed evidence records, each containing `reference_type`, `reference_id`, and optional summary/timestamp.
- `clarification_required`: flag for missing or ambiguous information.
- `clarification_question`: a follow-up prompt when required.
- `limitations`: text describing the known gap or missing interface.

The operator's final HTTP response still uses `OperatorChatResponse` and keeps the API contract consistent with Phase 1. References are flattened into `OperatorReference` entries using `graph_event`, `policy`, or `decision` types and preserved from the actual evidence set when available.

## Clarification and error behavior

When the request lacks a required identifier or is ambiguous, the investigation layer intentionally asks for clarification instead of inventing missing identifiers.

Examples:

- missing component ID => request a stable component ID or more specific name.
- missing source/destination => ask which two components should be compared.
- missing decision ID or flow ID => ask for one.
- unsupported question => explain what AMGR cannot currently retrieve.
- empty result => state that no matching records were returned for the authenticated scope.
- upstream service unavailable => explain that the requested information is unavailable, without implying success or a permissive decision.
- invalid model or routing failure => reject the invalid operation and return a controlled clarification or error response.

## Unsupported questions and known limitations

The following are not answerable from the current implementation and are therefore treated as unsupported or limited:

- verified graph topology or adjacency beyond the repository's placeholder contracts
- real lineage paths or provenance chains
- actual decision history or authorization outcomes
- external-service interaction records from persistent data
- policy evaluation details from Member 3 because the policy endpoint is still `501 Not Implemented`
- arbitrary free-form investigation requests outside the supported registry

## Integration gaps requiring coordination

The following areas still require coordination with the Member 2 and Member 3 workstreams before the operator can provide authoritative graph, lineage, and decision answers:

1. Member 2 must implement an actual graph query API and lineage service to populate component adjacency and provenance.
2. Member 2 must expose event ingestion and retrieval interfaces or a query API for flow and event history.
3. Member 3 must implement the authorization and decision-history service and expose a stable response schema.
4. Member 3 must expose policy identifiers, policy versions, decision reasons, and approval state in a consistent API.
5. Overall repository integration must align the route contracts, schema names, and response payloads before the operator can make authoritative claims.

This documentation reflects the actual repository state: the operator can safely route supported questions, validate scope, and explain unsupported or unavailable data without inventing evidence.
