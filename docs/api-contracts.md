# API contracts

All application routes are mounted under `/api/v1`. Health is also exposed at
`/health`. Routes that read or write scoped runtime data require trusted identity
middleware to set `request.state.operator_identity`; the middleware must authorize
the requested `(application_id, environment)` pair.

## Runtime events

### `POST /api/v1/flow-events`

Accepts one `FlowEvent` JSON object as specified in [event-schema.md](event-schema.md).
The event's required application and environment fields are checked against the
authenticated identity. The server persists the event, its graph node/edge links,
and its lineage record in one transaction.

Responses:

- `201 Created`: a new event was recorded (`duplicate: false`, `status: "accepted"`).
- `200 OK`: an identical event ID was already recorded in that scope (`duplicate: true`,
  `status: "already_processed"`).
- `401 Unauthorized` or `403 Forbidden`: trusted identity is absent or the scope is
  not authorized.
- `409 Conflict`: the same event ID and scope already has different event content,
  or a graph identity conflicts.
- `422 Unprocessable Entity`: invalid event data, event scope mismatch, a self/cyclic
  parent reference, or a parent from another flow.

Deduplication is scoped by `(event_id, application_id, environment)`. An event ID
may therefore be reused in another scope without exposing or replacing the first
scope's record. A parent is linked only when it exists in the same scope and flow.
If it has not arrived yet, the event is retained as incomplete provenance and the
relationship is linked when the parent arrives; no relationship is inferred from
timestamps or component adjacency.

There is no batch ingestion endpoint. SDK batch dispatch sends individual event
requests and reports partial failures.

## Lineage

All lineage responses are constrained to one authorized application/environment
scope. Omit both query parameters to use the caller's sole authorized scope;
callers with several scopes must provide both `application_id` and `environment`.
Supplying only one of the two parameters is a `400 Bad Request`.

- `GET /api/v1/lineage/events/{event_id}` — explicit ancestor/descendant traversal.
  Parameters: `direction` (`upstream`, `downstream`, or `both`), `max_depth` (1–8),
  and `limit` (1–100). The target event is included when found. The response reports
  parent IDs whose event nodes are absent from that response, whether results were
  truncated, why they were truncated (including cycle detection), and `completeness`
  (`complete` or `incomplete`)
  relative to the requested scope and result selection. `lineage_records` contains
  persisted evidence classifications and depths for included events when available.
- `GET /api/v1/lineage/flows/{flow_id}` — chronological event page with `limit`
  (1–100) and `offset` (nonnegative). A next offset is returned when more events
  remain in the scope.
- `GET /api/v1/lineage/components/{component_id}` — observed interactions involving
  a component, with `direction` (`upstream`, `downstream`, or `both`), `limit`
  (1–100), and `offset` (nonnegative). This reports recorded interactions; it does
  not assert that data moved between components.

Only recorded `parent_event_id` links form causal event edges. Component edges in a
lineage response represent observed source/destination interactions. A parent ID
may be absent from a response because its event is missing, outside the authorized
scope, outside the current page, or excluded by the component filter; the response
does not claim which case applies unless the traversal directly checked it. Any
parent link omitted from the returned graph makes completeness `incomplete`.

## Graph

- `GET /api/v1/graph` — scoped graph snapshot, filterable by component type, flow,
  time range, action, or data label; supports `limit` (1–500) and `offset`.
- `GET /api/v1/graph/components/{component_id}/neighborhood` — scoped component
  neighborhood with direction and pagination controls.
- `GET /api/v1/graph/paths` — bounded paths between two component IDs. Returned paths
  are based on recorded component interactions and do not prove data movement.

## Other routes

- `GET /api/v1/health` and `GET /health` — service health.
- Policy, authorization, approval, and operator routes are registered under their
  respective `/api/v1` prefixes and retain their own schemas and authorization rules.
