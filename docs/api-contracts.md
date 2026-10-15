# API contracts

## Versioning

All backend routes are served under the `/api/v1` prefix.

## Core endpoints

- `GET /api/v1/health` — returns API health status.
- `POST /api/v1/flow-events` — persists a scoped observed event; requires authenticated identity scope, returns event ID and whether it was an idempotent duplicate.
- `GET /api/v1/lineage/events/{event_id}` — bounded explicit parent/child traversal. Query: `direction` (`upstream`, `downstream`, `both`), `max_depth` (1–8), `limit` (1–100), and optional authorized `application_id` plus `environment`.
- `GET /api/v1/lineage/flows/{flow_id}` — stable timestamp/event-ID paginated events for a flow. Query: `limit` (1–100), `offset` (nonnegative), and optional authorized scope.
- `GET /api/v1/lineage/components/{component_id}` — one-hop observed interactions involving a component. Query: `direction` (`upstream`, `downstream`, `both`), `limit` (1–100), `offset` (nonnegative), and optional authorized scope.
- `GET /api/v1/graph` — graph query placeholder.
- `GET /api/v1/policies` — policy listing placeholder.
- `POST /api/v1/authorization/decisions` — authorization placeholder.
- `GET /api/v1/approvals` — approval listing placeholder.

## Shared schema conventions

- Event IDs and flow IDs are UUID-based.
- Event application and environment scope is required and is checked against the authenticated identity.
- Timestamps are timezone-aware UTC datetimes.
- Source and destination components are typed with `ComponentRef`.
- Metadata is a free-form dictionary keyed by strings.
- Authorization decisions include explicit `allow`, `deny`, or `review` outcomes.

The current app does not install authentication middleware that sets
`request.state.operator_identity`. Until the deployment provides that trusted
middleware, event ingestion and lineage endpoints intentionally return 401.
Run `alembic upgrade head` before using event persistence.

## Compatibility policy

The Python SDK and backend Pydantic models use the same field names and semantic intent. If they diverge over time, the project should keep both models compatible by default and prefer explicit versioning of contract changes rather than introducing a complex code generation workflow at this stage.
