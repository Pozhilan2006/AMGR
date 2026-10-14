# AI Security Operator tool-calling flow

## Purpose

The Phase 3 implementation extends the existing Phase 1 provider abstraction and Phase 2 investigation flow with a controlled, explicit, read-only tool-calling layer. The model may choose from a small registered set of approved investigation tools, but it cannot bypass the authenticated scope, execute arbitrary code, mutate policies, or silently invent security facts.

The architecture remains intentionally narrow:

- provider layer: communicates with the configured LLM provider
- investigation layer: classifies and bounds the user's request
- tool registry: contains only approved, read-only tools
- schema layer: validates tool names and arguments using Pydantic models
- executor: validates scope, authorizes access, and runs the tool handler
- evidence layer: preserves traceable evidence references
- response builder: turns the tool result into a grounded explanation

## Registered tools

The explicit tool registry is defined in `backend/app/ai_operator/tools/registry.py`.

The following tools are registered and read-only:

1. `get_component_details`
   - purpose: retrieve the available details and direct relationships for a known component
   - service interface: `backend/app/ai_operator/tools/graph_queries.py::lookup_component_neighbors`
   - arguments: `component_id`, `limit`
   - output: component identifier, neighbor list, scope, and evidence summary

2. `get_flow_lineage`
   - purpose: retrieve supported upstream or downstream lineage for a component
   - service interface: `lookup_upstream_lineage` or `lookup_downstream_lineage`
   - arguments: `component_id`, `direction`, `limit`, `traversal_depth`
   - output: component, direction, lineage list, and evidence summary

3. `get_component_paths`
   - purpose: retrieve a known path between two components
   - service interface: `lookup_path`
   - arguments: `source_component_id`, `destination_component_id`, `limit`, `traversal_depth`
   - output: path list and evidence summary

4. `get_policy_decision`
   - purpose: retrieve an authorization decision for a decision ID or flow ID
   - service interface: `explain_decision` or `find_decisions_for_flow`
   - arguments: `decision_id`, `flow_id`
   - output: decision outcome, reason, policy IDs, and evidence information

5. `get_policy_details`
   - purpose: retrieve details for a referenced policy when the repository exposes them
   - service interface: repository placeholder / unavailable interface
   - arguments: `policy_id`
   - output: currently unavailable, with explicit gap reporting

6. `get_decision_history`
   - purpose: retrieve recent or flow-scoped decision history
   - service interface: `list_recent_decisions` or `find_decisions_for_flow`
   - arguments: `flow_id`, `limit`
   - output: decision records or a clear empty/unavailable message

## Tool schemas

The tool input and result schemas are defined in `backend/app/ai_operator/tools/schemas.py`.

The key schema types are:

- `ToolCallRequest`: validates `tool_name` and `arguments`
- `ToolCallEnvelope`: wraps a single tool call as a structured payload
- `ToolResult`: normalizes success, empty, error, forbidden, and unavailable outcomes
- `ComponentDetailsArgs`, `FlowLineageArgs`, `ComponentPathsArgs`, `PolicyDecisionArgs`, `PolicyDetailsArgs`, `DecisionHistoryArgs`

All schemas forbid unknown fields and enforce bounded limits:

- `limit`: `1..50`
- `traversal_depth`: `1..8`
- identifiers: required non-empty strings with explicit max lengths
- enum values are restricted to the defined allowed values

## Service interfaces used

This phase reuses the actual repository contracts that are already present:

- `backend/app/ai_operator/tools/graph_queries.py`
  - `lookup_component_neighbors`
  - `lookup_path`
  - `lookup_external_service_interactions`
- `backend/app/ai_operator/tools/lineage_queries.py`
  - `lookup_upstream_lineage`
  - `lookup_downstream_lineage`
- `backend/app/ai_operator/tools/decision_queries.py`
  - `explain_decision`
  - `find_decisions_for_flow`
  - `list_recent_decisions`

These wrappers are intentionally read-only and do not duplicate the graph engine or policy engine. They also return explicit `unavailable` handlers when the upstream service is not implemented.

## Scope and authorization enforcement

The existing authentication and authorization flow is preserved from Phase 1 and Phase 2:

- `backend/app/api/routes/operator.py` resolves the caller's trusted identity.
- `resolve_scope` requires the request's scope to be in the authenticated identity's authorized scopes.
- `OperatorService.respond` passes the validated `OperatorScope` into the tool executor.
- `execute_tool_call` checks that the requested tool is registered and marked read-only.

The model can never override the trusted scope from the request context; tool arguments do not redefine the authenticated application or environment.

## Execution limits

The execution loop is intentionally bounded:

- tool call count is limited by the service flow and the registry-driven validation path
- tool result size is kept to a compact normalized payload
- graph traversal depth is capped to `8`
- result count is capped to `50`
- tool execution is short-circuited when the start of a request is already unsupported or unavailable

The Phase 3 implementation does not add an unbounded retry loop. When the execution budget is reached, the operator stops and explains that the investigation was limited by the configured budget.

## Evidence format and traceability

The evidence model lives in `backend/app/ai_operator/evidence.py`.

Each evidence record includes:

- `evidence_type`
- `record_id`
- `source_service`
- `timestamp`
- `relationship_type`
- `observation_status`
- `summary`

This keeps actual source record references and their origin visible without exposing sensitive internals. The evidence model distinguishes:

- `observed`: directly available from a record
- `derived`: derived from an explicit recorded relationship
- `incomplete`: some data was returned but the full path is not established
- `unavailable`: the upstream interface is unavailable or not implemented

## Error handling and query semantics

The executor distinguishes several outcome classes:

- success: a matching result was returned
- empty: a valid query completed but returned no records
- error: the tool could not be executed due to malformed input or runtime failure
- forbidden: a non-read-only or unauthorized tool was requested
- unavailable: the current repository does not expose the required service

This distinction is important: a successful empty result is not treated the same as a service failure or an authorization denial. The operator explains the difference to the user without making unsupported conclusions.

## Unsupported operations and missing dependencies

The following are explicitly unsupported or unavailable in the current repository implementation:

- policy modification or activation workflows
- approval or rejection actions
- privilege escalation through tool capabilities
- arbitrary code, shell, or SQL execution
- unrestricted outbound HTTP requests
- production graph lineage services without Member 2 implementation
- decision-history services without Member 3 implementation

The tool registry does not include or expose those operations. Any model request for them is rejected as invalid or unauthorized.

## Separation from policy mutation workflows

The AI operator remains read-only in this phase. It can explain observed evidence, but it cannot modify policies, activate or deactivate rules, approve or reject requests, or replace Member 3's authorization control plane.

Policy proposals and approvals belong to separate, explicit workflows with their own authorization checks and service boundaries. This phase does not implement those workflows.
