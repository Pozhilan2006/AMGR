# Flow graph frontend integration status

## Current implementation

The dashboard route `/graph` renders the Phase 2 frontend shell for graph data,
loading/error/unavailable states, filter placeholders, component details, and
upstream/downstream lineage panels. It does not render a live React Flow graph
because the current graph snapshot route still returns 501. No mock or inferred
graph data is used. Once an authoritative graph node can be selected, its stable
`component.component_id` is used to query the lineage endpoint. The existing
`/flow-graph` URL remains a compatibility redirect.

The frontend API helper targets the configured API base URL plus `GET /graph`.
With the default backend prefix, this is `GET /api/v1/graph`. The route exists in
`backend/app/api/routes/graph.py`, but always raises HTTP 501
(`Flow graph queries are not implemented.`).

The frontend intentionally does not add `@xyflow/react` until Member 2 provides a
working snapshot contract compatible with rendered node and edge data. The graph UI
is structured to support React Flow integration once the backend becomes available,
while preserving explicit unavailable states rather than simulating a successful graph.

## Declared schemas versus implemented API

`backend/app/schemas/graph.py` declares:

- `GraphResponse`: `nodes`, `edges`, and `generated_at`
- `GraphNode`: `id`, `component`, and optional `label`
- `GraphEdge`: `id`, `source`, `target`, and `action`
- `ComponentRef` (in `backend/app/schemas/flow_event.py`): `component_id`,
  `component_type`, and optional `name`

These are Pydantic models, not a working snapshot response contract yet: the
`GET /graph` route does not return or validate a `GraphResponse`. The frontend
TypeScript types reflect the declared field shape only and do not imply that the
endpoint is available.

## Other graph-related interfaces

| Capability | Repository interface | Current status |
| --- | --- | --- |
| Graph snapshot | `GET /api/v1/graph` | Route returns 501 |
| Component details | No read endpoint | Missing |
| Neighborhood query | No read endpoint | Missing |
| Event ingestion | `POST /api/v1/flow-events` | Persists scope-authorized events after migration; requires identity middleware |
| Event history for a relationship | No read endpoint | Missing |
| Event lineage | `GET /api/v1/lineage/events/{event_id}` | Implemented; scope-protected, follows explicit parent-event links, bounded by depth and result limit |
| Flow records | `GET /api/v1/lineage/flows/{flow_id}` | Implemented; scope-protected, offset-paginated, no causal inference from shared flow ID |
| Component lineage | `GET /api/v1/lineage/components/{component_id}` | Implemented; scope-protected, one-hop recorded interactions with direction filters; not component traversal |
| Graph filters | No query parameters defined | Unsupported |
| Pagination/truncation metadata | No API response contract defined | Unsupported |

The graph snapshot and component-neighborhood methods remain unimplemented.
Events can now be stored by the event ingestion route and queried through the
lineage service. The lineage API distinguishes explicit parent-event links from
recorded component interactions, and does not imply that data necessarily
propagated between an observed interaction's endpoints.

## Member 2 dependencies

Before a live interactive graph can be implemented, Member 2 needs to provide and
document:

1. A working graph snapshot endpoint backed by persisted graph data, with a
   response model and stable component and relationship identifiers.
2. Read-only component-detail and neighborhood endpoints, including supported
   result limits and pagination/truncation semantics.
3. A read-only relationship event-history endpoint with exact filters and event
   response schema. The flow lineage endpoint is not a generic edge-history
   endpoint.
4. A working graph snapshot API with stable component IDs to make the existing
   lineage panels reachable from graph selections.
5. Any supported graph filters and their query parameter validation.

Event ingestion and lineage also require the Alembic migration and trusted
identity middleware. The current app does not install that middleware, so
those endpoints return 401 until deployment provides it.

No frontend filters, traversal, pagination, event histories, or graph controls
are advertised as functional until those contracts exist. Once provided, the
frontend can map actual directed relationships into a visualization; it must
preserve backend identifiers and must reject or report malformed relationships
rather than manufacture replacements.

An observed edge must not be presented as proof that specific content moved
unless the backend evidence supports that claim. Missing graph coverage is not
evidence of safety, and graph event status must remain distinct from an
authorization decision or a component-wide security state.

## Dependencies

No graph rendering dependency has been added. In particular, React Flow is not
installed because the backend currently returns no graph data to render.
